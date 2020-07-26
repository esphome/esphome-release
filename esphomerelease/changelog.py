from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple
import functools

from github3.pulls import PullRequest

from .util import gprint, process_asynchronously
from .project import EsphomeDocsProject, EsphomeProject, Project
from .model import Version, BranchType


# Extra headers that are inserted in the changelog if
# one of these labels is applied
LABEL_HEADERS = {
    'new-feature': 'New Features',
    'new-integration': 'New Integrations',
    'breaking-change': 'Breaking Changes',
    'cherry-picked': 'Beta Fixes',
    'notable-change': 'Notable Changes',
}


def format_heading(title: str, markdown: bool, level: int = 2):
    if markdown:
        c = level * "#"
        return f'{c} {title}\n'
    else:
        prefix = {
            1: '=',
            2: '-',
            3: '*',
        }[level]
        return f'{title}\n{len(title) * prefix}\n'


def format_line(*, project: Project, pr: PullRequest, markdown: bool, include_author: bool) -> str:
    username = pr.user.login
    if markdown:
        pr_link = f'[{project.shortname}#{pr.number}]({pr.html_url})'
        user_link = f'[@{username}]({pr.user.html_url})'
    else:
        pr_link = f':{project.shortname}pr:`{pr.number}`'
        user_link = f':ghuser:`{username}`'

    line = f'- {project.shortname}: {pr.title} {pr_link}'
    if include_author and username != 'OttoWinter':
        line += f' by {user_link}'
    return line


def generate(*, base: BranchType, base_version: Version,
             head: BranchType, head_version: Version,
             markdown: bool = False, with_sections: bool = True,
             include_author: bool = True):
    gprint("Generating changelog...")

    # Here we store the lines to insert for each label
    # Mapping from label to list of lines
    label_groups: Dict[str, List[str]] = defaultdict(list)

    # Create a list of all log lines in all relevant projects
    list_: List[Tuple[Project, int]] = []

    for prj in (EsphomeProject, EsphomeDocsProject):
        list_ += [(prj, pr_number) for pr_number in
                  prj.prs_between(base, head)]

    lines: List[Tuple[Project, PullRequest, List[str]]] = []

    def job(project, pr_number):
        pr: PullRequest = project.get_pr(pr_number)

        labels: List[str] = [label['name'] for label in pr.labels]

        # Filter out commits for which the PR has one of the ignored
        # labels ('reverted')
        if 'reverted' in labels:
            return

        if 'cherry-picked' in labels:
            milestone = pr.milestone['title']
            try:
                pick_version = Version.parse(pr.milestone['title'])
                if pick_version < base_version or pick_version > head_version:
                    # Not included in this release
                    return
            except ValueError:
                print(f"Could not parse milestone {milestone}")
                labels.remove('cherry-picked')

        lines.append((project, pr, labels))

    jobs = [functools.partial(job, *it) for it in list_]
    process_asynchronously(jobs, "Load PRs")

    # Sort log lines by when the PR was merged
    lines.sort(key=lambda x: x[1].merged_at)

    # A list of strings containing all serialized changes
    changes: List[str] = []

    # Now go through the lines struct and serialize them
    for project, pr, labels in lines:
        parts = [format_line(
            project=project, pr=pr, markdown=markdown,
            include_author=include_author
        )]
        parts += [f"({label})" for label in labels if label in LABEL_HEADERS]

        msg = ' '.join(parts)
        changes.append(msg)

        for label in labels:
            label_groups[label].append(msg)

    outp = []

    if with_sections:
        if head_version is not None and head_version.patch != 0 and not head_version.beta:
            # Add header for patch releases
            if not markdown:
                now = datetime.now()
                heading = format_heading(
                    f'Release {head_version} - {now:%B} {now.day}', False
                )
                outp.append(heading)
        else:
            # For non-patch releases, insert header groups
            for label, title in LABEL_HEADERS.items():
                prs = label_groups[label]
                if not prs:
                    continue

                heading = format_heading(title, markdown)
                outp.append(heading)

                outp.extend(prs)
                # add newline
                outp.append('')

            heading = format_heading('All changes', markdown)
            outp.append(heading)

    outp.extend(changes)
    outp.append('')
    return '\n'.join(outp)
