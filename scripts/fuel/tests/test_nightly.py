from datetime import datetime, timedelta

from pipeline_fuel_nightly import besttech_run_in_progress, terminus_ready

NOW = datetime(2026, 10, 6, 21, 15)


def test_ready_against_recent_average():
    assert terminus_ready(424, [420, 430, 0, 410])
    assert not terminus_ready(150, [420, 430, 410])
    assert terminus_ready(210, [420, 420])


def test_ready_without_history():
    assert terminus_ready(5, [])
    assert not terminus_ready(0, [0, 0])


def test_besttech_run_in_progress_only_when_recent_and_running():
    assert besttech_run_in_progress({"status": "running", "created_at": NOW - timedelta(hours=1)}, NOW)
    assert not besttech_run_in_progress({"status": "running", "created_at": NOW - timedelta(hours=5)}, NOW)
    assert not besttech_run_in_progress({"status": "success", "created_at": NOW - timedelta(minutes=5)}, NOW)
    assert not besttech_run_in_progress({"status": "running"}, NOW)
    assert not besttech_run_in_progress(None, NOW)
