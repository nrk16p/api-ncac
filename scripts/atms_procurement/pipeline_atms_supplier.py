"""
Pipeline: ATMS Supplier master → เครดิตเทอมเจ้าหนี้ (feeds mena-wms /ap-tracking)

Scrapes /inv/supplier/index (1 หน้า = 100 แถว) และเขียนลง MongoDB:
  - atms.supplier_master        mirror ดิบทั้งชุด (key = atmsId)
  - master_data.ap_supplier     เฉพาะเจ้าที่ "มีใบ DD จริง" พร้อม ddCount/lastDdAt

ทำไมต้องมีทั้งสองชั้น: atms.* เป็นฐาน mirror อ่านอย่างเดียวของ ATMS ส่วน master_data.*
เป็นชั้น overlay ของแอป — คนตั้งเทอมทับได้ที่ ap_supplier.override ซึ่ง pipeline นี้ห้ามทับ

หมายเหตุ: เทอมที่ใช้จริงบนใบ DD คิดที่ mena-wms (resolveCreditTerm) ตามลำดับ
  override ของคน > "ap term" บน PO ของใบนั้น > ค่าจาก master ตรงนี้
ค่าที่ pipeline นี้เติมจึงเป็น "ตัวสำรอง" ของใบที่ไม่มี PO ผูก หรือ PO ไม่ได้ระบุเทอม

Auth: ATMS_USERNAME / ATMS_PASSWORD (หรือ PHPSESSID สำหรับเทสต์เครื่องตัวเอง)
Run log: atms.procurement_runs  (pipeline="atms_supplier")

รันเดี่ยว ๆ (pipeline_routes.py เรียกเป็น subprocess):
  python scripts/atms_procurement/pipeline_atms_supplier.py
"""

import os
import re
import sys
import time
from datetime import datetime

import requests
import urllib3
from bs4 import BeautifulSoup
from pymongo import MongoClient, UpdateOne

urllib3.disable_warnings()

BASE = "https://www.mena-atms.com"
LOGIN_URL = f"{BASE}/account/user/login"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
MONGO_URI = os.getenv("MONGODB_URI")

# ATMS เก็บ row-per-page ไว้ฝั่งเซิร์ฟเวอร์ต่อผู้ใช้ ไม่ใช่ต่อคำขอ — ตั้งแล้วต้องคืนค่าเดิมเสมอ
# ไม่งั้นหน้าอื่นของ user คนเดียวกันจะโดนพลอยเปลี่ยนตาม
PER_PAGE = int(os.getenv("ATMS_SUPPLIER_PER_PAGE", "100"))
PER_PAGE_DEFAULT = 25
PAGE_CAP = 60                       # กันลูปไม่รู้จบถ้า ATMS คืนหน้าเดิมซ้ำ ๆ


def log(*a):
    print("[atms_supplier]", *a, file=sys.stderr, flush=True)


def ensure_index(col, key, **kw):
    try:
        col.create_index(key, **kw)
    except Exception:
        pass


# ── auth (flow เดียวกับ pipeline_atms_procurement.get_session) ───────────────
def get_session():
    s = requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": UA, "Accept-Language": "th,en;q=0.8"})
    ck = os.getenv("PHPSESSID")
    if ck:
        s.cookies.set("PHPSESSID", ck.split("=")[-1].strip(), domain="www.mena-atms.com")
        return s
    user, pw = os.getenv("ATMS_USERNAME"), os.getenv("ATMS_PASSWORD")
    if not user or not pw:
        raise RuntimeError("ATMS_USERNAME / ATMS_PASSWORD not set")
    s.get(LOGIN_URL, timeout=30)
    s.post(LOGIN_URL, data={"username": user, "password": pw, "submit": "login",
                            "next": "", "forgotPasswd": "ลืมรหัสผ่าน"},
           timeout=30, allow_redirects=True)
    if "account/user/login" in s.get(f"{BASE}/inv/supplier/index", timeout=30).url:
        raise RuntimeError("ATMS login failed — check ATMS_USERNAME / ATMS_PASSWORD")
    log("logged in as", user)
    return s


def _get(session, url, params=None, tries=4):
    last = None
    for a in range(tries):
        try:
            r = session.get(url, params=params, timeout=90)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(2 * (a + 1))
    raise last


def _set_rows_per_page(session, n):
    try:
        session.get(f"{BASE}/account/user/set.row.per.page/",
                    params={"row-per-page": n}, timeout=30)
    except requests.RequestException as e:
        log("set.row.per.page failed (ไม่ร้ายแรง):", e)


# ── scrape ──────────────────────────────────────────────────────────────────
def _parse_page(html):
    """คืน (rows, total) — total มาจากแถบ '1 - 100 / 1,057' อ่านได้เฉพาะหน้าแรกก็พอ"""
    soup = BeautifulSoup(html, "html.parser")
    total = None
    summary = soup.select_one(".page-summary")
    if summary:
        m = re.search(r"/\s*([\d,]+)", summary.get_text())
        if m:
            total = int(m.group(1).replace(",", ""))

    rows = []
    for t in soup.find_all("table"):
        hdr = [th.get_text(strip=True) for th in t.find_all("th")]
        if "ap term" not in hdr:
            continue
        ix = {h: i for i, h in enumerate(hdr)}
        for tr in t.find_all("tr")[1:]:
            td = [x.get_text(strip=True) for x in tr.find_all("td")]
            if len(td) < len(hdr) - 1:
                continue
            a = tr.find("a", href=re.compile(r"/inv/supplier/(view|edit)/id/\d+"))
            if not a:                       # แถวสรุป/แถวเปล่า
                continue

            def g(k):
                i = ix.get(k)
                return td[i] if i is not None and i < len(td) else ""

            rows.append({
                "atmsId": int(re.search(r"/id/(\d+)", a["href"]).group(1)),
                "code": g("รหัส"), "name": g("ชื่อ"), "branch": g("สาขา"),
                "brand": g("ยี่ห้อ"), "type": g("ประเภท"), "phone": g("โทรศัพท์"),
                "bankInfo": g("ข้อมูล"), "email": g("อีเมล์"), "rpt": g("RPT"),
                "vatRate": g("อัตราภาษีมูลค่าเพิ่ม"), "apTerm": g("ap term"),
            })
    return rows, total


def scrape_suppliers(session):
    _set_rows_per_page(session, PER_PAGE)
    try:
        out, seen, total, page = [], set(), None, 1
        while page <= PAGE_CAP:
            r = _get(session, f"{BASE}/inv/supplier/index/",
                     {"page": page, "order_by": "s.code asc"})
            rows, tot = _parse_page(r.text)
            if total is None and tot:
                total = tot
                log(f"total {total} ราย · หน้าละ {PER_PAGE}")
            fresh = [x for x in rows if x["atmsId"] not in seen]
            for x in fresh:
                seen.add(x["atmsId"])
            out.extend(fresh)
            log(f"  page {page}: +{len(fresh)} (รวม {len(out)}/{total or '?'})")
            # หน้าเปล่า หรือหน้านี้ไม่มีของใหม่เลย = จบ (กันกรณี ATMS คืนหน้าสุดท้ายซ้ำ)
            if not rows or not fresh or (total and len(out) >= total):
                break
            page += 1
            time.sleep(0.6)
        if total and len(out) < total:
            log(f"⚠️ ได้ไม่ครบ: {len(out)}/{total}")
        return out, total
    finally:
        _set_rows_per_page(session, PER_PAGE_DEFAULT)


# ── write ───────────────────────────────────────────────────────────────────
def save_mirror(db_atms, rows, now):
    col = db_atms["supplier_master"]
    ensure_index(col, "atmsId", unique=True)
    ensure_index(col, "name")
    col.bulk_write(
        [UpdateOne({"atmsId": r["atmsId"]}, {"$set": {**r, "syncedAt": now}}, upsert=True)
         for r in rows], ordered=False)
    return len(rows)


def dd_activity(db_atms):
    """จำนวนใบ DD + วันรับล่าสุด ต่อชื่อเจ้าหนี้
    received_at เป็น string "DD/MM/YYYY HH:mm" — เรียง/หา max ตรง ๆ ไม่ได้ ต้องแปลงก่อน"""
    out = {}
    for g in db_atms["deposit_header"].aggregate([
        {"$match": {"supplier": {"$nin": ["", None]}}},
        {"$addFields": {"_d": {"$dateFromString": {
            "dateString": "$received_at", "format": "%d/%m/%Y %H:%M",
            "onError": None, "onNull": None}}}},
        {"$group": {"_id": "$supplier", "n": {"$sum": 1}, "last": {"$max": "$_d"}}},
    ]):
        out[str(g["_id"]).strip()] = (
            g["n"], g["last"].strftime("%Y-%m-%d") if g.get("last") else "")
    return out


def save_ap_supplier(db_md, rows, activity, now):
    """เติมเทอมให้เฉพาะเจ้าที่มีใบ DD จริง — ไม่ยัด 1,057 ราย ลงหน้าที่คนต้องมานั่งไล่

    ห้ามทับ ap_supplier.override (ค่าที่คนตั้งเอง) และห้ามลดเทอมที่รู้อยู่แล้วให้กลายเป็นว่าง
    เมื่อ ATMS เว้น ap term ไว้
    """
    col = db_md["ap_supplier"]
    ensure_index(col, "name", unique=True)

    # ชื่อซ้ำใน ATMS (มี 1 คู่ ณ 24/08/2026) — เลือกรายที่มี ap term ก่อน
    by_name = {}
    for r in rows:
        k = (r.get("name") or "").strip()
        if not k:
            continue
        prev = by_name.get(k)
        if not prev or (not prev.get("apTerm") and r.get("apTerm")):
            by_name[k] = r

    existing = {str(x.get("name", "")).strip(): x
                for x in col.find({}, {"_id": 0, "name": 1, "creditTerm": 1,
                                       "override": 1, "updatedBy": 1})}
    ops, stats = [], {"matched": 0, "no_atms": 0, "no_term": 0}
    for name, (n, last) in activity.items():
        hit = by_name.get(name)
        if not hit:
            stats["no_atms"] += 1
            continue
        stats["matched"] += 1
        cur = existing.get(name, {})
        atms_term = (hit.get("apTerm") or "").strip()
        cur_term = str(cur.get("creditTerm") or "").strip()
        # เทอมที่ "มนุษย์" ตั้งไว้ (ไม่ใช่ seed จาก Excel และไม่ใช่ตัว sync เอง) = override
        by_system = str(cur.get("updatedBy") or "") in ("", "seed", "atms-sync")
        override = str(cur.get("override") or "").strip()
        if cur_term and not by_system and cur_term != atms_term:
            override = cur_term
        credit_term = override or atms_term or cur_term
        if not credit_term:
            stats["no_term"] += 1

        ops.append(UpdateOne({"name": name}, {"$set": {
            "name": name, "creditTerm": credit_term, "override": override,
            "atmsTerm": atms_term, "atmsId": hit["atmsId"],
            "atmsCode": (hit.get("code") or "").strip(),
            "atmsType": (hit.get("type") or "").strip(),
            "atmsBranch": (hit.get("branch") or "").strip(),
            "ddCount": n, "lastDdAt": last, "syncedAt": now,
            **({} if cur else {"updatedBy": "atms-sync", "updatedAt": now}),
        }}, upsert=True))

    if ops:
        res = col.bulk_write(ops, ordered=False)
        stats["upserted"] = res.upserted_count
        stats["modified"] = res.modified_count
    return stats


def main():
    if not MONGO_URI:
        log("MONGODB_URI not set")
        sys.exit(1)
    started = datetime.utcnow()
    now = started.isoformat() + "Z"

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    atms, md = client["atms"], client["master_data"]
    counts, err = {}, None
    try:
        session = get_session()
        rows, total = scrape_suppliers(session)
        if not rows:
            raise RuntimeError("scrape ไม่ได้แถวเลย — หน้า /inv/supplier/index เปลี่ยนโครงสร้าง?")
        counts["scraped"] = len(rows)
        counts["expected"] = total
        counts["mirror"] = save_mirror(atms, rows, now)
        activity = dd_activity(atms)
        counts["dd_suppliers"] = len(activity)
        counts.update(save_ap_supplier(md, rows, activity, now))
        log("ok", counts)
    except Exception as e:
        err = str(e)
        log("ERROR:", err)

    finished = datetime.utcnow()
    atms["procurement_runs"].insert_one({
        "pipeline": "atms_supplier", "created_at": finished,
        "started_at": started, "finished_at": finished,
        "counts": counts, "ok": err is None, "error": err,
        "duration_sec": round((finished - started).total_seconds(), 1),
    })
    client.close()
    if err:
        sys.exit(1)


if __name__ == "__main__":
    main()
