import contextlib
import functools
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

import click
import pexpect
from github3.issues.issue import Issue
from github3.issues.milestone import Milestone
from github3.pulls import PullRequest
from github3.repos.repo import Repository

from . import util
from .config import CONFIG
from .exceptions import EsphomeReleaseError
from .model import Branch, BranchType, Version
from .util import confirm, execute_command, gprint, process_asynchronously


def _issue_pr_merged_at(issue: Issue) -> Optional[str]:
    """Merge timestamp of the PR behind an issue, from the issue payload.

    The issues listing embeds a ``pull_request`` block (with ``merged_at``)
    for issues that are pull requests, so both "is this a PR" and "is it
    merged" can be answered without any extra API request. Returns ``None``
    for plain issues and for unmerged PRs.
    """
    urls = issue.pull_request_urls or {}
    return urls.get("merged_at")


def _issue_is_pr(issue: Issue) -> bool:
    """Whether an issue from a listing is a pull request (no API call)."""
    return issue.pull_request_urls is not None


def _issue_is_cherry_picked(issue: Issue) -> bool:
    """Whether the issue carries the ``cherry-picked`` label (no API call).

    Uses the labels embedded in the issue listing payload instead of the
    ``issue.labels()`` method, which hits the API once per issue.
    """
    return any(label.name == "cherry-picked" for label in issue.original_labels)


class Project:
    def __init__(
        self,
        *,
        path: str,
        shortname: str,
        repo_name: Optional[str] = None,
        stable_branch: Optional[str] = None,
        beta_branch: Optional[str] = None,
        dev_branch: Optional[str] = None,
    ):
        # The name on the remote
        self._repo_name: str = repo_name
        self.shortname: str = shortname
        self._repo: Optional[Repository] = None

        # A cache or
        self.pr_cache: Dict[int, PullRequest] = {}

        # The current branch so we don't have to go through git
        self.branch: Optional[str] = None

        # Path of the repo
        self.path: Path = Path(path)
        assert self.path.is_dir(), f"Project dir {self.path} does not exist"

        # The branch we have frozen on with .workon()
        self._freeze_branch: Optional[str] = None

        self._branch_lookup: Dict[Branch, str] = {}
        if stable_branch is not None:
            self._branch_lookup[Branch.STABLE] = stable_branch
        if beta_branch is not None:
            self._branch_lookup[Branch.BETA] = beta_branch
        if dev_branch is not None:
            self._branch_lookup[Branch.DEV] = dev_branch

    @property
    def name(self) -> str:
        return self._repo_name

    def lookup_branch(self, branch: Union[str, Branch]) -> str:
        if isinstance(branch, Branch):
            return self._branch_lookup[branch]
        return branch

    @property
    def repo(self) -> Repository:
        """Return the repository as a git object"""
        # Load lazily
        if self._repo is None:
            from esphomerelease.github import get_session

            self._repo = get_session().repository("esphome", self._repo_name)
        return self._repo

    def get_pr(self, pr: int) -> PullRequest:
        """Get a PR by number (and cache it)."""
        if pr not in self.pr_cache:
            self.pr_cache[pr] = self.repo.pull_request(pr)
        return self.pr_cache[pr]

    def get_prs(self, numbers: List[int]) -> List[PullRequest]:
        """Get multiple PRs by number, fetching uncached ones in parallel."""
        missing = list(dict.fromkeys(n for n in numbers if n not in self.pr_cache))
        if missing:
            jobs = [functools.partial(self.repo.pull_request, n) for n in missing]
            for pull in process_asynchronously(jobs, "Fetching PRs"):
                self.pr_cache[pull.number] = pull
        return [self.pr_cache[n] for n in numbers]

    def _milestone_pr_issues(self, milestone: Milestone, state: str) -> List[Issue]:
        """List the issues on a milestone that are pull requests.

        Judged from the listing payload alone — no per-issue API requests.
        """
        return [
            issue
            for issue in self.repo.issues(milestone=milestone.number, state=state)
            if _issue_is_pr(issue)
        ]

    def get_pr_by_title(
        self,
        *,
        title: str,
        head: Optional[BranchType] = None,
        base: Optional[BranchType] = None,
    ) -> List[PullRequest]:
        if head is not None:
            head = self.lookup_branch(head)
        if base is not None:
            base = self.lookup_branch(base)

        res = []
        for pr in self.repo.pull_requests(head=head, base=base):
            self.pr_cache[pr.number] = pr
            if pr.title == title:
                res.append(pr)
        return res

    def get_milestone_by_title(self, title: str) -> Optional[Milestone]:
        """Get a milestone by title."""
        seen = []
        for ms in self.repo.milestones(state="open"):
            if ms.title == title:
                return ms

            seen.append(ms.title)

        return None

    def get_open_milestones(self) -> List[Milestone]:
        """Get all open milestones."""
        return list(self.repo.milestones(state="open"))

    def create_milestone(
        self, title: str, *, due_on: Optional[str] = None
    ) -> Milestone:
        return self.repo.create_milestone(title, due_on=due_on)

    def ensure_milestone(
        self, title: str, *, due_on: Optional[str] = None
    ) -> Milestone:
        """Get the open milestone with ``title``, creating it if missing.

        GitHub rejects duplicate milestone titles with a 422, so a milestone
        that was already created (e.g. manually) must be looked up, not
        re-created. If ``due_on`` is given and the existing milestone is due on
        a different day (or has no due date), it is corrected.
        """
        milestone = self.get_milestone_by_title(title)
        if milestone is None:
            return self.create_milestone(title, due_on=due_on)
        if due_on is not None:
            # GitHub normalizes due_on to midnight US/Pacific, so the returned
            # timestamp never matches the one sent; compare calendar days only.
            wanted_day = due_on.split("T", 1)[0]
            actual_day = (
                milestone.due_on.date().isoformat()
                if milestone.due_on is not None
                else None
            )
            if actual_day != wanted_day:
                milestone.update(due_on=due_on)
        return milestone

    def get_open_prs_for_milestone(self, milestone: Milestone) -> List[PullRequest]:
        """Get all open PRs assigned to a milestone."""
        if milestone is None:
            return []

        issues = self._milestone_pr_issues(milestone, "open")
        return self.get_prs([issue.number for issue in issues])

    def get_next_beta_prs_for_milestone(
        self, milestone: Milestone
    ) -> List[PullRequest]:
        """Get merged PRs in a milestone that haven't been cherry-picked yet.

        These are the PRs :meth:`cherry_pick_from_milestone` would pick at the
        next beta cut, sorted by merge time (the order they would be applied).
        """
        if milestone is None:
            return []

        numbers = [
            issue.number
            for issue in self._milestone_pr_issues(milestone, "closed")
            if _issue_pr_merged_at(issue) is not None
            and not _issue_is_cherry_picked(issue)
        ]
        return sorted(self.get_prs(numbers), key=lambda pr: pr.merged_at)

    def get_milestone_pr_numbers(
        self, milestone: Milestone, *, merged_only: bool = True
    ) -> List[int]:
        """Return PR numbers attached to a milestone.

        merged_only: if True (default), only include PRs that have been merged.
          Open/unmerged PRs are surfaced by the open-PR check, so for
          completeness verification we care about the merged ones that should be
          present in the release branch.
        """
        if milestone is None:
            return []

        numbers = []
        for issue in self.repo.issues(milestone=milestone.number, state="all"):
            if not issue.pull_request_urls:
                continue  # genuinely an issue, not a pull request
            # This issue *is* a PR, so a 404 here is unexpected (transient or
            # permission). Let it propagate rather than silently undercounting
            # the milestone and weakening the completeness check.
            pull = self.repo.pull_request(issue.number)
            if merged_only and not pull.is_merged():
                continue
            numbers.append(pull.number)
        return numbers

    def cherry_pick_from_milestone(self, milestone: Milestone) -> List[Issue]:
        """Cherry-pick all PRs in a milestone to the current branch.

        Returns a list of the found PRs (as Issue objects)
        """
        if milestone is None:
            return []

        pick_issues: List[Issue] = []

        for issue in self._milestone_pr_issues(milestone, "closed"):
            # Merged state and labels come from the issue listing payload;
            # only the PRs that will actually be picked are fetched (in
            # parallel, for their merge_commit_sha) below.
            if _issue_pr_merged_at(issue) is None:
                log = click.style(
                    f"Not merged yet: {issue.title}\nIf you want to add it please merge "
                    f"it manually then confirm.",
                    fg="yellow",
                )
                while not click.confirm(log):
                    pass
                continue

            if _issue_is_cherry_picked(issue):
                gprint(f"Already cherry picked: {issue.title}", fg="yellow")
                continue

            pick_issues.append(issue)

        pulls = self.get_prs([issue.number for issue in pick_issues])
        to_pick = sorted(zip(pulls, pick_issues), key=lambda obj: obj[0].merged_at)

        for pull, _ in to_pick:
            gprint(f"Cherry picking {pull.title}: {pull.merge_commit_sha}")

        for pull, issue in to_pick:
            self.cherry_pick(pull.merge_commit_sha)

        return [x[1] for x in to_pick]

    def mark_pulls_cherry_picked(self, to_pick: List[Issue]):
        """Mark all PRs cherry-picked by adding a label."""
        for issue in to_pick:
            issue.add_labels("cherry-picked")

    def remove_merged_prs_from_milestone(self, milestone: Milestone) -> List[Issue]:
        """Remove already-merged PRs from a milestone.

        Used at the first beta cut: those PRs are brought into the release by the
        dev->beta merge, so clearing their milestone stops later beta cuts from
        cherry-picking them again. Open PRs keep their milestone.
        """
        if milestone is None:
            return []

        removed = []
        for issue in self._milestone_pr_issues(milestone, "closed"):
            if _issue_pr_merged_at(issue) is None:
                continue
            issue.edit(milestone=0)  # 0 clears the milestone in github3
            removed.append(issue)
        return removed

    # GitHub lists releases newest-first, so the highest version is always
    # within the most recently created ones — one API page is enough instead
    # of paginating the repo's entire release history.
    RECENT_RELEASES_TO_CHECK = 30

    def latest_release(self, *, include_prereleases: bool = True) -> Version:
        """Get the latest release"""
        if not include_prereleases:
            return Version.parse(self.repo.latest_release().tag_name)
        found_versions = []
        for release in self.repo.releases(number=self.RECENT_RELEASES_TO_CHECK):
            try:
                found_versions.append(Version.parse(release.tag_name))
            except ValueError:
                pass
        return max(found_versions)

    def create_pr(
        self, *, title: str, target_branch: BranchType, body: Optional[str] = None
    ) -> PullRequest:
        target_branch = self.lookup_branch(target_branch)
        self.push(set_upstream=True)
        # Wait a bit for push to get to GitHub
        time.sleep(1.0)
        pr = self.repo.create_pull(title, target_branch, self.branch, body=body)
        gprint(
            f"Created Pull Request #{pr.number} from {self.branch} against {target_branch}"
        )
        click.launch(pr.html_url)
        return pr

    def create_release(
        self,
        version: Version,
        body: Optional[str] = None,
        prerelease: bool = False,
        draft: bool = False,
    ):
        """Create a release from the current branch on the remote.

        name: The title of the release.
        body: The body of text describing the release.
        prerelease: Whether it should be marked as a prerelease.
        draft: Whether the release should be created as a draft and the user must
          confirm it in the webinterface themself (safer)
        """
        self.push()
        # Wait a bit for push to get to GitHub
        time.sleep(1.0)
        tag = f"{version}"
        rel = self.repo.create_release(
            tag,
            target_commitish=self.branch,
            name=f"{version}",
            body=body,
            prerelease=prerelease,
            draft=draft,
        )

        if draft:
            url = rel.html_url.replace("/tag/", "/edit/")
            click.launch(url)
            log = click.style(
                "Please go to {} and publish the draft.".format(url), fg="green"
            )
            confirm(log)
        else:
            time.sleep(1.0)
            gprint(f"Created Release {tag} from {self.branch}")
            click.launch(rel.html_url)

        self.pull()

    def run_git(self, *args, **kwargs):
        """Run a git command given by args."""
        return self.run_command("git", *args, **kwargs)

    def run_command(self, *args, **kwargs):
        """Run a command in the repository working directory."""
        return execute_command(*args, cwd=str(self.path), **kwargs)

    def checkout(self, branch: BranchType):
        """Checkout a branch."""
        branch = self.lookup_branch(branch)
        # Check if we have frozen to a branch with .workon()
        if self._freeze_branch is not None and self._freeze_branch != branch:
            raise EsphomeReleaseError(
                "Branch is frozen to {} ({})".format(self._freeze_branch, branch)
            )
        self.run_git("checkout", branch)
        self.branch = branch

    def reset(self, target: str, hard: bool = False):
        """Reset the local repo to the given target ref."""
        target = self.lookup_branch(target)
        command = ["reset"]
        if hard:
            command.append("--hard")
        command.append(target)
        self.run_git(*command)

    def reset_hard_remote(self, branch: BranchType, remote: str = "origin"):
        """Reset hard to a remote branch."""
        branch = self.lookup_branch(branch)
        with self.workon(branch):
            self.reset(f"{remote}/{branch}", hard=True)

    @contextlib.contextmanager
    def workon(self, branch: BranchType):
        """Checkout a directory and make sure the branch is not changed in the meantime."""
        branch = self.lookup_branch(branch)
        if self._freeze_branch is not None:
            raise EsphomeReleaseError
        self._freeze_branch = branch
        self.checkout(branch)
        yield None
        self._freeze_branch = None

    def pull(self, remote: Optional[str] = None):
        """Pull the current branch from a remote."""
        if remote is not None:
            self.run_git("pull", remote, self.branch)
        else:
            self.run_git("pull")

    def _spawn_subshell(self, *, run: str, print_lines: List[str]):
        if not click.confirm("Spawn a shell to fix the problem?", default=True):
            return
        old_cwd = os.getcwd()
        try:
            os.chdir(str(self.path))
            out = pexpect.run(run)
            sys.stdout.write(out.decode())
            for line in print_lines:
                gprint(line)
            os.system(os.getenv("SHELL", "/bin/bash"))
        except Exception as exc:  # pylint: disable=broad-except
            print(exc)
        finally:
            os.chdir(old_cwd)
        confirm("Confirm the problem has been fixed")

    def merge(self, branch: BranchType, strategy_option: Optional[str] = None):
        """Merge the branch `branch` into the current branch with an optional explicit strategy."""
        branch = self.lookup_branch(branch)
        command = ["merge"]
        if strategy_option is not None:
            command += ["-X", strategy_option]
        command.append(branch)

        def on_fail(stdout):
            gprint("===== MERGE FAILED ====")
            self._spawn_subshell(
                run="git status",
                print_lines=[
                    f"{self._repo_name} Merging {branch} into {self.branch} failed!",
                    "To fix, run in the shell that will be spawned:",
                    " - look at `git status` output",
                    " - resolve merge conflicts",
                    " - git add .",
                    " - git commit",
                    " - Then exit the shell with Ctrl+D",
                ],
            )
            return stdout

        self.run_git(*command, on_fail=on_fail)

    # pylint: disable=redefined-outer-name
    def commit(
        self,
        message: str,
        ignore_empty: bool = False,
        confirm: bool = False,
        no_verify: bool = False,
    ):
        """Create a commit with the given message.

        ignore_empty: If the diff is empty, don't create a commit instead of failing.
        """
        self.run_git("add", ".")
        if ignore_empty and not self._has_staged_changes():
            return
        if confirm:
            gprint("=============== DIFF START ===============")
            self.run_git("diff", "--color", "--cached", show=True)
            util.confirm(
                click.style("==== Please verify the diff is correct ====", fg="green")
            )
        cmd = [
            "commit",
            "-m",
            message,
        ]
        if no_verify:
            cmd.append("--no-verify")
        self.run_git(*cmd)

    def push(self, set_upstream: bool = False):
        """Push the current ref to the given remote."""
        if set_upstream:
            self.run_git("push", "--set-upstream", "origin", self.branch)
        else:
            self.run_git("push")

    def checkout_pull(self, branch: BranchType):
        """Checkout a branch, then pull on that branch."""
        with self.workon(branch):
            self.pull()

    def checkout_merge(self, target: BranchType, base: BranchType):
        """Checkout `target` branch, then merge `base` into `target`."""
        with self.workon(target):
            self.merge(base)

    @property
    def has_local_changes(self) -> bool:
        try:
            self.run_git(
                "diff-index", "--quiet", "HEAD", "--", fail_ok=True, silent=True
            )
            return False
        except EsphomeReleaseError:
            return True

    def _has_staged_changes(self) -> bool:
        try:
            self.run_git(
                "diff", "--cached", "--quiet", fail_ok=True, silent=True
            )
            return False
        except EsphomeReleaseError:
            return True

    def does_branch_exist(self, branch: BranchType) -> bool:
        branch = self.lookup_branch(branch)
        out = self.run_git("branch", "--list", branch, fail_ok=True, silent=True)
        return bool(out)

    def checkout_new_branch(self, branch: BranchType):
        branch = self.lookup_branch(branch)

        if self.does_branch_exist(branch):
            self.run_git("branch", "-D", branch)
        self.run_git("checkout", "-b", branch)

    def checkout_push(self, branch: BranchType):
        """Checkout `branch`, then push."""
        with self.workon(branch):
            self.push()

    def cherry_pick(self, sha: str):
        """Cherry-pick a commit by SHA."""

        def on_fail(stdout):
            gprint("===== CHERRY PICK FAILED ====")
            self._spawn_subshell(
                run="git status",
                print_lines=[
                    f"{self._repo_name} Cherry-picking {sha} into {self.branch} failed!",
                    "To fix, run in the shell that will be spawned:",
                    " - look at `git status` output",
                    " - resolve merge conflicts",
                    " - git add .",
                    " - git commit",
                    " - Then exit the shell with Ctrl+D",
                ],
            )
            return stdout

        self.run_git("cherry-pick", sha, on_fail=on_fail)

    def bump_version(self, version: Version):
        self.run_command("script/bump-version.py", str(version))
        self.commit(f"Bump version to {version}", no_verify=True, ignore_empty=True)

    def prs_between(self, base: BranchType, head: BranchType) -> List[int]:
        base = self.lookup_branch(base)
        head = self.lookup_branch(head)

        stdout = self.run_git("log", f"{base}..{head}", "--pretty=format:%s").decode()
        last = None

        prs = []
        for line in stdout.splitlines(False):
            if line == last:
                continue
            last = line
            match = re.match(r"^.+\(\#(\d+)\)$", line)
            if match is not None:
                prs.append(int(match.group(1)))

        return prs


EsphomeProject = Project(
    repo_name="esphome",
    path=CONFIG["esphome_path"],
    shortname="esphome",
    stable_branch="release",
    beta_branch="beta",
    dev_branch="dev",
)
EsphomeDocsProject = Project(
    repo_name="esphome.io",
    path=CONFIG.get("esphome_io_path", CONFIG.get("esphome_docs_path")),
    shortname="docs",
    stable_branch="current",
    beta_branch="beta",
    dev_branch="next",
)
EsphomeHassioProject = Project(
    repo_name="hassio", path=CONFIG["esphome_hassio_path"], shortname="hassio"
)
EsphomeIssuesProject = Project(
    repo_name="issues", path=CONFIG["esphome_issues_path"], shortname="issues"
)
EsphomeFeatureRequestsProject = Project(
    repo_name="feature-requests",
    path=CONFIG["esphome_feature_requests_path"],
    shortname="feature-requests",
)


ALL_PROJECTS: List[Project] = [
    EsphomeProject,
    EsphomeDocsProject,
    EsphomeHassioProject,
    EsphomeIssuesProject,
    EsphomeFeatureRequestsProject,
]
