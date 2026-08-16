"""Tests for ordering ``Version`` objects.

Within one ``major.minor.patch`` the order is dev < betas (in order) < final,
which is what ``Project.latest_release`` and the cut/publish lookups rely on.
"""

import pytest

from esphomerelease.model import Version

# Ascending order; every pair is checked in both directions below.
ORDERED = [
    "2025.12.4",
    "2026.1.0-dev",
    "2026.1.0b1",
    "2026.1.0b2",
    "2026.1.0b10",
    "2026.1.0",
    "2026.1.1",
    "2026.2.0b1",
    "2026.2.0",
    "2027.1.0",
]


@pytest.mark.parametrize("index", range(len(ORDERED) - 1))
def test_consecutive_versions_are_ordered(index):
    lower = Version.parse(ORDERED[index])
    higher = Version.parse(ORDERED[index + 1])

    assert lower < higher
    assert lower <= higher
    assert higher > lower
    assert higher >= lower
    assert not higher < lower
    assert not lower > higher


@pytest.mark.parametrize("value", ORDERED)
def test_version_equal_to_itself(value):
    version = Version.parse(value)
    same = Version.parse(value)

    assert version == same
    assert version <= same
    assert version >= same
    assert not version < same
    assert not version > same


def test_max_picks_highest_regardless_of_input_order():
    versions = [Version.parse(value) for value in reversed(ORDERED)]

    assert max(versions) == Version.parse(ORDERED[-1])
    assert min(versions) == Version.parse(ORDERED[0])


def test_sorted_matches_expected_order():
    versions = [Version.parse(value) for value in reversed(ORDERED)]

    assert [str(v) for v in sorted(versions)] == [
        str(Version.parse(value)) for value in ORDERED
    ]
