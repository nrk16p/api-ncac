"""Overspeed segments for one plate-day (spec §10.2).

A faithful port of schedule_fuel/cal_overspeed/etl_overspeed_v4.py `build_segments()` with its
parameters made explicit, so /overspeed keeps reading exactly the same fields: readings that meet a
speed condition are grouped into segments wherever two of them are more than `gap_minutes` apart;
a segment is kept when it lasts more than `min_duration_min` and has at least `min_records` readings.
Datetimes stay naive Thai local time, as the old script stored them.
"""
import numpy as np
import pandas as pd

PLATE = "ทะเบียนพาหนะ"
SPEED = "ความเร็ว(กม./ชม.)"
DIST = "ระยะทาง(กม.)"
GROUPS = (
    (">70", lambda s: s > 70),
    ("60-70", lambda s: (s >= 60) & (s <= 70)),
)
OUTPUT_FIELDS = ["segment_id", "vehicle", "start_datetime", "end_datetime", "duration_minutes",
                 "sum_distance_km", "records", "avg_speed", "max_speed", "w_speed", "speed_group"]


def frame_from_rows(rows: list[dict]) -> pd.DataFrame:
    """driving_log rows (plate, วันที่, เวลา, speed, distance) → sorted frame with a `datetime` column."""
    if not rows:
        return pd.DataFrame(columns=[PLATE, "datetime", SPEED, DIST])
    df = pd.DataFrame(rows)
    for col in (SPEED, DIST):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    df["datetime"] = pd.to_datetime(df["วันที่"].astype(str) + " " + df["เวลา"].astype(str),
                                    format="%d/%m/%Y %H:%M:%S", errors="coerce")
    df = df.dropna(subset=["datetime"])
    df = df[df[PLATE].apply(lambda p: isinstance(p, str) and bool(p.strip()))]
    return df.sort_values([PLATE, "datetime"]).reset_index(drop=True)


def build_segments(df: pd.DataFrame, condition, speed_label: str, gap_minutes: float,
                   min_duration_min: float, min_records: int) -> pd.DataFrame:
    d = df.loc[condition].copy()
    if d.empty:
        return pd.DataFrame()
    d["dt_diff"] = d["datetime"].diff()
    d["segment_id"] = ((d["dt_diff"] > pd.Timedelta(minutes=gap_minutes)) | d["dt_diff"].isna()).cumsum()
    seg = d.groupby("segment_id", as_index=False).agg(
        vehicle=(PLATE, "first"),
        start_datetime=("datetime", "min"),
        end_datetime=("datetime", "max"),
        duration_minutes=("datetime", lambda x: (x.max() - x.min()).total_seconds() / 60),
        sum_distance_km=(DIST, "sum"),
        records=("datetime", "count"),
        avg_speed=(SPEED, "mean"),
        max_speed=(SPEED, "max"),
        w_speed=(SPEED, lambda x: np.average(x, weights=df.loc[x.index, DIST])
                 if df.loc[x.index, DIST].sum() > 0 else None),
    )
    seg = seg[(seg["duration_minutes"] > min_duration_min) & (seg["records"] >= min_records)]
    if seg.empty:
        return pd.DataFrame()
    seg["speed_group"] = speed_label
    return seg


def plate_segments(g: pd.DataFrame, gap_minutes: float = 2, min_duration_min: float = 2,
                   min_records: int = 5) -> list[dict]:
    """All overspeed segments of one plate-day as Mongo-ready dicts (both speed groups, by start time)."""
    parts = [build_segments(g, cond(g[SPEED]), label, gap_minutes, min_duration_min, min_records)
             for label, cond in GROUPS]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return []
    out = pd.concat(parts, ignore_index=True).sort_values("start_datetime").reset_index(drop=True)
    out = out.astype(object).where(pd.notnull(out), None)
    records = out[OUTPUT_FIELDS].to_dict("records")
    for rec in records:
        for key in ("start_datetime", "end_datetime"):
            rec[key] = pd.Timestamp(rec[key]).to_pydatetime()
        rec["segment_id"] = int(rec["segment_id"])
        rec["records"] = int(rec["records"])
        for key in ("duration_minutes", "sum_distance_km", "avg_speed", "max_speed", "w_speed"):
            if rec[key] is not None:
                rec[key] = float(rec[key])
    return records
