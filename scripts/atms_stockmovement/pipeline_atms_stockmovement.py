"""
Pipeline: ATMS stock movement → atms.stockmovement_v5
Feeds mena-wms /deadstock + /safety-stock + /vendors, datawarehouse.dw_stockmovement
(KPI-Motors) and mena-intelligence /cost/* + /price-benchmark.

Replaces `1_ext_deposit_data.ipynb`, which ran on the office Mac via launchd and
therefore skipped any day the laptop was asleep or the network gate misfired
(2026-09-03 was skipped outright). Ported from cost_saving_project's
`backfill_new_inventories.py`, which already had auto-login, whole-month bulk
pulls, ATMS-500 fallbacks and a refuse-to-shrink guard — with the target set
flipped: this writes ONLY the legacy four warehouses (the notebook's set) and
never touches the other 27, which the backfill script owns.

Window: trailing SM_MONTHS months (default 5). ATMS accepts back-dated entries
for ~2 months and deletes rows too, so "just the current month" would miss both.

Auth: ATMS_USERNAME / ATMS_PASSWORD (PHPSESSID env for local testing).
Run log: atms.stockmovement_runs (pipeline="atms_stockmovement").

Run standalone (invoked as a subprocess by routes/pipeline/pipeline_routes.py):
  python scripts/atms_stockmovement/pipeline_atms_stockmovement.py

  SM_MONTHS=1 ...              # current month only (the light variant)
  SM_DRY_RUN=1 ...             # fetch + report the exact diff, write nothing
  SM_END_MONTH=2026-08 ...     # end the window somewhere other than this month

── ห้ามลืม (กับดักที่เคยเจ็บมาแล้ว) ──────────────────────────────────────────
* `row_key` ต้องคงสูตรเดิมเป๊ะ: md5(year_month|inventory_id|row_hash|dup_seq)
  โดย row_hash = md5 ของ HASH_COLS 11 คอลัมน์ตามลำดับด้านล่าง เพี้ยนแม้ช่องเดียว
  = ข้อมูลเดิมทั้งชุดถูก "เพิ่มใหม่" แทนที่จะทับ
* ดึงแบบ inventory_id="" (ทั้งหมด) ต้อง map `คลังสินค้า` → id กลับเสมอ ไม่งั้น
  ทุกแถวได้ inventory_id="" แล้ว row_key ใหม่หมด (กับดักเดียวกับข้อบน)
* login ที่ถูกคือ POST /account/user/login + submit:"login" ตัวเล็ก และตรวจผลด้วย
  การหาลิงก์ออกจากระบบ ไม่ใช่ status code — POST ไป "/" ได้ PHPSESSID กลับมา
  เหมือนกันแต่ยังไม่ได้ login จริง แล้วรายงานทุกฉบับกลายเป็น HTML เงียบ ๆ
* ATMS ตอบ 500 ถาวรกับบางเดือน/คลัง (เช่น inv 4 วันที่ 14/12/2024) → ต้องถอยไป
  ดึงรายคลัง แล้วซอยครึ่งวันที่ ไม่ใช่ retry เฉย ๆ
* session อยู่ได้ ~30 นาที — re-login กลางรันเมื่อเจอหน้า login
"""

import hashlib
import io
import os
import sys
import time
import warnings
from datetime import date, datetime, timezone

import certifi
import numpy as np
from bson import json_util
import pandas as pd
import requests
import urllib3
from dateutil.relativedelta import relativedelta
from pymongo import MongoClient, ReplaceOne

from atms_inventories import BY_NAME, LEGACY_4, NAME_OF

warnings.filterwarnings("ignore")
urllib3.disable_warnings()

BASE = "https://www.mena-atms.com"
LOGIN_URL = f"{BASE}/account/user/login"
REPORT_URL = f"{BASE}/report/excel/index.excel/type/stock.movement"
UA = "Mozilla/5.0"

MONGO_URI = os.getenv("MONGODB_URI")
DB_NAME = "atms"
COLL_NAME = "stockmovement_v5"
RUN_COLL = "stockmovement_runs"

RUN_LABEL = os.getenv("SM_RUN_LABEL", "atms_stockmovement")
MONTHS_BACK = int(os.getenv("SM_MONTHS", "5"))
DRY_RUN = os.getenv("SM_DRY_RUN") == "1"
# ถ้าตั้งไว้ ทุกแถวที่กำลังจะถูก "ลบ" หรือ "ทับ" จะถูกสำรองเป็น JSON ก่อนเขียน
# (ใช้ตอน cutover จากเครื่อง Mac — บน Render ไม่ต้องตั้ง ดิสก์เป็น ephemeral อยู่แล้ว)
BACKUP_DIR = os.getenv("SM_BACKUP_DIR")
INVENTORIES = [i.strip() for i in os.getenv("SM_INVENTORIES", ",".join(LEGACY_4)).split(",") if i.strip()]

REQUEST_SLEEP = 3.0          # ATMS degrades under sustained load; be gentle
MAX_ATTEMPTS = 3
BACKOFF = [5, 15, 45]
MIN_KEEP_RATIO = 0.5         # refuse to replace a slice with < 50% of its rows
BATCH = 1000

# The 11 columns behind row_hash — order matters, it is joined with "|".
HASH_COLS = ["inventory_id", "วันที่", "PR", "PO", "DD", "WD", "MR",
             "รหัสสินค้า", "เลขที่เฉพาะ", "รับ", "จ่าย", "ราคาทุน"]
NUMERIC_COLS = ["รับ", "จ่าย", "ราคาทุน", "ยอดเงิน"]
DATE_COLS = ["วันที่"]

# Excluded when deciding "did this row change?".
#   downloaded_at — moves every run by definition.
#   source_row_no — the row's line number in the downloaded file. A bulk pull
#     numbers across every warehouse, so it differs from the notebook's
#     per-warehouse numbering, and one inserted row shifts every later number.
#     Nothing reads it (mena-intelligence /api/cost/actual projects it away), so
#     comparing it would rewrite the whole window for no one's benefit.
IGNORE_ON_COMPARE = {"_id", "downloaded_at", "source_row_no"}


_STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")


def started_stamp():
    return _STAMP


def log(*a):
    print("[atms_stockmovement]", *a, file=sys.stderr, flush=True)


class SessionDead(Exception):
    pass


class ServerError(Exception):
    pass


# ── auth ────────────────────────────────────────────────────────────────────
def get_session():
    s = requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": UA})

    ck = os.getenv("PHPSESSID")
    if ck:
        s.cookies.set("PHPSESSID", ck, domain="www.mena-atms.com")
        log("using PHPSESSID from env")
        return s

    login(s)
    return s


def login(s):
    """POST to /account/user/login with a lowercase submit value.

    Posting to "/" (as the older scripts do) hands back a PHPSESSID but leaves
    you anonymous, and every report then comes back as an HTML login page —
    so success is verified by the logout link, not the status code.
    """
    user, pw = os.getenv("ATMS_USERNAME"), os.getenv("ATMS_PASSWORD")
    if not user or not pw:
        raise SystemExit("ATMS_USERNAME / ATMS_PASSWORD not set")
    s.get(BASE + "/", timeout=30)
    r = s.post(LOGIN_URL,
               data={"username": user, "password": pw, "submit": "login", "next": ""},
               headers={"Referer": BASE + "/"}, timeout=30)
    r.raise_for_status()
    if "ออกจากระบบ" not in r.text and "logout" not in r.text.lower():
        raise SystemExit("login failed — no logout link on the response page")
    log("logged in as", user)


# ── extraction (byte-identical to the notebook's) ───────────────────────────
def normalize_dataframe(df):
    df = df.copy()
    df.columns = df.columns.str.strip()
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in DATE_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce")
    return df


def build_logical_hash_series(df):
    h = df[HASH_COLS].copy()
    for col in h.columns:
        if pd.api.types.is_datetime64_any_dtype(h[col]):
            h[col] = h[col].dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
        else:
            h[col] = h[col].fillna("").astype(str).str.strip()
    return h.agg("|".join, axis=1).apply(
        lambda x: hashlib.md5(x.encode("utf-8")).hexdigest())


def create_keys(df):
    df = df.copy()
    df["row_hash"] = build_logical_hash_series(df)
    df["dup_seq"] = df.groupby("row_hash").cumcount() + 1
    rk = (df["year_month"].astype(str).fillna("") + "|" +
          df["inventory_id"].astype(str).fillna("") + "|" +
          df["row_hash"].astype(str).fillna("") + "|" +
          df["dup_seq"].astype(str).fillna(""))
    df["row_key"] = rk.apply(lambda x: hashlib.md5(x.encode("utf-8")).hexdigest())
    return df


def month_bounds(ym):
    y, m = (int(x) for x in ym.split("-"))
    first = date(y, m, 1)
    last = first + relativedelta(months=1) - relativedelta(days=1)
    return first.strftime("%d/%m/%Y"), last.strftime("%d/%m/%Y")


def window_months():
    end = os.getenv("SM_END_MONTH") or date.today().strftime("%Y-%m")
    y, m = (int(x) for x in end.split("-"))
    last = date(y, m, 1)
    return [(last - relativedelta(months=n)).strftime("%Y-%m")
            for n in range(MONTHS_BACK - 1, -1, -1)]


# ── ATMS ────────────────────────────────────────────────────────────────────
def fetch_range(s, from_date, to_date, inv=""):
    """One download for a date range; inv="" means ทั้งหมด (every warehouse).

    Two different ways ATMS says no:
      200 + HTML → the login page; the session died, log in again.
      500 + HTML → ATMS could not build the report. Retrying is pointless,
                   the range has to be made smaller.
    """
    r = s.post(REPORT_URL, data={
        "from_date": from_date, "to_date": to_date,
        "code": "", "sku": "", "inventory_id": inv,
        "doc_type": "", "submit": "พิมพ์",
        "report_type": "stock.movement",
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=300)

    if "text/html" in r.headers.get("Content-Type", ""):
        if r.status_code >= 500:
            raise ServerError(f"ATMS 500 for {from_date}–{to_date} inv={inv or 'all'}")
        raise SessionDead(f"login page for {from_date}–{to_date}")
    r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content), skiprows=1)
    return df if not df.empty else pd.DataFrame()


def _with_retries(s, from_date, to_date, inv):
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fetch_range(s, from_date, to_date, inv)
        except ServerError:
            raise                       # a smaller range is the only cure
        except SessionDead as e:
            last_err = e
            log("   session expired — logging in again")
            login(s)
            time.sleep(2)
        except Exception as e:
            last_err = e
            wait = BACKOFF[min(attempt - 1, len(BACKOFF) - 1)]
            log(f"   attempt {attempt}/{MAX_ATTEMPTS} failed ({type(e).__name__}: {e})"
                f" — retry in {wait}s")
            if attempt < MAX_ATTEMPTS:
                time.sleep(wait)
    raise RuntimeError(f"{from_date}–{to_date} inv={inv or 'all'}: {last_err}")


def _split_range(s, from_date, to_date, inv, notes):
    """Halve a date range until ATMS can render each piece.

    Splitting on DATE keeps row_key stable: วันที่ is one of the hashed columns,
    so every member of a row_hash group lands in the same slice and dup_seq
    comes out as it would from a whole-month pull.
    """
    try:
        return _with_retries(s, from_date, to_date, inv)
    except ServerError as e:
        a = datetime.strptime(from_date, "%d/%m/%Y").date()
        b = datetime.strptime(to_date, "%d/%m/%Y").date()
        if a >= b:
            msg = f"inv {inv or 'all'} {from_date}: unreadable, skipped ({e})"
            log("   ⚠️", msg)
            notes.append(msg)
            return pd.DataFrame()
        mid = a + (b - a) // 2
        left = _split_range(s, from_date, mid.strftime("%d/%m/%Y"), inv, notes)
        time.sleep(REQUEST_SLEEP)
        right = _split_range(s, (mid + relativedelta(days=1)).strftime("%d/%m/%Y"),
                             to_date, inv, notes)
        return pd.concat([left, right], ignore_index=True)


def fetch_month(s, ym, notes):
    """Every warehouse for one month, in as few requests as ATMS allows.

    Normally ONE bulk (ทั้งหมด) request — proven by probe_all_inventories.py to
    reproduce the per-warehouse row_keys byte for byte. Only when ATMS 500s on
    the whole month does it fall back to one request per target warehouse, and
    only then to halving dates.
    """
    from_date, to_date = month_bounds(ym)
    try:
        return _with_retries(s, from_date, to_date, "")
    except ServerError as e:
        msg = f"{ym} unreadable as a whole ({e}) — pulling {len(INVENTORIES)} warehouses one by one"
        log("  ", msg)
        notes.append(msg)

    frames = []
    for inv in INVENTORIES:
        df = _split_range(s, from_date, to_date, inv, notes)
        if not df.empty:
            # A per-warehouse pull has no คลังสินค้า to map back from on some
            # report variants — stamp it from the id we asked for.
            df = df.copy()
            if "คลังสินค้า" not in df.columns:
                # a per-warehouse pull has nothing to map back from — stamp the
                # name we asked for so the caller's BY_NAME lookup still works
                df["คลังสินค้า"] = NAME_OF[inv]
            frames.append(df)
        time.sleep(REQUEST_SLEEP)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── write ───────────────────────────────────────────────────────────────────
def to_native(records):
    """numpy scalar → Python scalar. pandas ≥2 already does this in
    to_dict("records"); on an older pandas pymongo would choke on numpy.int64,
    and Render's pinned version is not ours to assume."""
    for r in records:
        for k, v in r.items():
            if isinstance(v, np.generic):
                r[k] = v.item()
    return records


def comparable(doc):
    return {k: v for k, v in doc.items() if k not in IGNORE_ON_COMPARE}


def write_slice(coll, ym, inv, part, result):
    """Prune-and-replace one (year_month, inventory_id) slice, minimally.

    Rows are matched on row_key, so an untouched row is left exactly as it is —
    no rewrite, no empty window a concurrent reader could cache. Rows ATMS has
    since deleted are removed, which upsert-only never did (that drift is why
    recent months read slightly high everywhere downstream).
    """
    slice_q = {"year_month": ym, "inventory_id": inv}
    existing = {d["row_key"]: d for d in coll.find(slice_q)}
    fresh = {r["row_key"]: r for r in part}

    # Refuse to shrink: a truncated download must never wipe a slice.
    if existing and len(fresh) < len(existing) * MIN_KEEP_RATIO:
        msg = (f"{ym}/inv{inv}: fresh {len(fresh)} < 50% of existing "
               f"{len(existing)} — slice kept as-is")
        log("   ⚠️", msg)
        result["skipped"].append(msg)
        return

    added = [k for k in fresh if k not in existing]
    removed = [k for k in existing if k not in fresh]
    changed = [k for k in fresh
               if k in existing and comparable(fresh[k]) != comparable(existing[k])]

    result["slices"].append({
        "year_month": ym, "inventory_id": inv,
        "before": len(existing), "fetched": len(fresh),
        "added": len(added), "removed": len(removed), "changed": len(changed),
    })
    result["added"] += len(added)
    result["removed"] += len(removed)
    result["changed"] += len(changed)

    if DRY_RUN or not (added or removed or changed):
        return

    if BACKUP_DIR and (removed or changed):
        # เอกสารฉบับก่อนแก้ — กู้คืนได้ด้วย mongoimport / bson.json_util.loads
        os.makedirs(BACKUP_DIR, exist_ok=True)
        path = os.path.join(BACKUP_DIR, f"{RUN_LABEL}-{started_stamp()}-{ym}-inv{inv}.json")
        with open(path, "w") as fh:
            fh.write(json_util.dumps([existing[k] for k in removed + changed], indent=1))
        log(f"   สำรอง {len(removed) + len(changed)} แถวไว้ที่ {path}")

    ops = [ReplaceOne({"row_key": k}, fresh[k], upsert=True) for k in added + changed]
    for i in range(0, len(ops), BATCH):
        coll.bulk_write(ops[i:i + BATCH], ordered=False)
    for i in range(0, len(removed), BATCH):
        coll.delete_many({"row_key": {"$in": removed[i:i + BATCH]}})


def main():
    if not MONGO_URI:
        raise SystemExit("MONGODB_URI not set")

    started = datetime.now(timezone.utc)
    months = window_months()
    log(f"window {months[0]} → {months[-1]} ({len(months)} months) · "
        f"warehouses {','.join(INVENTORIES)} · dry_run={DRY_RUN} · label={RUN_LABEL}")

    # certifi ติดมากับ requests อยู่แล้ว — ระบุ CA ตรง ๆ ให้รันได้ทั้งบน Render และ macOS
    client = MongoClient(MONGO_URI, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=20000)
    coll = client[DB_NAME][COLL_NAME]

    # row_key must stay unique or a retried batch could duplicate a row.
    if not any(ix.get("unique") and ix["key"] == [("row_key", 1)]
               for ix in coll.index_information().values()):
        raise SystemExit("stockmovement_v5 has no unique index on row_key — aborting")

    # Everything outside the target warehouses is owned by other jobs and must
    # come out of this run untouched. Asserted again at the end.
    others_q = {"inventory_id": {"$nin": INVENTORIES}}
    others_before = coll.count_documents(others_q)

    result = {"added": 0, "removed": 0, "changed": 0,
              "slices": [], "skipped": [], "notes": [], "errors": []}

    s = get_session()

    for i, ym in enumerate(months, 1):
        try:
            raw = fetch_month(s, ym, result["notes"])
        except Exception as e:
            msg = f"{ym}: fetch failed — {type(e).__name__}: {e}"
            log("  ❌", msg)
            result["errors"].append(msg)
            continue

        if raw.empty:
            log(f"[{i}/{len(months)}] {ym} — empty month, nothing written")
            time.sleep(REQUEST_SLEEP)
            continue

        df = normalize_dataframe(raw)
        if "คลังสินค้า" not in df.columns:
            msg = f"{ym}: report has no คลังสินค้า column — cannot map inventory_id, skipped"
            log("  ❌", msg)
            result["errors"].append(msg)
            continue

        names = df["คลังสินค้า"].astype(str).str.strip()
        df = df[names.isin(BY_NAME)].copy()
        names = names[names.isin(BY_NAME)]
        df["inventory_id"] = names.map(BY_NAME)
        df["year_month"] = ym
        df["downloaded_at"] = datetime.utcnow()
        df["source_row_no"] = np.arange(2, len(df) + 2)
        df = create_keys(df)

        mine = df[df["inventory_id"].isin(INVENTORIES)]
        for inv in INVENTORIES:
            part = mine[mine["inventory_id"] == inv]
            if part.empty:
                continue
            records = to_native(part.replace({np.nan: None}).to_dict("records"))
            write_slice(coll, ym, inv, records, result)

        got = {inv: int((mine["inventory_id"] == inv).sum()) for inv in INVENTORIES}
        log(f"[{i}/{len(months)}] {ym} — {len(df):,} rows in file · "
            + " ".join(f"inv{k}={v}" for k, v in got.items()))
        del raw, df, mine
        time.sleep(REQUEST_SLEEP)

    others_after = coll.count_documents(others_q)
    if others_after != others_before:
        msg = (f"other warehouses changed {others_before:,} → {others_after:,} "
               f"— this pipeline must never touch them")
        log("🔥", msg)
        result["errors"].append(msg)

    finished = datetime.now(timezone.utc)
    doc = {
        "pipeline": RUN_LABEL,
        "created_at": finished,
        "started_at": started,
        "seconds": round((finished - started).total_seconds(), 1),
        "window": {"from": months[0], "to": months[-1], "months": len(months)},
        "inventories": INVENTORIES,
        "dry_run": DRY_RUN,
        "others_before": others_before,
        "others_after": others_after,
        **{k: result[k] for k in ("added", "removed", "changed", "slices",
                                  "skipped", "notes", "errors")},
    }
    if not DRY_RUN:
        client[DB_NAME][RUN_COLL].insert_one(dict(doc))

    verb = "would add" if DRY_RUN else "added"
    guard = ("other warehouses untouched" if others_after == others_before
             else f"🔥 OTHER WAREHOUSES MOVED {others_before:,} → {others_after:,}")
    log(f"done in {doc['seconds']}s — {verb} {result['added']:,} · "
        f"changed {result['changed']:,} · removed {result['removed']:,} · "
        f"{others_before:,} rows in {guard}")
    if result["skipped"]:
        log(f"⚠️ {len(result['skipped'])} slice(s) kept as-is")
    if result["errors"]:
        log(f"❌ {len(result['errors'])} error(s):", *result["errors"])

    client.close()
    if result["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
