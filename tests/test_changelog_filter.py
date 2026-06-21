"""Tests for changelog PR-inclusion logic.

``changelog_filter`` is deliberately import-clean (only ``.model``), so these
run without a configured working copy or any GitHub objects.
"""

from esphomerelease.changelog_filter import resolve_changelog_labels
from esphomerelease.model import Version

BASE = Version.parse("1.15.0")
HEAD = Version.parse("1.15.3")


def test_plain_pr_is_included_unchanged():
    labels = ["new-feature"]
    assert resolve_changelog_labels(labels, None, BASE, HEAD) == ["new-feature"]


def test_reverted_pr_is_excluded():
    assert resolve_changelog_labels(["reverted"], None, BASE, HEAD) is None
    # Excluded even alongside other labels.
    assert (
        resolve_changelog_labels(["reverted", "new-feature"], None, BASE, HEAD) is None
    )


def test_cherry_pick_inside_range_is_included():
    # 1.15.0 < 1.15.2 <= 1.15.3
    result = resolve_changelog_labels(
        ["cherry-picked"], "1.15.2", BASE, HEAD
    )
    assert result == ["cherry-picked"]


def test_cherry_pick_at_head_is_included():
    # Upper bound is inclusive.
    result = resolve_changelog_labels(["cherry-picked"], "1.15.3", BASE, HEAD)
    assert result == ["cherry-picked"]


def test_cherry_pick_at_base_is_excluded():
    # Lower bound is exclusive — already shipped in the base release.
    assert resolve_changelog_labels(["cherry-picked"], "1.15.0", BASE, HEAD) is None


def test_cherry_pick_before_base_is_excluded():
    assert resolve_changelog_labels(["cherry-picked"], "1.14.0", BASE, HEAD) is None


def test_cherry_pick_after_head_is_excluded():
    assert resolve_changelog_labels(["cherry-picked"], "1.15.4", BASE, HEAD) is None


def test_cherry_pick_unparseable_milestone_keeps_pr_drops_label():
    result = resolve_changelog_labels(
        ["cherry-picked", "bugfix"], "not-a-version", BASE, HEAD
    )
    assert result == ["bugfix"]


def test_cherry_pick_without_milestone_is_included_unchanged():
    result = resolve_changelog_labels(["cherry-picked"], None, BASE, HEAD)
    assert result == ["cherry-picked"]


def test_input_labels_not_mutated():
    labels = ["cherry-picked", "bugfix"]
    resolve_changelog_labels(labels, "not-a-version", BASE, HEAD)
    assert labels == ["cherry-picked", "bugfix"]


def test_reverted_takes_precedence_over_cherry_pick():
    # A reverted cherry-pick is dropped regardless of milestone range.
    assert (
        resolve_changelog_labels(
            ["reverted", "cherry-picked"], "1.15.2", BASE, HEAD
        )
        is None
    )
