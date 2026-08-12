"""Pure, import-clean formatting logic for the supporters page.

Extracted from ``docs.gen_supporters`` so it can be unit-tested without a
``config.json`` or any GitHub session. Imports stdlib only — no ``.config``
or ``.project`` coupling.
"""

from datetime import datetime
from typing import Mapping, TypedDict

CONTRIBUTIONS_PLACEHOLDER = "TEMPLATE_CONTRIBUTIONS"
GENERATION_DATE_PLACEHOLDER = "TEMPLATE_GENERATION_DATE"


class Supporter(TypedDict):
    """One cached contributor: their current login and display name.

    ``name`` is ``None`` when GitHub reports no name for the account, or when
    the name is otherwise uninformative (e.g. equal to the login).
    """

    login: str
    name: str | None


# Bot logins that don't use GitHub's "[bot]" suffix convention, casefolded.
BOT_LOGINS: frozenset[str] = frozenset({"copilot"})

# Bot account ids that use neither the "[bot]" suffix nor a BOT_LOGINS entry:
# 68923041 = esphomebot, 284751220 = bluetoothbot ("Bluetooth Devices Bot"),
# 287758279 = esphbot ("ESPHome Bot"). Ids are keyed as strings to match
# users_cache.json's keys.
BOT_IDS: frozenset[str] = frozenset({"68923041", "284751220", "287758279"})


def is_bot_login(login: str) -> bool:
    """True when ``login`` looks like a bot account, not a human contributor.

    Matches GitHub's own convention of suffixing machine accounts with
    ``[bot]`` (e.g. ``dependabot[bot]``, case-insensitive) plus any login
    listed in ``BOT_LOGINS`` for bots that don't use that suffix (e.g. the
    ``Copilot`` code-review account).
    """
    folded = login.casefold()
    return folded.endswith("[bot]") or folded in BOT_LOGINS


def is_bot_account(account_id: str, login: str) -> bool:
    """True when the account is a known bot, by id first, then by login.

    The numeric account id is checked against ``BOT_IDS`` first since it is
    rename-proof, unlike a login. ``is_bot_login`` is the fallback for bots
    that haven't been enumerated by id yet.
    """
    return account_id in BOT_IDS or is_bot_login(login)


def format_supporter_lines(users: Mapping[str, Supporter]) -> list[str]:
    """Render contributor markdown lines from an ``id -> Supporter`` mapping.

    Sorts case-insensitively by login (the mapping's keys are numeric GitHub
    account ids and are not used for ordering). Falls back to the login when
    the display name is missing (``None`` or empty), and strips surrounding
    whitespace from the chosen name. Mirrors the historical output exactly.
    """
    lines = []
    for entry in sorted(
        users.values(), key=lambda supporter: supporter["login"].casefold()
    ):
        login = entry["login"]
        name = entry["name"] or login
        lines.append(f"- [{name.strip()} (@{login})](https://github.com/{login})")
    return lines


def format_generation_date(now: datetime) -> str:
    """Format the generation timestamp as e.g. ``June 19, 2026``."""
    return f"{now:%B} {now.day}, {now.year}"


def render_supporters_template(
    template: str, contribs_lines: list[str], now: datetime
) -> str:
    """Substitute the contributions block and generation date into the template."""
    template = template.replace(CONTRIBUTIONS_PLACEHOLDER, "\n".join(contribs_lines))
    template = template.replace(
        GENERATION_DATE_PLACEHOLDER, format_generation_date(now)
    )
    return template
