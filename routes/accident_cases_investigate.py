"""
AC Investigation Report (Part 2)

  POST   /accident-case-investigate/{document_no_ac}   create or update (upsert)
  GET    /accident-case-investigate/                   list (summary)
  GET    /accident-case-investigate/{document_no_ac}   read one (full)
  PUT    /accident-case-investigate/{document_no_ac}   update only
  DELETE /accident-case-investigate/{document_no_ac}   delete

การจัดการตารางลูก (why / root cause / measure / investigator):
เป็นแบบ "replace ทั้งชุด" ตาม state ของฟอร์ม โดยใช้ cascade delete-orphan
ของ SQLAlchemy ทำให้ทั้งหมดจบใน transaction เดียว
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload

import models
from database import get_db
from schemas.accident_case_investigate_schema import (
    AccidentCaseInvestigateCreate,
    AccidentCaseInvestigateOut,
    AccidentCaseInvestigateSummary,
    AccidentCaseInvestigateUpdate,
)

router = APIRouter(
    prefix="/accident-case-investigate",
    tags=["Accident Case Investigate"],
)

COMPLETED_STATUS = "Completed Investigate"

PARENT_FIELDS = {
    "accident_types",
    "severity_level",
    "accident_description",
    "risk_assessment_completed",
    "risk_assessment_reviewed",
    "risk_assessment_result",
    "risk_assessment_date",
    "risk_assessment_team",
    "risk_assessment_attached",
    "avoidability",
}
CHILD_FIELDS = {"why_analysis", "root_causes", "measures", "investigators"}


# ======================================================
# HELPERS
# ======================================================
def _new_uid() -> str:
    return uuid.uuid4().hex[:12]


def _base_query(db: Session):
    """โหลดตารางลูกด้วย selectinload กัน N+1 query"""
    return db.query(models.AccidentCaseInvestigate).options(
        selectinload(models.AccidentCaseInvestigate.why_analysis),
        selectinload(models.AccidentCaseInvestigate.root_causes),
        selectinload(models.AccidentCaseInvestigate.measures),
        selectinload(models.AccidentCaseInvestigate.investigators),
    )


def _get_record(db: Session, document_no_ac: str):
    return _base_query(db).filter_by(document_no_ac=document_no_ac).first()


def _ensure_accident_case(db: Session, document_no_ac: str):
    case = (
        db.query(models.AccidentCase)
        .filter_by(document_no_ac=document_no_ac)
        .first()
    )
    if not case:
        raise HTTPException(
            status_code=404,
            detail=f"ไม่พบเอกสารอุบัติเหตุเลขที่ {document_no_ac}",
        )
    return case


def _rows(items, model_cls):
    """
    แปลง payload → ORM rows
    - เติม uid ให้แถวที่ FE ไม่ได้ส่ง id มา
    - ตีเลข seq ใหม่ 1..n ตามลำดับใน array เสมอ (ให้ตรงกับที่เห็นบนฟอร์ม)
    """
    return [
        model_cls(
            id=item.id or _new_uid(),
            seq=index,
            **item.model_dump(exclude={"id", "seq"}),
        )
        for index, item in enumerate(items, start=1)
    ]


def _apply_children(record, payload, partial: bool):
    """
    เขียนตารางลูกใหม่ทั้งชุด
    partial=True (PUT) : list ที่เป็น None คือ "ไม่แตะ"
    partial=False (POST): list ที่เป็น None คือ "ล้างให้ว่าง"
    """
    if payload.why_analysis is not None or not partial:
        record.why_analysis = _rows(
            payload.why_analysis or [], models.AccidentCaseInvestigateWhy
        )

    if payload.root_causes is not None or not partial:
        record.root_causes = _rows(
            payload.root_causes or [], models.AccidentCaseInvestigateRootCause
        )

    if payload.investigators is not None or not partial:
        record.investigators = _rows(
            payload.investigators or [],
            models.AccidentCaseInvestigateInvestigator,
        )

    # มาตรการต้องผูกกับสาเหตุที่มีอยู่จริงเสมอ — ที่ผูกกับสาเหตุที่ถูกลบไปแล้ว
    # (ฝั่ง FE เรียกว่า orphan measure) จะไม่มีวันแสดงบนฟอร์ม จึงตัดทิ้งทุกครั้ง
    # รวมถึงตอน PUT ที่แก้เฉพาะ root_causes โดยไม่ได้ส่ง measures มาด้วย
    valid_ids = {rc.id for rc in record.root_causes if rc.id}

    if payload.measures is not None or not partial:
        kept = [
            m
            for m in (payload.measures or [])
            if m.root_cause_id and m.root_cause_id in valid_ids
        ]
        record.measures = _rows(kept, models.AccidentCaseInvestigateMeasure)
    else:
        kept = [m for m in record.measures if m.root_cause_id in valid_ids]
        if len(kept) != len(record.measures):
            for index, measure in enumerate(kept, start=1):
                measure.seq = index
            record.measures = kept


def _sync_case_status(db: Session, record) -> None:
    """สอบสวนครบ → ดัน casestatus ของเคสเป็น Completed Investigate"""
    if not record.is_complete:
        return
    case = (
        db.query(models.AccidentCase)
        .filter_by(document_no_ac=record.document_no_ac)
        .first()
    )
    if case and case.casestatus != COMPLETED_STATUS:
        case.casestatus = COMPLETED_STATUS
        db.commit()


# ======================================================
# CREATE OR UPDATE (UPSERT)
# ======================================================
@router.post("/{document_no_ac}", response_model=AccidentCaseInvestigateOut)
def create_or_update_investigation(
    document_no_ac: str,
    payload: AccidentCaseInvestigateCreate,
    db: Session = Depends(get_db),
):
    _ensure_accident_case(db, document_no_ac)

    record = _get_record(db, document_no_ac)
    if record is None:
        record = models.AccidentCaseInvestigate(document_no_ac=document_no_ac)
        db.add(record)

    for key, value in payload.model_dump(include=PARENT_FIELDS).items():
        setattr(record, key, value)

    _apply_children(record, payload, partial=False)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"บันทึกผลการสอบสวนไม่สำเร็จ: {exc}"
        ) from exc

    db.refresh(record)
    _sync_case_status(db, record)
    return record


# ======================================================
# LIST (SUMMARY)
# ======================================================
@router.get("/", response_model=list[AccidentCaseInvestigateSummary])
def list_investigations(
    document_no_ac: str | None = Query(None, description="ค้นหาบางส่วนของเลขที่เอกสาร"),
    severity_level: list[str] | None = Query(None),
    avoidability: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = _base_query(db)

    if document_no_ac:
        query = query.filter(
            models.AccidentCaseInvestigate.document_no_ac.ilike(f"%{document_no_ac}%")
        )
    if severity_level:
        query = query.filter(
            models.AccidentCaseInvestigate.severity_level.in_(severity_level)
        )
    if avoidability:
        query = query.filter(
            models.AccidentCaseInvestigate.avoidability == avoidability
        )

    records = (
        query.order_by(models.AccidentCaseInvestigate.investigate_ac_id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return [
        AccidentCaseInvestigateSummary(
            investigate_id=r.investigate_ac_id,
            document_no_ac=r.document_no_ac,
            severity_level=r.severity_level,
            avoidability=r.avoidability,
            accident_types=r.accident_types or [],
            root_cause_count=len(r.root_causes),
            measure_count=len(r.measures),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in records
    ]


# ======================================================
# READ ONE
# ======================================================
@router.get("/{document_no_ac}", response_model=AccidentCaseInvestigateOut)
def get_investigation(document_no_ac: str, db: Session = Depends(get_db)):
    record = _get_record(db, document_no_ac)
    if not record:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลการสอบสวนของเอกสารนี้")
    return record


# ======================================================
# UPDATE ONLY
# ======================================================
@router.put("/{document_no_ac}", response_model=AccidentCaseInvestigateOut)
def update_investigation(
    document_no_ac: str,
    payload: AccidentCaseInvestigateUpdate,
    db: Session = Depends(get_db),
):
    record = _get_record(db, document_no_ac)
    if not record:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลการสอบสวนของเอกสารนี้")

    for key, value in payload.model_dump(
        include=PARENT_FIELDS, exclude_unset=True
    ).items():
        setattr(record, key, value)

    _apply_children(record, payload, partial=True)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"อัปเดตผลการสอบสวนไม่สำเร็จ: {exc}"
        ) from exc

    db.refresh(record)
    _sync_case_status(db, record)
    return record


# ======================================================
# DELETE
# ======================================================
@router.delete("/{document_no_ac}", status_code=status.HTTP_204_NO_CONTENT)
def delete_investigation(document_no_ac: str, db: Session = Depends(get_db)):
    record = _get_record(db, document_no_ac)
    if not record:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลการสอบสวนของเอกสารนี้")

    db.delete(record)  # ตารางลูกถูกลบตาม cascade
    db.commit()
    return None
