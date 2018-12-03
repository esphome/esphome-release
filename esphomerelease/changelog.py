from collections import OrderedDict
from datetime import datetime
from distutils.version import StrictVersion

import click

from esphomerelease.util import EsphomedocsProject, EsphomelibProject, \
    EsphomeyamlProject, gprint

LABEL_HEADERS = {
    'new-feature': 'New Features',
    'breaking-change': 'Breaking Changes',
    'cherry-picked': 'Beta Fixes',
}


def format_link(text, href, markdown):
    if markdown:
        return '[{}]({})'.format(text, href)
    return '`{} <{}>`__'.format(text, href)


def format_line(project, pr, msg, markdown):
    user = pr.user
    if user.login == 'OttoWinter':
        format = '- {project}: {message} {pr_link}'
    else:
        format = '- {project}: {message} {pr_link} by {user_link}'

    if markdown:
        pr_link = '[{}#{}]({})'.format(project.shortname, pr.number, pr.html_url)
        user_link = '[@{}]({})'.format(user, user.html_url)
    else:
        pr_link = ':{}pr:`{}`'.format(project.shortname, pr.number)
        user_link = ':ghuser:`{}`'.format(user)
    line = format.format(project=project.shortname, message=msg,
                         pr_link=pr_link, user_link=user_link)
    return line


def generate(release, *, markdown=False):
    gprint("Generating changelog...")
    label_groups = OrderedDict()
    label_groups['new-feature'] = []
    label_groups['breaking-change'] = []
    if release.version.version[-1] == 0:
        # Only add 'beta fix' for 0-release
        label_groups['cherry-picked'] = []

    changes = []
    lines = []

    list_ = []
    for prj in (EsphomelibProject, EsphomeyamlProject, EsphomedocsProject):
        list_ += [(prj, line) for line in release.log_lines(prj)]
    with click.progressbar(list_, label="PRs") as bar:
        for project, line in bar:
            # Filter out git commits that are not merge commits
            if line.pr is None:
                continue

            pr = project.get_pr(line.pr)

            labels = [label['name'] for label in pr.labels]

            # Filter out commits for which the PR has one of the ignored labels ('reverted')
            if 'reverted' in labels:
                continue

            lines.append((project, pr, line.message, labels))

    lines.sort(key=lambda x: x[1].merged_at)

    for project, pr, msg, labels in lines:
        parts = [format_line(project, pr, msg, markdown)]

        for label in labels:
            if label in label_groups:
                parts.append("({})".format(label))

        msg = ' '.join(parts)
        changes.append(msg)

        for label in labels:
            if label in label_groups:
                label_groups[label].append(msg)

    outp = []

    if release.is_patch_release:
        if not markdown:
            now = datetime.now()
            title = f'Release {release.version} - {now.strftime("%B")} {now.day}'
            outp.append(title)
            outp.append('-' * len(title))
            outp.append('')

    else:
        for label, prs in label_groups.items():
            # if label == 'breaking change':
            #     outp.append(WEBSITE_DIVIDER)

            if not prs:
                continue

            title = LABEL_HEADERS[label]
            if not markdown:
                outp.append(title)
                outp.append('-' * len(title))
            else:
                outp.append(f'## {title}')

            outp.append('')
            outp.extend(prs)
            outp.append('')

        title = 'All changes'
        if not markdown:
            outp.append(title)
            outp.append('-' * len(title))
        else:
            outp.append(f'## {title}')
        outp.append('')

    outp.extend(changes)
    return '\n'.join(outp)
