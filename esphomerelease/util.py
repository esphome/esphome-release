import contextlib
import pathlib
import re
import subprocess

import github3


class Project(object):
    def __init__(self, name, default_branch='master'):
        self.name = name
        self.default_branch = default_branch
        self._repo = None
        self.pr_cache = {}
        self.branch = None
        self._freeze_branch = None

    @property
    def shortname(self):
        return self.name[len('esphome'):]

    @property
    def repo(self):
        if self._repo is None:
            from esphomerelease.github import get_session

            self._repo = get_session().repository('OttoWinter', self.name)
        return self._repo

    @property
    def path(self):
        return self.get_path()

    def get_path(self):
        return pathlib.Path('..') / self.name

    def get_pr(self, pr):
        if pr not in self.pr_cache:
            self.pr_cache[pr] = self.repo.pull_request(pr)
        return self.pr_cache[pr]

    def get_milestone(self, number):
        return self.repo.milestone(number)

    def get_milestone_by_title(self, title):
        seen = []
        for ms in self.repo.milestones(state='open'):
            if ms.title == title:
                return ms

            seen.append(ms.title)

        return None

    def cherry_pick_from_milestone(self, milestone):
        import click

        if milestone is None:
            return []

        to_pick = []

        for issue in self.repo.issues(milestone=milestone.number, state='closed'):
            try:
                pull = self.repo.pull_request(issue.number)
            except github3.exceptions.NotFoundError:
                gprint("{}: {} (#{}) is not a pull request",
                       self.shortname, issue.title, issue.number)
                continue

            if not pull.is_merged():
                log = click.style("Not merged yet: {}".format(pull.title), fg='yellow')
                if not click.confirm(log):
                    raise EsphomeReleaseError
                continue

            if any(label.name == 'cherry-picked' for label in issue.labels()):
                gprint("Already cherry picked: {}", pull.title, fg='yellow')
                continue

            to_pick.append((pull, issue))
            gprint("Cherry picking {}: {}", pull.title, pull.merge_commit_sha)

        to_pick = sorted(to_pick, key=lambda obj: obj[0].merged_at)

        for pull, issue in to_pick:
            self.cherry_pick(pull.merge_commit_sha)

        return to_pick

    def mark_pulls_cherry_picked(self, milestone, to_pick):
        if milestone is None:
            return

        for _, issue in to_pick:
            issue.add_labels('cherry-picked')
        milestone.update(state='closed')

    def latest_release(self):
        return self.repo.latest_release()

    def create_release(self, name, body=None, prerelease=False):
        import click

        self.push()
        rel = self.repo.create_release('v{}'.format(name), target_commitish=self.branch, name=name,
                                       body=body, prerelease=prerelease, draft=True)

        url = rel.html_url.replace('/tag/', '/edit/')
        click.launch(url)
        log = click.style("Please go to {} and publish the draft.".format(url), fg='green')
        if not click.confirm(log):
            raise EsphomeReleaseError

        self.pull()

    def run_git(self, *args, **kwargs):
        from esphomerelease.git import execute_git

        execute_git(self, *args, **kwargs)

    def run_command(self, *args, **kwargs):
        from esphomerelease.git import execute_command

        execute_command(*args, cwd=str(self.path), **kwargs)

    def checkout(self, branch):
        if self._freeze_branch is not None and self._freeze_branch != branch:
            raise EsphomeReleaseError("Branch is frozen to {} ({})".format(
                self._freeze_branch, branch))
        self.run_git('checkout', branch)
        self.branch = branch

    @contextlib.contextmanager
    def workon(self, branch):
        if self._freeze_branch is not None:
            raise EsphomeReleaseError
        self._freeze_branch = branch
        self.checkout(branch)
        yield None
        self._freeze_branch = None

    def pull(self, remote=None):
        if remote is not None:
            self.run_git('pull', remote, self.branch)
        else:
            self.run_git('pull')

    def merge(self, branch):
        try:
            self.run_git('merge', branch)
        except EsphomeReleaseError:
            import click

            gprint('{}: Merge failed ({} into {})', self.name, branch, self.branch, fg='red')
            if not click.confirm('Please fix your merge conflicts and confirm'):
                raise EsphomeReleaseError

    def commit(self, message, ignore_empty=False):
        if ignore_empty:
            try:
                self.run_git('diff-index', '--quiet', 'HEAD', '--')
                return
            except EsphomeReleaseError:
                pass
        self.run_git('commit', '-m', message)

    def push(self, remote='origin', tags=False):
        args = ['push', '-u', remote]
        if tags:
            args.append('--tags')
        self.run_git(*args)

    def replace_file_content(self, path, pattern, repl):
        replace_file_content(self.get_path() / pathlib.Path(path), pattern, repl)

    def checkout_pull(self, branch, remote=None):
        with self.workon(branch):
            self.pull(remote=remote)

    def checkout_merge(self, target, base):
        with self.workon(target):
            self.merge(base)

    def checkout_push(self, branch, **kwargs):
        with self.workon(branch):
            self.push(**kwargs)

    def cherry_pick(self, sha):
        try:
            self.run_git('cherry-pick', sha)
        except EsphomeReleaseError:
            import click

            gprint('{}: Cherry-Pick {} failed!', self.shortname, sha, fg='red')
            if not click.confirm('Please fix your conflicts and confirm'):
                raise EsphomeReleaseError


EsphomelibProject = Project("esphomelib")
EsphomeyamlProject = Project("esphomeyaml")
EsphomedocsProject = Project("esphomedocs", 'current')


def copy_clipboard(text):
    subprocess.run('pbcopy', input=text.encode())


def replace_file_content(path, pattern, repl):
    with open(path, 'r') as f:
        content = f.read()

    content_new, count = re.subn(pattern, repl, content, flags=re.M)
    if count != 1:
        raise EsphomeReleaseError("Cannot find pattern in file {} ({})!".format(path, count))
    print("Replaced one occurrence in {} by {}".format(path, repl))
    with open(path, 'w') as f:
        f.write(content_new)


class EsphomeReleaseError(Exception):
    pass


def gprint(s, *args, fg='green'):
    import click

    click.secho(s.format(*args), fg=fg)
