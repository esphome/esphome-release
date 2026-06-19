"""Tests for esphomerelease.util.execute_command error handling.

util.py imports ``.config``, which loads ``config.json`` at import time. The
``util`` fixture chdir's into a tmp dir with an empty config so the module is
importable without a real working copy (mirrors the import-safe test pattern
used elsewhere in this repo).
"""

import importlib

import pytest


@pytest.fixture
def util(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text("{}")
    import esphomerelease.config as config

    importlib.reload(config)
    import esphomerelease.util as util_mod

    importlib.reload(util_mod)
    return util_mod


def test_success_returns_stdout(util):
    out = util.execute_command("sh", "-c", "echo hello", silent=True)
    assert out.strip() == b"hello"


def test_live_failure_invokes_on_fail(util):
    """A failure under ``live=True`` (merged stderr) must still call on_fail.

    Regression guard: previously ``process.stderr is None`` triggered a bare
    raise before the on_fail callback could run.
    """
    called = {}

    def on_fail(stdout):
        called["yes"] = True
        return b"recovered"

    result = util.execute_command(
        "sh",
        "-c",
        "echo hi; exit 3",
        live=True,
        on_fail=on_fail,
        silent=True,
    )
    assert called.get("yes") is True
    assert result == b"recovered"


def test_live_failure_reaches_retry_prompt(util, monkeypatch):
    """Without fail_ok/on_fail, a live failure must reach the retry prompt."""
    prompts = []

    def fake_confirm(*args, **kwargs):
        prompts.append(args)
        return True

    monkeypatch.setattr(util.click, "confirm", fake_confirm)

    util.execute_command("sh", "-c", "echo data; exit 1", live=True, silent=True)
    assert prompts, "retry prompt was never shown for a failed live command"


def test_fail_ok_still_raises(util):
    """fail_ok suppresses the user prompt but still raises (has_local_changes
    relies on this to detect non-zero exits)."""
    from esphomerelease.exceptions import EsphomeReleaseError

    with pytest.raises(EsphomeReleaseError):
        util.execute_command("sh", "-c", "exit 1", fail_ok=True, silent=True)


def test_fail_ok_live_raises(util):
    """fail_ok under live mode (stderr is None) must still raise, not silently
    swallow the failure."""
    from esphomerelease.exceptions import EsphomeReleaseError

    with pytest.raises(EsphomeReleaseError):
        util.execute_command(
            "sh", "-c", "exit 1", fail_ok=True, live=True, silent=True
        )
