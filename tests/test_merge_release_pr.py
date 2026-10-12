"""Selecting the release PR to merge when several match the bump branch."""

from types import SimpleNamespace
from typing import Any

import click

from esphomerelease.model import Branch, Version

VERSION = Version.parse("2026.6.0")


class FakePR:
    def __init__(self, number: int) -> None:
        self.number = number
        self.user = SimpleNamespace(login="dev")
        self.html_url = f"https://github.com/esphome/esphome/pull/{number}"
        self.state = "open"
        self.merged = False

    def merge(self, merge_method: str) -> bool:
        self.merged = True
        return True


def _setup(cutting: Any, monkeypatch: Any, answer: str) -> tuple[list[FakePR], list[dict[str, Any]]]:
    prs = [FakePR(1), FakePR(2)]
    proj = SimpleNamespace(shortname="code", get_pr_by_title=lambda **kwargs: prs)
    prompts: list[dict[str, Any]] = []

    def prompt(text: str, **kwargs: Any) -> str:
        prompts.append(kwargs)
        return answer

    monkeypatch.setattr(click, "prompt", prompt)
    cutting._merge_release_pr(proj=proj, version=VERSION, head_branch=Branch.BETA)
    return prs, prompts


def test_choice_accepts_every_listed_number(cutting, monkeypatch):
    _, prompts = _setup(cutting, monkeypatch, "1")
    choice = prompts[0]["type"]
    assert list(choice.choices) == ["1", "2", "3"]
    assert choice.convert("3", None, None) == "3"


def test_selected_pr_is_merged(cutting, monkeypatch):
    prs, _ = _setup(cutting, monkeypatch, "2")
    assert [pr.merged for pr in prs] == [False, True]


def test_auto_merge_none_option_merges_nothing(cutting, monkeypatch):
    prs, _ = _setup(cutting, monkeypatch, "3")
    assert [pr.merged for pr in prs] == [False, False]
