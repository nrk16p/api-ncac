"""Shared helpers for engineon pipelines — ATMS login, job logging, target periods.

Job logs go to analytics.etl_jobs in BOTH shapes:
  - job_type/start_time/... → fuel-control-center UI (unchanged contract)
  - pipeline/created_at     → api-ncac GET /pipeline/status/{type}
"""
import logging
import os
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_URL = "https://www.mena-atms.com"
MONGODB_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
UA = "Mozilla/5.0"

warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("engineon")


def atms_session() -> requests.Session:
    """Logged-in ATMS session (PHPSESSID env = local-test shortcut)."""
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
    login_url = f"{BASE_URL}/account/user/login"
    s.get(login_url, timeout=30)  # seed PHPSESSID
    s.post(login_url, data={"username": user, "password": pw, "submit": "login",
                            "next": "", "forgotPasswd": "ลืมรหัสผ่าน"},
           timeout=30, allow_redirects=True)
    chk = s.get(f"{BASE_URL}/cms/file/index", timeout=30)
    if "account/user/login" in chk.url:
        raise RuntimeError("ATMS login failed — check ATMS_USERNAME / ATMS_PASSWORD")
    log.info("ATMS login OK (%s)", user)
    return s


def now_bkk() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=7)


def yesterday_bkk() -> datetime:
    return now_bkk() - timedelta(days=1)


def target_year_month() -> tuple[int, int]:
    """YEAR/MONTH env override (from /pipeline/run body), else month of yesterday (BKK).
    "Month of yesterday" means the daily run on the 1st still finalizes last month."""
    y = yesterday_bkk()
    return int(os.getenv("YEAR", y.year)), int(os.getenv("MONTH", y.month))


class JobLog:
    def __init__(self, job_type: str, pipeline: str, meta: dict | None = None):
        ts = datetime.now(timezone.utc)
        self.col = MongoClient(MONGODB_URI)["analytics"]["etl_jobs"]
        self.job_id = f"{job_type}_{ts.strftime('%Y-%m-%d_%H%M%S')}"
        self.start = ts
        self.col.insert_one({
            "_id": self.job_id,
            "job_type": job_type,
            "pipeline": pipeline,
            "status": "running",
            "start_time": ts.isoformat(),
            "end_time": None,
            "duration_sec": None,
            "error": None,
            "created_at": ts,
            "source": "api-ncac",
            **(meta or {}),
        })

    def finish(self, status: str = "success", **extra):
        end = datetime.now(timezone.utc)
        self.col.update_one({"_id": self.job_id}, {"$set": {
            "status": status,
            "end_time": end.isoformat(),
            "duration_sec": int((end - self.start).total_seconds()),
            **extra,
        }})
