"""Tests for ``Project.merge_keeping_tree``, the history-only back-merge into dev.

Uses a real git repo: the point is that ``-s ours`` never changes the tree even
when ``-X ours`` would have auto-merged non-conflicting hunks from the release branch.
"""

import contextlib
import importlib
import json
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from esphomerelease.model import Branch

FUNC = "def feature():\n    return 1\n"
COPY = "def feature_copy():\n    return 1\n"
PADDING = "".join(f"# line {i}\n" for i in range(30))


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def commit(path: Path, files: dict[str, str | None], message: str) -> None:
    for name, content in files.items():
        if content is None:
            git(path, "rm", "-q", name)
        else:
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(content)
            git(path, "add", name)
    git(path, "commit", "-q", "-m", message)


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
    mods: list[ModuleType] = []
    for name in ("config", "util", "project", "cutting"):
        mod = importlib.import_module(f"esphomerelease.{name}")
        mods.append(importlib.reload(mod))
    return SimpleNamespace(util=mods[1], project=mods[2])


class Repo:
    """A project over a real repo with a `dev` branch and a `beta` branch forked from it."""

    def __init__(self, modules: SimpleNamespace, path: Path, back_merge_paths: tuple[str, ...] = ()) -> None:
        path.mkdir()
        git(path, "init", "-q", "-b", "dev")
        commit(path, {"const.py": 'VERSION = "1"\n', "core.py": PADDING + FUNC + PADDING, "old.py": "x = 1\n"}, "base")
        git(path, "branch", "beta")
        self.path = path
        self.proj = modules.project.Project(
            path=str(path), shortname="esphome", dev_branch="dev", beta_branch="beta", back_merge_paths=back_merge_paths
        )
        self.proj.branch = "dev"

    def on_beta(self, files: dict[str, str | None], message: str) -> None:
        git(self.path, "checkout", "-q", "beta")
        commit(self.path, files, message)
        git(self.path, "checkout", "-q", "dev")


@pytest.fixture
def prompts(modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []
    monkeypatch.setattr(modules.util, "confirm", asked.append)
    return asked


def assert_history_only(repo: Repo, before: str) -> None:
    assert git(repo.path, "rev-parse", "HEAD^2") == git(repo.path, "rev-parse", "beta")
    assert git(repo.path, "rev-parse", "HEAD^1") == before
    assert git(repo.path, "diff", "HEAD^1", "HEAD") == ""


def test_leak_case_warns_and_keeps_tree(
    modules: SimpleNamespace, tmp_path: Path, prompts: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    repo = Repo(modules, tmp_path / "leak")
    commit(repo.path, {"const.py": 'VERSION = "2"\n'}, "dev bump")
    # beta: a different-looking copy added far from dev's code, plus a conflicting bump
    repo.on_beta({"core.py": PADDING + FUNC + PADDING + COPY, "const.py": 'VERSION = "1b"\n'}, "alt pick")
    before = git(repo.path, "rev-parse", "HEAD")

    repo.proj.merge_keeping_tree(Branch.BETA)

    assert_history_only(repo, before)
    assert "feature_copy" not in (repo.path / "core.py").read_text()
    out = capsys.readouterr().out
    assert "would change these files" in out
    assert "core.py" in out
    assert len(prompts) == 1
    assert "dev" in prompts[0]


def test_clean_case_does_not_warn(
    modules: SimpleNamespace, tmp_path: Path, prompts: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    repo = Repo(modules, tmp_path / "clean")
    commit(repo.path, {"core.py": PADDING + FUNC + PADDING + COPY, "const.py": 'VERSION = "2"\n'}, "dev change")
    repo.on_beta({"core.py": PADDING + FUNC + PADDING + COPY, "const.py": 'VERSION = "1b"\n'}, "pick")
    before = git(repo.path, "rev-parse", "HEAD")

    repo.proj.merge_keeping_tree("beta")

    assert_history_only(repo, before)
    assert prompts == []
    assert "would change" not in capsys.readouterr().out


def test_unresolvable_conflict_still_merges(
    modules: SimpleNamespace, tmp_path: Path, prompts: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """dev deletes a file beta modifies: merge-tree exits 1 but still reports a tree."""
    repo = Repo(modules, tmp_path / "conflict")
    commit(repo.path, {"old.py": None}, "dev removes old.py")
    repo.on_beta({"old.py": "x = 2\n"}, "beta edits old.py")
    before = git(repo.path, "rev-parse", "HEAD")

    repo.proj.merge_keeping_tree("beta")

    assert_history_only(repo, before)
    assert not (repo.path / "old.py").exists()
    assert "old.py" in capsys.readouterr().out
    assert len(prompts) == 1


def test_non_interactive_does_not_prompt(
    modules: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_prompt(*args: object, **kwargs: object) -> bool:
        raise AssertionError("must not prompt")

    monkeypatch.setattr(modules.util.click, "confirm", no_prompt)
    modules.util.set_non_interactive(True)
    repo = Repo(modules, tmp_path / "ni")
    repo.on_beta({"core.py": PADDING + FUNC + PADDING + COPY}, "alt pick")
    before = git(repo.path, "rev-parse", "HEAD")

    repo.proj.merge_keeping_tree("beta")

    assert_history_only(repo, before)


def test_cutting_uses_it_for_dev_only(modules: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    cutting = importlib.import_module("esphomerelease.cutting")
    monkeypatch.setattr(cutting, "_publish_release", lambda **kwargs: None)
    log: list[tuple[object, ...]] = []

    class Proj:
        @contextlib.contextmanager
        def workon(self, branch: Branch):
            log.append(("workon", branch))
            yield

        def pull(self) -> None:
            pass

        def push(self) -> None:
            pass

        def merge(self, branch: Branch, *, ff_only: bool) -> None:
            log.append(("merge", branch, ff_only))

        def merge_keeping_tree(self, branch: Branch) -> None:
            log.append(("merge_keeping_tree", branch))

    from esphomerelease.model import Version

    cutting.publish_beta_release(Version.parse("2026.7.0b2"), projects=[Proj()], base=Version.parse("2026.7.0b1"))
    assert log == [("workon", Branch.DEV), ("merge_keeping_tree", Branch.BETA)]

    log.clear()
    cutting.publish_release(Version.parse("2026.7.2"), projects=[Proj()], base=Version.parse("2026.7.1"))
    assert log == [
        ("workon", Branch.BETA),
        ("merge", Branch.STABLE, True),
        ("workon", Branch.DEV),
        ("merge_keeping_tree", Branch.STABLE),
    ]


def test_merge_ff_only_fast_forwards(modules: SimpleNamespace, tmp_path: Path) -> None:
    repo = Repo(modules, tmp_path / "ff")
    git(repo.path, "checkout", "-q", "beta")
    repo.proj.branch = "beta"
    git(repo.path, "checkout", "-q", "dev")
    commit(repo.path, {"const.py": 'VERSION = "2"\n'}, "release work")
    git(repo.path, "checkout", "-q", "beta")

    repo.proj.merge("dev", ff_only=True)

    assert git(repo.path, "rev-parse", "HEAD") == git(repo.path, "rev-parse", "dev")
    assert git(repo.path, "rev-list", "--merges", "HEAD") == ""


def test_merge_ff_only_diverged_fails_loudly(modules: SimpleNamespace, tmp_path: Path) -> None:
    modules.util.set_non_interactive(True)
    repo = Repo(modules, tmp_path / "diverged")
    commit(repo.path, {"const.py": 'VERSION = "2"\n'}, "dev work")
    repo.on_beta({"old.py": "x = 2\n"}, "beta only")
    git(repo.path, "checkout", "-q", "beta")
    before = git(repo.path, "rev-parse", "HEAD")
    repo.proj.branch = "beta"

    with pytest.raises(modules.util.EsphomeReleaseError):
        repo.proj.merge("dev", ff_only=True)

    assert git(repo.path, "rev-parse", "HEAD") == before
    assert git(repo.path, "rev-list", "--merges", "HEAD") == ""


def test_merge_without_ff_only_makes_merge_commit(modules: SimpleNamespace, tmp_path: Path) -> None:
    repo = Repo(modules, tmp_path / "plain")
    commit(repo.path, {"const.py": 'VERSION = "2"\n'}, "dev work")
    repo.on_beta({"old.py": "x = 2\n"}, "beta only")
    git(repo.path, "checkout", "-q", "beta")
    repo.proj.branch = "beta"

    repo.proj.merge("dev")

    assert len(git(repo.path, "rev-list", "--merges", "HEAD").splitlines()) == 1


DOCS_PATHS = ("docs/changelog", "docs/supporters.mdx")


def docs_repo(modules: SimpleNamespace, path: Path) -> Repo:
    repo = Repo(modules, path, back_merge_paths=DOCS_PATHS)
    commit(
        repo.path,
        {"docs/changelog/x.mdx": "a\n", "docs/changelog-old/x.mdx": "o\n", "docs/supporters.mdx": "s1\ns2\ns3\n"},
        "docs base",
    )
    git(repo.path, "branch", "-f", "beta", "dev")
    return repo


def assert_merge_commit(repo: Repo) -> None:
    assert git(repo.path, "rev-parse", "HEAD^2") == git(repo.path, "rev-parse", "beta")
    assert len(git(repo.path, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3


def test_back_merge_paths_carried_and_rest_dropped(
    modules: SimpleNamespace, tmp_path: Path, prompts: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    repo = docs_repo(modules, tmp_path / "docs")
    commit(repo.path, {"const.py": 'VERSION = "2"\n', "docs/supporters.mdx": "d1\ns2\ns3\n"}, "dev change")
    beta_supporters = "b1\ns2\ns3\nb4\n"
    repo.on_beta(
        {
            "core.py": PADDING + FUNC + PADDING + COPY,
            "docs/changelog/x.mdx": "a\nb\nc\n",
            "docs/supporters.mdx": beta_supporters,
        },
        "release bump",
    )

    repo.proj.merge_keeping_tree(Branch.BETA)

    assert_merge_commit(repo)
    assert (repo.path / "docs/changelog/x.mdx").read_text() == "a\nb\nc\n"
    assert (repo.path / "docs/supporters.mdx").read_text() == beta_supporters
    assert "feature_copy" not in (repo.path / "core.py").read_text()
    assert git(repo.path, "status", "--porcelain") == ""
    out = capsys.readouterr().out
    warning, carried = out.split("Taking these files from")
    assert "core.py" in warning
    assert "docs/" not in warning
    assert "docs/changelog/x.mdx" in carried
    assert "docs/supporters.mdx" in carried
    assert len(prompts) == 1


def test_only_carried_paths_do_not_prompt(
    modules: SimpleNamespace, tmp_path: Path, prompts: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    repo = docs_repo(modules, tmp_path / "carried")
    repo.on_beta({"docs/changelog/x.mdx": "a\nb\n"}, "changelog")

    repo.proj.merge_keeping_tree("beta")

    assert_merge_commit(repo)
    assert (repo.path / "docs/changelog/x.mdx").read_text() == "a\nb\n"
    assert prompts == []
    out = capsys.readouterr().out
    assert "would change" not in out
    assert "docs/changelog/x.mdx" in out


def test_carried_deletion_is_applied(modules: SimpleNamespace, tmp_path: Path, prompts: list[str]) -> None:
    repo = docs_repo(modules, tmp_path / "deleted")
    repo.on_beta({"docs/changelog/x.mdx": None}, "drop page")

    repo.proj.merge_keeping_tree("beta")

    assert_merge_commit(repo)
    assert not (repo.path / "docs/changelog/x.mdx").exists()
    assert prompts == []


def test_concluding_commit_skips_pre_commit_hook(modules: SimpleNamespace, tmp_path: Path, prompts: list[str]) -> None:
    """The docs repo's pre-commit hook rejects direct commits to dev; a plain merge never runs it."""
    repo = docs_repo(modules, tmp_path / "hook")
    repo.on_beta({"docs/changelog/x.mdx": "a\nb\n"}, "changelog")
    hook = repo.path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'direct commits to dev are not allowed' >&2\nexit 1\n")
    hook.chmod(0o755)

    repo.proj.merge_keeping_tree(Branch.BETA)

    assert_merge_commit(repo)
    assert (repo.path / "docs/changelog/x.mdx").read_text() == "a\nb\n"
    assert prompts == []


def test_prefix_match_requires_directory_boundary(
    modules: SimpleNamespace, tmp_path: Path, prompts: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    repo = docs_repo(modules, tmp_path / "sibling")
    repo.on_beta({"docs/changelog-old/x.mdx": "changed\n"}, "sibling")
    before = git(repo.path, "rev-parse", "HEAD")

    repo.proj.merge_keeping_tree("beta")

    assert_history_only(repo, before)
    assert (repo.path / "docs/changelog-old/x.mdx").read_text() == "o\n"
    assert "docs/changelog-old/x.mdx" in capsys.readouterr().out
    assert len(prompts) == 1


def test_real_project_back_merge_paths(modules: SimpleNamespace) -> None:
    paths = modules.project.EsphomeDocsProject.back_merge_paths
    assert "src/content/docs/changelog" in paths
    assert "src/content/docs/guides/supporters.mdx" in paths
    assert "src/content/docs/blog" in paths
    assert "data/version.json" not in paths
    assert modules.project.EsphomeProject.back_merge_paths == ()
