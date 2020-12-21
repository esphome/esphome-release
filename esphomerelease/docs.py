import json
import codecs
from datetime import datetime
from .project import EsphomeDocsProject
from .github import get_session

# Contrib api does not return full user name, and since we query 1 api call per contrib
# cache so next runs takes less time.
USERS_CACHE_FILE = '.users_cache.json'


def add_repo_contribs(session, contribs, repo):
    repo = session.repository('esphome', repo)
    repo_contribs = repo.contributors()

    for c in repo_contribs:
        if c.login in contribs:
            contribs[c.login] = contribs[c.login] + c.contributions_count
        else:
            contribs[c.login] = c.contributions_count


def gen_supporters():
    template = open('supporters.template.rst', 'r', encoding='utf-8').read()

    sess = get_session()

    try:
        usernames = json.load(open(USERS_CACHE_FILE))
    except FileNotFoundError:
        usernames = {}

    contribs = {}

    orgs = sess.organization('esphome')

    for r in orgs.repositories():
        add_repo_contribs(sess, contribs, r.name)

    OttoContribs = 0

    contribs_lines = []

    for c in sorted(contribs.keys(), key=str.casefold):
        count = contribs[c]
        if c == 'OttoWinter':
            OttoContribs = count
            continue

        if not c in usernames:
            user = sess.user(c)
            usernames[c] = user.name

        name = usernames[c] or c
        contribs_lines.append(
            f'- `{name} (@{c}) <https://github.com/{c}>`__ - {count:d} contribution{("" if count == 1 else "s")}')

    json.dump(usernames, open(USERS_CACHE_FILE, 'w'))

    output_filename = EsphomeDocsProject.path / 'guides' / 'supporters.rst'
    output = codecs.open(output_filename, 'w', 'utf-8')

    template = template.replace('TEMPLATE_OTTO_CONTRIBUTIONS', str(OttoContribs))
    template = template.replace('TEMPLATE_CONTRIBUTIONS', '\n'.join(contribs_lines))

    now = datetime.now()
    template = template.replace('TEMPLATE_GENERATION_DATE', f'{now:%B} {now.day}, {now.year}')
    output.write(template)
