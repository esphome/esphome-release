"""Shared fixtures for tests that exercise :mod:`esphomerelease.cutting`.

``cutting`` imports ``.project``, which instantiates every ``Project`` at
import time and asserts each configured path is a directory. The ``cutting``
fixture writes a temp ``config.json`` whose paths point at real directories so
the module is importable, mirroring the import-safe reload pattern used
elsewhere in this repo.
"""

import importlib
import json
import subprocess
from pathlib import Path

import pytest


# Trimmed stand-in for the docs repo's script/blog_post_template.mdx: the
# real file carries more frontmatter and marker sections, but the cutter only
# fills {TOKEN}s and inserts featured rows after the ImgTable opening line.
BLOG_TEMPLATE = """\
---
title: "ESPHome {VERSION}: {TAGLINE}"
description: "{DESCRIPTION}"
date: {DATE}
excerpt: "{DESCRIPTION}"
cover:
  image: "https://assets.openhomefoundation.org/opengraph?url=https://esphome.io/{BLOG_PATH}"
---

import ImgTable from "@components/ImgTable.astro";

{/* MANUAL: Add featured components here */}
<ImgTable items={[
  // ["Component Name", "/components/path/", "image.png"],
]} />

## Release Overview

{/* RELEASE_OVERVIEW_START */}
{/* RELEASE_OVERVIEW_END */}

## Full List of Changes

For the complete list of every merged pull request in this release, see the
[full {VERSION} changelog](/changelog/{VERSION}/).
"""


@pytest.fixture
def cutting(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "script").mkdir()
    (repo_dir / "script" / "blog_post_template.mdx").write_text(BLOG_TEMPLATE)
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
