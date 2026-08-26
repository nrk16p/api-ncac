from fastapi import APIRouter, Depends, Query
from sqlalchemy import nullslast
from sqlalchemy.orm import Session, joinedload
from typing import List

from database import get_db
from models import AccidentCase
from schemas.news_schema import AccidentCaseNewsItem
from services.s3_service import get_image_presigned_urls_bulk

router = APIRouter(prefix="/news", tags=["News"])

PAGE_SIZE = 10
S3_BASE_PATH = "mn-ncac"


@router.get("/accident-case", response_model=List[AccidentCaseNewsItem])
def list_accident_case_news(
    page: int = Query(1, ge=1, description="Page index (1 = newest 10 items)"),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(AccidentCase)
        .options(
            joinedload(AccidentCase.site),
            joinedload(AccidentCase.client),
        )
        .order_by(
            nullslast(AccidentCase.record_datetime.desc()),
            nullslast(AccidentCase.incident_datetime.desc()),
            AccidentCase.accident_case_id.desc(),
        )
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )

    document_nos = [case.document_no_ac for case in rows]
    image_urls_by_doc = get_image_presigned_urls_bulk(document_nos, base_path=S3_BASE_PATH)

    items = []
    for case in rows:
        site = case.site.site_name_th if case.site else None
        client = (case.client.client_name if case.client else None) or "-"
        title = f"รายงาน AC: {client}"

        description = case.case_details or ""

        items.append(
            AccidentCaseNewsItem(
                accident_case_id=case.accident_case_id,
                document_no_ac=case.document_no_ac,
                title=title,
                description=description,
                image_urls=image_urls_by_doc.get(case.document_no_ac, []),
                client_name=client,
                site=site,
                priority=case.priority,
                casestatus=case.casestatus,
                record_datetime=case.record_datetime,
                incident_datetime=case.incident_datetime,
            )
        )

    return items
