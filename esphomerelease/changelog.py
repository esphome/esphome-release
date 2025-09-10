import functools
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

from github3.pulls import PullRequest

from .model import BranchType, Version
from .project import EsphomeDocsProject, EsphomeProject, Project
from .util import gprint, process_asynchronously

# Extra headers that are inserted in the changelog if
# one of these labels is applied
LABEL_HEADERS = {
    "new-feature": "New Features",
    "new-component": "New Components",
    "new-platform": "New Platforms",
    "breaking-change": "Breaking Changes",
    "cherry-picked": "Beta Changes",
    "notable-change": "Notable Changes",
}

LINE_LABELS = [
    "new-feature",
    "new-component",
    "new-platform",
    "breaking-change",
    "notable-change",
]

DEPENDENCY_LABELS = [
    "dependencies",
]


def format_heading(title: str, *, level: int = 2):
    c = level * "#"
    return f"{c} {title}\n"


def format_line(
    *, project: Project, pr: PullRequest, include_author: bool
) -> str:
    username = pr.user.login
    pr_link = f"[{project.shortname}#{pr.number}]({pr.html_url})"
    user_link = f"[@{username}]({pr.user.html_url})"

    line = f"- {pr.title} {pr_link}"
    if include_author:
        line += f" by {user_link}"
    return line


def generate(
    *,
    project: Project,
    base: BranchType,
    base_version: Version,
    head: BranchType,
    head_version: Version,
    prerelease: bool,
    gh_release: bool = False,
    with_sections: bool = True,
    include_author: bool = True,
):
    gprint("Generating changelog...")

    # Here we store the lines to insert for each label
    # Mapping from label to list of lines
    label_groups: Dict[str, List[str]] = defaultdict(list)

    # Create a list of all log lines in all relevant projects
    list_ = project.prs_between(base, head)

    lines: List[Tuple[PullRequest, List[str]]] = []

    def job(pr_number):
        pr: PullRequest = project.get_pr(pr_number)

        labels: List[str] = [label["name"] for label in pr.labels]

        # Filter out commits for which the PR has one of the ignored
        # labels ('reverted')
        if "reverted" in labels:
            return

        if "cherry-picked" in labels:
            try:
                milestone = pr.milestone["title"]
                pick_version = Version.parse(pr.milestone["title"])
                if pick_version <= base_version or pick_version > head_version:
                    # Not included in this release
                    return
            except ValueError:
                print(f"Could not parse milestone {milestone}")
                labels.remove("cherry-picked")
            except TypeError:
                print(f"PR {pr.number} has no milestone")

        lines.append((pr, labels))

    jobs = [functools.partial(job, pr) for pr in list_]
    gprint(f"Processing {len(jobs)} PRs")
    process_asynchronously(jobs, "Load PRs")

    # Sort log lines by when the PR was merged
    lines.sort(key=lambda x: x[0].merged_at)

    # A list of strings containing all serialized changes
    changes: List[str] = []

    # Now go through the lines struct and serialize them
    for pr, labels in lines:
        parts = [
            format_line(
                project=project, pr=pr, include_author=include_author
            )
        ]
        parts += [f"({label})" for label in labels if label in LINE_LABELS]

        msg = " ".join(parts)

        if not with_sections or not any(label in labels for label in DEPENDENCY_LABELS):
            changes.append(msg)

        for label in labels:
            label_groups[label].append(msg)

    outp = []

    if with_sections:
        if (
            head_version is not None
            and head_version.patch != 0
            and not head_version.beta
        ):
            # Add header for patch releases
            if not gh_release:
                now = datetime.now()
                heading = format_heading(
                    f"Release {head_version} - {now:%B} {now.day}"
                )
                outp.append(heading)
        else:
            heading = format_heading("Full list of changes")
            outp.append(heading)
            # For non-patch releases, insert header groups
            for label, title in LABEL_HEADERS.items():
                if not prerelease and title == "Beta Changes":
                    continue  # Skip beta changes for non-prerelease
                prs = label_groups[label]
                if not prs:
                    continue

                heading = format_heading(title, level=3)
                outp.append(heading)

                outp.extend(prs)
                # add newline
                outp.append("")

            heading = format_heading("All changes", level=3)
            outp.append(heading)

    if with_sections and not gh_release:
        outp.append("<details>")
        outp.append("<summary></summary>")
        outp.append("")
        outp.extend(changes)
        outp.append("")
        outp.append("</details>")
    else:
        outp.extend(changes)
    outp.append("")

    if with_sections:
        depdendency_prs = [
            pr
            for label, prs in label_groups.items()
            if label in DEPENDENCY_LABELS
            for pr in prs
        ]
        if depdendency_prs:
            heading = format_heading("Dependency Changes", level=3)
            outp.append("<details>")
            outp.append("<summary></summary>")
            outp.append("")
            outp.extend(depdendency_prs)
            outp.append("")
            outp.append("</details>")

    return "\n".join(outp)
