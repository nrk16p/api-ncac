# ======================================================
# 📄 ATMS — เปิด job แจ้งซ่อม / ขอเปลี่ยนยาง
#    POST /atms/openjob/          section 1 (+ items ในครั้งเดียวถ้าส่งมา)
#    POST /atms/openjob/item      section 2 แยกเส้น (ต่อจาก maintenance_request_id)
#    GET  /atms/openjob/options   ค่า dropdown จริงจาก ATMS (branch / ประเภทซ่อม / ตำแหน่งยาง)
#    GET  /atms/openjob/lookup    autocomplete ทะเบียนรถ / คนขับ / ช่าง
#    GET  /atms/openjob/search    ค้นรายการ job (ตามเลขที่เอกสาร / ทะเบียน / ช่วงวันที่)
#    GET  /atms/openjob/{ref}     ดึง job ที่เปิดไว้ — ref = id (175039) หรือ code (SBMR26070457)
#    ทุกเส้นส่ง cookie ผ่าน header X-PHPSESSID (ไม่ส่ง = login ด้วย env)
# ======================================================
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from services.atms_openjob_service import (
    AtmsAuthError,
    AtmsClient,
    AtmsNotFoundError,
    AtmsOpenJobError,
    LOOKUP_URLS,
    get_client,
)

router = APIRouter(prefix="/atms/openjob", tags=["ATMS Open Job"])


# ── schema ──────────────────────────────────────────────────────────────────
class JobItem(BaseModel):
    """1 รายการซ่อม — ฟิลด์นอก schema ส่งผ่านไปให้ ATMS ตามเดิม"""
    model_config = ConfigDict(extra="allow")

    maintenance_type_id: str = Field(..., description="ดูค่าได้จาก GET /atms/openjob/options")
    problem: str = Field("", description="รายละเอียดอาการ")
    has_refer: str = Field("0", description="1 = อ้างอิงใบแจ้งซ่อมเดิม")
    ref_maintenance_request: Optional[str] = ""
    ref_maintenance_request_id: Optional[str] = ""
    ref_problem: Optional[str] = ""
    images: List[str] = Field(default_factory=list,
                              description="รูปแนบสูงสุด 3 รูป — base64 / data-URI / path ไฟล์")


class OpenJobRequest(BaseModel):
    """
    section 1 — ส่งแค่ "ชื่อ" ได้เลย (vehicle / driver / mechanic)
    helper จะยิง autocomplete หา *_id + owner_type_id ให้เอง ถ้าไม่ได้ระบุมา
    """
    model_config = ConfigDict(extra="allow")

    flow: str = Field("request tire", description="request tire | request maintenance")
    schedule_at: str = Field(..., description="DD/MM/YYYY HH:MM เช่น 30/07/2026 16:51")
    branch_id: str = Field(..., description="ดูค่าได้จาก GET /atms/openjob/options")
    owner_type_id: Optional[str] = Field("", description="เว้นไว้ = ดึงจากทะเบียนรถให้อัตโนมัติ")
    vehicle: Optional[str] = Field("", description="ทะเบียนรถ เช่น 1ฒย-838")
    vehicle_id: Optional[str] = ""
    driver: Optional[str] = ""
    driver_id: Optional[str] = ""
    mechanic: Optional[str] = ""
    mechanic_id: Optional[str] = ""
    accident: Optional[str] = ""
    accident_id: Optional[str] = ""
    inform_mile_no: Optional[str] = ""
    is_broken: str = "0"
    tire_positions: List[str] = Field(default_factory=list,
                                      description='เลข ("1") หรือรหัสตำแหน่ง ("F1", "RA3") ก็ได้')
    items: List[JobItem] = Field(default_factory=list,
                                 description="ใส่มาด้วย = เปิด job + ลงรายการให้ในครั้งเดียว")


class AddItemsRequest(BaseModel):
    maintenance_request_id: int = Field(..., description="ได้จาก response ของ section 1")
    items: List[JobItem] = Field(..., min_length=1)


# ── shared ──────────────────────────────────────────────────────────────────
def _client(phpsessid: Optional[str]) -> AtmsClient:
    try:
        return get_client(phpsessid)
    except AtmsAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))


def _run(fn, *args, **kw):
    """map ข้อผิดพลาดของ ATMS → HTTP status ที่สื่อความหมาย"""
    try:
        return fn(*args, **kw)
    except AtmsAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except AtmsNotFoundError as e:                       # ต้องมาก่อน AtmsOpenJobError (เป็นลูกของมัน)
        raise HTTPException(status_code=404, detail=str(e))
    except AtmsOpenJobError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ── endpoints ───────────────────────────────────────────────────────────────
@router.post("/", summary="เปิด job (section 1) — ใส่ items มาด้วยได้")
def create_open_job(
    payload: OpenJobRequest,
    x_phpsessid: Optional[str] = Header(None, alias="X-PHPSESSID"),
) -> Dict[str, Any]:
    """
    ATMS ตอบ 302 เมื่อสำเร็จ (Post/Redirect/Get) — helper อ่าน `Location` เพื่อแยกว่า
    สำเร็จ (ได้ `maintenance_request_id`) หรือ session หมดอายุ (เด้ง login → 401)
    """
    body = payload.model_dump()
    items = body.pop("items", [])
    client = _client(x_phpsessid)
    if items:
        return _run(client.create_job, body, items)
    return {"job": _run(client.open_job, body), "items": []}


@router.post("/item", summary="เพิ่มรายการซ่อม (section 2)")
def create_job_items(
    payload: AddItemsRequest,
    x_phpsessid: Optional[str] = Header(None, alias="X-PHPSESSID"),
) -> Dict[str, Any]:
    """ฟอร์มนี้เป็น multipart/form-data (มีช่องแนบรูป) — ส่งได้หลายรายการต่อ 1 call"""
    items = [i.model_dump() for i in payload.items]
    results = _run(_client(x_phpsessid).add_items, payload.maintenance_request_id, items)
    return {"ok": True, "maintenance_request_id": payload.maintenance_request_id,
            "added": len(results), "results": results}


@router.get("/options", summary="ค่า dropdown จริงจาก ATMS")
def job_options(
    x_phpsessid: Optional[str] = Header(None, alias="X-PHPSESSID"),
) -> Dict[str, Any]:
    """อ่านสดจากฟอร์ม ATMS แล้ว cache ไว้ในหน่วยความจำ — ไม่ต้องฝัง magic number ที่ frontend"""
    client = _client(x_phpsessid)
    return {**_run(client.options, "job"), **_run(client.options, "item")}


@router.get("/lookup", summary="ค้นทะเบียนรถ / คนขับ / ช่าง")
def job_lookup(
    kind: str = Query(..., description=f"หนึ่งใน {sorted(LOOKUP_URLS)}"),
    q: str = Query(..., min_length=1, description="คำค้น เช่น 1ฒย-838"),
    limit: int = Query(20, ge=1, le=100),
    x_phpsessid: Optional[str] = Header(None, alias="X-PHPSESSID"),
) -> Dict[str, Any]:
    """ผลลัพธ์มี id ไปใส่เป็น vehicle_id / driver_id / mechanic_id ได้ตรง ๆ"""
    if kind not in LOOKUP_URLS:
        raise HTTPException(status_code=422, detail=f"kind ต้องเป็นหนึ่งใน {sorted(LOOKUP_URLS)}")
    return {"kind": kind, "q": q, "results": _run(_client(x_phpsessid).lookup, kind, q, limit)}


@router.get("/search", summary="ค้นรายการ job ที่เปิดไว้")
def search_jobs(
    code: Optional[str] = Query(None, description="เลขที่แจ้งซ่อม เช่น SBMR26070457"),
    plate_no: Optional[str] = Query(None, description="ทะเบียนรถ เช่น 1ฒย-838"),
    vehicle_no: Optional[str] = Query(None, description="เลขรถ เช่น T-0031"),
    branch_id: Optional[str] = Query(None, description="ดูค่าได้จาก GET /atms/openjob/options"),
    flow: Optional[str] = Query(None, description="request tire | request maintenance"),
    from_schedule_at: Optional[str] = Query(None, description="แจ้งซ่อมตั้งแต่ DD/MM/YYYY"),
    to_schedule_at: Optional[str] = Query(None, description="แจ้งซ่อมถึง DD/MM/YYYY"),
    order_by: Optional[str] = Query(None, description="เช่น mr.code desc"),
    limit: int = Query(50, ge=1, le=200),
    x_phpsessid: Optional[str] = Header(None, alias="X-PHPSESSID"),
) -> Dict[str, Any]:
    """
    ชื่อ query string ตรงกับช่องค้นหาของ ATMS ตรง ๆ — `fields` ใน response คือรายชื่อ
    ช่องที่ ATMS มีจริง (อ่านสดจากฟอร์ม) ส่วน filter ที่ ATMS ไม่รู้จักจะไม่ล้ม
    แต่ถูกรายงานไว้ใน `ignored`
    """
    return _run(_client(x_phpsessid).search_jobs, limit,
                code=code, plate_no=plate_no, vehicle_no=vehicle_no, branch_id=branch_id,
                flow=flow, from_schedule_at=from_schedule_at, to_schedule_at=to_schedule_at,
                order_by=order_by)


@router.get("/{ref}", summary="ดึงข้อมูล job — ใส่ id หรือเลขที่เอกสารก็ได้")
def get_job(
    ref: str,
    items: bool = Query(True, description="ดึงรายการซ่อมของ job มาด้วย"),
    raw: bool = Query(False, description="แนบ HTML ดิบมาด้วย (ไว้ debug ตอน ATMS เปลี่ยนหน้า)"),
    x_phpsessid: Optional[str] = Header(None, alias="X-PHPSESSID"),
) -> Dict[str, Any]:
    """
    `ref` เลขล้วน = maintenance_request_id (175039) · อย่างอื่น = เลขที่เอกสาร (SBMR26070457)
    ซึ่งจะถูกค้นเป็น id ให้ก่อนจากหน้า index

    response
      info   ป้ายภาษาไทย–ค่า แบบที่เห็นในหน้าเว็บ
      fields ชื่อฟิลด์ตรงกับตอน POST (vehicle_id, branch_id) — แก้แล้วยิงกลับได้เลย
      labels ความหมายของ id ในฟิลด์ (branch_id "4" → ชื่อสาขา)
      items  รายการซ่อมของ job นี้
    """
    return _run(_client(x_phpsessid).job, ref, with_items=items, raw=raw)
