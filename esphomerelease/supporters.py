"""Pure, import-clean formatting logic for the supporters page.

Extracted from ``docs.gen_supporters`` so it can be unit-tested without a
``config.json`` or any GitHub session. Imports stdlib only — no ``.config``
or ``.project`` coupling.
"""

from datetime import datetime

CONTRIBUTIONS_PLACEHOLDER = "TEMPLATE_CONTRIBUTIONS"
GENERATION_DATE_PLACEHOLDER = "TEMPLATE_GENERATION_DATE"


def format_supporter_lines(usernames: dict[str, str]) -> list[str]:
    """Render contributor markdown lines from a ``login -> name`` mapping.

    Sorts case-insensitively by login. Falls back to the login when the
    display name is missing (``None`` or empty), and strips surrounding
    whitespace from the chosen name. Mirrors the historical output exactly.
    """
    lines = []
    for login in sorted(usernames.keys(), key=str.casefold):
        name = usernames[login] or login
        lines.append(f"- [{name.strip()} (@{login})](https://github.com/{login})")
    return lines


def format_generation_date(now: datetime) -> str:
    """Format the generation timestamp as e.g. ``June 19, 2026``."""
    return f"{now:%B} {now.day}, {now.year}"


def render_supporters_template(
    template: str, contribs_lines: list[str], now: datetime
) -> str:
    """Substitute the contributions block and generation date into the template."""
    template = template.replace(
        CONTRIBUTIONS_PLACEHOLDER, "\n".join(contribs_lines)
    )
    template = template.replace(
        GENERATION_DATE_PLACEHOLDER, format_generation_date(now)
    )
    return template
