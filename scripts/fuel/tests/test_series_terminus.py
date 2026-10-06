from datetime import date

from series_codec import decode_columns
from series_terminus import row_reading, terminus_day_docs

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


def test_silent_plates_get_no_data_docs():
    docs = {d["plate"]: d for d in terminus_day_docs(DAY, [], {}, silent_plates={"สบ.70-0001"})}
    assert docs["สบ.70-0001"]["coverage"]["status"] == "no_data"
