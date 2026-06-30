"""
SCCO pipeline — all tasks in memory, output to MongoDB.
Replaces task_1 → task_2 → task_3 → task_4 → task_5 chain.
Static refs (zone, truck_type) still read from disk.
"""
import decimal
import io
import logging
import os
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql
import requests
import urllib3
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_DIR = Path(__file__).parent
PIPELINE = "scco"
BASE_URL = "https://www.mena-atms.com"

ATMS_USERNAME = os.getenv("ATMS_USERNAME")
ATMS_PASSWORD = os.getenv("ATMS_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "157.230.39.131")
DB_USER = os.getenv("DB_USER", "plug")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "mn-terminus-api")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
MONGODB_URI = os.getenv("MONGODB_URI")

warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Step 1: MySQL ─────────────────────────────────────────────────────────────

def fetch_ldt() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (df_ldt_processed, raw_ldt)."""
    truck_type_path = BASE_DIR / "data" / "truck_type" / "truck_type.xlsx"
    df_truck_type = pd.read_excel(str(truck_type_path)) if truck_type_path.exists() else pd.DataFrame()

    try:
        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
            database=DB_NAME, port=DB_PORT,
            cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM rmcconcretetrip "
                "WHERE DATE(TicketCreateAt) = DATE_SUB(CURDATE(), INTERVAL 1 DAY)"
            )
            df = pd.DataFrame(cur.fetchall())
        conn.close()
    except Exception as e:
        log.error(f"MySQL error: {e}")
        return pd.DataFrame(), pd.DataFrame()

    if df.empty:
        log.warning("No LDT rows from MySQL.")
        return df, df

    # Convert MySQL Decimal types to native float so pandas operations work correctly
    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, decimal.Decimal) if x is not None else False).any():
            df[col] = df[col].apply(lambda x: float(x) if isinstance(x, decimal.Decimal) else x)

    df = df[df["TruckNo"].astype(str).str.startswith("TH")]
    if not df_truck_type.empty:
        df = df.merge(df_truck_type, left_on="TruckNo", right_on="เบอร์รถ", how="left")
    df["TruckPlateNo"] = df["TruckPlateNo"].str.split(" ").str[0].str.replace("สบ", "", regex=False)
    df = df.rename(columns={
        "TicketNo": "LDT", "PlantCode": "แพล้นท์",
        "TicketCreateAt": "วันที่", "TruckPlateNo": "ทะเบียนหัว",
    })
    df = df[df["ReasonCode"] == ""]
    raw_ldt = df.copy()

    df["Ship To"] = df["แพล้นท์"] + df["SiteCode"]
    df["เลขที่ตั๋วเพิ่ม 1"] = df["แพล้นท์"]
    df["เลขที่ตั๋วเพิ่ม 2"] = "2100050"
    df["เลขที่ตั๋วเพิ่ม 4"] = "ZCC1"
    df["สาขา"] = "LB"
    df["ผลิตภัณฑ์"] = "คอนกรีตผสมเสร็จ"
    df["นน ต้นทาง"] = df["Quantity"]
    if "ประเภท" in df.columns:
        df.loc[df["ประเภท"] == "Scco MS", "นน ปลายทาง"] = 1
        df.loc[df["ประเภท"] == "Scco ML", "นน ปลายทาง"] = (
            df.loc[df["ประเภท"] == "Scco ML", "Quantity"].apply(lambda x: 4 if x < 4 else x)
        )
        df["บริการ"] = None
        df.loc[df["ประเภท"] == "Scco MS", "บริการ"] = "M002"
        df.loc[df["ประเภท"] == "Scco ML", "บริการ"] = "M001"
    df["วันที่ครบกำหนด"] = df["วันที่"]
    for col in ["วันเวลาอ้างอิง 1", "วันเวลาอ้างอิง 2", "วันเวลาอ้างอิง 3",
                "วันเวลาอ้างอิง 4", "เวลาออกเดินทาง"]:
        df[col] = df.apply(
            lambda row: row["LoadAt"] if pd.notna(row.get("LoadAt")) else row["วันที่"], axis=1
        )
    df["Type"] = "single drop"
    df["ประเภทการวิ่ง"] = "legacy"
    df["ประเภทการขนส่งขากลับ"] = ""
    df["ประเภทการขนส่ง"] = "heavy"
    df["Service Parameter A"] = ""
    df["Service Parameter B"] = ""
    df["dropoffs"] = ""
    df["วันเวลาลงสินค้า"] = df.apply(
        lambda row: row["PlantMoveOutAt"] if pd.notna(row.get("PlantMoveOutAt")) else row["วันที่"], axis=1
    )
    df["วันเวลาปิด LDT"] = df.apply(
        lambda row: row["ArriveToPlantAt"] if pd.notna(row.get("ArriveToPlantAt")) else row["วันที่"], axis=1
    )

    selected = [
        "LDT", "แพล้นท์", "วันที่", "ทะเบียนหัว", "Ship To",
        "เลขที่ตั๋วเพิ่ม 2", "เลขที่ตั๋วเพิ่ม 4", "สาขา", "ผลิตภัณฑ์",
        "นน ปลายทาง", "นน ต้นทาง", "วันเวลาลงสินค้า", "วันเวลาปิด LDT",
        "วันเวลาอ้างอิง 1", "วันเวลาอ้างอิง 2", "วันเวลาอ้างอิง 3", "วันเวลาอ้างอิง 4",
        "เวลาออกเดินทาง", "วันที่ครบกำหนด", "Type", "ประเภทการวิ่ง",
        "ประเภทการขนส่งขากลับ", "ประเภทการขนส่ง",
        "Service Parameter A", "Service Parameter B", "dropoffs", "บริการ",
    ]
    existing = [c for c in selected if c in df.columns]
    log.info(f"MySQL: {len(df)} LDT rows")
    return df[existing], raw_ldt


# ── ATMS login ────────────────────────────────────────────────────────────────

def atms_login(session: requests.Session) -> None:
    session.post(
        f"{BASE_URL}/account/user/login",
        data={"username": ATMS_USERNAME, "password": ATMS_PASSWORD,
              "submit": "login", "next": ""},
        verify=False, timeout=30,
    ).raise_for_status()
    log.info("ATMS login OK")


# ── Step 2: ATMS vehicle daily ────────────────────────────────────────────────

def fetch_vehicle_daily(session: requests.Session, target_date: str) -> pd.DataFrame:
    url = f"{BASE_URL}/report/print.out/print.excel/type/vehicle.daily.transaction"
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
            log.warning(f"Vehicle daily fleet_group_id={fgid} returned HTML — may be empty or no access")

    if not frames:
        log.warning("No vehicle daily data returned — continuing without driver info")
        return pd.DataFrame()

    final_df = pd.concat(frames, ignore_index=True)
    rename_map = {
        "หัว": "ยี่ห้อ", "Unnamed: 7": "เบอร์รถ", "Unnamed: 8": "ทะเบียน",
        "Unnamed: 13": "รหัส.1", "Unnamed: 14": "ชื่อ.1", "Unnamed: 15": "เบอร์โทร",
        "คนขับรถ": "สถานะ", "สเตตัส": "คนขับ",
    }
    final_df = final_df.rename(columns={k: v for k, v in rename_map.items() if k in final_df.columns})
    if "วันที่" in final_df.columns:
        final_df = final_df.dropna(subset=["วันที่"])
    if "ทะเบียน" in final_df.columns:
        final_df["ทะเบียน"] = final_df["ทะเบียน"].str.replace("สบ.", "", regex=False)
    log.info(f"Vehicle daily: {len(final_df)} rows")
    return final_df


# ── Step 3: Merge LDT + driver + zone ────────────────────────────────────────

def process_ldt(df_ldf: pd.DataFrame, df_driver: pd.DataFrame, df_zone: pd.DataFrame) -> pd.DataFrame:
    if not df_driver.empty:
        keep = [c for c in ["ทะเบียน", "สถานะ", "เบอร์รถ", "รหัส.1", "ชื่อ.1"] if c in df_driver.columns]
        df_driver = df_driver[keep].copy()
        if "ทะเบียน" in df_driver.columns:
            df_driver["ทะเบียน"] = df_driver["ทะเบียน"].astype(str).str.replace("สบ.", "", regex=True)
        df_ldf = df_ldf.merge(df_driver, left_on="ทะเบียนหัว", right_on="ทะเบียน", how="left")
        if "รหัส.1" in df_ldf.columns:
            df_ldf = df_ldf.rename(columns={"รหัส.1": "รหัส พจส 1"})
        if "สถานะ" in df_ldf.columns:
            df_ldf["ประเภทรถร่วม"] = df_ldf["สถานะ"].apply(
                lambda x: "OT-MT02" if x == "พจร" else "OT-MT01"
            )

    df_ldf["ทะเบียนหัว"] = "สบ." + df_ldf["ทะเบียนหัว"].astype(str)
    df_ldf["วันที่"] = pd.to_datetime(df_ldf["วันที่"], errors="coerce").dt.strftime("%d/%m/%Y")
    df_ldf["วันที่ครบกำหนด"] = df_ldf["วันที่"]
    for col in ["วันเวลาอ้างอิง 1", "วันเวลาอ้างอิง 2", "วันเวลาอ้างอิง 3",
                "วันเวลาอ้างอิง 4", "วันเวลาลงสินค้า", "วันเวลาปิด LDT", "เวลาออกเดินทาง"]:
        if col in df_ldf.columns:
            df_ldf[col] = pd.to_datetime(df_ldf[col], errors="coerce").dt.strftime("%d/%m/%Y %H:%M")

    df_ldf = df_ldf.merge(df_zone, left_on="แพล้นท์", right_on="Plant", how="left")
    if "บริการ" in df_ldf.columns:
        df_ldf["เส้นทาง"] = None
        df_ldf.loc[df_ldf["บริการ"] == "M002", "เส้นทาง"] = "6 ล้อ"
        df_ldf.loc[df_ldf["บริการ"] == "M001", "เส้นทาง"] = "10 ล้อ"
    df_ldf = df_ldf.drop(columns=["Plant", "รายละเอียด"], errors="ignore")
    df_ldf["เลขที่ตั๋วเพิ่ม 1"] = df_ldf["แพล้นท์"]

    required = [
        "สาขา", "บริการ", "LDT", "Type", "ผลิตภัณฑ์", "Ship To", "เส้นทาง", "dropoffs",
        "ประเภทการวิ่ง", "ประเภทการขนส่งขากลับ", "ประเภทการขนส่ง",
        "Service Parameter A", "Service Parameter B", "แพล้นท์", "Base Plant",
        "วันที่", "วันที่ครบกำหนด", "เลขที่ตั๋วเพิ่ม 1", "เลขที่ตั๋วเพิ่ม 2",
        "เลขที่ตั๋วเพิ่ม 3", "เลขที่ตั๋วเพิ่ม 4", "หมายเหตุ", "ประเภทรถร่วม", "ผู้จัดส่งร่วม",
        "รหัส พจส 1", "รหัส พจส 2", "ทะเบียนหัว", "ทะเบียนหาง", "เวลาออกเดินทาง",
        "นน ต้นทาง", "นน ปลายทาง", "วันเวลาอ้างอิง 1", "วันเวลาอ้างอิง 2",
        "วันเวลาอ้างอิง 3", "วันเวลาอ้างอิง 4", "วันเวลาลงสินค้า", "วันเวลาปิด LDT",
        "วิ่งแทนรถทะเบียน",
    ]
    for col in required:
        if col not in df_ldf.columns:
            df_ldf[col] = ""
    df_ldf = df_ldf[required]
    df_ldf = df_ldf[~df_ldf["LDT"].duplicated(keep="last")]
    df_ldf = df_ldf[df_ldf["แพล้นท์"] != "317"]
    log.info(f"Processed LDT: {len(df_ldf)} rows")
    return df_ldf


# ── Step 4: ATMS ship.to ─────────────────────────────────────────────────────

def fetch_ship_to(session: requests.Session) -> pd.DataFrame:
    r = session.post(
        f"{BASE_URL}/report/excel/index.excel/type/ship.to",
        data={"customer_id": "1", "status": "A", "from_valid_date": "01/01/2025",
              "submit": "พิมพ์", "display_type": "multiple-day", "report_type": "ship.to"},
        verify=False, timeout=600,
    )
    r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content), header=1)
    log.info(f"Ship.to: {len(df)} rows")
    return df


# ── Step 5: Build final output ────────────────────────────────────────────────

def _fmt_date(val) -> str:
    if pd.isna(val):
        return ""
    dt = pd.to_datetime(val, dayfirst=True, errors="coerce")
    return "" if pd.isna(dt) else dt.strftime("%d/%m/%Y")


def _fmt_datetime(val) -> str:
    if pd.isna(val):
        return ""
    dt = pd.to_datetime(val, dayfirst=True, errors="coerce")
    if pd.isna(dt):
        return str(val)
    return dt.strftime("%d/%m/%Y") if (dt.hour == 0 and dt.minute == 0) else dt.strftime("%d/%m/%Y %H:%M")


def build_output(
    df_processed: pd.DataFrame,
    df_ship_to_atms: pd.DataFrame,
    raw_ldt: pd.DataFrame,
    df_zone: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    for col in ["วันที่", "วันที่ครบกำหนด"]:
        if col in df_processed.columns:
            df_processed[col] = df_processed[col].apply(_fmt_date)
    for col in ["เวลาออกเดินทาง", "วันเวลาอ้างอิง 1", "วันเวลาอ้างอิง 2", "วันเวลาอ้างอิง 3",
                "วันเวลาอ้างอิง 4", "วันเวลาลงสินค้า", "วันเวลาปิด LDT"]:
        if col in df_processed.columns:
            df_processed[col] = df_processed[col].apply(_fmt_datetime)

    check = df_processed[["LDT", "Ship To"]].merge(
        df_ship_to_atms, left_on="Ship To", right_on="รหัส", how="left"
    )
    check = check.drop_duplicates(subset=["LDT"], keep="first")
    check["is_NaN_ใช้งานตั้งแต่"] = check["ใช้งานตั้งแต่"].isna().map({True: "Yes", False: "No"})

    create_st = check[check["is_NaN_ใช้งานตั้งแต่"] == "Yes"][["LDT", "Ship To"]]
    merge_cols = [c for c in ["LDT", "เส้นทาง", "แพล้นท์", "บริการ"] if c in df_processed.columns]
    create_st = create_st.merge(df_processed[merge_cols], on="LDT", how="left")

    zone_cols = [c for c in ["Plant", "โซน", "สภาพการจราจร_ML", "สภาพการจราจร_MS", "จังหวัด", "อำเภอ"]
                 if c in df_zone.columns]
    create_st = create_st.merge(df_zone[zone_cols], left_on="แพล้นท์", right_on="Plant", how="left")

    raw_cols = [c for c in ["LDT", "SiteName", "PlantToSiteDistance", "SiteToPlantDistance"]
                if c in raw_ldt.columns]
    if raw_cols:
        create_st = create_st.merge(raw_ldt[raw_cols], on="LDT", how="left")
    create_st["ประเทศ"] = "ไทย"
    create_st = create_st.rename(columns={
        "PlantToSiteDistance": "ทางเรียบหนัก",
        "SiteToPlantDistance": "ทางเรียบเบา",
        "SiteName": "ชื่อไซร้งาน",
    })

    if "สภาพการจราจร_ML" in create_st.columns and "สภาพการจราจร_MS" in create_st.columns:
        create_st["สภาพการจราจร"] = np.where(
            create_st.get("บริการ") == "M002", create_st["สภาพการจราจร_MS"],
            np.where(create_st.get("บริการ") == "M001", create_st["สภาพการจราจร_ML"], None),
        )

    create_st["เส้นทาง"] = None
    if "บริการ" in create_st.columns:
        create_st.loc[create_st["บริการ"] == "M001", "เส้นทาง"] = "10 ล้อ"
        create_st.loc[create_st["บริการ"] == "M002", "เส้นทาง"] = "6 ล้อ"

    s = create_st["Ship To"].astype("string").fillna("").str.strip()
    create_st["เดินทางจาก"] = s.str[:4]
    create_st["เดินทางไป"] = s.str[4:].str.strip()

    desired = [
        "LDT", "Ship To", "เส้นทาง", "ชื่อไซร้งาน", "ลูกค้า", "โซน", "สภาพการจราจร", "ประเทศ",
        "จังหวัด", "อำเภอ", "ที่อยู่", "ระยะทาง", "ตีเปล่า", "ทางเรียบหนัก", "ขึ้นเขาหนัก",
        "ขึ้นเขาสูงหนัก", "ทางเรียบเบา", "ขึ้นเขาเบา", "ขึ้นเขาสูงเบา", "สำรอง", "เดินทางจาก", "เดินทางไป",
    ]
    for col in desired:
        if col not in create_st.columns:
            create_st[col] = None
    create_st = create_st[desired]
    create_st["ลูกค้า"] = "1000"
    for col in ["ระยะทาง", "ตีเปล่า", "ขึ้นเขาหนัก", "ขึ้นเขาสูงหนัก", "ขึ้นเขาเบา", "ขึ้นเขาสูงเบา"]:
        create_st[col] = 0
    create_st["ที่อยู่"] = "-"
    create_st["สำรอง"] = 0

    today = datetime.today()
    create_st["เริ่มใช้งานตั้งแต่"] = today.replace(day=1).strftime("1/%m/%Y")
    create_st["ใช้งานถึงวันที่"] = today.replace(day=1, year=today.year + 1).strftime("1/%m/%Y")

    df = create_st.drop(columns=["LDT"], errors="ignore")
    df_g = df.groupby("Ship To").agg({
        "ทางเรียบหนัก": lambda x: x[x != 0].mean(),
        "ทางเรียบเบา": lambda x: x[x != 0].mean(),
    }).reset_index()
    df_g.iloc[:, 1:] = np.ceil(df_g.iloc[:, 1:])
    df_g = df_g.fillna(0)
    df.set_index("Ship To", inplace=True)
    df_g.set_index("Ship To", inplace=True)
    df.update(df_g)
    df.reset_index(inplace=True)
    df = df.drop_duplicates(subset=["Ship To"])
    df["รหัสอ้างอิงลูกค้า"] = ""
    df["ดึงระยะทางจาก Map API"] = "N"
    for col in ["เริ่มใช้งานตั้งแต่", "ใช้งานถึงวันที่"]:
        if col in df.columns:
            df[col] = df[col].apply(_fmt_date)
    df = df.rename(columns={"เส้นทาง": "รหัสย่อย"})
    df["แพล้นท์"] = df["เดินทางจาก"]
    final_cols = [
        "Ship To", "รหัสย่อย", "รหัสอ้างอิงลูกค้า", "ชื่อไซร้งาน", "ลูกค้า", "แพล้นท์",
        "โซน", "สภาพการจราจร", "เดินทางจาก", "เดินทางไป", "ที่อยู่", "ระยะทาง", "ตีเปล่า",
        "ทางเรียบหนัก", "ขึ้นเขาหนัก", "ขึ้นเขาสูงหนัก", "ทางเรียบเบา", "ขึ้นเขาเบา",
        "ขึ้นเขาสูงเบา", "สำรอง", "ใช้งานถึงวันที่", "เริ่มใช้งานตั้งแต่", "ดึงระยะทางจาก Map API",
    ]
    for col in final_cols:
        if col not in df.columns:
            df[col] = ""
    return df_processed, df[final_cols]


# ── MongoDB write ─────────────────────────────────────────────────────────────

def write_to_mongo(df_ldt: pd.DataFrame, df_new_ship_to: pd.DataFrame, run_date: str, filename: str) -> None:
    if not MONGODB_URI:
        log.warning("MONGODB_URI not set — skipping MongoDB write")
        return
    client = MongoClient(MONGODB_URI)
    try:
        col = client["atms"]["ldt_runs"]

        def _coerce(v):
            if isinstance(v, decimal.Decimal):
                return float(v)
            return v

        def to_records(df: pd.DataFrame) -> list:
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
    target_date = yesterday.strftime("%d/%m/%Y")
    run_date = yesterday.strftime("%Y-%m-%d")
    filename = f"LDTSCCO_{yesterday.strftime('%d%m%Y')}(มี+ไม่มีshipto).xlsx"

    log.info(f"=== SCCO pipeline starting for {run_date} ===")

    df_zone = pd.read_excel(str(BASE_DIR / "data" / "zone" / "df_zone.xlsx"))

    df_ldt_raw, raw_ldt = fetch_ldt()
    if df_ldt_raw.empty:
        log.warning("No LDT data — aborting")
        raise SystemExit(0)

    with requests.Session() as session:
        atms_login(session)
        df_driver = fetch_vehicle_daily(session, target_date)
        df_ship_to = fetch_ship_to(session)

    df_processed = process_ldt(df_ldt_raw, df_driver, df_zone)
    df_ldt_final, df_new_ship_to = build_output(df_processed, df_ship_to, raw_ldt, df_zone)

    write_to_mongo(df_ldt_final, df_new_ship_to, run_date, filename)
    log.info(f"=== SCCO pipeline done: {len(df_ldt_final)} LDT, {len(df_new_ship_to)} new ship_to ===")
