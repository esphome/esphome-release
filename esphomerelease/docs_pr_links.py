"""Pure logic for finding linked ``esphome/esphome`` PR references in docs PR bodies.

Kept deliberately import-clean (stdlib ``re`` only) so it is unit-testable without
a configured working copy, GitHub session, or ``config.json``. The CLI helper
``check_docs_prs.py`` consumes :func:`extract_esphome_pr_numbers` to flag docs PRs
whose linked code PR has already merged.
"""

import re

# ``esphome/esphome#1234`` style references. The trailing ``#`` immediately after
# the repo slug means sibling repos like ``esphome/esphome.io#1234`` do NOT match
# (the char after ``esphome/esphome`` there is ``.``, not ``#``).
_SHORTHAND_RE = re.compile(r"esphome/esphome#(\d+)")

# Full ``https://github.com/esphome/esphome/pull/1234`` URLs. Restricted to
# ``/pull/`` so issue and discussion links of the same number are ignored.
_PULL_URL_RE = re.compile(r"github\.com/esphome/esphome/pull/(\d+)")

# ``[display text](url)`` markdown links. Replacing these with just the URL means
# link *display text* (e.g. a discussions link rendered as ``esphome/esphome#3624``)
# can't be mistaken for a real PR reference — only the destination URL counts.
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def extract_esphome_pr_numbers(body: str) -> list[int]:
    """Extract referenced ``esphome/esphome`` PR numbers from a docs PR body.

    Returns a sorted, de-duplicated list of PR numbers. Display text inside
    markdown links is discarded in favour of the link destination, so a PR
    number that only appears as link text (pointing at a discussion/issue URL)
    is not falsely reported.
    """
    if not body:
        return []

    # Collapse markdown links to their destination URL before scanning.
    body = _MARKDOWN_LINK_RE.sub(r"\2", body)

    pr_numbers = set()
    for match in _SHORTHAND_RE.finditer(body):
        pr_numbers.add(int(match.group(1)))
    for match in _PULL_URL_RE.finditer(body):
        pr_numbers.add(int(match.group(1)))

    return sorted(pr_numbers)
