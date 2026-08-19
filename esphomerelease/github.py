import subprocess
from datetime import datetime

import github3.session
from github3 import GitHub

from .config import CONFIG
from .exceptions import EsphomeReleaseError


GITHUB_SESSION = None
GITHUB_TOKEN: str | None = None

# The gh OAuth token needs the `repo` scope to create releases and pull
# requests. Without it the API answers 403 instead of anything descriptive.
SCOPE_HINT = (
    "Make sure the token carries the `repo` scope, adding it with "
    "`gh auth refresh -s repo` if needed."
)


def _token_from_gh_cli() -> str:
    """Return the OAuth token the GitHub CLI has stored.

    Raises EsphomeReleaseError with an actionable message when gh is missing,
    unauthenticated, or hands back nothing.
    """
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise EsphomeReleaseError(
            "The GitHub CLI (gh) is not installed, so no GitHub token could be "
            "resolved. Install it from https://cli.github.com/ and run "
            f"`gh auth login`. {SCOPE_HINT}"
        ) from None

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        suffix = f" ({detail})" if detail else ""
        raise EsphomeReleaseError(
            f"`gh auth token` failed{suffix}. Run `gh auth login` to "
            f"authenticate the GitHub CLI. {SCOPE_HINT}"
        )

    token = proc.stdout.strip()
    if not token:
        raise EsphomeReleaseError(
            "`gh auth token` returned an empty token. Run `gh auth login` to "
            f"authenticate the GitHub CLI. {SCOPE_HINT}"
        )
    return token


def get_token() -> str:
    """Return the GitHub token to authenticate with, resolving it once.

    Prefers the token held by the GitHub CLI so that no secret has to live in
    the plaintext config.json, and revoking access stays a `gh` concern. Falls
    back to the optional `github_token` config key when gh cannot supply one.
    """
    global GITHUB_TOKEN

    if GITHUB_TOKEN is not None:
        return GITHUB_TOKEN

    try:
        token = _token_from_gh_cli()
    except EsphomeReleaseError:
        token = CONFIG.get("github_token") or ""
        if not token:
            raise

    GITHUB_TOKEN = token
    return GITHUB_TOKEN


def get_session() -> GitHub:
    global GITHUB_SESSION

    if GITHUB_SESSION is not None:
        return GITHUB_SESSION

    token = get_token()

    # Increase read timeout for creating PRs with long bodies.
    sess = github3.session.GitHubSession(default_read_timeout=30)
    gh = GitHub(token=token, session=sess)
    rate_limit = gh.rate_limit()["rate"]
    limit = rate_limit["limit"]
    remaining = rate_limit["remaining"]
    reset = datetime.utcfromtimestamp(rate_limit["reset"])
    print(f"{remaining}/{limit} rate limit remaining")
    print(f"Reset at {reset} UTC (in {reset - datetime.utcnow()})")
    GITHUB_SESSION = gh
    return GITHUB_SESSION
