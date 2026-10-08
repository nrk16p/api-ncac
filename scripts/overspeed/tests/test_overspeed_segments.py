from datetime import datetime, timedelta

import pytest

from overspeed_segments import OUTPUT_FIELDS, PLATE, frame_from_rows, plate_segments


def readings(plate, start, speeds, step_s=30, dist=0.25):
    t0 = datetime.strptime(f"05/10/2026 {start}", "%d/%m/%Y %H:%M:%S")
    out = []
    for i, speed in enumerate(speeds):
        t = t0 + timedelta(seconds=step_s * i)
        out.append({PLATE: plate, "วันที่": t.strftime("%d/%m/%Y"), "เวลา": t.strftime("%H:%M:%S"),
                    "ความเร็ว(กม./ชม.)": speed, "ระยะทาง(กม.)": dist})
    return out


def segs(rows, **kw):
    df = frame_from_rows(rows)
    return plate_segments(df[df[PLATE] == rows[0][PLATE]], **kw)


def test_both_speed_groups_become_segments():
    out = segs(readings("71-0001", "08:00:00", [75] * 10 + [65] * 10))
    assert [s["speed_group"] for s in out] == [">70", "60-70"]
    first = out[0]
    assert set(first) == set(OUTPUT_FIELDS)
    assert first["vehicle"] == "71-0001" and first["records"] == 10
    assert first["start_datetime"] == datetime(2026, 10, 5, 8, 0, 0)
    assert first["end_datetime"] == datetime(2026, 10, 5, 8, 4, 30)
    assert first["duration_minutes"] == pytest.approx(4.5)
    assert first["avg_speed"] == 75 and first["max_speed"] == 75


def test_gap_longer_than_two_minutes_splits():
    rows = readings("71-0001", "08:00:00", [80] * 8) + readings("71-0001", "08:09:00", [80] * 8)
    out = segs(rows)
    assert len(out) == 2 and [s["segment_id"] for s in out] == [1, 2]


def test_short_or_sparse_runs_are_dropped():
    assert segs(readings("71-0001", "08:00:00", [80] * 4)) == []                 # 4 records < 5
    assert segs(readings("71-0001", "08:00:00", [80] * 6, step_s=10)) == []      # 50 s ≤ 2 min


def test_distance_weighting_matches_old_script():
    weighted = segs(readings("71-0001", "08:00:00", [72, 72, 72, 90, 90, 90], step_s=60, dist=1.0))[0]
    assert weighted["sum_distance_km"] == pytest.approx(6.0) and weighted["w_speed"] == pytest.approx(81.0)
    zero = segs(readings("71-0001", "08:00:00", [80] * 6, step_s=60, dist=0.0))[0]
    assert zero["sum_distance_km"] == 0 and zero["w_speed"] is None
    rows = readings("71-0001", "08:00:00", [80] * 6, step_s=60, dist=1.0)
    rows[2]["ระยะทาง(กม.)"] = float("nan")                                      # one missing distance
    gappy = segs(rows)[0]
    assert gappy["sum_distance_km"] == pytest.approx(5.0) and gappy["w_speed"] is None


def test_frame_from_rows_drops_bad_rows_and_sorts():
    rows = readings("71-0002", "09:00:00", [61, 62]) + readings("71-0001", "08:00:00", [61])
    rows.append({PLATE: None, "วันที่": "05/10/2026", "เวลา": "08:00:00", "ความเร็ว(กม./ชม.)": 70, "ระยะทาง(กม.)": 0})
    rows.append({PLATE: "71-0003", "วันที่": "05/10/2026", "เวลา": "bad", "ความเร็ว(กม./ชม.)": "70", "ระยะทาง(กม.)": 0})
    df = frame_from_rows(rows)
    assert list(df[PLATE]) == ["71-0001", "71-0002", "71-0002"]
    assert frame_from_rows([]).empty
