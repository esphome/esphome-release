from datetime import datetime

from esphomerelease.supporters import (
    format_generation_date,
    format_supporter_lines,
    render_supporters_template,
)


def test_format_lines_basic():
    lines = format_supporter_lines({"alice": "Alice Example"})
    assert lines == ["- [Alice Example (@alice)](https://github.com/alice)"]


def test_format_lines_sorted_case_insensitively():
    lines = format_supporter_lines(
        {"zoe": "Zoe", "Bob": "Bob", "alice": "Alice"}
    )
    logins = [line.split("(@")[1].split(")")[0] for line in lines]
    assert logins == ["alice", "Bob", "zoe"]


def test_format_lines_missing_name_falls_back_to_login():
    assert format_supporter_lines({"ghost": None}) == [
        "- [ghost (@ghost)](https://github.com/ghost)"
    ]
    assert format_supporter_lines({"empty": ""}) == [
        "- [empty (@empty)](https://github.com/empty)"
    ]


def test_format_lines_strips_whitespace_from_name():
    assert format_supporter_lines({"x": "  Padded Name  "}) == [
        "- [Padded Name (@x)](https://github.com/x)"
    ]


def test_format_lines_empty_mapping():
    assert format_supporter_lines({}) == []


def test_format_generation_date():
    assert format_generation_date(datetime(2026, 6, 19)) == "June 19, 2026"
    assert format_generation_date(datetime(2026, 1, 1)) == "January 1, 2026"


def test_render_substitutes_both_placeholders():
    template = "Contributors:\nTEMPLATE_CONTRIBUTIONS\nGenerated TEMPLATE_GENERATION_DATE"
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
