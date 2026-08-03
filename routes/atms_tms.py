"""ATMS TMS — auto-submit ใบงานขนส่ง (deliver order) และ Route/Ship To.

Reverse-engineered from /tms/deliver.order/add and /tms/ship.to/add (Zend forms,
no CSRF — same pattern as routes/atms_maintenance.py). Plain selects that have no
autocomplete endpoint (service, customer, zone, plant, product, traffic) are
resolved by parsing the add-form's <option> list, cached per process.

Deliver order quirk: choosing a service injects extra required selects
(service_parameter_A/B/...) fetched from /tms/service.parameter/get.selects/ —
pass them via "service_parameters": {"A": "..."} and inspect them first with
GET /atms/tms/service-info.
"""
import re
import threading
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from routes.atms_maintenance import (
    BASE_URL, BRANCH_IDS, _get_session, _resolve, _verify_key,
)

router = APIRouter(prefix="/atms/tms", tags=["ATMS TMS"])

_form_options_cache: Dict[str, Dict[str, Dict[str, str]]] = {}
_form_cache_lock = threading.Lock()

FORM_PAGES = {
    "deliver_order": f"{BASE_URL}/tms/deliver.order/add",
    "ship_to": f"{BASE_URL}/tms/ship.to/add",
}


def _form_options(page: str, refresh: bool = False) -> Dict[str, Dict[str, str]]:
    """{select_name: {option_label: value}} parsed from the add-form page."""
    with _form_cache_lock:
        if not refresh and page in _form_options_cache:
            return _form_options_cache[page]
    r = _get_session().get(FORM_PAGES[page], timeout=30)
    if "basic-input-form" not in r.text:
        _get_session(force_new=True)
        r = _get_session().get(FORM_PAGES[page], timeout=30)
        if "basic-input-form" not in r.text:
            raise HTTPException(status_code=502, detail="ATMS session expired and re-login failed")
    out: Dict[str, Dict[str, str]] = {}
    for name, body in re.findall(r'<select name="([\w\[\]]+)"[^>]*>(.*?)</select>', r.text, re.S):
        out[name] = {label: value
                     for value, label in re.findall(r'<option value="([^"]*)" label="([^"]*)"', body)
                     if value != ""}
    with _form_cache_lock:
        _form_options_cache[page] = out
    return out


def _resolve_select(page: str, select: str, q: str) -> str:
    """Resolve an option label (exact, else unique substring match) to its value."""
    options = _form_options(page).get(select, {})
    if q in options:
        return options[q]
    ql = q.strip().lower()
    hits = {label: v for label, v in options.items() if ql in label.lower()}
    if len(hits) == 1:
        return next(iter(hits.values()))
    if not hits:
        raise HTTPException(status_code=422, detail=f"{select}: no match for '{q}'")
    raise HTTPException(status_code=422, detail={
        "error": f"{select}: ambiguous match for '{q}'",
        "candidates": sorted(hits)[:20],
    })


def _post_form(path: str, form: dict) -> dict:
    s = _get_session()
    r = s.post(f"{BASE_URL}{path}", data=form, timeout=30, allow_redirects=False)
    if r.status_code != 302:
        _get_session(force_new=True)
        r = _get_session().post(f"{BASE_URL}{path}", data=form, timeout=30, allow_redirects=False)
    location = r.headers.get("Location", "")
    if r.status_code != 302 or "/add" in location.replace(path, ""):
        raise HTTPException(status_code=502, detail={
            "error": "ATMS ไม่ตอบ redirect ที่คาดไว้ — รายการอาจไม่ถูกสร้าง",
            "status_code": r.status_code, "location": location,
        })
    m = re.search(r"(\d+)/?$", location)
    return {"result": "created", "id": int(m.group(1)) if m else None,
            "location": location, "form": form}


@router.get("/service-info", dependencies=[Depends(_verify_key)])
def service_info(service_id: int):
    """ข้อมูล service + service_parameter selects ที่ต้องส่งเพิ่ม + ชื่อ ref fields."""
    s = _get_session()
    svc = s.get(f"{BASE_URL}/tms/service/get.service.json/",
                params={"id": service_id}, timeout=30).json()
    html = s.get(f"{BASE_URL}/tms/service.parameter/get.selects/",
                 params={"service_id": service_id}, timeout=30).text
    params = {}
    for name, body in re.findall(r'<select name="(service_parameter_\w+)"[^>]*>(.*?)</select>', html, re.S):
        params[name] = [v for v, _ in re.findall(r'<option value="([^"]*)" label="([^"]*)"', body) if v != ""]
    ref_fields = {f"ref_field_0{i}": svc.get(f"ref_field_0{i}") or None for i in range(1, 5)}
    return {"service": {"id": svc.get("id"), "name": svc.get("name"), "code": svc.get("code")},
            "ref_fields": ref_fields, "service_parameters": params}


@router.get("/options/{page}", dependencies=[Depends(_verify_key)])
def form_options(page: str, select: Optional[str] = None, refresh: bool = False):
    """ตัวเลือกทั้งหมดของฟอร์ม: page = deliver_order | ship_to (?select=service_id เจาะราย select)."""
    if page not in FORM_PAGES:
        raise HTTPException(status_code=404, detail=f"unknown page '{page}' — ใช้ {list(FORM_PAGES)}")
    opts = _form_options(page, refresh=refresh)
    if select:
        if select not in opts:
            raise HTTPException(status_code=404, detail=f"no select '{select}' — มี {list(opts)}")
        return opts[select]
    return opts


class DeliverOrderIn(BaseModel):
    branch: Optional[str] = None
    branch_id: Optional[int] = None
    code: str                                  # เลข LDT
    service: Optional[str] = None              # ชื่อบริการ — resolve จาก select ในฟอร์ม
    service_id: Optional[int] = None
    product: Optional[str] = None
    product_id: Optional[int] = None
    type_of_run: str                           # "one way" | "round trip" | "legacy"
    type_of_shipping: str                      # "heavy" (หนัก) | "empty" (เบา)
    ship_to: Optional[str] = None              # Route/Ship To — autocomplete
    ship_to_id: Optional[int] = None
    t_date: str                                # "dd/mm/yyyy"
    due_date: Optional[str] = None             # default = t_date
    ref_field_01: Optional[str] = None
    ref_field_02: Optional[str] = None
    ref_field_03: Optional[str] = None
    ref_field_04: Optional[str] = None
    remark: Optional[str] = None
    service_parameters: Dict[str, str] = {}    # {"A": "..."} → service_parameter_A
    dry_run: bool = False


@router.post("/deliver-order", dependencies=[Depends(_verify_key)])
def create_deliver_order(payload: DeliverOrderIn):
    if payload.type_of_run not in ("one way", "round trip", "legacy"):
        raise HTTPException(status_code=422, detail="type_of_run ต้องเป็น 'one way' | 'round trip' | 'legacy'")
    if payload.type_of_shipping not in ("heavy", "empty"):
        raise HTTPException(status_code=422, detail="type_of_shipping ต้องเป็น 'heavy' (หนัก) | 'empty' (เบา)")

    branch_id = payload.branch_id
    if branch_id is None and payload.branch:
        branch_id = BRANCH_IDS.get(payload.branch.strip())
    if branch_id is None:
        raise HTTPException(status_code=422, detail=f"branch ไม่รู้จัก — ใช้ {list(BRANCH_IDS)} หรือส่ง branch_id")

    service_id = payload.service_id
    if service_id is None:
        if not payload.service:
            raise HTTPException(status_code=422, detail="ต้องระบุ service หรือ service_id")
        service_id = int(_resolve_select("deliver_order", "service_id", payload.service))

    product_id = payload.product_id
    if product_id is None and payload.product:
        product_id = int(_resolve_select("deliver_order", "product_id", payload.product))

    ship_to_id, ship_to_name = payload.ship_to_id, payload.ship_to
    if ship_to_id is None:
        if not payload.ship_to:
            raise HTTPException(status_code=422, detail="ต้องระบุ ship_to หรือ ship_to_id")
        st = _resolve("ship_to", payload.ship_to)
        ship_to_id, ship_to_name = st["id"], st["name"]

    form = {
        "branch_id": str(branch_id),
        "product_id": str(product_id) if product_id else "",
        "code": payload.code,
        "service_id": str(service_id),
        "type_of_run": payload.type_of_run,
        "type_of_shipping": payload.type_of_shipping,
        "ship_to": ship_to_name or "",
        "ship_to_id": str(ship_to_id),
        "t_date": payload.t_date,
        "due_date": payload.due_date or payload.t_date,
        "ref_field_01": payload.ref_field_01 or "",
        "ref_field_02": payload.ref_field_02 or "",
        "ref_field_03": payload.ref_field_03 or "",
        "ref_field_04": payload.ref_field_04 or "",
        "remark": payload.remark or "",
    }
    for key, value in payload.service_parameters.items():
        name = key if key.startswith("service_parameter_") else f"service_parameter_{key.upper()}"
        form[name] = value

    if payload.dry_run:
        return {"result": "dry_run", "form": form}
    out = _post_form("/tms/deliver.order/add", form)
    out["deliver_order_id"] = out.pop("id")
    return out


class ShipToIn(BaseModel):
    customer: Optional[str] = None             # ชื่อลูกค้า — resolve จาก select
    customer_id: Optional[int] = None
    from_location: Optional[str] = None        # ต้นทาง — autocomplete
    from_location_id: Optional[int] = None
    to_location: Optional[str] = None          # ปลายทาง — autocomplete
    to_location_id: Optional[int] = None
    zone: Optional[str] = None                 # โซนการจัดส่ง
    zone_id: Optional[int] = None
    traffic: Optional[str] = None              # สภาพการจราจร (optional)
    traffic_id: Optional[int] = None
    plant: Optional[str] = None                # แพล้นท์ (optional)
    plant_id: Optional[int] = None
    code: str                                  # Route/Ship To
    sub_code: str
    name: str
    ref_no: Optional[str] = None
    address: Optional[str] = None
    valid_from_date: str                       # "dd/mm/yyyy"
    valid_to_date: str
    distance: float                            # ระยะทาง
    distance_08: float = 0                     # ตีเปล่า
    distance_01: float = 0                     # ทางเรียบหนัก
    distance_02: float = 0                     # ขึ้นเขาหนัก
    distance_03: float = 0                     # ขึ้นเขาสูงหนัก
    distance_05: float = 0                     # ทางเรียบ
    distance_06: float = 0                     # ขึ้นเขา
    distance_07: float = 0                     # ขึ้นเขาสูง
    distance_04: float = 0                     # สำรอง
    dry_run: bool = False


@router.post("/ship-to", dependencies=[Depends(_verify_key)])
def create_ship_to(payload: ShipToIn):
    customer_id = payload.customer_id
    if customer_id is None:
        if not payload.customer:
            raise HTTPException(status_code=422, detail="ต้องระบุ customer หรือ customer_id")
        customer_id = int(_resolve_select("ship_to", "customer_id", payload.customer))

    zone_id = payload.zone_id
    if zone_id is None:
        if not payload.zone:
            raise HTTPException(status_code=422, detail="ต้องระบุ zone หรือ zone_id")
        zone_id = int(_resolve_select("ship_to", "zone_id", payload.zone))

    traffic_id = payload.traffic_id
    if traffic_id is None and payload.traffic:
        traffic_id = int(_resolve_select("ship_to", "traffic_id", payload.traffic))
    plant_id = payload.plant_id
    if plant_id is None and payload.plant:
        plant_id = int(_resolve_select("ship_to", "plant_id", payload.plant))

    from_id, from_name = payload.from_location_id, payload.from_location
    if from_id is None:
        if not payload.from_location:
            raise HTTPException(status_code=422, detail="ต้องระบุ from_location หรือ from_location_id")
        loc = _resolve("location", payload.from_location)
        from_id, from_name = loc["id"], loc["name"]

    to_id, to_name = payload.to_location_id, payload.to_location
    if to_id is None:
        if not payload.to_location:
            raise HTTPException(status_code=422, detail="ต้องระบุ to_location หรือ to_location_id")
        loc = _resolve("location", payload.to_location)
        to_id, to_name = loc["id"], loc["name"]

    form = {
        "customer_id": str(customer_id),
        "from_location": from_name or "",
        "from_location_id": str(from_id),
        "to_location": to_name or "",
        "to_location_id": str(to_id),
        "zone_id": str(zone_id),
        "traffic_id": str(traffic_id) if traffic_id else "",
        "plant_id": str(plant_id) if plant_id else "",
        "code": payload.code,
        "sub_code": payload.sub_code,
        "name": payload.name,
        "ref_no": payload.ref_no or "",
        "address": payload.address or "",
        "valid_from_date": payload.valid_from_date,
        "valid_to_date": payload.valid_to_date,
        "distance": str(payload.distance),
        "distance_08": str(payload.distance_08),
        "distance_01": str(payload.distance_01),
        "distance_02": str(payload.distance_02),
        "distance_03": str(payload.distance_03),
        "distance_05": str(payload.distance_05),
        "distance_06": str(payload.distance_06),
        "distance_07": str(payload.distance_07),
        "distance_04": str(payload.distance_04),
    }
    if payload.dry_run:
        return {"result": "dry_run", "form": form}
    out = _post_form("/tms/ship.to/add", form)
    out["ship_to_id"] = out.pop("id")
    return out
