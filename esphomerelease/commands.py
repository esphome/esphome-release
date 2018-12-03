from distutils.version import StrictVersion
import glob
import re

import click

from esphomerelease.git import execute_command
from . import changelog, git, github, model
from .util import EsphomeReleaseError, EsphomedocsProject, EsphomelibProject, EsphomeyamlProject, \
    copy_clipboard, gprint


@click.group()
def cli():
    pass


VERSION_REGEX = r'[\d\.\ab\-dev]+'


def test_changes():
    execute_command('./lib-test.sh', live=True)
    execute_command('./yaml-test.sh', live=True)
    execute_command('./docs-test.sh', live=True)
    if not click.confirm(click.style("Please check everything worked!", fg='yellow')):
        raise EsphomeReleaseError


def esphomelib_replace(version):
    gprint('lib: branch {} -> {}', EsphomelibProject.branch, version)
    EsphomelibProject.replace_file_content(
        'library.json',
        r'^(  \"version\":\s*\")' + VERSION_REGEX,
        r'\g<1>{}'.format(version)
    )
    EsphomelibProject.replace_file_content(
        'library.properties',
        r'(version=)[\d\.ab\-dev]+',
        r'\g<1>{}'.format(version)
    )
    EsphomelibProject.replace_file_content(
        'src/esphomelib/defines.h',
        r'(#define\s+ESPHOMELIB_VERSION\s+")' + VERSION_REGEX,
        r'\g<1>{}'.format(version)
    )

    confirm_replace(EsphomelibProject)
    EsphomelibProject.commit('Bump version to v{}'.format(version), ignore_empty=True)


def esphomeyaml_replace(version):
    gprint('yaml: branch {} -> {}', EsphomeyamlProject.branch, version)
    version_ = StrictVersion(version)
    EsphomeyamlProject.replace_file_content(
        'esphomeyaml/const.py',
        r'(MAJOR_VERSION\s*=\s*)\d+',
        r'\g<1>{}'.format(version_.version[0]),
    )
    EsphomeyamlProject.replace_file_content(
        'esphomeyaml/const.py',
        r'(MINOR_VERSION\s*=\s*)\d+',
        r'\g<1>{}'.format(version_.version[1]),
    )
    patch_ = str(version_.version[2])
    if version_.prerelease is not None:
        patch_ += version_.prerelease[0] + str(version_.prerelease[1])
    EsphomeyamlProject.replace_file_content(
        'esphomeyaml/const.py',
        r'(PATCH_VERSION\s*=\s*\')\d+[abdev\-\d]*',
        r'\g<1>{}'.format(patch_),
    )
    EsphomeyamlProject.replace_file_content(
        'esphomeyaml/const.py',
        r'^(ESPHOMELIB_VERSION\s*=\s*\')' + VERSION_REGEX,
        r'\g<1>{}'.format(version),
    )

    confirm_replace(EsphomeyamlProject)
    EsphomeyamlProject.commit('Bump version to v{}'.format(version), ignore_empty=True)


def esphomeyaml_replace_beta(version):
    gprint('yaml (BETA): {} -> {}', EsphomeyamlProject.branch, version)
    EsphomeyamlProject.replace_file_content(
        'esphomeyaml-beta/config.json',
        r'(\s+\"version\":\s*\")[\d\.b]+',
        r'\g<1>{}'.format(version)
    )
    confirm_replace(EsphomeyamlProject)
    EsphomeyamlProject.commit('Bump beta version to v{}'.format(version), ignore_empty=True)


def esphomeyaml_replace_release(version):
    gprint('yaml (RELEASE): branch {} -> {}', EsphomeyamlProject.branch, version)
    for k in ('', '-beta'):
        EsphomeyamlProject.replace_file_content(
            f'esphomeyaml{k}/config.json',
            r'(\s+\"version\":\s*\")[\d\.b]+',
            r'\g<1>{}'.format(version)
        )
    confirm_replace(EsphomeyamlProject)
    EsphomeyamlProject.commit('Bump HassIO version to v{}'.format(version), ignore_empty=True)


def esphomedocs_replace(version, release):
    gprint('docs: branch {} -> {}', EsphomedocsProject.branch, version)

    text = changelog.generate(release, markdown=False)
    copy_clipboard(text)
    gprint("Changelog has been copied to your clipboard!")
    click.edit(filename=str(EsphomedocsProject.get_path() / "esphomeyaml" / "changelog" /
                            "index.rst"))
    if not click.confirm(click.style("Please insert the changelog in the docs!", fg='yellow')):
        raise EsphomeReleaseError

    version_ = StrictVersion(version)
    EsphomedocsProject.replace_file_content(
        'Makefile',
        r'(ESPHOMELIB_TAG\s*=\s*v)' + VERSION_REGEX,
        r'\g<1>{}'.format(version)
    )
    EsphomedocsProject.replace_file_content(
        'Doxygen',
        r'^(PROJECT_NUMBER\s*=\s*)' + VERSION_REGEX,
        r'\g<1>{}'.format(version)
    )
    EsphomedocsProject.replace_file_content(
        'conf.py',
        r'(version\s*=\s*\')[\d\.]+',
        r'\g<1>{}.{}'.format(version_.version[0], version_.version[1])
    )
    EsphomedocsProject.replace_file_content(
        'conf.py',
        r'(release\s*=\s*\')' + VERSION_REGEX,
        r'\g<1>{}'.format(version)
    )
    with open(EsphomedocsProject.get_path() / '_static' / 'version', 'w') as f:
        f.write(str(version))

    confirm_replace(EsphomedocsProject)
    EsphomedocsProject.commit('Bump version to v{}'.format(version), ignore_empty=True)


def confirm_replace(prj):
    gprint("=============== DIFF START ===============")
    prj.run_git('add', '.')
    prj.run_git('diff', '--color', '--cached', show=True)
    if not click.confirm(click.style("==== Please verify the diff is correct ====", fg='green')):
        raise EsphomeReleaseError


def update_local_copies():
    gprint("Updating local repo copies")
    EsphomelibProject.checkout_pull('master', remote='origin')
    EsphomelibProject.checkout_pull('dev', remote='origin')
    EsphomelibProject.checkout_pull('rc', remote='origin')

    EsphomeyamlProject.checkout_pull('master', remote='origin')
    EsphomeyamlProject.checkout_pull('dev', remote='origin')
    EsphomeyamlProject.checkout_pull('rc', remote='origin')

    EsphomedocsProject.checkout_pull('current', remote='origin')
    EsphomedocsProject.checkout_pull('next', remote='origin')
    EsphomedocsProject.checkout_pull('rc', remote='origin')


def checkout_dev():
    gprint("Checking out dev again...")
    EsphomelibProject.checkout('dev')
    EsphomeyamlProject.checkout('dev')
    EsphomedocsProject.checkout('next')


@cli.command(help="Create a beta release.")
@click.argument('version')
@click.option('--base')
@click.option('--dev')
@click.option('--from-dev/--not-from-dev', default=False)
def beta_release(version, base, dev, from_dev):
    try:
        beta_release_impl_(version, base, dev, from_dev)
    except EsphomeReleaseError as err:
        gprint(str(err), fg='red')


@cli.command(help="Create a full release.")
@click.argument('version')
@click.option('--base')
def release(version, base):
    try:
        release_impl_(version, base)
    except EsphomeReleaseError as err:
        gprint(str(err), fg='red')


def release_impl_(version, base):
    version_ = StrictVersion(version)
    if version_.prerelease is not None:
        raise EsphomeReleaseError('Must be full release!')

    if base is None:
        base = EsphomelibProject.latest_release().tag_name[1:]
        base = click.prompt("Please enter base", default=base)

    rel = model.Release(version, base, 'master')
    update_local_copies()

    target = ('master', 'master', 'current')

    if version_.version[2] == 0:
        gprint("Creating new full-release version")

        base = ('rc', 'rc', 'rc')
        update_using_merge(target, base, version, rel)
        esphomeyaml_update_stable(version)

        confirm_correct()

        md_text = changelog.generate(rel, markdown=True)
        post_normal(target, version, False, md_text)
    else:
        gprint("Creating new full-release patch version")

        dat = update_using_milestone(target, version, rel)
        esphomeyaml_update_stable(version)

        confirm_correct()

        md_text = changelog.generate(rel, markdown=True)
        post_milestone(target, dat, False, md_text, version)

    checkout_dev()


def esphomeyaml_update_stable(version):
    gprint("yaml: Updating HassIO stable version number on dev to {}".format(version))
    with EsphomeyamlProject.workon('dev'):
        esphomeyaml_replace_release(version)
    with EsphomeyamlProject.workon('master'):
        esphomeyaml_replace_release(version)


def post_milestone(target, dat, prerelease, body, version):
    gprint("==== esphomelib POST ====")
    with EsphomelibProject.workon(target[0]):
        EsphomelibProject.create_release(version, body=body, prerelease=prerelease)
        EsphomelibProject.push(remote='gitlab', tags=True)
        EsphomelibProject.push(remote='gitlab')
        EsphomelibProject.mark_pulls_cherry_picked(dat[0][0], dat[0][1])
    gprint("==== esphomedocs POST ====")
    with EsphomedocsProject.workon(target[2]):
        EsphomedocsProject.mark_pulls_cherry_picked(dat[1][0], dat[1][1])
        EsphomedocsProject.create_release(version, body=body, prerelease=prerelease)
        EsphomedocsProject.push(remote='gitlab', tags=True)
        EsphomedocsProject.push(remote='gitlab')
        EsphomedocsProject.push(remote='origin')
    gprint("==== esphomeyaml POST ====")
    with EsphomeyamlProject.workon(target[1]):
        EsphomeyamlProject.create_release(version, body=body, prerelease=prerelease)
        EsphomeyamlProject.mark_pulls_cherry_picked(dat[1][0], dat[1][1])
        EsphomeyamlProject.push(remote='gitlab', tags=True)
        EsphomeyamlProject.push(remote='gitlab')
    wait_gitlab_esphomeyaml()
    with EsphomeyamlProject.workon('dev'):
        EsphomeyamlProject.push()


def update_using_milestone(target, version, rel):
    gprint("==== esphomelib ====")
    with EsphomelibProject.workon(target[0]):
        lib_milestone = EsphomelibProject.get_milestone_by_title(version)
        lib_to_pick = EsphomelibProject.cherry_pick_from_milestone(lib_milestone)
        esphomelib_replace(version)
    gprint("==== esphomeyaml ====")
    with EsphomeyamlProject.workon(target[1]):
        yaml_milestone = EsphomeyamlProject.get_milestone_by_title(version)
        yaml_to_pick = EsphomeyamlProject.cherry_pick_from_milestone(yaml_milestone)
        esphomeyaml_replace(version)
    gprint("==== esphomedocs ====")
    with EsphomedocsProject.workon(target[2]):
        docs_milestone = EsphomedocsProject.get_milestone_by_title(version)
        docs_to_pick = EsphomedocsProject.cherry_pick_from_milestone(docs_milestone)
        esphomedocs_replace(version, rel)
    return [(lib_milestone, lib_to_pick), (yaml_milestone, yaml_to_pick),
           (docs_milestone, docs_to_pick)]


def update_using_merge(target, base, version, rel):
    gprint("==== esphomelib ====")
    with EsphomelibProject.workon(target[0]):
        gprint("lib: merging {} into {}".format(base[0], target[0]))
        EsphomelibProject.merge(base[0])
        esphomelib_replace(version)
    gprint("==== esphomeyaml ====")
    with EsphomeyamlProject.workon(target[1]):
        gprint("yaml: merging {} into {}".format(base[1], target[1]))
        EsphomeyamlProject.merge(base[1])
        esphomeyaml_replace(version)
    gprint("==== esphomedocs ====")
    with EsphomedocsProject.workon(target[2]):
        gprint("docs: merging {} into {}".format(base[2], target[2]))
        EsphomedocsProject.merge(base[2])
        esphomedocs_replace(version, rel)


def post_normal(target, version, prerelease, body):
    gprint("==== esphomelib POST ====")
    with EsphomelibProject.workon(target[0]):
        EsphomelibProject.create_release(version, prerelease=prerelease, body=body)
        EsphomelibProject.push(remote='gitlab', tags=True)
        EsphomelibProject.push(remote='gitlab')
    with EsphomelibProject.workon('dev'):
        EsphomelibProject.push()
    gprint("==== esphomedocs POST ====")
    with EsphomedocsProject.workon(target[2]):
        EsphomedocsProject.create_release(version, prerelease=prerelease, body=body)
        EsphomedocsProject.push(remote='gitlab', tags=True)
        EsphomedocsProject.push(remote='gitlab')
        EsphomedocsProject.push(remote='origin')
    gprint("==== esphomeyaml POST ====")
    with EsphomeyamlProject.workon(target[1]):
        EsphomeyamlProject.create_release(version, prerelease=prerelease, body=body)
        EsphomeyamlProject.push(remote='gitlab', tags=True)
        EsphomeyamlProject.push(remote='gitlab')
    wait_gitlab_esphomeyaml()
    with EsphomeyamlProject.workon('dev'):
        EsphomeyamlProject.push()


def confirm_correct():
    test_changes()

    if not click.confirm(click.style("Please confirm everything is correct", fg='red')):
        raise EsphomeReleaseError


def wait_gitlab_esphomeyaml():
    with open('data/gitlab_url', 'r') as f:
        gitlab_url = f.read()
    click.launch(gitlab_url)
    if not click.confirm("Please wait for Gitlab CI to complete at {}".format(gitlab_url)):
        raise EsphomeReleaseError


def beta_release_impl_(version, base, dev, from_dev):
    version_ = StrictVersion(version)
    if version_.prerelease is None:
        raise EsphomeReleaseError('Must be beta release!')

    if dev is None:
        dev = '{}.{}.{}-dev'.format(version_.version[0], version_.version[1] + 1, 0)
        dev = click.prompt("Please enter dev", default=dev)

    if base is None:
        base = EsphomelibProject.latest_release().tag_name[1:]
        base = click.prompt("Please enter base", default=base)

    rel = model.Release(version, base, 'rc')
    update_local_copies()

    target = ('rc', 'rc', 'rc')

    if version_.prerelease[1] == 1 or from_dev:
        gprint("Creating new version pre-release")

        gprint("docs: merging current into next")
        with EsphomedocsProject.workon('next'):
            EsphomedocsProject.merge('current')

        base = ('dev', 'dev', 'next')
        update_using_merge(target, base, version, rel)

        gprint("lib: Updating dev version number to {}".format(dev))
        with EsphomelibProject.workon('dev'):
            esphomelib_replace(dev)
        esphomeyaml_update_beta(version)

        confirm_correct()

        post_normal(target, version, True, None)
    else:
        gprint("Creating pre-release fix bump")

        dat = update_using_milestone(target, version, rel)
        esphomeyaml_update_beta(version)

        confirm_correct()

        post_milestone(target, dat, True, None, version)

    with EsphomeyamlProject.workon('master'):
        EsphomeyamlProject.push()

    checkout_dev()


def esphomeyaml_update_beta(version):
    gprint("yaml: Updating HassIO beta version number on dev to {}".format(version))
    with EsphomeyamlProject.workon('dev'):
        esphomeyaml_replace_beta(version)
    with EsphomeyamlProject.workon('master'):
        esphomeyaml_replace_beta(version)


@cli.command(help='Generate release notes.')
@click.option('--source')
@click.option('--target')
@click.option('--release', default=None)
def release_notes(target, source, release):
    if release is None:
        release = git.get_esphomeyaml_version(target)
        print("Auto detected version", release)

    rel = model.Release(release, source, target)
    file_website = 'data/{}.rst'.format(rel.identifier)
    file_github = 'data/{}-github.md'.format(rel.identifier)

    for file, markdown in (file_website, False), (file_github, True):
        with open(file, 'wt') as outp:
            outp.write(changelog.generate(rel, markdown=markdown))

    input('Press enter to copy website changelog to clipboard')
    with open(file_website, 'rt') as file:
        copy_clipboard(file.read())

    input('Press enter to copy GitHub changelog to clipboard')
    with open(file_github, 'rt') as file:
        copy_clipboard(file.read())


@cli.command(help='Find unmerged documentation PRs.')
@click.option('--branch', default='rc')
@click.argument('release')
def unmerged_docs(branch, release):
    docs_pr_ptrn = re.compile('home-assistant/home-assistant.github.io#(\d+)')
    gh_session = github.get_session()
    repo = gh_session.repository('home-assistant', 'home-assistant')
    docs_repo = gh_session.repository('home-assistant', 'home-assistant.github.io')
    release = model.Release(release, branch=branch)
    prs = model.PRCache(repo)
    doc_prs = model.PRCache(docs_repo)

    for line in release.log_lines():
        if line.pr is None:
            continue

        pr = prs.get(line.pr)
        match = docs_pr_ptrn.search(pr.body_text)
        if not match:
            continue

        docs_pr = doc_prs.get(match.groups()[0])

        if docs_pr.state == 'closed':
            continue

        print(pr.title)
        print(docs_pr.html_url)
        print()


@cli.command(help='Test the current branches')
def test():
    test_changes()


def count_file(fname):
    i = 0
    with open(fname) as f:
        for i, _ in enumerate(f):
            pass
    return i + 1


def count_folder(path, mask):
    count = 0
    for fname in glob.glob(str(path / '**' / mask), recursive=True):
        count += count_file(fname)
    return count


@cli.command(help='Count the number of lines.')
def count_lines():

    cpp = count_folder(EsphomelibProject.path / 'src', '*.cpp')
    gprint("Esphomelib .cpp: {}", cpp)
    h = count_folder(EsphomelibProject.path / 'src', '*.h')
    gprint("Esphomelib .h: {}", h)
    tcc = count_folder(EsphomelibProject.path / 'src', '*.tcc')
    gprint("Esphomelib .tcc: {}", tcc)
    py = count_folder(EsphomeyamlProject.path / 'esphomeyaml', '*.py')
    gprint("Esphomeyaml .py: {}", py)
    yaml_rst = count_folder(EsphomedocsProject.path / 'esphomeyaml', '*.rst')
    gprint("Esphomedocs yaml .rst: {}", yaml_rst)
    api_rst = count_folder(EsphomedocsProject.path / 'api', '*.rst')
    gprint("Esphomedocs api .rst: {}", api_rst)

    total = cpp + h + tcc + py + yaml_rst + api_rst
    gprint("Total: {}", total)
