"""Tests for docs PR-link extraction.

``docs_pr_links`` is deliberately import-clean (stdlib ``re`` only), so these
run without a configured working copy or any GitHub objects.
"""

from esphomerelease.docs_pr_links import extract_esphome_pr_numbers


def test_empty_body_returns_empty_list():
    assert extract_esphome_pr_numbers("") == []
    assert extract_esphome_pr_numbers(None) == []


def test_shorthand_reference():
    assert extract_esphome_pr_numbers("Fixes esphome/esphome#1234") == [1234]


def test_pull_url_reference():
    body = "See https://github.com/esphome/esphome/pull/999 for details"
    assert extract_esphome_pr_numbers(body) == [999]


def test_results_are_sorted_and_deduplicated():
    body = (
        "esphome/esphome#30 and esphome/esphome#10 plus "
        "https://github.com/esphome/esphome/pull/10"
    )
    assert extract_esphome_pr_numbers(body) == [10, 30]


def test_markdown_link_uses_destination_url():
    # Display text and URL both point at the same PR -> the PR is reported once.
    body = "[#1234](https://github.com/esphome/esphome/pull/1234)"
    assert extract_esphome_pr_numbers(body) == [1234]


def test_markdown_link_text_does_not_create_false_positive():
    # A discussions link whose display text *looks* like a PR shorthand must not
    # be reported: only the destination URL (a discussion) counts.
    body = "[esphome/esphome#3624](https://github.com/esphome/esphome/discussions/3624)"
    assert extract_esphome_pr_numbers(body) == []


def test_discussion_url_is_ignored():
    body = "https://github.com/esphome/esphome/discussions/42"
    assert extract_esphome_pr_numbers(body) == []


def test_issue_url_is_ignored():
    body = "https://github.com/esphome/esphome/issues/77"
    assert extract_esphome_pr_numbers(body) == []


def test_sibling_repo_shorthand_is_ignored():
    # esphome/esphome.io#5 is a docs-repo reference, not an esphome/esphome PR.
    assert extract_esphome_pr_numbers("esphome/esphome.io#5") == []


def test_sibling_repo_pull_url_is_ignored():
    body = "https://github.com/esphome/esphome.io/pull/5"
    assert extract_esphome_pr_numbers(body) == []


def test_mixed_real_and_decoy_references():
    body = (
        "Implements [esphome/esphome#100](https://github.com/esphome/esphome/pull/100). "
        "Related discussion [esphome/esphome#200]"
        "(https://github.com/esphome/esphome/discussions/200). "
        "Also esphome/esphome#300."
    )
    assert extract_esphome_pr_numbers(body) == [100, 300]


def test_no_references_returns_empty_list():
    assert extract_esphome_pr_numbers("Just a plain docs update, no links.") == []
