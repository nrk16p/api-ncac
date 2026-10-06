from datetime import date, datetime

import pytest

import pipeline_overspeed
from pipeline_overspeed import day_plates, overspeed_day, run_params
from test_overspeed_segments import readings

DAY = date(2026, 10, 5)


MISSING = object()


def mongo_path(doc, name):
    """Mongo reads a projected name as a path: "a.b" means doc["a"]["b"] — so a name with dots in it
    (ความเร็ว(กม./ชม.), ระยะทาง(กม.)) finds nothing."""
    value = doc
    for part in name.split("."):
        if not isinstance(value, dict) or part not in value:
            return MISSING
        value = value[part]
    return value


def project(doc, spec):
    out = {}
    for name, how in spec.items():
        if name == "_id":
            continue
        value = doc.get(how["$getField"], MISSING) if isinstance(how, dict) else mongo_path(doc, name)
        if value is not MISSING:
            out[name] = value
    return out


class FakeCursor(list):
    def hint(self, _index):
        return self


class FakeDrivingLog:
    """terminus.driving_log with Mongo's projection semantics for dotted names."""

    def __init__(self, rows):
        self.rows, self.matches, self.hints = rows, [], []

    def distinct(self, field, query):
        return [r[field] for r in self.rows if r["วันที่"] == query["วันที่"]] + [None, "  "]

    def _match(self, query):
        wanted = set(query["ทะเบียนพาหนะ"]["$in"])
        return [r for r in self.rows if r["วันที่"] == query["วันที่"] and r["ทะเบียนพาหนะ"] in wanted]

    def find(self, query, projection):
        self.matches.append(query)
        return FakeCursor(project(r, projection) for r in self._match(query))

    def aggregate(self, pipeline, hint=None):
        match, spec = pipeline[0]["$match"], pipeline[1]["$project"]
        self.matches.append(match)
        self.hints.append(hint)
        return iter([project(r, spec) for r in self._match(match)])


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



def test_speed_and_distance_are_read_despite_the_dots_in_their_names():
    log = FakeDrivingLog(readings("71-0001", "08:00:00", [80] * 10))
    target = FakeTarget()
    overspeed_day(log, target, DAY, batch_pause_s=0)
    assert target.inserts[0]["max_speed"] == 80
    assert target.inserts[0]["sum_distance_km"] == pytest.approx(2.5)
    assert log.hints == ["idx_date_plate_status_order_desc"]


def test_rows_without_speed_fail_instead_of_wiping_the_day():
    rows = [{k: v for k, v in r.items() if k != "ความเร็ว(กม./ชม.)"}
            for r in readings("71-0001", "08:00:00", [80] * 10)]
    target = FakeTarget()
    with pytest.raises(RuntimeError, match="without speed"):
        overspeed_day(FakeDrivingLog(rows), target, DAY, batch_pause_s=0)
    assert target.deletes == [] and target.inserts == []


def test_plates_filter_accepts_either_plate_form():
    log = FakeDrivingLog(readings("71-0001", "08:00:00", [80] * 10))
    overspeed_day(log, FakeTarget(), DAY, plates=["สบ.71-0001"], batch_pause_s=0)
    assert log.matches[0]["ทะเบียนพาหนะ"] == {"$in": ["71-0001"]}


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
