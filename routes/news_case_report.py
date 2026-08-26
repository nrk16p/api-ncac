from fastapi import APIRouter, Depends, Query
from sqlalchemy import nullslast
from sqlalchemy.orm import Session, joinedload
from typing import List

from database import get_db
from models import CaseReport
from schemas.news_schema import CaseReportNewsItem
from services.s3_service import get_image_presigned_urls_bulk

router = APIRouter(prefix="/news", tags=["News"])

PAGE_SIZE = 10
S3_BASE_PATH = "mn-ncac"


@router.get("/case-report", response_model=List[CaseReportNewsItem])
def list_case_report_news(
    page: int = Query(1, ge=1, description="Page index (1 = newest 10 items)"),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(CaseReport)
        .options(
            joinedload(CaseReport.site),
            joinedload(CaseReport.client),
        )
        .order_by(
            nullslast(CaseReport.record_date.desc()),
            nullslast(CaseReport.incident_date.desc()),
            CaseReport.case_id.desc(),
        )
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )

    document_nos = [case.document_no for case in rows]
    image_urls_by_doc = get_image_presigned_urls_bulk(document_nos, base_path=S3_BASE_PATH)

    items = []
    for case in rows:
        site = case.site.site_name_th if case.site else None
        client = (case.client.client_name if case.client else None) or "-"
        title = f"รายงาน NC: {client}"

        description = case.case_details or ""

        items.append(
            CaseReportNewsItem(
                case_id=case.case_id,
                document_no=case.document_no,
                title=title,
                description=description,
                image_urls=image_urls_by_doc.get(case.document_no, []),
                client_name=client,
                site=site,
                priority=case.priority,
                casestatus=case.casestatus,
                record_date=case.record_date,
                incident_date=case.incident_date,
            )
        )

    return items
