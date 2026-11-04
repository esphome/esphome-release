"""Integration tests for ``release-cherry-pick`` overrides during a milestone pick.

The real ``cherry_pick_from_milestone``, ``_resolve_pick_sha`` and
``_verify_override`` run against a real temporary git repository (a bare
``origin`` plus a clone). Only the GitHub side and ``click.confirm`` are faked.
"""

import importlib
import json
import subprocess
import types
from datetime import datetime
from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from test_next_beta_prs import FakeIssue, FakePull, FakeRepo

MILESTONE = types.SimpleNamespace(title="2026.7.1", number=5, closed_issues=1, open_issues=0)
MISSING_SHA = "1" * 40


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)
    return out.stdout.strip()


def commit_file(cwd: Path, content: str, message: str) -> str:
    (cwd / "a.txt").write_text(content)
    git(cwd, "commit", "-qam", message)
    return git(cwd, "rev-parse", "HEAD")


class Scenario(types.SimpleNamespace):
    """SHAs and paths of the temporary repositories."""


@pytest.fixture
def scenario(tmp_path: Path) -> Scenario:
    """``release`` has R1; the PR's merge commit M (on ``dev``) conflicts with it.

    ``override``      single commit on top of R1 with the conflict resolved
    ``merge``         a merge commit
    ``foreign``       single commit whose parent (M) is not on ``release``
    ``fetch_only``    like ``override`` but absent from the clone until fetched
    """
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "release", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
        git(work, "config", key, value)
    git(work, "checkout", "-q", "-b", "release")
    (work / "a.txt").write_text("base\n")
    git(work, "add", ".")
    git(work, "commit", "-qm", "base")
    base = git(work, "rev-parse", "HEAD")

    git(work, "checkout", "-q", "-b", "dev")
    merge_commit = commit_file(work, "dev\n", "PR change")
    git(work, "checkout", "-q", "-b", "foreign-fix")
    foreign = commit_file(work, "foreign\n", "foreign override")

    git(work, "checkout", "-q", "release")
    commit_file(work, "release\n", "R1")
    r1 = git(work, "rev-parse", "HEAD")
    override = commit_file(work, "resolved\n", "resolved PR change")
    git(work, "reset", "-q", "--hard", r1)
    git(work, "checkout", "-q", "-b", "side", base)
    commit_file(work, "side\n", "side")
    git(work, "checkout", "-q", "release")
    git(work, "merge", "-q", "-s", "ours", "--no-edit", "side")
    merge_sha = git(work, "rev-parse", "HEAD")
    git(work, "branch", "merge-fix")
    git(work, "reset", "-q", "--hard", r1)

    # Publish everything, then make a (non-hardlinked) clone that only knows ``release``.
    for ref in ("dev", "foreign-fix", "merge-fix"):
        git(work, "push", "-q", "origin", ref)
    git(work, "update-ref", "refs/heads/pr-fix", override)
    git(work, "push", "-q", "origin", "release", "pr-fix")
    project_dir = tmp_path / "project"
    git(tmp_path, "clone", "-q", "--branch", "release", "--single-branch", origin.as_uri(), str(project_dir))
    for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
        git(project_dir, "config", key, value)
    # The PR's merge commit is on dev, which a real checkout already has.
    git(project_dir, "fetch", "-q", "origin", "dev")
    return Scenario(
        project_dir=project_dir,
        merge_commit=merge_commit,
        override=override,
        foreign=foreign,
        merge=merge_sha,
        r1=r1,
    )


@pytest.fixture
def modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    import esphomerelease.commands as commands_mod

    importlib.reload(commands_mod)
    return project_mod, cutting_mod, commands_mod


class Confirms:
    """Scripted ``click.confirm``: records prompts, pops answers (default True)."""

    def __init__(self, answers: list[bool]):
        self.answers = answers
        self.prompts: list[tuple[str, dict]] = []

    def __call__(self, text: str, **kwargs) -> bool:
        self.prompts.append((text, kwargs))
        return self.answers.pop(0) if self.answers else True


def block(branch: str, commit: str) -> str:
    return f"intro\n```release-cherry-pick\nbranch: {branch}\ncommit: {commit}\n```\n"


@pytest.fixture
def make_project(modules, scenario: Scenario, monkeypatch: pytest.MonkeyPatch):
    project_mod = modules[0]

    def make(body: str | None, *, answers: list[bool] | None = None, real_pick: bool = False):
        proj = project_mod.Project(
            path=str(scenario.project_dir),
            shortname="esphome",
            stable_branch="release",
            beta_branch="beta",
        )
        pull = FakePull(
            7,
            title="Fix thing",
            merged_at=datetime(2026, 7, 1),
            merge_commit_sha=scenario.merge_commit,
            body=body,
            merged_by=types.SimpleNamespace(login="bob"),
        )
        proj._repo = FakeRepo(
            closed_issues=[FakeIssue(7, pr=True, merged_at="2026-07-01T00:00:00Z", title="Fix thing")],
            pulls={7: pull},
        )
        monkeypatch.setattr(proj, "_resolve_milestone_index_drift", lambda milestone: [])
        confirms = Confirms(answers or [])
        monkeypatch.setattr(click, "confirm", confirms)
        picked: list[str] = []
        if not real_pick:
            monkeypatch.setattr(proj, "cherry_pick", picked.append)
        return proj, confirms, picked

    return make


def test_confirmed_override_is_cherry_picked_for_real(make_project, scenario, capsys):
    proj, confirms, _ = make_project(block("release", scenario.override), answers=[True], real_pick=True)

    result = proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert len(result) == 1
    assert (scenario.project_dir / "a.txt").read_text() == "resolved\n"
    # Exactly one prompt: the default-No override confirmation.
    assert len(confirms.prompts) == 1
    text, kwargs = confirms.prompts[0]
    assert kwargs == {"default": False}
    assert scenario.override in text
    assert scenario.merge_commit in text
    out = capsys.readouterr().out
    assert f"Cherry picking Fix thing: {scenario.override} (override from PR body)" in out
    assert "Author:         alice" in out
    assert "Merged by:      bob" in out
    assert "Range-diff" in out
    assert "resolved PR change" in out  # git log -1 output
    assert "not based on the current branch" not in out


def test_override_missing_from_clone_is_fetched(make_project, scenario):
    # ``foreign`` lives on origin/foreign-fix, which the single-branch clone never fetched.
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{scenario.foreign}^{{commit}}"], cwd=str(scenario.project_dir), check=False
    )
    assert proc.returncode != 0
    proj, _, picked = make_project(block("release", scenario.foreign), answers=[True])

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.foreign]
    assert git(scenario.project_dir, "cat-file", "-t", scenario.foreign) == "commit"


def test_declined_override_falls_back(make_project, scenario, capsys):
    proj, _, picked = make_project(block("release", scenario.override), answers=[False])

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.merge_commit]
    out = capsys.readouterr().out
    assert f"Using the original merge commit {scenario.merge_commit}" in out
    assert "override from PR body" not in out


def test_override_without_merged_by(make_project, scenario, capsys):
    proj, _, picked = make_project(block("release", scenario.override), answers=[True])
    proj.repo.pull_request(7).merged_by = None

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.override]
    assert "Merged by" not in capsys.readouterr().out


def test_parse_errors_fall_back(make_project, scenario, capsys):
    body = "```release-cherry-pick\nbranch: release\ncommit: nothex\n```\n"
    proj, confirms, picked = make_project(body, answers=[False, True])

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.merge_commit]
    # Acknowledgement loops until yes.
    assert len(confirms.prompts) == 2
    out = capsys.readouterr().out
    assert "Invalid release-cherry-pick block in #7 Fix thing" in out
    assert "ignored" in out


def test_no_target_with_overrides_is_ignored(make_project, scenario, capsys):
    proj, confirms, picked = make_project(block("release", scenario.override))

    proj.cherry_pick_from_milestone(MILESTONE)

    assert picked == [scenario.merge_commit]
    assert len(confirms.prompts) == 1
    assert "no target branch is known" in capsys.readouterr().out


def test_no_target_without_overrides_is_silent(make_project, scenario):
    proj, confirms, picked = make_project(None)

    proj.cherry_pick_from_milestone(MILESTONE)

    assert picked == [scenario.merge_commit]
    assert confirms.prompts == []


def test_prose_only_mention_warns(make_project, scenario, capsys):
    body = f"Release manager: cherry-pick https://github.com/esphome/esphome/commit/{scenario.override} instead"
    proj, confirms, picked = make_project(body)

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.merge_commit]
    assert len(confirms.prompts) == 1
    out = capsys.readouterr().out
    assert "mentions a replacement commit in prose" in out
    assert "#7 Fix thing" in out


def test_other_branch_override_ignored_silently(make_project, scenario):
    proj, confirms, picked = make_project(block("beta", scenario.override))

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.merge_commit]
    assert confirms.prompts == []


def test_missing_commit_falls_back(make_project, scenario, capsys):
    proj, confirms, picked = make_project(block("release", MISSING_SHA))

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.merge_commit]
    assert len(confirms.prompts) == 1
    assert "could not be found" in capsys.readouterr().out


def test_merge_commit_override_rejected(make_project, scenario, capsys):
    proj, confirms, picked = make_project(block("release", scenario.merge))

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.merge_commit]
    assert len(confirms.prompts) == 1
    assert "not a single non-merge commit" in capsys.readouterr().out


def test_root_commit_override_rejected(make_project, scenario, capsys):
    root = git(scenario.project_dir, "rev-list", "--max-parents=0", "HEAD")
    proj, _, picked = make_project(block("release", root))

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.merge_commit]
    assert "not a single non-merge commit" in capsys.readouterr().out


def test_override_not_based_on_head_warns_then_prompts(make_project, scenario, capsys):
    proj, confirms, picked = make_project(block("release", scenario.foreign), answers=[True])

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.foreign]
    assert "not based on the current branch" in capsys.readouterr().out
    assert confirms.prompts[0][1] == {"default": False}


def test_uppercase_sha_is_normalised(make_project, scenario):
    proj, _, picked = make_project(block("release", scenario.override.upper()), answers=[True])

    proj.cherry_pick_from_milestone(MILESTONE, target_branch="release")

    assert picked == [scenario.override]


def test_strategy_wiring_passes_target_branch(modules):
    _, cutting, _ = modules
    from esphomerelease.model import Branch, Version

    calls: list[tuple] = []

    class FakeProject:
        def lookup_branch(self, branch):
            return {Branch.STABLE: "release", Branch.BETA: "beta"}[branch]

        def get_milestone_by_title(self, title):
            return "milestone"

        def checkout(self, branch):
            pass

        def checkout_new_branch(self, name):
            pass

        def merge(self, head, strategy_option):
            pass

        def bump_version(self, version):
            pass

        def cherry_pick_from_milestone(self, milestone, target_branch=None):
            calls.append((milestone, target_branch))
            return ["picked"]

    version = Version.parse("2026.7.1")
    assert cutting._strategy_cherry_pick(FakeProject(), version, base=Branch.STABLE) == ["picked"]
    assert cutting._strategy_cherry_pick(FakeProject(), version, base=Branch.BETA) == ["picked"]
    assert cutting._strategy_merge_then_cherry_pick(FakeProject(), version, base=Branch.STABLE, head=Branch.BETA) == [
        "picked"
    ]
    assert calls == [("milestone", "release"), ("milestone", "beta"), ("milestone", "release")]


def test_milestone_cherry_pick_target_option(modules, monkeypatch):
    _, _, commands = modules
    calls: list[tuple] = []
    milestone = object()

    for proj in (commands.EsphomeProject, commands.EsphomeDocsProject):
        monkeypatch.setattr(proj, "get_milestone_by_title", lambda title: milestone)
        monkeypatch.setattr(
            proj,
            "cherry_pick_from_milestone",
            lambda m, target_branch=None, p=proj: calls.append((p.name, target_branch)) or [],
        )
        monkeypatch.setattr(proj, "mark_pulls_cherry_picked", lambda picked: None)

    runner = CliRunner()
    args = ["milestone-cherry-pick", "2026.7.1"]
    result = runner.invoke(commands.cli, [*args, "--target", "release"], input="y\ny\ny\ny\n")
    assert result.exit_code == 0, result.output
    assert [c[1] for c in calls] == ["release", "release"]

    calls.clear()
    result = runner.invoke(commands.cli, args, input="y\ny\ny\ny\n")
    assert result.exit_code == 0, result.output
    assert [c[1] for c in calls] == [None, None]
