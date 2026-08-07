"""Milestone completeness checks.

When a release is cut, every PR attached to that version's milestone is expected
to end up in the released code. Historically that was not guaranteed: a PR could
be merged and milestoned, yet its commit never make it into the release branch
(e.g. a cherry-pick that was skipped, or commits left behind in a beta). This
module provides the pure set logic to detect that gap.

Kept deliberately free of any ``config`` / GitHub imports so it stays importable
(and unit-testable) without a configured working copy.
"""

from typing import Iterable, List


def milestone_title_candidates(version) -> List[str]:
    """Ordered milestone titles to try for a release, most-specific first.

    Two milestone models exist in the wild:

    * per-version — each release has its own milestone (title == ``str(version)``);
    * per-cycle — a single milestone shared by every beta plus the final release,
      titled with the beta/dev components stripped (e.g. ``2026.6.0`` for
      ``2026.6.0b3``); patch releases keep their own title.

    Returns the per-cycle title followed by the per-version title, de-duplicated,
    so a lookup can resolve a milestone under either model. ``version`` only needs
    a ``replace(beta=, dev=)`` method and a ``str()`` — no GitHub/config import.
    """
    cycle_title = str(version.replace(beta=0, dev=False))
    per_version = str(version)
    if cycle_title == per_version:
        return [cycle_title]
    return [cycle_title, per_version]


def find_missing_milestone_prs(
    milestone_prs: Iterable[int], release_prs: Iterable[int]
) -> List[int]:
    """Return milestone PR numbers that are absent from the release.

    ``milestone_prs`` is the set of (merged) PR numbers attached to the
    milestone; ``release_prs`` is the set of PR numbers actually present in the
    release branch (as parsed from the git log). The result is the sorted list
    of PRs that belong to the milestone but never landed — the commits that were
    "left behind".
    """
    return sorted(set(milestone_prs) - set(release_prs))
