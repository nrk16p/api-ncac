"""Engine-On GPS pipeline — detects "Parking - Engine On" events from terminus.driving_log,
writes analytics.raw_engineon (events) + analytics.summary_engineon (per truck/day,
plant vs not-plant minutes).

Default range: yesterday (Bangkok). Override via env START_DATE / END_DATE (dd/mm/YYYY),
MAX_DISTANCE (meters, default 200) — passed through POST /pipeline/run/engineon body.

Ported from api-engineon app/etl_engineon.py (low-mem streaming version).
"""
import gc
import os
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pymongo import MongoClient, ReplaceOne

from common import MONGODB_URI, JobLog, log, yesterday_bkk

warnings.filterwarnings("ignore")


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    lat1 = np.radians(lat1)
    lon1 = np.radians(lon1)
    lat2 = np.radians(lat2)
    lon2 = np.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2.0 * R * np.arcsin(np.sqrt(a))


def _classify_voltage_type(v):
    try:
        if str(v).strip() == "เฟิร์มแวร์ไม่รองรับ":
            return "v1"
        float(v)
        return "v2"
    except Exception:
        return None


def _classify_engine_state(v, status):
    if status != "จอดรถ":
        return "Other"
    if pd.isna(v):
        return "Unknown"
    return "Parking - Engine On" if v >= 25.0 else "Parking - Engine Off"


def _split_latlng(series: pd.Series):
    arr = series.astype(str).str.split(",", n=1, expand=True).to_numpy()
    lat = pd.to_numeric(arr[:, 0], errors="coerce")
    lng = pd.to_numeric(arr[:, 1], errors="coerce")
    return lat, lng


def _mode_first(x: pd.Series):
    m = x.mode()
    return m.iloc[0] if not m.empty else x.iloc[0]


def process_engineon_data_optimized(
    mongo_uri: str,
    db_terminus: str = "terminus",
    db_atms: str = "atms",
    db_analytics: str = "analytics",
    start_date: str = "01/12/2025",
    end_date: str = "01/12/2025",
    max_distance: int = 200,
    save_raw: bool = True,
    save_summary: bool = True,
    debug_vehicle: str | None = None,
    mongo_batch_size: int = 1000,
    write_batch_size: int = 1000,
):
    client = MongoClient(mongo_uri)

    col_log = client[db_terminus]["driving_log"]
    col_plants = client[db_atms]["plants"]
    col_raw = client[db_analytics]["raw_engineon"]
    col_sum = client[db_analytics]["summary_engineon"]

    # -------- Plants --------
    plants = pd.DataFrame(list(col_plants.find({}, {"_id": 0})))
    if plants.empty:
        raise ValueError("❌ No plant data found")

    plants["Latitude"] = pd.to_numeric(plants["Latitude"], errors="coerce")
    plants["Longitude"] = pd.to_numeric(plants["Longitude"], errors="coerce")
    plants = plants.dropna(subset=["Latitude", "Longitude"]).reset_index(drop=True)

    p_lat = plants["Latitude"].to_numpy(dtype="float64", copy=False)
    p_lng = plants["Longitude"].to_numpy(dtype="float64", copy=False)
    p_code = plants["plant_code"].astype(str).to_numpy(copy=False)

    def nearest_plant_code(lat: float, lng: float):
        d = haversine(lat, lng, p_lat, p_lng)
        if d.size == 0:
            return None
        i = int(np.nanargmin(d))
        return p_code[i] if float(d[i]) <= max_distance else None

    # -------- Dates --------
    d0 = datetime.strptime(start_date, "%d/%m/%Y")
    d1 = datetime.strptime(end_date, "%d/%m/%Y")
    date_list = [
        (d0 + timedelta(days=i)).strftime("%d/%m/%Y")
        for i in range((d1 - d0).days + 1)
    ]

    projection = {
        "_id": 0,
        "ทะเบียนพาหนะ": 1,
        "วันที่": 1,
        "เวลา": 1,
        "Voltage": 1,
        "สถานะ": 1,
        "สถานที่": 1,
        "พิกัด": 1,
    }

    raw_ops: list[ReplaceOne] = []
    sum_ops: list[ReplaceOne] = []
    raw_written = 0
    sum_written = 0

    def flush_writes(force=False):
        nonlocal raw_ops, sum_ops, raw_written, sum_written

        if save_raw and raw_ops and (force or len(raw_ops) >= write_batch_size):
            col_raw.bulk_write(raw_ops, ordered=False)
            raw_written += len(raw_ops)
            raw_ops.clear()

        if save_summary and sum_ops and (force or len(sum_ops) >= max(200, write_batch_size // 10)):
            col_sum.bulk_write(sum_ops, ordered=False)
            sum_written += len(sum_ops)
            sum_ops.clear()

        gc.collect()

    def process_plate(plate: str, rows: list[dict], target_date: str):
        if not rows:
            return

        dfp = pd.DataFrame(rows)
        dfp = dfp.dropna(subset=["ทะเบียนพาหนะ", "เวลา"]).reset_index(drop=True)
        if dfp.empty:
            return

        # voltage type
        vtype = dfp["Voltage"].apply(_classify_voltage_type)
        version_type = (
            "v1" if (vtype == "v1").any()
            else "v2" if (vtype == "v2").any()
            else None
        )
        if not version_type:
            return

        # datetime
        dfp["datetime"] = pd.to_datetime(
            dfp["วันที่"].astype(str) + " " + dfp["เวลา"].astype(str),
            format="%d/%m/%Y %H:%M:%S",
            errors="coerce",
        )
        dfp = dfp.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
        if dfp.empty:
            return

        # engine state
        vnum = pd.to_numeric(dfp["Voltage"], errors="coerce")
        dfp["engine_state"] = [
            _classify_engine_state(v, s)
            for v, s in zip(vnum, dfp["สถานะ"].astype(str))
        ]

        dfp["prev_dt"] = dfp["datetime"].shift(1)
        dfp["prev_state"] = dfp["engine_state"].shift(1)
        dfp["prev_place"] = dfp["สถานที่"].shift(1)
        dfp["time_diff"] = (dfp["datetime"] - dfp["prev_dt"]).dt.total_seconds() / 60.0

        dfv = dfp.loc[
            (dfp["engine_state"] == "Parking - Engine On")
            & (dfp["prev_state"] == "Parking - Engine On")
            & (dfp["สถานที่"] == dfp["prev_place"])
            & (dfp["time_diff"] > 0)
            & (dfp["time_diff"] <= 5)
        ].copy()

        if dfv.empty:
            return

        # lat/lng
        dfv["lat"], dfv["lng"] = _split_latlng(dfv["พิกัด"])
        dfv["prev_lat"] = dfv["lat"].shift(1)
        dfv["prev_lng"] = dfv["lng"].shift(1)
        dfv["dist"] = haversine(dfv["prev_lat"], dfv["prev_lng"], dfv["lat"], dfv["lng"])

        # event split
        dfv["event_id"] = ((dfv["dist"] > max_distance) | dfv["dist"].isna()).astype(int).cumsum()

        events = (
            dfv.groupby("event_id", as_index=False)
            .agg(
                start_time=("prev_dt", "first"),
                end_time=("datetime", "last"),
                total_engine_on_min=("time_diff", "sum"),
                lat=("lat", "mean"),
                lng=("lng", "mean"),
                สถานที่=("สถานที่", _mode_first),
                count_records=("time_diff", "count"),
            )
        )

        if events.empty:
            return

        events["total_engine_on_hr"] = events["total_engine_on_min"] / 60.0

        # nearest plant
        nearest = []
        for r in events.itertuples(index=False):
            if pd.isna(r.lat) or pd.isna(r.lng):
                nearest.append(None)
            else:
                nearest.append(nearest_plant_code(float(r.lat), float(r.lng)))
        events["nearest_plant"] = nearest

        date_key = datetime.strptime(target_date, "%d/%m/%Y").strftime("%Y-%m-%d")

        # -------- RAW --------
        if save_raw:
            for rec in events.to_dict("records"):
                eid = rec["event_id"]
                _id = f"{plate}_{date_key}_{eid}"

                raw_ops.append(
                    ReplaceOne(
                        {"_id": _id},
                        {
                            "_id": _id,
                            "ทะเบียนพาหนะ": plate,
                            "date": target_date,
                            "version_type": version_type,
                            **rec,
                        },
                        upsert=True,
                    )
                )

        # -------- SUMMARY (plant + not_plant) --------
        if save_summary:
            plant_events = events[events["nearest_plant"].notna()]
            not_plant_events = events[events["nearest_plant"].isna()]

            plant_min = float(plant_events["total_engine_on_min"].sum()) if not plant_events.empty else 0.0
            not_plant_min = float(not_plant_events["total_engine_on_min"].sum()) if not not_plant_events.empty else 0.0

            if plant_min > 0 or not_plant_min > 0:
                sum_id = f"{plate}_{date_key}"

                sum_ops.append(
                    ReplaceOne(
                        {"_id": sum_id},
                        {
                            "_id": sum_id,
                            "ทะเบียนพาหนะ": plate,
                            "date": target_date,
                            "total_engine_on_min": plant_min,
                            "total_engine_on_hr": plant_min / 60.0,
                            "total_engine_on_min_not_plant": not_plant_min,
                            "total_engine_on_hr_not_plant": not_plant_min / 60.0,
                            "version_type": version_type,
                        },
                        upsert=True,
                    )
                )

                if debug_vehicle and plate == debug_vehicle:
                    log.info("🔍 %s %s | plant=%.2f min | not_plant=%.2f min",
                             plate, target_date, plant_min, not_plant_min)

        flush_writes(False)
        del dfp, dfv, events
        gc.collect()

    # -------- MAIN LOOP --------
    for target_date in date_list:
        cursor = (
            col_log.find({"วันที่": target_date}, projection)
            .sort("ทะเบียนพาหนะ", 1)
            .batch_size(mongo_batch_size)
        )

        current_plate = None
        buffer: list[dict] = []
        processed_plates = 0

        for doc in cursor:
            plate = doc.get("ทะเบียนพาหนะ")
            if not plate:
                continue

            if current_plate is None:
                current_plate = plate

            if plate != current_plate:
                process_plate(current_plate, buffer, target_date)
                buffer.clear()
                current_plate = plate
                processed_plates += 1

            buffer.append(doc)

        if buffer and current_plate:
            process_plate(current_plate, buffer, target_date)
            processed_plates += 1

        flush_writes(True)

        log.info("%s: processed_plates=%d, raw_upserts=%d, sum_upserts=%d",
                 target_date, processed_plates, raw_written, sum_written)

        raw_written = 0
        sum_written = 0

    log.info("🎉 engineon ETL completed")


def main():
    y = yesterday_bkk().strftime("%d/%m/%Y")
    start_date = os.getenv("START_DATE", y)
    end_date = os.getenv("END_DATE", y)
    max_distance = int(os.getenv("MAX_DISTANCE", "200"))

    job = JobLog("engineon", "engineon",
                 {"start_date": start_date, "end_date": end_date})
    try:
        process_engineon_data_optimized(
            mongo_uri=MONGODB_URI,
            start_date=start_date,
            end_date=end_date,
            max_distance=max_distance,
        )
        job.finish("success")
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
