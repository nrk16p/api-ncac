from datetime import date, datetime

import pytest

import pipeline_overspeed
from pipeline_overspeed import day_plates, overspeed_day, run_params
from test_overspeed_segments import readings

DAY = date(2026, 10, 5)


class FakeCursor(list):
    def hint(self, _index):
        return self


class FakeDrivingLog:
    def __init__(self, rows):
        self.rows, self.finds = rows, []

    def distinct(self, field, query):
        return [r[field] for r in self.rows if r["วันที่"] == query["วันที่"]] + [None, "  "]

    def find(self, query, projection):
        self.finds.append(query)
        wanted = set(query["ทะเบียนพาหนะ"]["$in"])
        return FakeCursor(r for r in self.rows if r["วันที่"] == query["วันที่"] and r["ทะเบียนพาหนะ"] in wanted)


class DeleteResult:
    deleted_count = 3


class FakeTarget:
    def __init__(self):
        self.deletes, self.inserts = [], []

    def delete_many(self, query):
        self.deletes.append(query)
        return DeleteResult()

    def insert_many(self, docs):
        self.inserts += docs


def test_day_replaces_rows_for_every_processed_plate():
    rows = readings("71-0001", "08:00:00", [80] * 10) + readings("71-0002", "08:00:00", [40] * 10)
    target = FakeTarget()
    stats = overspeed_day(FakeDrivingLog(rows), target, DAY, batch_pause_s=0)
    assert stats == {"day": "2026-10-05", "plates": 2, "segments": 1, "deleted": 3}
    delete = target.deletes[0]
    assert delete["vehicle"] == {"$in": ["71-0001", "71-0002"]}          # 71-0002 has no segment any more
    assert delete["start_datetime"] == {"$gte": datetime(2026, 10, 5), "$lt": datetime(2026, 10, 6)}
    assert [d["vehicle"] for d in target.inserts] == ["71-0001"]


def test_plates_filter_accepts_either_plate_form():
    log = FakeDrivingLog(readings("71-0001", "08:00:00", [80] * 10))
    overspeed_day(log, FakeTarget(), DAY, plates=["สบ.71-0001"], batch_pause_s=0)
    assert log.finds[0]["ทะเบียนพาหนะ"] == {"$in": ["71-0001"]}


def test_day_without_rows_deletes_nothing():
    target = FakeTarget()
    assert overspeed_day(FakeDrivingLog([]), target, DAY, batch_pause_s=0)["plates"] == 0
    assert target.deletes == [] and target.inserts == []


def test_day_plates_skips_vendor_nulls():
    assert day_plates(["71-0002", None, "  ", "71-0001", 7]) == ["71-0001", "71-0002"]


def test_run_params_defaults_and_validation():
    p = run_params({}, "05/10/2026")
    assert p["days"] == [DAY] and p["plates"] is None and p["target"] == "overspeed"
    assert (p["gap_minutes"], p["min_duration_min"], p["min_records"]) == (2.0, 2.0, 5)
    p = run_params({"START_DATE": "01/10/2026", "END_DATE": "02/10/2026", "PLATES": "71-0001, 71-0002",
                    "MIN_RECORDS": "8", "OVERSPEED_COLLECTION": "overspeed_smoke"}, "05/10/2026")
    assert len(p["days"]) == 2 and p["plates"] == ["71-0001", "71-0002"] and p["min_records"] == 8
    with pytest.raises(ValueError):
        run_params({"OVERSPEED_COLLECTION": "raw_engineon"}, "05/10/2026")
    with pytest.raises(ValueError):
        run_params({"GAP_MINUTES": "0"}, "05/10/2026")


class FakeJob:
    def __init__(self, job_type, pipeline, meta=None):
        self.meta, self.finished = meta, []

    def finish(self, status="success", **extra):
        self.finished.append((status, extra))


def test_bad_parameters_are_logged_as_a_failed_run(monkeypatch):
    jobs = []
    monkeypatch.setattr(pipeline_overspeed, "JobLog", lambda *a: jobs.append(FakeJob(*a)) or jobs[-1])
    monkeypatch.setenv("MIN_RECORDS", "0")
    with pytest.raises(ValueError, match="MIN_RECORDS"):
        pipeline_overspeed.main()
    assert jobs[0].meta["min_records"] == "0"
    assert jobs[0].finished[0][0] == "failed" and "MIN_RECORDS" in jobs[0].finished[0][1]["error"]
