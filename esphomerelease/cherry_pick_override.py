"""Pure logic for pre-resolved cherry-pick overrides declared in a PR body.

When a PR does not cherry-pick cleanly onto a release branch, a contributor can
prepare a commit with the conflicts already resolved and point the cutter at it
with a fenced code block in the PR body, one block per target branch::

    ```release-cherry-pick
    branch: release
    commit: 4f53d5c8471da8081ef2077af36263785672def3
    ```

``branch`` is the release branch the commit applies to and ``commit`` is the full
40-character SHA of a single non-merge commit, based on that branch, that contains
the whole PR with conflicts resolved.

Trust model: anyone who can edit a PR body can write such a block, and GitHub
serves fork-network commits by SHA from the parent repository, so the SHA is
untrusted input. This module only parses; :mod:`esphomerelease.project` verifies
every override against the local repository and requires a manual, default-No
confirmation before one is used instead of the PR's merge commit.

Kept deliberately import-clean (stdlib only) so it is unit-testable without a
configured working copy, GitHub session, or ``config.json``.
"""

import re
from dataclasses import dataclass, field

INFO_STRING = "release-cherry-pick"

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_KEYS = ("branch", "commit")

_CHERRY_PICK_RE = re.compile(r"cherry[- ]?pick", re.IGNORECASE)
_COMMIT_URL_RE = re.compile(r"github\.com/esphome/esphome/commit/[0-9a-fA-F]{7,40}")
_BARE_SHA_RE = re.compile(r"(?<![0-9a-zA-Z])[0-9a-fA-F]{40}(?![0-9a-zA-Z])")


@dataclass(frozen=True)
class CherryPickOverride:
    """A commit to cherry-pick onto ``branch`` instead of the PR's merge commit."""

    branch: str
    commit: str


@dataclass
class OverrideParseResult:
    """Overrides found in a PR body plus human-readable problems with them."""

    overrides: list[CherryPickOverride] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _split_blocks(body: str | None) -> tuple[list[str], str]:
    """Split ``body`` into ``release-cherry-pick`` block contents and the rest.

    Other fenced blocks are left in the remainder untouched (their contents are
    never treated as an override). CRLF is normalised first. An unterminated
    fence runs to the end of the body, as in CommonMark.
    """
    if not body:
        return [], ""

    blocks: list[str] = []
    rest: list[str] = []
    # (fence char, fence length, collected lines) of the block being read.
    current: tuple[str, int, list[str]] | None = None
    # Whether the open fence is a release-cherry-pick one.
    structured = False

    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _FENCE_RE.match(line)
        if current is None:
            if match is None:
                rest.append(line)
                continue
            fence, info = match.group(1), match.group(2).strip()
            if fence[0] == "`" and "`" in info:
                # Not a fence opener: an inline code span at line start.
                rest.append(line)
                continue
            structured = info == INFO_STRING
            current = (fence[0], len(fence), [])
            if not structured:
                rest.append(line)
            continue

        char, length, lines = current
        closes = match is not None and match.group(1)[0] == char and len(match.group(1)) >= length
        closes = closes and not match.group(2).strip()
        if closes:
            if structured:
                blocks.append("\n".join(lines))
            else:
                rest.append(line)
            current = None
        elif structured:
            lines.append(line)
        else:
            rest.append(line)

    if current is not None and structured:
        blocks.append("\n".join(current[2]))

    return blocks, "\n".join(rest)


def _parse_block(content: str, index: int) -> tuple[CherryPickOverride | None, list[str]]:
    """Parse one block's ``key: value`` lines; ``index`` is its 1-based position."""
    where = f"release-cherry-pick block {index}"
    errors: list[str] = []
    values: dict[str, str] = {}

    for raw in content.split("\n"):
        line = raw.strip()
        if not line:
            continue
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if not sep or key not in _KEYS:
            errors.append(f"{where}: unrecognised line {line!r} (expected 'branch: ...' or 'commit: ...')")
        elif key in values:
            errors.append(f"{where}: duplicate key {key!r}")
        else:
            values[key] = value.strip()

    for key in _KEYS:
        if key not in values:
            errors.append(f"{where}: missing key {key!r}")
        elif not values[key]:
            errors.append(f"{where}: empty value for {key!r}")

    commit = values.get("commit")
    if commit and not _SHA_RE.match(commit):
        errors.append(f"{where}: commit {commit!r} is not a full 40-character hex SHA")

    if errors:
        return None, errors
    return CherryPickOverride(branch=values["branch"], commit=values["commit"].lower()), []


def parse_overrides(body: str | None) -> OverrideParseResult:
    """Extract every ``release-cherry-pick`` block from a PR body."""
    result = OverrideParseResult()
    seen: set[str] = set()

    for index, content in enumerate(_split_blocks(body)[0], start=1):
        override, errors = _parse_block(content, index)
        result.errors.extend(errors)
        if override is None:
            continue
        if override.branch in seen:
            result.errors.append(f"more than one release-cherry-pick block for branch {override.branch!r}")
            continue
        seen.add(override.branch)
        result.overrides.append(override)

    return result


def override_for_branch(result: OverrideParseResult, branch: str) -> CherryPickOverride | None:
    """Return the override targeting ``branch``, if any."""
    for override in result.overrides:
        if override.branch == branch:
            return override
    return None


def mentions_unstructured_override(body: str | None) -> bool:
    """True if the body discusses a replacement commit in prose.

    That is: it mentions cherry-picking AND names a commit (an esphome commit
    URL or a bare 40-hex SHA), outside any ``release-cherry-pick`` block. Such a
    PR should use the structured block instead; the cutter flags it so the
    release manager reads the body.
    """
    text = _split_blocks(body)[1]
    if not _CHERRY_PICK_RE.search(text):
        return False
    return bool(_COMMIT_URL_RE.search(text) or _BARE_SHA_RE.search(text))
