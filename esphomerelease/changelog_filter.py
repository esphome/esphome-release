"""Changelog PR-inclusion logic.

Deciding whether a merged PR belongs in a given release's changelog — and with
which labels — is fiddly: reverted PRs drop out, cherry-picked PRs are only
relevant when their milestone version falls inside the released range, and an
unparseable or missing milestone needs a sensible fallback. Historically all of
this lived inline inside ``changelog.generate`` as a closure that operated on
live ``github3`` PR objects, so it could never be unit-tested.

This module isolates the pure decision. It imports only ``.model`` (stdlib +
typing underneath), so it stays importable and testable without a configured
working copy — mirroring ``milestone.py``.
"""

from typing import List, Optional

from .model import Version


def resolve_changelog_labels(
    labels: List[str],
    milestone_title: Optional[str],
    base_version: Version,
    head_version: Version,
) -> Optional[List[str]]:
    """Decide whether a PR belongs in the changelog and with which labels.

    Returns the effective list of labels to render the PR with, or ``None`` if
    the PR should be excluded from this release's changelog entirely.

    Rules (faithful to the historical inline logic in ``generate``):

    - ``reverted`` PRs are always excluded.
    - ``cherry-picked`` PRs are included only if their milestone version falls in
      the half-open range ``(base_version, head_version]``. A cherry-pick
      milestoned at or before the base, or after the head, was not part of this
      release and is excluded.
    - If the milestone title can't be parsed as a version, the PR is still
      included but the ``cherry-picked`` label is dropped — we can't place it in
      a beta-changes section, so we treat it as a normal change.
    - If a cherry-picked PR has no milestone at all (``milestone_title`` is
      ``None``), it is included unchanged: we can't range-check it, so we don't
      second-guess the label.

    The input list is never mutated; a copy is returned.
    """
    labels = list(labels)

    if "reverted" in labels:
        return None

    if "cherry-picked" in labels:
        if milestone_title is None:
            return labels
        try:
            pick_version = Version.parse(milestone_title)
        except ValueError:
            labels.remove("cherry-picked")
            return labels
        if not (base_version < pick_version <= head_version):
            # Picked into a different release — not part of this one.
            return None

    return labels
