"""
แบบฟอร์มแคมเปญ — สร้าง/อนุมัติใน mena-next-lb (/campaigns แท็บ "ฟอร์ม") แล้วให้คนขับตอบผ่าน Mena-go

  DB          : campaign_forms
  Collections : forms_questions (ฟอร์ม + คำถาม) · forms_answer (คำตอบ) · forms_log (การกระทำฝั่งผู้ดูแล)

spec: mena-next-lb/docs/campaign-forms-backend.md

prefix เป็น /campaign-forms ไม่ใช่ /forms ตาม spec เพราะ /forms/{form_code} ถูก
routes/forms/* (ฟอร์ม approval workflow บน PostgreSQL) ใช้อยู่แล้ว

ชื่อผู้กระทำ (created_by / approved_by / forms_log.name) รับทาง query `actor` เพราะ
ncacdb ยังไม่มี auth ต่อ request — Next.js proxy เป็นคนส่งชื่อจาก session มา
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Path, Query
from pymongo import DESCENDING
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError, PyMongoError

from schemas.campaign_forms import (
    AnswerOut,
    AnswerSubmit,
    AnswerSubmitResult,
    ApprovePayload,
    CampaignFormCreate,
    CampaignFormOut,
    CampaignFormUpdate,
    PublicFormOut,
)
from services.mongo_service import get_mongo_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaign-forms", tags=["Campaign Forms"])
public_router = APIRouter(prefix="/public/campaign-forms", tags=["Campaign Forms - Mena-go"])

DB_NAME = "campaign_forms"
FORMS = "forms_questions"
ANSWERS = "forms_answer"
LOGS = "forms_log"

FORM_ID_PREFIX = "FRM-GO-"
CREATE_RETRIES = 5
POINT_MIN, POINT_MAX = 1, 5
TEXT_MAX = 5000

_DATE_FMT = "%Y-%m-%d"
_DATETIME_FMT = "%Y-%m-%dT%H:%M"

_indexes_ready = False

ActorQuery = Query(..., min_length=1, max_length=200, description="ชื่อผู้ใช้จาก session (fallback เป็น email)")


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

def ensure_indexes() -> None:
    db = get_mongo_db(DB_NAME)
    db[FORMS].create_index([("form_id", 1)], unique=True, name="uniq_form_id")
    db[ANSWERS].create_index([("form_id", 1), ("submitted_at", DESCENDING)], name="form_submitted_desc")
    db[LOGS].create_index([("form_id", 1), ("timestamp", DESCENDING)], name="form_timestamp_desc")


def _col(name: str) -> Collection:
    """
    สร้าง index แบบ lazy ครั้งเดียวต่อ process — ไม่ทำตอน import เพราะถ้า Mongo
    ล่มตอนบูต route อื่นที่ไม่ได้ใช้ Mongo จะพังตามไปด้วย
    """
    global _indexes_ready

    try:
        db = get_mongo_db(DB_NAME)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not _indexes_ready:
        try:
            ensure_indexes()
            _indexes_ready = True
        except PyMongoError:
            # สร้าง index ไม่ได้ไม่ควรทำให้ใช้งานไม่ได้ — ลองใหม่รอบหน้า
            logger.warning("campaign_forms: create_index failed, will retry", exc_info=True)

    return db[name]


def _mongo_error(exc: PyMongoError) -> HTTPException:
    return HTTPException(status_code=503, detail=f"MongoDB ใช้งานไม่ได้: {exc}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_form_id(form_id: str) -> str:
    return form_id.strip().upper()


def _write_log(form_id: str, action: str, name: str) -> None:
    """log เป็นแค่ประวัติ — เขียนไม่สำเร็จไม่ควรทำให้งานหลักที่สำเร็จไปแล้วกลายเป็น error"""
    try:
        _col(LOGS).insert_one({"form_id": form_id, "action": action, "name": name.strip(), "timestamp": _now()})
    except PyMongoError:
        logger.warning("campaign_forms: write log failed (%s %s)", form_id, action, exc_info=True)


# ---------------------------------------------------------------------------
# Forms helpers
# ---------------------------------------------------------------------------

def _get_form(form_id: str, *, public: bool = False) -> Dict[str, Any]:
    query: Dict[str, Any] = {"form_id": form_id, "deleted_at": None}
    if public:
        query["approved_by"] = {"$ne": None}

    try:
        doc = _col(FORMS).find_one(query)
    except PyMongoError as exc:
        raise _mongo_error(exc) from exc

    if not doc:
        raise HTTPException(status_code=404, detail=f"ไม่พบฟอร์ม '{form_id}'")
    return doc


def _count_answers(form_ids: List[str]) -> Dict[str, int]:
    if not form_ids:
        return {}
    pipeline = [
        {"$match": {"form_id": {"$in": form_ids}}},
        {"$group": {"_id": "$form_id", "n": {"$sum": 1}}},
    ]
    try:
        return {row["_id"]: row["n"] for row in _col(ANSWERS).aggregate(pipeline)}
    except PyMongoError as exc:
        raise _mongo_error(exc) from exc


def _form_out(doc: Dict[str, Any], response_count: int) -> Dict[str, Any]:
    out = {k: v for k, v in doc.items() if k not in ("_id", "deleted_at")}
    out["id"] = doc["form_id"]
    out["response_count"] = response_count
    return out


def _next_form_id(col: Collection) -> str:
    """เลขรันต่อจากเลขสูงสุด (รวมฟอร์มที่ลบแล้ว ไม่ให้ไอดีเก่าถูกใช้ซ้ำ) — เทียบเป็นตัวเลข เพราะ '1000' < '999' ถ้าเทียบเป็นข้อความ"""
    pipeline = [
        {"$match": {"form_id": {"$regex": r"^FRM-GO-\d+$"}}},
        {"$project": {"n": {"$toInt": {"$substrCP": ["$form_id", len(FORM_ID_PREFIX), 20]}}}},
        {"$sort": {"n": -1}},
        {"$limit": 1},
    ]
    top = next(col.aggregate(pipeline), None)
    n = (top["n"] if top else 0) + 1
    return f"{FORM_ID_PREFIX}{n:03d}"


def _breaking_changes(old_questions: List[Dict[str, Any]], new_questions: List[Dict[str, Any]]) -> List[str]:
    """
    ฟอร์มที่มีคำตอบแล้ว: สลับลำดับ แก้ข้อความ/คำอธิบาย/บังคับตอบ เพิ่มคำถามหรือเพิ่มตัวเลือกได้
    แต่ห้ามลบคำถาม เปลี่ยนประเภท หรือลบ/เปลี่ยนชื่อตัวเลือก เพราะคำตอบเก่าจะไม่ตรงกับคำถาม
    """
    new_by_id = {q["id"]: q for q in new_questions}
    problems: List[str] = []

    for old in old_questions:
        new = new_by_id.get(old["id"])
        label = old.get("label") or old["id"]
        if new is None:
            problems.append(f"ลบคำถาม '{label}' ไม่ได้")
            continue
        if new["type"] != old["type"]:
            problems.append(f"เปลี่ยนประเภทคำถาม '{label}' ไม่ได้")
            continue
        missing = [o for o in old.get("options", []) if o not in new["options"]]
        if missing:
            problems.append(f"ลบ/เปลี่ยนชื่อตัวเลือก {', '.join(missing)} ของคำถาม '{label}' ไม่ได้")

    return problems


# ---------------------------------------------------------------------------
# Answer validation
# ---------------------------------------------------------------------------

def _is_empty(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, list):
        return all(v is None or v == "" for v in value)
    return False


def _valid_datetime(value: Any, fmt: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.strptime(value, fmt).strftime(fmt) == value
    except ValueError:
        return False


def _check_answer(q: Dict[str, Any], value: Any) -> Tuple[Any, Optional[str]]:
    """คืน (ค่าที่เก็บ, ข้อความ error) ตามตารางประเภทคำถามใน spec"""
    qtype = q["type"]
    options = q.get("options") or []

    if qtype in ("short", "paragraph"):
        if not isinstance(value, str):
            return None, "ต้องเป็นข้อความ"
        if len(value) > TEXT_MAX:
            return None, f"ยาวเกิน {TEXT_MAX} ตัวอักษร"
        return value.strip(), None

    if qtype == "single":
        if not isinstance(value, str) or value not in options:
            return None, "ต้องเลือกจากตัวเลือกที่มี"
        return value, None

    if qtype == "multiple":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return None, "ต้องเป็นรายการตัวเลือก"
        picked = [v for v in value if v]
        if any(v not in options for v in picked):
            return None, "มีตัวเลือกที่ไม่อยู่ในฟอร์ม"
        if len(set(picked)) != len(picked):
            return None, "เลือกตัวเลือกซ้ำ"
        return picked, None

    if qtype == "date":
        if not _valid_datetime(value, _DATE_FMT):
            return None, "ต้องเป็นวันที่รูปแบบ YYYY-MM-DD"
        return value, None

    if qtype == "datetime":
        if not _valid_datetime(value, _DATETIME_FMT):
            return None, "ต้องเป็นวันเวลารูปแบบ YYYY-MM-DDTHH:mm"
        return value, None

    if qtype == "point":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (isinstance(value, float) and not value.is_integer())
        ):
            return None, "ต้องเป็นตัวเลขจำนวนเต็ม"
        if not POINT_MIN <= value <= POINT_MAX:
            return None, f"ต้องอยู่ระหว่าง {POINT_MIN}–{POINT_MAX}"
        return int(value), None

    if qtype == "ranking":
        # index 0 = อันดับ 1 · ช่องที่ยังไม่เลือกเป็น "" (ฝั่ง frontend เก็บแบบนี้) ตำแหน่งจึงมีความหมาย
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return None, "ต้องเป็นรายการเรียงตามอันดับ"
        if len(value) > len(options):
            return None, "จำนวนอันดับเกินจำนวนตัวเลือก"
        ranked = [v for v in value if v]
        if any(v not in options for v in ranked):
            return None, "มีตัวเลือกที่ไม่อยู่ในฟอร์ม"
        if len(set(ranked)) != len(ranked):
            return None, "จัดอันดับตัวเลือกซ้ำ"
        if q.get("required") and len(ranked) != len(options):
            return None, "ต้องจัดอันดับให้ครบทุกตัวเลือก"
        return value, None

    return None, f"ไม่รู้จักประเภทคำถาม '{qtype}'"


def _validate_answers(questions: List[Dict[str, Any]], answers: Dict[str, Any]) -> Dict[str, Any]:
    by_id = {q["id"]: q for q in questions}
    errors: List[Dict[str, str]] = [
        {"question_id": qid, "message": "ไม่มีคำถามนี้ในฟอร์ม"} for qid in answers if qid not in by_id
    ]
    clean: Dict[str, Any] = {}

    for q in questions:
        value = answers.get(q["id"])
        if _is_empty(value):
            if q.get("required"):
                errors.append({"question_id": q["id"], "message": f"'{q['label']}' ต้องตอบ"})
            continue

        stored, error = _check_answer(q, value)
        if error:
            errors.append({"question_id": q["id"], "message": f"'{q['label']}' {error}"})
        elif not _is_empty(stored):
            clean[q["id"]] = stored
        elif q.get("required"):
            errors.append({"question_id": q["id"], "message": f"'{q['label']}' ต้องตอบ"})

    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return clean


def _answer_out(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["id"] = str(doc["_id"])
    return out


# ---------------------------------------------------------------------------
# Endpoints — ฝั่งผู้สร้าง/ผู้ดูแล (mena-next-lb)
# ---------------------------------------------------------------------------

@router.get("", response_model=List[CampaignFormOut])
def list_forms(search: Optional[str] = Query(None, max_length=200)):
    """รายการฟอร์ม (ไม่รวมที่ลบแล้ว) ใหม่ก่อน พร้อมจำนวนคำตอบ"""
    query: Dict[str, Any] = {"deleted_at": None}
    term = (search or "").strip()
    if term:
        pattern = {"$regex": re.escape(term), "$options": "i"}
        query["$or"] = [
            {"form_id": pattern},
            {"name": pattern},
            {"description": pattern},
            {"created_by": pattern},
            {"approved_by": pattern},
        ]

    try:
        docs = list(_col(FORMS).find(query).sort("created_at", DESCENDING))
    except PyMongoError as exc:
        raise _mongo_error(exc) from exc

    counts = _count_answers([d["form_id"] for d in docs])
    return [_form_out(d, counts.get(d["form_id"], 0)) for d in docs]


@router.post("", response_model=CampaignFormOut, status_code=201)
def create_form(payload: CampaignFormCreate, actor: str = ActorQuery):
    """สร้างฟอร์ม — backend ออก form_id ให้ (FRM-GO-001, 002, ...) สถานะเริ่มต้นคือรออนุมัติ"""
    col = _col(FORMS)
    now = _now()
    doc: Dict[str, Any] = {
        "name": payload.name,
        "description": payload.description,
        "questions": [q.model_dump() for q in payload.questions],
        "created_by": actor.strip(),
        "created_at": now,
        "updated_at": now,
        "approved_by": None,
        "approved_at": None,
        "deleted_at": None,
    }

    # สร้างพร้อมกันอาจได้เลขเดียวกัน — unique index กันไว้ แล้วคำนวณเลขใหม่
    for _ in range(CREATE_RETRIES):
        try:
            doc["form_id"] = _next_form_id(col)
            col.insert_one(doc)
            break
        except DuplicateKeyError:
            doc.pop("_id", None)
            continue
        except PyMongoError as exc:
            raise _mongo_error(exc) from exc
    else:
        raise HTTPException(status_code=409, detail="ออกรหัสฟอร์มไม่สำเร็จ ลองใหม่อีกครั้ง")

    _write_log(doc["form_id"], "create", actor)
    return _form_out(doc, 0)


@router.get("/{form_id}", response_model=CampaignFormOut)
def get_form(form_id: str = Path(..., description="เช่น FRM-GO-001")):
    form_id = _normalize_form_id(form_id)
    doc = _get_form(form_id)
    return _form_out(doc, _count_answers([form_id]).get(form_id, 0))


@router.patch("/{form_id}", response_model=CampaignFormOut)
def update_form(payload: CampaignFormUpdate, form_id: str = Path(...), actor: str = ActorQuery):
    """แก้ name / description / questions — ถ้ามีคำตอบแล้วจะห้ามแก้แบบที่ทำให้คำตอบเก่าไม่ตรงกับคำถาม"""
    form_id = _normalize_form_id(form_id)
    doc = _get_form(form_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="ไม่มีข้อมูลที่ต้องแก้")

    response_count = _count_answers([form_id]).get(form_id, 0)
    if "questions" in changes and response_count:
        problems = _breaking_changes(doc["questions"], changes["questions"])
        if problems:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"ฟอร์มนี้มีคำตอบแล้ว {response_count} รายการ — ส่งออก CSV แล้วล้างคำตอบก่อน ถ้าต้องการแก้ส่วนนี้",
                    "problems": problems,
                },
            )

    changes["updated_at"] = _now()
    try:
        _col(FORMS).update_one({"_id": doc["_id"]}, {"$set": changes})
    except PyMongoError as exc:
        raise _mongo_error(exc) from exc

    _write_log(form_id, "update", actor)
    return _form_out({**doc, **changes}, response_count)


@router.patch("/{form_id}/approve", response_model=CampaignFormOut)
def approve_form(payload: ApprovePayload, form_id: str = Path(...), actor: str = ActorQuery):
    """เปิด/ปิดอนุมัติ — ยกเลิกอนุมัติแล้ว Mena-go จะเปิดฟอร์มนี้ไม่ได้"""
    form_id = _normalize_form_id(form_id)
    doc = _get_form(form_id)
    response_count = _count_answers([form_id]).get(form_id, 0)

    if payload.approved == (doc.get("approved_by") is not None):
        return _form_out(doc, response_count)  # สถานะเดิมอยู่แล้ว ไม่ต้องเขียนซ้ำ

    changes: Dict[str, Any] = (
        {"approved_by": actor.strip(), "approved_at": _now()}
        if payload.approved
        else {"approved_by": None, "approved_at": None}
    )
    try:
        _col(FORMS).update_one({"_id": doc["_id"]}, {"$set": changes})
    except PyMongoError as exc:
        raise _mongo_error(exc) from exc

    _write_log(form_id, "approve" if payload.approved else "unapprove", actor)
    return _form_out({**doc, **changes}, response_count)


@router.delete("/{form_id}")
def delete_form(form_id: str = Path(...), actor: str = ActorQuery):
    """soft delete — เอกสารยังอยู่ (deleted_at) แต่หายจากรายการและ Mena-go เปิดไม่ได้"""
    form_id = _normalize_form_id(form_id)
    doc = _get_form(form_id)
    deleted_at = _now()
    try:
        _col(FORMS).update_one({"_id": doc["_id"]}, {"$set": {"deleted_at": deleted_at}})
    except PyMongoError as exc:
        raise _mongo_error(exc) from exc

    _write_log(form_id, "delete", actor)
    return {"form_id": form_id, "deleted_at": deleted_at}


@router.get("/{form_id}/answers", response_model=List[AnswerOut])
def list_answers(form_id: str = Path(...)):
    """คำตอบทั้งหมดของฟอร์ม ล่าสุดก่อน"""
    form_id = _normalize_form_id(form_id)
    _get_form(form_id)
    try:
        docs = _col(ANSWERS).find({"form_id": form_id}).sort("submitted_at", DESCENDING)
        return [_answer_out(d) for d in docs]
    except PyMongoError as exc:
        raise _mongo_error(exc) from exc


@router.delete("/{form_id}/answers")
def clear_answers(form_id: str = Path(...), actor: str = ActorQuery):
    """ล้างคำตอบทั้งหมด — ลบจริง กู้คืนไม่ได้ ฟอร์มยังเก็บคำตอบใหม่ต่อได้"""
    form_id = _normalize_form_id(form_id)
    _get_form(form_id)
    try:
        result = _col(ANSWERS).delete_many({"form_id": form_id})
    except PyMongoError as exc:
        raise _mongo_error(exc) from exc

    _write_log(form_id, "clear_answers", actor)
    return {"form_id": form_id, "deleted_count": result.deleted_count}


@router.post("/{form_id}/export-log")
def log_export(form_id: str = Path(...), actor: str = ActorQuery):
    """frontend สร้าง CSV เอง แล้วเรียกเพื่อบันทึกว่าใครส่งออกเมื่อไร"""
    form_id = _normalize_form_id(form_id)
    _get_form(form_id)
    _write_log(form_id, "export_csv", actor)
    return {"form_id": form_id, "logged": True}


# ---------------------------------------------------------------------------
# Endpoints — Mena-go (คนขับ)
# ---------------------------------------------------------------------------

@public_router.get("/{form_id}", response_model=PublicFormOut)
def get_public_form(form_id: str = Path(..., description="ไอดีที่ตรวจจับได้จาก regex FRM-GO-\\d{3,}")):
    """เฉพาะฟอร์มที่อนุมัติแล้วและยังไม่ถูกลบ — นอกนั้นตอบ 404"""
    form_id = _normalize_form_id(form_id)
    doc = _get_form(form_id, public=True)
    return {
        "id": form_id,
        "form_id": form_id,
        "name": doc["name"],
        "description": doc.get("description", ""),
        "questions": doc["questions"],
    }


@public_router.post("/{form_id}/answers", response_model=AnswerSubmitResult, status_code=201)
def submit_answers(payload: AnswerSubmit, form_id: str = Path(...)):
    """ส่งคำตอบ — ตรวจตามประเภทคำถามและข้อบังคับตอบ backend ใส่ submitted_at เอง"""
    form_id = _normalize_form_id(form_id)
    doc = _get_form(form_id, public=True)
    answers = _validate_answers(doc["questions"], payload.answers)

    submitted_at = _now()
    answer_doc = {
        "_id": ObjectId(),
        "form_id": form_id,
        "respondent": payload.respondent,
        "drivercode": payload.drivercode,
        "line_user_id": payload.line_user_id,
        "campaign_id": payload.campaign_id,
        "submitted_at": submitted_at,
        "answers": answers,
    }
    try:
        _col(ANSWERS).insert_one(answer_doc)
    except PyMongoError as exc:
        raise _mongo_error(exc) from exc

    return {"id": str(answer_doc["_id"]), "form_id": form_id, "submitted_at": submitted_at}
