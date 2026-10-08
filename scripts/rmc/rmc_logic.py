"""CPAC RMC compensation — transform, payload and run rules (spec §10.2).

Ported from schedule_fuel/Cpac_compen/compensation_cpac/rmc_daily/rmc_daily.py with the same tiers
(site minutes 91–119 / 120–150 / > 150; ML 1/2/3, MS 0.5/1/1.5), the 2026-09-28 fix for numeric truck
codes read as "6496.0", and one new guard: a day that fetched trips but would send 0 rows fails instead
of silently advancing (the bug that lost 32 days).
"""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

MAX_CATCHUP_DAYS = 14
ML, MS = "รถโม่ใหญ่ 10 ล้อ", "รถโม่เล็ก 4 ล้อ"
SOURCE_COLUMNS = ["หมายเลข DP", "รหัสรถ", "ประเภทรถ", "ชื่อแพลนต์", "เวลาถึงไซต์งาน", "เวลาออกจากไซต์งาน", "เวลาออกตั๋ว"]
REQUIRED = ["TicketNo", "TruckPlateNo", "TruckPlateNo_clean", "PlantName", "truck_type", "date_ticket"]
STRING_COLS = ["TicketNo", "TruckNo", "TruckPlateNo", "TruckPlateNo_clean", "PlantName", "tier", "truck_type",
               "is_complete_trip"]
DATETIME_COLS = ["SiteMoveInAt", "SiteMoveOutAt", "TicketCreateAt"]


class RmcError(RuntimeError):
    """A day that must not be counted as done."""


def transform(raw: pd.DataFrame, vehicles: list[dict]) -> pd.DataFrame:
    """fleetlink trip report rows + vehicle mapping → compensation rows (rmc_daily.transform_data)."""
    df = raw[SOURCE_COLUMNS].copy()
    df["เวลาถึงไซต์งาน"] = pd.to_datetime(df["เวลาถึงไซต์งาน"], errors="coerce")
    df["เวลาออกจากไซต์งาน"] = pd.to_datetime(df["เวลาออกจากไซต์งาน"], errors="coerce")
    df["site_minutes"] = ((df["เวลาออกจากไซต์งาน"] - df["เวลาถึงไซต์งาน"]).dt.total_seconds() / 60).round(0)
    minutes = df["site_minutes"]
    df["tier"] = np.select(
        [minutes.isna(), minutes < 91, minutes.between(91, 119), minutes.between(120, 150), minutes > 150],
        ["no_tier", "tier_0", "tier_1", "tier_2", "tier_3"], default="tier_3")
    df["ประเภทรถ"] = df["ประเภทรถ"].astype(str).str.strip()
    kind, tier = df["ประเภทรถ"], df["tier"]
    df["compensate"] = np.select(
        [(tier == "tier_1") & (kind == ML), (tier == "tier_2") & (kind == ML), (tier == "tier_3") & (kind == ML),
         (tier == "tier_1") & (kind == MS), (tier == "tier_2") & (kind == MS), (tier == "tier_3") & (kind == MS)],
        [1, 2, 3, 0.5, 1, 1.5], default=0)
    # numeric-only codes come back from Excel as floats → "6496.0"; strip ".0" so the mapping matches
    df["รหัสรถ"] = df["รหัสรถ"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    vehicle_df = pd.DataFrame(vehicles)
    vehicle_df["code"] = pd.to_numeric(vehicle_df["code"], errors="coerce").fillna(0).astype(int).astype(str)
    df = df.merge(vehicle_df, how="left", left_on="รหัสรถ", right_on="code")
    df["truck_type"] = df["ประเภทรถ"].map({ML: "ML", MS: "MS"})
    df = df.rename(columns={
        "หมายเลข DP": "TicketNo", "รหัสรถ": "TruckNo", "plate_no": "TruckPlateNo",
        "plate_no_only": "TruckPlateNo_clean", "ชื่อแพลนต์": "PlantName", "เวลาถึงไซต์งาน": "SiteMoveInAt",
        "เวลาออกจากไซต์งาน": "SiteMoveOutAt", "site_minutes": "minutes_diff", "เวลาออกตั๋ว": "TicketCreateAt"})
    df["date_ticket"] = pd.to_datetime(df["TicketCreateAt"], errors="coerce").dt.date
    df["is_complete_trip"] = np.where(df["SiteMoveInAt"].notna() & df["SiteMoveOutAt"].notna(), "Y", "N")
    return df


def payload(df: pd.DataFrame) -> tuple[list[dict], int]:
    """Rows ready for API_PUSH (rmc_daily.push_api without the HTTP call) and how many were dropped."""
    df = df.replace([np.inf, -np.inf], np.nan)
    before = len(df)
    df = df.dropna(subset=REQUIRED).copy()
    dropped = before - len(df)
    for col in STRING_COLS:
        if col in df.columns:
            df[col] = df[col].astype(str)
    for col in DATETIME_COLS:
        df[col] = pd.to_datetime(df[col], errors="coerce").apply(lambda x: x.isoformat() if pd.notnull(x) else None)
    df["date_ticket"] = pd.to_datetime(df["date_ticket"]).dt.date.astype(str)
    df["minutes_diff"] = pd.to_numeric(df["minutes_diff"], errors="coerce").fillna(0)
    df["compensate"] = pd.to_numeric(df["compensate"], errors="coerce").fillna(0)
    df = df.astype(object).where(pd.notnull(df), None)
    return df.to_dict(orient="records"), dropped


def check_sending(day: date, fetched: int, sending: int) -> None:
    if fetched > 0 and sending == 0:
        raise RmcError(f"{day.isoformat()}: fetched {fetched} trips but 0 rows left to send "
                       "(vehicle mapping or required columns) — not marking the day done")


def pending_days(last_success: date, today: date, cap: int = MAX_CATCHUP_DAYS) -> list[date]:
    """Days after `last_success` through yesterday, at most `cap` of them (oldest first)."""
    days, d = [], last_success + timedelta(days=1)
    while d <= today - timedelta(days=1) and len(days) < cap:
        days.append(d)
        d += timedelta(days=1)
    return days


def _iso_day(text: str, name: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{name} must be YYYY-MM-DD, got {text!r}") from None


def run_mode(env: dict) -> dict:
    """DATE | START+END → manual run (state untouched); nothing → catch-up. DRY_RUN never pushes."""
    day, start, end = (env.get(k, "").strip() for k in ("DATE", "START", "END"))
    dry_run = env.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")
    if day and (start or end):
        raise ValueError("use either DATE or START + END, not both")
    if bool(start) != bool(end):
        raise ValueError("START and END must be given together")
    if day:
        d = _iso_day(day, "DATE")
        return {"kind": "manual", "start": d, "end": d, "dry_run": dry_run}
    if start:
        s, e = _iso_day(start, "START"), _iso_day(end, "END")
        if s > e:
            raise ValueError("START must be on or before END")
        if (e - s).days + 1 > MAX_CATCHUP_DAYS:
            raise ValueError(f"at most {MAX_CATCHUP_DAYS} days per run")
        return {"kind": "manual", "start": s, "end": e, "dry_run": dry_run}
    return {"kind": "catchup", "start": None, "end": None, "dry_run": dry_run}


def run_days(days, fetch, push, vehicles, dry_run=False, on_success=None) -> list[dict]:
    """fetch → transform → payload → guard → push, one day at a time; `on_success(day)` after each pushed day."""
    stats = []
    for d in days:
        raw = fetch(d)
        records, dropped = payload(transform(raw, vehicles))
        check_sending(d, len(raw), len(records))
        result = push(records) if records and not dry_run else None
        if on_success and not dry_run:
            on_success(d)
        stats.append({"day": d.isoformat(), "fetched": int(len(raw)), "dropped": int(dropped),
                      "rows": len(records), "pushed": bool(records) and not dry_run, "result": result})
    return stats
