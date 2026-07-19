"""Unit tests for esphomerelease.model.Version.

These exercise pure, dependency-free logic: parsing, version arithmetic and
ordering. The module imports only the stdlib, so no ``config.json`` or sibling
repository checkouts are required to run them.
"""

import pytest

from esphomerelease.model import Version


def test_parse_stable():
    v = Version.parse("1.15.0")
    assert (v.major, v.minor, v.patch, v.beta, v.dev) == (1, 15, 0, 0, False)


def test_parse_beta():
    v = Version.parse("1.15.0b3")
    assert (v.major, v.minor, v.patch, v.beta, v.dev) == (1, 15, 0, 3, False)


def test_parse_dev():
    v = Version.parse("1.15.0-dev")
    assert v.dev is True
    assert v.beta == 0


def test_parse_rejects_zero_beta():
    with pytest.raises(ValueError):
        Version.parse("1.15.0b0")


def test_parse_rejects_invalid():
    with pytest.raises(ValueError):
        Version.parse("not-a-version")


@pytest.mark.parametrize(
    "text",
    ["1.15.0", "1.15.0b1", "1.15.0-dev", "2.0.1"],
)
def test_str_roundtrip(text):
    assert str(Version.parse(text)) == text


def test_previous_patch_version():
    assert Version.parse("1.15.2").previous_patch_version == Version.parse("1.15.1")


def test_previous_patch_version_zero_raises():
    with pytest.raises(ValueError):
        Version.parse("1.15.0").previous_patch_version


def test_next_patch_version_clears_prerelease():
    assert Version.parse("1.15.0b2").next_patch_version == Version.parse("1.15.1")


def test_next_beta_version():
    assert Version.parse("1.15.0b1").next_beta_version == Version.parse("1.15.0b2")


def test_previous_beta_version():
    assert Version.parse("1.15.0b2").previous_beta_version == Version.parse("1.15.0b1")


def test_previous_beta_version_zero_raises():
    with pytest.raises(ValueError):
        Version.parse("1.15.0").previous_beta_version


def test_next_dev_version_bumps_minor():
    assert Version.parse("1.14.5").next_dev_version == Version.parse("1.15.0-dev")


# --- Ordering ----------------------------------------------------------------

ORDERED = [
    "1.14.5",
    "1.15.0-dev",
    "1.15.0b1",
    "1.15.0b2",
    "1.15.0",
    "1.15.1",
    "2.0.0",
]


@pytest.mark.parametrize("idx", range(len(ORDERED) - 1))
def test_strictly_increasing(idx):
    lower = Version.parse(ORDERED[idx])
    higher = Version.parse(ORDERED[idx + 1])
    assert lower < higher
    assert higher > lower
    assert not (higher < lower)


def test_beta_ordering():
    """Regression: betas must compare in numeric order, not collapse to equal."""
    assert Version.parse("1.15.0b1") < Version.parse("1.15.0b2")


def test_beta_precedes_stable():
    """Regression: a beta is older than its final stable release."""
    assert Version.parse("1.15.0b1") < Version.parse("1.15.0")
    assert not (Version.parse("1.15.0") < Version.parse("1.15.0b1"))


def test_dev_precedes_beta_and_stable():
    assert Version.parse("1.15.0-dev") < Version.parse("1.15.0b1")
    assert Version.parse("1.15.0-dev") < Version.parse("1.15.0")


def test_max_picks_latest_prerelease():
    """``Project.latest_release`` relies on ``max`` over parsed releases."""
    versions = [
        Version.parse(t) for t in ["1.15.0b1", "1.15.0-dev", "1.15.0b2", "1.14.9"]
    ]
    assert max(versions) == Version.parse("1.15.0b2")


def test_sorted_full_order():
    shuffled = [Version.parse(t) for t in reversed(ORDERED)]
    assert [str(v) for v in sorted(shuffled)] == ORDERED


def test_le_ge_equal():
    v = Version.parse("1.15.0")
    assert v <= v
    assert v >= v


def test_equality_distinguishes_prerelease():
    assert Version.parse("1.15.0") != Version.parse("1.15.0b1")
    assert Version.parse("1.15.0") != Version.parse("1.15.0-dev")
