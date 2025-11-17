from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
import models, schemas

router = APIRouter(
    prefix="/case-report-investigate",
    tags=["Case Report Investigate"]
)

# ======================================================
# HELPER: Check if Investigation is complete
# ======================================================
def investigation_is_complete(record):

    # required parent fields (must NOT be empty)
    required_parent = [
        record.root_cause_analysis,
        record.claim_type,
        record.insurance_claim,
        record.product_resellable,
        record.remaining_damage_cost,
        record.driver_cost,
        record.company_cost,
    ]

    for f in required_parent:
        if f is None or (isinstance(f, str) and f.strip() == ""):
            return False

    # required child fields
    for item in record.corrective_actions:
        if not item.corrective_action or item.corrective_action.strip() == "":
            return False
        if not item.pic_contract or item.pic_contract.strip() == "":
            return False
        if not item.plan_date:
            return False
        # action_completed_date can be NULL → allowed

    return True


# ======================================================
# CREATE OR UPDATE INVESTIGATION
# ======================================================
@router.post("/{document_no}", response_model=schemas.CaseReportInvestigateOut)
def create_or_update_investigation(
    document_no: str,
    payload: schemas.CaseReportInvestigateCreate,
    db: Session = Depends(get_db)
):

    record = db.query(models.CaseReportInvestigate).filter_by(
        document_no=document_no
    ).first()

    # ======================================================
    # UPDATE IF EXISTS
    # ======================================================
    if record:

        # Update parent fields
        for key, value in payload.dict(
            exclude={"corrective_actions"},
            exclude_unset=True
        ).items():
            setattr(record, key, value)

        # Replace child rows
        db.query(models.CaseReportCorrectiveAction).filter_by(
            investigate_id=record.investigate_id
        ).delete()

        for item in payload.corrective_actions:
            db.add(
                models.CaseReportCorrectiveAction(
                    investigate_id=record.investigate_id,
                    **item.dict(exclude_unset=True)
                )
            )

        db.commit()
        db.refresh(record)

        # Auto update CaseReport.casestatus
        if investigation_is_complete(record):
            case_report = db.query(models.CaseReport).filter_by(
                document_no=document_no
            ).first()
            if case_report:
                case_report.casestatus = "Completed Investigate"
                db.commit()

        return record

    # ======================================================
    # CREATE NEW INVESTIGATION
    # ======================================================
    new_record = models.CaseReportInvestigate(
        document_no=document_no,
        **payload.dict(exclude={"corrective_actions"})
    )
    db.add(new_record)
    db.commit()
    db.refresh(new_record)

    # Add child rows
    for item in payload.corrective_actions:
        db.add(
            models.CaseReportCorrectiveAction(
                investigate_id=new_record.investigate_id,
                **item.dict(exclude_unset=True)
            )
        )

    db.commit()
    db.refresh(new_record)

    # Auto update CaseReport.casestatus
    if investigation_is_complete(new_record):
        case_report = db.query(models.CaseReport).filter_by(
            document_no=document_no
        ).first()
        if case_report:
            case_report.casestatus = "Completed Investigate"
            db.commit()

    return new_record


# ======================================================
# READ ALL INVESTIGATIONS
# ======================================================
@router.get("/", response_model=list[schemas.CaseReportInvestigateOut])
def get_all_investigations(db: Session = Depends(get_db)):
    records = db.query(models.CaseReportInvestigate).all()

    # Auto-load parent CaseReport
    for r in records:
        r.case_report

    return records


# ======================================================
# READ ONE BY DOCUMENT NO (WITH CASE REPORT)
# ======================================================
@router.get("/{document_no}", response_model=schemas.CaseReportInvestigateOut)
def get_investigation(document_no: str, db: Session = Depends(get_db)):
    record = db.query(models.CaseReportInvestigate).filter_by(
        document_no=document_no
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="Investigation not found")

    # ensure parent CaseReport loads
    record.case_report

    return record


# ======================================================
# UPDATE ONLY (PUT)
# ======================================================
@router.put("/{document_no}", response_model=schemas.CaseReportInvestigateOut)
def update_investigation(
    document_no: str,
    payload: schemas.CaseReportInvestigateUpdate,
    db: Session = Depends(get_db)
):

    record = db.query(models.CaseReportInvestigate).filter_by(
        document_no=document_no
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="Investigation not found")

    # update parent fields
    for key, value in payload.dict(
        exclude={"corrective_actions"},
        exclude_unset=True
    ).items():
        setattr(record, key, value)

    # update children (replace)
    if payload.corrective_actions is not None:
        db.query(models.CaseReportCorrectiveAction).filter_by(
            investigate_id=record.investigate_id
        ).delete()

        for item in payload.corrective_actions:
            db.add(
                models.CaseReportCorrectiveAction(
                    investigate_id=record.investigate_id,
                    **item.dict(exclude_unset=True)
                )
            )

    db.commit()
    db.refresh(record)

    # auto update status
    if investigation_is_complete(record):
        case_report = db.query(models.CaseReport).filter_by(
            document_no=document_no
        ).first()
        if case_report:
            case_report.casestatus = "Investigation Completed"
            db.commit()

    return record
