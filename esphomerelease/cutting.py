"""Logic for cutting releases."""

import datetime
import re
from pathlib import Path
from typing import NamedTuple

import click

from . import changelog, docs
from .changelog_url import (
    changelog_too_long,
    changelog_website_url,
    use_website_link_for_release,
)
from .exceptions import EsphomeReleaseError
from .model import Branch, BranchType, Version
from .project import EsphomeDocsProject, EsphomeProject, Project
from .util import (
    confirm,
    feature_freeze_date,
    gprint,
    milestone_due_on,
    open_vscode,
    release_date,
    update_local_copies,
)

METADATA_MD = """
<details>
<summary>Metadata</summary>

@coderabbitai ignore
</details>
"""


def _bump_branch_name(version: Version) -> str:
    return f"bump-{version}"


def _cycle_milestone_title(version: Version) -> str:
    """Title of the single milestone shared by a whole release cycle.

    Every beta and the final release of e.g. 2026.6.0 use the ``2026.6.0``
    milestone; patch releases keep their own (``2026.6.1`` etc.).
    """
    return str(version.replace(beta=0, dev=False))


def _check_open_milestone_prs(version: Version, *, block: bool):
    """Check for open PRs on the cycle milestone.

    Open PRs are always reported. When ``block`` is True (full releases) the
    user must clear the milestone or abort; for betas this only warns and
    continues.
    """
    milestone_title = _cycle_milestone_title(version)
    while True:
        open_prs = []
        for proj in [EsphomeProject, EsphomeDocsProject]:
            milestone = proj.get_milestone_by_title(milestone_title)
            if milestone is None:
                continue
            for pr in proj.get_open_prs_for_milestone(milestone):
                open_prs.append((proj, pr))

        if not open_prs:
            return

        gprint(click.style(
            f"Warning: Found {len(open_prs)} open PR(s) on the {milestone_title} milestone:",
            fg="yellow",
        ))
        for proj, pr in open_prs:
            gprint(f"  - [{proj.shortname}] #{pr.number}: {pr.title} ({pr.html_url})")

        if not block:
            return

        if not click.confirm(
            click.style("Check again?", fg="yellow"),
            default=True,
        ):
            raise EsphomeReleaseError("Aborted: open PRs on milestone")


def _strategy_merge(project: Project, version: Version, *, base: Branch, head: Branch):
    branch_name = _bump_branch_name(version)

    project.checkout(base)
    project.checkout_new_branch(branch_name)
    project.merge(head, strategy_option="theirs")
    project.bump_version(version)


def _strategy_cherry_pick(project: Project, version: Version, *, base: Branch):
    branch_name = _bump_branch_name(version)
    milestone = project.get_milestone_by_title(_cycle_milestone_title(version))

    project.checkout(base)
    project.checkout_new_branch(branch_name)
    ret = project.cherry_pick_from_milestone(milestone)
    project.bump_version(version)
    return ret


def _strategy_merge_then_cherry_pick(
    project: Project, version: Version, *, base: Branch, head: Branch
):
    """Merge ``head`` into a fresh bump branch, then cherry-pick milestone stragglers.

    Used for the first full release: the merge brings in everything already on
    the beta branch, and the cherry-pick catches milestone PRs that were merged
    but never cherry-picked into a beta (e.g. last-minute additions straight to
    the final release). PRs already in via a beta are skipped by their
    ``cherry-picked`` label.
    """
    branch_name = _bump_branch_name(version)
    milestone = project.get_milestone_by_title(_cycle_milestone_title(version))

    project.checkout(base)
    project.checkout_new_branch(branch_name)
    project.merge(head, strategy_option="theirs")
    ret = project.cherry_pick_from_milestone(milestone)
    project.bump_version(version)
    return ret


def _create_prs(*, version: Version, base: Version, target_branch: BranchType):
    branch_name = _bump_branch_name(version)

    for proj in [EsphomeProject, EsphomeDocsProject]:
        # For first beta or first main release, use link instead of generating changelog for EsphomeProject
        if use_website_link_for_release(
            version, is_primary_project=proj == EsphomeProject
        ):
            gprint(
                f"Using website link for {proj.shortname} changelog (first beta/main release)"
            )
            changelog_md = changelog_website_url(version)
        else:
            changelog_md = changelog.generate(
                project=proj,
                base=f"{base}",
                base_version=base,
                head=branch_name,
                head_version=version,
                prerelease=target_branch == Branch.BETA,
                gh_release=True,
                with_sections=False,
                # Don't include author to not spam everybody for release PRs
                include_author=False,
            )

            # If changelog is too long, replace with a link to website
            if changelog_too_long(changelog_md):
                gprint(
                    f"Changelog too long ({len(changelog_md)} chars), replacing with website link"
                )
                changelog_md = changelog_website_url(version)

        body = (
            "**Do not merge, release script will automatically merge**\n"
            + changelog_md
            + METADATA_MD
        )
        with proj.workon(branch_name):
            proj.create_pr(title=str(version), target_branch=target_branch, body=body)


def _ensure_cycle_milestone(version: Version):
    """Make sure the shared cycle milestone exists.

    The milestone is normally opened when the previous cycle's first beta is
    cut (see :func:`_open_next_cycle_milestone`). This idempotent guard runs at
    the first beta to cover the very first cycle or a milestone that went
    missing.
    """
    title = _cycle_milestone_title(version)
    for proj in [EsphomeProject, EsphomeDocsProject]:
        proj.ensure_milestone(title)


def _open_next_cycle_milestone(version: Version):
    """Open the ``.0`` cycle milestone for the month after ``version``.

    Runs as soon as the first beta is cut — from that moment ``dev`` is the
    next cycle, so PRs can immediately be marked for it (prioritized for
    review/merge). Its due date is set to the new-component/feature merge
    deadline (the Monday before the second Wednesday of that month).

    Idempotent: a milestone that already exists (e.g. created manually) is
    reused, with its due date corrected if it doesn't match.
    """
    next_year, next_month = _next_cycle_year_month(version)
    title = f"{next_year}.{next_month}.0"
    due_on = milestone_due_on(feature_freeze_date(next_year, next_month))
    for proj in [EsphomeProject, EsphomeDocsProject]:
        proj.ensure_milestone(title, due_on=due_on)


def _next_cycle_year_month(version: Version) -> tuple[int, int]:
    """Year and month of the cycle that follows ``version`` (handles December)."""
    year, month = version.major, version.minor + 1
    if month > 12:
        year, month = year + 1, 1
    return year, month


def _previous_cycle_year_month(version: Version) -> tuple[int, int]:
    """Year and month of the cycle before ``version`` (handles January)."""
    year, month = version.major, version.minor - 1
    if month < 1:
        year, month = year - 1, 12
    return year, month


def _clear_merged_prs_from_cycle_milestone(version: Version):
    """Drop already-merged PRs from the cycle milestone after the first beta.

    The first beta merges ``dev`` into ``beta``, so every merged milestone PR is
    now in the release. Removing them from the milestone keeps later beta cuts
    from cherry-picking them a second time; open PRs keep their milestone.
    """
    title = _cycle_milestone_title(version)
    for proj in [EsphomeProject, EsphomeDocsProject]:
        milestone = proj.get_milestone_by_title(title)
        for issue in proj.remove_merged_prs_from_milestone(milestone):
            gprint(f"Removed merged #{issue.number} from {title} ({proj.shortname})")


def _close_previous_month_patch_milestones(version: Version):
    """Close leftover open patch milestones from the previous month.

    When the first beta of a new cycle is cut, the previous month's patch line
    is done; close any of its patch milestones (e.g. ``2026.6.1``) still open —
    typically the unused "next patch" milestone left behind by the last patch
    release.
    """
    prev_year, prev_month = _previous_cycle_year_month(version)
    for proj in [EsphomeProject, EsphomeDocsProject]:
        for milestone in proj.get_open_milestones():
            try:
                ms_version = Version.parse(milestone.title)
            except ValueError:
                continue
            if (
                ms_version.major == prev_year
                and ms_version.minor == prev_month
                and ms_version.patch >= 1
                and ms_version.beta == 0
                and not ms_version.dev
            ):
                gprint(
                    f"Closing leftover patch milestone {milestone.title} for {proj.shortname}"
                )
                milestone.update(state="closed")


def _set_cycle_milestone_due(version: Version, due: datetime.date):
    """Set the due date on the cycle milestone across all projects."""
    title = _cycle_milestone_title(version)
    due_on = milestone_due_on(due)
    for proj in [EsphomeProject, EsphomeDocsProject]:
        milestone = proj.get_milestone_by_title(title)
        if milestone is not None:
            milestone.update(due_on=due_on)


def _close_cycle_milestone(*, version: Version, next_version: Version):
    """Close the current cycle milestone and open the next patch milestone.

    When a ``.0`` release goes out, also make sure the next month's ``.0``
    cycle milestone exists. It is normally opened when the first beta is cut
    (see :func:`_open_next_cycle_milestone`); this is just a safety net.
    """
    if version.patch == 0:
        _open_next_cycle_milestone(version)

    for proj in [EsphomeProject, EsphomeDocsProject]:
        proj.ensure_milestone(str(next_version))

        old_milestone = proj.get_milestone_by_title(_cycle_milestone_title(version))
        if old_milestone is not None:
            old_milestone.update(state="closed")


def _mark_cherry_picked(cherry_picked):
    for picked in cherry_picked:
        picked.add_labels("cherry-picked")


def _default_base_version(version: Version) -> Version:
    """Best guess of the previously published version to diff against.

    Derived from the version being released so no GitHub API pagination is
    needed: a later beta follows the previous beta, a patch release follows
    the previous patch. Only the first beta and the first full release of a
    cycle (where the previous stable patch number is unknowable) ask GitHub,
    and that is a single ``releases/latest`` request.
    """
    if version.beta > 1:
        return version.previous_beta_version
    if version.beta == 0 and version.patch > 0:
        return version.previous_patch_version
    return EsphomeProject.latest_release(include_prereleases=False)


def _prompt_base_version(version: Version) -> Version:
    base_str = click.prompt(
        "Please enter base (what release to compare with for changelog)",
        default=str(_default_base_version(version)),
    )
    return Version.parse(base_str)


BETA_NOTICE = (
    "> [!NOTE]\n"
    "> This is a beta release. Details on this page may change before the stable release is published."
)


def _with_beta_notice(content: str) -> str:
    """Return ``content`` with the beta notice inserted (no-op if present).

    The notice goes right after the frontmatter, before the ``import ...``
    lines, separated by blank lines. MDX hoists ESM imports, so content
    before an import is fine.
    """
    if BETA_NOTICE in content:
        return content
    lines = content.split("\n")
    insert_at = 0
    if lines[0] == "---":
        insert_at = lines.index("---", 1) + 1
    lines[insert_at:insert_at] = ["", *BETA_NOTICE.split("\n")]
    return "\n".join(lines)


def _without_beta_notice(content: str) -> str:
    """Return ``content`` with the beta notice removed (no-op if absent).

    The surrounding newlines are consumed so the neighbours keep a single
    blank line between them.
    """
    return content.replace(f"\n{BETA_NOTICE}\n", "", 1)


FULL_CHANGES_HEADING = "## Full list of changes"
BETA_CHANGES_START = "{/* BETA_CHANGES_START */}"
BETA_CHANGES_END = "{/* BETA_CHANGES_END */}"
ALL_CHANGES_END = "{/* ALL_CHANGES_END */}"
DEPENDENCY_CHANGES_END = "{/* DEPENDENCY_CHANGES_END */}"


class _DocsChange(NamedTuple):
    """One changelog line for the docs page."""

    labels: list[str]
    ref: str
    msg: str

    @property
    def is_dependency(self) -> bool:
        return any(label in self.labels for label in changelog.DEPENDENCY_LABELS)


def _docs_changes(*, version: Version, base: Version) -> list[_DocsChange]:
    """The formatted changelog entries since ``base``, oldest first."""
    entries = changelog.collect(
        project=EsphomeProject,
        base=f"{base}",
        base_version=base,
        head=_bump_branch_name(version),
        head_version=version,
    )
    return [
        _DocsChange(
            labels=labels,
            ref=f"[{EsphomeProject.shortname}#{pr.number}]",
            msg=changelog.format_change(project=EsphomeProject, pr=pr, labels=labels),
        )
        for pr, labels in entries
    ]


def _render_full_changes_block(changes: list[_DocsChange]) -> str:
    """The generated tail of a fresh changelog page, with merge markers.

    Mirrors the layout of :func:`changelog.generate` with sections, plus the
    marker comments later cuts use to merge new lines in: the Beta Changes
    block between the label sections and All changes, the end of the All
    changes list, and the end of the (headingless) dependency list.
    """
    out: list[str] = [
        "{/* markdownlint-disable MD013 */}",
        "",
        FULL_CHANGES_HEADING,
        "",
    ]
    for label, title in changelog.LABEL_HEADERS.items():
        if label == "cherry-picked":
            # Beta changes get their own marker-delimited block below.
            continue
        msgs = [c.msg for c in changes if label in c.labels]
        if not msgs:
            continue
        out += [f"### {title}", "", *msgs, ""]
    out += [
        BETA_CHANGES_START,
        BETA_CHANGES_END,
        "",
        "### All changes",
        "",
        "<details>",
        "<summary></summary>",
        "",
        *[c.msg for c in changes if not c.is_dependency],
        ALL_CHANGES_END,
        "",
        "</details>",
        "",
        "<details>",
        "<summary></summary>",
        "",
        *[c.msg for c in changes if c.is_dependency],
        DEPENDENCY_CHANGES_END,
        "",
        "</details>",
    ]
    return "\n".join(out) + "\n"


def _append_full_changes_block(content: str, changes: list[_DocsChange]) -> str:
    """Append the generated changes block to a page that has none yet."""
    return content.rstrip("\n") + "\n\n" + _render_full_changes_block(changes)


def _insert_patch_section(
    content: str, *, version: Version, changes: list[_DocsChange]
) -> str:
    """Insert a patch release section right before the full-changes list.

    Idempotent: a section for ``version`` that is already on the page is
    left alone.
    """
    if f"## Release {version} " in content:
        return content
    if FULL_CHANGES_HEADING not in content:
        raise EsphomeReleaseError(
            f"Cannot find '{FULL_CHANGES_HEADING}' in the changelog page"
        )
    now = datetime.datetime.now()
    section = "\n".join(
        [
            f"## Release {version} - {now:%B} {now.day}",
            "",
            "<details>",
            "<summary></summary>",
            "",
            *[c.msg for c in changes],
            "",
            "</details>",
            "",
            "",
        ]
    )
    return content.replace(FULL_CHANGES_HEADING, section + FULL_CHANGES_HEADING, 1)


def _merge_changes(content: str, changes: list[_DocsChange], *, beta: bool) -> str:
    """Merge new changelog lines into an existing page.

    Lines already on the page (matched by PR reference) are skipped, so the
    merge is idempotent. New non-dependency lines go into the Beta Changes
    block (created on demand; beta cuts only) and the All changes block;
    dependency lines go into the dependency block.
    """
    fresh = [c for c in changes if c.ref not in content]
    normal = [c.msg for c in fresh if not c.is_dependency]
    deps = [c.msg for c in fresh if c.is_dependency]

    def insert_before(marker: str, msgs: list[str], text: str) -> str:
        if marker not in text:
            raise EsphomeReleaseError(f"Cannot find '{marker}' in the changelog page")
        return text.replace(marker, "\n".join(msgs) + f"\n{marker}", 1)

    if normal and beta:
        empty_block = f"{BETA_CHANGES_START}\n{BETA_CHANGES_END}"
        if empty_block in content:
            content = content.replace(
                empty_block,
                f"{BETA_CHANGES_START}\n### Beta Changes\n\n{BETA_CHANGES_END}",
                1,
            )
        content = insert_before(BETA_CHANGES_END, normal, content)
    if normal:
        content = insert_before(ALL_CHANGES_END, normal, content)
    if deps:
        content = insert_before(DEPENDENCY_CHANGES_END, deps, content)
    return content


_BETA_CHANGES_BLOCK_RE = re.compile(
    re.escape(BETA_CHANGES_START) + r"(?s:.*?)" + re.escape(BETA_CHANGES_END) + r"\n\n?"
)


def _remove_beta_changes_block(content: str) -> str:
    """Drop the Beta Changes block at the stable release (no-op when absent)."""
    return _BETA_CHANGES_BLOCK_RE.sub("", content, count=1)


COMPONENTS_INDEX = "src/content/docs/components/index.mdx"

# An ImgTable row in the components index: ["Name", "/components/.../", ...],
_IMG_TABLE_ROW_RE = re.compile(r'^\["[^"]+"\s*,\s*"(?P<url>[^"]+)"')


def _new_component_table_lines(base: Version) -> list[str]:
    """ImgTable rows added to the components index since the ``base`` release.

    Diffs the docs components index between the ``base`` release tag and the
    checked-out bump branch: every ImgTable row added during the cycle is a
    freshly documented component, so it drafts the changelog's featured table.
    Rows are deduplicated by URL (one component can be listed in several
    categories) and rows that merely moved within the file are skipped. Edited
    rows (e.g. a rename) can still slip through — the MANUAL marker stays on
    the page so the block gets curated by hand.
    """
    try:
        diff = EsphomeDocsProject.run_git(
            "diff", f"{base}...HEAD", "--", COMPONENTS_INDEX, fail_ok=True
        ).decode()
    except EsphomeReleaseError:
        gprint(
            f"Could not diff {COMPONENTS_INDEX} against {base}, "
            "please fill in the featured components manually"
        )
        return []

    removed = {
        line[1:].strip()
        for line in diff.split("\n")
        if line.startswith("-") and not line.startswith("---")
    }
    seen: set[str] = set()
    rows: list[str] = []
    for line in diff.split("\n"):
        if not line.startswith("+"):
            continue
        row = line[1:].strip()
        match = _IMG_TABLE_ROW_RE.match(row)
        if match is None or row in removed or match.group("url") in seen:
            continue
        seen.add(match.group("url"))
        rows.append(row)
    return rows


def _render_changelog_page(changelog_version: Version, featured: list[str]) -> str:
    """The skeleton of a fresh changelog page: header, import, featured table."""
    month = f"{datetime.date(changelog_version.major, changelog_version.minor, 1):%B}"
    rows = "".join(f"  {row}\n" for row in featured)
    return (
        "---\n"
        f'description: "Changelog for ESPHome {changelog_version}."\n'
        f'title: "ESPHome {changelog_version} - {month} {changelog_version.major}"\n'
        "pagefind: false\n"
        f'slug: "changelog/{changelog_version}"\n'
        "---\n"
        "\n"
        'import ImgTable from "@components/ImgTable.astro";\n'
        "\n"
        "{/* MANUAL: Add featured components here */}\n"
        "<ImgTable items={[\n"
        f"{rows}"
        "]} />\n"
    )


def _changelog_page_path(version: Version) -> Path:
    """Path of the cycle's changelog page (always the ``.0`` stable name)."""
    changelog_version = version.replace(patch=0, beta=0, dev=False)
    return (
        EsphomeDocsProject.path
        / "src"
        / "content"
        / "docs"
        / "changelog"
        / f"{changelog_version}.mdx"
    )


def _ensure_changelog_page(*, version: Version, base: Version) -> bool:
    """Create the cycle's changelog page skeleton if it doesn't exist yet.

    Only the first beta actually creates the page; later betas, the stable
    release and patch releases find it already present and leave it alone.
    Returns whether the page was created.
    """
    path = _changelog_page_path(version)
    if path.exists():
        return False
    changelog_version = version.replace(patch=0, beta=0, dev=False)
    featured = _new_component_table_lines(base)
    path.write_text(_render_changelog_page(changelog_version, featured))
    return True


def _docs_insert_changelog(*, version: Version, base: Version):
    branch_name = _bump_branch_name(version)
    with EsphomeDocsProject.workon(branch_name):
        changelog_path = _changelog_page_path(version)
        if _ensure_changelog_page(version=version, base=base):
            gprint(f"Created changelog page {changelog_path.name}")

        content = changelog_path.read_text()
        changes = _docs_changes(version=version, base=base)

        if version.patch > 0 and not version.beta:
            content = _insert_patch_section(content, version=version, changes=changes)
        elif FULL_CHANGES_HEADING not in content:
            content = _append_full_changes_block(content, changes)
        else:
            content = _merge_changes(content, changes, beta=version.beta > 0)

        if version.beta:
            content = _with_beta_notice(content)
        else:
            content = _remove_beta_changes_block(content)
            content = _without_beta_notice(content)

        changelog_path.write_text(content)
        gprint(f"Changelog written to {changelog_path.name}")
        open_vscode(str(changelog_path))
        confirm("Does the changelog page look correct?")
        EsphomeDocsProject.commit(f"Update changelog for {version}")


def _docs_update_supporters(*, version: Version):
    branch_name = _bump_branch_name(version)
    gprint("Updating supporters")
    with EsphomeDocsProject.workon(branch_name):
        docs.gen_supporters()
        EsphomeDocsProject.commit(f"Update supporters for {version}", ignore_empty=True)


def _push_current_merge_branches():
    """Push the docs branches that received the ``current`` merge.

    :func:`update_local_copies` merges the docs ``current`` branch into ``next``
    and ``beta`` locally at the start of every cut. Push them once the cut has
    finished successfully so the merge lands on the remote.
    """
    for branch in (Branch.DEV, Branch.BETA):
        with EsphomeDocsProject.workon(branch):
            EsphomeDocsProject.push()


def cut_beta_release(version: Version):
    if not version.beta:
        raise EsphomeReleaseError("Must be beta release!")

    base = _prompt_base_version(version)
    _check_open_milestone_prs(version, block=False)
    update_local_copies()

    # Commits that were cherry-picked
    cherry_picked = []

    if version.beta == 1:
        gprint("Creating first beta version using merge")
        dev_str = click.prompt(
            "Please enter next dev version (what will be seen on dev branches after release)",
            default=str(version.next_dev_version),
        )
        dev = Version.parse(dev_str)

        for proj in [EsphomeProject, EsphomeDocsProject]:
            _strategy_merge(proj, version, base=Branch.BETA, head=Branch.DEV)

            gprint(f"Updating dev version number to {dev}")
            with proj.workon(Branch.DEV):
                proj.bump_version(dev)
    else:
        gprint("Creating next beta version using cherry-pick")
        for proj in [EsphomeProject, EsphomeDocsProject]:
            cherry_picked.extend(_strategy_cherry_pick(proj, version, base=Branch.BETA))
    _docs_insert_changelog(version=version, base=base)
    _docs_update_supporters(version=version)

    _confirm_correct()
    _create_prs(version=version, base=base, target_branch=Branch.BETA)
    _ensure_cycle_milestone(version)
    if version.beta == 1:
        # Beta is now being cut, so the milestone is due on release day: the
        # third Wednesday of the month.
        _set_cycle_milestone_due(version, release_date(version.major, version.minor))
        # dev is now the next cycle, so open its milestone right away.
        _open_next_cycle_milestone(version)
        # The previous month's patch line is done; close its leftover milestones.
        _close_previous_month_patch_milestones(version)
        # The merge already brought every merged milestone PR into the release;
        # drop them from the milestone so later betas don't re-cherry-pick them.
        _clear_merged_prs_from_cycle_milestone(version)
    _mark_cherry_picked(cherry_picked)

    if version.beta == 1:
        for proj in [EsphomeProject, EsphomeDocsProject]:
            with proj.workon(Branch.DEV):
                proj.push()

    _push_current_merge_branches()


def cut_release(version: Version):
    if version.beta or version.dev:
        raise EsphomeReleaseError("Must be full release!")

    base = _prompt_base_version(version)
    _check_open_milestone_prs(version, block=True)
    update_local_copies()

    # Commits that were cherry-picked
    cherry_picked = []

    if version.patch == 0:
        gprint("Creating first release version using merge + cherry-pick")
        for proj in [EsphomeProject, EsphomeDocsProject]:
            cherry_picked.extend(
                _strategy_merge_then_cherry_pick(
                    proj, version, base=Branch.STABLE, head=Branch.BETA
                )
            )
    else:
        gprint("Creating next full release using cherry-pick")
        for proj in [EsphomeProject, EsphomeDocsProject]:
            cherry_picked.extend(
                _strategy_cherry_pick(proj, version, base=Branch.STABLE)
            )
    _docs_insert_changelog(version=version, base=base)
    _docs_update_supporters(version=version)

    _confirm_correct()
    _create_prs(version=version, base=base, target_branch=Branch.STABLE)
    _close_cycle_milestone(version=version, next_version=version.next_patch_version)
    _mark_cherry_picked(cherry_picked)

    _push_current_merge_branches()


def _merge_release_pr(*, proj: Project, version: Version, head_branch: BranchType):
    prs = proj.get_pr_by_title(
        title=str(version), head=_bump_branch_name(version), base=head_branch
    )
    release_pr = None
    if not prs:
        confirm(
            f"No release PRs found for {proj.shortname}, please verify it has been merged."
        )
    elif len(prs) == 1:
        release_pr = prs[0]
    else:
        gprint("Found multiple release PRs. Please select the matchin one")
        for i, pr in enumerate(prs, start=1):
            gprint(f" [{i}] #{pr.number} by @{pr.user.login} ({pr.html_url})")
        gprint(f" [{len(prs) + 1}] Auto-merge none")
        num = (
            int(
                click.prompt(
                    f"Please select release PR for {proj.shortname}",
                    type=click.Choice([i + 1 for i in range(len(prs))]),
                )
            )
            - 1
        )
        release_pr = None if num == len(prs) else prs[num]

    if release_pr is not None and release_pr.state == "open":
        success = release_pr.merge(merge_method="merge")
        if not success:
            confirm("Merging failed, please check and confirm when ready")


def _publish_release(
    *,
    version: Version,
    base: Version,
    head_branch: BranchType,
    prerelease: bool,
    projects: list[Project],
):
    update_local_copies()
    confirm(f"Publish version {version}?")
    for proj in projects:
        # For first beta or first main release, use link instead of generating changelog for EsphomeProject
        if use_website_link_for_release(
            version, is_primary_project=proj == EsphomeProject
        ):
            gprint(
                f"Using website link for {proj.shortname} changelog (first beta/main release)"
            )
            changelog_md = changelog_website_url(version)
        else:
            changelog_md = changelog.generate(
                project=proj,
                base=f"{base}",
                base_version=base,
                head=_bump_branch_name(version),
                head_version=version,
                prerelease=prerelease,
                gh_release=True,
                with_sections=False,
            )

            # If changelog is too long, replace with a link to website
            if changelog_too_long(changelog_md):
                gprint(
                    f"Changelog too long ({len(changelog_md)} chars), replacing with website link"
                )
                changelog_md = changelog_website_url(version)

        _merge_release_pr(proj=proj, version=version, head_branch=head_branch)
        with proj.workon(head_branch):
            proj.pull()
            proj.create_release(version, prerelease=prerelease, body=changelog_md)


def publish_beta_release(version: Version, projects: list[Project]):
    if not version.beta:
        raise EsphomeReleaseError("Must be beta release!")

    base = _prompt_base_version(version)
    _publish_release(
        version=version,
        base=base,
        head_branch=Branch.BETA,
        prerelease=True,
        projects=projects,
    )
    for proj in projects:
        with proj.workon(Branch.DEV):
            proj.pull()
            proj.merge(Branch.BETA, "ours")
            proj.push()


def publish_release(version: Version, projects: list[Project]):
    if version.beta or version.dev:
        raise EsphomeReleaseError("Must be full release!")

    base = _prompt_base_version(version)
    _publish_release(
        version=version,
        base=base,
        head_branch=Branch.STABLE,
        prerelease=False,
        projects=projects,
    )
    for proj in projects:
        with proj.workon(Branch.BETA):
            proj.pull()
            proj.merge(Branch.STABLE, "ours")
            proj.push()
        with proj.workon(Branch.DEV):
            proj.pull()
            proj.merge(Branch.STABLE, "ours")
            proj.push()


def _confirm_correct():
    confirm(click.style("Please confirm everything is correct", fg="red"))
