"""CPAC fleetlink report fetch + compensation push (spec §10.2), ported from rmc_daily.py."""
import io
import logging
import time
from datetime import date
from urllib.parse import urlsplit

import pandas as pd
import requests

log = logging.getLogger("rmc")
RETRY_WAITS = (5, 15)          # 3 attempts, as rmc_daily
COMPANY_ID = 1231


def _where(url: str) -> str:
    """URL without its query — the fleetlink file link may be signed, and run errors are shown in FCC."""
    return urlsplit(url)._replace(query="", fragment="").geturl()


def _reason(e: requests.RequestException) -> str:
    status = getattr(e.response, "status_code", None) if e.response is not None else None
    return f"HTTP {status}" if status else type(e).__name__


def request_with_retry(session, method: str, url: str, sleep=time.sleep, **kwargs):
    reason = ""
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            resp = session.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            reason = _reason(e)
            log.warning("%s %s attempt %d failed: %s", method, _where(url), attempt + 1, reason)
            if attempt < len(RETRY_WAITS):
                sleep(RETRY_WAITS[attempt])
    raise RuntimeError(f"{method} {_where(url)} failed after {len(RETRY_WAITS) + 1} attempts: {reason}")


def fetch_report(day: date, post_url: str, vehicle_list: list[int], session=None, sleep=time.sleep) -> pd.DataFrame:
    """POST the report request, download the Excel it points to, read it like rmc_daily (skiprows=3)."""
    if not post_url:
        raise RuntimeError("POST_URL is not set")
    session = session or requests.Session()
    body = {
        "date_start": f"{day.isoformat()} 00:00:00", "date_end": f"{day.isoformat()} 23:59:59",
        "type": "vehicle", "vehicle_list": vehicle_list, "plants_list": ["all"], "company_id": COMPANY_ID,
        "vehicle_visibility": ",".join(map(str, vehicle_list)), "site_id": "", "type_file": "excel",
    }
    resp = request_with_retry(session, "POST", post_url, sleep=sleep, json=body, timeout=180)
    file_url = (resp.json() or {}).get("result")
    if not file_url:
        raise RuntimeError("fleetlink returned no result file URL")
    excel = request_with_retry(session, "GET", file_url, sleep=sleep, timeout=180)
    return pd.read_excel(io.BytesIO(excel.content), skiprows=3)


def push_records(records: list[dict], api_push: str, session=None, sleep=time.sleep):
    """POST the rows to API_PUSH (upsert); returns its JSON ({created, updated, total}) or text."""
    if not api_push:
        raise RuntimeError("API_PUSH is not set")
    session = session or requests.Session()
    resp = request_with_retry(session, "POST", api_push, sleep=sleep, json=records, timeout=120)
    try:
        return resp.json()
    except ValueError:
        return resp.text[:500]
