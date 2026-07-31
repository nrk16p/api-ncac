"""Drivercost-ticket pipeline — downloads ATMS monthly-driver-cost batch reports and
rebuilds atms.driver_cost_ticket for one month (delete + insert, keyed by mmyy).

NOT the same as the existing driver_cost pipeline (mena-bi.driverCost, payroll):
this one feeds the engineon trip summary with trips per (truck, driver, day).

Default month: month of yesterday (Bangkok). Override via env YEAR / MONTH —
passed through POST /pipeline/run/drivercost_ticket body.

Ported from api-engineon app/etl_drivercost.py, with ATMS auto-login.
"""
import io
import os
import re
import urllib.parse
import warnings
from typing import Dict, List

import pandas as pd
from bs4 import BeautifulSoup
from pymongo import MongoClient

from common import BASE_URL, MONGODB_URI, JobLog, atms_session, log, target_year_month

warnings.filterwarnings("ignore")

INDEX_URL = f"{BASE_URL}/cms/file/index"

TARGET_SERVICES = [
    'Mixer UMO (CPAC) - MIXB', 'Mixer นครหลวง โม่เล็ก - MIXS',
    'Mixer นครหลวง โม่ใหญ่ - MIXB', 'Mixer ORC - MIXB',
    'Mixer CPAC โม่เล็ก - MIXS', 'Mixer CPAC (คิว) - MIXB',
    'Mixer ACON อยุธยาคอนกรีต - MIXB', 'Mixer KPAC - MIXB',
    'Mixer KPAC โม่เล็ก - MIXS', 'Mixer ฟาสท์ คอนกรีต - MIXB',
    'Mixer เอเชีย - MIXB', 'Mixer ที.เอ็น.ซีเมนต์บล็อค - MIXB',
    'Mixer มหาทรัพย์ซีเมนต์ - MIXB'
]


def _parse_download_links(html: str) -> List[Dict[str, str]]:
    DL_RE = re.compile(r"/cms/file/download/id/(\d+)")
    soup = BeautifulSoup(html, "html.parser")

    items = []
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        a = tds[0].find("a", href=True)
        if not a:
            continue
        m = DL_RE.search(a["href"])
        if not m:
            continue
        items.append({
            "download_id": m.group(1),
            "download_url": urllib.parse.urljoin(BASE_URL, a["href"]),
            "title": a.get_text(strip=True),
            "year": tds[1].get_text(strip=True) if len(tds) > 1 else "",
            "month": tds[2].get_text(strip=True) if len(tds) > 2 else "",
        })

    seen, uniq = set(), []
    for it in items:
        if it["download_id"] not in seen:
            uniq.append(it)
            seen.add(it["download_id"])
    return uniq


def run(year: int, month: int) -> int:
    s = atms_session()

    params = {
        "type": "monthly-driver-cost",
        "use_as": "batch-report",
        "year": str(year),
        "month": f"{month:02d}",
        "ref_id": "1",
        "search_fields": "year,month",
        "submit": "ค้นหา",
    }
    r = s.get(INDEX_URL, params=params, timeout=60)
    r.raise_for_status()

    links = _parse_download_links(r.text)
    if not links:
        raise RuntimeError("No monthly-driver-cost reports found — check ATMS login or period")
    log.info("found downloads: %s", [i["download_id"] for i in links])

    dfs = []
    for it in links:
        rr = s.get(it["download_url"], timeout=60)
        rr.raise_for_status()
        with io.BytesIO(rr.content) as f:
            df = pd.read_excel(f, sheet_name=0, dtype=str, skiprows=1)
        df["download_id"] = it["download_id"]
        df["report_title"] = it["title"]
        df["year"] = it["year"]
        df["month"] = it["month"]
        dfs.append(df)

    combined = pd.concat(dfs, ignore_index=True)
    filtered = combined[combined["บริการ"].isin(TARGET_SERVICES)]

    result = (
        filtered.groupby(['ออก LDT', 'เลขรถ', 'หัว', 'พจส1'], as_index=False)
        .agg(LDT_unique_count=('LDT', 'nunique'))
    )

    result["หัว"] = result["หัว"].str.replace("สบ.", "", regex=False).str.strip()
    result['ออก LDT'] = pd.to_datetime(result['ออก LDT'], format='%d/%m/%Y', errors='coerce')
    result['ออก LDT_fmt'] = result['ออก LDT'].dt.strftime('%d/%m/%Y')
    result['mmyy'] = result['ออก LDT'].dt.strftime('%m/%Y')

    col = MongoClient(MONGODB_URI)["atms"]["driver_cost_ticket"]
    records = result.to_dict(orient="records")
    if not records:
        log.warning("no records to insert")
        return 0

    deleted = col.delete_many({"mmyy": f"{month:02d}/{year}"}).deleted_count
    col.insert_many(records)
    log.info("replaced %d old → inserted %d rows (atms.driver_cost_ticket %02d/%d)",
             deleted, len(records), month, year)
    return len(records)


def main():
    year, month = target_year_month()
    job = JobLog("drivercost", "drivercost_ticket", {"year": year, "month": month})
    try:
        n = run(year, month)
        job.finish("success", rows=n)
    except Exception as e:
        job.finish("failed", error=str(e))
        raise


if __name__ == "__main__":
    main()
