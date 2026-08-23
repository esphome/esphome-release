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
    - For a patch release (``head_version`` has a non-zero patch and is neither a
      beta nor a dev prerelease) only PRs milestoned exactly at ``head_version``
      are included. A patch ships precisely what its milestone says it ships, so
      anything else in the released range - most visibly the docs PRs that land
      straight on the docs ``current`` branch between releases and so sit in the
      range without ever having been part of the patch - is excluded. An empty
      changelog is a valid outcome.
    - ``cherry-picked`` PRs are included only if their milestone version falls in
      the half-open range ``(base_version, upper_bound]``, where ``upper_bound``
      is ``head_version`` for a final release, or that release cycle's final
      version (``head_version`` with ``beta`` and ``dev`` cleared) when
      ``head_version`` is itself a beta or dev prerelease. This matters because
      cherry-picked PRs are milestoned with the cycle's final version (e.g.
      ``2026.8.0``), not a per-beta milestone - without normalising the upper
      bound, every cherry-pick would sort above a beta head and be excluded. A
      cherry-pick milestoned at or before the base, or after the upper bound,
      was not part of this release and is excluded.
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

    if head_version.patch != 0 and not head_version.beta and not head_version.dev:
        # A patch release contains exactly the PRs on its own milestone.
        if milestone_title is None:
            return None
        try:
            pr_version = Version.parse(milestone_title)
        except ValueError:
            return None
        if pr_version != head_version:
            return None
        return labels

    if "cherry-picked" in labels:
        if milestone_title is None:
            return labels
        try:
            pick_version = Version.parse(milestone_title)
        except ValueError:
            labels.remove("cherry-picked")
            return labels
        upper_bound = head_version
        if head_version.beta or head_version.dev:
            upper_bound = head_version.replace(beta=0, dev=False)
        if not (base_version < pick_version <= upper_bound):
            # Picked into a different release — not part of this one.
            return None

    return labels
