"""Tests for the config.json lookup and the friendly wrong-directory error.

``esphomerelease.config`` loads ``config.json`` from the cwd at import time, so
the tests chdir into temp trees and reload/evict modules. ``restore_modules``
puts ``sys.modules`` back afterwards so a failed import cannot break later tests.
"""

import importlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from esphomerelease.exceptions import ConfigNotFoundError

REPO_KEYS = (
    "esphome_path",
    "esphome_io_path",
    "esphome_hassio_path",
    "esphome_issues_path",
    "esphome_feature_requests_path",
)


@pytest.fixture(autouse=True)
def restore_modules() -> Iterator[None]:
    saved = {k: v for k, v in sys.modules.items() if k.startswith("esphomerelease")}
    yield
    added = [k for k in sys.modules if k.startswith("esphomerelease") and k not in saved]
    for name in [k for k in sys.modules if k.startswith("esphomerelease")]:
        del sys.modules[name]
    sys.modules.update(saved)
    # A leftover attribute for a module that was not imported before would be handed out
    # by ``from pkg import mod`` instead of a fresh import.
    for name in added:
        parent, _, child = name.rpartition(".")
        if parent in saved and hasattr(saved[parent], child):
            delattr(saved[parent], child)
    # Package attributes shadow sys.modules for ``import pkg.mod as x``.
    for name, mod in saved.items():
        parent, _, child = name.rpartition(".")
        if parent:
            setattr(sys.modules[parent], child, mod)


def _config_mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import esphomerelease.config with an empty valid config in place."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text("{}")
    return importlib.reload(importlib.import_module("esphomerelease.config"))


@pytest.fixture
def config_mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    base = tmp_path / "base"
    base.mkdir()
    mod = _config_mod(base, monkeypatch)
    # Pretend espr is installed in tmp_path/install (no config.json) outside a venv, so
    # the real repo root and venv of this checkout never leak into the lookup.
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "install/esphomerelease/config.py"))
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    return mod


def _real_root() -> Path:
    import esphomerelease

    return Path(esphomerelease.__file__).resolve().parent.parent


def _stub_real_root_config(monkeypatch: pytest.MonkeyPatch, present: bool, content: Path | None = None) -> None:
    """Force whether this checkout's own root appears to hold a config.json.

    Used by the tests that import the real module fresh (so its __file__ cannot be
    patched first). The real code path still runs; only the filesystem answer changes.
    """
    target = _real_root() / "config.json"
    real_is_file = Path.is_file

    def is_file(self: Path) -> bool:
        return present if self == target else real_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    if content is not None:
        real_open = Path.open

        def open_(self: Path, *args: Any, **kwargs: Any) -> Any:
            return real_open(content if self == target else self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_)
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)


def test_config_in_cwd_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text('{"a": 1}')
    config = importlib.reload(importlib.import_module("esphomerelease.config"))
    assert config.CONFIG == {"a": 1}


def _make_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cwd = tmp_path / "work" / "here"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)
    return cwd


def test_found_in_parent(config_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = _make_cwd(tmp_path, monkeypatch)
    (cwd.parent / "config.json").write_text("{}")
    with pytest.raises(ConfigNotFoundError) as exc:
        config_mod.load_config()
    assert str(exc.value) == (
        f"No config.json in {cwd}. You are in the wrong directory - run espr from:\n  {cwd.parent}"
    )


def test_found_in_sibling_not_self(config_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = _make_cwd(tmp_path, monkeypatch)
    sibling = cwd.parent / "release"
    sibling.mkdir()
    (sibling / "config.json").write_text("{}")
    (cwd.parent / "plain-file.txt").write_text("x")
    (cwd.parent / "no-config").mkdir()
    assert config_mod.find_nearby_config_dirs(cwd) == [sibling]


def test_found_in_child(config_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = _make_cwd(tmp_path, monkeypatch)
    child = cwd / "esphome-release"
    child.mkdir()
    (child / "config.json").write_text("{}")
    (cwd / "a-file").write_text("x")
    assert config_mod.find_nearby_config_dirs(cwd) == [child]


def test_multiple_sorted_and_listed(config_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = _make_cwd(tmp_path, monkeypatch)
    (cwd.parent / "config.json").write_text("{}")
    for d in (cwd.parent / "zeta", cwd / "alpha"):
        d.mkdir()
        (d / "config.json").write_text("{}")
    assert config_mod.find_nearby_config_dirs(cwd) == sorted([cwd.parent, cwd.parent / "zeta", cwd / "alpha"])
    with pytest.raises(ConfigNotFoundError) as exc:
        config_mod.load_config()
    lines = str(exc.value).splitlines()
    assert lines[0].endswith("run espr from:")
    assert len(lines) == 4


def test_nothing_found(config_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cwd = _make_cwd(tmp_path, monkeypatch)
    with pytest.raises(ConfigNotFoundError) as exc:
        config_mod.load_config()
    assert str(exc.value) == (
        f"No config.json in {cwd}, its parent, sibling or child directories, "
        "or the espr install location. "
        "Run espr from the esphome-release folder "
        "(see README.md to create config.json)."
    )
    assert exc.value.__suppress_context__ is True


def _evict(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [k for k in sys.modules if k.startswith("esphomerelease.")]:
        if name != "esphomerelease.exceptions":
            monkeypatch.delitem(sys.modules, name)
            monkeypatch.delattr(sys.modules["esphomerelease"], name.split(".")[1], raising=False)


def test_main_wrong_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    release = tmp_path / "release"
    release.mkdir()
    (release / "config.json").write_text("{}")
    monkeypatch.chdir(cwd)
    _stub_real_root_config(monkeypatch, present=False)
    _evict(monkeypatch)
    from esphomerelease.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (f"No config.json in {cwd}. You are in the wrong directory - run espr from:\n  {release}\n")
    assert "Traceback" not in captured.err


def test_main_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = {"github_token": "x", "step": False}
    config.update({key: str(repo_dir) for key in REPO_KEYS})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(config))
    _evict(monkeypatch)
    from esphomerelease import commands
    from esphomerelease.__main__ import main

    called: list[bool] = []
    monkeypatch.setattr(commands, "cli", lambda: called.append(True))
    main()
    assert called == [True]


def test_module_entry_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Running ``python -m esphomerelease`` goes through the real click CLI."""
    import runpy

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = {"github_token": "x", "step": False}
    config.update({key: str(repo_dir) for key in REPO_KEYS})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(config))
    _evict(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["espr", "--help"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("esphomerelease.__main__", run_name="__main__")
    assert exc.value.code == 0
    assert "Usage" in capsys.readouterr().out


def _install(tmp_path: Path, mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "install"
    (root / "esphomerelease").mkdir(parents=True)
    (root / "config.json").write_text("{}")
    monkeypatch.setattr(mod, "__file__", str(root / "esphomerelease/config.py"))
    return root


def _far_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cwd = tmp_path / "far" / "away" / "dir"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)
    return cwd


def test_found_via_venv_prefix(config_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "venvhome"
    (folder / ".venv").mkdir(parents=True)
    (folder / "config.json").write_text("{}")
    monkeypatch.setattr(sys, "prefix", str(folder / ".venv"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "python"))
    assert config_mod.find_install_config_dirs() == [folder.resolve()]


def test_not_in_venv_ignores_prefix(config_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "venvhome"
    (folder / ".venv").mkdir(parents=True)
    (folder / "config.json").write_text("{}")
    monkeypatch.setattr(sys, "prefix", str(folder / ".venv"))
    monkeypatch.setattr(sys, "base_prefix", str(folder / ".venv"))
    assert config_mod.find_install_config_dirs() == []


def test_auto_switch_via_module_location(
    config_mod: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _install(tmp_path, config_mod, monkeypatch)
    (root / "config.json").write_text('{"where": "install"}')
    cwd = _far_cwd(tmp_path, monkeypatch)
    assert config_mod.load_config() == {"where": "install"}
    assert Path.cwd() == root.resolve()
    assert capsys.readouterr().err == f"No config.json in {cwd} - running from {root.resolve()} instead.\n"


def test_auto_switch_via_venv_prefix(
    config_mod: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = tmp_path / "venvhome"
    (folder / ".venv").mkdir(parents=True)
    (folder / "config.json").write_text('{"where": "venv"}')
    monkeypatch.setattr(sys, "prefix", str(folder / ".venv"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "python"))
    _far_cwd(tmp_path, monkeypatch)
    assert config_mod.load_config() == {"where": "venv"}
    assert Path.cwd() == folder.resolve()
    assert str(folder.resolve()) in capsys.readouterr().err


def test_module_location_preferred_over_venv(
    config_mod: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _install(tmp_path, config_mod, monkeypatch)
    (root / "config.json").write_text('{"where": "install"}')
    folder = tmp_path / "venvhome"
    (folder / ".venv").mkdir(parents=True)
    (folder / "config.json").write_text('{"where": "venv"}')
    monkeypatch.setattr(sys, "prefix", str(folder / ".venv"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "python"))
    assert config_mod.find_install_config_dirs() == [root.resolve(), folder.resolve()]
    _far_cwd(tmp_path, monkeypatch)
    assert config_mod.load_config() == {"where": "install"}
    assert Path.cwd() == root.resolve()


def test_nearby_only_hints_and_does_not_chdir(
    config_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _make_cwd(tmp_path, monkeypatch)
    (cwd.parent / "config.json").write_text("{}")
    with pytest.raises(ConfigNotFoundError):
        config_mod.load_config()
    assert Path.cwd() == cwd.resolve()


def test_main_from_far_away_cwd_switches_to_install_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cwd = _far_cwd(tmp_path, monkeypatch)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = {"github_token": "x", "step": False}
    config.update({key: str(repo_dir) for key in REPO_KEYS})
    fake_config = tmp_path / "fake-config.json"
    fake_config.write_text(json.dumps(config))
    _stub_real_root_config(monkeypatch, present=True, content=fake_config)
    _evict(monkeypatch)
    from esphomerelease import commands
    from esphomerelease.__main__ import main

    called: list[bool] = []
    monkeypatch.setattr(commands, "cli", lambda: called.append(True))
    main()
    assert called == [True]
    assert Path.cwd() == _real_root()
    assert capsys.readouterr().err == f"No config.json in {cwd} - running from {_real_root()} instead.\n"
