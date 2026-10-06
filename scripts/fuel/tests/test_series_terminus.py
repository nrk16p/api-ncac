from datetime import date

from fake_mongo import FakeClient, matches
from series_codec import decode_columns
from series_terminus import FIELDS, SPEED_FIELD, day_plates, ingest_terminus_day, row_reading, terminus_day_docs

DAY = date(2026, 10, 5)


def row(plate, t, fuel, status="จอดรถ", speed=0.0, latlng="13.6400, 100.5532", code="ME162"):
    return {"ทะเบียนพาหนะ": plate, "รหัสพาหนะ": code, "เวลา": t, "น้ำมัน": fuel,
            "ความเร็ว(กม./ชม.)": speed, "สถานะ": status, "พิกัด": latlng}


def test_row_reading_maps_fields():
    r = row_reading(row("71-8623", "08:00:10", 150.5, status="ดับเครื่อง"))
    assert (r.sec, r.fuel, r.engine, r.lat, r.lng) == (28810, 150.5, 0, 13.64, 100.5532)
    assert row_reading(row("71-8623", "08:00:10", 150.5, status="ความเร็วเกินกำหนด")).engine == 1


def test_row_reading_invalid_values():
    assert row_reading(row("71-8623", "nan", 1.0)) is None
    r = row_reading(row("71-8623", "08:00:00", float("nan"), latlng=float("nan")))
    assert r.fuel is None and r.lat is None and r.lng is None
    assert row_reading(row("71-8623", "08:00:00", 0.0)).fuel is None


def test_docs_per_plate_in_litres():
    rows = [row("71-8623", "08:00:10", 150.5), row("71-8623", "08:00:40", 150.7, status="รถวิ่ง", speed=20),
            row("71-8623", "08:01:05", float("nan"), status="ดับเครื่อง"),
            row("กว4506", "09:00:00", 60.0, code=None)]
    docs = {d["plate"]: d for d in terminus_day_docs(DAY, rows, {})}
    assert set(docs) == {"สบ.71-8623", "กว4506"}
    d = docs["สบ.71-8623"]
    assert d["fuel_unit"] == "dl" and d["truck_code"] == "ME162" and d["source"] == "terminus"
    cols = decode_columns(d["cols"], d["n"])
    assert cols["m"].tolist() == [480, 481]
    assert cols["fuel"].tolist() == [1506, -1]
    assert cols["speed"].tolist() == [20, 0]
    assert cols["engine"].tolist() == [1, 0]
    assert docs["กว4506"]["truck_code"] is None


def test_day_plates_skips_nulls_and_blanks():
    assert day_plates(["71-0002", None, float("nan"), "  ", "71-0001", 5]) == ["71-0001", "71-0002"]


def test_silent_plates_get_no_data_docs():
    docs = {d["plate"]: d for d in terminus_day_docs(DAY, [], {}, silent_plates={"สบ.70-0001"})}
    assert docs["สบ.70-0001"]["coverage"]["status"] == "no_data"


def test_reads_never_project_a_dotted_field_name():
    """Mongo reads a projected "ความเร็ว(กม./ชม.)" as the path ความเร็ว(กม → /ชม → ) and returns nothing:
    every Terminus minute came back with speed 0, so whole driving days looked parked."""
    assert [k for k in FIELDS if "." in k] == []
    assert FIELDS["speed"] == {"$getField": SPEED_FIELD} == {"$getField": "ความเร็ว(กม./ชม.)"}


class FakeDrivingLog:
    """distinct + aggregate($match, $project with 1 / $getField) — enough for ingest_terminus_day."""
    def __init__(self, rows):
        self.rows = rows

    def distinct(self, key, query):
        return sorted({r[key] for r in self.rows if matches(r, query)})

    def aggregate(self, pipeline, hint=None):
        rows = self.rows
        for stage in pipeline:
            if "$match" in stage:
                rows = [r for r in rows if matches(r, stage["$match"])]
            if "$project" in stage:
                rows = [{k: (r.get(v["$getField"]) if isinstance(v, dict) else r.get(k))
                         for k, v in stage["$project"].items() if v} for r in rows]
        return rows


def test_ingest_keeps_the_terminus_speed():
    rows = [dict(row("70-6302", "07:00:00", 160.0), วันที่="05/10/2026"),
            dict(row("70-6302", "07:27:56", 159.0, status="รถวิ่ง", speed=42.0), วันที่="05/10/2026")]
    db = FakeClient()["analytics"]
    ingest_terminus_day({"driving_log": FakeDrivingLog(rows)}, db, DAY, tanks={}, batch_pause_s=0)
    doc = db["gps_series"].find({})[0]
    assert int(decode_columns(doc["cols"], doc["n"])["speed"].max()) == 42
