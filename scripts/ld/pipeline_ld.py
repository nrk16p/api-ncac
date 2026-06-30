"""
Merged Asia (LD) pipeline — runs all tasks in memory, writes to MongoDB.
No intermediate Excel files. Static refs (zone, vehiclemaster) read from disk.
"""
import os, io, logging, warnings, urllib3, decimal
import pymysql, requests, pandas as pd, numpy as np
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from pymongo import MongoClient
from dotenv import load_dotenv

# Load .env from scripts/ directory (parent of ld/)
load_dotenv(Path(__file__).parent.parent / ".env")

BASE_DIR = Path(__file__).parent
BASE_URL = "https://www.mena-atms.com"
VEHICLE_MASTER_URL = (
    f"{BASE_URL}/veh/vehicle/index.export/"
    "?page=1&order_by=v.code%20asc&search-toggle-status=&order_by=v.code%20asc"
)
USERNAME = os.getenv("ATMS_USERNAME")
PASSWORD = os.getenv("ATMS_PASSWORD")
MONGODB_URI = os.getenv("MONGODB_URI")
PIPELINE = "asia"
CUSTOMER_IDS = [19, 20]

warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── helpers ──────────────────────────────────────────────────────────────────

def _fmt_date(val):
    if pd.isna(val): return ""
    dt = pd.to_datetime(val, errors="coerce")
    return "" if pd.isna(dt) else dt.strftime("%d/%m/%Y")

def _fmt_datetime(val):
    if pd.isna(val): return ""
    dt = pd.to_datetime(val, errors="coerce")
    if pd.isna(dt): return str(val)
    return dt.strftime("%d/%m/%Y") if (dt.hour == 0 and dt.minute == 0) else dt.strftime("%d/%m/%Y %H:%M")


# ── task 1: MySQL ─────────────────────────────────────────────────────────────

def fetch_ldt():
    """Fetch yesterday's LDT records from MySQL terminus DB."""
    try:
        conn = pymysql.connect(
            host=os.getenv("DB_HOST", "157.230.39.131"),
            user=os.getenv("DB_USER", "plug"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "mn-terminus-api"),
            port=int(os.getenv("DB_PORT", "3306")),
            cursorclass=pymysql.cursors.DictCursor,
        )
    except pymysql.MySQLError as e:
        logging.error(f"MySQL connection failed: {e}")
        return pd.DataFrame(), pd.DataFrame()

    query = "SELECT * FROM rmcconcretetrip WHERE DATE(TicketCreateAt) = DATE_SUB(CURDATE(), INTERVAL 1 DAY)"
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            raw = pd.DataFrame(cur.fetchall())
    except Exception as e:
        logging.error(f"MySQL query failed: {e}")
        return pd.DataFrame(), pd.DataFrame()
    finally:
        conn.close()

    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = raw.rename(columns={"TicketNo": "LDT", "PlantCode": "แพล้นท์",
                              "TicketCreateAt": "วันที่", "TruckPlateNo": "ทะเบียนหัว"})
    df = df[df["ReasonCode"] == ""]
    raw_ldt = df.copy()  # captured after rename so "LDT" column exists
    df["Ship To"] = df["แพล้นท์"] + df["SiteName"].str[:8]
    df["เลขที่ตั๋วเพิ่ม 2"] = "2100050"
    df["เลขที่ตั๋วเพิ่ม 4"] = ""
    df["สาขา"] = "LB"
    df["ผลิตภัณฑ์"] = "คอนกรีตผสมเสร็จ"
    df["นน ต้นทาง"] = df["Quantity"]
    df["นน ปลายทาง"] = df["Quantity"].apply(lambda x: 4 if x < 4 else x)
    df["วันที่ครบกำหนด"] = df["วันที่"]
    ref_time = df.apply(lambda r: r["LoadAt"] if r["LoadAt"] else r["วันที่"], axis=1)
    for col in ["วันเวลาอ้างอิง 1","วันเวลาอ้างอิง 2","วันเวลาอ้างอิง 3","วันเวลาอ้างอิง 4","เวลาออกเดินทาง"]:
        df[col] = ref_time
    df["วันเวลาลงสินค้า"] = df.apply(lambda r: r["PlantMoveOutAt"] if r["PlantMoveOutAt"] else r["วันที่"], axis=1)
    df["วันเวลาปิด LDT"] = df.apply(lambda r: r["ArriveToPlantAt"] if r["ArriveToPlantAt"] else r["วันที่"], axis=1)
    df["Type"] = "single drop"
    df["ประเภทการวิ่ง"] = "legacy"
    df["ประเภทการขนส่งขากลับ"] = ""
    df["ประเภทการขนส่ง"] = "heavy"
    df["Service Parameter A"] = ""
    df["Service Parameter B"] = ""
    df["dropoffs"] = ""
    df = df[df["แพล้นท์"].str.startswith(("SU", "SX"))]

    cols = ["LDT","แพล้นท์","วันที่","ทะเบียนหัว","Ship To","เลขที่ตั๋วเพิ่ม 2","เลขที่ตั๋วเพิ่ม 4",
            "สาขา","ผลิตภัณฑ์","นน ปลายทาง","นน ต้นทาง","วันเวลาลงสินค้า","วันเวลาปิด LDT",
            "วันเวลาอ้างอิง 1","วันเวลาอ้างอิง 2","วันเวลาอ้างอิง 3","วันเวลาอ้างอิง 4",
            "เวลาออกเดินทาง","วันที่ครบกำหนด","Type","ประเภทการวิ่ง","ประเภทการขนส่งขากลับ",
            "ประเภทการขนส่ง","Service Parameter A","Service Parameter B","dropoffs"]
    return df[[c for c in cols if c in df.columns]], raw_ldt


# ── task 2: ATMS vehicle daily ────────────────────────────────────────────────

def atms_login(session):
    session.post(f"{BASE_URL}/account/user/login",
                 data={"username": USERNAME, "password": PASSWORD, "submit": "login", "next": ""},
                 verify=False, timeout=30).raise_for_status()
    logging.info("ATMS login OK")


def fetch_driver(session, target_date):
    r = session.post(
        f"{BASE_URL}/report/print.out/print.excel/type/vehicle.daily.transaction",
        data={"fleet_group_id": "", "fleet_id": "1", "t_date": target_date,
              "num_of_day": "1", "submit": "พิมพ์",
              "display_type": "multiple-day", "report_type": "vehicle.daily.transaction"},
        verify=False, timeout=120,
    )
    r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content), header=2, engine="openpyxl")
    return df[["เบอร์รถ","ทะเบียน","สถานะ","คนขับ","รหัส.1","ชื่อ.1"]]


# ── task 3: merge LDT + driver + zone ─────────────────────────────────────────

def process_ldt(df_ldt, df_driver, df_zone):
    df_driver = df_driver.copy()
    df_driver["ทะเบียน"] = df_driver["ทะเบียน"].str.replace("สบ.", "", regex=False)
    df_driver = df_driver[["ทะเบียน","สถานะ","รหัส.1","ชื่อ.1"]]

    df = df_ldt.merge(df_driver, left_on="ทะเบียนหัว", right_on="ทะเบียน", how="left")
    df = df.rename(columns={"รหัส.1": "รหัส พจส 1"})
    df["ประเภทรถร่วม"] = df["สถานะ"].apply(lambda x: "OT-MT02" if x == "พจร" else "OT-MT01")
    df = df.assign(บริการ="M005", **{c: "" for c in ["Base Plant","เลขที่ตั๋วเพิ่ม 2","เลขที่ตั๋วเพิ่ม 3",
                                                      "เลขที่ตั๋วเพิ่ม 4","หมายเหตุ","วิ่งแทนรถทะเบียน"]})
    df["ทะเบียนหัว"] = "สบ." + df["ทะเบียนหัว"]
    df["วันที่"] = pd.to_datetime(df["วันที่"], errors="coerce").dt.strftime("%d/%m/%Y")
    df["วันที่ครบกำหนด"] = df["วันที่"]
    for col in ["วันเวลาอ้างอิง 1","วันเวลาอ้างอิง 2","วันเวลาอ้างอิง 3","วันเวลาอ้างอิง 4",
                "วันเวลาลงสินค้า","วันเวลาปิด LDT","เวลาออกเดินทาง"]:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%d/%m/%Y %H:%M")
    df = df.merge(df_zone, left_on="แพล้นท์", right_on="Plant", how="left")
    df = df.drop(columns=["Plant","รายละเอียด"], errors="ignore")

    required = ["สาขา","บริการ","LDT","Type","ผลิตภัณฑ์","Ship To","เส้นทาง","dropoffs",
                "ประเภทการวิ่ง","ประเภทการขนส่งขากลับ","ประเภทการขนส่ง",
                "Service Parameter A","Service Parameter B","แพล้นท์","Base Plant",
                "วันที่","วันที่ครบกำหนด","เลขที่ตั๋วเพิ่ม 1","เลขที่ตั๋วเพิ่ม 2",
                "เลขที่ตั๋วเพิ่ม 3","เลขที่ตั๋วเพิ่ม 4","หมายเหตุ","ประเภทรถร่วม","ผู้จัดส่งร่วม",
                "รหัส พจส 1","รหัส พจส 2","ทะเบียนหัว","ทะเบียนหาง","เวลาออกเดินทาง",
                "นน ต้นทาง","นน ปลายทาง","วันเวลาอ้างอิง 1","วันเวลาอ้างอิง 2",
                "วันเวลาอ้างอิง 3","วันเวลาอ้างอิง 4","วันเวลาลงสินค้า","วันเวลาปิด LDT","วิ่งแทนรถทะเบียน"]
    for c in required:
        if c not in df.columns:
            df[c] = ""
    df = df[required]
    df = df[~df["LDT"].duplicated(keep="last")]
    df = df[df["แพล้นท์"] != "317"]
    return df


# ── task 3b: ATMS vehiclemaster ──────────────────────────────────────────────

def fetch_vehiclemaster(session):
    resp = session.get(VEHICLE_MASTER_URL, verify=False, timeout=60)
    resp.raise_for_status()
    if not resp.encoding:
        resp.encoding = resp.apparent_encoding
    tables = pd.read_html(StringIO(resp.text), displayed_only=False)
    if not tables:
        return pd.DataFrame()
    df = max(tables, key=lambda t: t.shape[0] * t.shape[1])
    df.columns = df.columns.map(lambda c: str(c).strip())
    df = df.loc[:, ~df.columns.astype(str).str.contains(r"^Unnamed", case=False)]
    df = df.astype(str)
    keep = ["ทะเบียน", "เลขรถ", "ประเภทรถร่วม", "ประเภทยานพาหนะ", "ประเภทยานพาหนะเพิ่มเติม"]
    df = df[[c for c in keep if c in df.columns]]
    logging.info(f"Vehicle master: {len(df)} rows")
    return df


# ── task 4: ship.to ───────────────────────────────────────────────────────────

def fetch_ship_to(session):
    frames = []
    for cid in CUSTOMER_IDS:
        r = session.post(
            f"{BASE_URL}/report/excel/index.excel/type/ship.to",
            data={"customer_id": str(cid), "status": "A", "from_valid_date": "01/01/2025",
                  "submit": "พิมพ์", "display_type": "multiple-day", "report_type": "ship.to"},
            verify=False, timeout=600,
        )
        r.raise_for_status()
        frames.append(pd.read_excel(io.BytesIO(r.content), header=1))
        logging.info(f"ship.to customer_id={cid}: {len(frames[-1])} rows")
    return pd.concat(frames, ignore_index=True)


# ── task 5: final LDT + new ship_to ──────────────────────────────────────────

def build_final(df_processed, df_ship_to, df_zone, raw_ldt, vehicle):
    # Apply vehicle type overrides
    df = df_processed.copy()
    df["ทะเบียนหัว"] = df["ทะเบียนหัว"].astype(str).str.strip()
    vehicle["ทะเบียน"] = vehicle["ทะเบียน"].astype(str).str.strip()
    df = df.merge(vehicle[["ทะเบียน","เลขรถ","ประเภทยานพาหนะ"]],
                  how="left", left_on="ทะเบียนหัว", right_on="ทะเบียน")
    df["ประเภทยานพาหนะ"] = df["ประเภทยานพาหนะ"].astype(str).str.strip()
    df["บริการ"] = df["ประเภทยานพาหนะ"].map({"Mixer 10 ล้อ": "M005", "Mixer 6 ล้อ": "M031"}).fillna(df["บริการ"])
    df.loc[df["ประเภทยานพาหนะ"] == "Mixer 6 ล้อ", "นน ปลายทาง"] = 1
    df = df.drop(columns=["ทะเบียน","เลขรถ","ประเภทยานพาหนะ"], errors="ignore")

    # Format dates in final output
    for col in ["วันที่","วันที่ครบกำหนด"]:
        if col in df.columns:
            df[col] = df[col].apply(_fmt_date)
    for col in ["เวลาออกเดินทาง","วันเวลาอ้างอิง 1","วันเวลาอ้างอิง 2","วันเวลาอ้างอิง 3",
                "วันเวลาอ้างอิง 4","วันเวลาลงสินค้า","วันเวลาปิด LDT"]:
        if col in df.columns:
            df[col] = df[col].apply(_fmt_datetime)

    # Build new ship_to records
    check = df[["LDT","Ship To"]].merge(df_ship_to, left_on="Ship To", right_on="รหัส", how="left")
    check = check.drop_duplicates(subset=["LDT"], keep="first")
    check["is_NaN"] = check["ใช้งานตั้งแต่"].isna()
    new_st = check[check["is_NaN"]][["LDT","Ship To"]]
    new_st = new_st.merge(df[["LDT","เส้นทาง","แพล้นท์"]], on="LDT", how="left")
    new_st = new_st.merge(df_zone[["Plant","โซน","สภาพจราจร","จังหวัด","อำเภอ"]],
                          left_on="แพล้นท์", right_on="Plant", how="left")
    new_st = new_st.merge(raw_ldt[["LDT","SiteName","PlantToSiteDistance","SiteToPlantDistance"]],
                          on="LDT", how="left")
    new_st["ประเทศ"] = "ไทย"
    new_st = new_st.rename(columns={"PlantToSiteDistance":"ทางเรียบหนัก",
                                     "SiteToPlantDistance":"ทางเรียบเบา",
                                     "สภาพจราจร":"สภาพการจราจร","SiteName":"ชื่อไซร้งาน"})
    s = new_st["Ship To"].astype("string").fillna("").str.strip()
    new_st["เดินทางจาก"] = s.str[:4]
    new_st["เดินทางไป"] = s.str[4:].str.strip()
    desired = ["LDT","Ship To","เส้นทาง","ชื่อไซร้งาน","ลูกค้า","โซน","สภาพการจราจร","ประเทศ",
               "จังหวัด","อำเภอ","ที่อยู่","ระยะทาง","ตีเปล่า","ทางเรียบหนัก","ขึ้นเขาหนัก",
               "ขึ้นเขาสูงหนัก","ทางเรียบเบา","ขึ้นเขาเบา","ขึ้นเขาสูงเบา","สำรอง","เดินทางจาก","เดินทางไป"]
    for c in desired:
        if c not in new_st.columns:
            new_st[c] = None
    new_st = new_st[desired]
    new_st["ลูกค้า"] = new_st.apply(
        lambda r: 1019 if str(r["Ship To"]).startswith("SU") else 1018 if str(r["Ship To"]).startswith("SX") else r["ลูกค้า"], axis=1)
    for c in ["ระยะทาง","ตีเปล่า","ขึ้นเขาหนัก","ขึ้นเขาสูงหนัก","ขึ้นเขาเบา","ขึ้นเขาสูงเบา"]:
        new_st[c] = 0
    new_st["ที่อยู่"] = "-"
    new_st["สำรอง"] = 0
    new_st["เริ่มใช้งานตั้งแต่"] = datetime.today().replace(day=1).strftime("1/%m/%Y")
    new_st["ใช้งานถึงวันที่"] = datetime.today().replace(day=1, year=datetime.today().year + 1).strftime("1/%m/%Y")

    grouped = new_st.drop(columns=["LDT"], errors="ignore").groupby("Ship To").agg(
        {"ทางเรียบหนัก": lambda x: x[x != 0].mean(), "ทางเรียบเบา": lambda x: x[x != 0].mean()}
    ).reset_index()
    grouped.iloc[:, 1:] = np.ceil(grouped.iloc[:, 1:])
    grouped = grouped.fillna(0)
    out = new_st.drop(columns=["LDT"], errors="ignore").set_index("Ship To")
    grouped.set_index("Ship To", inplace=True)
    out.update(grouped)
    out.reset_index(inplace=True)
    out = out.drop_duplicates(subset=["Ship To"])
    out["รหัสอ้างอิงลูกค้า"] = ""
    out["ดึงระยะทางจาก Map API"] = "N"
    out = out.rename(columns={"เส้นทาง": "รหัสย่อย"})
    out["แพล้นท์"] = out["เดินทางจาก"]
    out = out[["Ship To","รหัสย่อย","รหัสอ้างอิงลูกค้า","ชื่อไซร้งาน","ลูกค้า","แพล้นท์",
               "โซน","สภาพการจราจร","เดินทางจาก","เดินทางไป","ที่อยู่","ระยะทาง","ตีเปล่า",
               "ทางเรียบหนัก","ขึ้นเขาหนัก","ขึ้นเขาสูงหนัก","ทางเรียบเบา","ขึ้นเขาเบา",
               "ขึ้นเขาสูงเบา","สำรอง","ใช้งานถึงวันที่","เริ่มใช้งานตั้งแต่","ดึงระยะทางจาก Map API"]]
    return df, out


# ── MongoDB write ─────────────────────────────────────────────────────────────

def write_to_mongo(df_ldt, df_new_ship_to, run_date, filename):
    if not MONGODB_URI:
        logging.warning("MONGODB_URI not set — skipping write")
        return
    client = MongoClient(MONGODB_URI)
    try:
        col = client["atms"]["ldt_runs"]

        def _coerce(v):
            if isinstance(v, decimal.Decimal):
                return float(v)
            return v

        def to_records(df):
            return [{k: _coerce(v) for k, v in row.items()}
                    for row in df.where(pd.notnull(df), None).to_dict("records")]
        col.replace_one(
            {"pipeline": PIPELINE, "run_date": run_date},
            {"pipeline": PIPELINE, "run_date": run_date, "filename": filename,
             "ldt_rows": to_records(df_ldt), "new_ship_to_rows": to_records(df_new_ship_to),
             "ldt_count": len(df_ldt), "new_ship_to_count": len(df_new_ship_to),
             "created_at": datetime.now(timezone.utc)},
            upsert=True,
        )
        logging.info(f"MongoDB OK: {PIPELINE}/{run_date} — {len(df_ldt)} LDT, {len(df_new_ship_to)} new ship_to")
    finally:
        client.close()


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    yesterday = datetime.today() - timedelta(days=1)
    run_date = yesterday.strftime("%Y-%m-%d")
    target_date = yesterday.strftime("%d/%m/%Y")
    filename = f"LDTASIA_{yesterday.strftime('%d%m%Y')}(มี+ไม่มีshipto).xlsx"

    logging.info(f"=== Asia pipeline started for {run_date} ===")

    # Load static zone reference
    df_zone = pd.read_excel(BASE_DIR / "data" / "zone" / "df_zone.xlsx")

    # Step 1: MySQL
    logging.info("Step 1/4: Fetching LDT from MySQL…")
    df_ldt, raw_ldt = fetch_ldt()
    logging.info(f"  → {len(df_ldt)} LDT rows")

    # Steps 2, 3b & 4 share one ATMS session
    with requests.Session() as session:
        atms_login(session)

        # Step 2: vehicle daily
        logging.info("Step 2/4: Downloading vehicle daily report…")
        df_driver = fetch_driver(session, target_date)
        logging.info(f"  → {len(df_driver)} driver rows")

        # Step 3b: vehiclemaster (dynamic, fallback to disk)
        logging.info("Step 3b: Downloading vehicle master…")
        try:
            vehicle = fetch_vehiclemaster(session)
        except Exception as e:
            logging.warning(f"Vehicle master fetch failed ({e}), using disk copy")
            vehicle = pd.read_excel(BASE_DIR / "raw_data" / "vehiclemaster.xlsx")

        # Step 4: ship.to (slow — up to 600s)
        logging.info("Step 4/4: Downloading ship.to (may take several minutes)…")
        df_ship_to = fetch_ship_to(session)
        logging.info(f"  → {len(df_ship_to)} ship.to rows")

    # Step 3: merge
    logging.info("Step 3/4: Processing LDT + driver + zone…")
    df_processed = process_ldt(df_ldt, df_driver, df_zone)
    logging.info(f"  → {len(df_processed)} processed rows")

    # Step 5: final output
    logging.info("Building final LDT + new ship_to…")
    df_final, df_new_ship_to = build_final(df_processed, df_ship_to, df_zone, raw_ldt, vehicle)
    logging.info(f"  → {len(df_final)} final LDT, {len(df_new_ship_to)} new ship_to")

    # Write to MongoDB
    write_to_mongo(df_final, df_new_ship_to, run_date, filename)

    logging.info(f"=== Asia pipeline complete ===")
    print(f"Done: {len(df_final)} LDT rows, {len(df_new_ship_to)} new ship_to → MongoDB")
