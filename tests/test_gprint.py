"""Tests for esphomerelease.util.gprint brace handling.

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


def test_literal_braces_are_not_formatted(util, capsys):
    """Regression guard: a plain message with braces must not be .format()ed.

    Cutting the first beta printed a hint mentioning the ``{TAGLINE}`` and
    ``{DESCRIPTION}`` placeholders, which raised ``KeyError: 'TAGLINE'``.
    """
    util.gprint("Fill in the {TAGLINE} and {DESCRIPTION} placeholders manually")
    assert "{TAGLINE}" in capsys.readouterr().out


def test_args_are_formatted(util, capsys):
    util.gprint("Total: {}", 42)
    assert "Total: 42" in capsys.readouterr().out
