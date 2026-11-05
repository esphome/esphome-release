import functools
import subprocess
import threading
import time
from datetime import UTC, datetime
from typing import Any

import click
import github3.session
import requests
from github3 import GitHub

from .config import CONFIG
from .exceptions import EsphomeReleaseError

# The gh OAuth token needs the `repo` scope to create releases and pull
# requests. Without it the API answers 403 instead of anything descriptive.
SCOPE_HINT = "Make sure the token carries the `repo` scope, adding it with `gh auth refresh -s repo` if needed."


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
            check=False,
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
            f"`gh auth token` failed{suffix}. Run `gh auth login` to authenticate the GitHub CLI. {SCOPE_HINT}"
        )

    token = proc.stdout.strip()
    if not token:
        raise EsphomeReleaseError(
            f"`gh auth token` returned an empty token. Run `gh auth login` to authenticate the GitHub CLI. {SCOPE_HINT}"
        )
    return token


@functools.cache
def get_token() -> str:
    """Return the GitHub token to authenticate with, resolving it once.

    Prefers the token held by the GitHub CLI so that no secret has to live in
    the plaintext config.json, and revoking access stays a `gh` concern. Falls
    back to the optional `github_token` config key when gh cannot supply one.
    """
    try:
        return _token_from_gh_cli()
    except EsphomeReleaseError:
        token = CONFIG.get("github_token") or ""
        if not token:
            raise
        return token


# Methods that are safe to repeat because they do not change anything on GitHub.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Gateway style failures that are usually gone a moment later.
TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})
# Automatic retries of an idempotent request before the user is asked.
AUTO_RETRIES = 3
# Seconds to wait per attempt number between automatic retries.
RETRY_BACKOFF = 2.0

# Requests run in parallel threads, so only one prompt may be on screen at once.
_PROMPT_LOCK = threading.Lock()


class RetryingGitHubSession(github3.session.GitHubSession):
    """GitHub session that retries transient failures instead of crashing.

    Idempotent requests are retried a few times automatically. Whatever still
    fails, and every failed non-idempotent request (which might have reached
    GitHub despite the error), is handed to the user to retry or give up.
    """

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> requests.Response:
        method_upper = str(method).upper()
        idempotent = method_upper in IDEMPOTENT_METHODS
        attempt = 0
        while True:
            response: requests.Response | None = None
            exc: Exception | None = None
            try:
                response = super().request(method, url, *args, **kwargs)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as err:
                exc = err
                problem = f"{type(err).__name__}: {err}"
            else:
                if response.status_code not in TRANSIENT_STATUSES:
                    return response
                problem = f"HTTP {response.status_code}"

            if idempotent and attempt < AUTO_RETRIES:
                attempt += 1
                time.sleep(RETRY_BACKOFF * attempt)
                continue

            if self._ask_retry(method_upper, url, problem, idempotent):
                attempt = 0
                continue
            if response is not None:
                # Let github3 raise its usual error for the response.
                return response
            raise EsphomeReleaseError(f"GitHub request {method_upper} {url} failed: {exc}") from exc

    @staticmethod
    def _ask_retry(method: str, url: str, problem: str, idempotent: bool) -> bool:
        with _PROMPT_LOCK:
            click.secho(f"GitHub request failed: {method} {url}: {problem}", fg="red")
            if not idempotent:
                click.secho(
                    "This request may still have reached GitHub, so check there before retrying.",
                    fg="red",
                )
            return click.confirm("Retry the GitHub request?", default=True)


@functools.cache
def get_session() -> GitHub:
    token = get_token()

    # Increase read timeout for creating PRs with long bodies.
    sess = RetryingGitHubSession(default_read_timeout=30)
    gh = GitHub(token=token, session=sess)
    rate_limit = gh.rate_limit()["rate"]
    limit = rate_limit["limit"]
    remaining = rate_limit["remaining"]
    reset = datetime.fromtimestamp(rate_limit["reset"], tz=UTC)
    print(f"{remaining}/{limit} rate limit remaining")
    print(f"Reset at {reset} UTC (in {reset - datetime.now(UTC)})")
    return gh
