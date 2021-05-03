from typing import Optional, Dict, Union, List
import time
import re
from pathlib import Path
import contextlib
import os
import sys

from github3.repos.repo import Repository
from github3.pulls import PullRequest
from github3.issues.issue import Issue
from github3.issues.milestone import Milestone
import click
import pexpect

from . import util
from .model import Version, Branch, BranchType
from .config import CONFIG
from .exceptions import EsphomeReleaseError
from .util import gprint, confirm, execute_command


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

    def create_milestone(self, title: str) -> Milestone:
        return self.repo.create_milestone(title)

    def cherry_pick_from_milestone(self, milestone: Milestone) -> List[Issue]:
        """Cherry-pick all PRs in a milestone to the current branch.

        Returns a list of the found PRs (as Issue objects)
        """
        if milestone is None:
            return []

        to_pick = []

        for issue in self.repo.issues(milestone=milestone.number, state="closed"):
            # Convert to pull request and check if it's merged yet
            pull = self.repo.pull_request(issue.number)

            if not pull.is_merged():
                log = click.style(
                    f"Not merged yet: {pull.title}\nIf you want to add it please merge "
                    f"it manually then confirm.",
                    fg="yellow",
                )
                while not click.confirm(log):
                    pass
                continue

            if any(label.name == "cherry-picked" for label in issue.labels()):
                gprint(f"Already cherry picked: {pull.title}", fg="yellow")
                continue

            to_pick.append((pull, issue))

        to_pick = sorted(to_pick, key=lambda obj: obj[0].merged_at)

        for pull, _ in to_pick:
            gprint(f"Cherry picking {pull.title}: {pull.merge_commit_sha}")

        for pull, issue in to_pick:
            self.cherry_pick(pull.merge_commit_sha)

        return [x[1] for x in to_pick]

    def mark_pulls_cherry_picked(self, to_pick: List[Issue]):
        """Mark all PRs cherry-picked by adding a label."""
        for issue in to_pick:
            issue.add_labels("cherry-picked")

    def latest_release(self, *, include_prereleases: bool = True) -> Version:
        """Get the latest release"""
        if not include_prereleases:
            return Version.parse(self.repo.latest_release().tag_name[1:])
        found_versions = []
        for release in self.repo.releases():
            try:
                found_versions.append(Version.parse(release.tag_name[1:]))
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
        tag = f"v{version}"
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
    def commit(self, message: str, ignore_empty: bool = False, confirm: bool = False):
        """Create a commit with the given message.

        ignore_empty: If the diff is empty, don't create a commit instead of failing.
        """
        if ignore_empty and not self.has_local_changes:
            return
        self.run_git("add", ".")
        if confirm:
            gprint("=============== DIFF START ===============")
            self.run_git("diff", "--color", "--cached", show=True)
            util.confirm(
                click.style("==== Please verify the diff is correct ====", fg="green")
            )
        self.run_git("commit", "-m", message)

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

    def does_branch_exist(self, branch: BranchType) -> bool:
        branch = self.lookup_branch(branch)
        out = self.run_git("branch", "--list", branch, fail_ok=True, silent=True)
        return bool(out)

    def checkout_new_branch(self, branch: BranchType):
        branch = self.lookup_branch(branch)

        if self.does_branch_exist(branch):
            if click.confirm(
                f"Branch {branch} already exists. Delete first?", default=True
            ):
                self.run_git("branch", "-D", branch)
            else:
                return
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
        self.commit(f"Bump version to v{version}")

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
    stable_branch="master",
    beta_branch="beta",
    dev_branch="dev",
)
EsphomeDocsProject = Project(
    repo_name="esphome-docs",
    path=CONFIG["esphome_docs_path"],
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
