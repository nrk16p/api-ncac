from datetime import date

import pandas as pd
import pytest

from rmc_logic import ML, MS, RmcError, check_sending, payload, pending_days, run_days, run_mode, transform

VEHICLES = [
    {"id": 1, "code": "6496", "plate_no": "71-6496 สบ.", "plate_no_only": "716496", "driver_name": "A",
     "driver_id": 10, "device_types_id": 2},
    {"id": 2, "code": "7001", "plate_no": "71-8635 สบ.", "plate_no_only": "718635", "driver_name": "B",
     "driver_id": 11, "device_types_id": 2},
]


def trip(dp, code, kind, minutes, ticket="2026-10-05 07:10:00"):
    arrive = pd.Timestamp("2026-10-05 08:00:00")
    leave = arrive + pd.Timedelta(minutes=minutes) if minutes is not None else pd.NaT
    return {"หมายเลข DP": dp, "รหัสรถ": code, "ประเภทรถ": kind, "ชื่อแพลนต์": "บางนา",
            "เวลาถึงไซต์งาน": arrive, "เวลาออกจากไซต์งาน": leave, "เวลาออกตั๋ว": ticket, "อื่นๆ": "x"}


def frame(*rows):
    return pd.DataFrame(list(rows))


@pytest.mark.parametrize("minutes,tier,comp_ml,comp_ms", [
    (90, "tier_0", 0, 0), (91, "tier_1", 1, 0.5), (119, "tier_1", 1, 0.5), (120, "tier_2", 2, 1),
    (150, "tier_2", 2, 1), (151, "tier_3", 3, 1.5), (None, "no_tier", 0, 0),
])
def test_tiers_and_compensation(minutes, tier, comp_ml, comp_ms):
    out = transform(frame(trip("D1", 6496, ML, minutes), trip("D2", 7001, f" {MS} ", minutes)), VEHICLES)
    assert list(out["tier"]) == [tier, tier]
    assert list(out["compensate"]) == [comp_ml, comp_ms]
    assert list(out["truck_type"]) == ["ML", "MS"]


def test_numeric_codes_read_as_floats_still_map():          # 2026-09-28 "6496.0" regression
    out = transform(frame(trip("D1", 6496.0, ML, 100)), VEHICLES)
    assert out.loc[0, "TruckNo"] == "6496" and out.loc[0, "TruckPlateNo"] == "71-6496 สบ."


def test_payload_converts_and_drops_unmapped_rows():
    out = transform(frame(trip("D1", 6496, ML, 100), trip("D2", "ZZ999", ML, 100), trip("D3", "7001", MS, None)),
                    VEHICLES)
    rows, dropped = payload(out)
    assert dropped == 1 and [r["TicketNo"] for r in rows] == ["D1", "D3"]
    first, third = rows
    assert first["SiteMoveInAt"] == "2026-10-05T08:00:00" and first["date_ticket"] == "2026-10-05"
    assert first["minutes_diff"] == 100 and first["compensate"] == 1 and first["is_complete_trip"] == "Y"
    assert third["SiteMoveOutAt"] is None and third["tier"] == "no_tier" and third["is_complete_trip"] == "N"
    assert "_id" not in first and first["driver_name"] == "A"


def test_zero_rows_after_mapping_is_an_error():
    with pytest.raises(RmcError, match="0 rows"):
        check_sending(date(2026, 10, 5), fetched=12, sending=0)
    check_sending(date(2026, 10, 5), fetched=0, sending=0)          # a day with no trips is fine


def test_pending_days_caps_at_14():
    assert pending_days(date(2026, 10, 4), date(2026, 10, 6)) == [date(2026, 10, 5)]
    assert pending_days(date(2026, 10, 5), date(2026, 10, 6)) == []
    gap = pending_days(date(2026, 9, 1), date(2026, 10, 6))
    assert len(gap) == 14 and gap[0] == date(2026, 9, 2)


def test_run_mode_validation():
    assert run_mode({})["kind"] == "catchup"
    m = run_mode({"DATE": "2026-10-05", "DRY_RUN": "true"})
    assert (m["kind"], m["start"], m["end"], m["dry_run"]) == ("manual", date(2026, 10, 5), date(2026, 10, 5), True)
    assert run_mode({"START": "2026-09-01", "END": "2026-09-14"})["end"] == date(2026, 9, 14)
    for bad in ({"DATE": "2026-10-05", "START": "2026-10-01"}, {"START": "2026-10-01"},
                {"START": "2026-10-05", "END": "2026-10-01"}, {"START": "2026-09-01", "END": "2026-09-15"},
                {"DATE": "05/10/2026"}):
        with pytest.raises(ValueError):
            run_mode(bad)


def test_run_days_advances_state_only_after_each_pushed_day():
    days = [date(2026, 10, 4), date(2026, 10, 5)]
    pushed, saved = [], []

    def fetch(d):
        if d == date(2026, 10, 5):
            return frame(trip("D9", "ZZ999", ML, 100))      # unmapped → 0 rows → guard
        return frame(trip("D1", 6496, ML, 100))

    with pytest.raises(RmcError):
        run_days(days, fetch, lambda rows: pushed.append(rows) or {"created": 1}, VEHICLES, on_success=saved.append)
    assert saved == [date(2026, 10, 4)] and len(pushed) == 1


def test_dry_run_never_pushes_or_saves():
    pushed, saved = [], []
    stats = run_days([date(2026, 10, 4)], lambda d: frame(trip("D1", 6496, ML, 100)),
                     lambda rows: pushed.append(rows), VEHICLES, dry_run=True, on_success=saved.append)
    assert pushed == [] and saved == [] and stats[0]["rows"] == 1 and stats[0]["pushed"] is False
