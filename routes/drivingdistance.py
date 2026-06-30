import logging
from datetime import date, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from database import get_db
from models.drivingdistance_model import DrivingDistance
from schemas.drivingdistance_schema import DrivingDistanceCreate, DrivingDistanceOut

router = APIRouter(prefix="/drivingdistance", tags=["Driving Distance"])

logger = logging.getLogger("drivingdistance")
BKK_TZ = timezone(timedelta(hours=7))


class DrivingDistanceFilter(BaseModel):
    plate_number: Optional[List[str]] = None
    start_at: Optional[date] = None
    end_at: Optional[date] = None
    limit: int = 500


def _apply_filters(query, payload: DrivingDistanceFilter):
    filters = []
    if payload.plate_number:
        filters.append(DrivingDistance.plate_number.in_(payload.plate_number))
    if payload.start_at and payload.end_at:
        filters.append(and_(DrivingDistance.date >= payload.start_at, DrivingDistance.date <= payload.end_at))
    elif payload.start_at:
        filters.append(DrivingDistance.date >= payload.start_at)
    elif payload.end_at:
        filters.append(DrivingDistance.date <= payload.end_at)
    return query.filter(*filters) if filters else query


def _localize(records):
    for r in records:
        if r.created_at and r.created_at.tzinfo is None:
            r.created_at = r.created_at.replace(tzinfo=timezone.utc).astimezone(BKK_TZ)
    return records


@router.post("/bulk", response_model=List[DrivingDistanceOut])
def bulk_insert(payload: List[DrivingDistanceCreate], db: Session = Depends(get_db)):
    if not payload:
        raise HTTPException(status_code=400, detail="No records provided.")

    batch_size = 2000
    try:
        for start in range(0, len(payload), batch_size):
            batch = payload[start:start + batch_size]
            db.bulk_save_objects([DrivingDistance(**r.model_dump()) for r in batch])
            db.commit()
        logger.info("Bulk insert: %d records", len(payload))
    except Exception as e:
        db.rollback()
        logger.error("Bulk insert failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Database insert failed: {e}")

    inserted = (
        db.query(DrivingDistance)
        .order_by(DrivingDistance.id.desc())
        .limit(len(payload))
        .all()
    )
    return list(reversed(_localize(inserted)))


@router.post("/filter", response_model=List[DrivingDistanceOut])
def filter_records(payload: DrivingDistanceFilter, db: Session = Depends(get_db)):
    records = (
        _apply_filters(db.query(DrivingDistance), payload)
        .order_by(DrivingDistance.date.asc())
        .limit(payload.limit)
        .all()
    )
    if not records:
        raise HTTPException(status_code=404, detail="No matching records found")
    return _localize(records)


@router.get("/", response_model=List[DrivingDistanceOut])
def get_records(
    plate_number: Optional[List[str]] = Query(None),
    start_at: Optional[date] = Query(None),
    end_at: Optional[date] = Query(None),
    limit: int = Query(500),
    db: Session = Depends(get_db),
):
    f = DrivingDistanceFilter(plate_number=plate_number, start_at=start_at, end_at=end_at, limit=limit)
    records = (
        _apply_filters(db.query(DrivingDistance), f)
        .order_by(DrivingDistance.date.asc())
        .limit(limit)
        .all()
    )
    if not records:
        raise HTTPException(status_code=404, detail="No matching records found")
    return _localize(records)


@router.post("/sumdistance")
def sum_distance(payload: DrivingDistanceFilter, db: Session = Depends(get_db)):
    query = db.query(
        DrivingDistance.plate_number,
        func.sum(DrivingDistance.distance).label("total_distance"),
    )
    results = (
        _apply_filters(query, payload)
        .group_by(DrivingDistance.plate_number)
        .order_by(DrivingDistance.plate_number.asc())
        .all()
    )
    if not results:
        raise HTTPException(status_code=404, detail="No records found for summary")
    return {
        "period": f"{payload.start_at} → {payload.end_at}",
        "summary": [
            {"plate_number": r.plate_number, "total_distance": float(r.total_distance or 0),
             "start_at": payload.start_at, "end_at": payload.end_at}
            for r in results
        ],
    }


@router.get("/platenumber")
def get_plate_numbers(db: Session = Depends(get_db)):
    plates = (
        db.query(DrivingDistance.plate_number)
        .distinct()
        .order_by(DrivingDistance.plate_number.asc())
        .all()
    )
    unique = [p[0] for p in plates if p[0]]
    if not unique:
        raise HTTPException(status_code=404, detail="No plate numbers found")
    return {"count": len(unique), "plates": unique}
