"""Logic for cutting releases."""
import click

from .model import Version, Branch, BranchType
from .project import Project, EsphomeProject, EsphomeDocsProject, EsphomeHassioProject, EsphomeIssuesProject
from .util import update_local_copies, gprint, copy_clipboard, open_vscode, random_quote, confirm
from .exceptions import EsphomeReleaseError
from . import changelog


def _bump_branch_name(version: Version) -> str:
    return f'bump-{version}'


def _strategy_merge(project: Project, version: Version, *, base: Branch, head: Branch):
    branch_name = _bump_branch_name(version)

    project.checkout(base)
    project.checkout_new_branch(branch_name)
    project.merge(head, strategy_option='theirs')
    project.bump_version(version)


def _strategy_cherry_pick(project: Project, version: Version, *, base: Branch):
    branch_name = _bump_branch_name(version)
    milestone = project.get_milestone_by_title(str(version))

    project.checkout(base)
    project.checkout_new_branch(branch_name)
    ret = project.cherry_pick_from_milestone(milestone)
    project.bump_version(version)
    return ret


def _create_prs(*, version: Version, base: Version, target_branch: BranchType):
    branch_name = _bump_branch_name(version)
    changelog_md = changelog.generate(
        base=f'v{base}', base_version=base,
        head=branch_name, head_version=version,
        markdown=True, with_sections=False,
        # Don't include author to not spam everybody for release PRs
        include_author=False
    )

    for proj in [EsphomeProject, EsphomeDocsProject]:
        body = "**Remember to use merge commit, not squash**\n" + random_quote() + changelog_md
        with proj.workon(branch_name):
            proj.create_pr(
                title=str(version),
                target_branch=target_branch,
                body=body
            )


def _update_milestones(*, version: Version, next_version: Version):
    for proj in [EsphomeProject, EsphomeDocsProject, EsphomeIssuesProject]:
        proj.create_milestone(str(next_version))

        old_milestone = proj.get_milestone_by_title(str(version))
        if old_milestone is not None:
            old_milestone.update(state='closed')


def _mark_cherry_picked(cherry_picked):
    for picked in cherry_picked:
        picked.add_labels('cherry-picked')


def _promt_base_version() -> Version:
    base_str = click.prompt("Please enter base (what release to compare with for changelog)",
                            default=str(EsphomeProject.latest_release()))
    return Version.parse(base_str)


def _docs_insert_changelog(*, version: Version, base: Version):
    branch_name = _bump_branch_name(version)
    with EsphomeDocsProject.workon(branch_name):
        changelog_rst = changelog.generate(
            base=f'v{base}', base_version=base,
            head=branch_name, head_version=version,
            markdown=False, with_sections=version.beta == 1
        )
        copy_clipboard(changelog_rst)
        changelog_version = version.replace(patch=0, beta=0, dev=False)
        changelog_path = EsphomeDocsProject.path / "changelog" / f"v{changelog_version}.rst"
        gprint("Changelog has been copied to your clipboard. Please paste it in.")
        open_vscode(str(changelog_path))
        confirm("Pasted changelog?")
        EsphomeDocsProject.commit(f'Update changelog for {version}')


def cut_beta_release(version: Version):
    if not version.beta:
        raise EsphomeReleaseError('Must be beta release!')

    base = _promt_base_version()
    update_local_copies()

    # Commits that were cherry-picked
    cherry_picked = []

    if version.beta == 1:
        gprint("Creating first beta version using merge")
        dev_str = click.prompt("Please enter next dev version (what will be seen on dev branches after release)",
                               default=str(version.next_dev_version))
        dev = Version.parse(dev_str)

        for proj in [EsphomeProject, EsphomeDocsProject]:
            _strategy_merge(proj, version, base=Branch.BETA, head=Branch.DEV)

            gprint(f"Updating dev version number to {dev}")
            with proj.workon(Branch.DEV):
                proj.bump_version(dev)
    else:
        gprint("Creating next beta version using cherry-pick")
        for proj in [EsphomeProject, EsphomeDocsProject]:
            cherry_picked.extend(
                _strategy_cherry_pick(proj, version, base=Branch.BETA)
            )
    _docs_insert_changelog(version=version, base=base)

    _confirm_correct()
    _create_prs(version=version, base=base, target_branch=Branch.BETA)
    _update_milestones(version=version, next_version=version.next_beta_version)
    _mark_cherry_picked(cherry_picked)

    if version.beta == 1:
        for proj in [EsphomeProject, EsphomeDocsProject]:
            with proj.workon(Branch.DEV):
                proj.push()


def cut_release(version: Version):
    if version.beta or version.dev:
        raise EsphomeReleaseError('Must be full release!')

    base = _promt_base_version()
    update_local_copies()

    # Commits that were cherry-picked
    cherry_picked = []

    if version.patch == 0:
        gprint("Creating first release version using merge")
        for proj in [EsphomeProject, EsphomeDocsProject]:
            _strategy_merge(proj, version, base=Branch.STABLE, head=Branch.BETA)
    else:
        gprint("Creating next full release using cherry-pick")
        for proj in [EsphomeProject, EsphomeDocsProject]:
            cherry_picked.extend(
                _strategy_cherry_pick(proj, version, base=Branch.STABLE)
            )
    _docs_insert_changelog(version=version, base=base)

    _confirm_correct()
    _create_prs(version=version, base=base, target_branch=Branch.STABLE)
    _update_milestones(version=version, next_version=version.next_patch_version)
    _mark_cherry_picked(cherry_picked)


def _publish_release(*, version: Version, base: Version, head_branch: BranchType, prerelease: bool):
    update_local_copies()
    confirm(f"Please make sure the {version} PRs have been merged")
    changelog_md = changelog.generate(
        base=f'v{base}', base_version=base,
        head=head_branch, head_version=version,
        markdown=True, with_sections=False
    )
    confirm(f"Publish version {version}?")
    for proj in [EsphomeProject, EsphomeDocsProject]:
        with proj.workon(head_branch):
            proj.create_release(version, prerelease=prerelease, body=changelog_md)

    EsphomeHassioProject.bump_version(version)
    EsphomeHassioProject.create_release(version, prerelease=prerelease)


def publish_beta_release(version: Version):
    if not version.beta:
        raise EsphomeReleaseError('Must be beta release!')

    base = _promt_base_version()
    _publish_release(
        version=version, base=base,
        head_branch=Branch.BETA, prerelease=True
    )


def publish_release(version: Version):
    if version.beta or version.dev:
        raise EsphomeReleaseError('Must be full release!')

    base = _promt_base_version()
    _publish_release(
        version=version, base=base,
        head_branch=Branch.STABLE, prerelease=False
    )


def _confirm_correct():
    confirm(click.style("Please confirm everything is correct", fg='red'))
