"""ATMS แจ้งซ่อม / ขอเปลี่ยนยาง — auto-submit maintenance request to www.mena-atms.com.

Reverse-engineered from /veh/maintenance.request/add (Zend form, no CSRF):
  POST form-urlencoded with vehicle_id/driver_id/mechanic_id resolved via the
  same autocomplete JSON endpoints the web UI uses. Success = 302 redirect to
  /veh/maintenance.request.item/add/.../maintenance_request_id/<id>.
"""
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from scripts.engineon.common import BASE_URL, atms_session

router = APIRouter(prefix="/atms", tags=["ATMS Maintenance Request"])

BKK = timezone(timedelta(hours=7))

BRANCH_IDS = {
    "DIST": 7, "กรุงเทพฯ": 4, "กรุงเทพ": 4, "ขอนแก่น": 5, "บางปะกง": 9,
    "ระยอง": 6, "ลาดกระบัง": 2, "สระบุรี": 3,
}

TIRE_POSITION_IDS = {
    "F1": 1, "F2": 2, "F3": 24, "F4": 25,
    "RA1": 3, "RA2": 4, "RA3": 5, "RA4": 6, "RA5": 7, "RA6": 8, "RA7": 9, "RA8": 10,
    "RB1": 11, "RB2": 12, "RB3": 13, "RB4": 14, "RB5": 15, "RB6": 16,
    "RB7": 17, "RB8": 18, "RB9": 19, "RB10": 20, "RB11": 21, "RB12": 22, "RB13": 23,
}

LOOKUP_URLS = {
    "vehicle": f"{BASE_URL}/veh/vehicle/plate.no.json/",
    "driver": f"{BASE_URL}/veh/driver/name.json/",
    "mechanic": f"{BASE_URL}/account/user/mechanic.json/",
    "accident": f"{BASE_URL}/veh/accident/code.json/",
    "maintenance_request": f"{BASE_URL}/veh/maintenance.request/code.json/",
    "ship_to": f"{BASE_URL}/tms/ship.to/code.json/",
    "location": f"{BASE_URL}/tms/location/ac.json/",
}

_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def _verify_key(x_api_key: str = Header(..., alias="x-api-key")):
    key = os.getenv("PIPELINE_API_KEY")
    if not key or x_api_key != key:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _get_session(force_new: bool = False) -> requests.Session:
    global _session
    with _session_lock:
        if force_new or _session is None:
            _session = atms_session()
        return _session


def _atms_lookup(kind: str, q: str, retry: bool = True) -> List[dict]:
    params = {"q": q, "limit": 100}
    if kind == "vehicle":
        params["tail"] = "all"
    s = _get_session()
    r = s.get(LOOKUP_URLS[kind], params=params, timeout=30)
    try:
        return r.json()
    except ValueError:
        # ATMS returns the login page (HTML, HTTP 200) when the session expires
        if retry:
            _get_session(force_new=True)
            return _atms_lookup(kind, q, retry=False)
        raise HTTPException(status_code=502, detail="ATMS session expired and re-login failed")


def _resolve(kind: str, q: str) -> dict:
    rows = _atms_lookup(kind, q)
    if not rows:
        raise HTTPException(status_code=422, detail=f"{kind}: no match for '{q}'")
    ql = q.strip().lower()
    exact = [r for r in rows if r["name"].strip().lower() == ql]
    prefix = [r for r in rows if r["name"].strip().lower().startswith(ql)]
    if exact:
        return exact[0]
    if len(rows) == 1:
        return rows[0]
    if len(prefix) == 1:
        return prefix[0]
    raise HTTPException(status_code=422, detail={
        "error": f"{kind}: ambiguous match for '{q}' — ระบุให้ชัดขึ้นหรือใช้ *_id ตรง ๆ",
        "candidates": [{"id": r["id"], "name": r["name"]} for r in rows[:20]],
    })


class MaintenanceRequestIn(BaseModel):
    flow: str = "request maintenance"          # "request maintenance" (แจ้งซ่อม) | "request tire" (ขอเปลี่ยนยาง)
    vehicle: Optional[str] = None              # ทะเบียน/รหัสรถ — resolved via autocomplete
    vehicle_id: Optional[int] = None
    driver: Optional[str] = None               # ชื่อ พจส.
    driver_id: Optional[int] = None
    mechanic: Optional[str] = None
    mechanic_id: Optional[int] = None
    accident: Optional[str] = None
    accident_id: Optional[int] = None
    branch: Optional[str] = None               # ชื่อสาขา เช่น "กรุงเทพฯ"
    branch_id: Optional[int] = None
    owner_type_id: Optional[int] = None        # default: จาก vehicle lookup
    inform_mile_no: int
    schedule_at: Optional[str] = None          # "dd/mm/YYYY HH:MM" — default now (BKK)
    is_broken: bool = False
    tire_positions: List[str] = []             # เช่น ["F1","RA3"] หรือเลข id — ใช้เมื่อ flow=request tire
    dry_run: bool = False


@router.get("/lookup/{kind}", dependencies=[Depends(_verify_key)])
def lookup(kind: str, q: str):
    """Passthrough autocomplete: kind = vehicle | driver | mechanic | accident."""
    if kind not in LOOKUP_URLS:
        raise HTTPException(status_code=404, detail=f"unknown kind '{kind}'")
    return _atms_lookup(kind, q)


@router.get("/maintenance-request/by-code/{code}", dependencies=[Depends(_verify_key)])
def maintenance_request_by_code(code: str):
    """แปลงเลขที่ใบแจ้งซ่อม เช่น BKMR26080001 → internal id สำหรับ URL view/edit."""
    row = _resolve("maintenance_request", code)
    return {"code": row["name"], "id": row["id"],
            "view_url": f"{BASE_URL}/veh/maintenance.request/view/id/{row['id']}"}


@router.post("/maintenance-request", dependencies=[Depends(_verify_key)])
def create_maintenance_request(payload: MaintenanceRequestIn):
    if payload.flow not in ("request maintenance", "request tire"):
        raise HTTPException(status_code=422, detail="flow ต้องเป็น 'request maintenance' หรือ 'request tire'")

    branch_id = payload.branch_id
    if branch_id is None and payload.branch:
        branch_id = BRANCH_IDS.get(payload.branch.strip())
    if branch_id is None:
        raise HTTPException(status_code=422, detail=f"branch ไม่รู้จัก — ใช้ {list(BRANCH_IDS)} หรือส่ง branch_id")

    vehicle_id, vehicle_name, owner_type_id = payload.vehicle_id, payload.vehicle, payload.owner_type_id
    if vehicle_id is None:
        if not payload.vehicle:
            raise HTTPException(status_code=422, detail="ต้องระบุ vehicle หรือ vehicle_id")
        v = _resolve("vehicle", payload.vehicle)
        vehicle_id, vehicle_name = v["id"], v["name"]
        if owner_type_id is None:
            owner_type_id = v.get("owner_type_id")
    if owner_type_id is None:
        raise HTTPException(status_code=422, detail="owner_type_id หาไม่ได้จาก vehicle — ส่งมาตรง ๆ")

    driver_id, driver_name = payload.driver_id, payload.driver
    if driver_id is None:
        if not payload.driver:
            raise HTTPException(status_code=422, detail="ต้องระบุ driver หรือ driver_id")
        d = _resolve("driver", payload.driver)
        driver_id, driver_name = d["id"], d["name"]

    mechanic_id, mechanic_name = payload.mechanic_id, payload.mechanic
    if mechanic_id is None:
        if not payload.mechanic:
            raise HTTPException(status_code=422, detail="ต้องระบุ mechanic หรือ mechanic_id")
        m = _resolve("mechanic", payload.mechanic)
        mechanic_id, mechanic_name = m["id"], m["name"]

    accident_id, accident_name = payload.accident_id, payload.accident
    if accident_id is None and payload.accident:
        a = _resolve("accident", payload.accident)
        accident_id, accident_name = a["id"], a["name"]

    tire_ids = []
    if payload.flow == "request tire":
        if not payload.tire_positions:
            raise HTTPException(status_code=422, detail="flow=request tire ต้องระบุ tire_positions")
        for t in payload.tire_positions:
            key = str(t).strip().upper()
            tid = TIRE_POSITION_IDS.get(key) or (int(key) if key.isdigit() else None)
            if tid is None:
                raise HTTPException(status_code=422, detail=f"tire position ไม่รู้จัก: '{t}' — ใช้ {list(TIRE_POSITION_IDS)}")
            tire_ids.append(tid)

    schedule_at = payload.schedule_at or datetime.now(BKK).strftime("%d/%m/%Y %H:%M")

    form = {
        "flow": payload.flow,
        "schedule_at": schedule_at,
        "branch_id": str(branch_id),
        "owner_type_id": str(owner_type_id),
        "mechanic": mechanic_name or "",
        "mechanic_id": str(mechanic_id),
        "accident": accident_name or "",
        "accident_id": str(accident_id) if accident_id else "",
        "driver": driver_name or "",
        "driver_id": str(driver_id),
        "vehicle": vehicle_name or "",
        "vehicle_id": str(vehicle_id),
        "inform_mile_no": str(payload.inform_mile_no),
        "is_broken": "1" if payload.is_broken else "0",
        "mode": "",
    }
    if tire_ids:
        form["tire_positions[]"] = [str(t) for t in tire_ids]

    if payload.dry_run:
        return {"result": "dry_run", "form": form}

    s = _get_session()
    r = s.post(f"{BASE_URL}/veh/maintenance.request/add", data=form,
               timeout=30, allow_redirects=False)
    if r.status_code != 302:
        # expired session renders the login page with HTTP 200 — retry once after re-login
        _get_session(force_new=True)
        r = _get_session().post(f"{BASE_URL}/veh/maintenance.request/add", data=form,
                                timeout=30, allow_redirects=False)
    location = r.headers.get("Location", "")
    m = re.search(r"maintenance_request_id/(\d+)", location)
    if r.status_code != 302 or not m:
        raise HTTPException(status_code=502, detail={
            "error": "ATMS ไม่ตอบ redirect ที่คาดไว้ — ใบแจ้งซ่อมอาจไม่ถูกสร้าง",
            "status_code": r.status_code, "location": location,
        })
    mr_id = int(m.group(1))
    return {
        "result": "created",
        "maintenance_request_id": mr_id,
        "url": f"{BASE_URL}/veh/maintenance.request.item/add/mode//maintenance_request_id/{mr_id}",
        "form": form,
    }
