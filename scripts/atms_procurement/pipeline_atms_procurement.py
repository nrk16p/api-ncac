"""
Pipeline: ATMS Procurement refresh (PR / PO / deposit_header / PR-PO items)
Feeds mena-wms /pr (การจัดการ PR).

Scrapes www.mena-atms.com and upserts into MongoDB db `atms`:
  - purchase_requests        (list + approval icon)
  - purchase_orders          (list)
  - deposit_header           (list/index only — /pr ใช้แค่ purchase_order)
  - purchase_request_items   (detail line items)
  - purchase_order_items     (detail line items)

Auth: logs in with ATMS_USERNAME / ATMS_PASSWORD (or PHPSESSID env for local test).
Window: rolling from the 1st of *last* month (catches new + late-updated rows).
Run log: atms.procurement_runs  (pipeline="atms_procurement").

Run standalone (invoked as a subprocess by routes/pipeline/pipeline_routes.py):
  python scripts/atms_procurement/pipeline_atms_procurement.py
"""

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests
import urllib3
from bs4 import BeautifulSoup
from pymongo import MongoClient, UpdateOne, ReplaceOne

urllib3.disable_warnings()

BASE = "https://www.mena-atms.com"
LOGIN_URL = f"{BASE}/account/user/login"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")
MONGO_URI = os.getenv("MONGODB_URI")
PR_KEY = "ใบขอสั่งซื้อ (PR)"
PO_KEY = "รหัส"


def log(*a):
    print("[atms_procurement]", *a, file=sys.stderr, flush=True)


def ensure_index(col, key, **kw):
    """create_index but ignore conflicts with an existing index of the same key."""
    try:
        col.create_index(key, **kw)
    except Exception:
        pass


# ── auth ────────────────────────────────────────────────────────────────────
def get_session():
    s = requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": UA, "Accept-Language": "th,en;q=0.8"})
    ck = os.getenv("PHPSESSID")
    if ck:  # local test shortcut
        s.cookies.set("PHPSESSID", ck.split("=")[-1].strip(), domain="www.mena-atms.com")
        return s
    user, pw = os.getenv("ATMS_USERNAME"), os.getenv("ATMS_PASSWORD")
    if not user or not pw:
        raise RuntimeError("ATMS_USERNAME / ATMS_PASSWORD not set")
    s.get(LOGIN_URL, timeout=30)  # seed PHPSESSID
    s.post(LOGIN_URL, data={"username": user, "password": pw, "submit": "login",
                            "next": "", "forgotPasswd": "ลืมรหัสผ่าน"},
           timeout=30, allow_redirects=True)
    chk = s.get(f"{BASE}/inv/purchase.order/index", timeout=30)
    if "account/user/login" in chk.url:
        raise RuntimeError("ATMS login failed — check ATMS_USERNAME / ATMS_PASSWORD")
    log("logged in as", user)
    return s


# ── html helpers ────────────────────────────────────────────────────────────
def _get(session, url, params=None, tries=4):
    """GET พร้อม retry+backoff — ทน ConnectionAborted/transient จาก ATMS throttle"""
    last = None
    for a in range(tries):
        try:
            r = session.get(url, params=params, timeout=45)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(2 * (a + 1))   # 2s, 4s, 6s
    raise last


def soup_of(session, url, params=None):
    return BeautifulSoup(_get(session, url, params).text, "html.parser")


def data_table(soup):
    tables = soup.find_all("table")
    return max(tables, key=lambda t: len(t.find_all("tr")), default=None)


def headers_of(table):
    thead = table.find("thead")
    hr = thead.find("tr") if thead else table.find("tr")
    return [th.get_text(strip=True) for th in hr.find_all(["th", "td"])] if hr else []


def next_page(soup, cur):
    for a in soup.find_all("a", href=True):
        m = re.search(r"[?&]page=(\d+)", a["href"])
        if m and int(m.group(1)) > cur:
            return int(m.group(1))
    return None


def num(v):
    v = (v or "").replace(",", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def split_sku(v):
    v = (v or "").strip()
    if " : " in v:
        c, n = v.split(" : ", 1)
        return c.strip(), n.strip()
    return "", v


# ── PR / PO list ────────────────────────────────────────────────────────────
def _scrape_list(session, base, params_base, key_field, db, coll, approval=False):
    col = db[coll]
    ensure_index(col, key_field)
    now = datetime.utcnow()
    ins = upd = page = 0
    page = 1
    headers = None
    while True:
        params = dict(params_base)
        if page > 1:
            params["page"] = page
        soup = soup_of(session, base, params)
        table = data_table(soup)
        if not table:
            break
        if headers is None:
            headers = headers_of(table)
        body = table.find("tbody") or table
        rows = []
        for tr in body.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells or all(c.name == "th" for c in cells):
                continue
            vals = [c.get_text(" ", strip=True) for c in cells]
            if len(vals) != len(headers):
                vals = (vals + [""] * len(headers))[: len(headers)]
            row = {}
            for h, v in zip(headers, vals):
                if h == "":
                    continue
                if h == "รวม":
                    row[h] = num(v)
                else:
                    row[h] = v or None
            if approval and "is approved" in headers:
                ai = headers.index("is approved")
                img = cells[ai].find("img") if ai < len(cells) else None
                src = img.get("src", "") if img else ""
                row["is approved"] = True if "check-square" in src else False if "x-square" in src else None
            if row.get(key_field):
                rows.append(row)
        if not rows:
            break
        ops = [UpdateOne({key_field: r[key_field]}, {"$set": {**r, "scraped_at": now}}, upsert=True) for r in rows]
        res = col.bulk_write(ops, ordered=False)
        ins += res.upserted_count
        upd += res.modified_count
        nxt = next_page(soup, page)
        if not nxt:
            break
        page = nxt
        time.sleep(0.4)
    return {"inserted": ins, "updated": upd}


def scrape_pr(session, from_date, db):
    params = {"code": "", "user": "", "remark": "", "inventory_id": "", "department_id": "",
              "is_approved": "", "has_po": "", "from_t_date": from_date, "to_t_date": "",
              "submit": "ค้นหา", "order_by": "pr.code desc"}
    return _scrape_list(session, f"{BASE}/inv/purchase.request/index", params, PR_KEY,
                        db, "purchase_requests", approval=True)


def scrape_po(session, from_date, db):
    params = {"code": "", "purchase_request": "", "supplier": "", "vehicle": "",
              "inventory_id": "", "department_id": "", "received_status": "",
              "from_t_date": from_date, "to_t_date": "", "related_party_transaction": "",
              "submit": "ค้นหา", "order_by": "po.code desc"}
    return _scrape_list(session, f"{BASE}/inv/purchase.order/index", params, PO_KEY,
                        db, "purchase_orders", approval=False)


# ── deposit_header + deposit_items ────────────────────────────────────────────
DEPOSIT_COLS = ["deposit_code", "warehouse", "purchase_order", "withdraw_ref",
                "supplier", "supplier_ref_no", "amount", "created_at", "received_at",
                "approver", "user"]


def scrape_deposit(session, from_date, db, collect_ids=None):
    col = db["deposit_header"]
    ensure_index(col, "deposit_id", unique=True, background=True)
    up = page = 0
    page = 1
    while True:
        params = {"code": "", "purchase_order": "", "supplier": "", "supplier_ref_no": "",
                  "user": "", "from_created_at": "", "to_created_at": "",
                  "from_received_at": from_date, "to_received_at": "", "inventory_id": "",
                  "submit": "ค้นหา", "order_by": "gm.code desc"}
        if page > 1:
            params["page"] = page
        soup = soup_of(session, f"{BASE}/inv/deposit/index", params)
        table = soup.find("table", class_="defaultTable") or data_table(soup)
        if not table:
            break
        rows = []
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            texts = [c.get_text(" ", strip=True) for c in cells]
            if len(texts) < len(DEPOSIT_COLS):
                continue
            dep = wid = poid = None
            for a in tr.find_all("a", href=True):
                m = re.search(r"/deposit/view/id/(\d+)", a["href"])
                dep = int(m.group(1)) if m else dep
                m = re.search(r"/withdraw/view/id/(\d+)", a["href"])
                wid = int(m.group(1)) if m else wid
                m = re.search(r"/purchase\.order/view/id/(\d+)", a["href"])
                poid = int(m.group(1)) if m else poid
            row = {"deposit_id": dep, "withdraw_id": wid, "purchase_order_id": poid}
            row.update(dict(zip(DEPOSIT_COLS, texts)))
            if dep:
                rows.append(row)
                if collect_ids is not None:
                    collect_ids.append(dep)
        if not rows:
            break
        ops = [ReplaceOne({"deposit_id": r["deposit_id"]}, r, upsert=True) for r in rows]
        col.bulk_write(ops, ordered=False)
        up += len(ops)
        nxt = next_page(soup, page)
        if not nxt:
            break
        page = nxt
        time.sleep(0.3)
    return {"upserted": up}


def parse_deposit_items(html, deposit_id):
    """แถวสินค้าในหน้า /inv/deposit/view/id/<id> → list ของ dict

    รูปแบบคอลัมน์ยึดตาม atms-extractor (extractor/deposit_parser.parse_deposit_detail)
    ที่ใช้เก็บ deposit_items ชุดเดิมมาตลอด — ชื่อฟิลด์ต้องตรงกันเป๊ะ เพราะโมดัลรายละเอียดใบ DD
    ของ mena-wms (/ap-tracking) อ่าน item/qty/unit_price/total ตรงจากฟิลด์พวกนี้
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="defaultTable") or data_table(soup)
    if not table:
        return []
    items = []
    for tr in table.find_all("tr")[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        # แถวสรุป (grand total / VAT) มีคอลัมน์ไม่ครบ — ข้าม
        if len(cells) < 7:
            continue
        items.append({
            "deposit_id": deposit_id,
            "parts_group": cells[0], "item": cells[1], "serial_no": cells[2],
            "qty": cells[3], "unit_price": cells[4], "total": cells[5], "remark": cells[6],
        })
    return items


def scrape_deposit_items(session, deposit_ids, db, workers=None):
    """ดึงรายการสินค้าของใบ DD ที่ "ยังไม่มีใน deposit_items" เท่านั้น

    ทำไมต้องมี: หน้า index ให้แค่หัวใบ ไม่มีรายการสินค้า — ก่อนหน้านี้ deposit_items
    ถูกเติมด้วย atms-extractor (cli.py deposit) ที่รันมือเท่านั้น ทำให้ใบใหม่ ๆ ไม่มีรายการ
    และโมดัลรายละเอียดใน /ap-tracking ขึ้น "ไม่มีรายการสินค้าในระบบ" ทุกใบ

    ทำไมเอาเฉพาะใบที่ยังไม่มี: หน้า detail ต้องยิงทีละใบ (1 request/ใบ) ถ้าดึงใหม่ทั้งหน้าต่าง
    ทุกรอบจะกิน 2-4 พัน request ต่อรอบ × 5 รอบ/วัน — รอบแรกจึงเป็นการเติมย้อนหลังให้เอง
    แล้วรอบถัด ๆ ไปเหลือเฉพาะใบใหม่ของวันนั้น (หลักสิบ)
    ข้อแลกเปลี่ยน: ใบที่ถูกแก้รายการทีหลังใน ATMS จะไม่ถูกดึงซ้ำ — ล้าง deposit_items ของใบนั้น
    แล้วให้รอบถัดไปดึงใหม่ ถ้าเจอเคสแบบนั้น
    """
    if workers is None:
        workers = int(os.getenv("ATMS_ITEM_WORKERS", "8"))
    # เพดานต่อรอบ กันรอบแรก (เติมย้อนหลัง) ลากยาวจนชน timeout ของ Render — ที่เหลือไหลไปรอบถัดไป
    cap = int(os.getenv("ATMS_DD_ITEM_LIMIT", "1500"))
    col = db["deposit_items"]
    ensure_index(col, "deposit_id", background=True)

    ids = sorted({int(i) for i in deposit_ids if i})
    if not ids:
        return {"todo": 0, "fetched": 0, "items": 0, "remaining": 0, "errors": 0}
    # ถามฐานเป็นก้อนละ 1000 id (ใช้ index deposit_id) ว่ามีรายการอยู่แล้วใบไหนบ้าง
    have = set()
    for i in range(0, len(ids), 1000):
        have.update(col.distinct("deposit_id", {"deposit_id": {"$in": ids[i:i + 1000]}}))
    missing = [i for i in ids if i not in have]
    todo, remaining = missing[:cap], max(0, len(missing) - cap)
    if remaining:
        log(f"deposit_items: ค้างอีก {remaining:,} ใบ (เพดาน {cap:,}/รอบ) — จะดึงต่อในรอบถัดไป")
    if not todo:
        return {"todo": 0, "fetched": 0, "items": 0, "remaining": 0, "errors": 0}

    now = datetime.utcnow()
    fetched = errors = total_items = 0

    def work(dep_id):
        for _ in range(2):                       # retry 1 ครั้ง (transient/หน้าว่าง)
            try:
                r = session.get(f"{BASE}/inv/deposit/view/id/{dep_id}", timeout=45)
                r.raise_for_status()
                return parse_deposit_items(r.text, dep_id)
            except Exception:
                time.sleep(0.4)
        return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, d): d for d in todo}
        for fut in as_completed(futs):
            dep_id = futs[fut]
            items = fut.result()
            if items is None:                    # ยิงไม่สำเร็จ — ปล่อยให้รอบหน้าลองใหม่ (ยังนับว่า missing)
                errors += 1
                continue
            fetched += 1
            if not items:                        # ใบที่ไม่มีรายการจริง ๆ ก็มี — ไม่ต้องเขียนอะไร
                continue
            for it in items:
                it["scraped_at"] = now
            col.delete_many({"deposit_id": dep_id})
            col.insert_many(items, ordered=False)
            total_items += len(items)

    return {"todo": len(todo), "fetched": fetched, "items": total_items,
            "remaining": remaining, "errors": errors}


# ── PR / PO line items (detail) ───────────────────────────────────────────────
def _item_table(soup):
    best = None
    for t in soup.find_all("table"):
        heads = [th.get_text(strip=True) for th in t.find_all("th")]
        if "สินค้า" in heads and any("unit" in h.lower() for h in heads):
            best = (t, heads)
    return best


def _parse_items(html, code, code_key):
    found = _item_table(BeautifulSoup(html, "html.parser"))
    if not found:
        return []
    table, heads = found
    idx = {h: i for i, h in enumerate(heads)}

    def cell(cells, name):
        i = idx.get(name)
        return cells[i].get_text(" ", strip=True) if (i is not None and i < len(cells)) else ""

    items, seq = [], 0
    for tr in (table.find("tbody") or table).find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        raw = cell(cells, "สินค้า")
        if not raw:
            continue
        sku, name = split_sku(raw)
        seq += 1
        row = {code_key: code, "seq": seq, "group": cell(cells, "กลุ่มสินค้า"),
               "sku": sku, "name": name, "amount": num(cell(cells, "amount")),
               "unit_price": num(cell(cells, "unit price")), "total": num(cell(cells, "รวม")),
               "note": cell(cells, "หมายเหตุ") or None}
        if "รับ" in idx:
            row["received"] = num(cell(cells, "รับ"))
        if "ค้างรับ" in idx:
            row["outstanding"] = num(cell(cells, "ค้างรับ"))
        if "คลังสินค้า" in idx:
            row["warehouse"] = cell(cells, "คลังสินค้า")
        items.append(row)
    return items


def _collect_codes(session, index_url, id_pat, from_date, order_by):
    pairs, seen, page = [], set(), 1
    while True:
        params = {"from_t_date": from_date, "to_t_date": "", "submit": "ค้นหา", "order_by": order_by}
        if page > 1:
            params["page"] = page
        soup = soup_of(session, index_url, params)
        table = data_table(soup)
        if not table:
            break
        rows_this = 0
        for tr in (table.find("tbody") or table).find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            code = cells[0].get_text(strip=True)
            did = None
            for a in tr.find_all("a", href=True):
                m = id_pat.search(a["href"])
                if m:
                    did = m.group(1)
                    break
            if code and did and code not in seen:
                seen.add(code)
                pairs.append((code, did))
                rows_this += 1
        if rows_this == 0:
            break
        nxt = next_page(soup, page)
        if not nxt:
            break
        page = nxt
        time.sleep(0.2)
    return pairs


def _scrape_items(session, kind, from_date, db, workers=None):
    if workers is None:
        workers = int(os.getenv("ATMS_ITEM_WORKERS", "8"))   # เบาลงบน Render Starter (512MB)
    cfg = {
        "pr": {"index": f"{BASE}/inv/purchase.request/index",
               "view": f"{BASE}/inv/purchase.request/view/id/",
               "pat": re.compile(r"purchase\.request/view/id/(\d+)"),
               "coll": "purchase_request_items", "key": "pr_code", "order": "pr.code desc"},
        "po": {"index": f"{BASE}/inv/purchase.order/index",
               "view": f"{BASE}/inv/purchase.order/view/id/",
               "pat": re.compile(r"purchase\.order/view/id/(\d+)"),
               "coll": "purchase_order_items", "key": "po_code", "order": "po.code desc"},
    }[kind]
    col = db[cfg["coll"]]
    ensure_index(col, cfg["key"])
    pairs = _collect_codes(session, cfg["index"], cfg["pat"], from_date, cfg["order"])
    now = datetime.utcnow()
    total_items = [0]

    def work(pair):
        code, did = pair
        items = None
        for _ in range(2):  # retry once (transient/empty)
            try:
                r = session.get(cfg["view"] + did, timeout=45)
                items = _parse_items(r.text, code, cfg["key"])
                if items:
                    break
            except Exception:
                items = None
            time.sleep(0.4)
        if items is None:
            return code, None
        for it in items:
            it["detail_id"] = did
            it["scraped_at"] = now
        return code, items

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, p) for p in pairs]
        for fut in as_completed(futs):
            code, items = fut.result()
            if not items:
                continue
            total_items[0] += len(items)
            col.delete_many({cfg["key"]: code})
            col.insert_many(items, ordered=False)
    return {"codes": len(pairs), "items": total_items[0]}


def scrape_items(session, from_date, db):
    pr = _scrape_items(session, "pr", from_date, db)
    po = _scrape_items(session, "po", from_date, db)
    return {"pr": pr, "po": po}


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    if not MONGO_URI:
        log("MONGODB_URI not set"); sys.exit(1)
    started = datetime.utcnow()
    ict = started + timedelta(hours=7)                       # Asia/Bangkok
    # default = ย้อนหลัง 2 เดือน (วันที่ 1 ของเดือน 2 เดือนก่อน) — จับข้อมูลใหม่+อัปเดตย้อนหลัง
    # override ได้ด้วย env ATMS_FROM_DATE (DD/MM/YYYY) เช่นเทสรอบแรกแบบเบา
    y, mth = ict.year, ict.month - 2
    while mth <= 0:
        mth += 12
        y -= 1
    from_date = os.getenv("ATMS_FROM_DATE") or f"01/{mth:02d}/{y}"
    log("window from", from_date, "· item workers", os.getenv("ATMS_ITEM_WORKERS", "8"))

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    db = client["atms"]
    counts, err = {}, None
    try:
        s = get_session()
        counts["purchase_requests"] = scrape_pr(s, from_date, db);   log("PR", counts["purchase_requests"])
        counts["purchase_orders"]   = scrape_po(s, from_date, db);   log("PO", counts["purchase_orders"])
        dep_ids = []
        counts["deposit_header"]    = scrape_deposit(s, from_date, db, dep_ids); log("DD", counts["deposit_header"])
        # รายการสินค้าของใบ DD — เฉพาะใบที่ยังไม่มีใน deposit_items (ดูเหตุผลใน scrape_deposit_items)
        counts["deposit_items"]     = scrape_deposit_items(s, dep_ids, db); log("DD items", counts["deposit_items"])
        counts["items"]             = scrape_items(s, from_date, db); log("items", counts["items"])
    except Exception as e:
        err = str(e)
        log("ERROR:", err)

    finished = datetime.utcnow()
    db["procurement_runs"].insert_one({
        "pipeline": os.getenv("ATMS_RUN_LABEL", "atms_procurement"), "created_at": finished,
        "started_at": started, "finished_at": finished, "from_date": from_date,
        "counts": counts, "ok": err is None, "error": err,
        "duration_sec": round((finished - started).total_seconds(), 1),
    })
    client.close()
    if err:
        sys.exit(1)
    log("done", counts)


if __name__ == "__main__":
    main()
