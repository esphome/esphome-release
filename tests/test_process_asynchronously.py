"""Tests for ``util.process_asynchronously``.

The helper fans jobs out over a thread pool. A job that raises must not kill
its worker thread before ``task_done()`` — that would leave ``q.join()``
blocked forever — so exceptions are captured and re-raised after all jobs
have drained.

util.py imports ``.config``, which loads ``config.json`` at import time. The
``util`` fixture chdir's into a tmp dir with an empty config so the module is
importable without a real working copy (mirrors the import-safe test pattern
used elsewhere in this repo).
"""

import importlib

import pytest


@pytest.fixture
def util(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text("{}")
    import esphomerelease.config as config

    importlib.reload(config)
    import esphomerelease.util as util_mod

    importlib.reload(util_mod)
    return util_mod


def test_returns_results_in_job_order(util):
    jobs = [lambda i=i: i * 2 for i in range(20)]
    assert util.process_asynchronously(jobs, "test") == [i * 2 for i in range(20)]


def test_empty_job_list(util):
    assert util.process_asynchronously([], "test") == []


def test_job_exception_is_reraised_not_hung(util):
    def boom():
        raise ValueError("job failed")

    jobs = [lambda: 1, boom, lambda: 3]
    with pytest.raises(ValueError, match="job failed"):
        util.process_asynchronously(jobs, "test")


def test_first_failing_job_wins_when_several_fail(util):
    def fail(msg):
        raise RuntimeError(msg)

    jobs = [lambda: 0, lambda: fail("first"), lambda: fail("second")]
    with pytest.raises(RuntimeError, match="first"):
        util.process_asynchronously(jobs, "test")
