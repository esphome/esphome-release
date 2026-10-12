"""Watch CI on the release PRs after a cut and notify when attention is needed."""

import contextlib
import enum
import select
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
import requests

from . import cutting
from .cutting import _bump_branch_name
from .exceptions import EsphomeReleaseError
from .github import get_token
from .model import Branch, Version
from .project import EsphomeDocsProject, EsphomeProject, Project
from .util import gprint

GRAPHQL_URL: str = "https://api.github.com/graphql"
GRAPHQL_TIMEOUT: float = 30
NOTIFY_TIMEOUT: float = 10
APP_NAME: str = "esphome-release"
# Shipped alongside this module (copied from the docs site favicon).
ICON_PATH: Path = Path(__file__).with_name("esphome-logo.png")

# CheckRun conclusions that count as a failed check.
FAILING_CONCLUSIONS: frozenset[str] = frozenset(
    {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
)
# StatusContext states that count as a failed check.
FAILING_CONTEXT_STATES: frozenset[str] = frozenset({"FAILURE", "ERROR"})


class CiState(enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"


@dataclass(frozen=True)
class WatchedPr:
    project: Project
    number: int
    url: str


@dataclass
class PrStatus:
    pr: WatchedPr
    state: CiState
    failing: list[str] = field(default_factory=list)


def _osascript_quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify(title: str, message: str, *, urgent: bool = False) -> None:
    """Send a desktop notification, always echoing to the terminal too."""
    gprint(f"{title}: {message}", fg="red" if urgent else "green")
    click.echo("\a", nl=False)

    cmd: list[str]
    if sys.platform == "darwin":
        script = f'display notification "{_osascript_quote(message)}" with title "{_osascript_quote(title)}"'
        cmd = ["osascript", "-e", script]
    elif sys.platform.startswith("linux"):
        cmd = [
            "notify-send",
            f"--app-name={APP_NAME}",
            f"--icon={ICON_PATH}",
            f"--urgency={'critical' if urgent else 'normal'}",
            title,
            message,
        ]
    else:
        return

    if shutil.which(cmd[0]) is None:
        return
    # A missing notification daemon must never break the watch.
    with contextlib.suppress(subprocess.SubprocessError, OSError):
        subprocess.run(cmd, check=True, capture_output=True, timeout=NOTIFY_TIMEOUT)


def build_query(prs: list[WatchedPr]) -> str:
    """One aliased GraphQL query covering every watched PR."""
    parts = []
    for idx, pr in enumerate(prs):
        parts.append(
            f"""  pr{idx}: repository(owner: "esphome", name: "{pr.project.name}") {{
    pullRequest(number: {pr.number}) {{
      number
      url
      commits(last: 1) {{ nodes {{ commit {{ statusCheckRollup {{
        state
        contexts(first: 100) {{ nodes {{
          __typename
          ... on CheckRun {{ name conclusion status }}
          ... on StatusContext {{ context state }}
        }} }}
      }} }} }} }}
    }}
  }}"""
        )
    return "query {\n" + "\n".join(parts) + "\n}"


def _rollup_state(rollup: dict[str, Any] | None) -> CiState:
    if rollup is None:
        return CiState.PENDING
    state = rollup["state"]
    if state == "SUCCESS":
        return CiState.SUCCESS
    if state in ("FAILURE", "ERROR"):
        return CiState.FAILURE
    return CiState.PENDING


def _failing_checks(rollup: dict[str, Any]) -> list[str]:
    failing = []
    for node in rollup["contexts"]["nodes"]:
        if node["__typename"] == "CheckRun" and node["conclusion"] in FAILING_CONCLUSIONS:
            failing.append(node["name"])
        elif node["__typename"] == "StatusContext" and node["state"] in FAILING_CONTEXT_STATES:
            failing.append(node["context"])
    return failing


def fetch_statuses(prs: list[WatchedPr]) -> list[PrStatus]:
    """Look up the CI state of all PRs with a single GraphQL request."""
    resp = requests.post(
        GRAPHQL_URL,
        json={"query": build_query(prs)},
        headers={"Authorization": f"bearer {get_token()}"},
        timeout=GRAPHQL_TIMEOUT,
    )
    if resp.status_code != 200:
        raise EsphomeReleaseError(f"GitHub GraphQL request failed: HTTP {resp.status_code}")
    payload = resp.json()
    if payload.get("errors"):
        raise EsphomeReleaseError(f"GitHub GraphQL errors: {payload['errors']}")

    statuses = []
    for idx, pr in enumerate(prs):
        pr_data = payload["data"][f"pr{idx}"]["pullRequest"]
        if pr_data is None:
            raise EsphomeReleaseError(f"[{pr.project.shortname}] PR #{pr.number} not found")
        nodes = pr_data["commits"]["nodes"]
        rollup = nodes[0]["commit"]["statusCheckRollup"] if nodes else None
        state = _rollup_state(rollup)
        failing: list[str] = []
        if state is CiState.FAILURE:
            assert rollup is not None  # a missing rollup is PENDING
            failing = _failing_checks(rollup)
        statuses.append(PrStatus(pr=pr, state=state, failing=failing))
    return statuses


def overall_state(statuses: list[PrStatus]) -> CiState:
    if any(s.state is CiState.FAILURE for s in statuses):
        return CiState.FAILURE
    if all(s.state is CiState.SUCCESS for s in statuses):
        return CiState.SUCCESS
    return CiState.PENDING


def find_release_prs(version: Version) -> list[WatchedPr]:
    """Find the code and docs release PRs for a freshly cut version."""
    base = Branch.BETA if version.beta else Branch.STABLE
    found = []
    for proj in (EsphomeProject, EsphomeDocsProject):
        matches = proj.get_pr_by_title(title=str(version), head=_bump_branch_name(version), base=base)
        if len(matches) != 1:
            gprint(
                f"[{proj.shortname}] expected one release PR for {version}, found {len(matches)}; not watching it",
                fg="yellow",
            )
            continue
        found.append(WatchedPr(project=proj, number=matches[0].number, url=matches[0].html_url))
    return found


def _wait_for_enter(timeout: float) -> bool:
    """Wait up to ``timeout`` seconds for Enter; True if it was pressed."""
    readable, _, _ = select.select([sys.stdin], [], [], timeout)
    if readable:
        sys.stdin.readline()
        return True
    return False


def _describe(statuses: list[PrStatus]) -> str:
    return "; ".join(
        f"[{s.pr.project.shortname}] #{s.pr.number}: " + ", ".join(s.failing)
        for s in statuses
        if s.state is CiState.FAILURE
    )


def _watch_loop(prs: list[WatchedPr], poll_seconds: float, notify_seconds: float) -> None:
    """Poll until CI is green and the user presses Enter."""
    state = CiState.PENDING
    announced: CiState | None = None
    notified: CiState | None = None
    last_notify = 0.0
    while True:
        try:
            statuses = fetch_statuses(prs)
        except (requests.RequestException, EsphomeReleaseError) as err:
            # A GitHub hiccup (5xx, GraphQL error) must not end a long watch.
            gprint(f"Could not fetch CI status, retrying: {err}", fg="yellow")
        else:
            state = overall_state(statuses)
            if state is CiState.PENDING:
                notified = None
                if announced is not CiState.PENDING:
                    gprint("CI is still running...", fg="yellow")
            else:
                now = time.monotonic()
                if state is not notified or now - last_notify >= notify_seconds:
                    notified = state
                    last_notify = now
                    if state is CiState.FAILURE:
                        notify(
                            "Release CI failing",
                            _describe(statuses),
                            urgent=True,
                        )
                    else:
                        notify(
                            "Release CI passed",
                            "Press Enter in the terminal to publish.",
                        )
            announced = state

        if _wait_for_enter(poll_seconds):
            if state is CiState.SUCCESS:
                return
            gprint(f"CI is not passing yet (currently {state.value})", fg="yellow")


def watch_release_ci(
    version: Version,
    *,
    base: Version,
    poll_seconds: float = 60,
    notify_seconds: float = 300,
) -> None:
    """Watch the release PRs' CI, notify, and offer to publish once it passes."""
    prs = find_release_prs(version)
    if not prs:
        gprint("No release PRs found to watch.", fg="yellow")
        return

    gprint(
        "Watching CI for "
        + ", ".join(f"[{p.project.shortname}] #{p.number}" for p in prs)
        + "; Ctrl+C to stop safely; press Enter once CI passes to publish."
    )
    for pr in prs:
        gprint(f"  {pr.url}")

    try:
        _watch_loop(prs, poll_seconds, notify_seconds)
    except KeyboardInterrupt:
        gprint(
            "Stopped watching CI. Publish later with `esphomerelease publish`.",
            fg="yellow",
        )
        return

    if not click.confirm(f"Publish {version} now?", default=True):
        gprint("Publish later with `esphomerelease publish`.")
        return

    projects: list[Project] = [EsphomeProject, EsphomeDocsProject]
    if version.beta:
        cutting.publish_beta_release(version, projects=projects, base=base, confirmed=True)
    else:
        cutting.publish_release(version, projects=projects, base=base, confirmed=True)
