"""Tests for the ``check_docs_prs.py`` script's fan-out over linked PRs.

The script lives at the repo root (not inside the package), so it is loaded
from its file path. Linked esphome PRs are deduplicated across docs PRs and
fetched concurrently — one ``gh`` call per unique PR number.
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


def test_fetch_linked_pr_states_empty(script):
    assert script.fetch_linked_pr_states([]) == {}


def test_main_flags_merged_and_dedupes_lookups(script, monkeypatch, capsys):
    """Two docs PRs referencing the same esphome PR trigger a single lookup;
    only docs PRs with a merged link are flagged."""
    docs_prs = [
        _docs_pr(1, "Fixes esphome/esphome#100"),
        _docs_pr(2, "Also for esphome/esphome#100"),
        _docs_pr(3, "For esphome/esphome#200 and esphome/esphome#300"),
        _docs_pr(4, "No links here"),
    ]
    monkeypatch.setattr(script, "get_open_docs_prs", lambda: docs_prs)

    states = {
        100: script.LinkedPR(100, "MERGED", "2026-07-01T00:00:00Z", "Merged PR"),
        200: script.LinkedPR(200, "OPEN", None, "Open PR"),
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
