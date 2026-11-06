"""Edge paths in ``Project``: draft releases, the frozen-branch guard and the fix-it subshell."""

import importlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import click
import pytest

from esphomerelease.exceptions import EsphomeReleaseError
from esphomerelease.model import Version


@pytest.fixture
def project_mod(cutting: Any) -> ModuleType:
    return importlib.import_module("esphomerelease.project")


def test_draft_release_asks_user_to_publish(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")
    created: list[dict[str, Any]] = []
    launched: list[str] = []
    confirmed: list[str] = []

    class Repo:
        def create_release(self, tag: str, **kwargs: Any) -> SimpleNamespace:
            created.append({"tag": tag, **kwargs})
            return SimpleNamespace(html_url="https://github.com/esphome/esphome/releases/tag/2026.6.0")

    proj._repo = Repo()
    proj.branch = "release"
    monkeypatch.setattr(proj, "push", lambda: None)
    monkeypatch.setattr(proj, "pull", lambda: None)
    monkeypatch.setattr(project_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(click, "launch", launched.append)
    monkeypatch.setattr(project_mod, "confirm", confirmed.append)

    proj.create_release(Version.parse("2026.6.0"), draft=True)

    assert created[0]["draft"] is True
    assert launched == ["https://github.com/esphome/esphome/releases/edit/2026.6.0"]
    assert (
        "Please go to https://github.com/esphome/esphome/releases/edit/2026.6.0 and publish the draft." in confirmed[0]
    )


def test_checkout_of_other_branch_while_frozen_raises(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome", dev_branch="dev", beta_branch="beta")
    ran: list[tuple[Any, ...]] = []
    monkeypatch.setattr(proj, "run_git", lambda *args: ran.append(args))

    with proj.workon("dev"), pytest.raises(EsphomeReleaseError, match="frozen to dev"):
        proj.checkout("beta")
    assert ran == [("checkout", "dev")]


def test_spawn_subshell_declined_does_nothing(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")
    monkeypatch.setattr(click, "confirm", lambda *args, **kwargs: False)
    monkeypatch.setattr(project_mod.pexpect, "run", pytest.fail)

    proj._spawn_subshell(run="git status", print_lines=["hint"])


def test_spawn_subshell_runs_in_repo_and_restores_cwd(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "subshell-repo"
    repo.mkdir()
    start = tmp_path / "start"
    start.mkdir()
    monkeypatch.chdir(start)
    proj = project_mod.Project(path=str(repo), shortname="esphome")
    seen_cwd: list[Path] = []
    shells: list[str] = []
    confirmed: list[str] = []

    def fake_run(cmd: str) -> bytes:
        seen_cwd.append(Path.cwd())
        return b"merge conflict\n"

    monkeypatch.setattr(click, "confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(project_mod.pexpect, "run", fake_run)
    monkeypatch.setattr(project_mod.os, "system", shells.append)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(project_mod, "confirm", confirmed.append)

    proj._spawn_subshell(run="git status", print_lines=["fix the conflict"])

    assert seen_cwd == [repo]
    assert Path.cwd() == start
    assert shells == ["/bin/zsh"]
    out = capsys.readouterr().out
    assert "merge conflict" in out
    assert "fix the conflict" in out
    assert confirmed == ["Confirm the problem has been fixed"]


def test_spawn_subshell_error_is_printed_and_cwd_restored(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "subshell-repo"
    repo.mkdir()
    monkeypatch.chdir(tmp_path)
    proj = project_mod.Project(path=str(repo), shortname="esphome")
    confirmed: list[str] = []

    def failing_run(cmd: str) -> bytes:
        raise RuntimeError("pexpect exploded")

    monkeypatch.setattr(click, "confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(project_mod.pexpect, "run", failing_run)
    monkeypatch.setattr(project_mod, "confirm", confirmed.append)

    proj._spawn_subshell(run="git status", print_lines=[])

    assert Path.cwd() == tmp_path
    assert "pexpect exploded" in capsys.readouterr().out
    assert confirmed == ["Confirm the problem has been fixed"]


def _raise_422() -> Exception:
    import github3.exceptions

    resp = SimpleNamespace(status_code=422, json=lambda: {"message": "Validation Failed"}, content=b"")
    return github3.exceptions.UnprocessableEntity(resp)


def _new_project(project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome", dev_branch="dev", beta_branch="beta")
    proj.branch = "bump-1"
    monkeypatch.setattr(proj, "push", lambda *a, **k: None)
    monkeypatch.setattr(proj, "pull", lambda: None)
    monkeypatch.setattr(project_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(click, "launch", lambda url: None)
    return proj


class _PrRepo:
    def __init__(self, outcomes: list[Any], existing: list[Any]) -> None:
        self.outcomes = outcomes
        self.existing = existing
        self.create_calls = 0

    def create_pull(self, *args: Any, **kwargs: Any) -> Any:
        self.create_calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    def pull_requests(self, **kwargs: Any) -> list[Any]:
        return self.existing


def test_create_pr_uses_existing_pr_after_422(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _new_project(project_mod, tmp_path, monkeypatch)
    existing = SimpleNamespace(number=7, title="T", html_url="u")
    other = SimpleNamespace(number=8, title="other", html_url="u")
    proj._repo = _PrRepo([_raise_422()], [other, existing])

    assert proj.create_pr(title="T", target_branch="beta") is existing


def test_create_pr_422_without_existing_retries(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _new_project(project_mod, tmp_path, monkeypatch)
    created = SimpleNamespace(number=9, html_url="u")
    proj._repo = _PrRepo([_raise_422(), created], [])
    monkeypatch.setattr(click, "confirm", lambda *a, **k: True)

    assert proj.create_pr(title="T", target_branch="beta") is created
    assert proj._repo.create_calls == 2


def test_create_pr_422_declined_raises(project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = _new_project(project_mod, tmp_path, monkeypatch)
    proj._repo = _PrRepo([_raise_422()], [])
    monkeypatch.setattr(click, "confirm", lambda *a, **k: False)

    with pytest.raises(EsphomeReleaseError, match="rejected creating the PR"):
        proj.create_pr(title="T", target_branch="beta")


class _ReleaseRepo:
    def __init__(self, outcomes: list[Any], tagged: Any) -> None:
        self.outcomes = outcomes
        self.tagged = tagged

    def create_release(self, tag: str, **kwargs: Any) -> Any:
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    def release_from_tag(self, tag: str) -> Any:
        if isinstance(self.tagged, Exception):
            raise self.tagged
        return self.tagged


def test_create_release_uses_existing_release_after_422(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _new_project(project_mod, tmp_path, monkeypatch)
    proj._repo = _ReleaseRepo([_raise_422()], SimpleNamespace(html_url="u"))

    proj.create_release(Version.parse("2026.6.0"))


def test_create_release_422_not_found_retries(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import github3.exceptions

    proj = _new_project(project_mod, tmp_path, monkeypatch)
    missing = github3.exceptions.NotFoundError(SimpleNamespace(status_code=404, json=dict, content=b""))
    proj._repo = _ReleaseRepo([_raise_422(), SimpleNamespace(html_url="u")], missing)
    monkeypatch.setattr(click, "confirm", lambda *a, **k: True)

    proj.create_release(Version.parse("2026.6.0"))


def test_create_release_422_declined_raises(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import github3.exceptions

    proj = _new_project(project_mod, tmp_path, monkeypatch)
    missing = github3.exceptions.NotFoundError(SimpleNamespace(status_code=404, json=dict, content=b""))
    proj._repo = _ReleaseRepo([_raise_422()], missing)
    monkeypatch.setattr(click, "confirm", lambda *a, **k: False)

    with pytest.raises(EsphomeReleaseError, match="rejected creating the release"):
        proj.create_release(Version.parse("2026.6.0"))


def _fail_cherry_pick(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, pull: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run ``cherry_pick`` with a git failure; return the subshell calls and the git args."""
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")
    proj.branch = "release"
    shells: list[dict[str, Any]] = []
    git_args: list[str] = []

    def fake_run_git(*args: str, on_fail: Any) -> Any:
        git_args.extend(args)
        return on_fail("conflict")

    monkeypatch.setattr(proj, "run_git", fake_run_git)
    monkeypatch.setattr(proj, "_spawn_subshell", lambda **kw: shells.append(kw))
    proj.cherry_pick("abc123", pull=pull)
    return shells, git_args


def test_cherry_pick_failure_shows_pr_url(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pull = SimpleNamespace(number=42, title="Fix thing", html_url="https://github.com/esphome/esphome/pull/42")

    shells, git_args = _fail_cherry_pick(project_mod, tmp_path, monkeypatch, pull)

    pr_line = "PR #42: Fix thing - https://github.com/esphome/esphome/pull/42"
    assert git_args == ["cherry-pick", "abc123"]
    out = capsys.readouterr().out
    assert "CHERRY PICK FAILED" in out
    assert pr_line in out
    assert shells[0]["print_lines"][1] == pr_line


def test_cherry_pick_failure_without_pr(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shells, _ = _fail_cherry_pick(project_mod, tmp_path, monkeypatch, None)

    assert "PR #" not in capsys.readouterr().out
    assert shells[0]["print_lines"][1] == "To fix, run in the shell that will be spawned:"


def test_spawn_subshell_ask_false_skips_confirmation(
    project_mod: ModuleType, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")
    monkeypatch.setattr(click, "confirm", pytest.fail)
    monkeypatch.setattr(project_mod.pexpect, "run", lambda cmd: b"")
    shells: list[str] = []
    monkeypatch.setattr(project_mod.os, "system", shells.append)
    monkeypatch.setattr(project_mod, "confirm", lambda text: None)

    proj._spawn_subshell(run="git status", print_lines=[], ask=False)

    assert len(shells) == 1
