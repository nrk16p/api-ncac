"""One-off: backfill atms.purchase_requests สำหรับปี 2025 (ปี 2569 มีครบแล้ว)
ใช้ฟังก์ชันเดิมจาก pipeline_atms_procurement.py ไม่แก้ pipeline ตัวจริง
  --probe  ดึงหน้าแรกหน้าเดียว ไม่เขียน Mongo
  --apply  ดึงจริงทั้งช่วงแล้ว upsert

รันจากเครื่องเท่านั้น (อ่าน .env จาก path ในเครื่อง) ไม่ได้ถูกเรียกโดย route หรือ cron ใดๆ
คีย์อยู่ที่ ตั้ง row-per-page=1000 ก่อนเริ่ม — 17,704 ใบเหลือ 18 requests แทนหลายร้อย
รอบจริง 2026-08-25: ปี 2568 เข้า 17,704 ใบ (11,957 -> 29,661) 102 วิ ไม่เจอ 500 เลย
ดึงเฉพาะ purchase_requests ข้าม PO/deposit/items ซึ่งเป็นส่วนที่หนักจริง
"""
import os, re, sys, time
from datetime import datetime

ROOT = os.path.expanduser("~/Documents/project")
SCRIPTS = f"{ROOT}/ncac/api-ncac/scripts/atms_procurement"

def load_env(path, keys):
    txt = open(os.path.expanduser(path), encoding="utf-8").read()
    for k in keys:
        m = re.search(rf'^{k}\s*=\s*"?([^"\n]+)"?', txt, re.M)
        if m:
            os.environ[k] = m.group(1).strip()

load_env(f"{ROOT}/task/1_Jan2026/ext_cost/.env", ["ATMS_USERNAME", "ATMS_PASSWORD"])
txt = open(f"{ROOT}/master-sku-web/.env", encoding="utf-8").read()
os.environ["MONGODB_URI"] = re.search(r'^MONGO_URI\s*=\s*"?([^"\n]+)"?', txt, re.M).group(1).strip()

sys.path.insert(0, SCRIPTS)
import pipeline_atms_procurement as P
from pymongo import MongoClient

FROM_DATE, TO_DATE = "01/01/2025", "31/12/2025"
PACE, MAX_PAGES, ROWS_PER_PAGE = 3.0, 60, 1000
PARAMS = {"code": "", "user": "", "remark": "", "inventory_id": "", "department_id": "",
          "is_approved": "", "has_po": "", "from_t_date": FROM_DATE, "to_t_date": TO_DATE,
          "submit": "ค้นหา", "order_by": "pr.code desc"}
URL = f"{P.BASE}/inv/purchase.request/index"

# ── pacing + เพดานหน้า (pipeline เดิมเว้น 0.4 วิ ซึ่งเร็วเกินสำหรับรอบย้อนหลัง) ──
_n = 0
_orig_get = P._get
def paced_get(session, url, params=None, tries=4, timeout=45):
    global _n
    _n += 1
    if _n > 1:
        time.sleep(PACE)
    pg = (params or {}).get("page", 1)
    print(f"  [{_n:>3}] page {pg}", flush=True)
    return _orig_get(session, url, params, tries, timeout)
P._get = paced_get

_orig_next = P.next_page
def capped_next(soup, cur):
    nxt = _orig_next(soup, cur)
    if nxt and nxt > MAX_PAGES:
        print(f"  !! ชนเพดาน MAX_PAGES={MAX_PAGES} หยุดไว้ก่อน", flush=True)
        return None
    return nxt
P.next_page = capped_next

mode = "--apply" if "--apply" in sys.argv else "--probe"
print(f"โหมด {mode} · ช่วง {FROM_DATE} – {TO_DATE} · {ROWS_PER_PAGE} แถว/หน้า · เว้น {PACE} วิ/หน้า")

s = P.get_session()
r = s.get(f"{P.BASE}/account/user/set.row.per.page/", params={"row-per-page": ROWS_PER_PAGE}, timeout=30)
print("ตั้ง rows-per-page:", r.status_code)

if mode == "--probe":
    soup = P.soup_of(s, URL, PARAMS)
    table = P.data_table(soup)
    heads = P.headers_of(table)
    body = table.find("tbody") or table
    rows = [tr for tr in body.find_all("tr") if tr.find_all("td")]
    print(f"\nคอลัมน์ ({len(heads)}): {heads}")
    print(f"แถวในหน้าแรก: {len(rows)}")
    txt = soup.get_text(" ", strip=True)
    m = re.search(r"(\d[\d,]*)\s*-\s*(\d[\d,]*)\s*/\s*(\d[\d,]*)", txt)
    if m:
        total = int(m.group(3).replace(",", ""))
        print(f"แถบเลขหน้า: {m.group(0)}  → รวม {total:,} ใบ ≈ {-(-total // max(len(rows),1))} หน้า")
    i_pr = heads.index(P.PR_KEY) if P.PR_KEY in heads else -1
    i_who = heads.index("ผู้ขอซื้อ") if "ผู้ขอซื้อ" in heads else -1
    i_date = heads.index("วันที่") if "วันที่" in heads else -1
    print("\nตัวอย่าง 5 แถวแรก (PR · วันที่ · ผู้ขอซื้อ):")
    for tr in rows[:5]:
        c = [x.get_text(" ", strip=True) for x in tr.find_all(["td", "th"])]
        g = lambda i: c[i] if 0 <= i < len(c) else "?"
        print("  ", g(i_pr), "·", g(i_date), "·", g(i_who))
    print("\nยังไม่เขียน Mongo — probe อย่างเดียว")
    sys.exit(0)

cli = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=10_000)
db = cli["atms"]
before = db["purchase_requests"].count_documents({})
started = datetime.utcnow()
res = P._scrape_list(s, URL, PARAMS, P.PR_KEY, db, "purchase_requests", approval=True)
finished = datetime.utcnow()
after = db["purchase_requests"].count_documents({})
print(f"\nผล: {res} · docs {before:,} → {after:,} (+{after-before:,}) · {(finished-started).total_seconds():.0f} วิ · {_n} requests")
db["procurement_runs"].insert_one({
    "pipeline": "atms_procurement_backfill_pr_2025", "created_at": finished,
    "started_at": started, "finished_at": finished, "from_date": FROM_DATE, "to_date": TO_DATE,
    "counts": {"purchase_requests": res}, "docs_before": before, "docs_after": after,
    "requests": _n, "ok": True, "duration_sec": round((finished-started).total_seconds(), 1),
})
cli.close()
