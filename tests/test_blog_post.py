"""Tests for the release notes blog post management on esphome.io.

The first beta creates the cycle's blog post skeleton (dated the release
Wednesday after user confirmation) next to the changelog page; later betas
keep the beta notice on it, the stable release removes the notice, and cuts
of pre-blog cycles (no post in the tree) leave everything alone. After
touching the post the docs repo's ``script/bump-version.py`` is re-run so it
derives ``blog_url`` in ``data/version.json`` from the post's actual path.

The ``cutting`` and ``docs_git`` fixtures live in ``conftest.py``.
"""

import contextlib
import datetime
from pathlib import Path

import pytest

NOTICE = (
    "> [!NOTE]\n"
    "> This is a beta release. Details on this page may change before the stable release is published."
)

ROW = '["New Thing", "/components/new_thing/", "new_thing.svg"],'

EXAMPLE_ROW = '// ["Component Name", "/components/path/", "image.png"],'

OG = (
    "https://assets.openhomefoundation.org/opengraph"
    "?url=https://esphome.io/blog/2026/07/15/esphome-2026-7"
)

# conftest.BLOG_TEMPLATE filled for 2026.7.0b1, dated 2026-07-15, one drafted
# row. {TAGLINE} and {DESCRIPTION} stay literal for manual fill, matching the
# docs repo's generate_release_notes.py convention.
EXPECTED_POST = f"""\
---
title: "ESPHome 2026.7.0: {{TAGLINE}}"
description: "{{DESCRIPTION}}"
date: 2026-07-15
excerpt: "{{DESCRIPTION}}"
cover:
  image: "{OG}"
---

import ImgTable from "@components/ImgTable.astro";

{{/* MANUAL: Add featured components here */}}
<ImgTable items={{[
  {ROW}
  {EXAMPLE_ROW}
]}} />

## Release Overview

{{/* RELEASE_OVERVIEW_START */}}
{{/* RELEASE_OVERVIEW_END */}}

## Full List of Changes

For the complete list of every merged pull request in this release, see the
[full 2026.7.0 changelog](/changelog/2026.7.0/).
"""


def _post_path(cutting) -> Path:
    return (
        Path(cutting.EsphomeDocsProject.path)
        / "src" / "content" / "docs" / "blog"
        / "2026" / "07" / "15" / "esphome-2026-7.mdx"
    )


def test_blog_slug(cutting):
    from esphomerelease.model import Version

    assert cutting._blog_slug(Version.parse("2026.7.0b1")) == "esphome-2026-7"
    assert cutting._blog_slug(Version.parse("2026.7.2")) == "esphome-2026-7"


def test_find_blog_post_and_url(cutting):
    from esphomerelease.model import Version

    version = Version.parse("2026.7.0b1")
    assert cutting._find_blog_post(version) is None

    path = _post_path(cutting)
    path.parent.mkdir(parents=True)
    path.write_text("x")
    assert cutting._find_blog_post(version) == path
    assert cutting._blog_post_url(path) == "/blog/2026/07/15/esphome-2026-7/"

    # A duplicate slug under a later date wins, as in generate_release_notes.py.
    newer = path.parent.parent.parent / "08" / "19" / path.name
    newer.parent.mkdir(parents=True)
    newer.write_text("x")
    assert cutting._find_blog_post(version) == newer


def test_prompt_blog_date_defaults_to_release_wednesday(cutting, monkeypatch):
    from esphomerelease.model import Version

    seen = {}

    def fake_prompt(text, default=None):
        seen["default"] = default
        return default

    monkeypatch.setattr(cutting.click, "prompt", fake_prompt)
    date = cutting._prompt_blog_date(Version.parse("2026.7.0b1"))
    assert seen["default"] == "2026-07-15"
    assert date == datetime.date(2026, 7, 15)


def test_prompt_blog_date_rejects_invalid_input(cutting, monkeypatch):
    from esphomerelease.model import Version

    answers = iter(["not-a-date", "2026-07-22"])
    messages = []
    monkeypatch.setattr(cutting.click, "prompt", lambda *a, **k: next(answers))
    monkeypatch.setattr(cutting, "gprint", lambda msg, **k: messages.append(msg))
    date = cutting._prompt_blog_date(Version.parse("2026.7.0b1"))
    assert date == datetime.date(2026, 7, 22)
    assert messages == ["Invalid date 'not-a-date', expected YYYY-MM-DD"]


def test_render_blog_post(cutting):
    from esphomerelease.model import Version

    post = cutting._render_blog_post(
        Version.parse("2026.7.0b1"),
        date=datetime.date(2026, 7, 15),
        featured=[ROW],
    )
    assert post == EXPECTED_POST


def test_render_blog_post_no_featured_rows(cutting):
    """Without drafted rows the template's example comment is left alone."""
    from esphomerelease.model import Version

    post = cutting._render_blog_post(
        Version.parse("2026.7.0b1"),
        date=datetime.date(2026, 7, 15),
        featured=[],
    )
    assert ROW not in post
    assert EXAMPLE_ROW in post


def test_render_blog_post_missing_template(cutting):
    from esphomerelease.exceptions import EsphomeReleaseError
    from esphomerelease.model import Version

    (Path(cutting.EsphomeDocsProject.path) / "script" / "blog_post_template.mdx").unlink()
    with pytest.raises(EsphomeReleaseError, match="blog_post_template"):
        cutting._render_blog_post(
            Version.parse("2026.7.0b1"),
            date=datetime.date(2026, 7, 15),
            featured=[],
        )


def test_render_blog_post_template_without_table(cutting, monkeypatch):
    """A template missing the ImgTable can't take drafted rows: warn instead."""
    from esphomerelease.model import Version

    template = Path(cutting.EsphomeDocsProject.path) / "script" / "blog_post_template.mdx"
    template.write_text("---\ndate: {DATE}\n---\n")
    messages = []
    monkeypatch.setattr(cutting, "gprint", lambda msg, **k: messages.append(msg))
    post = cutting._render_blog_post(
        Version.parse("2026.7.0b1"),
        date=datetime.date(2026, 7, 15),
        featured=[ROW],
    )
    assert post == "---\ndate: 2026-07-15\n---\n"
    assert any("add the drafted rows manually" in msg for msg in messages)


def _run_update_blog_post(
    cutting, monkeypatch, version_str, base_str, *, prompts=(), prs=None
):
    """Drive the real ``_docs_update_blog_post`` with the side effects stubbed.

    Only the interactive/remote edges are stubbed (git checkout, VS Code, the
    prompts, non-git subprocesses, GitHub PR fetching when ``prs`` is given);
    ``git`` commands run for real so the featured-components diff is
    exercised when the ``docs_git`` fixture is in play. Non-git commands
    (``script/bump-version.py``) are recorded.

    Reviewing and committing the post is ``_docs_insert_changelog``'s job (it
    shows the post next to the changelog page), so ``commits`` and ``opened``
    stay empty here - see ``test_beta_notice.py`` for that half.
    """
    from esphomerelease.model import Version

    commits = []
    commands = []
    messages = []
    opened = []
    answers = iter(prompts)
    real_run_command = cutting.EsphomeDocsProject.run_command

    def fake_run_command(*args, **kwargs):
        commands.append(args)
        if args[0] == "git":
            return real_run_command(*args, **kwargs)
        return b""

    monkeypatch.setattr(
        cutting.EsphomeDocsProject, "workon", lambda branch: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        cutting.EsphomeDocsProject, "commit", lambda msg, **k: commits.append(msg)
    )
    monkeypatch.setattr(cutting.EsphomeDocsProject, "run_command", fake_run_command)
    monkeypatch.setattr(cutting, "open_vscode", opened.append)
    monkeypatch.setattr(cutting, "confirm", lambda msg: None)
    monkeypatch.setattr(cutting, "gprint", lambda msg, **k: messages.append(msg))
    monkeypatch.setattr(cutting.click, "prompt", lambda *a, **k: next(answers))
    if prs is not None:
        from test_beta_notice import FakeProject

        fake = FakeProject(prs)
        monkeypatch.setattr(cutting.EsphomeProject, "prs_between", fake.prs_between)
        monkeypatch.setattr(cutting.EsphomeProject, "get_pr", fake.get_pr)

    url, review = cutting._docs_update_blog_post(
        version=Version.parse(version_str), base=Version.parse(base_str)
    )
    return url, review, commits, commands, messages, opened


def test_update_blog_post_first_beta_creates_post(cutting, docs_git, monkeypatch):
    url, review, commits, commands, messages, opened = _run_update_blog_post(
        cutting, monkeypatch, "2026.7.0b1", "2026.6.0", prompts=["2026-07-15"]
    )
    post = _post_path(cutting)
    content = post.read_text()
    assert url == "/blog/2026/07/15/esphome-2026-7/"
    assert content.count(NOTICE) == 1
    # The featured components drafted from the index diff made it in.
    assert ROW in content
    assert '["UFM-01 Flow Meter", "/components/ufm01/"' in content
    assert ("script/bump-version.py", "2026.7.0b1") in commands
    # Handed back for review, not shown or committed here.
    assert review == post
    assert opened == []
    assert commits == []
    assert "Created release notes blog post esphome-2026-7.mdx" in messages
    # The skeleton is handed straight to the docs repo's release notes
    # generator, before the post is opened for review.
    assert (cutting.RELEASE_NOTES_SCRIPT, "2026.7.0") in commands
    assert (
        cutting.RELEASE_NOTES_SCRIPT,
        "2026.7.0",
        "--assemble",
        "--blog-only",
    ) in commands
    # Nothing answered the prompts here, so the placeholders are still called out.
    assert "Fill in the {TAGLINE} and {DESCRIPTION} placeholders manually" in messages


def test_update_blog_post_later_beta_keeps_notice(cutting, monkeypatch):
    post = _post_path(cutting)
    post.parent.mkdir(parents=True)
    post.write_text(EXPECTED_POST)

    url, review, commits, commands, _, opened = _run_update_blog_post(
        cutting, monkeypatch, "2026.7.0b2", "2026.7.0b1"
    )
    assert url == "/blog/2026/07/15/esphome-2026-7/"
    assert post.read_text().count(NOTICE) == 1
    assert ("script/bump-version.py", "2026.7.0b2") in commands
    # Only the beta notice changed, so there is nothing new to read.
    assert review is None
    assert opened == []
    assert commits == []


def test_update_blog_post_stable_removes_notice(cutting, monkeypatch):
    post = _post_path(cutting)
    post.parent.mkdir(parents=True)
    post.write_text(cutting._with_beta_notice(EXPECTED_POST))

    url, review, commits, commands, _, _ = _run_update_blog_post(
        cutting, monkeypatch, "2026.7.0", "2026.6.0"
    )
    assert url == "/blog/2026/07/15/esphome-2026-7/"
    assert post.read_text() == EXPECTED_POST
    assert ("script/bump-version.py", "2026.7.0") in commands
    assert review is None
    assert commits == []


def test_update_blog_post_patch_appends_section(cutting, monkeypatch):
    """Patch release content goes onto the cycle's blog post."""
    from test_beta_notice import FakePR, _line

    post = _post_path(cutting)
    post.parent.mkdir(parents=True)
    post.write_text(EXPECTED_POST)

    # Patch PRs are cherry-picked from the patch milestone, so they carry it.
    fix = FakePR(30, "Fix crash", milestone="2026.7.1")
    url, review, commits, commands, _, opened = _run_update_blog_post(
        cutting, monkeypatch, "2026.7.1", "2026.7.0", prs=[fix]
    )
    content = post.read_text()
    now = datetime.datetime.now()
    assert (
        f"{cutting.MD013_DISABLE}\n\n"
        f"## Release 2026.7.1 - {now:%B} {now.day}\n\n"
        f"{_line(fix)}\n\n"
        f"{cutting.MD013_ENABLE}\n\n"
        "## Full List of Changes"
    ) in content
    assert url == "/blog/2026/07/15/esphome-2026-7/"
    assert ("script/bump-version.py", "2026.7.1") in commands
    # The inserted patch section is new text, so it is handed back for review.
    assert review == post
    assert opened == []
    assert commits == []

    # The next patch appends inside the existing region, after the first.
    fix2 = FakePR(31, "Fix other crash")
    _run_update_blog_post(cutting, monkeypatch, "2026.7.2", "2026.7.1", prs=[fix2])
    content = post.read_text()
    assert content.count(cutting.MD013_DISABLE) == 1
    assert (
        content.index("## Release 2026.7.1")
        < content.index("## Release 2026.7.2")
        < content.index("## Full List of Changes")
    )

    # Re-running a patch cut is idempotent and asks for no review.
    before = post.read_text()
    _, review, _, _, _, _ = _run_update_blog_post(
        cutting, monkeypatch, "2026.7.2", "2026.7.1", prs=[fix2]
    )
    assert post.read_text() == before
    assert review is None


@pytest.mark.parametrize("version_str", ["2026.7.1", "2026.7.0b2", "2026.7.0"])
def test_update_blog_post_missing_post_skips(cutting, monkeypatch, version_str):
    """Cuts of pre-blog cycles leave the tree and version.json alone."""
    url, review, commits, commands, messages, _ = _run_update_blog_post(
        cutting, monkeypatch, version_str, "2026.6.0"
    )
    assert url is None
    assert review is None
    assert commits == []
    assert commands == []
    assert messages == [f"No release notes blog post for {version_str}, skipping"]


def _prompts_dir(cutting) -> Path:
    return (
        Path(cutting.EsphomeDocsProject.path)
        / "script" / "cache" / "2026.7.0" / "prompts"
    )


def _write_prompts(cutting, names) -> None:
    prompts = _prompts_dir(cutting)
    prompts.mkdir(parents=True, exist_ok=True)
    for name in names:
        (prompts / name).write_text("follow these instructions")


def _fake_docs_commands(cutting, monkeypatch, *, fail_on=None):
    """Record the docs-repo commands, failing the one ``fail_on`` matches."""
    from esphomerelease.exceptions import EsphomeReleaseError

    commands = []
    kwargs_seen = []
    messages = []

    def fake_run_command(*args, **kwargs):
        commands.append(args)
        kwargs_seen.append(kwargs)
        if fail_on is not None and fail_on(args):
            raise EsphomeReleaseError("Failed running command!")
        return b""

    monkeypatch.setattr(cutting.EsphomeDocsProject, "run_command", fake_run_command)
    monkeypatch.setattr(cutting, "gprint", lambda msg, **k: messages.append(msg))
    return commands, kwargs_seen, messages


def _claude_call(cutting, name: str) -> tuple:
    prompt = Path("script") / "cache" / "2026.7.0" / "prompts" / name
    return (
        cutting.CLAUDE_CLI,
        "--permission-mode",
        "acceptEdits",
        "-p",
        f"Read {prompt} and follow the instructions in it.",
    )


def test_generate_release_notes_runs_every_step(cutting, monkeypatch):
    """Prompts, then the AI pass for each, then the blog-only assembly."""
    from esphomerelease.model import Version

    _write_prompts(cutting, cutting.RELEASE_NOTES_PROMPTS)
    commands, kwargs_seen, _ = _fake_docs_commands(cutting, monkeypatch)

    cutting._generate_release_notes(Version.parse("2026.7.0b1"))

    assert commands == [
        (cutting.RELEASE_NOTES_SCRIPT, "2026.7.0"),
        *[_claude_call(cutting, name) for name in cutting.RELEASE_NOTES_PROMPTS],
        (cutting.RELEASE_NOTES_SCRIPT, "2026.7.0", "--assemble", "--blog-only"),
    ]
    # Output is streamed, and a failure is reported rather than prompting for
    # an interactive retry of a command this cut can do without.
    assert all(kw == {"live": True, "fail_ok": True} for kw in kwargs_seen)


def test_generate_release_notes_skips_prompts_that_were_not_written(
    cutting, monkeypatch
):
    """A cycle without breaking changes gets no such prompt: skip, don't fail."""
    from esphomerelease.model import Version

    _write_prompts(cutting, ["contributors.txt"])
    commands, _, messages = _fake_docs_commands(cutting, monkeypatch)

    cutting._generate_release_notes(Version.parse("2026.7.0b1"))

    assert commands == [
        (cutting.RELEASE_NOTES_SCRIPT, "2026.7.0"),
        _claude_call(cutting, "contributors.txt"),
        (cutting.RELEASE_NOTES_SCRIPT, "2026.7.0", "--assemble", "--blog-only"),
    ]
    assert "No overview_and_highlights.txt prompt was generated, skipping it" in messages
    assert "No breaking_changes.txt prompt was generated, skipping it" in messages


def test_generate_release_notes_stops_when_the_prompts_fail(cutting, monkeypatch):
    """Without prompts there is nothing to answer or assemble."""
    from esphomerelease.model import Version

    _write_prompts(cutting, cutting.RELEASE_NOTES_PROMPTS)
    commands, _, messages = _fake_docs_commands(
        cutting, monkeypatch, fail_on=lambda args: args[0] == cutting.RELEASE_NOTES_SCRIPT
    )

    cutting._generate_release_notes(Version.parse("2026.7.0b1"))

    assert commands == [(cutting.RELEASE_NOTES_SCRIPT, "2026.7.0")]
    assert any(
        msg.startswith(f"Running {cutting.RELEASE_NOTES_SCRIPT} failed")
        for msg in messages
    )


def test_generate_release_notes_stops_when_the_ai_pass_fails(cutting, monkeypatch):
    """A half-answered set of prompts must not be assembled into the post."""
    from esphomerelease.model import Version

    _write_prompts(cutting, cutting.RELEASE_NOTES_PROMPTS)
    commands, _, messages = _fake_docs_commands(
        cutting, monkeypatch, fail_on=lambda args: args[0] == cutting.CLAUDE_CLI
    )

    cutting._generate_release_notes(Version.parse("2026.7.0b1"))

    assert commands == [
        (cutting.RELEASE_NOTES_SCRIPT, "2026.7.0"),
        _claude_call(cutting, cutting.RELEASE_NOTES_PROMPTS[0]),
    ]
    assert any(
        msg.startswith(f"Running {cutting.CLAUDE_CLI} failed") for msg in messages
    )


def test_update_blog_post_filled_post_does_not_warn(cutting, monkeypatch):
    """Once the generator has filled the post, the manual hint stays quiet."""
    post = _post_path(cutting)
    post.parent.mkdir(parents=True)
    post.write_text(
        EXPECTED_POST.replace("{TAGLINE}", "Faster Everything").replace(
            "{DESCRIPTION}", "A release about speed."
        )
    )

    _, _, _, _, messages, _ = _run_update_blog_post(
        cutting, monkeypatch, "2026.7.0b2", "2026.7.0b1"
    )
    assert not any("placeholders manually" in msg for msg in messages)


def test_generate_release_notes_survives_a_missing_cli(cutting, monkeypatch):
    """The AI CLI not being installed is a skipped step, not a failed cut."""
    from esphomerelease.model import Version

    _write_prompts(cutting, cutting.RELEASE_NOTES_PROMPTS)
    commands, _, messages = _fake_docs_commands(cutting, monkeypatch)
    real_fake = cutting.EsphomeDocsProject.run_command

    def fake_run_command(*args, **kwargs):
        real_fake(*args, **kwargs)
        if args[0] == cutting.CLAUDE_CLI:
            raise FileNotFoundError(2, "No such file or directory", cutting.CLAUDE_CLI)
        return b""

    monkeypatch.setattr(cutting.EsphomeDocsProject, "run_command", fake_run_command)

    cutting._generate_release_notes(Version.parse("2026.7.0b1"))

    assert commands == [
        (cutting.RELEASE_NOTES_SCRIPT, "2026.7.0"),
        _claude_call(cutting, cutting.RELEASE_NOTES_PROMPTS[0]),
    ]
    assert any(
        msg.startswith(f"Running {cutting.CLAUDE_CLI} failed") for msg in messages
    )
