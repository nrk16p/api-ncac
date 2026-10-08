from datetime import date, datetime

import pytest

import pipeline_rmc
from pipeline_rmc import STATE_ID, VEHICLE_LIST, load_last_success, load_vehicles, save_last_success
from rmc_logic import ML, RmcError
from test_rmc_logic import VEHICLES, frame, trip


class Col:
    def __init__(self):
        self.docs, self.updates = [], []

    def find(self, query, projection):
        return [{k: v for k, v in d.items() if k != "_id"} for d in self.docs]

    def find_one(self, query):
        return next((d for d in self.docs if d.get("_id") == query["_id"]), None)

    def update_one(self, query, update, upsert=False):
        self.updates.append((query, update, upsert))


class DB(dict):
    def __getitem__(self, name):
        return self.setdefault(name, Col())


def test_vehicle_mapping_must_be_seeded():
    db = DB()
    with pytest.raises(RmcError, match="seed_rmc"):
        load_vehicles(db)
    db["rmc_vehicles"].docs = [{"_id": "x", "code": "6496"}]
    assert load_vehicles(db) == [{"code": "6496"}]


def test_state_is_read_and_written_as_one_doc():
    db = DB()
    with pytest.raises(RmcError, match="state.json"):
        load_last_success(db)
    db["etl_state"].docs = [{"_id": STATE_ID, "last_success_date": "2026-10-05"}]
    assert load_last_success(db) == date(2026, 10, 5)
    save_last_success(db, date(2026, 10, 6))
    query, update, upsert = db["etl_state"].updates[0]
    assert query == {"_id": STATE_ID} and upsert is True
    assert update["$set"]["last_success_date"] == "2026-10-06"


def test_vehicle_list_is_the_cpac_pipeline_list():
    assert len(VEHICLE_LIST) == 136 and len(set(VEHICLE_LIST)) == 136


class FakeJob:
    def __init__(self, job_type, pipeline, meta=None):
        self.meta, self.finished = meta, []

    def finish(self, status="success", **extra):
        self.finished.append((status, extra))


def test_bad_parameters_are_logged_as_a_failed_run(monkeypatch):
    jobs = []
    monkeypatch.setattr(pipeline_rmc, "JobLog", lambda *a: jobs.append(FakeJob(*a)) or jobs[-1])
    monkeypatch.setenv("DATE", "2026-10-05")
    monkeypatch.setenv("START", "2026-10-01")
    for name in ("END", "DRY_RUN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="not both"):
        pipeline_rmc.main()
    assert jobs[0].meta == {"date": "2026-10-05", "start": "2026-10-01"}
    assert jobs[0].finished[0][0] == "failed"


def run_main(monkeypatch, env, db, now=datetime(2026, 10, 6, 9, 0)):
    """main() with Mongo, fleetlink and the push API faked; returns the pushed batches."""
    pushed = []
    monkeypatch.setattr(pipeline_rmc, "JobLog", lambda *a: FakeJob(*a))
    monkeypatch.setattr(pipeline_rmc, "MongoClient", lambda uri: {"analytics": db})
    monkeypatch.setattr(pipeline_rmc, "now_bkk", lambda: now)
    monkeypatch.setattr(pipeline_rmc, "fetch_report", lambda d, url, ids: frame(trip("D1", 6496, ML, 100)))
    monkeypatch.setattr(pipeline_rmc, "push_records", lambda rows, url: pushed.append(rows) or {"created": len(rows)})
    for name in ("DATE", "START", "END", "DRY_RUN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    pipeline_rmc.main()
    return pushed


def seeded_db(last_success=None):
    db = DB()
    db["rmc_vehicles"].docs = [dict(v) for v in VEHICLES]
    if last_success:
        db["etl_state"].docs = [{"_id": STATE_ID, "last_success_date": last_success}]
    return db


def test_manual_runs_push_but_never_touch_the_state(monkeypatch):
    db = seeded_db()
    assert len(run_main(monkeypatch, {"DATE": "2026-10-05"}, db)) == 1
    assert len(run_main(monkeypatch, {"START": "2026-10-01", "END": "2026-10-02"}, db)) == 2
    assert db["etl_state"].updates == []


def test_catch_up_pushes_each_missed_day_and_advances_the_state(monkeypatch):
    db = seeded_db("2026-10-03")
    assert len(run_main(monkeypatch, {}, db)) == 2  # 10-04 and 10-05; today (10-06) is not done yet
    assert [u[1]["$set"]["last_success_date"] for u in db["etl_state"].updates] == ["2026-10-04", "2026-10-05"]


def test_dry_catch_up_neither_pushes_nor_saves(monkeypatch):
    db = seeded_db("2026-10-03")
    assert run_main(monkeypatch, {"DRY_RUN": "true"}, db) == []
    assert db["etl_state"].updates == []
