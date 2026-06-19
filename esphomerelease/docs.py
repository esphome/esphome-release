import json
from collections import OrderedDict
from datetime import datetime

from github3.exceptions import NotFoundError

from .github import get_session
from .project import EsphomeDocsProject
from .supporters import format_supporter_lines, render_supporters_template

# Contrib api does not return full user name, and since we query 1 api call per contrib
# cache so next runs takes less time.
USERS_CACHE_FILE = "users_cache.json"

MAX_RETRIES = 5

REPO_CONTRIBS_IGNORE = [
    "backlog",
]


def add_repo_contribs(session, contribs: list[str], repo):
    attempts = 0
    exception_message = ""
    while attempts < MAX_RETRIES:
        try:
            repo = session.repository("esphome", repo)
            repo_contribs = repo.contributors()
            for c in repo_contribs:
                if c.login not in contribs:
                    contribs.append(c.login)
        except Exception as e:
            attempts += 1
            exception_message = str(e)
        else:
            return

    print(f"Error getting contributors from {repo.name}: {exception_message}")


def gen_supporters():
    with open("supporters.template.md", "r", encoding="utf-8") as f:
        template = f.read()

    sess = get_session()

    try:
        with open(USERS_CACHE_FILE, encoding="utf-8") as f:
            usernames: dict[str, str] = json.load(f)
    except FileNotFoundError:
        usernames = {}

    contribs: list[str] = []

    orgs = sess.organization("esphome")

    for r in orgs.repositories():
        if r.name in REPO_CONTRIBS_IGNORE:
            continue
        add_repo_contribs(sess, contribs, r.name)

    sorted_usernames = sorted(usernames.keys(), key=str.casefold)

    for c in sorted(contribs, key=str.casefold):
        if c not in sorted_usernames:
            try:
                user = sess.user(c)
                usernames[c] = user.name
            except NotFoundError as e:
                print(f"Error getting user {c}: {e}")

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
