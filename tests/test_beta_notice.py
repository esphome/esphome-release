"""Tests for the automatic changelog page management on esphome.io.

Cutting the first beta creates the cycle's changelog page skeleton (header,
import, a featured-components table drafted from ImgTable rows added to the
components index since the base release) and inserts the beta notice block;
cutting the stable release removes the notice again. Everything is idempotent
because repeated betas and patch releases hit the same ``<cycle>.0.mdx`` file.

``cutting`` imports ``.project``, which instantiates every ``Project`` at
import time and asserts each configured path is a directory. The ``cutting``
fixture writes a temp ``config.json`` whose paths point at real directories so
the module is importable, mirroring the import-safe reload pattern used
elsewhere in this repo.
"""

import contextlib
import importlib
import json
import subprocess
from pathlib import Path

import pytest

FRONTMATTER = """\
---
description: "Changelog for ESPHome 2026.7.0."
title: "ESPHome 2026.7.0 - July 2026"
pagefind: false
slug: "changelog/2026.7.0"
---
"""

NOTICE = (
    "> [!NOTE]\n"
    "> This is a beta release. Details on this page may change before the stable release is published."
)

STABLE_PAGE = f"""\
{FRONTMATTER}
import ImgTable from "@components/ImgTable.astro";

{{/* MANUAL: Add featured components here */}}
<ImgTable items={{[]}} />
"""

BETA_PAGE = f"""\
{FRONTMATTER}
{NOTICE}

import ImgTable from "@components/ImgTable.astro";

{{/* MANUAL: Add featured components here */}}
<ImgTable items={{[]}} />
"""

# The pre-automation layout put the notice after the import line; removal
# must handle pages that still look like that.
LEGACY_BETA_PAGE = f"""\
{FRONTMATTER}
import ImgTable from "@components/ImgTable.astro";

{NOTICE}

{{/* MANUAL: Add featured components here */}}
<ImgTable items={{[]}} />
"""


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


INDEX_BASE = """\
<ImgTable items={[
  ["ESP32", "/components/esp32/", "esp32.svg"],
  ["RP2040", "/components/rp2040/", "rp2040.svg"],
]} />

## Other

<ImgTable items={[
  ["Zigbee", "/components/zigbee/", "zigbee.svg"],
]} />
"""

# Against INDEX_BASE this adds one component in two tables (dedupe by URL),
# one single-table component, moves the ESP32 row and renames RP2040 to RP2.
INDEX_HEAD = """\
<ImgTable items={[
  ["New Thing", "/components/new_thing/", "new_thing.svg"],
  ["RP2", "/components/rp2/", "rp2040.svg"],
  ["ESP32", "/components/esp32/", "esp32.svg"],
]} />

## Other

<ImgTable items={[
  ["New Thing", "/components/new_thing/", "new_thing.svg"],
  ["UFM-01 Flow Meter", "/components/ufm01/", "ufm01.png", "Flow & Temperature"],
  ["Zigbee", "/components/zigbee/", "zigbee.svg"],
]} />
"""


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def docs_git(cutting):
    """Turn the docs project path into a git repo with a tagged base index."""
    repo = Path(cutting.EsphomeDocsProject.path)
    index = repo / Path(cutting.COMPONENTS_INDEX)
    index.parent.mkdir(parents=True, exist_ok=True)
    (repo / "src" / "content" / "docs" / "changelog").mkdir(
        parents=True, exist_ok=True
    )

    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    index.write_text(INDEX_BASE)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "tag", "2026.6.0")
    index.write_text(INDEX_HEAD)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "new components")
    return repo


def test_with_beta_notice_inserts_after_frontmatter(cutting):
    assert cutting._with_beta_notice(STABLE_PAGE) == BETA_PAGE


def test_with_beta_notice_is_idempotent(cutting):
    assert cutting._with_beta_notice(BETA_PAGE) == BETA_PAGE


def test_with_beta_notice_without_frontmatter(cutting):
    content = "# Changelog\n"
    assert cutting._with_beta_notice(content) == f"\n{NOTICE}\n# Changelog\n"


def test_without_beta_notice_removes_block(cutting):
    assert cutting._without_beta_notice(BETA_PAGE) == STABLE_PAGE


def test_without_beta_notice_removes_legacy_placement(cutting):
    assert cutting._without_beta_notice(LEGACY_BETA_PAGE) == STABLE_PAGE


def test_without_beta_notice_is_idempotent(cutting):
    assert cutting._without_beta_notice(STABLE_PAGE) == STABLE_PAGE


def _changelog_page(cutting, version_str: str, content: str) -> Path:
    changelog_dir = (
        Path(cutting.EsphomeDocsProject.path)
        / "src"
        / "content"
        / "docs"
        / "changelog"
    )
    changelog_dir.mkdir(parents=True, exist_ok=True)
    page = changelog_dir / f"{version_str}.mdx"
    page.write_text(content)
    return page


class FakeUser:
    def __init__(self, login: str):
        self.login = login
        self.html_url = f"https://github.com/{login}"


class FakePR:
    def __init__(self, number, title, labels=(), milestone=None):
        self.number = number
        self.title = title
        self.labels = [{"name": name} for name in labels]
        self.milestone = {"title": milestone} if milestone else None
        self.merged_at = number  # merge order follows PR numbers
        self.html_url = f"https://github.com/esphome/esphome/pull/{number}"
        self.user = FakeUser("someone")


class FakeProject:
    shortname = "esphome"

    def __init__(self, prs):
        self._prs = {pr.number: pr for pr in prs}

    def prs_between(self, base, head):
        return list(self._prs)

    def get_pr(self, number):
        return self._prs[number]


LINE_LABELS = (
    "new-feature",
    "new-component",
    "new-platform",
    "breaking-change",
    "notable-change",
)


def _line(pr: FakePR) -> str:
    """The changelog line ``format_change`` produces for a fake PR."""
    line = (
        f"- {pr.title} [esphome#{pr.number}]({pr.html_url})"
        f" by [@someone](https://github.com/someone)"
    )
    for label in pr.labels:
        if label["name"] in LINE_LABELS:
            line += f" ({label['name']})"
    return line


def _run_docs_insert_changelog(cutting, monkeypatch, version, base, prs):
    """Drive the real ``_docs_insert_changelog`` with the side effects stubbed.

    Only the interactive/remote edges are stubbed (git checkout, GitHub PR
    fetching, VS Code, confirmation prompt); the page manipulation all runs
    for real.
    """
    from esphomerelease.model import Version

    messages = []
    commits = []
    fake = FakeProject(prs)
    monkeypatch.setattr(
        cutting.EsphomeDocsProject, "workon", lambda branch: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        cutting.EsphomeDocsProject, "commit", lambda msg, **k: commits.append(msg)
    )
    monkeypatch.setattr(cutting.EsphomeProject, "prs_between", fake.prs_between)
    monkeypatch.setattr(cutting.EsphomeProject, "get_pr", fake.get_pr)
    monkeypatch.setattr(cutting, "open_vscode", lambda path: None)
    monkeypatch.setattr(cutting, "confirm", lambda msg: None)
    monkeypatch.setattr(cutting, "gprint", lambda msg, **k: messages.append(msg))

    cutting._docs_insert_changelog(version=version, base=Version.parse(base))
    return messages, commits


NEW_THING_ROW = '["New Thing", "/components/new_thing/", "new_thing.svg"],'
UFM01_ROW = '["UFM-01 Flow Meter", "/components/ufm01/", "ufm01.png", "Flow & Temperature"],'
RP2_ROW = '["RP2", "/components/rp2/", "rp2040.svg"],'

RENDERED_PAGE = f"""\
{FRONTMATTER}
import ImgTable from "@components/ImgTable.astro";

{{/* MANUAL: Add featured components here */}}
<ImgTable items={{[
  {NEW_THING_ROW}
  {RP2_ROW}
  {UFM01_ROW}
]}} />
"""


def test_new_component_table_lines(cutting, docs_git):
    """Added rows are collected once each; moved rows are skipped.

    The renamed RP2040 row is indistinguishable from a new component and is
    included — the MANUAL marker leaves the block to human curation.
    """
    from esphomerelease.model import Version

    rows = cutting._new_component_table_lines(Version.parse("2026.6.0"))
    assert rows == [NEW_THING_ROW, RP2_ROW, UFM01_ROW]


def test_new_component_table_lines_missing_base_tag(cutting, docs_git, capsys):
    """An undiffable base (e.g. missing tag) degrades to an empty draft."""
    from esphomerelease.model import Version

    rows = cutting._new_component_table_lines(Version.parse("1.0.0"))
    assert rows == []
    assert "please fill in the featured components manually" in capsys.readouterr().out


def test_render_changelog_page(cutting):
    from esphomerelease.model import Version

    page = cutting._render_changelog_page(
        Version.parse("2026.7.0"), [NEW_THING_ROW, RP2_ROW, UFM01_ROW]
    )
    assert page == RENDERED_PAGE


def test_render_changelog_page_empty_table(cutting):
    from esphomerelease.model import Version

    page = cutting._render_changelog_page(Version.parse("2026.8.0"), [])
    assert 'title: "ESPHome 2026.8.0 - August 2026"' in page
    assert "<ImgTable items={[\n]} />" in page


def test_ensure_changelog_page_creates_skeleton(cutting, docs_git):
    from esphomerelease.model import Version

    created = cutting._ensure_changelog_page(
        version=Version.parse("2026.7.0b1"), base=Version.parse("2026.6.0")
    )
    assert created is True
    page = docs_git / "src" / "content" / "docs" / "changelog" / "2026.7.0.mdx"
    assert page.read_text() == RENDERED_PAGE


def test_ensure_changelog_page_noop_when_present(cutting, docs_git):
    from esphomerelease.model import Version

    page = docs_git / "src" / "content" / "docs" / "changelog" / "2026.7.0.mdx"
    page.write_text(BETA_PAGE)
    created = cutting._ensure_changelog_page(
        version=Version.parse("2026.7.0b2"), base=Version.parse("2026.7.0b1")
    )
    assert created is False
    assert page.read_text() == BETA_PAGE


B1_PRS = [
    FakePR(1, "Add foo sensor", ["new-feature"]),
    FakePR(2, "Fix bar"),
    FakePR(3, "Bump dep from 1 to 2", ["dependencies"]),
]

FULL_BLOCK = f"""\
{{/* markdownlint-disable MD013 */}}

## Full list of changes

### New Features

{_line(B1_PRS[0])}

{{/* BETA_CHANGES_START */}}
{{/* BETA_CHANGES_END */}}

### All changes

<details>
<summary></summary>

{_line(B1_PRS[0])}
{_line(B1_PRS[1])}
{{/* ALL_CHANGES_END */}}

</details>

<details>
<summary></summary>

{_line(B1_PRS[2])}
{{/* DEPENDENCY_CHANGES_END */}}

</details>
"""


def _docs_change(cutting, pr: FakePR):
    labels = [label["name"] for label in pr.labels]
    return cutting._DocsChange(
        labels=labels, ref=f"[esphome#{pr.number}]", msg=_line(pr)
    )


def test_render_full_changes_block(cutting):
    changes = [_docs_change(cutting, pr) for pr in B1_PRS]
    assert cutting._render_full_changes_block(changes) == FULL_BLOCK


def test_merge_changes_beta(cutting):
    content = cutting._append_full_changes_block(
        STABLE_PAGE, [_docs_change(cutting, pr) for pr in B1_PRS]
    )
    beta_fix = FakePR(10, "Fix beta bug")
    beta_dep = FakePR(11, "Bump dep2 from 1 to 2", ["dependencies"])
    new = [_docs_change(cutting, pr) for pr in (beta_fix, beta_dep, B1_PRS[1])]

    merged = cutting._merge_changes(content, new, beta=True)
    assert (
        f"{{/* BETA_CHANGES_START */}}\n### Beta Changes\n\n"
        f"{_line(beta_fix)}\n{{/* BETA_CHANGES_END */}}"
    ) in merged
    assert f"{_line(B1_PRS[1])}\n{_line(beta_fix)}\n{{/* ALL_CHANGES_END */}}" in merged
    assert (
        f"{_line(B1_PRS[2])}\n{_line(beta_dep)}\n{{/* DEPENDENCY_CHANGES_END */}}"
    ) in merged
    # The already-present line was not added again.
    assert merged.count(_line(B1_PRS[1])) == 1

    # A later beta appends to the existing block without a second heading.
    later_fix = FakePR(12, "Another fix")
    merged2 = cutting._merge_changes(
        merged, [_docs_change(cutting, later_fix)], beta=True
    )
    assert merged2.count("### Beta Changes") == 1
    assert (
        f"{_line(beta_fix)}\n{_line(later_fix)}\n{{/* BETA_CHANGES_END */}}" in merged2
    )


def test_merge_changes_stable_skips_beta_block(cutting):
    content = cutting._append_full_changes_block(
        STABLE_PAGE, [_docs_change(cutting, pr) for pr in B1_PRS]
    )
    straggler = FakePR(20, "Straggler fix")
    merged = cutting._merge_changes(
        content, [_docs_change(cutting, straggler)], beta=False
    )
    assert "### Beta Changes" not in merged
    assert f"{_line(straggler)}\n{{/* ALL_CHANGES_END */}}" in merged


def test_merge_changes_nothing_new(cutting):
    changes = [_docs_change(cutting, pr) for pr in B1_PRS]
    content = cutting._append_full_changes_block(STABLE_PAGE, changes)
    assert cutting._merge_changes(content, changes, beta=True) == content


def test_merge_changes_missing_marker_raises(cutting):
    from esphomerelease.exceptions import EsphomeReleaseError

    with pytest.raises(EsphomeReleaseError, match="ALL_CHANGES_END"):
        cutting._merge_changes(
            STABLE_PAGE, [_docs_change(cutting, FakePR(1, "Fix"))], beta=False
        )


def test_remove_beta_changes_block(cutting):
    populated = (
        "x\n\n{/* BETA_CHANGES_START */}\n### Beta Changes\n\n- a\n"
        "{/* BETA_CHANGES_END */}\n\n### All changes\n"
    )
    empty = "x\n\n{/* BETA_CHANGES_START */}\n{/* BETA_CHANGES_END */}\n\n### All changes\n"
    assert cutting._remove_beta_changes_block(populated) == "x\n\n### All changes\n"
    assert cutting._remove_beta_changes_block(empty) == "x\n\n### All changes\n"
    # No block at all: no-op.
    assert cutting._remove_beta_changes_block(STABLE_PAGE) == STABLE_PAGE


def test_insert_patch_section(cutting):
    import datetime

    from esphomerelease.model import Version

    content = cutting._append_full_changes_block(
        STABLE_PAGE, [_docs_change(cutting, pr) for pr in B1_PRS]
    )
    patch_fix = FakePR(30, "Fix crash")
    result = cutting._insert_patch_section(
        content,
        version=Version.parse("2026.7.1"),
        changes=[_docs_change(cutting, patch_fix)],
    )
    now = datetime.datetime.now()
    assert (
        f"## Release 2026.7.1 - {now:%B} {now.day}\n\n"
        f"<details>\n<summary></summary>\n\n{_line(patch_fix)}\n\n</details>\n\n"
        "## Full list of changes"
    ) in result
    # Re-running the same patch cut leaves the page alone.
    assert (
        cutting._insert_patch_section(
            result,
            version=Version.parse("2026.7.1"),
            changes=[_docs_change(cutting, patch_fix)],
        )
        == result
    )


def test_insert_patch_section_missing_heading_raises(cutting):
    from esphomerelease.exceptions import EsphomeReleaseError
    from esphomerelease.model import Version

    with pytest.raises(EsphomeReleaseError, match="Full list of changes"):
        cutting._insert_patch_section(
            STABLE_PAGE,
            version=Version.parse("2026.7.1"),
            changes=[_docs_change(cutting, FakePR(30, "Fix crash"))],
        )


def test_docs_changes_formats_and_flags(cutting, monkeypatch):
    """The real collect/format pipeline runs against a fake GitHub project."""
    from esphomerelease.model import Version

    prs = [
        FakePR(1, "Add foo", ["new-feature"]),
        FakePR(3, "Bump dep from 1 to 2", ["dependencies"]),
        FakePR(4, "Oops", ["reverted"]),
    ]
    fake = FakeProject(prs)
    monkeypatch.setattr(cutting.EsphomeProject, "prs_between", fake.prs_between)
    monkeypatch.setattr(cutting.EsphomeProject, "get_pr", fake.get_pr)

    changes = cutting._docs_changes(
        version=Version.parse("2026.7.0b2"), base=Version.parse("2026.7.0b1")
    )
    assert [c.ref for c in changes] == ["[esphome#1]", "[esphome#3]"]
    assert changes[0].msg == _line(prs[0])
    assert not changes[0].is_dependency
    assert changes[1].is_dependency


def test_changelog_generate_with_sections(cutting):
    """The refactored generate() still renders the sectioned changelog."""
    from esphomerelease.model import Version

    prs = [
        FakePR(1, "Add foo", ["new-feature"]),
        FakePR(2, "Fix bar"),
        FakePR(3, "Bump dep from 1 to 2", ["dependencies"]),
        FakePR(4, "Reverted thing", ["reverted"]),
        FakePR(5, "Beta fix", ["cherry-picked"], milestone="2026.7.0b1"),
    ]
    out = cutting.changelog.generate(
        project=FakeProject(prs),
        base="2026.6.0",
        base_version=Version.parse("2026.6.0"),
        head="bump-2026.7.0b1",
        head_version=Version.parse("2026.7.0b1"),
        prerelease=True,
    )
    assert "## Full list of changes" in out
    assert f"### New Features\n\n{_line(prs[0])}" in out
    assert "### Beta Changes" in out
    assert _line(prs[4]) in out
    assert "Reverted thing" not in out
    # Dependency PRs are excluded from the main list, kept in the tail block.
    assert out.count(_line(prs[2])) == 1


def test_docs_insert_changelog_release_cycle(cutting, docs_git, monkeypatch):
    """Full cycle: the first beta writes the page, later cuts merge into it."""
    from esphomerelease.model import Version

    page = docs_git / "src" / "content" / "docs" / "changelog" / "2026.7.0.mdx"

    # First beta: page skeleton + full changes block + beta notice.
    messages, commits = _run_docs_insert_changelog(
        cutting, monkeypatch, Version.parse("2026.7.0b1"), "2026.6.0", B1_PRS
    )
    expected = (RENDERED_PAGE.rstrip("\n") + "\n\n" + FULL_BLOCK).replace(
        '\n\nimport ImgTable from "@components/ImgTable.astro";',
        f'\n\n{NOTICE}\n\nimport ImgTable from "@components/ImgTable.astro";',
    )
    assert page.read_text() == expected
    assert "Created changelog page 2026.7.0.mdx" in messages
    assert "Changelog written to 2026.7.0.mdx" in messages
    assert commits == ["Update changelog for 2026.7.0b1"]

    # Second beta: new lines merge into the beta/all/dependency blocks.
    beta_fix = FakePR(10, "Fix beta bug")
    beta_dep = FakePR(11, "Bump dep2 from 1 to 2", ["dependencies"])
    _, commits = _run_docs_insert_changelog(
        cutting,
        monkeypatch,
        Version.parse("2026.7.0b2"),
        "2026.7.0b1",
        [*B1_PRS, beta_fix, beta_dep],
    )
    content = page.read_text()
    assert (
        f"{{/* BETA_CHANGES_START */}}\n### Beta Changes\n\n"
        f"{_line(beta_fix)}\n{{/* BETA_CHANGES_END */}}"
    ) in content
    assert f"{_line(B1_PRS[1])}\n{_line(beta_fix)}\n{{/* ALL_CHANGES_END */}}" in content
    assert (
        f"{_line(B1_PRS[2])}\n{_line(beta_dep)}\n{{/* DEPENDENCY_CHANGES_END */}}"
    ) in content
    assert content.count(NOTICE) == 1
    assert content.count(_line(B1_PRS[1])) == 1
    assert commits == ["Update changelog for 2026.7.0b2"]

    # Stable: beta notice + Beta Changes block removed, stragglers merged in.
    straggler = FakePR(20, "Straggler fix")
    _, commits = _run_docs_insert_changelog(
        cutting,
        monkeypatch,
        Version.parse("2026.7.0"),
        "2026.6.0",
        [*B1_PRS, beta_fix, beta_dep, straggler],
    )
    content = page.read_text()
    assert NOTICE not in content
    assert "BETA_CHANGES" not in content
    assert "### Beta Changes" not in content
    assert f"{_line(beta_fix)}\n{_line(straggler)}\n{{/* ALL_CHANGES_END */}}" in content
    assert content.count(_line(beta_fix)) == 1
    assert commits == ["Update changelog for 2026.7.0"]

    # Patch: its own release section lands right before the full list.
    patch_fix = FakePR(30, "Fix crash")
    _, commits = _run_docs_insert_changelog(
        cutting, monkeypatch, Version.parse("2026.7.1"), "2026.7.0", [patch_fix]
    )
    content = page.read_text()
    assert "## Release 2026.7.1 - " in content
    assert (
        f"<details>\n<summary></summary>\n\n{_line(patch_fix)}\n\n</details>\n\n"
        "## Full list of changes"
    ) in content
    assert content.index("## Release 2026.7.1") < content.index(
        "## Full list of changes"
    )
    assert commits == ["Update changelog for 2026.7.1"]
