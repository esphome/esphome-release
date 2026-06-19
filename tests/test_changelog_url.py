"""Unit tests for esphomerelease.changelog_url.

These exercise pure, dependency-free logic: changelog website URL construction,
the first-beta/first-release link decision and the length threshold. The module
imports only :mod:`esphomerelease.model` (stdlib only), so no ``config.json`` or
sibling repository checkouts are required to run them.
"""

import pytest

from esphomerelease.changelog_url import (
    MAX_CHANGELOG_LENGTH,
    changelog_too_long,
    changelog_website_url,
    use_website_link_for_release,
)
from esphomerelease.model import Version


# ---- changelog_website_url -------------------------------------------------


def test_website_url_stable_strips_patch():
    url = changelog_website_url(Version.parse("2026.6.3"))
    assert url == "https://esphome.io/changelog/2026.6.0.html"


def test_website_url_first_release_already_minor_zero():
    url = changelog_website_url(Version.parse("2026.6.0"))
    assert url == "https://esphome.io/changelog/2026.6.0.html"


def test_website_url_beta_uses_beta_subdomain_and_strips_beta():
    url = changelog_website_url(Version.parse("2026.6.0b3"))
    assert url == "https://beta.esphome.io/changelog/2026.6.0.html"


def test_website_url_dev_uses_stable_domain():
    # dev versions have beta == 0, so they resolve to the stable subdomain.
    url = changelog_website_url(Version.parse("2026.7.0-dev"))
    assert url == "https://esphome.io/changelog/2026.7.0.html"


def test_website_url_does_not_mutate_input():
    version = Version.parse("2026.6.3b2")
    changelog_website_url(version)
    # original is unchanged (Version is frozen, but guard the contract anyway)
    assert version == Version.parse("2026.6.3b2")


# ---- use_website_link_for_release ------------------------------------------


def test_link_for_first_beta_primary():
    assert use_website_link_for_release(
        Version.parse("2026.6.0b1"), is_primary_project=True
    )


def test_link_for_first_stable_release_primary():
    assert use_website_link_for_release(
        Version.parse("2026.6.0"), is_primary_project=True
    )


def test_no_link_for_later_beta_primary():
    assert not use_website_link_for_release(
        Version.parse("2026.6.0b2"), is_primary_project=True
    )


def test_no_link_for_patch_release_primary():
    assert not use_website_link_for_release(
        Version.parse("2026.6.3"), is_primary_project=True
    )


def test_no_link_for_non_primary_even_on_first_beta():
    assert not use_website_link_for_release(
        Version.parse("2026.6.0b1"), is_primary_project=False
    )


def test_no_link_for_non_primary_first_release():
    assert not use_website_link_for_release(
        Version.parse("2026.6.0"), is_primary_project=False
    )


# ---- changelog_too_long ----------------------------------------------------


def test_too_long_false_at_limit():
    assert not changelog_too_long("x" * MAX_CHANGELOG_LENGTH)


def test_too_long_true_above_limit():
    assert changelog_too_long("x" * (MAX_CHANGELOG_LENGTH + 1))


def test_too_long_false_for_empty():
    assert not changelog_too_long("")


@pytest.mark.parametrize(
    "version_str",
    ["2026.6.0", "2026.6.0b1", "2026.6.5", "1.15.0b3", "2026.12.0-dev"],
)
def test_website_url_is_well_formed(version_str):
    url = changelog_website_url(Version.parse(version_str))
    assert url.startswith("https://")
    assert url.endswith(".html")
    assert "/changelog/" in url
