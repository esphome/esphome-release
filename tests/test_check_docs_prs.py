"""Tests for the ``check_docs_prs.py`` script's fan-out over linked PRs.

The script lives at the repo root (not inside the package), so it is loaded
from its file path. Linked esphome PRs are deduplicated across docs PRs and
fetched concurrently: one ``gh`` call per unique PR number.

Flagging is bidirectional. A docs PR is only flagged when a code PR it names
names it back (a confirmed pair) and that code PR has merged. Code PRs the docs
PR merely mentions in prose are reported separately as unconfirmed, and never
flag anything on their own.
"""

import importlib.util
import threading
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "check_docs_prs.py"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("check_docs_prs", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _docs_pr(number: int, body: str) -> dict:
    return {
        "number": number,
        "title": f"Docs PR {number}",
        "url": f"https://github.com/esphome/esphome.io/pull/{number}",
        "body": body,
    }


def _back_link(docs_number: int) -> str:
    """A code PR body that references ``docs_number`` back."""
    return (
        "**Pull request in [esphome.io](https://github.com/esphome/esphome.io) "
        "with documentation (if applicable):**\n"
        f"- esphome/esphome.io#{docs_number}\n"
    )


def test_fetch_linked_pr_states_empty(script):
    assert script.fetch_linked_pr_states([]) == {}


def test_main_flags_merged_and_dedupes_lookups(script, monkeypatch, capsys):
    """Two docs PRs referencing the same esphome PR trigger a single lookup;
    only docs PRs with a merged *confirmed* link are flagged."""
    docs_prs = [
        _docs_pr(1, "Fixes esphome/esphome#100"),
        _docs_pr(2, "Also for esphome/esphome#100"),
        _docs_pr(3, "For esphome/esphome#200 and esphome/esphome#300"),
        _docs_pr(4, "No links here"),
    ]
    monkeypatch.setattr(script, "get_open_docs_prs", lambda: docs_prs)

    states = {
        # #100 links both docs PRs back, so both are confirmed pairs.
        100: script.LinkedPR(
            100,
            "MERGED",
            "2026-07-01T00:00:00Z",
            "Merged PR",
            _back_link(1) + _back_link(2),
        ),
        200: script.LinkedPR(200, "OPEN", None, "Open PR", _back_link(3)),
        # 300 is unresolvable (gh error) -> None
    }
    calls = []
    lock = threading.Lock()

    def fake_state(number: int):
        with lock:
            calls.append(number)
        return states.get(number)

    monkeypatch.setattr(script, "get_esphome_pr_state", fake_state)

    assert script.main() == 1
    assert sorted(calls) == [100, 200, 300]  # 100 fetched once despite two refs

    out = capsys.readouterr().out
    assert "Docs PR #1" in out and "Docs PR #2" in out
    # PR 3 links only an open + unresolvable PR; PR 4 has no links.
    assert "Docs PR #3" not in out and "Docs PR #4" not in out
    assert "Summary: 2 docs PRs need attention" in out
    assert "confirmed, they link back" in out
    assert "unconfirmed" not in out


def test_main_nothing_flagged(script, monkeypatch, capsys):
    monkeypatch.setattr(
        script, "get_open_docs_prs", lambda: [_docs_pr(1, "No links")]
    )
    monkeypatch.setattr(
        script,
        "get_esphome_pr_state",
        lambda number: pytest.fail("should not be called"),
    )

    assert script.main() == 0
    assert "No docs PRs found" in capsys.readouterr().out


def test_main_merged_pr_without_back_link_is_not_flagged(script, monkeypatch, capsys):
    """Regression for esphome/esphome.io#7071.

    Docs #7071 mentions merged code PR #14255 only in prose (#14255 links a
    different docs PR), and pairs with #17797, which links back but is still
    open. The old one-way check flagged it off #14255; it must not any more.
    """
    docs_body = (
        "Documents the default-route arbitration added in esphome/esphome#17797.\n"
        "Replaces the placeholder note (added with esphome/esphome#14255's docs).\n"
        "\n"
        "**Pull request in esphome with YAML changes (if applicable):**\n"
        "- esphome/esphome#17797\n"
    )
    monkeypatch.setattr(script, "get_open_docs_prs", lambda: [_docs_pr(7071, docs_body)])

    states = {
        14255: script.LinkedPR(
            14255,
            "MERGED",
            "2026-07-23T01:55:04Z",
            "Add network priority for multi-interface support",
            "- esphome/esphome-docs#6676\n",
        ),
        17797: script.LinkedPR(
            17797,
            "OPEN",
            None,
            "Arbitrate the default route from the network priority list",
            _back_link(7071),
        ),
    }
    monkeypatch.setattr(script, "get_esphome_pr_state", lambda number: states[number])

    assert script.main() == 0
    assert "No docs PRs found" in capsys.readouterr().out


def test_main_shows_unconfirmed_section_for_flagged_pr(script, monkeypatch, capsys):
    """A flagged docs PR still lists the one-way mentions, clearly labelled."""
    docs_body = "Pairs with esphome/esphome#100, also mentions esphome/esphome#200."
    monkeypatch.setattr(script, "get_open_docs_prs", lambda: [_docs_pr(5, docs_body)])

    states = {
        100: script.LinkedPR(
            100, "MERGED", "2026-07-01T00:00:00Z", "Paired code PR", _back_link(5)
        ),
        200: script.LinkedPR(
            200, "MERGED", "2026-06-01T00:00:00Z", "Unrelated code PR", "no back-link"
        ),
    }
    monkeypatch.setattr(script, "get_esphome_pr_state", lambda number: states[number])

    assert script.main() == 1

    out = capsys.readouterr().out
    confirmed_at = out.index("confirmed, they link back")
    unconfirmed_at = out.index("unconfirmed, no back-link")
    assert confirmed_at < unconfirmed_at
    assert out.index("#100: Paired code PR") < unconfirmed_at
    assert unconfirmed_at < out.index("#200: Unrelated code PR")
    assert "Summary: 1 docs PRs need attention" in out


def test_get_esphome_pr_state_parses_gh_output(script, monkeypatch):
    """The real parser runs against a recorded ``gh`` payload, body included."""
    recorded = []

    def fake_run(args: list[str]) -> str:
        recorded.append(args)
        return (
            '{"state": "MERGED", "mergedAt": "2026-07-01T00:00:00Z", '
            '"title": "Some PR", "body": "- esphome/esphome.io#7071"}'
        )

    monkeypatch.setattr(script, "run_gh_command", fake_run)

    linked = script.get_esphome_pr_state(17797)

    assert linked == script.LinkedPR(
        17797, "MERGED", "2026-07-01T00:00:00Z", "Some PR", "- esphome/esphome.io#7071"
    )
    # The body rides along on the existing call rather than costing a second one.
    assert recorded == [
        ["pr", "view", "17797", "--repo", "esphome/esphome", "--json",
         "state,mergedAt,title,body"]
    ]


def test_get_esphome_pr_state_null_body_becomes_empty_string(script, monkeypatch):
    """``gh`` returns ``null`` for an empty PR body; it must not reach the regexes."""
    monkeypatch.setattr(
        script,
        "run_gh_command",
        lambda args: '{"state": "OPEN", "mergedAt": null, "title": "T", "body": null}',
    )

    assert script.get_esphome_pr_state(1).body == ""


def test_get_esphome_pr_state_returns_none_on_gh_error(script, monkeypatch):
    import subprocess

    def fail(args: list[str]) -> str:
        raise subprocess.CalledProcessError(1, "gh")

    monkeypatch.setattr(script, "run_gh_command", fail)

    assert script.get_esphome_pr_state(999) is None
