# ======================================================
# 📄 ATMS — ใบเบิก (WD) / ใบรับคืน  เรียกจาก WMS แบบ server-to-server
#    GET  /atms/withdraw/options | whoami | list | lookup/{kind} | {gm_id}
#    POST /atms/withdraw/issue | return   (dry_run: true = ไม่ยิง ATMS)
#    ทุกเส้นต้องมี x-api-key และบัญชี ATMS ของผู้ใช้ (X-ATMS-Username/Password หรือ X-PHPSESSID)
#    ห้ามใช้ ATMS_* จาก env
# ======================================================
import hashlib
import hmac
import os
import re
import threading
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.atms_openjob_service import AtmsAuthError, AtmsNotFoundError, AtmsOpenJobError
from services.atms_withdraw_service import (
    AtmsWithdrawClient,
    IssueLine,
    IssueProgress,
    ReturnLine,
    WithdrawPartialError,
)

router = APIRouter(prefix="/atms/withdraw", tags=["ATMS Withdraw"])

CLIENT_TTL = 20 * 60  # วินาที


# ── auth ────────────────────────────────────────────────────────────────────
def require_api_key(x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    key = os.getenv("PIPELINE_API_KEY")
    if not key or not x_api_key or not hmac.compare_digest(x_api_key.encode(), key.encode()):
        # 403 เสมอ — 401 สงวนไว้ให้ "ATMS login/session ไม่ผ่าน"
        raise HTTPException(status_code=403, detail="api key ไม่ถูกต้อง")


_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def _check_date(d: Optional[str]) -> None:
    if d is not None and not _DATE_RE.fullmatch(d):
        raise HTTPException(status_code=422, detail="วันที่ต้องเป็นรูปแบบ DD/MM/YYYY")


class Creds(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    phpsessid: Optional[str] = None


def atms_creds(
    x_atms_username: Optional[str] = Header(None, alias="X-ATMS-Username"),
    x_atms_password: Optional[str] = Header(None, alias="X-ATMS-Password"),
    x_phpsessid: Optional[str] = Header(None, alias="X-PHPSESSID"),
) -> Creds:
    if not x_phpsessid and not (x_atms_username and x_atms_password):
        raise HTTPException(status_code=401, detail="ต้องส่งบัญชี ATMS ของผู้ใช้มา")
    return Creds(username=x_atms_username, password=x_atms_password, phpsessid=x_phpsessid)


# ── client cache (ต่อ username, 20 นาที) ───────────────────────────────────
_cache: Dict[str, Dict[str, Any]] = {}
_user_locks: Dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _pw_hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _lock_for(username: str) -> threading.Lock:
    with _guard:
        return _user_locks.setdefault(username, threading.Lock())


def _drop(username: Optional[str]) -> None:
    if username:
        with _guard:
            _cache.pop(username, None)


def _get_client(c: Creds, dry_run: bool = False) -> AtmsWithdrawClient:
    if c.phpsessid and not (c.username and c.password):
        return AtmsWithdrawClient(phpsessid=c.phpsessid, dry_run=dry_run)
    if dry_run:  # ไม่เอา client ที่ dry_run เข้าแคช
        return AtmsWithdrawClient(username=c.username, password=c.password, dry_run=True)
    h, now = _pw_hash(c.password), time.monotonic()
    with _guard:
        for u in [u for u, e in _cache.items() if e["exp"] <= now]:
            del _cache[u]
        e = _cache.get(c.username)
        if e and e["pw"] == h:
            e["exp"] = now + CLIENT_TTL
            return e["client"]
        client = AtmsWithdrawClient(username=c.username, password=c.password)
        _cache[c.username] = {"client": client, "pw": h, "exp": now + CLIENT_TTL}
        return client


def _run(c: Creds, fn, *, write: bool = False, dry_run: bool = False):
    """รัน fn(client) แล้วแปลง error เป็น HTTP — write = ทีละงานต่อ username"""
    lock = _lock_for(c.username) if (write and c.username and not dry_run) else None
    try:
        if lock:
            lock.acquire()
        return fn(_get_client(c, dry_run))
    except WithdrawPartialError as e:
        if isinstance(e.cause, AtmsAuthError):
            _drop(c.username)        # session/รหัสผ่านใช้ไม่ได้แล้ว → ครั้งหน้าต้อง login ใหม่
        return JSONResponse(status_code=409, content={"detail": str(e), "progress": asdict(e.progress)})
    except AtmsAuthError as e:
        _drop(c.username)
        raise HTTPException(status_code=401, detail=str(e))
    except AtmsNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except AtmsOpenJobError as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        if lock:
            lock.release()


# ── schema ──────────────────────────────────────────────────────────────────
class IssueLineIn(BaseModel):
    sku_id: int
    amount: float
    sku_name: str
    mr_item_id: Optional[int] = None
    serial_no_id: Optional[int] = None
    remark: str = ""


class ReturnLineIn(BaseModel):
    sku_id: int
    amount: float
    unit_price: float
    sku_name: str
    serial_no: str = ""
    remark: str = ""


class ProgressIn(BaseModel):
    gm_id: Optional[int] = None
    done: List[int] = Field(default_factory=list)
    approved: bool = False
    code: Optional[str] = None


class IssueBody(BaseModel):
    inventory_id: int
    pick_to_id: int
    remark_token: str
    remark: str = ""
    date: Optional[str] = None  # DD/MM/YYYY วันที่สร้างใบ
    lines: List[IssueLineIn]
    progress: Optional[ProgressIn] = None
    dry_run: bool = False


class ReturnBody(BaseModel):
    inventory_id: int
    withdraw_id: int
    withdraw_code: str
    remark_token: str
    date: Optional[str] = None
    lines: List[ReturnLineIn]
    progress: Optional[ProgressIn] = None
    mark_complete: bool = False
    dry_run: bool = False


def _progress(p: Optional[ProgressIn]) -> Optional[IssueProgress]:
    return IssueProgress(**p.model_dump()) if p else None


# ── read ────────────────────────────────────────────────────────────────────
@router.get("/options", dependencies=[Depends(require_api_key)])
def options(c: Creds = Depends(atms_creds)):
    return _run(c, lambda cl: cl.options())


@router.get("/whoami", dependencies=[Depends(require_api_key)])
def whoami(c: Creds = Depends(atms_creds)):
    return _run(c, lambda cl: cl.profile())


@router.get("/list", dependencies=[Depends(require_api_key)])
def list_withdraws(inventory_id: int = Query(...), date: str = Query(..., description="DD/MM/YYYY"),
                   c: Creds = Depends(atms_creds)):
    _check_date(date)
    return {"rows": _run(c, lambda cl: cl.list_withdraws(inventory_id, date))}


@router.get("/lookup/{kind}", dependencies=[Depends(require_api_key)])
def lookup(kind: str, q: str = Query(""), limit: int = Query(20, ge=1, le=100),
           inventory_id: Optional[int] = None, sku_id: Optional[int] = None,
           c: Creds = Depends(atms_creds)):
    return {"results": _run(c, lambda cl: cl.lookup(kind, q, limit, inventory_id, sku_id))}


# ── write ───────────────────────────────────────────────────────────────────
@router.post("/issue", dependencies=[Depends(require_api_key)])
def issue(body: IssueBody, c: Creds = Depends(atms_creds)):
    _check_date(body.date)
    lines = [IssueLine(**ln.model_dump()) for ln in body.lines]
    p = _run(c, lambda cl: cl.issue_withdraw(
        body.inventory_id, body.pick_to_id, body.remark_token, lines,
        progress=_progress(body.progress), remark=body.remark, date_ddmmyyyy=body.date),
        write=True, dry_run=body.dry_run)
    return p if isinstance(p, JSONResponse) else {"progress": asdict(p)}


@router.post("/return", dependencies=[Depends(require_api_key)])
def return_(body: ReturnBody, c: Creds = Depends(atms_creds)):
    _check_date(body.date)
    lines = [ReturnLine(**ln.model_dump()) for ln in body.lines]
    p = _run(c, lambda cl: cl.return_to_stock(
        body.inventory_id, body.withdraw_id, body.withdraw_code, body.remark_token, lines,
        progress=_progress(body.progress), mark_complete=body.mark_complete,
        date_ddmmyyyy=body.date),
        write=True, dry_run=body.dry_run)
    return p if isinstance(p, JSONResponse) else {"progress": asdict(p)}


# ต้องอยู่ท้ายสุด ไม่ให้ชน /options /whoami /list
@router.get("/{gm_id}", dependencies=[Depends(require_api_key)])
def get_withdraw(gm_id: int, c: Creds = Depends(atms_creds)):
    return _run(c, lambda cl: cl.get_withdraw(gm_id))
