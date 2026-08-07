"""Tests for milestone-completeness detection.

These exercise the pure set logic in isolation so they need no GitHub access or
configured working copy.
"""

from esphomerelease.milestone import (
    find_missing_milestone_prs,
    milestone_title_candidates,
)
from esphomerelease.model import Version


def test_beta_tries_cycle_title_then_per_version():
    # A beta resolves the per-cycle milestone first, then its own title.
    v = Version.parse("2026.6.0b3")
    assert milestone_title_candidates(v) == ["2026.6.0", "2026.6.0b3"]


def test_final_release_has_single_candidate():
    # The final .0 release: cycle title == per-version title, deduplicated.
    v = Version.parse("2026.6.0")
    assert milestone_title_candidates(v) == ["2026.6.0"]


def test_patch_release_keeps_its_own_title():
    # Stripping beta/dev leaves the patch component, so a patch has one title.
    v = Version.parse("2026.6.1")
    assert milestone_title_candidates(v) == ["2026.6.1"]


def test_dev_version_strips_to_cycle_title():
    v = Version.parse("2026.6.0-dev")
    assert milestone_title_candidates(v) == ["2026.6.0", "2026.6.0-dev"]


def test_all_present_returns_empty():
    assert find_missing_milestone_prs([1, 2, 3], [3, 2, 1]) == []


def test_missing_prs_are_reported_sorted():
    # 5 and 2 are on the milestone but never landed in the release.
    assert find_missing_milestone_prs([1, 2, 5], [1, 3]) == [2, 5]


def test_extra_release_prs_are_ignored():
    # PRs in the release that are not on the milestone are irrelevant.
    assert find_missing_milestone_prs([1], [1, 2, 3, 4]) == []


def test_empty_milestone_returns_empty():
    assert find_missing_milestone_prs([], [1, 2]) == []


def test_empty_release_returns_all_milestone_prs():
    assert find_missing_milestone_prs([3, 1, 2], []) == [1, 2, 3]


def test_duplicates_are_deduplicated():
    assert find_missing_milestone_prs([1, 1, 2, 2], [1]) == [2]


def test_accepts_arbitrary_iterables():
    # Sets and generators must work, not just lists.
    missing = find_missing_milestone_prs({4, 5, 6}, (n for n in (5,)))
    assert missing == [4, 6]
