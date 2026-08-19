"""ATMS maintenance-request (MR) sync → atms.maint_header/tasks/parts/timing + repair-analysis.

Ported from ~/Documents/project/atms-extractor (cli.py + scrape_timing.py +
build_repair_analysis.py) with two changes: auto-login via engineon common
(no manual PHPSESSID), and one fetch per id feeds both the detail parse and
the timing parse.

Daily incremental (02:00 BKK):
  1. crawl new ids — Mongo max request_id+1 .. current ATMS max (index page,
     order_by mr.id desc), capped at MAX_NEW per run
  2. re-scrape open (not-closed) MRs in a trailing id window — step/timing
     fields fill in over the MR lifetime
  3. re-flatten repair-analysis for every touched id (same อู่/แหล่งอะไหล่
     derivation rules, delete+insert per exact id set)

Env overrides (POST /pipeline/run/maintenance body → UPPERCASE env vars):
  START_ID / END_ID  explicit crawl range — skips discovery (backfill/test)
  MAX_NEW            cap on new ids per run           (default 3000)
  OPEN_SPAN          id window scanned for open MRs   (default 8000 ≈ 5 months)
  OPEN_LIMIT         max open MRs re-scraped per run  (default 2500)
  WORKERS            concurrent fetchers              (default 6)
  DELAY              sleep per request in seconds     (default 0.15)
  SKIP_OPEN=1        skip step 2 (test/backfill runs)
"""
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup
from pymongo import MongoClient, ReplaceOne

sys.path.insert(0, str(Path(__file__).parent.parent / "engineon"))
from common import BASE_URL, MONGODB_URI, JobLog, atms_session  # noqa: E402

import logging  # noqa: E402
log = logging.getLogger("maintenance")

MR_VIEW = BASE_URL + "/veh/maintenance.request/view/id/{id}"
MR_INDEX = BASE_URL + "/veh/maintenance.request/index/"

MAX_NEW = int(os.getenv("MAX_NEW", 3000))
OPEN_SPAN = int(os.getenv("OPEN_SPAN", 8000))
OPEN_LIMIT = int(os.getenv("OPEN_LIMIT", 2500))
WORKERS = int(os.getenv("WORKERS", 6))
DELAY = float(os.getenv("DELAY", 0.15))
RETRY_SLEEPS = (2, 5, 10)
BATCH_SIZE = 200

HEADER_LABELS = {
    "ขั้นตอน": "step",
    "เลขที่แจ้งซ่อม": "request_code",
    "แจ้งซ่อม": "reported_at",
    "สาขา": "branch",
    "ประเภทรถร่วม": "owner_type",
    "mechanic": "mechanic",
    "พจส.": "driver",
    "เลขรถ": "vehicle_no",
    "ยานพาหนะ": "plate_no",
    "รถวิ่งไม่ได้": "vehicle_grounded",
    "เลขไมล์วันแจ้ง": "mileage_at_report",
    "ผู้ใช้งาน": "created_by",
    "บันทึกเมื่อ": "saved_at",
    "อุบัติเหตุ": "accident_ref",
}

# timing labels → maint_timing fields; value = the text line right after the
# label, "" when the next line is itself another label (empty value)
TIMING_FIELDS = [
    ("เวลาที่ใช้ (ชม.)", "estimated_hours"),
    ("ประมาณวันที่เสร็จ", "estimate_finish_at"),
    ("เข้าซ่อมเมื่อ", "garage_entry_at"),
    ("เลขไมล์วันนำรถเข้าซ่อม", "mileage_at_entry"),
    ("ซ่อมเสร็จเมื่อ", "garage_finish_at"),
    ("ปิดเมื่อ", "closed_at"),
    ("ปิดโดย", "closed_by"),
    ("ขั้นตอน", "step"),
    ("เลขที่แจ้งซ่อม", "request_code"),
]
LABEL_RE = re.compile(r".+\s*:\s*$")

FLAT_HEADER_FIELDS = ["step", "request_code", "reported_at", "branch", "owner_type",
                      "mechanic", "plate_no", "mileage_at_report", "created_by"]


class SessionExpired(RuntimeError):
    pass


# ---------------------------------------------------------------- parse

def parse_detail(html: str, rid: int):
    """One MR view page → (header_doc, task_docs, part_docs, timing_doc)."""
    soup = BeautifulSoup(html, "html.parser")

    header = {"request_id": rid}
    fieldset = soup.find("fieldset", id="fieldset-maintenance_request_information")
    if fieldset is not None:
        dl = fieldset.find("dl", class_="form-nested-lv-0")
        if dl is not None:
            for dt, dd in zip(dl.find_all("dt", recursive=False), dl.find_all("dd", recursive=False)):
                label = dt.get_text(strip=True).rstrip(":").strip()
                header[HEADER_LABELS.get(label, label)] = dd.get_text(" ", strip=True)

    tables = soup.find_all("table", class_="defaultTable")
    tasks, parts = [], []
    if len(tables) >= 1:
        for tr in tables[0].find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not cells or cells[0].lower() == "grand total":
                continue
            cells += [""] * (6 - len(cells))
            tasks.append({"request_id": rid, "task_id": cells[0], "repair_type": cells[1],
                          "description": cells[2], "reference": cells[3],
                          "estimated_cost": cells[4], "other_cost": cells[5]})
    if len(tables) >= 2:
        for tr in tables[1].find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not cells or cells[0].lower() == "grand total":
                continue
            cells += [""] * (9 - len(cells))
            parts.append({"request_id": rid, "task_id": cells[0], "requisition_no": cells[1],
                          "parts_group": cells[2], "part": cells[3], "serial_no": cells[4],
                          "qty": cells[5], "unit_price": cells[6], "total": cells[7],
                          "remark": cells[8]})

    timing = {"request_id": rid}
    lines = [l for l in soup.get_text("\n", strip=True).split("\n") if l]
    for i, line in enumerate(lines):
        for label, field in TIMING_FIELDS:
            if line.startswith(label) and line.rstrip().endswith(":"):
                nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
                timing[field] = "" if LABEL_RE.match(nxt) else nxt
                break

    return header, tasks, parts, timing


# ---------------------------------------------------------------- fetch

def fetch_detail(session, rid: int) -> str:
    last_err = None
    for attempt, backoff in enumerate((0,) + RETRY_SLEEPS):
        if backoff:
            time.sleep(backoff)
        try:
            resp = session.get(MR_VIEW.format(id=rid), timeout=30)
            if "account/user/login" in resp.url or "<title>เข้าสู่ระบบ" in resp.text[:2000]:
                raise SessionExpired(f"login page for id {rid}")
            if resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}"
                continue
            resp.raise_for_status()
            return resp.text
        except SessionExpired:
            raise
        except Exception as e:  # noqa: BLE001 — retry with backoff
            last_err = str(e)
    raise RuntimeError(f"id {rid}: {last_err}")


def discover_max_id(session) -> int:
    resp = session.get(MR_INDEX, params={"order_by": "mr.id desc"}, timeout=30)
    resp.raise_for_status()
    ids = [int(m) for m in re.findall(r"/maintenance\.request/view/id/(\d+)", resp.text)]
    if not ids:
        raise RuntimeError("no MR ids on index page — login failed or layout changed")
    return max(ids)


def crawl(session_holder: dict, ids: list[int], db, counters: dict):
    """Fetch+parse+upsert ids with a worker pool. One re-login retry on
    session expiry; per-id errors are collected, not fatal."""
    remaining = list(ids)
    for round_no in (1, 2):
        expired, batch = [], []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            s = session_holder["s"]

            def work(rid):
                time.sleep(DELAY)
                return parse_detail(fetch_detail(s, rid), rid)

            futures = {pool.submit(work, rid): rid for rid in remaining}
            for fut in as_completed(futures):
                rid = futures[fut]
                try:
                    batch.append(fut.result())
                except SessionExpired:
                    expired.append(rid)
                except Exception as e:  # noqa: BLE001
                    counters["errors"].append({"request_id": rid, "error": str(e)})
                if len(batch) >= BATCH_SIZE:
                    flush(db, batch, counters)
                    batch = []
        flush(db, batch, counters)
        if not expired:
            return
        if round_no == 1:
            log.info("session expired on %d ids — re-login and retry", len(expired))
            session_holder["s"] = atms_session()
            remaining = expired
        else:
            counters["errors"] += [{"request_id": r, "error": "SESSION_EXPIRED"} for r in expired]


def flush(db, results: list, counters: dict):
    if not results:
        return
    header_ops, timing_ops, tasks, parts, ids = [], [], [], [], []
    for header, t, p, timing in results:
        ids.append(header["request_id"])
        header_ops.append(ReplaceOne({"request_id": header["request_id"]}, header, upsert=True))
        timing_ops.append(ReplaceOne({"request_id": timing["request_id"]}, timing, upsert=True))
        tasks += t
        parts += p
    id_filter = {"request_id": {"$in": ids}}
    db.maint_header.bulk_write(header_ops, ordered=False)
    db.maint_timing.bulk_write(timing_ops, ordered=False)
    db.maint_tasks.delete_many(id_filter)
    if tasks:
        db.maint_tasks.insert_many(tasks, ordered=False)
    db.maint_parts.delete_many(id_filter)
    if parts:
        db.maint_parts.insert_many(parts, ordered=False)
    counters["saved"] += ids
    log.info("flushed %d ids (%d total)", len(ids), len(counters["saved"]))


# ---------------------------------------------------------------- flatten

def to_num(v):
    try:
        f = float(str(v).replace(",", ""))
        return int(f) if f == int(f) else f
    except (ValueError, TypeError):
        return 0


def flatten(db, touched: list[int]) -> tuple[int, int]:
    """Rebuild repair-analysis rows for the touched ids (delete+insert)."""
    if not touched:
        return 0, 0
    id_filter = {"request_id": {"$in": touched}}
    headers = {h["request_id"]: h for h in db.maint_header.find(id_filter)}
    parts = list(db.maint_parts.find(id_filter, {"_id": 0}))

    outside = {p["request_id"] for p in parts if "ค่าแรง" in (p.get("parts_group") or "")}
    docs = []
    for p in parts:
        h = headers.get(p["request_id"])
        if h is None:
            continue
        is_labor = "ค่าแรง" in (p.get("parts_group") or "")
        is_out = p["request_id"] in outside
        if is_labor:
            src = "ค่าแรง"
        elif (p.get("parts_group") or "") == "ยาง":
            src = "อะไหล่คลัง"
        else:
            src = "อะไหล่ศูนย์/อู่นอก" if is_out else "อะไหล่คลัง"
        doc = {
            "request_id": p["request_id"],
            "task_id": to_num(p.get("task_id")),
            "requisition_no": p.get("requisition_no") or "",
            "parts_group": p.get("parts_group") or "",
            "part": p.get("part") or "",
            "serial_no": p.get("serial_no") or None,
            "qty": to_num(p.get("qty")),
            "unit_price": to_num(p.get("unit_price")),
            "total": to_num(p.get("total")),
            "remark": p.get("remark") or "",
            "อู่": "อู่นอก" if is_out else "อู่ใน",
            "แหล่งอะไหล่": src,
        }
        for f in FLAT_HEADER_FIELDS:
            doc[f] = h.get(f) or ""
        docs.append(doc)

    col = db["repair-analysis"]
    deleted = col.delete_many(id_filter).deleted_count
    if docs:
        col.insert_many(docs, ordered=False)
    return deleted, len(docs)


# ---------------------------------------------------------------- main

def main():
    db = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10_000)["atms"]
    db.maint_header.create_index("request_id", unique=True, background=True)
    db.maint_timing.create_index("request_id", unique=True, background=True)
    db.maint_tasks.create_index([("request_id", 1), ("task_id", 1)], background=True)
    db.maint_parts.create_index([("request_id", 1), ("task_id", 1)], background=True)
    db["repair-analysis"].create_index("request_id", background=True)

    job = JobLog("maintenance", "maintenance")
    counters = {"saved": [], "errors": []}
    try:
        holder = {"s": atms_session()}

        # -- 1. new ids
        if os.getenv("START_ID") and os.getenv("END_ID"):
            start_id, end_id = int(os.environ["START_ID"]), int(os.environ["END_ID"])
        else:
            last = db.maint_header.find_one(sort=[("request_id", -1)], projection={"request_id": 1})
            start_id = (last["request_id"] if last else 0) + 1
            end_id = discover_max_id(holder["s"])
        new_ids = list(range(start_id, min(end_id, start_id + MAX_NEW - 1) + 1))
        if end_id - start_id + 1 > len(new_ids):
            log.info("gap %d ids > MAX_NEW=%d — crawling first %d, rest next run",
                     end_id - start_id + 1, MAX_NEW, len(new_ids))
        log.info("new ids: %d (%s..%s)", len(new_ids),
                 new_ids[0] if new_ids else "-", new_ids[-1] if new_ids else "-")
        crawl(holder, new_ids, db, counters)
        new_saved = len(counters["saved"])

        # -- 2. refresh open MRs
        refreshed = 0
        if os.getenv("SKIP_OPEN") != "1":
            floor = max((end_id if new_ids else start_id) - OPEN_SPAN, 0)
            open_ids = [d["request_id"] for d in db.maint_timing.find(
                {"request_id": {"$gte": floor, "$lt": start_id},
                 "$or": [{"closed_at": ""}, {"closed_at": None},
                         {"closed_at": {"$exists": False}}]},
                {"request_id": 1}).sort("request_id", -1).limit(OPEN_LIMIT)]
            log.info("open MRs to refresh: %d (floor id %d)", len(open_ids), floor)
            crawl(holder, open_ids, db, counters)
            refreshed = len(counters["saved"]) - new_saved

        # -- 3. flatten
        deleted, inserted = flatten(db, counters["saved"])
        log.info("repair-analysis: deleted %d, inserted %d", deleted, inserted)

        n_err = len(counters["errors"])
        if n_err:
            log.warning("%d id errors: %s", n_err, counters["errors"][:10])
        job.finish("success" if n_err == 0 else "partial",
                   new_ids_crawled=new_saved, open_refreshed=refreshed,
                   flat_deleted=deleted, flat_inserted=inserted,
                   id_errors=n_err, range_start=start_id, range_end=end_id)
        log.info("done — new %d, refreshed %d, errors %d", new_saved, refreshed, n_err)
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
