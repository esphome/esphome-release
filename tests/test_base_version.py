"""Tests for figuring out the base (previous) version for cut/publish.

``_default_base_version`` derives the changelog base from the version being
released so the first step of a cut/publish no longer paginates the repo's
entire release history; only first-beta/first-release fall back to a single
``releases/latest`` lookup. ``Project.latest_release`` (still used by
``release-notes`` and ``next-beta-prs``) now only scans the most recently
created releases instead of every page.

``cutting`` imports ``.project``, which instantiates every ``Project`` at
import time and asserts each configured path is a directory. The ``modules``
fixture writes a temp ``config.json`` whose paths point at real directories so
the modules are importable, mirroring the import-safe reload pattern used
elsewhere in this repo.
"""

import contextlib
import importlib
import json
import types

import pytest

from esphomerelease.model import Branch, Version


@pytest.fixture
def modules(tmp_path, monkeypatch):
    """Reload config/project/cutting against a temp config.json."""
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
    return project_mod, cutting_mod


class FakeReleasesRepo:
    """Fake github3 repository exposing just the release lookups."""

    def __init__(self, *, latest_tag: str, all_tags: list[str]):
        self._latest_tag = latest_tag
        self._all_tags = all_tags
        self.releases_number: int | None = None

    def latest_release(self):
        return types.SimpleNamespace(tag_name=self._latest_tag)

    def releases(self, number: int):
        self.releases_number = number
        return [types.SimpleNamespace(tag_name=tag) for tag in self._all_tags]


def test_previous_patch_version():
    assert Version.parse("2026.6.2").previous_patch_version == Version.parse("2026.6.1")


def test_previous_patch_version_of_first_release_raises():
    with pytest.raises(ValueError):
        Version.parse("2026.6.0").previous_patch_version


def test_default_base_for_later_beta_needs_no_api(modules):
    """b2+ derives the previous beta arithmetically — no GitHub call at all."""
    _, cutting = modules
    cutting.EsphomeProject._repo = None  # any API access would blow up

    assert cutting._default_base_version(Version.parse("2026.7.0b3")) == Version.parse(
        "2026.7.0b2"
    )


def test_default_base_for_patch_release_needs_no_api(modules):
    """Patch releases derive the previous patch arithmetically."""
    _, cutting = modules
    cutting.EsphomeProject._repo = None

    assert cutting._default_base_version(Version.parse("2026.6.2")) == Version.parse(
        "2026.6.1"
    )


def test_default_base_for_first_beta_uses_latest_stable(modules):
    """b1 has no derivable base; it asks GitHub for the latest stable release."""
    _, cutting = modules
    cutting.EsphomeProject._repo = FakeReleasesRepo(
        latest_tag="2026.6.3", all_tags=[]
    )

    assert cutting._default_base_version(Version.parse("2026.7.0b1")) == Version.parse(
        "2026.6.3"
    )


def test_default_base_for_first_full_release_uses_latest_stable(modules):
    """x.y.0 has no derivable base; it asks GitHub for the latest stable release."""
    _, cutting = modules
    cutting.EsphomeProject._repo = FakeReleasesRepo(
        latest_tag="2026.6.3", all_tags=[]
    )

    assert cutting._default_base_version(Version.parse("2026.7.0")) == Version.parse(
        "2026.6.3"
    )


def test_prompt_base_version_offers_derived_default(modules, monkeypatch):
    """The prompt defaults to the derived base and parses the answer."""
    _, cutting = modules
    cutting.EsphomeProject._repo = None
    seen = {}

    def fake_prompt(text, default):
        seen["default"] = default
        return default

    monkeypatch.setattr(cutting.click, "prompt", fake_prompt)

    assert cutting._prompt_base_version(Version.parse("2026.7.0b2")) == Version.parse(
        "2026.7.0b1"
    )
    assert seen["default"] == "2026.7.0b1"


class FakePublishProject:
    """Records the post-publish branch merges without touching git."""

    def __init__(self):
        self.calls = []

    @contextlib.contextmanager
    def workon(self, branch):
        self.calls.append(("workon", branch))
        yield

    def pull(self):
        self.calls.append(("pull",))

    def merge(self, branch, strategy):
        self.calls.append(("merge", branch, strategy))

    def push(self):
        self.calls.append(("push",))


def test_publish_beta_release_derives_base_without_api(modules, monkeypatch):
    """Publishing b2 derives the b1 base instantly and merges beta into dev."""
    _, cutting = modules
    cutting.EsphomeProject._repo = None  # any API access would blow up
    monkeypatch.setattr(cutting.click, "prompt", lambda text, default: default)

    published = {}
    monkeypatch.setattr(
        cutting,
        "_publish_release",
        lambda **kwargs: published.update(kwargs),
    )

    proj = FakePublishProject()
    cutting.publish_beta_release(Version.parse("2026.7.0b2"), projects=[proj])

    assert published["base"] == Version.parse("2026.7.0b1")
    assert proj.calls == [
        ("workon", Branch.DEV),
        ("pull",),
        ("merge", Branch.BETA, "ours"),
        ("push",),
    ]


def test_publish_release_derives_base_without_api(modules, monkeypatch):
    """Publishing a patch derives the previous patch base instantly and
    merges stable back into beta and dev."""
    _, cutting = modules
    cutting.EsphomeProject._repo = None
    monkeypatch.setattr(cutting.click, "prompt", lambda text, default: default)

    published = {}
    monkeypatch.setattr(
        cutting,
        "_publish_release",
        lambda **kwargs: published.update(kwargs),
    )

    proj = FakePublishProject()
    cutting.publish_release(Version.parse("2026.7.2"), projects=[proj])

    assert published["base"] == Version.parse("2026.7.1")
    assert proj.calls == [
        ("workon", Branch.BETA),
        ("pull",),
        ("merge", Branch.STABLE, "ours"),
        ("push",),
        ("workon", Branch.DEV),
        ("pull",),
        ("merge", Branch.STABLE, "ours"),
        ("push",),
    ]


def test_latest_release_only_scans_recent_releases(modules, tmp_path):
    """Prerelease-inclusive lookup fetches one bounded page, newest first,
    skipping unparseable tags."""
    project_mod, _ = modules
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")
    repo = FakeReleasesRepo(
        latest_tag="2026.6.3",
        all_tags=["not-a-version", "2026.7.0b2", "2026.7.0b1", "2026.6.3"],
    )
    proj._repo = repo

    assert proj.latest_release() == Version.parse("2026.7.0b2")
    assert repo.releases_number == project_mod.Project.RECENT_RELEASES_TO_CHECK


def test_latest_release_stable_uses_single_lookup(modules, tmp_path):
    """The stable-only path uses GitHub's dedicated latest-release endpoint."""
    project_mod, _ = modules
    proj = project_mod.Project(path=str(tmp_path / "repo"), shortname="esphome")
    proj._repo = FakeReleasesRepo(latest_tag="2026.6.3", all_tags=[])

    assert proj.latest_release(include_prereleases=False) == Version.parse("2026.6.3")
