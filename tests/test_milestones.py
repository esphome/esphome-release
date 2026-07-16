"""Tests for idempotent milestone handling during cuts.

``Project.ensure_milestone`` is a get-or-create guard: GitHub rejects duplicate
milestone titles with a 422, so a milestone that already exists (e.g. created
manually) must be looked up instead of re-created. The cut flows use it in
``_ensure_cycle_milestone``, ``_open_next_cycle_milestone`` (next cycle opened
at the first beta cut) and ``_close_cycle_milestone`` (safety net at the ``.0``
release).

``cutting`` imports ``.project``, which instantiates every ``Project`` at
import time and asserts each configured path is a directory. The ``cutting``
fixture writes a temp ``config.json`` whose paths point at real directories so
the module is importable, mirroring the import-safe reload pattern used
elsewhere in this repo.
"""

import importlib
import json

import pytest


@pytest.fixture
def cutting(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
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
    return cutting_mod


class FakeMilestone:
    def __init__(self, title: str, due_on=None):
        self.title = title
        self.due_on = due_on
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


def _all_projects(cutting):
    return [
        cutting.EsphomeProject,
        cutting.EsphomeDocsProject,
        cutting.EsphomeIssuesProject,
    ]


def _project_with_existing(cutting, monkeypatch, existing):
    """EsphomeProject where lookup finds ``existing`` and creation is forbidden."""
    proj = cutting.EsphomeProject
    monkeypatch.setattr(proj, "get_milestone_by_title", lambda title: existing)

    def fail_create(*a, **k):
        raise AssertionError("create_milestone must not be called")

    monkeypatch.setattr(proj, "create_milestone", fail_create)
    return proj


def test_ensure_milestone_returns_existing_without_creating(cutting, monkeypatch):
    """A manually-created milestone already due on the right day is left untouched.

    GitHub normalizes due dates to midnight US/Pacific, so the stored timestamp
    (07:00Z) never equals the noon-UTC one the tool sends; only the calendar
    day must match.
    """
    import datetime

    existing = FakeMilestone(
        "2026.8.0",
        due_on=datetime.datetime(2026, 8, 10, 7, tzinfo=datetime.timezone.utc),
    )
    proj = _project_with_existing(cutting, monkeypatch, existing)

    assert proj.ensure_milestone("2026.8.0", due_on="2026-08-10T12:00:00Z") is existing
    assert existing.updates == []


def test_ensure_milestone_without_due_leaves_existing_alone(cutting, monkeypatch):
    """When no due date is requested, an existing milestone is never touched."""
    import datetime

    existing = FakeMilestone(
        "2026.7.1",
        due_on=datetime.datetime(2026, 7, 20, 7, tzinfo=datetime.timezone.utc),
    )
    proj = _project_with_existing(cutting, monkeypatch, existing)

    assert proj.ensure_milestone("2026.7.1") is existing
    assert existing.updates == []


def test_ensure_milestone_corrects_wrong_due_date(cutting, monkeypatch):
    import datetime

    existing = FakeMilestone(
        "2026.8.0",
        due_on=datetime.datetime(2026, 8, 3, 7, tzinfo=datetime.timezone.utc),
    )
    proj = _project_with_existing(cutting, monkeypatch, existing)

    assert proj.ensure_milestone("2026.8.0", due_on="2026-08-10T12:00:00Z") is existing
    assert existing.updates == [{"due_on": "2026-08-10T12:00:00Z"}]


def test_ensure_milestone_sets_missing_due_date(cutting, monkeypatch):
    existing = FakeMilestone("2026.8.0", due_on=None)
    proj = _project_with_existing(cutting, monkeypatch, existing)

    assert proj.ensure_milestone("2026.8.0", due_on="2026-08-10T12:00:00Z") is existing
    assert existing.updates == [{"due_on": "2026-08-10T12:00:00Z"}]


def test_ensure_milestone_creates_missing(cutting, monkeypatch):
    proj = cutting.EsphomeProject
    created = []
    sentinel = FakeMilestone("2026.8.0")
    monkeypatch.setattr(proj, "get_milestone_by_title", lambda title: None)

    def create(title, *, due_on=None):
        created.append((title, due_on))
        return sentinel

    monkeypatch.setattr(proj, "create_milestone", create)

    assert proj.ensure_milestone("2026.8.0", due_on="due") is sentinel
    assert created == [("2026.8.0", "due")]


def _record_ensure(cutting, monkeypatch):
    calls = []
    for proj in _all_projects(cutting):

        def ensure(title, *, due_on=None, _proj=proj):
            calls.append((_proj.shortname, title, due_on))
            return FakeMilestone(title)

        monkeypatch.setattr(proj, "ensure_milestone", ensure)
    return calls


def test_ensure_cycle_milestone_ensures_on_all_projects(cutting, monkeypatch):
    from esphomerelease.model import Version

    calls = _record_ensure(cutting, monkeypatch)
    cutting._ensure_cycle_milestone(Version.parse("2026.7.0b1"))
    assert calls == [
        ("esphome", "2026.7.0", None),
        ("docs", "2026.7.0", None),
        ("issues", "2026.7.0", None),
    ]


def test_open_next_cycle_milestone(cutting, monkeypatch):
    """Cutting the 2026.7 cycle's first beta opens 2026.8.0 with the feature-freeze due date."""
    from esphomerelease.model import Version

    calls = _record_ensure(cutting, monkeypatch)
    cutting._open_next_cycle_milestone(Version.parse("2026.7.0b1"))
    # Feature freeze for August 2026: Monday Aug 10 (second Wednesday is Aug 12).
    due = "2026-08-10T12:00:00Z"
    assert calls == [
        ("esphome", "2026.8.0", due),
        ("docs", "2026.8.0", due),
        ("issues", "2026.8.0", due),
    ]


def test_open_next_cycle_milestone_december_rolls_over(cutting, monkeypatch):
    from esphomerelease.model import Version
    from esphomerelease.util import feature_freeze_date, milestone_due_on

    calls = _record_ensure(cutting, monkeypatch)
    cutting._open_next_cycle_milestone(Version.parse("2026.12.0b1"))
    due = milestone_due_on(feature_freeze_date(2027, 1))
    assert calls == [
        ("esphome", "2027.1.0", due),
        ("docs", "2027.1.0", due),
        ("issues", "2027.1.0", due),
    ]


def test_close_cycle_milestone_first_release(cutting, monkeypatch):
    """A ``.0`` release ensures the next cycle milestone and closes the old one."""
    from esphomerelease.model import Version

    calls = _record_ensure(cutting, monkeypatch)
    opened = []
    monkeypatch.setattr(
        cutting, "_open_next_cycle_milestone", lambda v: opened.append(v)
    )

    old_milestones = {}
    for proj in _all_projects(cutting):
        old = FakeMilestone("2026.7.0")
        old_milestones[proj.shortname] = old
        monkeypatch.setattr(proj, "get_milestone_by_title", lambda title, _old=old: _old)

    cutting._close_cycle_milestone(
        version=Version.parse("2026.7.0"),
        next_version=Version.parse("2026.7.1"),
    )

    assert opened == [Version.parse("2026.7.0")]
    assert calls == [
        ("esphome", "2026.7.1", None),
        ("docs", "2026.7.1", None),
        ("issues", "2026.7.1", None),
    ]
    for old in old_milestones.values():
        assert old.updates == [{"state": "closed"}]


def test_close_cycle_milestone_patch_release(cutting, monkeypatch):
    """A patch release opens no next-cycle milestone; a missing old milestone is skipped."""
    from esphomerelease.model import Version

    calls = _record_ensure(cutting, monkeypatch)
    opened = []
    monkeypatch.setattr(
        cutting, "_open_next_cycle_milestone", lambda v: opened.append(v)
    )
    for proj in _all_projects(cutting):
        monkeypatch.setattr(proj, "get_milestone_by_title", lambda title: None)

    cutting._close_cycle_milestone(
        version=Version.parse("2026.7.1"),
        next_version=Version.parse("2026.7.2"),
    )

    assert opened == []
    assert calls == [
        ("esphome", "2026.7.2", None),
        ("docs", "2026.7.2", None),
        ("issues", "2026.7.2", None),
    ]
