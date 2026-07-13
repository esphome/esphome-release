"""Tests for pushing the docs ``next``/``beta`` branches after a cut.

``update_local_copies`` merges the docs ``current`` branch into ``next`` and
``beta`` locally at the start of every cut; ``cutting._push_current_merge_branches``
pushes those branches once the cut has finished so the merge lands on the remote.

``cutting`` imports ``.project``, which instantiates every ``Project`` at import
time and asserts each configured path is a directory. The ``cutting`` fixture
writes a temp ``config.json`` whose paths point at real directories (a real git
working copy for the docs repo) so the module is importable, mirroring the
import-safe reload pattern used elsewhere in this repo.
"""

import importlib
import subprocess

import pytest


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _rev(cwd, ref):
    return subprocess.run(
        ["git", "rev-parse", ref],
        cwd=str(cwd),
        check=True,
        capture_output=True,
    ).stdout.decode().strip()


@pytest.fixture
def docs_repo(tmp_path):
    """A docs working copy with ``current``/``next``/``beta`` tracking a bare remote."""
    remote = tmp_path / "esphome.io.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-b", "current")

    work = tmp_path / "esphome.io"
    work.mkdir()
    _git(work, "init", "-b", "current")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    _git(work, "remote", "add", "origin", str(remote))
    (work / "README").write_text("init\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "init")
    for branch in ("next", "beta"):
        _git(work, "branch", branch)
    _git(work, "push", "-u", "origin", "current", "next", "beta")
    return work, remote


@pytest.fixture
def cutting(tmp_path, docs_repo, monkeypatch):
    work, _ = docs_repo
    # Every configured project path must be an existing directory.
    other = tmp_path / "other"
    other.mkdir()
    config = {
        "github_token": "x",
        "step": False,
        "esphome_path": str(other),
        "esphome_io_path": str(work),
        "esphome_hassio_path": str(other),
        "esphome_issues_path": str(other),
        "esphome_feature_requests_path": str(other),
    }
    monkeypatch.chdir(tmp_path)
    import json

    (tmp_path / "config.json").write_text(json.dumps(config))

    import esphomerelease.config as config_mod

    importlib.reload(config_mod)
    import esphomerelease.project as project_mod

    importlib.reload(project_mod)
    import esphomerelease.cutting as cutting_mod

    importlib.reload(cutting_mod)
    return cutting_mod


def test_push_current_merge_branches_pushes_next_and_beta(cutting, docs_repo):
    """The real helper checks out ``next``/``beta`` and pushes each to origin."""
    work, remote = docs_repo

    # Simulate the ``current`` merge landing on each branch locally, un-pushed.
    for branch in ("next", "beta"):
        _git(work, "checkout", branch)
        (work / branch).write_text("merge\n")
        _git(work, "add", ".")
        _git(work, "commit", "-m", f"merge current into {branch}")
        assert _rev(work, branch) != _rev(remote, branch)

    cutting._push_current_merge_branches()

    for branch in ("next", "beta"):
        assert _rev(work, branch) == _rev(remote, branch)


def _stub_cut_helpers(cutting, monkeypatch, recorder):
    """Neutralise every heavy helper a cut calls, recording the final push."""
    from esphomerelease.model import Version

    for name in (
        "_check_open_milestone_prs",
        "update_local_copies",
        "_docs_insert_changelog",
        "_docs_update_supporters",
        "_confirm_correct",
        "_create_prs",
        "_ensure_cycle_milestone",
        "_set_cycle_milestone_due",
        "_close_previous_month_patch_milestones",
        "_clear_merged_prs_from_cycle_milestone",
        "_close_cycle_milestone",
        "_mark_cherry_picked",
        "_strategy_merge",
        "_strategy_cherry_pick",
        "_strategy_merge_then_cherry_pick",
    ):
        monkeypatch.setattr(cutting, name, lambda *a, **k: [])

    monkeypatch.setattr(
        cutting, "_prompt_base_version", lambda **k: Version.parse("2026.5.0")
    )
    monkeypatch.setattr(
        cutting, "_push_current_merge_branches", lambda: recorder.append(True)
    )


def test_cut_release_pushes_merge_branches(cutting, monkeypatch):
    from esphomerelease.model import Version

    recorder = []
    _stub_cut_helpers(cutting, monkeypatch, recorder)

    cutting.cut_release(Version.parse("2026.6.1"))
    assert recorder == [True]


def test_cut_beta_release_pushes_merge_branches(cutting, monkeypatch):
    from esphomerelease.model import Version

    recorder = []
    _stub_cut_helpers(cutting, monkeypatch, recorder)

    cutting.cut_beta_release(Version.parse("2026.6.0b2"))
    assert recorder == [True]


def test_cut_first_beta_pushes_merge_branches(cutting, monkeypatch):
    """The first beta also bumps/pushes dev; the merge-branch push still runs."""
    from esphomerelease.model import Version

    recorder = []
    _stub_cut_helpers(cutting, monkeypatch, recorder)

    # The first-beta path bumps and pushes the dev branch of each project.
    for proj in (cutting.EsphomeProject, cutting.EsphomeDocsProject):
        monkeypatch.setattr(proj, "bump_version", lambda *a, **k: None)
        monkeypatch.setattr(proj, "push", lambda *a, **k: None)
        monkeypatch.setattr(proj, "checkout", lambda *a, **k: None)

    monkeypatch.setattr(cutting.click, "prompt", lambda *a, **k: "2026.7.0-dev")

    cutting.cut_beta_release(Version.parse("2026.6.0b1"))
    assert recorder == [True]
