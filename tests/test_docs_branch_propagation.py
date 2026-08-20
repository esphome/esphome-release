"""Tests for propagating the docs ``current`` branch into ``next``/``beta``.

Docs fixes for the released version land on ``current``, so every cut first
merges it into ``next`` and ``beta``. ``propagate_docs_current_branch`` pushes
that merge immediately: a merge left sitting locally makes the next ``git
pull`` on the branch fail to fast-forward, which blocks the following cut.
``update_local_copies`` is now purely local (discard + pull), and publishing
only calls that.

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
    return (
        subprocess.run(
            ["git", "rev-parse", ref],
            cwd=str(cwd),
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )


def _is_ancestor(cwd, ancestor, branch):
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, branch],
            cwd=str(cwd),
            capture_output=True,
        ).returncode
        == 0
    )


def _commit(work, branch, name):
    _git(work, "checkout", branch)
    (work / name).write_text("content\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", f"{name} on {branch}")


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
    # Every configured project path must be an existing directory; the code
    # project is a clean git repo so the local-changes check can run on it.
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "dev")
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "config", "user.name", "Test")
    (other / "README").write_text("init\n")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "init")
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


def test_propagate_merges_current_and_pushes(cutting, docs_repo):
    """A fix on ``current`` reaches ``next``/``beta`` on the remote, not just locally."""
    work, remote = docs_repo
    _commit(work, "current", "fix")
    _git(work, "push")

    cutting.propagate_docs_current_branch()

    for branch in ("next", "beta"):
        assert _rev(work, branch) == _rev(remote, branch)
        assert _is_ancestor(work, "current", branch)


def test_propagate_pushes_merge_left_behind_by_an_earlier_run(cutting, docs_repo):
    """A branch left ahead of the remote is pushed, so the next pull fast-forwards."""
    work, remote = docs_repo
    for branch in ("next", "beta"):
        _commit(work, branch, f"stale-{branch}")
        assert _rev(work, branch) != _rev(remote, branch)

    cutting.propagate_docs_current_branch()

    for branch in ("next", "beta"):
        assert _rev(work, branch) == _rev(remote, branch)


def test_propagate_without_changes_does_not_push(cutting, docs_repo, monkeypatch):
    """Nothing to propagate means no push at all."""
    work, _ = docs_repo
    pushes = []
    monkeypatch.setattr(
        cutting.EsphomeDocsProject, "push", lambda *a, **k: pushes.append(True)
    )

    cutting.propagate_docs_current_branch()

    assert pushes == []


def test_update_local_copies_does_not_merge_or_push(cutting, docs_repo, monkeypatch):
    """The local sync only pulls; it must not leave unpushed merges behind."""
    import esphomerelease.project as project_mod
    from esphomerelease import util

    work, remote = docs_repo
    _commit(work, "current", "fix")
    _git(work, "push")
    # Only the docs project is a real repo here; stub the rest of the sync.
    monkeypatch.setattr(util, "_discard_local_changes", lambda: None)
    for proj in (project_mod.EsphomeProject, project_mod.EsphomeHassioProject):
        monkeypatch.setattr(proj, "checkout_pull", lambda *a, **k: None)

    util.update_local_copies()

    for branch in ("next", "beta"):
        assert _rev(work, branch) == _rev(remote, branch)
        # current's fix has not been merged in either.
        assert _rev(work, branch) != _rev(work, "current")


def _stub_cut_helpers(cutting, monkeypatch, order):
    """Neutralise every heavy helper a cut calls, recording the pre-flight steps."""
    from esphomerelease.model import Version

    for name in (
        "_check_open_milestone_prs",
        "_check_linked_docs_prs",
        "_docs_insert_changelog",
        "_docs_update_supporters",
        "_confirm_correct",
        "_create_prs",
        "_ensure_cycle_milestone",
        "_set_cycle_milestone_due",
        "_open_next_cycle_milestone",
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
        cutting, "_prompt_base_version", lambda *a, **k: Version.parse("2026.5.0")
    )
    monkeypatch.setattr(cutting, "update_local_copies", lambda: order.append("update"))
    monkeypatch.setattr(
        cutting, "propagate_docs_current_branch", lambda: order.append("propagate")
    )


def test_cut_release_propagates_after_updating(cutting, monkeypatch):
    from esphomerelease.model import Version

    order = []
    _stub_cut_helpers(cutting, monkeypatch, order)

    cutting.cut_release(Version.parse("2026.6.1"))
    assert order == ["update", "propagate"]


def test_cut_beta_release_propagates_after_updating(cutting, monkeypatch):
    from esphomerelease.model import Version

    order = []
    _stub_cut_helpers(cutting, monkeypatch, order)
    opened = []
    monkeypatch.setattr(
        cutting, "_open_next_cycle_milestone", lambda v: opened.append(v)
    )

    cutting.cut_beta_release(Version.parse("2026.6.0b2"))
    assert order == ["update", "propagate"]
    # Only the first beta opens the next cycle's milestone.
    assert opened == []


def test_cut_first_beta_propagates_after_updating(cutting, monkeypatch):
    """The first beta also bumps/pushes dev; propagation still runs up front."""
    from esphomerelease.model import Version

    order = []
    _stub_cut_helpers(cutting, monkeypatch, order)
    opened = []
    monkeypatch.setattr(
        cutting, "_open_next_cycle_milestone", lambda v: opened.append(v)
    )

    # The first-beta path bumps and pushes the dev branch of each project.
    for proj in (cutting.EsphomeProject, cutting.EsphomeDocsProject):
        monkeypatch.setattr(proj, "bump_version", lambda *a, **k: None)
        monkeypatch.setattr(proj, "push", lambda *a, **k: None)
        monkeypatch.setattr(proj, "checkout", lambda *a, **k: None)

    monkeypatch.setattr(cutting.click, "prompt", lambda *a, **k: "2026.7.0-dev")

    cutting.cut_beta_release(Version.parse("2026.6.0b1"))
    assert order == ["update", "propagate"]
    assert opened == [Version.parse("2026.6.0b1")]


def test_publish_only_updates_local_copies(cutting, monkeypatch):
    """Publishing must not create docs merges it would leave unpushed."""
    from esphomerelease.model import Branch, Version

    order = []
    monkeypatch.setattr(cutting, "update_local_copies", lambda: order.append("update"))
    monkeypatch.setattr(
        cutting, "propagate_docs_current_branch", lambda: order.append("propagate")
    )
    monkeypatch.setattr(cutting, "confirm", lambda *a, **k: None)

    cutting._publish_release(
        version=Version.parse("2026.6.0b2"),
        base=Version.parse("2026.6.0b1"),
        head_branch=Branch.BETA,
        prerelease=True,
        projects=[],
    )

    assert order == ["update"]


def test_discard_local_changes_leaves_clean_repos_alone(cutting, monkeypatch):
    """Nothing to discard means nothing is asked and nothing is reset."""
    import click

    from esphomerelease import util

    asked = []
    monkeypatch.setattr(click, "confirm", lambda *a, **k: asked.append(a) or True)

    util._discard_local_changes()

    assert asked == []


def test_discard_local_changes_resets_when_confirmed(cutting, docs_repo, monkeypatch):
    """Confirming wipes both tracked edits and untracked files."""
    import click

    from esphomerelease import util

    work, _ = docs_repo
    (work / "README").write_text("edited\n")
    (work / "untracked").write_text("junk\n")
    monkeypatch.setattr(click, "confirm", lambda *a, **k: True)

    util._discard_local_changes()

    assert (work / "README").read_text() == "init\n"
    assert not (work / "untracked").exists()


def test_discard_local_changes_aborts_when_declined(cutting, docs_repo, monkeypatch):
    """Declining aborts the release instead of touching the working copy."""
    import click

    from esphomerelease import util
    from esphomerelease.exceptions import EsphomeReleaseError

    work, _ = docs_repo
    (work / "README").write_text("edited\n")
    monkeypatch.setattr(click, "confirm", lambda *a, **k: False)

    with pytest.raises(EsphomeReleaseError, match="local changes in docs"):
        util._discard_local_changes()

    assert (work / "README").read_text() == "edited\n"
