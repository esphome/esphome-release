"""Tests for the pure ``release-cherry-pick`` PR body parser."""

import pytest

from esphomerelease.cherry_pick_override import (
    CherryPickOverride,
    OverrideParseResult,
    mentions_unstructured_override,
    override_for_branch,
    parse_overrides,
)

SHA = "4f53d5c8471da8081ef2077af36263785672def3"
SHA2 = "a" * 40

BODY_22111 = (
    "> [!IMPORTANT]\n"
    "> **Release manager:** cherry-pick "
    f"https://github.com/esphome/esphome/commit/{SHA} instead of this PR's commits\n\n"
    "# What does this implement?\n"
)


def block(*lines: str, fence: str = "```") -> str:
    return "\n".join([f"{fence}release-cherry-pick", *lines, fence])


def test_parse_happy_path() -> None:
    result = parse_overrides(f"intro\n\n{block('branch: release', f'commit: {SHA}')}\n\nmore text")
    assert result.errors == []
    assert result.overrides == [CherryPickOverride("release", SHA)]


def test_parse_tilde_fence_crlf_uppercase_and_loose_keys() -> None:
    body = block("", "  BRANCH :  beta ", f"Commit:{SHA.upper()}", "", fence="~~~~").replace("\n", "\r\n")
    result = parse_overrides(body)
    assert result.errors == []
    assert result.overrides == [CherryPickOverride("beta", SHA)]


def test_parse_multiple_branches() -> None:
    body = block("branch: release", f"commit: {SHA}") + "\n\n" + block("branch: beta", f"commit: {SHA2}")
    result = parse_overrides(body)
    assert result.errors == []
    assert [o.branch for o in result.overrides] == ["release", "beta"]


def test_parse_none_and_empty() -> None:
    assert parse_overrides(None) == OverrideParseResult()
    assert parse_overrides("") == OverrideParseResult()


def test_other_fences_and_inline_code_are_ignored() -> None:
    body = (
        "```python\nprint('hi')\n```\n"
        "```release-cherry-pick extra\nbranch: x\n```\n"
        "``` not `a fence`\n"
        f"~~~ release-cherry-pick\nbranch: release\ncommit: {SHA}\n~~~\n"
    )
    result = parse_overrides(body)
    assert result.errors == []
    assert result.overrides == [CherryPickOverride("release", SHA)]


def test_longer_fence_needs_matching_close() -> None:
    # A shorter fence and a different fence char inside do not close the block.
    body = f"````release-cherry-pick\nbranch: release\n```\ncommit: {SHA}\n````\n"
    result = parse_overrides(body)
    # The inner ``` line is not a key line, so it is reported.
    assert result.overrides == []
    assert any("unrecognised line" in e for e in result.errors)


def test_closing_fence_with_trailing_text_does_not_close() -> None:
    body = f"```release-cherry-pick\nbranch: release\ncommit: {SHA}\n``` trailing\n```\n"
    result = parse_overrides(body)
    assert any("unrecognised line" in e for e in result.errors)


def test_unterminated_block_runs_to_end() -> None:
    result = parse_overrides(f"```release-cherry-pick\nbranch: release\ncommit: {SHA}")
    assert result.overrides == [CherryPickOverride("release", SHA)]


def test_unterminated_other_block_is_ignored() -> None:
    assert parse_overrides("```text\nbranch: release") == OverrideParseResult()


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        (["branch: release", "commit: " + SHA, "extra: 1"], "unrecognised line"),
        (["branch: release", "just text", "commit: " + SHA], "unrecognised line"),
        ([f"commit: {SHA}"], "missing key 'branch'"),
        (["branch: release"], "missing key 'commit'"),
        (["branch:", f"commit: {SHA}"], "empty value for 'branch'"),
        (["branch: release", f"commit: {SHA}", "branch: beta"], "duplicate key 'branch'"),
        (["branch: release", "commit: abc1234"], "not a full 40-character hex SHA"),
        (["branch: release", "commit: " + "g" * 40], "not a full 40-character hex SHA"),
    ],
)
def test_block_errors(lines: list[str], message: str) -> None:
    result = parse_overrides(block(*lines))
    assert result.overrides == []
    assert any(message in e for e in result.errors), result.errors


def test_same_branch_twice_is_an_error() -> None:
    body = block("branch: release", f"commit: {SHA}") + "\n" + block("branch: release", f"commit: {SHA2}")
    result = parse_overrides(body)
    assert result.overrides == [CherryPickOverride("release", SHA)]
    assert result.errors == ["more than one release-cherry-pick block for branch 'release'"]


def test_override_for_branch() -> None:
    result = parse_overrides(block("branch: release", f"commit: {SHA}"))
    assert override_for_branch(result, "release") == CherryPickOverride("release", SHA)
    assert override_for_branch(result, "beta") is None


def test_mentions_unstructured_true_for_22111() -> None:
    assert mentions_unstructured_override(BODY_22111)


@pytest.mark.parametrize("phrase", ["Cherry-pick", "cherry pick", "CHERRYPICK"])
def test_mentions_unstructured_bare_sha(phrase: str) -> None:
    assert mentions_unstructured_override(f"Please {phrase} {SHA} instead")


def test_mentions_unstructured_false_cases() -> None:
    structured = "cherry-pick this:\n" + block("branch: release", f"commit: {SHA}")
    assert not mentions_unstructured_override(structured)
    assert not mentions_unstructured_override(f"see https://github.com/esphome/esphome/commit/{SHA}")
    assert not mentions_unstructured_override("please cherry-pick this PR")
    assert not mentions_unstructured_override(f"cherry-pick {SHA}0")
    assert not mentions_unstructured_override(None)
    assert not mentions_unstructured_override("")
