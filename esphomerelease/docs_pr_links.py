"""Pure logic for pairing ``esphome/esphome`` PRs with their ``esphome/esphome.io`` PRs.

Kept deliberately import-clean (stdlib ``re`` only) so it is unit-testable without
a configured working copy, GitHub session, or ``config.json``. Both consumers use
it: the ``check_docs_prs.py`` CLI helper (which talks to the ``gh`` CLI) and the
release-cut pre-flight in :mod:`esphomerelease.cutting` (which talks to github3).
"""

import re

# ``esphome/esphome#1234`` style references. The trailing ``#`` immediately after
# the repo slug means sibling repos like ``esphome/esphome.io#1234`` do NOT match
# (the char after ``esphome/esphome`` there is ``.``, not ``#``).
_SHORTHAND_RE = re.compile(r"esphome/esphome#(\d+)")

# Full ``https://github.com/esphome/esphome/pull/1234`` URLs. Restricted to
# ``/pull/`` so issue and discussion links of the same number are ignored.
_PULL_URL_RE = re.compile(r"github\.com/esphome/esphome/pull/(\d+)")

# Docs-repo shorthand: ``esphome/esphome.io#1234`` plus the pre-rename slug
# ``esphome/esphome-docs#1234``. ``esphome/esphome-docs`` was renamed to
# ``esphome/esphome.io`` with PR numbering preserved, so an old-slug reference
# (common on older code PRs) names the very same PR. The leading lookbehind
# anchors the owner so ``esphome/developers.esphome.io#1234`` - the separate
# developer-docs repo, which the esphome PR template has its own field for -
# cannot slip through.
_DOCS_SHORTHAND_RE = re.compile(r"(?<![\w.-])esphome/esphome(?:\.io|-docs)#(\d+)")

# Full docs-repo pull URLs under either slug. Restricted to ``/pull/`` (same
# rationale as _PULL_URL_RE) and anchored on ``github.com/esphome/`` so the
# developer-docs repo does not match here either.
_DOCS_PULL_URL_RE = re.compile(r"github\.com/esphome/esphome(?:\.io|-docs)/pull/(\d+)")

# ``[display text](url)`` markdown links. Replacing these with just the URL means
# link *display text* (e.g. a discussions link rendered as ``esphome/esphome#3624``)
# can't be mistaken for a real PR reference: only the destination URL counts.
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _extract(body: str, patterns: list[re.Pattern]) -> list[int]:
    """Collect the PR numbers matched by ``patterns`` in ``body``.

    Markdown links are collapsed to their destination URL first, so display text
    can never create a reference. Returns a sorted, de-duplicated list.
    """
    if not body:
        return []

    # Collapse markdown links to their destination URL before scanning.
    body = _MARKDOWN_LINK_RE.sub(r"\2", body)

    pr_numbers = set()
    for pattern in patterns:
        for match in pattern.finditer(body):
            pr_numbers.add(int(match.group(1)))

    return sorted(pr_numbers)


def extract_esphome_pr_numbers(body: str) -> list[int]:
    """Extract referenced ``esphome/esphome`` PR numbers from a docs PR body.

    Returns a sorted, de-duplicated list of PR numbers. Display text inside
    markdown links is discarded in favour of the link destination, so a PR
    number that only appears as link text (pointing at a discussion/issue URL)
    is not falsely reported.
    """
    return _extract(body, [_SHORTHAND_RE, _PULL_URL_RE])


def extract_docs_pr_numbers(body: str) -> list[int]:
    """Extract referenced ``esphome/esphome.io`` PR numbers from a code PR body.

    The counterpart of :func:`extract_esphome_pr_numbers`, scanning a PR body in
    the code repo for the docs PR(s) it says it pairs with. Both the current
    ``esphome/esphome.io`` slug and the pre-rename ``esphome/esphome-docs`` slug
    are accepted. The unfilled template placeholder
    ``- esphome/esphome.io#<esphome.io PR number goes here>`` is not numeric and
    so is never reported. Returns a sorted, de-duplicated list of PR numbers.
    """
    return _extract(body, [_DOCS_SHORTHAND_RE, _DOCS_PULL_URL_RE])


def is_confirmed_pair(
    *, docs_body: str, docs_number: int, code_body: str, code_number: int
) -> bool:
    """Whether a docs PR and a code PR genuinely reference each other.

    A PR body can mention the other repo's PR in passing prose ("replaces the
    note added with esphome/esphome#14255's docs") without that being the PR it
    ships alongside, so a one-way reference proves nothing. Only a back-link
    from both sides confirms the two are the pair that has to land together.
    """
    if code_number not in extract_esphome_pr_numbers(docs_body):
        return False
    return docs_number in extract_docs_pr_numbers(code_body)
