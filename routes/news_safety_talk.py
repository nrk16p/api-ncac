from fastapi import APIRouter, Depends, Query
from sqlalchemy import nullslast
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import inspection as models
from schemas.news_schema import SafetyTalkNewsItem
from services.s3_service import get_image_presigned_urls

router = APIRouter(prefix="/news", tags=["News"])

PAGE_SIZE = 10


@router.get("/safety-talk", response_model=List[SafetyTalkNewsItem])
def list_safety_talk_news(
    page: int = Query(1, ge=1, description="Page index (1 = newest 10 items)"),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.SafetyTalk, models.InspectionTask)
        .join(
            models.InspectionTask,
            models.SafetyTalk.inspection_task_id == models.InspectionTask.inspection_task_id,
        )
        .order_by(
            nullslast(models.InspectionTask.action_date.desc()),
            nullslast(models.InspectionTask.created_at.desc()),
        )
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )

    items = []
    for safety_talk, task in rows:
        date_str = task.action_date.strftime("%d/%m/%Y") if task.action_date else "-"
        place = task.plant_name or task.client_name or "-"
        title = f"Safety Talk ประจำวันที่ {date_str} แพล้นท์ {place}"

        topics = safety_talk.topics or []
        description = ", ".join(topics) if topics else ""

        items.append(
            SafetyTalkNewsItem(
                inspection_task_id=task.inspection_task_id,
                title=title,
                description=description,
                image_urls=get_image_presigned_urls(task.inspection_task_id),
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )

    return items
