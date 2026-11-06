from datetime import datetime, timezone

import pandas as pd
import pytest

import pipeline_vehicle_daily as vd

RUN_AT = datetime(2026, 11, 6, tzinfo=timezone.utc)


def header_rows(range_label="01/10/2026 + 31"):
    r0 = [None] * 28
    r0[1] = range_label
    r1 = [None] * 28
    for c, v in {0: "วันที่", 1: "ฟลีท", 2: "ลำดับ", 3: "ผู้ว่าจ้าง", 4: "แพลนท์", 6: "หัว", 9: "คิว",
                 10: "หาง", 12: "คนขับรถ", 16: "สเตตัส", 18: "หัวหน้า", 19: "รถจีน",
                 20: "โอนรถข้ามแพลนท์", 22: "สรุปการเข้างาน", 24: "ลางาน", 25: "ซ่อม",
                 26: "หมายเหตุ", 27: "วัคซีน"}.items():
        r1[c] = v
    r2 = [None] * 28
    for c, v in {4: "รหัส", 5: "ชื่อ", 6: "ยี่ห้อ", 7: "เบอร์รถ", 8: "ทะเบียน", 10: "เบอร์รถ",
                 11: "ทะเบียน", 12: "สถานะ", 13: "รหัส", 14: "ชื่อ", 15: "เบอร์โทร", 16: "รถ",
                 17: "คนขับ", 20: "1", 21: "2", 22: "Logoff", 23: "Logon"}.items():
        r2[c] = v
    return [r0, r1, r2]


def data_row(day, plate, trailer=None, seq="1"):
    r = [None] * 28
    r[0], r[1], r[2], r[3] = day, "ฟลีททดสอบ", seq, "ลูกค้าทดสอบ"
    r[4], r[5], r[6], r[7], r[8] = "P01", "แพลนท์ทดสอบ", "HINO", "T-0001", plate
    if trailer:
        r[10], r[11] = "TR-01", trailer
    r[12], r[13], r[14] = "พจส", "690001", " คนขับ ทดสอบ "
    r[16], r[17] = "A", "A"
    return r


def frame(rows, range_label="01/10/2026 + 31"):
    return pd.DataFrame(header_rows(range_label) + rows, dtype=object)


def test_parse_report_maps_columns_and_keys():
    raw = frame([data_row("01/10/2026", "สบ.71-0264", trailer="สบ.71-0079"),
                 data_row("02/10/2026", "สบ.71-0264", seq="2")])
    docs = vd.parse_report(raw, "3", "ตู้ผ้าใบ", 2026, 10, "run1", RUN_AT)
    assert len(docs) == 2
    d = docs[0]
    assert d["_id"] == "3|01/10/2026|สบ.71-0264"
    assert d["date"] == datetime(2026, 10, 1)
    assert d["year_month"] == "2026-10"
    assert d["plate_norm"] == "71-0264"
    assert d["trailer_plate_norm"] == "71-0079"
    assert d["หาง_ทะเบียน"] == "สบ.71-0079"
    assert d["คนขับ_ชื่อ"] == "คนขับ ทดสอบ"  # trimmed
    assert d["ลำดับ"] == 1
    assert d["fleet_group_id"] == 3 and d["fleet_group_name"] == "ตู้ผ้าใบ"
    assert d["etl_run_id"] == "run1"
    assert docs[1]["trailer_plate_norm"] is None and docs[1]["หาง_ทะเบียน"] is None
    assert all("." not in k for k in d)  # Mongo reads dotted names as nested paths


def test_parse_report_empty_report_gives_no_docs():
    assert vd.parse_report(frame([]), "9", "TDM", 2026, 10, "r", RUN_AT) == []


def test_parse_report_drops_days_outside_month_and_dedupes():
    raw = frame([data_row("30/09/2026", "สบ.1"), data_row("01/10/2026", "สบ.1", seq="1"),
                 data_row("01/10/2026", "สบ.1", seq="9")])
    docs = vd.parse_report(raw, "1", "โม่ใหญ่", 2026, 10, "r", RUN_AT)
    assert len(docs) == 1 and docs[0]["ลำดับ"] == 9  # last row wins


def test_parse_report_rejects_changed_layout():
    raw = frame([data_row("01/10/2026", "สบ.1")])
    raw.iat[1, 27] = "อื่น ๆ"
    with pytest.raises(ValueError, match="หัวตาราง"):
        vd.parse_report(raw, "1", "x", 2026, 10, "r", RUN_AT)
    with pytest.raises(ValueError, match="คอลัมน์"):
        vd.parse_report(frame([]).iloc[:, :27], "1", "x", 2026, 10, "r", RUN_AT)


def test_months_default_and_override():
    assert vd.parse_months(None, datetime(2026, 11, 6)) == [(2026, 10), (2026, 11)]
    assert vd.parse_months("", datetime(2026, 1, 3)) == [(2025, 12), (2026, 1)]
    assert vd.parse_months("09/2026, 10/2026", datetime(2026, 11, 6)) == [(2026, 9), (2026, 10)]
    with pytest.raises(ValueError):
        vd.parse_months("2026-10", datetime(2026, 11, 6))
    with pytest.raises(ValueError):
        vd.parse_months("13/2026", datetime(2026, 11, 6))


def test_month_window():
    today = datetime(2026, 11, 6)
    assert vd.month_window(2026, 10, today) == ("01/10/2026", 31)
    assert vd.month_window(2026, 11, today) == ("01/11/2026", 6)
    assert vd.month_window(2024, 2, today) == ("01/02/2024", 29)
    assert vd.month_window(2026, 12, today) is None


def test_fleet_groups_from_form():
    html = ('<select name="fleet_group_id" class="x"><option value="">เลือก</option>'
            '<option value="4">BTG</option><option value="1" selected>โม่ใหญ่ </option></select>'
            '<select name="fleet_id"><option value="5">Acon</option></select>')
    assert vd.fleet_groups_from_form(html) == {"4": "BTG", "1": "โม่ใหญ่"}
    assert vd.fleet_groups_from_form("<html>login</html>") == {}


def test_is_excel_and_norm_plate():
    assert vd.is_excel(b"PK\x03\x04rest")
    assert not vd.is_excel(b"<!DOCTYPE html>")
    assert vd.norm_plate("สบ. 72-8814") == "72-8814"
    assert vd.norm_plate(None) is None
