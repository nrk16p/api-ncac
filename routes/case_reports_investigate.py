from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
import models, schemas

router = APIRouter(
    prefix="/case-report-investigate",
    tags=["Case Report Investigate"]
)

@router.post("/{document_no}", response_model=schemas.CaseReportInvestigateOut)
def create_or_update_investigation(
    document_no: str,
    payload: schemas.CaseReportInvestigateCreate,
    db: Session = Depends(get_db)
):
    record = db.query(models.CaseReportInvestigate).filter(
        models.CaseReportInvestigate.document_no == document_no
    ).first()

    if record:
        for key, value in payload.dict(exclude_unset=True).items():
            setattr(record, key, value)
        db.commit()
        db.refresh(record)
        return record
    else:
        new_record = models.CaseReportInvestigate(document_no=document_no, **payload.dict())
        db.add(new_record)
        db.commit()
        db.refresh(new_record)
        return new_record


# ✅ Read all
@router.get("/", response_model=list[schemas.CaseReportInvestigateOut])
def get_all_investigations(db: Session = Depends(get_db)):
    return db.query(models.CaseReportInvestigate).all()


# ✅ Read one by document_no
@router.get("/{document_no}", response_model=schemas.CaseReportInvestigateOut)
def get_investigation(document_no: str, db: Session = Depends(get_db)):
    record = db.query(models.CaseReportInvestigate).filter(models.CaseReportInvestigate.document_no == document_no).first()
    if not record:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return record


# ✅ Update by document_no
@router.put("/{document_no}", response_model=schemas.CaseReportInvestigateOut)
def update_investigation(document_no: str, payload: schemas.CaseReportInvestigateUpdate, db: Session = Depends(get_db)):
    record = db.query(models.CaseReportInvestigate).filter(models.CaseReportInvestigate.document_no == document_no).first()
    if not record:
        raise HTTPException(status_code=404, detail="Investigation not found")

    for key, value in payload.dict(exclude_unset=True).items():
        setattr(record, key, value)

    db.commit()
    db.refresh(record)
    return record


