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
