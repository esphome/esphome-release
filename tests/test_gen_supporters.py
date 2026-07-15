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
    def __init__(self, login: str):
        self.login = login


class FakeRepo:
    def __init__(self, contributors, *, fail: bool = False):
        self._contributors = [FakeContributor(c) for c in contributors]
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
    """Contributors are collected across repos (ignoring the ignore-list),
    cached names are not re-fetched, missing users are reported, and the
    cache + supporters page are written."""
    (tmp_path / "supporters.template.md").write_text(
        "TEMPLATE_CONTRIBUTIONS\nTEMPLATE_GENERATION_DATE\n"
    )
    # "cached" is already known; its name must not be fetched again.
    (tmp_path / docs_mod.USERS_CACHE_FILE).write_text(
        json.dumps({"cached": "Cached Name"})
    )

    session = FakeSession(
        repos={
            "esphome": FakeRepo(["alice", "bob"]),
            "esphome.io": FakeRepo(["bob", "cached", "ghost"]),
            "backlog": FakeRepo(["ignored"]),
        },
        users={"alice": "Alice A", "bob": None},
    )
    docs_mod.get_session = lambda: session

    docs_mod.gen_supporters()

    # "ghost" 404s: reported, not cached. "ignored" never collected.
    assert "Error getting user ghost" in capsys.readouterr().out
    assert sorted(session.user_calls) == ["alice", "bob", "ghost"]
    assert session._repos["backlog"].contributor_calls == 0

    cache = json.loads((tmp_path / docs_mod.USERS_CACHE_FILE).read_text())
    assert cache == {"alice": "Alice A", "bob": None, "cached": "Cached Name"}

    page = (
        tmp_path / "repo" / "src" / "content" / "docs" / "guides" / "supporters.mdx"
    ).read_text()
    assert "- [Alice A (@alice)](https://github.com/alice)" in page
    assert "- [bob (@bob)](https://github.com/bob)" in page
    assert "- [Cached Name (@cached)](https://github.com/cached)" in page
    assert "ghost" not in page


def test_get_repo_contribs_retries_then_reports(docs_mod, capsys):
    """A repo that keeps failing is retried MAX_RETRIES times, reported, and
    contributes nothing."""
    failing = FakeRepo([], fail=True)
    session = FakeSession(repos={"esphome": failing}, users={})

    assert docs_mod.get_repo_contribs(session, "esphome") == []
    assert failing.contributor_calls == docs_mod.MAX_RETRIES
    assert "Error getting contributors from esphome: boom" in capsys.readouterr().out
