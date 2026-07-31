"""Engine-On trip summary pipeline — joins atms.driver_cost_ticket (trips per driver/day)
with analytics.summary_engineon (engine-on minutes) into analytics.engineon_trip_summary.

Default month: month of yesterday (Bangkok). Override via env YEAR / MONTH /
VERSION_TYPE — passed through POST /pipeline/run/engineon_trip_summary body.

Ported from api-engineon app/etl_engineon_trip_summary.py (with the dropna and
_id-dedup fixes) + Mongo-side month filtering instead of full-collection reads.
"""
import os
import warnings
from typing import Optional

import numpy as np
import pandas as pd
from pymongo import MongoClient, ReplaceOne

from common import MONGODB_URI, JobLog, log, target_year_month

warnings.filterwarnings("ignore")

DB_ATMS = "atms"
DB_ANALYTICS = "analytics"

COL_DRIVER_COST = "driver_cost_ticket"
COL_VEHICLE_MASTER = "vehiclemaster"
COL_ENGINE_ON = "summary_engineon"
COL_OUTPUT = "engineon_trip_summary"

FUEL_RATE = {
    "Mixer 10 ล้อ": 2.0,  # ลิตร/ชั่วโมง
    "Mixer 6 ล้อ": 1.0,   # ลิตร/ชั่วโมง
}

# BA Rule: majority threshold ต่อเดือน (ถ่วงด้วย #trip)
MAJORITY_THRESHOLD = 0.7

SRC_ACTUAL = "ระบุตามชื่อตั๋วในวัน"
SRC_MAJORITY = "ระบุตามชื่อตั๋วที่มีมากที่สุดในเดือน"
SRC_MULTI = "อาจมีคนขับมากกว่า 1 ในเดือนจึงไม่ได้สามารถระบุได้"
SRC_UNASSIGNED = "ไม่มีข้อมูลจึงไม่ได้สามารถระบุได้"


def safe_to_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, dayfirst=True, errors="coerce")


def to_hms(total_minutes: Optional[float]) -> Optional[str]:
    if pd.isna(total_minutes):
        return None
    sec = int(round(float(total_minutes) * 60))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def clean_plate(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.replace("สบ.", "", regex=False)
        .str.replace("สบ", "", regex=False)
        .str.strip()
    )


def load_mongo_data(mongo_uri: str, year: int, month: int):
    """Month-bounded reads — filter server-side, not in pandas."""
    client = MongoClient(mongo_uri)
    mm = f"{month:02d}/{year}"
    df_driver_cost = pd.DataFrame(
        list(client[DB_ATMS][COL_DRIVER_COST].find({"mmyy": mm}))
    )
    df_vehicle = pd.DataFrame(list(client[DB_ATMS][COL_VEHICLE_MASTER].find()))
    # summary_engineon.date is a dd/mm/YYYY string
    df_engineon = pd.DataFrame(
        list(client[DB_ANALYTICS][COL_ENGINE_ON].find(
            {"date": {"$regex": f"/{month:02d}/{year}$"}}
        ))
    )
    return df_driver_cost, df_vehicle, df_engineon


def compute_monthly_majority_supervisor(
    df: pd.DataFrame,
    threshold: float = MAJORITY_THRESHOLD,
) -> pd.DataFrame:
    base = df.dropna(subset=["พจส1"]).copy()
    if base.empty:
        return pd.DataFrame(columns=["หัว", "Supervisor_majority", "majority_ratio"])

    agg = (
        base.groupby(["หัว", "พจส1"], as_index=False)["LDT_unique_count"]
        .sum()
        .rename(columns={"LDT_unique_count": "trip_sum"})
    )

    total = (
        agg.groupby("หัว", as_index=False)["trip_sum"]
        .sum()
        .rename(columns={"trip_sum": "total_trip"})
    )

    agg = agg.merge(total, on="หัว", how="left")
    agg["majority_ratio"] = np.where(
        agg["total_trip"].fillna(0) > 0,
        agg["trip_sum"] / agg["total_trip"],
        np.nan
    )

    majority = (
        agg.sort_values(["หัว", "majority_ratio", "trip_sum"], ascending=[True, False, False])
        .drop_duplicates(subset=["หัว"])
        .rename(columns={"พจส1": "Supervisor_majority"})
        [["หัว", "Supervisor_majority", "majority_ratio"]]
    )

    return majority


def aggregate_driver_cost(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ออก LDT_fmt"] = safe_to_datetime(df["ออก LDT_fmt"])
    df["หัว"] = clean_plate(df["หัว"])

    majority_df = compute_monthly_majority_supervisor(df, threshold=MAJORITY_THRESHOLD)
    df = df.merge(majority_df, on="หัว", how="left")

    def resolve(row):
        if pd.notna(row.get("พจส1")) and str(row.get("พจส1")).strip() != "":
            return pd.Series([row["พจส1"], SRC_ACTUAL])

        maj = row.get("Supervisor_majority")
        ratio = row.get("majority_ratio")

        if pd.notna(maj) and pd.notna(ratio) and float(ratio) >= MAJORITY_THRESHOLD:
            return pd.Series([maj, SRC_MAJORITY])

        if pd.notna(maj):
            return pd.Series([None, SRC_MULTI])

        return pd.Series([None, SRC_UNASSIGNED])

    df[["Supervisor_resolved", "driver_source"]] = df.apply(resolve, axis=1)

    # dropna=False: ห้าม drop แถวที่ Supervisor_resolved เป็น None (SRC_MULTI / SRC_UNASSIGNED)
    agg = (
        df.groupby(
            ["หัว", "Supervisor_resolved", "driver_source", "ออก LDT_fmt"],
            as_index=False,
            dropna=False,
        )["LDT_unique_count"].sum()
    )

    return agg


def aggregate_engineon(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = safe_to_datetime(df["date"])
    df["ทะเบียนพาหนะ"] = clean_plate(df["ทะเบียนพาหนะ"])

    agg = (
        df.groupby(
            ["ทะเบียนพาหนะ", "date", "version_type"],
            as_index=False
        )[["total_engine_on_min", "total_engine_on_min_not_plant"]]
        .sum()
    )

    agg["Duration_str"] = agg["total_engine_on_min"].apply(to_hms)
    agg["Duration_str_not_plant"] = agg["total_engine_on_min_not_plant"].apply(to_hms)

    return agg


def calculate_fuel(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["#trip"] = df["#trip"].fillna(0)

    df["สำรองเวลาโหลด"] = df["#trip"] * 30
    df["ส่วนต่าง"] = df["TotalMinutes"] - df["สำรองเวลาโหลด"]
    df["ส่วนต่าง"] = df["ส่วนต่าง"].clip(lower=0)
    df["ส่วนต่าง_hhmm"] = df["ส่วนต่าง"].apply(to_hms)

    df["จำนวนลิตร"] = (
        df["ประเภทยานพาหนะ"].map(FUEL_RATE).fillna(0)
        * (df["ส่วนต่าง"] / 60)
    )
    df["จำนวนลิตร"] = df["จำนวนลิตร"].round(2)

    if "total_engine_on_min_not_plant" in df.columns:
        df["not_plant_minutes"] = df["total_engine_on_min_not_plant"].fillna(0)
        df["not_plant_hhmm"] = df["not_plant_minutes"].apply(to_hms)
        df["not_plant_liter"] = (
            df["ประเภทยานพาหนะ"].map(FUEL_RATE).fillna(0)
            * (df["not_plant_minutes"] / 60)
        )
        df["not_plant_liter"] = df["not_plant_liter"].round(2)

    return df


def build_engineon_trip_summary(
    mongo_uri: str,
    year: int,
    month: int,
    version_type: Optional[str] = None,
) -> pd.DataFrame:

    df_driver, df_vehicle, df_engineon = load_mongo_data(mongo_uri, year, month)

    if not df_driver.empty and "ออก LDT_fmt" in df_driver.columns:
        df_driver["ออก LDT_fmt"] = safe_to_datetime(df_driver["ออก LDT_fmt"])
        df_driver = df_driver[
            (df_driver["ออก LDT_fmt"].dt.year == year)
            & (df_driver["ออก LDT_fmt"].dt.month == month)
        ]

    if not df_engineon.empty and "date" in df_engineon.columns:
        df_engineon["date"] = safe_to_datetime(df_engineon["date"])
        df_engineon = df_engineon[
            (df_engineon["date"].dt.year == year)
            & (df_engineon["date"].dt.month == month)
        ]

    if version_type and (not df_engineon.empty) and ("version_type" in df_engineon.columns):
        df_engineon = df_engineon[df_engineon["version_type"] == version_type]

    driver_agg = aggregate_driver_cost(df_driver) if not df_driver.empty else pd.DataFrame(
        columns=["หัว", "Supervisor_resolved", "driver_source", "ออก LDT_fmt", "LDT_unique_count"]
    )

    engine_agg = aggregate_engineon(df_engineon) if not df_engineon.empty else pd.DataFrame(
        columns=[
            "ทะเบียนพาหนะ", "date", "version_type",
            "total_engine_on_min", "total_engine_on_min_not_plant",
            "Duration_str", "Duration_str_not_plant"
        ]
    )

    merged = driver_agg.merge(
        engine_agg,
        left_on=["หัว", "ออก LDT_fmt"],
        right_on=["ทะเบียนพาหนะ", "date"],
        how="outer",
    )

    merged["TruckPlateNo"] = merged["หัว"].combine_first(merged["ทะเบียนพาหนะ"])
    merged["Date"] = merged["ออก LDT_fmt"].combine_first(merged["date"])

    for c in ["หัว", "ทะเบียนพาหนะ", "ออก LDT_fmt", "date"]:
        if c in merged.columns:
            merged = merged.drop(columns=[c])

    merged = merged.rename(
        columns={
            "Supervisor_resolved": "Supervisor",
            "LDT_unique_count": "#trip",
            "total_engine_on_min": "TotalMinutes",
        }
    )

    if "driver_source" not in merged.columns:
        merged["driver_source"] = np.nan

    if not df_vehicle.empty and "ทะเบียน" in df_vehicle.columns:
        df_vehicle = df_vehicle.copy()
        df_vehicle["ทะเบียน"] = clean_plate(df_vehicle["ทะเบียน"])

        merged = merged.merge(
            df_vehicle,
            left_on="TruckPlateNo",
            right_on="ทะเบียน",
            how="left",
        )

    merged = calculate_fuel(merged)

    # --- _id for upsert (กัน NaT)
    merged["Date"] = safe_to_datetime(merged["Date"])
    merged["_id"] = (
        merged["TruckPlateNo"].astype(str)
        + "_"
        + merged["Date"].dt.strftime("%Y-%m-%d").fillna("NA")
    )

    # กัน _id ชนกัน (เช่น รถคันเดียวมี พจส. มากกว่า 1 คนในวันเดียว)
    # แถวซ้ำได้ suffix _d1, _d2, ... — ฝั่ง frontend (raw-engineon) strip suffix นี้ก่อน match
    dup_seq = merged.groupby("_id").cumcount()
    merged["_id"] = np.where(
        dup_seq > 0,
        merged["_id"] + "_d" + dup_seq.astype(str),
        merged["_id"],
    )

    merged["year"] = year
    merged["month"] = month

    return merged.reset_index(drop=True)


def save_engineon_trip_summary(mongo_uri: str, df: pd.DataFrame):
    if df is None or df.empty:
        return 0

    client = MongoClient(mongo_uri)
    col = client[DB_ANALYTICS][COL_OUTPUT]

    # ลบข้อมูลเดือนนั้นทิ้งก่อน upsert — กันแถวเก่าค้าง
    year = int(df["year"].iloc[0])
    month = int(df["month"].iloc[0])
    col.delete_many({"year": year, "month": month})

    records = df.to_dict("records")
    ops = [ReplaceOne({"_id": r["_id"]}, r, upsert=True) for r in records]

    if not ops:
        return 0

    col.bulk_write(ops, ordered=False)
    return len(ops)


def main():
    year, month = target_year_month()
    version_type = os.getenv("VERSION_TYPE") or None

    job = JobLog("engineon_trip_summary", "engineon_trip_summary",
                 {"year": year, "month": month, "version_type": version_type})
    try:
        df = build_engineon_trip_summary(MONGODB_URI, year, month, version_type)
        n = save_engineon_trip_summary(MONGODB_URI, df)
        log.info("trip summary %02d/%d: %d rows", month, year, n)
        job.finish("success", rows=n)
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
