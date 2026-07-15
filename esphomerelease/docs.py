import functools
import json
from collections import OrderedDict
from datetime import datetime
from typing import Optional

from github3.exceptions import NotFoundError

from .github import get_session
from .project import EsphomeDocsProject
from .supporters import format_supporter_lines, render_supporters_template
from .util import process_asynchronously

# Contrib api does not return full user name, and since we query 1 api call per contrib
# cache so next runs takes less time.
USERS_CACHE_FILE = "users_cache.json"

MAX_RETRIES = 5

REPO_CONTRIBS_IGNORE = [
    "backlog",
]


def get_repo_contribs(session, repo_name: str) -> list[str]:
    """Contributor logins of one repo, retrying transient API errors."""
    attempts = 0
    exception_message = ""
    while attempts < MAX_RETRIES:
        try:
            repo = session.repository("esphome", repo_name)
            return [c.login for c in repo.contributors()]
        except Exception as e:  # pylint: disable=broad-except
            attempts += 1
            exception_message = str(e)

    print(f"Error getting contributors from {repo_name}: {exception_message}")
    return []


def _fetch_user_name(session, login: str) -> tuple[str, Optional[str], Optional[str]]:
    """Look up a user's display name; returns (login, name, error)."""
    try:
        return login, session.user(login).name, None
    except NotFoundError as e:
        return login, None, str(e)


def gen_supporters():
    with open("supporters.template.md", "r", encoding="utf-8") as f:
        template = f.read()

    sess = get_session()

    try:
        with open(USERS_CACHE_FILE, encoding="utf-8") as f:
            usernames: dict[str, str] = json.load(f)
    except FileNotFoundError:
        usernames = {}

    orgs = sess.organization("esphome")
    repo_names = [
        r.name for r in orgs.repositories() if r.name not in REPO_CONTRIBS_IGNORE
    ]

    contrib_jobs = [
        functools.partial(get_repo_contribs, sess, name) for name in repo_names
    ]
    contribs: list[str] = list(
        dict.fromkeys(
            login
            for repo_contribs in process_asynchronously(
                contrib_jobs, "Fetching contributors"
            )
            for login in repo_contribs
        )
    )

    user_jobs = [
        functools.partial(_fetch_user_name, sess, c)
        for c in sorted(contribs, key=str.casefold)
        if c not in usernames
    ]
    for login, name, error in process_asynchronously(user_jobs, "Fetching user names"):
        if error is not None:
            print(f"Error getting user {login}: {error}")
            continue
        usernames[login] = name

    sorted_users = OrderedDict(
        sorted(usernames.items(), key=lambda item: str.casefold(item[0]))
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
