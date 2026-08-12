from datetime import datetime

from esphomerelease.supporters import (
    format_generation_date,
    format_supporter_lines,
    is_bot_account,
    is_bot_login,
    render_supporters_template,
)


def test_format_lines_basic():
    lines = format_supporter_lines({"1": {"login": "alice", "name": "Alice Example"}})
    assert lines == ["- [Alice Example (@alice)](https://github.com/alice)"]


def test_format_lines_sorted_case_insensitively():
    users = {
        "3": {"login": "zoe", "name": "Zoe"},
        "2": {"login": "Bob", "name": "Bob"},
        "1": {"login": "alice", "name": "Alice"},
    }
    lines = format_supporter_lines(users)
    logins = [line.split("(@")[1].split(")")[0] for line in lines]
    assert logins == ["alice", "Bob", "zoe"]


def test_format_lines_sort_ignores_id_key_order():
    """Ordering follows the login inside each entry, not the id keys."""
    users = {
        "999": {"login": "alice", "name": "Alice"},
        "1": {"login": "zoe", "name": "Zoe"},
    }
    lines = format_supporter_lines(users)
    logins = [line.split("(@")[1].split(")")[0] for line in lines]
    assert logins == ["alice", "zoe"]


def test_format_lines_missing_name_falls_back_to_login():
    assert format_supporter_lines({"1": {"login": "ghost", "name": None}}) == [
        "- [ghost (@ghost)](https://github.com/ghost)"
    ]
    assert format_supporter_lines({"1": {"login": "empty", "name": ""}}) == [
        "- [empty (@empty)](https://github.com/empty)"
    ]


def test_format_lines_strips_whitespace_from_name():
    users = {"1": {"login": "x", "name": "  Padded Name  "}}
    assert format_supporter_lines(users) == [
        "- [Padded Name (@x)](https://github.com/x)"
    ]


def test_format_lines_empty_mapping():
    assert format_supporter_lines({}) == []


def test_format_generation_date():
    assert format_generation_date(datetime(2026, 6, 19)) == "June 19, 2026"
    assert format_generation_date(datetime(2026, 1, 1)) == "January 1, 2026"


def test_render_substitutes_both_placeholders():
    template = (
        "Contributors:\nTEMPLATE_CONTRIBUTIONS\nGenerated TEMPLATE_GENERATION_DATE"
    )
    out = render_supporters_template(
        template,
        ["- a", "- b"],
        datetime(2026, 6, 19),
    )
    assert out == "Contributors:\n- a\n- b\nGenerated June 19, 2026"


def test_render_with_no_contributors():
    out = render_supporters_template(
        "TEMPLATE_CONTRIBUTIONS|TEMPLATE_GENERATION_DATE",
        [],
        datetime(2026, 12, 31),
    )
    assert out == "|December 31, 2026"


def test_is_bot_login_bot_suffix():
    assert is_bot_login("dependabot[bot]") is True


def test_is_bot_login_bot_suffix_uppercase():
    assert is_bot_login("DEPENDABOT[BOT]") is True


def test_is_bot_login_extra_bot_login():
    assert is_bot_login("copilot") is True
    assert is_bot_login("Copilot") is True


def test_is_bot_login_normal_login_returns_false():
    assert is_bot_login("alice") is False


def test_is_bot_account_id_in_bot_ids():
    assert is_bot_account("284751220", "bluetoothbot") is True


def test_is_bot_account_login_bot_suffix():
    assert is_bot_account("123", "dependabot[bot]") is True


def test_is_bot_account_clean_login_and_id_returns_false():
    assert is_bot_account("123", "alice") is False
