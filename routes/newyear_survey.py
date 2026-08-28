"""
แบบสำรวจเสียง พจส. งานปีใหม่ — เก็บลง MongoDB

  DB         : hr_service
  Collection : newyear-survey

รูปแบบเอกสาร: เก็บ "รหัส พจส. + ชื่อ + คำตอบ" โดย `answers` เป็น object ที่
key = ข้อความคำถาม และ value = คำตอบ เพื่อให้เปิดใน Compass แล้วอ่านรู้เรื่องทันที
ส่วน `answers_detail` เก็บโครงเดิม (id / no / type / other) ไว้ให้เอาไปวิเคราะห์ต่อ

ตอบซ้ำได้ — upsert ด้วยคู่ (survey_id, drivercode) คำตอบล่าสุดทับของเดิม
ไม่เกิดเอกสารซ้ำคนเดียวกัน
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from schemas.newyear_survey import (
    AnswerItem,
    NewYearSurveyCreate,
    NewYearSurveyDoc,
    NewYearSurveySaveResult,
)
from services.mongo_service import get_mongo_db

router = APIRouter(prefix="/newyear-survey", tags=["New Year Survey"])

DB_NAME = "hr_service"
COLLECTION_NAME = "newyear-survey"

_index_ready = False


def _collection() -> Collection:
    """
    คืน collection พร้อม unique index (survey_id, drivercode)

    สร้าง index แบบ lazy ครั้งเดียวต่อ process — ไม่ทำตอน import เพราะถ้า Mongo
    ล่มตอนแอปบูต จะทำให้ทั้งแอปสตาร์ตไม่ขึ้นทั้งที่ route อื่นไม่ได้ใช้ Mongo เลย
    """
    global _index_ready

    try:
        col = get_mongo_db(DB_NAME)[COLLECTION_NAME]
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not _index_ready:
        try:
            col.create_index(
                [("survey_id", 1), ("drivercode", 1)],
                unique=True,
                name="uniq_survey_drivercode",
            )
            col.create_index([("submitted_at", -1)], name="submitted_at_desc")
            _index_ready = True
        except PyMongoError:
            # index สร้างไม่ได้ไม่ควรทำให้บันทึกคำตอบไม่ได้ — ลองใหม่รอบหน้า
            pass

    return col


# ---------------------------------------------------------------------------
# แปลงคำตอบให้อ่านง่าย
# ---------------------------------------------------------------------------

def _is_other_option(text: str) -> bool:
    return "อื่น" in text


def _readable_answer(item: AnswerItem) -> Any:
    """
    ยุบคำตอบ 1 ข้อให้เป็นค่าเดียวที่อ่านรู้เรื่อง

      choice     → "1–3 ปี"                     (ถ้าเลือก 'อื่นๆ' → "อื่นๆ: ข้อความที่พิมพ์")
      checkbox   → ["ติดงาน / รถต้องวิ่ง", ...]
      rank       → ["อาหารและเครื่องดื่ม", ...]  เรียงอันดับ 1 → N
      paragraph  → "ข้อความ"
    """
    if item.type == "rank":
        if item.ranked:
            return list(item.ranked)
        ranks = item.value if isinstance(item.value, dict) else {}
        return [k for k, _ in sorted(ranks.items(), key=lambda kv: kv[1])]

    if item.type == "checkbox":
        values = [str(v) for v in (item.value or [])]
        if item.other:
            for i, v in enumerate(values):
                if _is_other_option(v):
                    values[i] = f"{v}: {item.other}"
                    break
            else:
                values.append(item.other)
        return values

    if item.type == "choice":
        if item.value is None:
            return None
        if item.other:
            return f"{item.value}: {item.other}"
        return str(item.value)

    return item.value


def _safe_key(question: str) -> str:
    """
    ชื่อ field ใน Mongo ห้ามมี '.' และห้ามขึ้นต้นด้วย '$'
    คำถามชุดนี้ไม่มีอักขระพวกนั้น แต่กันไว้เผื่อ HR แก้ข้อความทีหลัง
    """
    key = (question or "").strip().replace(".", "·")
    if key.startswith("$"):
        key = "_" + key[1:]
    return key or "ไม่ระบุคำถาม"


def _build_answers_map(items: List[AnswerItem]) -> Dict[str, Any]:
    """object: คำถาม → คำตอบ (คำถามซ้ำกันจะต่อท้ายด้วยเลขข้อ กันค่าทับกัน)"""
    result: Dict[str, Any] = {}

    for item in items:
        key = _safe_key(item.question)
        if key in result:
            key = f"{key} (ข้อ {item.no})" if item.no else f"{key} ({item.id})"
        result[key] = _readable_answer(item)

    return result


def _has_answer(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict, str)):
        return len(value) > 0
    return True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/", response_model=NewYearSurveySaveResult, status_code=201)
def submit_newyear_survey(payload: NewYearSurveyCreate):
    """บันทึกคำตอบแบบสำรวจปีใหม่ — ส่งซ้ำได้ ระบบจะทับคำตอบเดิมของรหัสพนักงานนั้น"""
    drivercode = (payload.respondent.employee_code or "").strip()
    if not drivercode:
        raise HTTPException(
            status_code=400,
            detail="ไม่พบรหัสพนักงาน (respondent.employee_code) — บันทึกคำตอบไม่ได้",
        )

    if not payload.answers:
        raise HTTPException(status_code=400, detail="ไม่มีคำตอบส่งมา (answers ว่าง)")

    answers_map = _build_answers_map(payload.answers)
    answered_count = sum(1 for v in answers_map.values() if _has_answer(v))
    now = datetime.now(timezone.utc)
    submitted_at = payload.meta.submitted_at or now

    document: Dict[str, Any] = {
        "survey_id": payload.survey.id,
        "survey_version": payload.survey.version,
        "survey_title": payload.survey.title,

        "drivercode": drivercode,
        "driver_name": payload.respondent.employee_name,
        "truckplate": payload.respondent.truckplate,
        "plant": payload.respondent.plant,
        "customer": payload.respondent.customer,
        "line_user_id": payload.respondent.line_user_id,

        "attending": payload.meta.attending,
        "path": payload.meta.path,

        "answers": answers_map,
        "answers_detail": [item.model_dump() for item in payload.answers],
        "answered_count": answered_count,

        "started_at": payload.meta.started_at,
        "submitted_at": submitted_at,
        "duration_seconds": payload.meta.duration_seconds,
        "client": payload.meta.client,

        "updated_at": now,
    }

    try:
        result = _collection().update_one(
            {"survey_id": payload.survey.id, "drivercode": drivercode},
            {"$set": document, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail=f"บันทึกลง MongoDB ไม่สำเร็จ: {exc}") from exc

    return NewYearSurveySaveResult(
        created=result.upserted_id is not None,
        survey_id=payload.survey.id,
        drivercode=drivercode,
        driver_name=payload.respondent.employee_name,
        answered_count=answered_count,
        submitted_at=submitted_at,
    )


@router.get("/driver/{drivercode}", response_model=NewYearSurveyDoc)
def get_newyear_survey_by_driver(
    drivercode: str = Path(..., description="รหัสพนักงาน (drivercode)"),
    survey_id: str = Query("newyear-2570-mixer", description="รหัสชุดแบบสำรวจ"),
):
    """ดึงคำตอบของ พจส. คนหนึ่ง — ใช้เช็กว่าตอบไปแล้วหรือยัง (404 = ยังไม่ตอบ)"""
    try:
        doc = _collection().find_one(
            {"survey_id": survey_id, "drivercode": drivercode.strip()},
            projection={"_id": 0},
        )
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail=f"อ่าน MongoDB ไม่สำเร็จ: {exc}") from exc

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"ยังไม่พบคำตอบของรหัสพนักงาน '{drivercode}'",
        )

    return doc


@router.get("/", response_model=List[NewYearSurveyDoc])
def list_newyear_surveys(
    survey_id: str = Query("newyear-2570-mixer", description="รหัสชุดแบบสำรวจ"),
    attending: Optional[bool] = Query(None, description="กรองเฉพาะคนที่มา / ไม่มาร่วมงาน"),
    limit: int = Query(100, ge=1, le=1000),
    skip: int = Query(0, ge=0),
):
    """รายการคำตอบทั้งหมด (ใหม่ก่อน) สำหรับ HR เอาไปสรุปผล"""
    query: Dict[str, Any] = {"survey_id": survey_id}
    if attending is not None:
        query["attending"] = attending

    try:
        cursor = (
            _collection()
            .find(query, projection={"_id": 0})
            .sort("submitted_at", -1)
            .skip(skip)
            .limit(limit)
        )
        return list(cursor)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail=f"อ่าน MongoDB ไม่สำเร็จ: {exc}") from exc


@router.get("/count")
def count_newyear_surveys(
    survey_id: str = Query("newyear-2570-mixer", description="รหัสชุดแบบสำรวจ"),
):
    """จำนวนคนที่ตอบแล้ว แยกมา / ไม่มาร่วมงาน"""
    try:
        col = _collection()
        total = col.count_documents({"survey_id": survey_id})
        attending = col.count_documents({"survey_id": survey_id, "attending": True})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail=f"อ่าน MongoDB ไม่สำเร็จ: {exc}") from exc

    return {
        "survey_id": survey_id,
        "total": total,
        "attending": attending,
        "not_attending": total - attending,
    }
