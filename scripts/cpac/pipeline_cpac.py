"""
CPAC pipeline — all tasks in memory, output to MongoDB.
Replaces task_1 → task_2 → task_3 → task_4 → task_5 → task_6 → task_7 chain.
Static ref (zone) still read from disk at raw_data/zone.xlsx.
"""
import io
import json
import logging
import os
import random
import time
import warnings
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import urllib3
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_DIR = Path(__file__).parent
PIPELINE = "cpac"
BASE_ATMS = "https://www.mena-atms.com"
VEHICLE_MASTER_URL = (
    f"{BASE_ATMS}/veh/vehicle/index.export/"
    "?page=1&order_by=v.code%20asc&search-toggle-status=&order_by=v.code%20asc"
)
AUTH_URL = "https://api-cpac.scg.com/auth/oauth2/token"
REPORT_URL = "https://api-cpac.scg.com/e-suppliers/external/api/report-download/search"

ATMS_USERNAME = os.getenv("ATMS_USERNAME")
ATMS_PASSWORD = os.getenv("ATMS_PASSWORD")
CPAC_USERNAME = os.getenv("CPAC_USERNAME")
CPAC_PASSWORD = os.getenv("CPAC_PASSWORD")
CPAC_AUDIENT = os.getenv("CPAC_AUDIENT")
CPAC_SIGNATURE = os.getenv("CPAC_SIGNATURE")
POST_URL = os.getenv("POST_URL")
MONGODB_URI = os.getenv("MONGODB_URI")

CUSTOMER_IDS = ["139"]
VEHICLE_LIST = [
    55046,55047,55075,55085,55086,55091,55092,55093,55094,55095,55096,55097,
    55533,55534,55535,55686,55687,55688,55689,55690,55938,55939,55945,55947,
    55948,57178,57179,57180,57181,57182,57184,57185,57343,57344,58266,58267,
    40730,40733,46350,46363,46368,46371,46372,46409,46420,46424,46445,46458,
    46460,46892,46893,46894,46895,47066,47067,47068,47069,47114,47115,47116,
    47117,47119,47120,48650,48651,48652,48653,49427,49428,49429,49430,49431,
    49432,52603,52604,52774,52775,52829,53657,53658,53659,53660,53663,53664,
    53665,53666,53671,53672,53673,53674,53675,53715,53716,53717,53718,53719,
    53745,53746,53747,53748,53749,53750,53751,53752,53753,53754,53755,53756,
    53757,53758,53759,53821,53822,53823,53824,53825,53826,53827,53828,53830,
    54291,54292,54293,54294,54295,54377,54576,54577,54578,54579,54580,55004,
    55005,55006,55016,55017,
]
VEHICLE_VISIBILITY = ",".join(str(v) for v in VEHICLE_LIST)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
]

warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Step 1: CPAC REST API ─────────────────────────────────────────────────────

def _get_cpac_token() -> str:
    resp = requests.post(AUTH_URL, data={"grant_type": "client_credentials"},
                         auth=(CPAC_USERNAME, CPAC_PASSWORD), timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_cpac(target_date: str) -> pd.DataFrame:
    """target_date: dd-mm-YYYY format."""
    token = _get_cpac_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "x-audient": CPAC_AUDIENT,
        "x-signature": CPAC_SIGNATURE,
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://portal.cpac.co.th",
        "Accept": "application/json",
    }
    for attempt in range(1, 4):
        try:
            r = requests.get(REPORT_URL, headers=headers,
                             params={"dateFrom": target_date, "dateTo": target_date}, timeout=60)
            if r.status_code == 200:
                data = r.json().get("data", [])
                df = pd.DataFrame(data)
                log.info(f"CPAC report: {len(df)} rows")
                return df
            log.warning(f"CPAC API attempt {attempt}: {r.status_code}")
        except requests.RequestException as e:
            log.warning(f"CPAC API attempt {attempt}: {e}")
        if attempt < 3:
            time.sleep(5 * attempt)
    raise RuntimeError("All CPAC API attempts failed")


# ── Step 2: Fleetlink API ─────────────────────────────────────────────────────

def fetch_fleetlink(target_date_ymd: str) -> pd.DataFrame:
    """target_date_ymd: YYYY-MM-DD format."""
    if not POST_URL:
        log.warning("POST_URL not set — returning empty fleetlink DataFrame")
        return pd.DataFrame()

    payload = {
        "date_start": f"{target_date_ymd} 00:00:00",
        "date_end": f"{target_date_ymd} 23:59:59",
        "type": "vehicle",
        "vehicle_list": VEHICLE_LIST,
        "plants_list": ["all"],
        "company_id": 1231,
        "vehicle_visibility": VEHICLE_VISIBILITY,
        "site_id": "",
        "type_file": "excel",
    }
    with requests.Session() as s:
        resp = s.post(POST_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=180)
        resp.raise_for_status()
        result = resp.json()
        file_url = result.get("result")
        if not file_url:
            raise ValueError(f"No file URL in fleetlink response: {result}")
        r2 = s.get(file_url, timeout=180)
        r2.raise_for_status()
        df = pd.read_excel(io.BytesIO(r2.content), skiprows=3)

    cols = [
        "หมายเลข DP", "รหัสรถ", "รหัสคนขับ", "คนขับรถ", "ประเภทรถ",
        "รหัสแพลนต์", "ชื่อแพลนต์", "รหัสไซต์งาน", "ชื่อไซต์งาน",
        "ระยะทางจากแพลนต์ถึงไซต์งาน (กิโลเมตร) (คำนวณสิ้นวัน)",
        "ระยะทางจาก Google (Bluenet)", "เวลาออกตั๋ว", "เวลาออกจากโรงงาน",
        "เวลาถึงไซต์งาน", "เริ่มเทปูนในหน่วยงาน", "จบการเทปูนในหน่วยงาน",
        "ปริมาณที่เท (คิว)", "เวลาออกจากไซต์งาน", "รหัสโรงงานที่รถกลับ",
        "โรงงานที่รถกลับ", "เวลากลับเข้าโรงงาน", "รหัสยกเลิกตั๋ว",
        "สถานะ", "สถานะตั๋ว",
        "ระยะทางขากลับแพลนต์ (กิโลเมตร) (คำนวณสิ้นวัน)",
    ]
    existing = [c for c in cols if c in df.columns]
    df = df[existing]
    log.info(f"Fleetlink: {len(df)} rows")
    return df


# ── ATMS login ────────────────────────────────────────────────────────────────

def atms_login(session: requests.Session) -> None:
    session.post(
        f"{BASE_ATMS}/account/user/login",
        data={"username": ATMS_USERNAME, "password": ATMS_PASSWORD,
              "submit": "login", "next": ""},
        verify=False, timeout=30,
    ).raise_for_status()
    log.info("ATMS login OK")


# ── Step 3: ATMS vehicle daily ────────────────────────────────────────────────

def fetch_vehicle_daily(session: requests.Session, target_date: str) -> pd.DataFrame:
    url = f"{BASE_ATMS}/report/print.out/print.excel/type/vehicle.daily.transaction"
    frames = []
    for fgid in ["1", "2"]:
        r = session.post(url, data={
            "fleet_group_id": fgid, "fleet_id": "", "t_date": target_date,
            "num_of_day": "1", "submit": "พิมพ์",
            "display_type": "multiple-day", "report_type": "vehicle.daily.transaction",
        }, verify=False, timeout=120)
        r.raise_for_status()
        if r.content[:4] == b"PK\x03\x04" or r.content[:2] == b"\xd0\xcf":
            df = pd.read_excel(io.BytesIO(r.content), sheet_name=0, dtype=str, skiprows=1)
            df["fleet_group_id"] = fgid
            frames.append(df)
        else:
            log.warning(f"Vehicle daily fleet_group_id={fgid} returned HTML — no data or no access")

    if not frames:
        log.warning("No vehicle daily data — continuing without driver info")
        return pd.DataFrame()

    final_df = pd.concat(frames, ignore_index=True)
    keep = [c for c in ["วันที่", "ฟลีท", "แพลนท์", "หัว", "Unnamed: 7", "Unnamed: 8",
                         "Unnamed: 13", "Unnamed: 14", "Unnamed: 15", "สเตตัส", "คนขับรถ"]
            if c in final_df.columns]
    final_df = final_df[keep]
    rename_map = {
        "หัว": "ยี่ห้อ",
        "Unnamed: 7": "เบอร์รถ",
        "Unnamed: 8": "ทะเบียน",
        "Unnamed: 13": "รหัส",
        "Unnamed: 14": "ชื่อ",
        "Unnamed: 15": "เบอร์โทร",
    }
    final_df = final_df.rename(columns={k: v for k, v in rename_map.items() if k in final_df.columns})
    if "วันที่" in final_df.columns:
        final_df = final_df.dropna(subset=["วันที่"])
    if "ทะเบียน" in final_df.columns:
        final_df["ทะเบียน"] = final_df["ทะเบียน"].str.replace("สบ.", "", regex=False)
    log.info(f"Vehicle daily: {len(final_df)} rows")
    return final_df


# ── Step 4: ATMS vehiclemaster ────────────────────────────────────────────────

def fetch_vehiclemaster(session: requests.Session) -> pd.DataFrame:
    resp = session.get(VEHICLE_MASTER_URL, verify=False, timeout=60)
    resp.raise_for_status()
    if not resp.encoding:
        resp.encoding = resp.apparent_encoding
    tables = pd.read_html(StringIO(resp.text), displayed_only=False)
    if not tables:
        log.warning("Vehicle master: no tables found in HTML")
        return pd.DataFrame()
    df = max(tables, key=lambda t: t.shape[0] * t.shape[1])
    df.columns = df.columns.map(lambda c: str(c).strip())
    df = df.loc[:, ~df.columns.astype(str).str.contains(r"^Unnamed", case=False)]
    df = df.astype(str)
    keep_cols = ["ทะเบียน", "เลขรถ", "ประเภทรถร่วม", "ประเภทยานพาหนะ", "ประเภทยานพาหนะเพิ่มเติม"]
    df = df[[c for c in keep_cols if c in df.columns]]
    log.info(f"Vehicle master: {len(df)} rows")
    return df


# ── Step 5: ATMS ship.to ─────────────────────────────────────────────────────

def fetch_ship_to(session: requests.Session) -> pd.DataFrame:
    frames = []
    for cid in CUSTOMER_IDS:
        r = session.post(
            f"{BASE_ATMS}/report/excel/index.excel/type/ship.to",
            data={"customer_id": cid, "status": "A", "from_valid_date": "01/01/2025",
                  "submit": "พิมพ์", "display_type": "multiple-day", "report_type": "ship.to"},
            verify=False, timeout=600,
        )
        r.raise_for_status()
        df = pd.read_excel(io.BytesIO(r.content), sheet_name=0, dtype=str, skiprows=1)
        df["customer_id"] = cid
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    log.info(f"Ship.to: {len(df)} rows")
    return df


# ── Step 6: Build LDT merged output ──────────────────────────────────────────

def _convert_dptime(series: pd.Series) -> pd.Series:
    """Convert dpTime/dpDate from ms-int OR existing datetime to tz-aware Bangkok time."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_datetime(series, unit="ms", utc=True).dt.tz_convert("Asia/Bangkok")
    converted = pd.to_datetime(series, errors="coerce")
    if converted.dt.tz is None:
        converted = converted.dt.tz_localize("UTC")
    return converted.dt.tz_convert("Asia/Bangkok")


def build_ldt(
    cpac: pd.DataFrame,
    fleetlink: pd.DataFrame,
    vehicledaily: pd.DataFrame,
    vehiclemaster: pd.DataFrame,
    shipto: pd.DataFrame,
) -> pd.DataFrame:
    cpac = cpac[["plantNo", "dpNo", "dpDate", "dpTime", "carNo", "driverName",
                  "siteCode", "siteName", "quantity", "distanceCode"]].copy()

    for col in ["dpDate", "dpTime"]:
        if col in cpac.columns:
            cpac[col] = _convert_dptime(cpac[col])

    cpac["แพล้นท์"] = cpac["dpNo"].astype(str).str[:4]
    cpac["วันที่"] = cpac["dpTime"]
    cpac["วันที่ครบกำหนด"] = cpac["dpTime"]
    cpac["เลขที่ตั๋วเพิ่ม 1"] = cpac["plantNo"]
    cpac["เลขที่ตั๋วเพิ่ม 2"] = cpac["distanceCode"]
    cpac["เวลาออกเดินทาง"] = cpac["dpTime"]
    for col in ["วันเวลาอ้างอิง 1", "วันเวลาอ้างอิง 2", "วันเวลาอ้างอิง 3", "วันเวลาอ้างอิง 4"]:
        cpac[col] = cpac["dpTime"]
    cpac["วันเวลาลงสินค้า"] = cpac["dpTime"]
    cpac["วันเวลาปิด LDT"] = cpac["dpTime"]
    cpac["นน ต้นทาง"] = cpac["quantity"]
    cpac["LDT"] = cpac["dpNo"]
    cpac["Ship To"] = cpac["dpNo"].astype(str).str[:4] + cpac["siteCode"]

    for col, val in [
        ("Type", "single drop"), ("ผลิตภัณฑ์", "คอนกรีตผสมเสร็จ"), ("dropoffs", ""),
        ("ประเภทวิ่ง", "legacy"), ("ประเภทการขนส่งขากลับ", ""), ("ประเภทการขนส่ง", "heavy"),
        ("Service Parameter A", ""), ("Service Parameter B", ""),
        ("แพล้นท์โอเนย้าย", ""), ("ผู้จัดส่งร่วม", ""), ("ทะเบียนหาง", ""),
        ("หมายเหตุ", ""), ("วิ่งแทนรถทะเบียน", ""), ("สาขา", "LB"),
    ]:
        cpac[col] = val

    # Fleetlink: select relevant columns
    fl_cols = [c for c in ["หมายเลข DP", "เวลาถึงไซต์งาน", "เวลาออกจากไซต์งาน"]
               if c in fleetlink.columns]
    fl = fleetlink[fl_cols].copy()
    for col in ["เวลาถึงไซต์งาน", "เวลาออกจากไซต์งาน"]:
        if col in fl.columns:
            fl[col] = pd.to_datetime(fl[col], errors="coerce").dt.tz_localize(
                "Asia/Bangkok", ambiguous="NaT", nonexistent="NaT"
            )

    cpac["dpNo"] = cpac["dpNo"].astype(str)
    fl["หมายเลข DP"] = fl["หมายเลข DP"].astype(str)
    cpac["carNo"] = cpac["carNo"].astype(str)
    if not vehicledaily.empty:
        vehicledaily["เบอร์รถ"] = vehicledaily["เบอร์รถ"].astype(str)

    merged = cpac.merge(fl, how="inner", left_on="dpNo", right_on="หมายเลข DP")
    if not vehicledaily.empty:
        merged = merged.merge(vehicledaily, how="inner", left_on="carNo", right_on="เบอร์รถ")

    if "คนขับรถ" in merged.columns:
        merged["ประเภทรถร่วม"] = ""
        merged.loc[merged["คนขับรถ"] == "พจส", "ประเภทรถร่วม"] = "OT-MT01"
        merged.loc[merged["คนขับรถ"] == "พจร", "ประเภทรถร่วม"] = "OT-MT02"
    else:
        merged["ประเภทรถร่วม"] = ""

    merged["รหัส พจส 1"] = merged.get("รหัส", "")
    merged["รหัส พจส 2"] = ""
    if "ทะเบียน" in merged.columns:
        merged["ทะเบียนหัว"] = "สบ." + merged["ทะเบียน"].astype(str)
    else:
        merged["ทะเบียนหัว"] = ""

    if not vehiclemaster.empty:
        vehiclemaster = vehiclemaster.copy()
        merged = merged.merge(vehiclemaster, how="inner", left_on="carNo", right_on="เลขรถ")
        if "ประเภทยานพาหนะ" in merged.columns:
            merged["เส้นทาง"] = merged["ประเภทยานพาหนะ"].apply(
                lambda x: "CPAC L" if x == "Mixer 10 ล้อ" else "6 ล้อ"
            )
            merged["บริการ"] = merged["เส้นทาง"].apply(
                lambda x: "M026" if x == "6 ล้อ" else "M025 "
            )
        else:
            merged["เส้นทาง"] = ""
            merged["บริการ"] = ""
    else:
        merged["เส้นทาง"] = ""
        merged["บริการ"] = ""

    merged["Ship To"] = merged["Ship To"].astype(str)
    if not shipto.empty:
        shipto = shipto.copy()
        shipto["รหัส"] = shipto["รหัส"].astype(str)
        merged = merged.merge(shipto, how="left", left_on="Ship To", right_on="รหัส")

    merged["นน ปลายทาง"] = merged["นน ต้นทาง"]
    if "โซนการจัดส่ง" in merged.columns:
        merged["นน ปลายทาง"] = merged.apply(
            lambda row: 3 if (
                row.get("โซนการจัดส่ง") == "West"
                and isinstance(row["นน ปลายทาง"], (int, float))
                and row["นน ปลายทาง"] <= 3
            ) else row["นน ปลายทาง"],
            axis=1,
        )

    # Format datetimes
    dt_cols = merged.select_dtypes(include=["datetime64[ns]", "datetime64[ns, Asia/Bangkok]",
                                             "datetime64[ns, UTC]"]).columns
    for col in dt_cols:
        merged[col] = merged[col].dt.strftime("%d/%m/%Y %H:%M")

    # Rename conflicting columns
    rename_map = {}
    if "วันที่_x" in merged.columns:
        rename_map["วันที่_x"] = "วันที่"
    if "เวลาถึงไซต์งาน" in merged.columns:
        rename_map["เวลาถึงไซต์งาน"] = "เลขที่ตั๋วเพิ่ม 3"
    if "เวลาออกจากไซต์งาน" in merged.columns:
        rename_map["เวลาออกจากไซต์งาน"] = "เลขที่ตั๋วเพิ่ม 4"
    if "ประเภทรถร่วม_x" in merged.columns:
        rename_map["ประเภทรถร่วม_x"] = "ประเภทรถร่วม"
    merged = merged.rename(columns=rename_map)

    merged["วันที่"] = pd.to_datetime(merged["วันที่"], dayfirst=True, errors="coerce").dt.strftime("%d/%m/%Y")
    merged["วันที่ครบกำหนด"] = pd.to_datetime(merged["วันที่ครบกำหนด"], dayfirst=True, errors="coerce").dt.strftime("%d/%m/%Y")

    selected_cols = [
        "สาขา", "บริการ", "LDT", "Type", "ผลิตภัณฑ์", "Ship To", "เส้นทาง", "dropoffs",
        "ประเภทวิ่ง", "ประเภทการขนส่งขากลับ", "ประเภทการขนส่ง",
        "Service Parameter A", "Service Parameter B",
        "แพล้นท์", "แพล้นท์โอเนย้าย", "วันที่", "วันที่ครบกำหนด",
        "เลขที่ตั๋วเพิ่ม 1", "เลขที่ตั๋วเพิ่ม 2", "เลขที่ตั๋วเพิ่ม 3", "เลขที่ตั๋วเพิ่ม 4",
        "หมายเหตุ", "ประเภทรถร่วม", "ผู้จัดส่งร่วม", "รหัส พจส 1", "รหัส พจส 2",
        "ทะเบียนหัว", "ทะเบียนหาง", "เวลาออกเดินทาง",
        "นน ต้นทาง", "นน ปลายทาง",
        "วันเวลาอ้างอิง 1", "วันเวลาอ้างอิง 2", "วันเวลาอ้างอิง 3", "วันเวลาอ้างอิง 4",
        "วันเวลาลงสินค้า", "วันเวลาปิด LDT", "วิ่งแทนรถทะเบียน",
    ]
    for col in selected_cols:
        if col not in merged.columns:
            merged[col] = ""
    log.info(f"LDT merged: {len(merged)} rows")
    return merged[selected_cols]


# ── Step 7: Build new ship_to ─────────────────────────────────────────────────

def build_new_shippo(
    cpac: pd.DataFrame,
    fleetlink: pd.DataFrame,
    vehicledaily: pd.DataFrame,
    vehiclemaster: pd.DataFrame,
    shipto: pd.DataFrame,
    zone: pd.DataFrame,
) -> pd.DataFrame:
    cpac = cpac[["plantNo", "dpNo", "dpDate", "dpTime", "carNo", "driverName",
                  "siteCode", "siteName", "quantity", "distanceCode"]].copy()

    for col in ["dpDate", "dpTime"]:
        if col in cpac.columns:
            cpac[col] = _convert_dptime(cpac[col])

    cpac["แพล้นท์"] = cpac["dpNo"].astype(str).str[:4]
    cpac["LDT"] = cpac["dpNo"]
    cpac["Ship To"] = cpac["dpNo"].astype(str).str[:4] + cpac["siteCode"]
    cpac["นน ต้นทาง"] = cpac["quantity"]

    fl_cols = [c for c in [
        "หมายเลข DP", "เวลาถึงไซต์งาน", "เวลาออกจากไซต์งาน",
        "ระยะทางจากแพลนต์ถึงไซต์งาน (กิโลเมตร) (คำนวณสิ้นวัน)",
        "ระยะทางขากลับแพลนต์ (กิโลเมตร) (คำนวณสิ้นวัน)",
    ] if c in fleetlink.columns]
    fl = fleetlink[fl_cols].copy()
    for col in ["เวลาถึงไซต์งาน", "เวลาออกจากไซต์งาน"]:
        if col in fl.columns:
            fl[col] = pd.to_datetime(fl[col], errors="coerce").dt.tz_localize(
                "Asia/Bangkok", ambiguous="NaT", nonexistent="NaT"
            )

    cpac["dpNo"] = cpac["dpNo"].astype(str)
    fl["หมายเลข DP"] = fl["หมายเลข DP"].astype(str)
    cpac["carNo"] = cpac["carNo"].astype(str)
    if not vehicledaily.empty:
        vehicledaily["เบอร์รถ"] = vehicledaily["เบอร์รถ"].astype(str)

    merged = cpac.merge(fl, how="inner", left_on="dpNo", right_on="หมายเลข DP")
    if not vehicledaily.empty:
        merged = merged.merge(vehicledaily, how="inner", left_on="carNo", right_on="เบอร์รถ")
    if "คนขับรถ" in merged.columns:
        merged["ประเภทรถร่วม"] = ""
        merged.loc[merged["คนขับรถ"] == "พจส", "ประเภทรถร่วม"] = "OT-MT01"
        merged.loc[merged["คนขับรถ"] == "พจร", "ประเภทรถร่วม"] = "OT-MT02"
    if "ทะเบียน" in merged.columns:
        merged["ทะเบียนหัว"] = "สบ." + merged["ทะเบียน"].astype(str)

    if not vehiclemaster.empty:
        merged = merged.merge(vehiclemaster, how="inner", left_on="carNo", right_on="เลขรถ")
        if "ประเภทยานพาหนะ" in merged.columns:
            merged["เส้นทาง"] = merged["ประเภทยานพาหนะ"].apply(
                lambda x: "CPAC L" if x == "Mixer 10 ล้อ" else "6 ล้อ"
            )
            merged["บริการ"] = merged["เส้นทาง"].apply(
                lambda x: "M026" if x == "6 ล้อ" else "M025 "
            )

    merged["Ship To"] = merged["Ship To"].astype(str)
    shipto = shipto.copy()
    shipto["รหัส"] = shipto["รหัส"].astype(str)
    merged = merged.merge(shipto, how="left", left_on="Ship To", right_on="รหัส")

    merged["นน ปลายทาง"] = merged["นน ต้นทาง"]
    if "โซนการจัดส่ง" in merged.columns:
        merged["นน ปลายทาง"] = merged.apply(
            lambda row: 3 if (
                row.get("โซนการจัดส่ง") == "West"
                and isinstance(row["นน ปลายทาง"], (int, float))
                and row["นน ปลายทาง"] <= 3
            ) else row["นน ปลายทาง"],
            axis=1,
        )

    dt_cols = merged.select_dtypes(include=["datetime64[ns]", "datetime64[ns, Asia/Bangkok]",
                                             "datetime64[ns, UTC]"]).columns
    for col in dt_cols:
        merged[col] = merged[col].dt.strftime("%d/%m/%Y %H:%M")

    # Select subset for ship_to output (task_7 columns)
    merged2 = merged[["dpNo", "Ship To", "เส้นทาง", "siteName", "แพล้นท์", "siteCode", "distanceCode",
                       "ระยะทางจากแพลนต์ถึงไซต์งาน (กิโลเมตร) (คำนวณสิ้นวัน)",
                       "ระยะทางขากลับแพลนต์ (กิโลเมตร) (คำนวณสิ้นวัน)"]].copy() if all(
        c in merged.columns for c in [
            "dpNo", "Ship To", "เส้นทาง", "siteName", "แพล้นท์", "siteCode", "distanceCode",
            "ระยะทางจากแพลนต์ถึงไซต์งาน (กิโลเมตร) (คำนวณสิ้นวัน)",
            "ระยะทางขากลับแพลนต์ (กิโลเมตร) (คำนวณสิ้นวัน)",
        ]
    ) else merged.copy()

    merged2["แพล้นท์"] = merged2["แพล้นท์"].astype(str).str.strip()
    if not zone.empty and "รหัสแพล้นท์" in zone.columns:
        zone["รหัสแพล้นท์"] = zone["รหัสแพล้นท์"].astype(str).str.strip()
        zone_cols = [c for c in ["รหัสแพล้นท์", "โซน", "จราจร"] if c in zone.columns]
        merged2 = merged2.merge(zone[zone_cols], how="left",
                                left_on="แพล้นท์", right_on="รหัสแพล้นท์")

    merged2 = merged2.drop_duplicates(subset=["dpNo"], keep="first").reset_index(drop=True)
    merged2 = merged2.rename(columns={
        "siteName": "Site Name", "จราจร": "สภาพการจราจร",
        "แพล้นท์": "เดินทางจาก", "siteCode": "เดินทางไป", "distanceCode": "Distance Code",
        "ระยะทางจากแพลนต์ถึงไซต์งาน (กิโลเมตร) (คำนวณสิ้นวัน)": "ทางเรียบหนัก",
        "ระยะทางขากลับแพลนต์ (กิโลเมตร) (คำนวณสิ้นวัน)": "ทางเรียบเบา",
    })
    merged2["LDT"] = merged2["dpNo"]
    merged2["เลขที่"] = merged2["Ship To"]
    merged2["ลูกค้า"] = "CUS-00073"
    merged2["รหัสอ้างอิงลูกค้า"] = ""
    merged2["ที่อยู่"] = "-"
    for col in ["ตีเปล่า", "ขึ้นเขาสูงหนัก", "ขึ้นเขาเบา", "ขึ้นเขาสูงเบา", "สำรอง"]:
        merged2[col] = 0

    if "Distance Code" in merged2.columns:
        distance_map = {1: 5, 2: 10, 3: 15, 4: 20, 5: 25, 6: 30, 7: 35, 8: 40}
        merged2["Distance Code"] = (
            merged2["Distance Code"].astype(str).str.extract(r"(\d+)").astype(float)
        )
        merged2["ระยะทาง"] = merged2["Distance Code"].map(distance_map).fillna(0)
    else:
        merged2["ระยะทาง"] = 0

    today = datetime.today()
    merged2["เริ่มใช้งานตั้งแต่"] = today.replace(day=1).strftime("1/%m/%Y")
    merged2["ใช้งานถึงวันที่"] = today.replace(day=1, year=today.year + 1).strftime("1/%m/%Y")

    df_avg = merged2.copy()
    df_avg["ทางเรียบหนัก"] = pd.to_numeric(df_avg.get("ทางเรียบหนัก", 0), errors="coerce")
    df_avg["ทางเรียบเบา"] = pd.to_numeric(df_avg.get("ทางเรียบเบา", 0), errors="coerce")
    df_avg["ทางเรียบหนัก"] = df_avg.groupby("เลขที่")["ทางเรียบหนัก"].transform("mean").round(2)
    df_avg["ทางเรียบเบา"] = df_avg.groupby("เลขที่")["ทางเรียบเบา"].transform("mean").round(2)

    # Exclude already-existing ship_to codes
    df_avg["เลขที่"] = df_avg["เลขที่"].astype(str)
    shipto["รหัส"] = shipto["รหัส"].astype(str)
    df_new = df_avg[~df_avg["เลขที่"].isin(shipto["รหัส"])].copy()
    df_new["แพล้นท์"] = df_new["เดินทางจาก"]

    final_cols = [
        "เลขที่", "เส้นทาง", "รหัสอ้างอิงลูกค้า", "Site Name", "ลูกค้า", "โซน",
        "สภาพการจราจร", "เดินทางจาก", "เดินทางไป", "ที่อยู่", "ระยะทาง", "ตีเปล่า",
        "ทางเรียบหนัก", "ขึ้นเขาสูงหนัก", "ทางเรียบเบา", "ขึ้นเขาเบา", "ขึ้นเขาสูงเบา",
        "สำรอง", "แพล้นท์", "เริ่มใช้งานตั้งแต่", "ใช้งานถึงวันที่",
    ]
    for col in final_cols:
        if col not in df_new.columns:
            df_new[col] = ""
    log.info(f"New ship_to: {len(df_new)} rows")
    return df_new[final_cols]


# ── MongoDB write ─────────────────────────────────────────────────────────────

def write_to_mongo(
    df_ldt: pd.DataFrame, df_new_ship_to: pd.DataFrame, run_date: str, filename: str
) -> None:
    if not MONGODB_URI:
        log.warning("MONGODB_URI not set — skipping MongoDB write")
        return
    client = MongoClient(MONGODB_URI)
    try:
        col = client["atms"]["ldt_runs"]

        def to_records(df: pd.DataFrame) -> list:
            import decimal as _dec
            def _coerce(v):
                return float(v) if isinstance(v, _dec.Decimal) else v
            return [{k: _coerce(v) for k, v in row.items()}
                    for row in df.where(pd.notnull(df), None).to_dict("records")]

        doc = {
            "pipeline": PIPELINE,
            "run_date": run_date,
            "filename": filename,
            "ldt_rows": to_records(df_ldt),
            "new_ship_to_rows": to_records(df_new_ship_to),
            "ldt_count": len(df_ldt),
            "new_ship_to_count": len(df_new_ship_to),
            "created_at": datetime.now(timezone.utc),
        }
        col.replace_one({"pipeline": PIPELINE, "run_date": run_date}, doc, upsert=True)
        log.info(f"MongoDB: {PIPELINE}/{run_date} — {len(df_ldt)} LDT, {len(df_new_ship_to)} new ship_to")
    finally:
        client.close()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    yesterday = datetime.today() - timedelta(days=1)
    target_date_th = yesterday.strftime("%d-%m-%Y")   # for CPAC API
    target_date_atms = yesterday.strftime("%d/%m/%Y") # for ATMS forms
    target_date_ymd = yesterday.strftime("%Y-%m-%d")  # for fleetlink API
    run_date = yesterday.strftime("%Y-%m-%d")
    filename = f"LDTCPAC_{yesterday.strftime('%d-%m-%y')}.xlsx"

    log.info(f"=== CPAC pipeline starting for {run_date} ===")

    # Load static reference
    zone_path = BASE_DIR / "raw_data" / "zone.xlsx"
    df_zone = pd.read_excel(str(zone_path)) if zone_path.exists() else pd.DataFrame()

    # Fetch all data sources
    df_cpac = fetch_cpac(target_date_th)
    if df_cpac.empty:
        log.warning("No CPAC data — aborting")
        raise SystemExit(0)

    df_fleetlink = fetch_fleetlink(target_date_ymd)

    with requests.Session() as session:
        atms_login(session)
        df_vehicledaily = fetch_vehicle_daily(session, target_date_atms)
        df_vehiclemaster = fetch_vehiclemaster(session)
        df_shipto = fetch_ship_to(session)

    # Build outputs in memory
    df_ldt = build_ldt(df_cpac, df_fleetlink, df_vehicledaily, df_vehiclemaster, df_shipto)
    df_new_ship_to = build_new_shippo(df_cpac, df_fleetlink, df_vehicledaily, df_vehiclemaster, df_shipto, df_zone)

    # Write to MongoDB
    write_to_mongo(df_ldt, df_new_ship_to, run_date, filename)
    log.info(f"=== CPAC pipeline done: {len(df_ldt)} LDT, {len(df_new_ship_to)} new ship_to ===")
