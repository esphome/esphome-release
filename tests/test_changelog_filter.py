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


# 2026.8.0b6 regression: cherry-picked PRs are milestoned with the cycle's
# final version, not a per-beta milestone, so the upper bound of the range
# check must be normalised to the final version when the head is a beta.
BETA_BASE = Version.parse("2026.8.0b5")
BETA_HEAD = Version.parse("2026.8.0b6")


def test_cherry_pick_milestoned_at_cycle_final_is_included_for_beta_head():
    # This is the 2026.8.0b6 regression: the milestone is the cycle's final
    # version (2026.8.0), which sorts above a beta head unless the upper
    # bound is normalised to the final version.
    result = resolve_changelog_labels(
        ["cherry-picked"], "2026.8.0", BETA_BASE, BETA_HEAD
    )
    assert result == ["cherry-picked"]


def test_cherry_pick_milestoned_for_later_cycle_is_excluded_for_beta_head():
    assert (
        resolve_changelog_labels(
            ["cherry-picked"], "2026.9.0", BETA_BASE, BETA_HEAD
        )
        is None
    )


def test_cherry_pick_milestoned_at_or_before_base_is_excluded_for_beta_head():
    # Milestoned for an earlier cycle entirely.
    assert (
        resolve_changelog_labels(
            ["cherry-picked"], "2026.7.0", BETA_BASE, BETA_HEAD
        )
        is None
    )
    # Milestoned exactly at the (previous-beta) base is excluded too -
    # already shipped in the base release.
    assert (
        resolve_changelog_labels(
            ["cherry-picked"], "2026.8.0b5", BETA_BASE, BETA_HEAD
        )
        is None
    )


def test_cherry_pick_milestoned_at_cycle_final_is_included_for_dev_head():
    # dev heads (e.g. from the manual `release-notes` command) go through the
    # same normalisation as beta heads.
    dev_base = Version.parse("2026.7.0")
    dev_head = dev_base.next_dev_version  # 2026.8.0-dev
    result = resolve_changelog_labels(
        ["cherry-picked"], "2026.8.0", dev_base, dev_head
    )
    assert result == ["cherry-picked"]


def test_cherry_pick_inside_range_for_non_beta_head_is_unchanged():
    # Non-beta (patch) heads keep their existing behaviour: the upper bound
    # is the head version itself, unmodified.
    patch_base = Version.parse("2026.8.0")
    patch_head = Version.parse("2026.8.1")
    result = resolve_changelog_labels(
        ["cherry-picked"], "2026.8.1", patch_base, patch_head
    )
    assert result == ["cherry-picked"]
