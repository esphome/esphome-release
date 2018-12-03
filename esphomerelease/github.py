from distutils.version import StrictVersion
import os
import sys

from github3 import GitHub
from github3.exceptions import GitHubError

from esphomerelease.util import EsphomeReleaseError


GITHUB_SESSION = None


def get_session():
    global GITHUB_SESSION

    if GITHUB_SESSION is not None:
        return GITHUB_SESSION

    if not os.path.isfile('data/gh_token'):
        raise EsphomeReleaseError('Please write a GitHub token to data/gh_token')

    with open('data/gh_token') as fd:
        token = fd.readline().strip()

    gh = GitHub(token=token)
    try:  # Test connection before starting
        gh.is_starred('github', 'gitignore')
    except GitHubError as exc:
        raise EsphomeReleaseError('Invalid token found')
    GITHUB_SESSION = gh
    return GITHUB_SESSION


def get_latest_version_milestone(repo):
    """Fetch milestone by title."""
    milestones = []

    for ms in repo.milestones(state='open'):
        try:
            milestones.append((StrictVersion(ms.title), ms))
        except ValueError:
            print('Found milestone with invalid version', ms.title)

    if not milestones:
        sys.stderr.write('No milestones found\n')
        sys.exit(1)

    return list(reversed(sorted(milestones)))[0][1]
