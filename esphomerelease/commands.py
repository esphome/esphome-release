import glob
from typing import List

import click
from github3.repos import Repository

from . import changelog, cutting
from .config import CONFIG
from .docs import gen_supporters
from .github import get_session
from .model import Branch, Version
from .project import EsphomeDocsProject, EsphomeHassioProject, EsphomeProject
from .util import confirm, copy_clipboard, gprint


@click.group()
@click.option(
    "--step/--no-step", default=False, help="Prompt before each command is executed."
)
def cli(step):
    CONFIG["step"] = step


@cli.command(help="Cut a release.")
@click.argument("version")
def cut_release(version):
    version = Version.parse(version)
    if version.beta:
        cutting.cut_beta_release(version)
    else:
        cutting.cut_release(version)


@cli.command(help="Publish a release.")
@click.argument("version")
def publish_release(version):
    version = Version.parse(version)
    if version.beta:
        cutting.publish_beta_release(version)
    else:
        cutting.publish_release(version)


@cli.command(help="Reset branches to their upstream versions.")
def reset():
    if click.confirm("Reset esphome/dev ?"):
        EsphomeProject.reset_hard_remote("dev")
    if click.confirm("Reset esphome/release ?"):
        EsphomeProject.reset_hard_remote("release")
    if click.confirm("Reset esphome/beta ?"):
        EsphomeProject.reset_hard_remote("beta")

    if click.confirm("Reset esphome-docs/current ?"):
        EsphomeDocsProject.reset_hard_remote("current")
    if click.confirm("Reset esphome-docs/next ?"):
        EsphomeDocsProject.reset_hard_remote("next")
    if click.confirm("Reset esphome-docs/beta ?"):
        EsphomeDocsProject.reset_hard_remote("beta")

    if click.confirm("Reset esphome-hassio/main ?"):
        EsphomeHassioProject.reset_hard_remote("main")


@cli.command(help="Generate release notes.")
@click.option(
    "--markdown", is_flag=True, default=False, help="Use markdown instead of RST."
)
@click.option("--with-sections/--without-sections", help="Add sections", default=False)
@click.option(
    "--include-author/--dont-include-author",
    is_flag=True,
    help="Include author mentions",
    default=True,
)
@click.option("--base-ref", default=None, help="Base version")
@click.option("--head-ref", default=None, help="Head version")
@click.option("--head-version", default=None, help="Head version")
def release_notes(
    markdown, with_sections, include_author, base_ref, head_ref, head_version
):
    if base_ref is None:
        base_str = click.prompt(
            "Please enter base version", default=str(EsphomeProject.latest_release())
        )
    else:
        base_str = base_ref

    base_version = Version.parse(base_str)
    base_ref = f"{base_str}"

    default_head_version = None
    if head_ref is None:
        head_str = click.prompt(
            "Please enter head ref (dev/beta/stable)", default="dev"
        )
    else:
        head_str = head_ref

    if head_str == "dev":
        head_ref = Branch.DEV
        default_head_version = base_version.next_dev_version
    elif head_str == "beta":
        head_ref = Branch.BETA
        default_head_version = base_version.next_beta_version
    elif head_str in ["stable", "release"]:
        head_ref = Branch.STABLE
        default_head_version = base_version.next_patch_version
    else:
        head_ref = f"{head_str}"
        default_head_version = Version.parse(head_str)

    if head_version is None:
        head_version_str = click.prompt(
            "Please enter head version", default=str(default_head_version)
        )
    else:
        head_version_str = head_version
    head_version = Version.parse(head_version_str)

    text = changelog.generate(
        project=EsphomeProject,
        base=base_ref,
        base_version=base_version,
        head=head_ref,
        head_version=head_version,
        prerelease=head_version.beta > 0,
        markdown=markdown,
        with_sections=with_sections,
        include_author=include_author,
    )

    from sys import platform

    if platform == "darwin":
        copy_clipboard(text)
        gprint("Changelog has been copied to your clipboard. Please paste it in.")
    else:
        # Alternative where pbcopy does not work
        gprint("Start Changelog:")
        print(text)
        gprint("End Changelog, Please copy and paste changelog")


@cli.command(help="Cherry-pick from milestone")
@click.argument("milestone")
def milestone_cherry_pick(milestone):
    for proj in [EsphomeProject, EsphomeDocsProject]:
        milestone_obj = proj.get_milestone_by_title(milestone)
        if milestone_obj is None:
            confirm(f"Couldn't find milestone {milestone} for project {proj.name}")
            continue
        if not click.confirm(
            f"Cherry-pick commits for {proj.name} on current branch?", default=True
        ):
            continue
        ret = proj.cherry_pick_from_milestone(milestone_obj)
        if click.confirm("Label picked commits as cherry-picked?", default=True):
            proj.mark_pulls_cherry_picked(ret)


def count_file(fname):
    i = 0
    with open(fname) as f:
        for i, _ in enumerate(f):
            pass
    return i + 1


def count_folder(path, mask):
    count = 0
    for fname in glob.glob(str(path / "**" / mask), recursive=True):
        count += count_file(fname)
    return count


@cli.command(help="Count the number of lines.")
def count_lines():
    cpp = count_folder(EsphomeProject.path / "esphome", "*.cpp")
    gprint("Esphome .cpp: {}", cpp)
    h = count_folder(EsphomeProject.path / "esphome", "*.h")
    gprint("Esphome .h: {}", h)
    tcc = count_folder(EsphomeProject.path / "esphome", "*.tcc")
    gprint("Esphome .tcc: {}", tcc)
    py = count_folder(EsphomeProject.path / "esphome", "*.py")
    gprint("Esphome .py: {}", py)
    yaml_rst = count_folder(EsphomeDocsProject.path, "*.rst")
    gprint("Esphomedocs .rst: {}", yaml_rst)

    total = cpp + h + tcc + py + yaml_rst
    gprint("Total: {}", total)


@cli.command(help="Create labels")
def labels():
    components_folder = EsphomeProject.path / "esphome" / "components"
    found_labels = []
    for child in components_folder.iterdir():
        if not child.is_dir():
            continue
        init_file = child / "__init__.py"
        if not init_file.is_file():
            # print(f"No __init__: {child}")
            continue

        integration_name = child.stem
        found_labels.append(f"integration: {integration_name}")

    found_labels.sort()
    # print('\n'.join(found_labels))

    sess = get_session()
    repos: List[Repository] = [
        sess.repository("esphome", "issues"),
        sess.repository("esphome", "feature-requests"),
        sess.repository("esphome", "esphome"),
        sess.repository("esphome", "esphome-docs"),
    ]
    for repo in repos:
        has_labels = [label.name for label in repo.labels()]
        for label in found_labels:
            found = any(x.lower() == label.lower() for x in has_labels)
            if found:
                continue
            print(f"Create label '{label}' in {repo.name}")
            repo.create_label(label, "ededed")


@cli.command(help="Generate Supporters.")
def supporters():
    gen_supporters()
