import json
from collections import OrderedDict
from datetime import datetime

from github3.exceptions import NotFoundError

from .github import get_session
from .project import EsphomeDocsProject

# Contrib api does not return full user name, and since we query 1 api call per contrib
# cache so next runs takes less time.
USERS_CACHE_FILE = "users_cache.json"


def add_repo_contribs(session, contribs: list[str], repo):
    repo = session.repository("esphome", repo)
    repo_contribs = repo.contributors()

    try:
        for c in repo_contribs:
            if c.login not in contribs:
                contribs.append(c.login)
    except Exception as e:
        print(f"Error getting contributors from {repo.name}: {e}")


def gen_supporters():
    with open("supporters.template.rst", "r", encoding="utf-8") as f:
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
        add_repo_contribs(sess, contribs, r.name)

    contribs_lines = []

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

    for c in sorted_users:
        name = usernames[c] or c
        contribs_lines.append(f"- `{name} (@{c}) <https://github.com/{c}>`__")

    with open(USERS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted_users, f, indent=2)

    output_filename = EsphomeDocsProject.path / "guides" / "supporters.rst"

    template = template.replace("TEMPLATE_CONTRIBUTIONS", "\n".join(contribs_lines))

    now = datetime.now()
    template = template.replace(
        "TEMPLATE_GENERATION_DATE", f"{now:%B} {now.day}, {now.year}"
    )
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(template)
