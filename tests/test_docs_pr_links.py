"""Tests for docs/code PR-link extraction and pairing.

``docs_pr_links`` is deliberately import-clean (stdlib ``re`` only), so these
run without a configured working copy or any GitHub objects.
"""

from esphomerelease.docs_pr_links import (
    extract_docs_pr_numbers,
    extract_esphome_pr_numbers,
    is_confirmed_pair,
)


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


# --- extract_docs_pr_numbers: docs PR references inside a *code* PR body ---


def test_docs_empty_body_returns_empty_list():
    assert extract_docs_pr_numbers("") == []
    assert extract_docs_pr_numbers(None) == []


def test_docs_shorthand_reference():
    assert extract_docs_pr_numbers("- esphome/esphome.io#7071") == [7071]


def test_docs_old_repo_slug_shorthand_reference():
    # esphome/esphome-docs was renamed to esphome/esphome.io with PR numbering
    # preserved, so an old-slug reference names the same PR.
    assert extract_docs_pr_numbers("- esphome/esphome-docs#6676") == [6676]


def test_docs_pull_url_references():
    body = (
        "https://github.com/esphome/esphome.io/pull/123 and "
        "https://github.com/esphome/esphome-docs/pull/456"
    )
    assert extract_docs_pr_numbers(body) == [123, 456]


def test_docs_issue_and_discussion_urls_are_ignored():
    body = (
        "https://github.com/esphome/esphome.io/issues/123 "
        "https://github.com/esphome/esphome.io/discussions/123 "
        "https://github.com/esphome/esphome-docs/issues/456"
    )
    assert extract_docs_pr_numbers(body) == []


def test_docs_developer_docs_repo_is_not_matched():
    """developers.esphome.io is a *different* repo (the esphome PR template has
    its own field for it right below the docs field), so neither its shorthand
    nor its pull URL may be reported as a docs PR."""
    body = (
        "- esphome/developers.esphome.io#123\n"
        "- https://github.com/esphome/developers.esphome.io/pull/456\n"
    )
    assert extract_docs_pr_numbers(body) == []


def test_docs_unfilled_template_placeholder_is_ignored():
    # The esphome PR template ships this line verbatim; it must never be read as
    # a reference (the placeholder is non-numeric).
    body = "- esphome/esphome.io#<esphome.io PR number goes here>"
    assert extract_docs_pr_numbers(body) == []


def test_docs_template_heading_repo_link_is_ignored():
    """The template's heading links the repo itself, with no /pull/N."""
    body = (
        "**Pull request in [esphome.io](https://github.com/esphome/esphome.io) "
        "with documentation (if applicable):**"
    )
    assert extract_docs_pr_numbers(body) == []


def test_docs_markdown_link_text_does_not_create_false_positive():
    body = "[esphome/esphome.io#3624](https://github.com/esphome/esphome.io/issues/3624)"
    assert extract_docs_pr_numbers(body) == []


def test_docs_results_are_sorted_and_deduplicated():
    body = (
        "esphome/esphome.io#30 and esphome/esphome-docs#10 plus "
        "https://github.com/esphome/esphome.io/pull/10"
    )
    assert extract_docs_pr_numbers(body) == [10, 30]


def test_docs_code_repo_shorthand_is_ignored():
    # esphome/esphome#5 is a code PR reference, not a docs PR.
    assert extract_docs_pr_numbers("esphome/esphome#5") == []
    assert extract_docs_pr_numbers("https://github.com/esphome/esphome/pull/5") == []


def test_docs_no_references_returns_empty_list():
    assert extract_docs_pr_numbers("Just a code change, no docs needed.") == []


# --- is_confirmed_pair: bidirectionality ---

# Real bodies (trimmed) behind the esphome/esphome.io#7071 false positive.
DOCS_7071_BODY = (
    "Documents the default-route arbitration added in esphome/esphome#17797.\n"
    "Replaces the placeholder note (added with esphome/esphome#14255's docs).\n"
    "\n"
    "**Pull request in esphome with YAML changes (if applicable):**\n"
    "- esphome/esphome#17797\n"
)
CODE_17797_BODY = (
    "**Pull request in [esphome.io](https://github.com/esphome/esphome.io) "
    "with documentation (if applicable):**\n"
    "- esphome/esphome.io#7071\n"
)
CODE_14255_BODY = (
    "**Pull request in esphome-docs with documentation (if applicable):**\n"
    "- esphome/esphome-docs#6676\n"
)


def test_is_confirmed_pair_both_directions():
    assert is_confirmed_pair(
        docs_body=DOCS_7071_BODY,
        docs_number=7071,
        code_body=CODE_17797_BODY,
        code_number=17797,
    )


def test_is_confirmed_pair_rejects_prose_only_mention():
    """Docs #7071 names #14255 in prose, but #14255 links a different docs PR."""
    assert not is_confirmed_pair(
        docs_body=DOCS_7071_BODY,
        docs_number=7071,
        code_body=CODE_14255_BODY,
        code_number=14255,
    )


def test_is_confirmed_pair_rejects_missing_docs_side_reference():
    assert not is_confirmed_pair(
        docs_body="No code PR named here.",
        docs_number=7071,
        code_body=CODE_17797_BODY,
        code_number=17797,
    )


def test_is_confirmed_pair_rejects_empty_bodies():
    assert not is_confirmed_pair(
        docs_body=None, docs_number=1, code_body=None, code_number=2
    )
