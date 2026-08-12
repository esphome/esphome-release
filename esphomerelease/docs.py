import functools
import json
from datetime import datetime
from typing import Optional

from github3.exceptions import NotFoundError

from .github import get_session
from .project import EsphomeDocsProject
from .supporters import (
    Supporter,
    format_supporter_lines,
    is_bot_account,
    render_supporters_template,
)
from .util import process_asynchronously

# Contrib api does not return full user name, and since we query 1 api call per contrib
# cache so next runs takes less time.
USERS_CACHE_FILE = "users_cache.json"

MAX_RETRIES = 5

REPO_CONTRIBS_IGNORE = [
    "backlog",
]


def get_repo_contribs(session, repo_name: str) -> list[tuple[str, str]]:
    """(id, login) pairs of one repo's contributors, retrying transient API errors.

    Bot accounts (GitHub-reported ``type == "Bot"``, or an id/login matching
    ``is_bot_account``) are filtered out here so they never get written back
    into users_cache.json by gen_supporters(). The id is the numeric GitHub
    account id, stable across login renames.
    """
    attempts = 0
    exception_message = ""
    while attempts < MAX_RETRIES:
        try:
            repo = session.repository("esphome", repo_name)
            return [
                (str(c.id), c.login)
                for c in repo.contributors()
                if c.type != "Bot" and not is_bot_account(str(c.id), c.login)
            ]
        except Exception as e:  # pylint: disable=broad-except
            attempts += 1
            exception_message = str(e)

    print(f"Error getting contributors from {repo_name}: {exception_message}")
    return []


def _fetch_user_name(
    session, account_id: str, login: str
) -> tuple[str, str, Optional[str], Optional[str]]:
    """Look up a user's display name; returns (id, login, name, error)."""
    try:
        return account_id, login, session.user(login).name, None
    except NotFoundError as e:
        return account_id, login, None, str(e)


def gen_supporters():
    with open("supporters.template.md", "r", encoding="utf-8") as f:
        template = f.read()

    sess = get_session()

    try:
        with open(USERS_CACHE_FILE, encoding="utf-8") as f:
            users: dict[str, Supporter] = json.load(f)
    except FileNotFoundError:
        users = {}

    orgs = sess.organization("esphome")
    repo_names = [
        r.name for r in orgs.repositories() if r.name not in REPO_CONTRIBS_IGNORE
    ]

    contrib_jobs = [
        functools.partial(get_repo_contribs, sess, name) for name in repo_names
    ]
    # id -> login, deduplicated across repos. A later repo's casing for a
    # given id (should GitHub ever disagree with itself) wins, but this is
    # purely cosmetic - the id is what identifies the account.
    contribs: dict[str, str] = {}
    for repo_contribs in process_asynchronously(contrib_jobs, "Fetching contributors"):
        for account_id, login in repo_contribs:
            contribs[account_id] = login

    # Only fetch a display name for ids the cache doesn't already know about -
    # ids already cached keep their stored name untouched below, so this is
    # the only place an API call for a name happens.
    user_jobs = [
        functools.partial(_fetch_user_name, sess, account_id, contribs[account_id])
        for account_id in sorted(contribs, key=lambda i: contribs[i].casefold())
        if account_id not in users
    ]
    for account_id, login, name, error in process_asynchronously(
        user_jobs, "Fetching user names"
    ):
        if error is not None:
            print(f"Error getting user {login}: {error}")
            continue
        users[account_id] = {"login": login, "name": name}

    # Free rename tracking: for ids already in the cache, pick up GitHub's
    # current login without spending another API call on the name.
    for account_id, login in contribs.items():
        if account_id in users:
            users[account_id]["login"] = login

    sorted_users: dict[str, Supporter] = dict(
        sorted(users.items(), key=lambda item: int(item[0]))
    )

    contribs_lines = format_supporter_lines(sorted_users)

    with open(USERS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted_users, f, indent=2)

    output_filename = (
        EsphomeDocsProject.path
        / "src"
        / "content"
        / "docs"
        / "guides"
        / "supporters.mdx"
    )

    template = render_supporters_template(template, contribs_lines, datetime.now())
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(template)
