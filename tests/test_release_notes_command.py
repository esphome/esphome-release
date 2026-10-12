"""The ``release-notes`` command's prompting for head ref and head version."""

import importlib
from types import ModuleType
from typing import Any

import click
import pytest
from click.testing import CliRunner

from esphomerelease.model import Branch, Version


@pytest.fixture
def commands(cutting: Any, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    commands_mod = importlib.import_module("esphomerelease.commands")
    importlib.reload(commands_mod)
    monkeypatch.setattr(commands_mod.changelog, "generate", lambda **kwargs: "CHANGELOG")
    return commands_mod


def _record_prompts(monkeypatch: pytest.MonkeyPatch, answers: dict[str, str]) -> list[str]:
    asked: list[str] = []

    def prompt(text: str, **kwargs: Any) -> str:
        asked.append(text)
        return answers.get(text, kwargs["default"])

    monkeypatch.setattr(click, "prompt", prompt)
    return asked


def test_prompts_for_head_ref_when_not_passed(commands: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(commands.changelog, "generate", lambda **kwargs: captured.update(kwargs) or "CHANGELOG")
    asked = _record_prompts(monkeypatch, {"Please enter head ref (dev/beta/stable)": "beta"})

    result = CliRunner().invoke(
        commands.cli, ["release-notes", "--base-ref", "2026.6.0", "--head-version", "2026.7.0b1"]
    )

    assert result.exit_code == 0, result.output
    assert asked == ["Please enter head ref (dev/beta/stable)"]
    assert captured["head"] is Branch.BETA
    assert captured["head_version"] == Version.parse("2026.7.0b1")
    assert "CHANGELOG" in result.output


def test_head_ref_argument_skips_prompt(commands: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(commands.changelog, "generate", lambda **kwargs: captured.update(kwargs) or "CHANGELOG")
    asked = _record_prompts(monkeypatch, {})

    result = CliRunner().invoke(
        commands.cli,
        ["release-notes", "--base-ref", "2026.6.0", "--head-ref", "stable", "--head-version", "2026.6.1"],
    )

    assert result.exit_code == 0, result.output
    assert asked == []
    assert captured["head"] is Branch.STABLE
