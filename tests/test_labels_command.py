"""Tests for the ``labels`` subcommand's label listing and lookup logic.

``commands`` imports ``.project``, which instantiates every ``Project`` at
import time and asserts each configured path is a directory. The ``commands``
fixture writes a temp ``config.json`` whose paths point at real directories so
the modules are importable, mirroring the import-safe reload pattern used
elsewhere in this repo.
"""

import importlib
import json

import pytest
from click.testing import CliRunner


@pytest.fixture
def commands(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    components = repo_dir / "esphome" / "components"
    for comp in ("alpha", "beta", "gamma"):
        (components / comp).mkdir(parents=True)
        (components / comp / "__init__.py").touch()
    # A directory without __init__.py and a plain file are both skipped.
    (components / "not_a_component").mkdir()
    (components / "stray.py").touch()

    config = {
        "github_token": "x",
        "step": False,
        "esphome_path": str(repo_dir),
        "esphome_io_path": str(repo_dir),
        "esphome_hassio_path": str(repo_dir),
        "esphome_issues_path": str(repo_dir),
        "esphome_feature_requests_path": str(repo_dir),
    }
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(config))

    import esphomerelease.config as config_mod

    importlib.reload(config_mod)
    import esphomerelease.project as project_mod

    importlib.reload(project_mod)
    import esphomerelease.cutting as cutting_mod

    importlib.reload(cutting_mod)
    import esphomerelease.commands as commands_mod

    importlib.reload(commands_mod)
    return commands_mod


class FakeLabel:
    def __init__(self, name: str):
        self.name = name
        self.updates = []

    def update(self, *, name: str, color: str) -> None:
        self.updates.append(name)


class FakeLabelRepo:
    def __init__(self, name: str, labels=None):
        self.name = name
        self._labels = labels or []
        self.created = []
        self.labels_calls = 0

    def labels(self):
        self.labels_calls += 1
        return list(self._labels)

    def create_label(self, *, name: str, color: str) -> None:
        self.created.append(name)


def test_labels_updates_creates_and_renames(commands, monkeypatch):
    """Old ``integration:`` labels are renamed, existing ``component:``
    labels are kept, missing ones are created, and stray ``integration:``
    labels are migrated — with labels listed once per repo."""
    old_alpha = FakeLabel("Integration: alpha")
    existing_beta = FakeLabel("component: beta")
    stray = FakeLabel("integration: stray")
    esphome_repo = FakeLabelRepo("esphome", [old_alpha, existing_beta, stray])
    other_repos = {
        name: FakeLabelRepo(name)
        for name in ("issues", "feature-requests", "esphome.io")
    }
    repos = {"esphome": esphome_repo, **other_repos}

    session = type(
        "FakeSession",
        (),
        {"repository": lambda self, owner, name: repos[name]},
    )()
    monkeypatch.setattr(commands, "get_session", lambda: session)

    result = CliRunner().invoke(commands.cli, ["labels"])

    assert result.exit_code == 0, result.output
    # alpha's old label renamed in the component pass, stray migrated in the
    # cleanup pass, beta already present, gamma created.
    assert old_alpha.updates == ["component: alpha"]
    assert stray.updates == ["component: stray"]
    assert existing_beta.updates == []
    assert esphome_repo.created == ["component: gamma"]
    # Repos without labels create everything.
    for repo in other_repos.values():
        assert repo.created == [
            "component: alpha",
            "component: beta",
            "component: gamma",
        ]
        assert repo.labels_calls == 1
    assert esphome_repo.labels_calls == 1
