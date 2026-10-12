"""Tests for the post-cut CI watcher.

``ci_watch`` imports ``.cutting`` and ``.project``, which instantiate every
``Project`` at import time and assert each configured path is a directory. The
``mods`` fixture writes a temp ``config.json`` so the modules are importable,
mirroring the import-safe reload pattern used elsewhere in this repo.
"""

import importlib
import json
import subprocess
import types
from typing import Any

import pytest
import requests
from click.testing import CliRunner


@pytest.fixture
def mods(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = {
        "github_token": "x",
        "step": False,
        "esphome_path": str(repo_dir),
        "esphome_io_path": str(repo_dir),
        "esphome_hassio_path": str(repo_dir),
        "esphome_issues_path": str(repo_dir),
        "esphome_feature_requests_path": str(repo_dir),
    }
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(config))

    import esphomerelease.config as config_mod

    importlib.reload(config_mod)
    import esphomerelease.project as project_mod

    importlib.reload(project_mod)
    import esphomerelease.cutting as cutting_mod

    importlib.reload(cutting_mod)
    import esphomerelease.ci_watch as ci_mod

    importlib.reload(ci_mod)
    import esphomerelease.commands as commands_mod

    importlib.reload(commands_mod)
    return types.SimpleNamespace(ci=ci_mod, cutting=cutting_mod, commands=commands_mod, project=project_mod)


@pytest.fixture
def ci(mods):
    return mods.ci


def _version(mods, beta: bool):
    from esphomerelease.model import Version

    return Version(2026, 10, 0, beta=1 if beta else 0)


def _pr(mods, project, number: int):
    return mods.ci.WatchedPr(project=project, number=number, url=f"https://x/{number}")


# ---------------------------------------------------------------- notify


class _Run:
    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.exc = exc

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.exc:
            raise self.exc


def test_notify_linux(ci, monkeypatch, capsys):
    run = _Run()
    monkeypatch.setattr(ci.sys, "platform", "linux")
    monkeypatch.setattr(ci.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(ci.subprocess, "run", run)
    ci.notify("T", "M", urgent=True)
    ci.notify("T", "M")
    assert run.calls[0][0] == [
        "notify-send",
        "--app-name=esphome-release",
        f"--icon={ci.ICON_PATH}",
        "--urgency=critical",
        "T",
        "M",
    ]
    assert run.calls[1][0][3] == "--urgency=normal"
    assert ci.ICON_PATH.is_file()
    out = capsys.readouterr().out
    assert "T: M" in out
    assert "\a" in out


def test_notify_linux_tool_missing(ci, monkeypatch):
    run = _Run()
    monkeypatch.setattr(ci.sys, "platform", "linux")
    monkeypatch.setattr(ci.shutil, "which", lambda name: None)
    monkeypatch.setattr(ci.subprocess, "run", run)
    ci.notify("T", "M")
    assert run.calls == []


@pytest.mark.parametrize(
    "exc",
    [subprocess.CalledProcessError(1, "notify-send"), OSError("boom")],
)
def test_notify_failure_swallowed(ci, monkeypatch, exc):
    run = _Run(exc)
    monkeypatch.setattr(ci.sys, "platform", "linux")
    monkeypatch.setattr(ci.shutil, "which", lambda name: "/x")
    monkeypatch.setattr(ci.subprocess, "run", run)
    ci.notify("T", "M")
    assert len(run.calls) == 1


def test_notify_macos_escapes_quotes(ci, monkeypatch):
    run = _Run()
    monkeypatch.setattr(ci.sys, "platform", "darwin")
    monkeypatch.setattr(ci.shutil, "which", lambda name: "/x")
    monkeypatch.setattr(ci.subprocess, "run", run)
    ci.notify('a "b"', 'c\\d "e"')
    assert run.calls[0][0] == [
        "osascript",
        "-e",
        'display notification "c\\\\d \\"e\\"" with title "a \\"b\\""',
    ]


def test_notify_other_platform(ci, monkeypatch, capsys):
    run = _Run()
    monkeypatch.setattr(ci.sys, "platform", "win32")
    monkeypatch.setattr(ci.subprocess, "run", run)
    ci.notify("T", "M")
    assert run.calls == []
    assert "T: M" in capsys.readouterr().out


# --------------------------------------------------------------- graphql


def _rollup(state, nodes=()):
    return {"state": state, "contexts": {"nodes": list(nodes)}}


def _pr_payload(rollup, *, empty_commits=False):
    commits = [] if empty_commits else [{"commit": {"statusCheckRollup": rollup}}]
    return {"pullRequest": {"number": 1, "url": "u", "commits": {"nodes": commits}}}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _watched(mods):
    return [
        _pr(mods, mods.project.EsphomeProject, 11),
        _pr(mods, mods.project.EsphomeDocsProject, 22),
    ]


def test_build_query_aliases_both_repos(mods):
    query = mods.ci.build_query(_watched(mods))
    assert 'pr0: repository(owner: "esphome", name: "esphome")' in query
    assert 'pr1: repository(owner: "esphome", name: "esphome.io")' in query
    assert "pullRequest(number: 11)" in query
    assert "pullRequest(number: 22)" in query


def test_fetch_statuses_single_request_and_mapping(mods, monkeypatch):
    ci = mods.ci
    monkeypatch.setattr(ci, "get_token", lambda: "tok")
    posts = []

    def fake_post(url, **kwargs):
        posts.append((url, kwargs))
        return _Resp(
            {
                "data": {
                    "pr0": _pr_payload(
                        _rollup(
                            "FAILURE",
                            [
                                {
                                    "__typename": "CheckRun",
                                    "name": "lint",
                                    "conclusion": "FAILURE",
                                    "status": "COMPLETED",
                                },
                                {
                                    "__typename": "CheckRun",
                                    "name": "ok",
                                    "conclusion": "SUCCESS",
                                    "status": "COMPLETED",
                                },
                                {
                                    "__typename": "StatusContext",
                                    "context": "ci/legacy",
                                    "state": "ERROR",
                                },
                                {
                                    "__typename": "StatusContext",
                                    "context": "ci/fine",
                                    "state": "SUCCESS",
                                },
                                {"__typename": "Other"},
                            ],
                        )
                    ),
                    "pr1": _pr_payload(_rollup("SUCCESS")),
                }
            }
        )

    monkeypatch.setattr(ci.requests, "post", fake_post)
    statuses = ci.fetch_statuses(_watched(mods))
    assert len(posts) == 1
    assert posts[0][0] == ci.GRAPHQL_URL
    assert posts[0][1]["headers"] == {"Authorization": "bearer tok"}
    assert statuses[0].state is ci.CiState.FAILURE
    assert statuses[0].failing == ["lint", "ci/legacy"]
    assert statuses[1].state is ci.CiState.SUCCESS
    assert statuses[1].failing == []


@pytest.mark.parametrize(
    ("rollup", "empty", "expected"),
    [
        (_rollup("SUCCESS"), False, "SUCCESS"),
        (_rollup("FAILURE"), False, "FAILURE"),
        (_rollup("ERROR"), False, "FAILURE"),
        (_rollup("PENDING"), False, "PENDING"),
        (_rollup("EXPECTED"), False, "PENDING"),
        (None, False, "PENDING"),
        (None, True, "PENDING"),
    ],
)
def test_rollup_mapping(mods, monkeypatch, rollup, empty, expected):
    ci = mods.ci
    monkeypatch.setattr(ci, "get_token", lambda: "tok")
    payload = {"data": {"pr0": _pr_payload(rollup, empty_commits=empty)}}
    monkeypatch.setattr(ci.requests, "post", lambda *a, **k: _Resp(payload))
    [status] = ci.fetch_statuses(_watched(mods)[:1])
    assert status.state is ci.CiState[expected]


def test_fetch_statuses_http_error(mods, monkeypatch):
    ci = mods.ci
    monkeypatch.setattr(ci, "get_token", lambda: "tok")
    monkeypatch.setattr(ci.requests, "post", lambda *a, **k: _Resp({}, 502))
    with pytest.raises(ci.EsphomeReleaseError, match="HTTP 502"):
        ci.fetch_statuses(_watched(mods))


def test_fetch_statuses_graphql_errors(mods, monkeypatch):
    ci = mods.ci
    monkeypatch.setattr(ci, "get_token", lambda: "tok")
    monkeypatch.setattr(ci.requests, "post", lambda *a, **k: _Resp({"errors": [{"message": "bad"}]}))
    with pytest.raises(ci.EsphomeReleaseError, match="bad"):
        ci.fetch_statuses(_watched(mods))


def test_fetch_statuses_missing_pr(mods, monkeypatch):
    ci = mods.ci
    monkeypatch.setattr(ci, "get_token", lambda: "tok")
    payload = {"data": {"pr0": {"pullRequest": None}}}
    monkeypatch.setattr(ci.requests, "post", lambda *a, **k: _Resp(payload))
    with pytest.raises(ci.EsphomeReleaseError, match="not found"):
        ci.fetch_statuses(_watched(mods)[:1])


def test_overall_state(mods):
    ci = mods.ci
    pr = _watched(mods)[0]

    def st(*states):
        return [ci.PrStatus(pr=pr, state=s) for s in states]

    S, F, P = ci.CiState.SUCCESS, ci.CiState.FAILURE, ci.CiState.PENDING
    assert ci.overall_state(st(S, F)) is F
    assert ci.overall_state(st(S, P)) is P
    assert ci.overall_state(st(S, S)) is S


# ------------------------------------------------------------- discovery


def _fake_get_pr(mods, monkeypatch, results, calls):
    def make(name):
        def get_pr_by_title(self=None, **kwargs):
            calls.append((name, kwargs))
            return results[name]

        return get_pr_by_title

    monkeypatch.setattr(mods.project.EsphomeProject, "get_pr_by_title", make("code"), raising=False)
    monkeypatch.setattr(mods.project.EsphomeDocsProject, "get_pr_by_title", make("docs"), raising=False)


def _gh_pr(number):
    return types.SimpleNamespace(number=number, html_url=f"https://x/{number}")


@pytest.mark.parametrize("beta", [True, False])
def test_find_release_prs_base_branch(mods, monkeypatch, beta):
    from esphomerelease.model import Branch

    calls: list = []
    _fake_get_pr(mods, monkeypatch, {"code": [_gh_pr(1)], "docs": [_gh_pr(2)]}, calls)
    version = _version(mods, beta)
    prs = mods.ci.find_release_prs(version)
    assert [(p.number, p.url) for p in prs] == [(1, "https://x/1"), (2, "https://x/2")]
    expected_base = Branch.BETA if beta else Branch.STABLE
    for _, kwargs in calls:
        assert kwargs == {
            "title": str(version),
            "head": f"bump-{version}",
            "base": expected_base,
        }


def test_find_release_prs_skips_zero_and_multiple(mods, monkeypatch, capsys):
    calls: list = []
    _fake_get_pr(mods, monkeypatch, {"code": [], "docs": [_gh_pr(1), _gh_pr(2)]}, calls)
    assert mods.ci.find_release_prs(_version(mods, True)) == []
    out = capsys.readouterr().out
    assert "found 0" in out
    assert "found 2" in out


def test_watch_nothing_found_does_not_poll(mods, monkeypatch, capsys):
    ci = mods.ci
    monkeypatch.setattr(ci, "find_release_prs", lambda v: [])

    def boom(*a, **k):
        raise AssertionError("must not poll")

    monkeypatch.setattr(ci, "fetch_statuses", boom)
    monkeypatch.setattr(ci, "_wait_for_enter", boom)
    ci.watch_release_ci(_version(mods, True), base=_version(mods, False))
    assert "No release PRs" in capsys.readouterr().out


# ------------------------------------------------------------- the loop


def _statuses(mods, *states, failing=()):
    prs = _watched(mods)
    return [
        mods.ci.PrStatus(
            pr=prs[i],
            state=state,
            failing=list(failing) if state is mods.ci.CiState.FAILURE else [],
        )
        for i, state in enumerate(states)
    ]


class _Driver:
    """Scripted polls: each step is (statuses | exception, enter_pressed)."""

    def __init__(self, mods, monkeypatch, steps):
        ci = mods.ci
        self.steps = steps
        self.idx = -1
        self.now = 0.0
        self.waits: list[float] = []
        self.notifications: list[tuple[str, str, bool]] = []
        monkeypatch.setattr(ci.time, "monotonic", lambda: self.now)
        monkeypatch.setattr(ci, "fetch_statuses", self.fetch)
        monkeypatch.setattr(ci, "_wait_for_enter", self.wait)
        monkeypatch.setattr(
            ci,
            "notify",
            lambda title, msg, *, urgent=False: self.notifications.append((title, msg, urgent)),
        )

    def fetch(self, prs):
        self.idx += 1
        result = self.steps[self.idx][0]
        if isinstance(result, BaseException):
            raise result
        return result

    def wait(self, timeout):
        self.waits.append(timeout)
        self.now += timeout
        enter = self.steps[self.idx][1]
        if isinstance(enter, BaseException):
            raise enter
        return enter


def test_watch_loop_full_scenario(mods, monkeypatch, capsys):
    ci = mods.ci
    S, F, P = ci.CiState.SUCCESS, ci.CiState.FAILURE, ci.CiState.PENDING
    pend = _statuses(mods, P, S)
    fail = _statuses(mods, F, S, failing=["lint", "test"])
    ok = _statuses(mods, S, S)
    steps = [
        (pend, False),  # t=0 pending, announced
        (fail, False),  # t=100 failure -> notify
        (fail, True),  # t=200 within interval; Enter ignored
        (fail, False),  # t=300 still within (200 < 250)
        (fail, False),  # t=400 300 >= 250 -> re-notify
        (pend, False),  # t=500 reset
        (pend, False),  # t=600 pending again, silent
        (requests.ConnectionError("down"), False),  # t=700 skipped
        (ok, False),  # t=800 success -> notify
        (ok, False),  # t=900
        (ok, False),  # t=1000
        (ok, False),  # t=1100 re-notify
        (fail, False),  # t=1200 failure after success -> notify immediately
        (ok, True),  # t=1300 success again -> notify, Enter -> return
    ]
    driver = _Driver(mods, monkeypatch, steps)
    ci._watch_loop(_watched(mods), 100, 250)

    titles = [(t, u) for t, _, u in driver.notifications]
    assert titles == [
        ("Release CI failing", True),
        ("Release CI failing", True),
        ("Release CI passed", False),
        ("Release CI passed", False),
        ("Release CI failing", True),
        ("Release CI passed", False),
    ]
    assert "[esphome] #11: lint, test" in driver.notifications[0][1]
    assert driver.waits == [100] * len(steps)
    out = capsys.readouterr().out
    assert out.count("CI is still running") == 2
    assert "Could not fetch CI status" in out
    assert "not passing yet (currently failure)" in out


def test_watch_loop_request_error_first_poll_then_enter_ignored(mods, monkeypatch, capsys):
    ci = mods.ci
    ok = _statuses(mods, ci.CiState.SUCCESS, ci.CiState.SUCCESS)
    steps = [
        (requests.Timeout("slow"), True),  # state still pending -> ignored
        (mods.ci.EsphomeReleaseError("HTTP 502"), False),  # skipped too
        (ok, True),
    ]
    driver = _Driver(mods, monkeypatch, steps)
    ci._watch_loop(_watched(mods), 10, 300)
    assert len(driver.notifications) == 1
    out = capsys.readouterr().out
    assert "currently pending" in out
    assert "HTTP 502" in out


def test_watch_ctrl_c_returns_cleanly(mods, monkeypatch, capsys):
    ci = mods.ci
    monkeypatch.setattr(ci, "find_release_prs", lambda v: _watched(mods))
    _Driver(
        mods,
        monkeypatch,
        [(_statuses(mods, ci.CiState.PENDING, ci.CiState.PENDING), KeyboardInterrupt())],
    )

    def no_confirm(*a, **k):
        raise AssertionError("must not prompt")

    monkeypatch.setattr(ci.click, "confirm", no_confirm)
    ci.watch_release_ci(_version(mods, True), base=_version(mods, False))
    out = capsys.readouterr().out
    assert "Ctrl+C to stop safely" in out
    assert "https://x/11" in out
    assert "esphomerelease publish" in out


@pytest.mark.parametrize("beta", [True, False])
def test_watch_publish_confirmed(mods, monkeypatch, beta):
    ci = mods.ci
    monkeypatch.setattr(ci, "find_release_prs", lambda v: _watched(mods))
    monkeypatch.setattr(ci, "_watch_loop", lambda *a: None)
    asked: list = []
    monkeypatch.setattr(
        ci.click,
        "confirm",
        lambda text, default: asked.append((text, default)) or True,
    )
    calls: list = []
    monkeypatch.setattr(
        ci.cutting,
        "publish_beta_release",
        lambda v, projects, base, confirmed: calls.append(("b", v, projects, base, confirmed)),
    )
    monkeypatch.setattr(
        ci.cutting,
        "publish_release",
        lambda v, projects, base, confirmed: calls.append(("s", v, projects, base, confirmed)),
    )
    version = _version(mods, beta)
    base = _version(mods, False)
    ci.watch_release_ci(version, base=base)
    assert asked == [(f"Publish {version} now?", True)]
    assert calls == [
        (
            "b" if beta else "s",
            version,
            [mods.project.EsphomeProject, mods.project.EsphomeDocsProject],
            base,
            True,
        )
    ]


def test_watch_publish_declined(mods, monkeypatch, capsys):
    ci = mods.ci
    monkeypatch.setattr(ci, "find_release_prs", lambda v: _watched(mods))
    monkeypatch.setattr(ci, "_watch_loop", lambda *a: None)
    monkeypatch.setattr(ci.click, "confirm", lambda text, default: False)

    def boom(*a, **k):
        raise AssertionError("must not publish")

    monkeypatch.setattr(ci.cutting, "publish_beta_release", boom)
    monkeypatch.setattr(ci.cutting, "publish_release", boom)
    ci.watch_release_ci(_version(mods, True), base=_version(mods, False))
    assert "esphomerelease publish" in capsys.readouterr().out


def test_ctrl_c_during_publish_not_swallowed(mods, monkeypatch):
    ci = mods.ci
    monkeypatch.setattr(ci, "find_release_prs", lambda v: _watched(mods))
    monkeypatch.setattr(ci, "_watch_loop", lambda *a: None)
    monkeypatch.setattr(ci.click, "confirm", lambda text, default: True)

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(ci.cutting, "publish_beta_release", interrupted)
    with pytest.raises(KeyboardInterrupt):
        ci.watch_release_ci(_version(mods, True), base=_version(mods, False))


# ------------------------------------------------------- _wait_for_enter


def test_wait_for_enter_readable(ci, monkeypatch):
    class Stdin:
        read = 0

        def readline(self):
            Stdin.read += 1
            return "\n"

    seen: list = []
    monkeypatch.setattr(ci.sys, "stdin", Stdin())

    def fake_select(r, w, x, timeout):
        seen.append(timeout)
        return (r, [], [])

    monkeypatch.setattr(ci.select, "select", fake_select)
    assert ci._wait_for_enter(5) is True
    assert Stdin.read == 1
    assert seen == [5]


def test_wait_for_enter_timeout(ci, monkeypatch):
    class Stdin:
        def readline(self):
            raise AssertionError("must not read")

    monkeypatch.setattr(ci.sys, "stdin", Stdin())
    monkeypatch.setattr(ci.select, "select", lambda r, w, x, t: ([], [], []))
    assert ci._wait_for_enter(5) is False


# ------------------------------------------------------------------- CLI


BASE = "base-sentinel"


@pytest.mark.parametrize(
    ("args", "watched"),
    [(["cut", "2026.10.0b1"], True), (["cut", "2026.10.0b1", "--no-watch"], False)],
)
def test_cut_cli_watch_wiring(mods, monkeypatch, args, watched):
    cmds = mods.commands
    order: list = []
    monkeypatch.setattr(cmds.cutting, "cut_beta_release", lambda v: order.append("cut") or BASE)
    monkeypatch.setattr(cmds, "_commit_user_cache_if_changed", lambda: order.append("cache"))
    monkeypatch.setattr(cmds.ci_watch, "watch_release_ci", lambda v, base: order.append(("watch", str(v), base)))
    result = CliRunner().invoke(cmds.cli, args)
    assert result.exit_code == 0, result.output
    expected: list = ["cut", "cache"]
    if watched:
        expected.append(("watch", "2026.10.0b1", BASE))
    assert order == expected


def test_cut_cli_stable_path(mods, monkeypatch):
    cmds = mods.commands
    order: list = []
    monkeypatch.setattr(cmds.cutting, "cut_release", lambda v: order.append("cut") or BASE)
    monkeypatch.setattr(cmds, "_commit_user_cache_if_changed", lambda: None)
    monkeypatch.setattr(cmds.ci_watch, "watch_release_ci", lambda v, base: order.append(("w", base)))
    assert CliRunner().invoke(cmds.cli, ["cut", "2026.10.0"]).exit_code == 0
    assert order == ["cut", ("w", BASE)]
