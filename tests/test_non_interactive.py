"""Tests for ``--non-interactive`` (patch releases from CI, no TTY).

Every prompt reachable from a patch ``cut`` or ``publish`` either proceeds
with a logged assumption or raises. The end-to-end tests drive the real click
CLI with the git and GitHub layers faked and with ``click.confirm`` /
``click.prompt`` wired to fail, proving nothing waits for a terminal.
"""

import importlib
import json
import types
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import click
import pytest
from click.testing import CliRunner

from esphomerelease.exceptions import EsphomeReleaseError
from esphomerelease.model import Version

PATCH = Version.parse("2026.6.1")


@pytest.fixture
def modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
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
    import esphomerelease.util as util_mod

    importlib.reload(util_mod)
    import esphomerelease.project as project_mod

    importlib.reload(project_mod)
    import esphomerelease.cutting as cutting_mod

    importlib.reload(cutting_mod)
    import esphomerelease.commands as commands_mod

    importlib.reload(commands_mod)
    return SimpleNamespace(util=util_mod, project=project_mod, cutting=cutting_mod, commands=commands_mod)


@pytest.fixture
def ni(modules: SimpleNamespace) -> SimpleNamespace:
    """The modules with non-interactive mode switched on."""
    modules.util.set_non_interactive(True)
    return modules


def _no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("prompted in non-interactive mode")

    monkeypatch.setattr(click, "confirm", fail)
    monkeypatch.setattr(click, "prompt", fail)
    monkeypatch.setattr(click, "launch", fail)


# ---------------------------------------------------------------------- util


def test_state_roundtrip(modules: SimpleNamespace) -> None:
    assert modules.util.is_non_interactive() is False
    modules.util.require_interactive("anything")
    modules.util.set_non_interactive(True)
    assert modules.util.is_non_interactive() is True
    with pytest.raises(EsphomeReleaseError, match=r"Non-interactive mode cannot continue: needs a human"):
        modules.util.require_interactive("needs a human")


def test_ask_prompt(modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(click, "prompt", lambda text, default: f"typed {default}")
    assert modules.util.ask_prompt("Base?", default="2026.6.0") == "typed 2026.6.0"
    modules.util.set_non_interactive(True)
    _no_tty(monkeypatch)
    assert modules.util.ask_prompt("Base?", default="2026.6.0") == "2026.6.0"
    assert "Non-interactive: Base?: using 2026.6.0" in capsys.readouterr().out


def test_confirm_assumes_yes(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    _no_tty(monkeypatch)
    ni.util.confirm(click.style("Publish it?", fg="red"))
    assert "Non-interactive: assuming yes for: Publish it?" in capsys.readouterr().out


def test_confirm_interactive_asks(modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([False, True])
    monkeypatch.setattr(click, "confirm", lambda text: next(answers))
    modules.util.confirm("ok?")
    assert next(answers, None) is None


def test_open_helpers_skip(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("launched something")

    monkeypatch.setattr(ni.util.subprocess, "Popen", fail)
    monkeypatch.setattr(ni.util.subprocess, "run", fail)
    monkeypatch.setattr(click, "launch", fail)
    ni.util.open_url("https://example.com")
    ni.util.open_vscode("file")


def test_discard_local_changes_raises(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    monkeypatch.setattr(type(ni.project.EsphomeProject), "has_local_changes", property(lambda self: True))
    with pytest.raises(EsphomeReleaseError, match=r"local changes in the esphome repository"):
        ni.util._discard_local_changes()


def test_failed_command_raises(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    with pytest.raises(EsphomeReleaseError, match=r"command failed: false"):
        ni.util.execute_command("false", silent=True)


def test_retry_prompt_raises(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    import esphomerelease.github as github_mod

    _no_tty(monkeypatch)
    with pytest.raises(EsphomeReleaseError, match=r"GitHub request GET https://x failed: HTTP 502"):
        github_mod.RetryingGitHubSession._ask_retry("GET", "https://x", "HTTP 502", True)


# ------------------------------------------------------------------- cutting


def test_base_version_uses_default(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    assert ni.cutting._prompt_base_version(PATCH) == Version.parse("2026.6.0")


def _pr(number: int) -> SimpleNamespace:
    return SimpleNamespace(
        number=number, title=f"PR {number}", html_url=f"https://github.com/esphome/esphome/pull/{number}"
    )


def test_open_prs_raise(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    for proj in (ni.cutting.EsphomeProject, ni.cutting.EsphomeDocsProject):
        monkeypatch.setattr(proj, "get_milestone_by_title", lambda title: SimpleNamespace(number=1))
        monkeypatch.setattr(proj, "get_open_prs_for_milestone", lambda milestone: [_pr(5)])
    with pytest.raises(EsphomeReleaseError, match=r"open PRs on the 2026.6.1 milestone"):
        ni.cutting._check_open_milestone_prs(PATCH, block=True)


def test_open_prs_only_warn_for_non_blocking(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    monkeypatch.setattr(ni.cutting.EsphomeProject, "get_milestone_by_title", lambda title: SimpleNamespace(number=1))
    monkeypatch.setattr(ni.cutting.EsphomeDocsProject, "get_milestone_by_title", lambda title: None)
    monkeypatch.setattr(ni.cutting.EsphomeProject, "get_open_prs_for_milestone", lambda milestone: [_pr(5)])
    ni.cutting._check_open_milestone_prs(Version.parse("2026.6.0b2"), block=False)


def test_unmerged_docs_prs_raise(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    code_pr = SimpleNamespace(number=1, title="code")
    docs_pr = SimpleNamespace(number=2, title="docs", html_url="https://example.com/2")
    monkeypatch.setattr(
        ni.cutting, "_find_docs_pr_pairs", lambda version: ni.cutting.DocsPRFindings([(code_pr, docs_pr)], [], [])
    )
    with pytest.raises(EsphomeReleaseError, match=r"merge the unmerged docs PRs"):
        ni.cutting._check_linked_docs_prs(PATCH)


def test_unmilestoned_docs_prs_raise(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    code_pr = SimpleNamespace(number=1, title="code")
    docs_pr = SimpleNamespace(
        number=2, title="docs", html_url="https://example.com/2", milestone=None, base=SimpleNamespace(ref="next")
    )
    monkeypatch.setattr(
        ni.cutting, "_find_docs_pr_pairs", lambda version: ni.cutting.DocsPRFindings([], [], [(code_pr, docs_pr)])
    )
    with pytest.raises(EsphomeReleaseError, match=r"not on the 2026.6.1 docs milestone"):
        ni.cutting._check_linked_docs_prs(PATCH)


def test_docs_lint_failure_raises(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    docs = ni.cutting.EsphomeDocsProject
    monkeypatch.setattr(docs, "workon", lambda branch: __import__("contextlib").nullcontext())
    monkeypatch.setattr(docs, "run_command", lambda *a, **k: b"")
    monkeypatch.setattr(ni.cutting, "_docs_lint_failures", lambda: ["npm run lint"])
    with pytest.raises(EsphomeReleaseError, match=r"docs lint failed \(npm run lint\)"):
        ni.cutting._lint_docs(PATCH)


class MergeablePR:
    state = "open"
    html_url = "https://github.com/esphome/esphome/pull/9"

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def merge(self, merge_method: str) -> bool:
        return self.ok


def _merge(ni: SimpleNamespace, prs: list[Any]) -> None:
    proj = SimpleNamespace(shortname="code", get_pr_by_title=lambda **kwargs: prs)
    ni.cutting._merge_release_pr(proj=proj, version=PATCH, head_branch=ni.cutting.Branch.STABLE)


def test_merge_release_pr_policies(ni: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    _merge(ni, [MergeablePR()])
    with pytest.raises(EsphomeReleaseError, match=r"no release PR found for code 2026.6.1"):
        _merge(ni, [])
    with pytest.raises(EsphomeReleaseError, match=r"multiple release PRs found for code"):
        _merge(ni, [MergeablePR(), MergeablePR()])
    with pytest.raises(EsphomeReleaseError, match=r"merging the code release PR failed"):
        _merge(ni, [MergeablePR(ok=False)])


# ------------------------------------------------------------------- project


@pytest.fixture
def proj(modules: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    project = modules.project.Project(path=str(tmp_path / "repo"), shortname="esphome", repo_name="esphome")
    project.branch = "release"
    project.git_calls = []  # type: ignore[attr-defined]
    project.fail_git = set()  # type: ignore[attr-defined]

    def run_git(*args: str, **kwargs: Any) -> bytes:
        project.git_calls.append(args)
        if args in project.fail_git:
            raise EsphomeReleaseError("git failed")
        return b""

    monkeypatch.setattr(project, "run_git", run_git)
    return project


def _pull(body: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        number=7,
        title="Fix thing",
        html_url="https://github.com/esphome/esphome/pull/7",
        body=body,
        merge_commit_sha="a" * 40,
        user=SimpleNamespace(login="alice"),
    )


BLOCK = "```release-cherry-pick\nbranch: release\ncommit: " + "b" * 40 + "\n```\n"


def test_invalid_override_block_raises(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    pull = _pull("```release-cherry-pick\nbranch: release\ncommit: nothex\n```\n")
    with pytest.raises(EsphomeReleaseError, match=r"invalid release-cherry-pick block in #7 Fix thing"):
        proj._resolve_pick_sha(pull, "release")


def test_override_without_target_raises(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    with pytest.raises(EsphomeReleaseError, match=r"no target branch is known"):
        proj._resolve_pick_sha(_pull(BLOCK), None)


def test_prose_override_raises(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    body = "Release manager: cherry-pick https://github.com/esphome/esphome/commit/" + "b" * 40 + " instead"
    with pytest.raises(EsphomeReleaseError, match=r"mentions a replacement commit in prose"):
        proj._resolve_pick_sha(_pull(body), "release")


def test_plain_pr_uses_merge_commit(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    assert proj._resolve_pick_sha(_pull(None), "release") == "a" * 40


def test_override_commit_missing_raises(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    proj.fail_git.add(("cat-file", "-e", f"{'b' * 40}^{{commit}}"))
    with pytest.raises(EsphomeReleaseError, match=r"override commit .* for #7 not found"):
        proj._resolve_pick_sha(_pull(BLOCK), "release")


def test_override_merge_commit_raises(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    monkeypatch.setattr(proj, "run_git", lambda *a, **k: b"sha p1 p2" if a[0] == "rev-list" else b"")
    with pytest.raises(EsphomeReleaseError, match=r"is not a single non-merge commit"):
        proj._resolve_pick_sha(_pull(BLOCK), "release")


def test_valid_override_is_never_accepted(
    ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _no_tty(monkeypatch)
    monkeypatch.setattr(proj, "run_git", lambda *a, **k: b"sha parent" if a[0] == "rev-list" else b"")
    with pytest.raises(EsphomeReleaseError, match=r"#7 \(https://github.com/esphome/esphome/pull/7\) has a release"):
        proj._resolve_pick_sha(_pull(BLOCK), "release")
    # The PR, commit and range-diff are shown before giving up.
    out = capsys.readouterr().out
    assert "Release-cherry-pick override for #7" in out
    assert "Range-diff" in out


def test_unmerged_pr_raises(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    issue = SimpleNamespace(number=3, title="Open one", pull_request_urls={"merged_at": None}, original_labels=[])
    monkeypatch.setattr(proj, "_milestone_pr_issues", lambda milestone, state: [issue])
    with pytest.raises(EsphomeReleaseError, match=r"merge 'Open one' \(#3\)"):
        proj.cherry_pick_from_milestone(SimpleNamespace(title="m", number=1))


def test_milestone_drift_raises_with_commands(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    proj._repo = SimpleNamespace(issues=lambda **kw: [])
    drifted = SimpleNamespace(number=4)
    monkeypatch.setattr(proj, "_find_drifted_milestone_prs", lambda milestone, known: [drifted])
    monkeypatch.setattr(
        proj, "get_prs", lambda numbers: [SimpleNamespace(merge_commit_sha="c" * 40, number=4, title="T")]
    )
    monkeypatch.setattr(proj, "_spawn_subshell", lambda **kw: pytest.fail("spawned a shell"))
    milestone = SimpleNamespace(title="2026.6.1", number=1, closed_issues=1)
    with pytest.raises(EsphomeReleaseError, match=r"git cherry-pick " + "c" * 40):
        proj._resolve_milestone_index_drift(milestone)


def test_milestone_drift_warns_when_not_all_identified(
    ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj._repo = SimpleNamespace(issues=lambda **kw: [])
    monkeypatch.setattr(proj, "_find_drifted_milestone_prs", lambda milestone, known: [])
    monkeypatch.setattr(proj, "get_prs", lambda numbers: [])
    milestone = SimpleNamespace(title="2026.6.1", number=1, closed_issues=1)
    with pytest.raises(EsphomeReleaseError, match=r"only identified 0 of 1"):
        proj._resolve_milestone_index_drift(milestone)


def test_fix_and_retry_raises(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    with pytest.raises(EsphomeReleaseError, match=r"GitHub rejected creating the PR: boom"):
        proj._ask_fix_and_retry("creating the PR", ValueError("boom"))


def test_draft_release_raises(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    rel = SimpleNamespace(html_url="https://github.com/esphome/esphome/releases/tag/2026.6.1")
    proj._repo = SimpleNamespace(create_release=lambda *a, **k: rel)
    monkeypatch.setattr(proj, "push", lambda: None)
    monkeypatch.setattr(ni.project.time, "sleep", lambda s: None)
    with pytest.raises(EsphomeReleaseError, match=r"publish the draft release at .*/edit/2026.6.1"):
        proj.create_release(PATCH, draft=True)


def test_merge_conflict_aborts_and_raises(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    monkeypatch.setattr(proj, "_repo_name", "esphome")

    def fail_merge(*args: str, on_fail: Any = None, **kwargs: Any) -> bytes:
        proj.git_calls.append(args)
        return on_fail(b"")

    monkeypatch.setattr(proj, "run_git", fail_merge)
    monkeypatch.setattr(proj, "_git_ok", lambda *a: proj.git_calls.append(("ok", *a)) or True)
    with pytest.raises(EsphomeReleaseError, match=r"Merging beta into release failed"):
        proj.merge("beta")
    # Aborted the merge and stopped before trying the cherry-pick abort.
    assert ("ok", "merge", "--abort") in proj.git_calls
    assert ("ok", "cherry-pick", "--abort") not in proj.git_calls


def test_cherry_pick_conflict_aborts_and_raises_with_pr_link(
    ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_tty(monkeypatch)
    monkeypatch.setattr(proj, "_repo_name", "esphome")

    def fail_pick(*args: str, on_fail: Any = None, **kwargs: Any) -> bytes:
        return on_fail(b"")

    monkeypatch.setattr(proj, "run_git", fail_pick)
    # Not a merge: the merge abort fails, the cherry-pick abort succeeds.
    attempted: list[str] = []

    def git_ok(*args: str) -> bool:
        attempted.append(args[0])
        return args[0] == "cherry-pick"

    monkeypatch.setattr(proj, "_git_ok", git_ok)
    monkeypatch.setattr(proj, "_spawn_subshell", lambda **kw: pytest.fail("spawned a shell"))
    with pytest.raises(EsphomeReleaseError, match=r"PR #7: Fix thing - https://github.com/esphome/esphome/pull/7"):
        proj.cherry_pick("d" * 40, pull=_pull(), issue=SimpleNamespace())
    assert attempted == ["merge", "cherry-pick"]


def test_spawn_subshell_interactive_unchanged(
    modules: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(click, "confirm", lambda *a, **k: False)
    proj._spawn_subshell(run="git status", print_lines=["x"])


# ----------------------------------------------------------------------- CLI


def _invoke(modules: SimpleNamespace, *args: str, env: dict[str, str] | None = None) -> Any:
    return CliRunner().invoke(modules.commands.cli, list(args), env=env)


def test_step_conflicts(modules: SimpleNamespace) -> None:
    result = _invoke(modules, "--step", "--non-interactive", "cut", "2026.6.1")
    assert result.exit_code == 2
    assert "--step cannot be combined with --non-interactive" in result.output


def test_watch_conflicts(modules: SimpleNamespace) -> None:
    result = _invoke(modules, "--non-interactive", "cut", "--watch", "2026.6.1")
    assert result.exit_code == 1
    assert "--watch cannot be combined with --non-interactive" in result.output


@pytest.mark.parametrize("version", ["2026.6.0", "2026.7.0b1", "2026.6.1b2", "2026.6.1-dev"])
def test_cut_refuses_non_patch(modules: SimpleNamespace, version: str) -> None:
    result = _invoke(modules, "--non-interactive", "cut", version)
    assert result.exit_code == 1
    assert f"--non-interactive only supports patch releases, not {version}" in result.output


def test_publish_refuses_non_patch(modules: SimpleNamespace) -> None:
    result = _invoke(modules, "--non-interactive", "publish", "2026.6.0")
    assert result.exit_code == 1
    assert "only supports patch releases" in result.output


def test_env_var_enables_mode(modules: SimpleNamespace) -> None:
    result = _invoke(modules, "cut", "2026.6.0", env={"ESPR_NON_INTERACTIVE": "1"})
    assert result.exit_code == 1
    assert "only supports patch releases" in result.output
    assert modules.util.is_non_interactive() is True


def test_interactive_default_still_allows_everything(modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(modules.commands.cutting, "cut_release", calls.append)
    monkeypatch.setattr(modules.commands, "_commit_user_cache_if_changed", lambda: calls.append("cache"))
    monkeypatch.setattr(modules.commands.ci_watch, "watch_release_ci", lambda v, base: calls.append("watch"))
    result = _invoke(modules, "cut", "2026.6.0")
    assert result.exit_code == 0, result.output
    assert calls == [Version.parse("2026.6.0"), "cache", "watch"]
    assert modules.util.is_non_interactive() is False


def test_cut_patch_end_to_end(modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A patch cut through the real CLI, cutting gates and strategy; git and GitHub faked."""
    _no_tty(monkeypatch)
    cutting = modules.cutting
    events: list[str] = []
    monkeypatch.setattr(modules.commands, "_commit_user_cache_if_changed", lambda: pytest.fail("pushed user cache"))
    monkeypatch.setattr(modules.commands.ci_watch, "watch_release_ci", lambda *a, **k: pytest.fail("watched CI"))
    monkeypatch.setattr(modules.commands.EsphomeProject, "latest_release", lambda **kw: Version.parse("2026.6.0"))
    monkeypatch.setattr(cutting, "update_local_copies", lambda: events.append("update"))
    monkeypatch.setattr(cutting, "propagate_docs_current_branch", lambda: events.append("propagate"))
    monkeypatch.setattr(cutting, "_docs_update_supporters", lambda version: events.append("supporters"))
    monkeypatch.setattr(cutting, "_close_cycle_milestone", lambda **kw: events.append("close"))
    monkeypatch.setattr(cutting, "_mark_cherry_picked", lambda picked: events.append("label"))

    def create_prs(*, version: Version, base: Version, target_branch: Any) -> None:
        events.append(f"prs {version} base={base}")

    def insert_changelog(*, version: Version, base: Version) -> None:
        # Reviewing the page is a confirm(): it proceeds when non-interactive.
        cutting.confirm("Does the release notes blog post look correct?")
        events.append("changelog")

    monkeypatch.setattr(cutting, "_create_prs", create_prs)
    monkeypatch.setattr(cutting, "_docs_insert_changelog", insert_changelog)
    for proj in (cutting.EsphomeProject, cutting.EsphomeDocsProject):
        monkeypatch.setattr(proj, "get_milestone_by_title", lambda title: None)
        monkeypatch.setattr(proj, "checkout", lambda branch: None)
        monkeypatch.setattr(proj, "checkout_new_branch", lambda branch: None)
        monkeypatch.setattr(proj, "bump_version", lambda version: None)

    result = _invoke(modules, "--non-interactive", "cut", "s")

    assert result.exit_code == 0, result.output
    assert events == [
        "update",
        "propagate",
        "changelog",
        "supporters",
        "prs 2026.6.1 base=2026.6.0",
        "close",
        "label",
    ]
    assert "Non-interactive: Please enter base" in result.output
    assert "Non-interactive: assuming yes for: Please confirm everything is correct" in result.output
    assert "Non-interactive: not committing the user cache" in result.output


def test_cut_patch_end_to_end_stops_on_open_prs(modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tty(monkeypatch)
    cutting = modules.cutting
    monkeypatch.setattr(cutting, "_default_base_version", lambda version: Version.parse("2026.6.0"))
    monkeypatch.setattr(cutting, "update_local_copies", lambda: pytest.fail("went on after open PRs"))
    monkeypatch.setattr(cutting.EsphomeDocsProject, "get_milestone_by_title", lambda title: None)
    monkeypatch.setattr(cutting.EsphomeProject, "get_milestone_by_title", lambda title: SimpleNamespace(number=1))
    monkeypatch.setattr(cutting.EsphomeProject, "get_open_prs_for_milestone", lambda milestone: [_pr(5)])

    result = _invoke(modules, "--non-interactive", "cut", "2026.6.1")

    assert result.exit_code == 1
    assert isinstance(result.exception, EsphomeReleaseError)
    assert "open PRs on the 2026.6.1 milestone" in str(result.exception)


def test_publish_patch_end_to_end(modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A patch publish through the real CLI: merge release PRs, create releases, back-merge."""
    _no_tty(monkeypatch)
    project_mod = modules.project
    cutting = modules.cutting
    git: list[tuple[str, ...]] = []
    created: list[tuple[str, str, bool]] = []
    merged: list[str] = []

    def fake_execute(*args: str, **kwargs: Any) -> bytes:
        git.append(args)
        return b""

    monkeypatch.setattr(project_mod, "execute_command", fake_execute)
    monkeypatch.setattr(project_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(cutting, "update_local_copies", lambda: None)
    monkeypatch.setattr(cutting.changelog, "generate", lambda **kw: "changelog")
    monkeypatch.setattr(cutting, "changelog_too_long", lambda md: False)
    monkeypatch.setattr(cutting, "use_website_link_for_release", lambda version, is_primary_project: False)

    for proj in (cutting.EsphomeProject, cutting.EsphomeDocsProject):
        release_pr = types.SimpleNamespace(
            state="open",
            html_url="https://example.com/pr",
            merge=lambda merge_method, name=proj.shortname: merged.append(name) or True,
        )
        monkeypatch.setattr(proj, "get_pr_by_title", lambda release_pr=release_pr, **kw: [release_pr])

        class Repo:
            def __init__(self, name: str) -> None:
                self.name = name

            def create_release(self, tag: str, **kwargs: Any) -> Any:
                created.append((self.name, tag, kwargs["prerelease"]))
                return SimpleNamespace(html_url="https://example.com/release")

        proj._repo = Repo(proj.shortname)

    result = _invoke(modules, "--non-interactive", "publish", "2026.6.1")

    assert result.exit_code == 0, result.output
    assert merged == ["esphome", "docs"]
    assert created == [("esphome", "2026.6.1", False), ("docs", "2026.6.1", False)]
    assert ("git", "merge", "-X", "ours", "release") in git or any("merge" in c for c in git)
    assert "Non-interactive: assuming yes for: Publish version 2026.6.1?" in result.output
    assert "Non-interactive: Please enter base" in result.output
    assert isinstance(modules.project, ModuleType)


def test_give_up_with_nothing_in_progress(ni: SimpleNamespace, proj: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    attempted: list[str] = []
    monkeypatch.setattr(proj, "_git_ok", lambda *args: attempted.append(args[0]) or False)
    with pytest.raises(EsphomeReleaseError, match=r"manual conflict resolution needed\nline"):
        proj._give_up_if_non_interactive(["line"])
    assert attempted == ["merge", "cherry-pick"]
