from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, aliased
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from database import get_db
import models, schemas
from schemas.accident_schema import AccidentCaseDocData

router = APIRouter(prefix="/accident-cases", tags=["Accident Cases"])

# -----------------------------------------------------
# SITE CODE MAPPING
# -----------------------------------------------------
SITE_CODES = {
    1: "SB",
    2: "LB",
    3: "SB",
    4: "SB",
    5: "LB",
    6: "BP",
}

# -----------------------------------------------------
# DOCUMENT NUMBER GENERATOR
# -----------------------------------------------------
def generate_document_no_ac(db: Session, site_id: int) -> str:
    site_code = SITE_CODES.get(site_id, "XX")
    now = datetime.now(timezone.utc)
    yymm = now.strftime("%y%m")
    prefix = f"AC-{site_code}-{yymm}-"

    last = (
        db.query(models.AccidentCase.document_no_ac)
        .filter(models.AccidentCase.document_no_ac.like(f"{prefix}%"))
        .order_by(models.AccidentCase.document_no_ac.desc())
        .first()
    )

    last_num = int(last[0].split("-")[-1]) if last and last[0] else 0
    return f"{prefix}{last_num + 1:03d}"

# -----------------------------------------------------
# PRIORITY CALCULATION
# -----------------------------------------------------
def calculate_priority(
    estimated_goods_damage_value: Optional[float],
    estimated_vehicle_damage_value: Optional[float],
    actual_goods_damage_value: Optional[float],
    actual_vehicle_damage_value: Optional[float],
    alcohol_test_result: Optional[float],
    drug_test_result: Optional[str],
    injured_not_hospitalized: Optional[int],
    injured_hospitalized: Optional[int],
    fatalities: Optional[int],
) -> str:
    estimate = (estimated_goods_damage_value or 0) + (estimated_vehicle_damage_value or 0)
    actual = (actual_goods_damage_value or 0) + (actual_vehicle_damage_value or 0)
    total_damage = actual if actual else estimate

    alcohol_test_result = alcohol_test_result or 0
    drug_test_result = (drug_test_result or "").strip().lower()
    injured_not_hospitalized = injured_not_hospitalized or 0
    injured_hospitalized = injured_hospitalized or 0
    fatalities = fatalities or 0

    safe_drug_values = {"", "none", "negative", "ไม่ใส่ชนิดสารเสพติด"}

    # --- Priority Logic ---
    if (
        total_damage > 500000
        or alcohol_test_result > 0
        or (drug_test_result and drug_test_result not in safe_drug_values)
        or fatalities >= 1
    ):
        return "Crisis"
    elif 5001 <= total_damage <= 500000 and fatalities == 0 or (injured_hospitalized >= 1):
        return "Major"
    elif (total_damage <= 5000) or (injured_not_hospitalized >= 1 and fatalities == 0):
        return "Minor"
    else:
        return "Minor"

# -----------------------------------------------------
# DAMAGE ITEMS (replace ทั้งชุดตาม state ของฟอร์ม)
# -----------------------------------------------------
def replace_damage_items(db: Session, document_no_ac: str, items) -> None:
    """
    เขียนรายการความเสียหายใหม่ทั้งชุด
    - ตีเลข seq 1..n ตามลำดับใน array (ให้ตรงกับที่เห็นบนฟอร์ม)
    - แถวว่างล้วน (ไม่มีรายละเอียดและยอดเป็น 0) ไม่ต้องเก็บ
    """
    db.query(models.AccidentCaseDamageItem).filter(
        models.AccidentCaseDamageItem.document_no_ac == document_no_ac
    ).delete(synchronize_session=False)

    seq = 0
    for item in items or []:
        data = item if isinstance(item, dict) else item.dict()
        detail = (data.get("damage_detail") or "").strip()
        value = data.get("damage_value") or 0
        party = (data.get("responsible_party") or "").strip()
        if not detail and not party and not value:
            continue

        seq += 1
        db.add(
            models.AccidentCaseDamageItem(
                document_no_ac=document_no_ac,
                seq=seq,
                damage_category="goods"
                if data.get("damage_category") == "goods"
                else "vehicle",
                damage_detail=detail or None,
                damage_value=value,
                responsible_party=party or None,
            )
        )


# -----------------------------------------------------
# CREATE CASE
# -----------------------------------------------------
@router.post("", response_model=schemas.AccidentCaseResponse, status_code=201)
def create_case(payload: dict, db: Session = Depends(get_db)):
    case_data = schemas.AccidentCaseCreate(**payload)
    doc_no = generate_document_no_ac(db, case_data.site_id)

    priority = calculate_priority(
        case_data.estimated_goods_damage_value,
        case_data.estimated_vehicle_damage_value,
        case_data.actual_goods_damage_value,
        case_data.actual_vehicle_damage_value,
        case_data.alcohol_test_result,
        case_data.drug_test_result,
        case_data.injured_not_hospitalized,
        case_data.injured_hospitalized,
        case_data.fatalities,
    )

    case = models.AccidentCase(
        **case_data.dict(
            exclude={
                "priority",
                "document_no_ac",
                "casestatus",
                "attachments",
                "docs",
                "damage_items",
            }
        ),
        document_no_ac=doc_no,
        priority=priority,
        casestatus="Pending",
        attachments=f"https://mena-safety-ncac.vercel.app/nc-form?doc={doc_no}",
    )

    db.add(case)
    db.commit()
    db.refresh(case)

    # Create documents if any
    for doc in case_data.docs or []:
        db.add(models.AccidentCaseDoc(document_no_ac=doc_no, data=doc))

    replace_damage_items(db, doc_no, case_data.damage_items)
    db.commit()
    db.refresh(case)

    return case.to_dict()

# -----------------------------------------------------
# DATETIME PARSER
# -----------------------------------------------------
def parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None

# -----------------------------------------------------
# LIST CASES (FILTERABLE)
# -----------------------------------------------------
@router.get("", response_model=schemas.PaginatedAccidentCaseResponse)
def get_accident_cases(
    db: Session = Depends(get_db),
    document_no_ac: Optional[List[str]] = Query(None),
    site_id: Optional[List[int]] = Query(None),
    client_id: Optional[List[int]] = Query(None),        # ✅ NEW
    department_id: Optional[List[int]] = Query(None),    # ✅ NEW
    priority: Optional[List[str]] = Query(None),
    driver_id: Optional[List[str]] = Query(None),
    casestatus: Optional[List[str]] = Query(None),
    breakdown_status: Optional[List[str]] = Query(None),
    reporter_id: Optional[List[int]] = Query(None),
    vehicle_plate: Optional[str] = Query(None),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=5000),
    sort_by: str = Query("record_datetime"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
):
    query = db.query(models.AccidentCase).options(
        joinedload(models.AccidentCase.site),
        joinedload(models.AccidentCase.department),
        joinedload(models.AccidentCase.client),
        joinedload(models.AccidentCase.origin),
        joinedload(models.AccidentCase.reporter),
        joinedload(models.AccidentCase.driver),
        joinedload(models.AccidentCase.driver_role),
        joinedload(models.AccidentCase.vehicle_head),
        joinedload(models.AccidentCase.vehicle_tail),
        joinedload(models.AccidentCase.province),
        joinedload(models.AccidentCase.district),
        joinedload(models.AccidentCase.sub_district),
        joinedload(models.AccidentCase.docs),
        joinedload(models.AccidentCase.damage_items),
    )

    if document_no_ac:
        # Partial match (not exact) so a user can search without typing the full
        # document number.
        query = query.filter(
            or_(*[models.AccidentCase.document_no_ac.ilike(f"%{d}%") for d in document_no_ac])
        )

    if site_id:
        query = query.filter(models.AccidentCase.site_id.in_(site_id))

    # ✅ NEW FILTERS
    if client_id:
        query = query.filter(models.AccidentCase.client_id.in_(client_id))

    if department_id:
        query = query.filter(models.AccidentCase.department_id.in_(department_id))

    if priority:
        query = query.filter(models.AccidentCase.priority.in_(priority))

    if driver_id:
        query = query.filter(models.AccidentCase.driver_id.in_(driver_id))

    if casestatus:
        query = query.filter(models.AccidentCase.casestatus.in_(casestatus))

    if breakdown_status:
        query = query.filter(models.AccidentCase.breakdown_status.in_(breakdown_status))

    if reporter_id:
        query = query.filter(models.AccidentCase.reporter_id.in_(reporter_id))

    if vehicle_plate:
        VehicleHeadAlias = aliased(models.Vehicle)
        VehicleTailAlias = aliased(models.Vehicle)
        query = query.outerjoin(VehicleHeadAlias, models.AccidentCase.vehicle_id_head == VehicleHeadAlias.vehicle_id)
        query = query.outerjoin(VehicleTailAlias, models.AccidentCase.vehicle_id_tail == VehicleTailAlias.vehicle_id)
        like_pattern = f"%{vehicle_plate}%"
        query = query.filter(
            or_(
                models.AccidentCase.vehicle_truckno.ilike(like_pattern),
                VehicleHeadAlias.vehicle_number_plate.ilike(like_pattern),
                VehicleTailAlias.vehicle_number_plate.ilike(like_pattern),
            )
        )

    if start_date and end_date:
        start, end = parse_dt(start_date), parse_dt(end_date)
        if start and end:
            query = query.filter(
                models.AccidentCase.record_datetime >= start,
                models.AccidentCase.record_datetime < end + timedelta(days=1),
            )

    # -----------------------------------------------------
    # TOTAL COUNT (before ordering/pagination)
    # -----------------------------------------------------
    total = query.count()

    # -----------------------------------------------------
    # SORTING (allow-list only — never interpolate raw strings)
    # -----------------------------------------------------
    SORTABLE_COLUMNS = {
        "record_datetime": models.AccidentCase.record_datetime,
        "incident_datetime": models.AccidentCase.incident_datetime,
        "document_no_ac": models.AccidentCase.document_no_ac,
        "casestatus": models.AccidentCase.casestatus,
        "priority": models.AccidentCase.priority,
        "estimated_cost": func.coalesce(models.AccidentCase.estimated_goods_damage_value, 0)
        + func.coalesce(models.AccidentCase.estimated_vehicle_damage_value, 0),
        "actual_price": func.coalesce(models.AccidentCase.actual_goods_damage_value, 0)
        + func.coalesce(models.AccidentCase.actual_vehicle_damage_value, 0),
    }

    JOINED_SORT_FIELDS = {"site_name", "department_name", "client_name", "driver_name"}

    if sort_by in JOINED_SORT_FIELDS:
        if sort_by == "site_name":
            SiteAlias = aliased(models.Site)
            query = query.outerjoin(SiteAlias, models.AccidentCase.site_id == SiteAlias.site_id)
            sort_col = SiteAlias.site_name_th
        elif sort_by == "department_name":
            DeptAlias = aliased(models.Department)
            query = query.outerjoin(DeptAlias, models.AccidentCase.department_id == DeptAlias.department_id)
            sort_col = DeptAlias.department_name_th
        elif sort_by == "client_name":
            ClientAlias = aliased(models.Client)
            query = query.outerjoin(ClientAlias, models.AccidentCase.client_id == ClientAlias.client_id)
            sort_col = ClientAlias.client_name
        elif sort_by == "driver_name":
            DriverAlias = aliased(models.MasterDriver)
            query = query.outerjoin(DriverAlias, models.AccidentCase.driver_id == DriverAlias.driver_id)
            sort_col = func.concat(DriverAlias.first_name, ' ', DriverAlias.last_name)
    else:
        # Falls back silently to the default (record_datetime) if sort_by is unknown
        sort_col = SORTABLE_COLUMNS.get(sort_by, models.AccidentCase.record_datetime)

    order_expr = sort_col.desc() if sort_order == "desc" else sort_col.asc()

    cases = (
        query.order_by(order_expr, models.AccidentCase.accident_case_id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return {
        "items": [case.to_dict() for case in cases],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


# -----------------------------------------------------
# UPDATE CASE
# -----------------------------------------------------
@router.put("/{document_no_ac}", response_model=schemas.AccidentCaseResponse)
def update_case(document_no_ac: str, payload: dict, db: Session = Depends(get_db)):
    """
    Update a case by document_no_ac, safely handling both normal fields and docs.
    """
    case = (
        db.query(models.AccidentCase)
        .filter(models.AccidentCase.document_no_ac == document_no_ac)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{document_no_ac}' not found")

    # ✅ Handle docs separately
    docs_data = payload.get("docs", None)
    if docs_data:
        # Delete existing docs for this case
        db.query(models.AccidentCaseDoc).filter(
            models.AccidentCaseDoc.document_no_ac == document_no_ac
        ).delete(synchronize_session=False)

        # Insert new docs
        for doc in docs_data:
            new_doc = models.AccidentCaseDoc(
                document_no_ac=document_no_ac,
                data=doc
            )
            db.add(new_doc)

    # ✅ Handle damage items separately (replace ทั้งชุด)
    if "damage_items" in payload:
        replace_damage_items(db, document_no_ac, payload.get("damage_items"))

    # ✅ Update other fields
    update_fields = {
        k: v for k, v in payload.items()
        if k not in {"priority", "document_no_ac", "docs", "damage_items"}
        and hasattr(models.AccidentCase, k)
    }

    for key, value in update_fields.items():
        if key.endswith("_id") and value == 0:
            value = None
        setattr(case, key, value)

    db.flush()

    # ✅ Recalculate priority
    case.priority = calculate_priority(
        case.estimated_goods_damage_value,
        case.estimated_vehicle_damage_value,
        case.actual_goods_damage_value,
        case.actual_vehicle_damage_value,
        case.alcohol_test_result,
        case.drug_test_result,
        case.injured_not_hospitalized,
        case.injured_hospitalized,
        case.fatalities,
    )

    try:
        db.commit()
        db.refresh(case)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Database error: {str(e)}")

    return case.to_dict()

# -----------------------------------------------------
# DELETE CASE
# -----------------------------------------------------
@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: int, db: Session = Depends(get_db)):
    case = db.get(models.AccidentCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    db.delete(case)
    db.commit()

# -----------------------------------------------------
# DOCS MANAGEMENT
# -----------------------------------------------------
@router.post("/{document_no_ac}/docs", response_model=schemas.AccidentCaseDocSchema)
def add_accident_case_doc(document_no_ac: str, payload: schemas.AccidentCaseDocData, db: Session = Depends(get_db)):
    case = db.query(models.AccidentCase).filter(models.AccidentCase.document_no_ac == document_no_ac).first()
    if not case:
        raise HTTPException(status_code=404, detail="Accident case not found")

    doc = models.AccidentCaseDoc(document_no_ac=document_no_ac, data=payload.dict())
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc

@router.get("/{document_no_ac}/docs", response_model=List[schemas.AccidentCaseDocSchema])
def get_docs(document_no_ac: str, db: Session = Depends(get_db)):
    return db.query(models.AccidentCaseDoc).filter(models.AccidentCaseDoc.document_no_ac == document_no_ac).all()

@router.delete("/{document_no_ac}/docs/{doc_id}", status_code=204)
def delete_doc(document_no_ac: str, doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(models.AccidentCaseDoc).filter(
        models.AccidentCaseDoc.document_no_ac == document_no_ac,
        models.AccidentCaseDoc.id == doc_id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(doc)
    db.commit()

# -----------------------------------------------------
# GET SINGLE CASE
# -----------------------------------------------------
@router.get("/{document_no_ac}", response_model=schemas.AccidentCaseResponse)
def get_accident_case(document_no_ac: str, db: Session = Depends(get_db)):
    case = (
        db.query(models.AccidentCase)
        .options(
            joinedload(models.AccidentCase.site),
            joinedload(models.AccidentCase.department),
            joinedload(models.AccidentCase.client),
            joinedload(models.AccidentCase.origin),
            joinedload(models.AccidentCase.reporter),
            joinedload(models.AccidentCase.driver),
            joinedload(models.AccidentCase.driver_role),
            joinedload(models.AccidentCase.vehicle_head),
            joinedload(models.AccidentCase.vehicle_tail),
            joinedload(models.AccidentCase.province),
            joinedload(models.AccidentCase.district),
            joinedload(models.AccidentCase.sub_district),
            joinedload(models.AccidentCase.docs),
            joinedload(models.AccidentCase.damage_items),
        )
        .filter(models.AccidentCase.document_no_ac == document_no_ac)
        .first()
    )

    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{document_no_ac}' not found")

    return case.to_dict()
