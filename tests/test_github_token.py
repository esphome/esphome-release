"""Tests for resolving the GitHub token at runtime.

``esphomerelease.github`` no longer reads a long-lived personal access token
out of ``config.json``. It asks the GitHub CLI for its stored OAuth token via
``gh auth token`` and only falls back to the now-optional ``github_token``
config key, so nothing keeps a secret in a plaintext file in the repo folder.

Only the ``subprocess`` boundary is faked here: the resolution helpers
themselves run for real, including every failure path.
"""

import subprocess

import pytest

import esphomerelease.github as github_mod
from esphomerelease.exceptions import EsphomeReleaseError


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def reset_globals(monkeypatch):
    """Clear the process-lifetime caches so each test resolves afresh."""
    monkeypatch.setattr(github_mod, "GITHUB_TOKEN", None)
    monkeypatch.setattr(github_mod, "GITHUB_SESSION", None)
    monkeypatch.delitem(github_mod.CONFIG, "github_token", raising=False)


@pytest.fixture
def gh_calls(monkeypatch):
    """Record ``gh`` invocations and drive their result from a queue."""
    calls = []
    results = []

    def fake_run(args, **kwargs):
        calls.append(args)
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls, results


def test_token_from_gh_cli(gh_calls):
    """A healthy gh hands back its token, trailing newline stripped."""
    calls, results = gh_calls
    results.append(FakeCompletedProcess(stdout="gho_fromcli\n"))

    assert github_mod.get_token() == "gho_fromcli"
    assert calls == [["gh", "auth", "token"]]


def test_token_is_cached(gh_calls):
    """gh is shelled out to once per process, not per call."""
    calls, results = gh_calls
    results.append(FakeCompletedProcess(stdout="gho_fromcli\n"))

    assert github_mod.get_token() == "gho_fromcli"
    assert github_mod.get_token() == "gho_fromcli"
    assert len(calls) == 1


def test_gh_not_installed(gh_calls):
    """A missing gh binary explains how to install and authenticate it."""
    _, results = gh_calls
    results.append(FileNotFoundError("gh"))

    with pytest.raises(EsphomeReleaseError) as err:
        github_mod.get_token()

    message = str(err.value)
    assert "not installed" in message
    assert "gh auth login" in message
    assert "gh auth refresh -s repo" in message


def test_gh_not_authenticated(gh_calls):
    """A non-zero exit surfaces gh's own stderr alongside the fix."""
    _, results = gh_calls
    results.append(
        FakeCompletedProcess(returncode=1, stderr="not logged into any hosts\n")
    )

    with pytest.raises(EsphomeReleaseError) as err:
        github_mod.get_token()

    message = str(err.value)
    assert "not logged into any hosts" in message
    assert "gh auth login" in message


def test_gh_failure_detail_falls_back_to_stdout(gh_calls):
    """gh writing its complaint to stdout is still reported."""
    _, results = gh_calls
    results.append(FakeCompletedProcess(returncode=1, stdout="broken config\n"))

    with pytest.raises(EsphomeReleaseError) as err:
        github_mod.get_token()

    assert "broken config" in str(err.value)


def test_gh_failure_without_detail(gh_calls):
    """A silent non-zero exit still yields an actionable message."""
    _, results = gh_calls
    results.append(FakeCompletedProcess(returncode=1))

    with pytest.raises(EsphomeReleaseError) as err:
        github_mod.get_token()

    message = str(err.value)
    assert "`gh auth token` failed." in message
    assert "gh auth login" in message


def test_gh_returns_empty_token(gh_calls):
    """An exit code of 0 with no token is a failure, not an empty token."""
    _, results = gh_calls
    results.append(FakeCompletedProcess(stdout="\n"))

    with pytest.raises(EsphomeReleaseError) as err:
        github_mod.get_token()

    assert "empty token" in str(err.value)


def test_config_token_fallback(gh_calls, monkeypatch):
    """An existing config keeps working when gh cannot supply a token."""
    _, results = gh_calls
    results.append(FileNotFoundError("gh"))
    monkeypatch.setitem(github_mod.CONFIG, "github_token", "from_config")

    assert github_mod.get_token() == "from_config"


def test_empty_config_token_is_not_used(gh_calls, monkeypatch):
    """A blank github_token is treated as absent rather than as a token."""
    _, results = gh_calls
    results.append(FakeCompletedProcess(returncode=1, stderr="nope"))
    monkeypatch.setitem(github_mod.CONFIG, "github_token", "")

    with pytest.raises(EsphomeReleaseError):
        github_mod.get_token()


def test_missing_config_token_does_not_keyerror(gh_calls):
    """github_token is optional: its absence must not raise a KeyError."""
    _, results = gh_calls
    results.append(FakeCompletedProcess(stdout="gho_fromcli"))

    assert "github_token" not in github_mod.CONFIG
    assert github_mod.get_token() == "gho_fromcli"


class FakeGitHub:
    """Stand-in for github3.GitHub recording the token it was built with."""

    instances = []

    def __init__(self, token=None, session=None):
        self.token = token
        self.session = session
        FakeGitHub.instances.append(self)

    def rate_limit(self):
        return {"rate": {"limit": 5000, "remaining": 4999, "reset": 0}}


def test_get_session_uses_gh_token(gh_calls, monkeypatch, capsys):
    """The session is built with the resolved token and then cached."""
    calls, results = gh_calls
    results.append(FakeCompletedProcess(stdout="gho_fromcli\n"))
    FakeGitHub.instances = []
    monkeypatch.setattr(github_mod, "GitHub", FakeGitHub)

    session = github_mod.get_session()

    assert session.token == "gho_fromcli"
    assert "rate limit remaining" in capsys.readouterr().out

    assert github_mod.get_session() is session
    assert len(FakeGitHub.instances) == 1
    assert len(calls) == 1
