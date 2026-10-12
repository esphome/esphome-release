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
