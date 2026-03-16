from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import extract
from typing import List

from database import get_db
from models.mixer_compensation_model import MixerCompensationCPAC
from schemas.mixer_compensation_schema import (
    MixerCompensationCreate,
    MixerCompensationResponse
)
router = APIRouter(
    prefix="/mixer-compensation",
    tags=["Mixer Compensation"]
)


@router.get("", response_model=list[MixerCompensationResponse])
def get_records(
    year: int | None = Query(None),
    month: int | None = Query(None),
    db: Session = Depends(get_db)
):

    query = db.query(MixerCompensationCPAC)

    if year:
        query = query.filter(
            extract("year", MixerCompensationCPAC.date_ticket) == year
        )

    if month:
        query = query.filter(
            extract("month", MixerCompensationCPAC.date_ticket) == month
        )

    return query.all()

# -------------------------
# POST
# -------------------------
@router.post("")
def bulk_upsert_records(
    payload: List[MixerCompensationCreate],
    db: Session = Depends(get_db)
):

    ticket_nos = [p.TicketNo for p in payload]

    # existing records
    existing_records = (
        db.query(MixerCompensationCPAC)
        .filter(MixerCompensationCPAC.TicketNo.in_(ticket_nos))
        .all()
    )

    existing_map = {r.TicketNo: r for r in existing_records}

    created = 0
    updated = 0

    for row in payload:

        if row.TicketNo in existing_map:

            record = existing_map[row.TicketNo]

            for key, value in row.dict().items():
                setattr(record, key, value)

            updated += 1

        else:

            obj = MixerCompensationCPAC(**row.dict())
            db.add(obj)

            created += 1

    db.commit()

    return {
        "created": created,
        "updated": updated,
        "total": len(payload)
    }