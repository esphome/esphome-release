"""Tests for the docs lint gate run before a cut pushes and opens its PRs.

The gate runs the docs repo's CI lint steps on the bump branch; failures are
fixed by the user and committed before lint re-runs, and declining to retry
aborts the cut before anything is pushed.

The ``cutting`` fixture lives in ``conftest.py``.
"""

import pytest

from esphomerelease.exceptions import EsphomeReleaseError
from esphomerelease.model import Version

VERSION = Version.parse("2026.10.0b1")
LS_IGNORED = (
    "git",
    "ls-files",
    "--others",
    "--ignored",
    "--exclude-standard",
    "--directory",
)


@pytest.fixture
def docs(cutting, monkeypatch):
    """Record docs project commands, checkouts and commits instead of running them."""
    proj = cutting.EsphomeDocsProject
    calls = {"run": [], "checkout": [], "commit": [], "fail": set(), "ignored": b""}

    def run_command(*args, **kwargs):
        calls["run"].append(args)
        if args in calls["fail"]:
            calls["fail"].discard(args)
            raise EsphomeReleaseError("Failed running command!")
        if args == LS_IGNORED:
            return calls["ignored"]
        return b""

    monkeypatch.setattr(proj, "run_command", run_command)
    monkeypatch.setattr(proj, "checkout", lambda b: calls["checkout"].append(b))
    monkeypatch.setattr(
        proj, "commit", lambda msg, **kw: calls["commit"].append((msg, kw))
    )
    return calls


def test_lint_passes_runs_every_ci_step_on_bump_branch(cutting, docs, monkeypatch):
    monkeypatch.setattr(
        cutting.click, "confirm", lambda *a, **k: pytest.fail("no prompt expected")
    )

    cutting._lint_docs(VERSION)

    assert docs["checkout"] == ["bump-2026.10.0b1"]
    assert docs["run"] == [("npm", "ci"), LS_IGNORED, *cutting.DOCS_LINT_COMMANDS]
    assert docs["commit"] == []


def test_lint_skips_gitignored_paths(cutting, docs, monkeypatch, capsys):
    """markdownlint and eslint walk the filesystem, so local-only content
    (caches, Claude worktrees, venvs) is passed to them as ignores; contents
    git lists under an already ignored directory collapse into it."""
    docs["ignored"] = (
        b".claude/worktrees/\n"
        b".pytest_cache/\n"
        b".pytest_cache/README.md\n"
        b"script/cache/\n"
        b"src/data/allowed-referrers.json\n"
    )
    ignored = [
        ".claude/worktrees/",
        ".pytest_cache/",
        "script/cache/",
        "src/data/allowed-referrers.json",
    ]
    markdownlint = (
        "npx",
        "markdownlint",
        "--config",
        ".markdownlintrc",
        *(arg for path in ignored for arg in ("--ignore", path)),
        ".",
    )
    lint_js = (
        "npm",
        "run",
        "lint:js",
        "--",
        *(arg for path in ignored for arg in ("--ignore-pattern", path)),
    )
    docs["fail"] = {markdownlint, lint_js}
    monkeypatch.setattr(cutting.click, "confirm", lambda *a, **k: False)

    with pytest.raises(EsphomeReleaseError, match="Docs lint failed"):
        cutting._lint_docs(VERSION)

    _, format_check, _, lint, images = cutting.DOCS_LINT_COMMANDS
    assert docs["run"] == [
        ("npm", "ci"),
        LS_IGNORED,
        markdownlint,
        format_check,
        lint_js,
        lint,
        images,
    ]
    # Failures are reported by the base command, not the ignore-laden one.
    assert (
        "Docs lint failed: npx markdownlint --config .markdownlintrc ., "
        "npm run lint:js"
    ) in capsys.readouterr().out


def test_lint_failure_reports_all_steps_then_commits_fixes(
    cutting, docs, monkeypatch, capsys
):
    lint, images = cutting.DOCS_LINT_COMMANDS[3], cutting.DOCS_LINT_COMMANDS[4]
    docs["fail"] = {lint, images}
    prompts = []
    monkeypatch.setattr(
        cutting.click, "confirm", lambda text, **k: prompts.append(text) or True
    )

    cutting._lint_docs(VERSION)

    # Both failing steps ran in the first pass, then the whole lint re-ran.
    assert docs["run"] == [
        ("npm", "ci"),
        LS_IGNORED,
        *cutting.DOCS_LINT_COMMANDS,
        LS_IGNORED,
        *cutting.DOCS_LINT_COMMANDS,
    ]
    assert "Docs lint failed: npm run lint, npm run check:images" in (
        capsys.readouterr().out
    )
    assert len(prompts) == 1
    assert docs["commit"] == [("Fix docs lint for 2026.10.0b1", {"ignore_empty": True})]


def test_lint_failure_declined_aborts_without_commit(cutting, docs, monkeypatch):
    docs["fail"] = {cutting.DOCS_LINT_COMMANDS[0]}
    monkeypatch.setattr(cutting.click, "confirm", lambda *a, **k: False)

    with pytest.raises(EsphomeReleaseError, match="Docs lint failed"):
        cutting._lint_docs(VERSION)

    assert docs["commit"] == []


def test_create_prs_lints_before_opening_any_pr(cutting, monkeypatch):
    created = []
    for proj in (cutting.EsphomeProject, cutting.EsphomeDocsProject):
        monkeypatch.setattr(proj, "create_pr", lambda **kw: created.append(kw))

    def failing_lint(version):
        raise EsphomeReleaseError("Docs lint failed, not creating the PRs")

    monkeypatch.setattr(cutting, "_lint_docs", failing_lint)

    with pytest.raises(EsphomeReleaseError):
        cutting._create_prs(
            version=VERSION,
            base=Version.parse("2026.9.0"),
            target_branch=cutting.Branch.BETA,
        )

    assert created == []
