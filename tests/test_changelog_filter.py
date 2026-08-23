"""Tests for changelog PR-inclusion logic.

``changelog_filter`` is deliberately import-clean (only ``.model``), so these
run without a configured working copy or any GitHub objects.
"""

from esphomerelease.changelog_filter import resolve_changelog_labels
from esphomerelease.model import Version

# A non-patch head, so these cases exercise the generic rules rather than the
# patch-only milestone filter (covered separately at the bottom of the file).
BASE = Version.parse("1.15.0")
HEAD = Version.parse("1.16.0")


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
    # 1.15.0 < 1.15.2 <= 1.16.0
    result = resolve_changelog_labels(
        ["cherry-picked"], "1.15.2", BASE, HEAD
    )
    assert result == ["cherry-picked"]


def test_cherry_pick_at_head_is_included():
    # Upper bound is inclusive.
    result = resolve_changelog_labels(["cherry-picked"], "1.16.0", BASE, HEAD)
    assert result == ["cherry-picked"]


def test_cherry_pick_at_base_is_excluded():
    # Lower bound is exclusive — already shipped in the base release.
    assert resolve_changelog_labels(["cherry-picked"], "1.15.0", BASE, HEAD) is None


def test_cherry_pick_before_base_is_excluded():
    assert resolve_changelog_labels(["cherry-picked"], "1.14.0", BASE, HEAD) is None


def test_cherry_pick_after_head_is_excluded():
    assert resolve_changelog_labels(["cherry-picked"], "1.16.1", BASE, HEAD) is None


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


# A patch release ships exactly the PRs on its own milestone. Without this the
# docs release PR and GitHub release listed every PR merged onto the docs
# ``current`` branch since the previous release, because the patch bump branch
# is cut from ``current`` and those PRs sit in the released commit range.
PATCH_BASE = Version.parse("2026.8.0")
PATCH_HEAD = Version.parse("2026.8.1")


def test_patch_includes_pr_on_the_patch_milestone():
    result = resolve_changelog_labels(["bugfix"], "2026.8.1", PATCH_BASE, PATCH_HEAD)
    assert result == ["bugfix"]


def test_patch_excludes_pr_without_a_milestone():
    # Docs fixes merged straight onto ``current`` between releases.
    assert resolve_changelog_labels(["bugfix"], None, PATCH_BASE, PATCH_HEAD) is None


def test_patch_excludes_pr_with_unparseable_milestone():
    assert (
        resolve_changelog_labels(["bugfix"], "not-a-version", PATCH_BASE, PATCH_HEAD)
        is None
    )


def test_patch_excludes_pr_on_the_cycle_milestone():
    # Milestoned for the .0 release - already shipped in the base.
    assert (
        resolve_changelog_labels(["bugfix"], "2026.8.0", PATCH_BASE, PATCH_HEAD) is None
    )


def test_patch_excludes_pr_on_a_later_patch_milestone():
    assert (
        resolve_changelog_labels(["bugfix"], "2026.8.2", PATCH_BASE, PATCH_HEAD) is None
    )


def test_patch_keeps_cherry_picked_label_on_milestone_pr():
    result = resolve_changelog_labels(
        ["cherry-picked"], "2026.8.1", PATCH_BASE, PATCH_HEAD
    )
    assert result == ["cherry-picked"]


def test_patch_reverted_takes_precedence():
    assert (
        resolve_changelog_labels(["reverted"], "2026.8.1", PATCH_BASE, PATCH_HEAD)
        is None
    )


def test_patch_input_labels_not_mutated():
    labels = ["cherry-picked", "bugfix"]
    resolve_changelog_labels(labels, "2026.8.1", PATCH_BASE, PATCH_HEAD)
    assert labels == ["cherry-picked", "bugfix"]


def test_patch_beta_head_is_not_milestone_filtered():
    # A beta of a patch line still goes through the generic rules.
    assert resolve_changelog_labels(
        ["bugfix"], None, PATCH_BASE, Version.parse("2026.8.1b1")
    ) == ["bugfix"]


def test_patch_dev_head_is_not_milestone_filtered():
    dev_head = Version.parse("2026.8.1").replace(dev=True)
    assert resolve_changelog_labels(["bugfix"], None, PATCH_BASE, dev_head) == [
        "bugfix"
    ]
