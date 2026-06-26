"""Tests for Project git helpers, focused on the ahead-of-remote pull guard.

These are integration-style: they drive real ``git`` against throwaway repos in
a temp dir, so they exercise the exact commands the release tool runs in
production rather than mocking git's behaviour.
"""

import subprocess
from pathlib import Path

import pytest

from esphomerelease.exceptions import EsphomeReleaseError
from esphomerelease.project import Project


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _commit(cwd, filename, content):
    Path(cwd, filename).write_text(content)
    _git(cwd, "add", ".")
    _git(cwd, "commit", "-m", f"add {filename}")


@pytest.fixture
def repo(tmp_path):
    """A clone tracking a bare 'origin' remote, both on branch 'main'.

    Returns a Project pointed at the clone with ``branch`` set to 'main'.
    """
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))

    work = tmp_path / "seed"
    _git(tmp_path, "init", "--initial-branch=main", str(work))
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    _commit(work, "README.md", "hello")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(remote), str(clone))
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test")

    proj = Project(path=str(clone), shortname="esphome")
    proj.branch = "main"
    return proj


def test_ahead_behind_clean(repo):
    assert repo.ahead_behind("main") == (0, 0)


def test_ahead_behind_local_commit(repo):
    _commit(repo.path, "local.txt", "local only")
    assert repo.ahead_behind("main") == (1, 0)


def test_ahead_behind_remote_commit(repo, tmp_path):
    # Push a new commit through a second clone so the remote moves ahead.
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(tmp_path / "remote.git"), str(other))
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "config", "user.name", "Test")
    _commit(other, "remote.txt", "remote only")
    _git(other, "push", "origin", "main")

    repo.run_git("fetch", "origin", "main")
    assert repo.ahead_behind("main") == (0, 1)


def test_pull_aborts_when_ahead_and_user_declines(repo, monkeypatch):
    _commit(repo.path, "local.txt", "local only")
    monkeypatch.setattr("esphomerelease.project.click.confirm", lambda *a, **k: False)

    with pytest.raises(EsphomeReleaseError, match="ahead"):
        repo.pull()


def test_pull_resets_when_ahead_and_user_confirms(repo, monkeypatch):
    _commit(repo.path, "local.txt", "local only")
    monkeypatch.setattr("esphomerelease.project.click.confirm", lambda *a, **k: True)

    repo.pull()

    # The stray local commit must be gone; HEAD back in sync with the remote.
    assert repo.ahead_behind("main") == (0, 0)
    assert not Path(repo.path, "local.txt").exists()


def test_pull_fast_forwards_when_behind(repo, tmp_path):
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(tmp_path / "remote.git"), str(other))
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "config", "user.name", "Test")
    _commit(other, "remote.txt", "remote only")
    _git(other, "push", "origin", "main")

    # Not ahead -> pull proceeds and brings the new commit in.
    repo.pull()

    assert Path(repo.path, "remote.txt").exists()
    assert repo.ahead_behind("main") == (0, 0)
