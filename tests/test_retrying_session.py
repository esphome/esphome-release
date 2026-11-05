"""The GitHub session retries transient failures and defers to the user."""

from typing import Any

import click
import pytest
import requests

import esphomerelease.github as github_mod
from esphomerelease.exceptions import EsphomeReleaseError

URL = "https://api.github.com/x"


def fake_response(status: int) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    return resp


class TransportQueue(list):
    """Outcomes to serve, plus the (method, url) calls that were made."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> "TransportQueue":
    """Queue of outcomes for the underlying requests transport (responses or exceptions)."""
    outcomes = TransportQueue()
    calls = outcomes.calls

    def fake_request(self: Any, method: str, url: str, *args: Any, **kwargs: Any) -> requests.Response:
        calls.append((method, url))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(requests.Session, "request", fake_request)
    monkeypatch.setattr(github_mod.time, "sleep", lambda s: None)
    return outcomes


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"answers": [], "asked": [], "echoed": []}

    def confirm(text: str, default: bool = False) -> bool:
        state["asked"].append(text)
        return state["answers"].pop(0)

    monkeypatch.setattr(click, "confirm", confirm)
    monkeypatch.setattr(click, "secho", lambda msg, **kw: state["echoed"].append(msg))
    return state


def session() -> github_mod.RetryingGitHubSession:
    return github_mod.RetryingGitHubSession(default_read_timeout=30)


def test_get_retries_connection_error_then_succeeds(transport: TransportQueue, prompts: dict[str, Any]) -> None:
    transport.extend([requests.exceptions.ConnectionError("boom"), fake_response(200)])

    resp = session().get(URL)

    assert resp.status_code == 200
    assert prompts["asked"] == []
    assert len(transport.calls) == 2


def test_get_retries_5xx_and_timeout(transport: TransportQueue, prompts: dict[str, Any]) -> None:
    transport.extend([fake_response(502), requests.exceptions.ReadTimeout("slow"), fake_response(404)])

    assert session().get(URL).status_code == 404
    assert prompts["asked"] == []


def test_get_asks_after_auto_retries_exhausted(transport: TransportQueue, prompts: dict[str, Any]) -> None:
    transport.extend([fake_response(503)] * (github_mod.AUTO_RETRIES + 1) + [fake_response(200)])
    prompts["answers"] = [True]

    assert session().get(URL).status_code == 200
    assert prompts["asked"] == ["Retry the GitHub request?"]
    assert "HTTP 503" in prompts["echoed"][0]
    assert not any("may still have reached" in m for m in prompts["echoed"])


def test_post_never_auto_retries_and_warns(transport: TransportQueue, prompts: dict[str, Any]) -> None:
    transport.extend([requests.exceptions.ConnectionError("dropped"), fake_response(201)])
    prompts["answers"] = [True]

    assert session().post(URL).status_code == 201
    assert len(transport.calls) == 2
    assert any("may still have reached" in m for m in prompts["echoed"])
    assert "POST" in prompts["echoed"][0]


def test_decline_with_response_returns_it(transport: TransportQueue, prompts: dict[str, Any]) -> None:
    transport.append(fake_response(500))
    prompts["answers"] = [False]

    assert session().patch(URL).status_code == 500


def test_decline_with_exception_raises(transport: TransportQueue, prompts: dict[str, Any]) -> None:
    transport.append(requests.exceptions.ConnectionError("dropped"))
    prompts["answers"] = [False]

    with pytest.raises(EsphomeReleaseError, match=r"GitHub request POST .* failed: .*dropped"):
        session().post(URL)


def test_non_transient_response_returned_untouched(transport: TransportQueue, prompts: dict[str, Any]) -> None:
    transport.append(fake_response(422))

    assert session().post(URL).status_code == 422
    assert prompts["asked"] == []
