"""Tests for ``docs.gen_supporters`` and its parallel fetch helpers.

``docs`` imports ``.project``, which instantiates every ``Project`` at import
time and asserts each configured path is a directory. The ``docs_mod`` fixture
writes a temp ``config.json`` whose paths point at real directories so the
modules are importable, mirroring the import-safe reload pattern used
elsewhere in this repo.
"""

import importlib
import json
import types

import pytest
from github3.exceptions import NotFoundError


@pytest.fixture
def docs_mod(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    (repo_dir / "src" / "content" / "docs" / "guides").mkdir(parents=True)
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
    import esphomerelease.docs as docs

    importlib.reload(docs)
    return docs


def _not_found_error() -> NotFoundError:
    resp = types.SimpleNamespace(
        status_code=404,
        content=b"",
        json=lambda: {"message": "Not Found"},
    )
    return NotFoundError(resp)


class FakeContributor:
    def __init__(self, login: str, account_id: int, *, type: str = "User"):  # noqa: A002
        self.login = login
        self.id = account_id
        self.type = type


class FakeRepo:
    def __init__(self, contributors: list[FakeContributor], *, fail: bool = False):
        self._contributors = contributors
        self._fail = fail
        self.contributor_calls = 0

    def contributors(self):
        self.contributor_calls += 1
        if self._fail:
            raise RuntimeError("boom")
        return list(self._contributors)


class FakeSession:
    def __init__(self, repos: dict, users: dict):
        # repos: name -> FakeRepo; users: login -> display name
        self._repos = repos
        self._users = users
        self.user_calls = []

    def organization(self, name: str):
        assert name == "esphome"
        return types.SimpleNamespace(
            repositories=lambda: [
                types.SimpleNamespace(name=name) for name in self._repos
            ]
        )

    def repository(self, owner: str, name: str) -> FakeRepo:
        assert owner == "esphome"
        return self._repos[name]

    def user(self, login: str):
        self.user_calls.append(login)
        if login not in self._users:
            raise _not_found_error()
        return types.SimpleNamespace(name=self._users[login])


def test_gen_supporters_end_to_end(docs_mod, tmp_path, capsys):
    """Ids flow through end to end: contributors are collected across repos
    (ignoring the ignore-list) keyed by numeric id, a name already cached for
    a known id is not re-fetched, an id already cached whose login CHANGED
    has its login updated without a name re-fetch (free rename tracking), a
    brand new id gets its name fetched, a 404'd login is reported and not
    cached, and the cache is written sorted by numeric id."""
    (tmp_path / "supporters.template.md").write_text(
        "TEMPLATE_CONTRIBUTIONS\nTEMPLATE_GENERATION_DATE\n"
    )
    # id 3 is already cached under a stale login ("renamed-old"); the repos
    # below report it under a new login ("renamed-new") - its login must be
    # updated in place without spending a session.user() call on its name.
    (tmp_path / docs_mod.USERS_CACHE_FILE).write_text(
        json.dumps({"3": {"login": "renamed-old", "name": "Renamed Person"}})
    )

    session = FakeSession(
        repos={
            "esphome": FakeRepo(
                [FakeContributor("alice", 1), FakeContributor("bob", 2)]
            ),
            "esphome.io": FakeRepo(
                [
                    FakeContributor("bob", 2),
                    FakeContributor("renamed-new", 3),
                    FakeContributor("ghost", 4),
                ]
            ),
            "backlog": FakeRepo([FakeContributor("ignored", 99)]),
        },
        users={"alice": "Alice A", "bob": None},
    )
    docs_mod.get_session = lambda: session

    docs_mod.gen_supporters()

    # "ghost" 404s: reported, not cached. "ignored" never collected.
    assert "Error getting user ghost" in capsys.readouterr().out
    assert sorted(session.user_calls) == ["alice", "bob", "ghost"]
    assert session._repos["backlog"].contributor_calls == 0
    # id 3's name was never re-fetched - only alice/bob/ghost's logins were.
    assert "renamed-new" not in session.user_calls

    cache = json.loads((tmp_path / docs_mod.USERS_CACHE_FILE).read_text())
    assert cache == {
        "1": {"login": "alice", "name": "Alice A"},
        "2": {"login": "bob", "name": None},
        "3": {"login": "renamed-new", "name": "Renamed Person"},
    }
    # Sorted by numeric id ascending.
    assert list(cache.keys()) == ["1", "2", "3"]

    page = (
        tmp_path / "repo" / "src" / "content" / "docs" / "guides" / "supporters.mdx"
    ).read_text()
    assert "- [Alice A (@alice)](https://github.com/alice)" in page
    assert "- [bob (@bob)](https://github.com/bob)" in page
    assert "- [Renamed Person (@renamed-new)](https://github.com/renamed-new)" in page
    assert "ghost" not in page


def test_gen_supporters_missing_cache_file_starts_empty(docs_mod, tmp_path):
    """No users_cache.json yet: gen_supporters starts from an empty cache
    instead of raising, and fetches every contributor's name."""
    (tmp_path / "supporters.template.md").write_text(
        "TEMPLATE_CONTRIBUTIONS\nTEMPLATE_GENERATION_DATE\n"
    )
    assert not (tmp_path / docs_mod.USERS_CACHE_FILE).exists()

    session = FakeSession(
        repos={"esphome": FakeRepo([FakeContributor("alice", 1)])},
        users={"alice": "Alice A"},
    )
    docs_mod.get_session = lambda: session

    docs_mod.gen_supporters()

    cache = json.loads((tmp_path / docs_mod.USERS_CACHE_FILE).read_text())
    assert cache == {"1": {"login": "alice", "name": "Alice A"}}


def test_get_repo_contribs_retries_then_reports(docs_mod, capsys):
    """A repo that keeps failing is retried MAX_RETRIES times, reported, and
    contributes nothing."""
    failing = FakeRepo([], fail=True)
    session = FakeSession(repos={"esphome": failing}, users={})

    assert docs_mod.get_repo_contribs(session, "esphome") == []
    assert failing.contributor_calls == docs_mod.MAX_RETRIES
    assert "Error getting contributors from esphome: boom" in capsys.readouterr().out


def test_get_repo_contribs_filters_bots(docs_mod):
    """Bots are dropped whether GitHub reports them as type "Bot", they have
    a bot-shaped login (e.g. Copilot, which reports as "Bot" too but is
    exercised here via the login-heuristic branch), or they're a known bot
    id with a non-Bot type and a clean-looking login (e.g. esphbot); real
    human contributors pass through unchanged, paired with their numeric id.
    """
    repo = FakeRepo(
        [
            FakeContributor("alice", 1),
            FakeContributor("dependabot[bot]", 2, type="Bot"),
            FakeContributor("Copilot", 3, type="User"),
            FakeContributor("esphbot", 287758279, type="User"),
        ]
    )
    session = FakeSession(repos={"esphome": repo}, users={})

    assert docs_mod.get_repo_contribs(session, "esphome") == [("1", "alice")]
