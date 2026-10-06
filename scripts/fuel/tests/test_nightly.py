from datetime import datetime, timedelta

import pytest

import pipeline_fuel_nightly as nightly
from fake_mongo import FakeClient
from pipeline_fuel_nightly import besttech_run_in_progress, terminus_ready

NOW = datetime(2026, 10, 6, 21, 15)


def test_ready_against_recent_average():
    assert terminus_ready(424, [420, 430, 0, 410])
    assert not terminus_ready(150, [420, 430, 410])
    assert terminus_ready(210, [420, 420])


def test_ready_without_history():
    assert terminus_ready(5, [])
    assert not terminus_ready(0, [0, 0])


def test_crashed_0130_run_does_not_block_the_0415_catch_up():
    at_0415 = datetime(2026, 10, 6, 21, 15)                                   # UTC
    crashed = {"status": "running", "created_at": datetime(2026, 10, 6, 18, 35)}  # started 01:35 BKK
    assert not besttech_run_in_progress(crashed, at_0415)


def test_besttech_run_in_progress_only_when_recent_and_running():
    assert besttech_run_in_progress({"status": "running", "created_at": NOW - timedelta(hours=1)}, NOW)
    assert not besttech_run_in_progress({"status": "running", "created_at": NOW - timedelta(hours=5)}, NOW)
    assert not besttech_run_in_progress({"status": "success", "created_at": NOW - timedelta(minutes=5)}, NOW)
    assert not besttech_run_in_progress({"status": "running"}, NOW)
    assert not besttech_run_in_progress(None, NOW)


class FakeJob:
    finished: list = []

    def __init__(self, job_type, pipeline, meta=None):
        pass

    def finish(self, status="success", **extra):
        FakeJob.finished.append((status, extra))


def wire_nightly(monkeypatch, calls, terminus=None):
    """main() with every Mongo/HTTP step faked; `calls` records the order of the steps."""
    client = FakeClient()
    client["terminus"]["driving_log"].replace_one({}, {"_id": 1, "ทะเบียนพาหนะ": "71-0001", "วันที่": "05/10/2026"})
    FakeJob.finished = []

    def ingest(*args, **kwargs):
        calls.append("terminus")
        if terminus:
            raise terminus
        return 300

    monkeypatch.setattr(nightly, "MongoClient", lambda uri: client)
    monkeypatch.setattr(nightly, "JobLog", FakeJob)
    monkeypatch.setattr(nightly, "yesterday_bkk", lambda: datetime(2026, 10, 5))
    monkeypatch.setattr(nightly, "ingest_terminus_day", ingest)
    monkeypatch.setattr(nightly, "make_client", lambda: object())
    monkeypatch.setattr(nightly, "run_days", lambda *args, **kwargs: calls.append("besttech") or 130)
    monkeypatch.setattr(nightly, "run_day", lambda c, day: calls.append("events") or {"events": 7})
    monkeypatch.setenv("NIGHTLY_RETRIES", "0")


def test_nightly_order_terminus_then_besttech_catch_up_then_events(monkeypatch):
    calls: list = []
    wire_nightly(monkeypatch, calls)
    nightly.main()
    assert calls == ["terminus", "besttech", "events"]
    status, extra = FakeJob.finished[-1]
    assert status == "success" and extra["terminus_docs"] == 300 and extra["besttech_catchup_docs"] == 130
    assert extra["events"] == 7


def test_terminus_failure_still_runs_besttech_and_events_then_fails_the_run(monkeypatch):
    calls: list = []
    wire_nightly(monkeypatch, calls, terminus=RuntimeError("driving_log timeout"))
    with pytest.raises(RuntimeError):
        nightly.main()
    assert calls == ["terminus", "besttech", "events"]
    status, extra = FakeJob.finished[-1]
    assert status == "failed" and extra["terminus_error"] == "driving_log timeout" and extra["events"] == 7
