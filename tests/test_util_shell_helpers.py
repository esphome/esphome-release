"""Tests for the small subprocess helpers in esphomerelease.util.

util.py imports ``.config``, which loads ``config.json`` at import time, so the
``util`` fixture chdir's into a tmp dir with a minimal config first.
"""

import importlib
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture
def util(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"editor": "myeditor"}))
    from esphomerelease import config

    importlib.reload(config)
    import esphomerelease.util as util_mod

    return importlib.reload(util_mod)


def _fake_run(calls: list[tuple[Any, dict[str, Any]]], returncode: int) -> Any:
    def run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode)

    return run


def test_copy_clipboard_success_prints_nothing(
    util: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(util.subprocess, "run", _fake_run(calls, 0))

    util.copy_clipboard("notes")

    assert calls == [("pbcopy", {"input": b"notes", "check": False})]
    assert capsys.readouterr().out == ""


def test_copy_clipboard_failure_prints_text(
    util: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(util.subprocess, "run", _fake_run([], 1))

    util.copy_clipboard("notes")

    assert capsys.readouterr().out == ("---------- START COPY ----------\nnotes\n---------- STOP COPY ----------\n")


def test_open_vscode_uses_configured_editor(util: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(util.subprocess, "run", _fake_run(calls, 0))

    util.open_vscode("a.md", "b.md")

    assert calls == [(["myeditor", "a.md", "b.md"], {"check": False})]


def test_open_url_silences_xdg_open_on_linux(util: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(util.sys, "platform", "linux")
    monkeypatch.setattr(util.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(util.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(util.click, "launch", pytest.fail)

    util.open_url("https://example.com/pr/1")

    assert calls == [
        (
            ["/usr/bin/xdg-open", "https://example.com/pr/1"],
            {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "start_new_session": True,
            },
        )
    ]


@pytest.mark.parametrize(("platform", "which_result"), [("darwin", "/usr/bin/xdg-open"), ("linux", None)])
def test_open_url_falls_back_to_click_launch(
    util: ModuleType, monkeypatch: pytest.MonkeyPatch, platform: str, which_result: str | None
) -> None:
    launched: list[str] = []
    monkeypatch.setattr(util.sys, "platform", platform)
    monkeypatch.setattr(util.shutil, "which", lambda name: which_result)
    monkeypatch.setattr(util.subprocess, "Popen", pytest.fail)
    monkeypatch.setattr(util.click, "launch", launched.append)

    util.open_url("https://example.com/pr/1")

    assert launched == ["https://example.com/pr/1"]
