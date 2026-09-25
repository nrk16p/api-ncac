"""
สร้าง DB campaign_forms (index + ข้อมูลตั้งต้นจาก mock ของ mena-next-lb)

  python scripts/campaign_forms/seed_campaign_forms.py [path/to/mock]

รันซ้ำได้ — ฟอร์มที่มี form_id อยู่แล้วจะไม่ถูกเขียนทับ และจะใส่คำตอบเฉพาะฟอร์มที่ยังไม่มีคำตอบเลย
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from routes.campaign_forms import ANSWERS, DB_NAME, FORMS, LOGS, _validate_answers, ensure_indexes  # noqa: E402
from schemas.campaign_forms import FormQuestion  # noqa: E402
from services.mongo_service import get_mongo_db  # noqa: E402

DEFAULT_MOCK_DIR = Path.home() / "Desktop/project 2026/mena-next-lb/src/components/campaign-forms/mock"


def _utc(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso).astimezone(timezone.utc) if iso else None


def main() -> None:
    mock_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MOCK_DIR
    forms = json.loads((mock_dir / "forms.json").read_text(encoding="utf-8"))
    responses = json.loads((mock_dir / "responses.json").read_text(encoding="utf-8"))

    ensure_indexes()
    db = get_mongo_db(DB_NAME)

    for f in forms:
        form_id = f["id"]
        created_at = _utc(f["created_at"])
        approved_at = _utc(f.get("approved_at"))
        doc = {
            "form_id": form_id,
            "name": f["name"],
            "description": f.get("description", ""),
            "questions": [FormQuestion(**q).model_dump() for q in f["questions"]],
            "created_by": f["created_by"],
            "created_at": created_at,
            "updated_at": created_at,
            "approved_by": f.get("approved_by"),
            "approved_at": approved_at,
            "deleted_at": None,
        }
        result = db[FORMS].update_one({"form_id": form_id}, {"$setOnInsert": doc}, upsert=True)
        if result.upserted_id is None:
            print(f"skip   {form_id} (มีอยู่แล้ว)")
            continue

        logs = [{"form_id": form_id, "action": "create", "name": f["created_by"], "timestamp": created_at}]
        if f.get("approved_by"):
            logs.append({"form_id": form_id, "action": "approve", "name": f["approved_by"], "timestamp": approved_at})
        db[LOGS].insert_many(logs)
        print(f"insert {form_id} {f['name']}")

    by_form: dict[str, list] = {}
    for r in responses:
        by_form.setdefault(r["form_id"], []).append(r)

    for form_id, rows in by_form.items():
        if db[ANSWERS].count_documents({"form_id": form_id}, limit=1):
            print(f"skip   answers {form_id} (มีคำตอบอยู่แล้ว)")
            continue
        questions = db[FORMS].find_one({"form_id": form_id})["questions"]
        db[ANSWERS].insert_many([
            {
                "form_id": form_id,
                "respondent": r["respondent"],
                "drivercode": r.get("drivercode"),
                "line_user_id": None,
                "campaign_id": None,
                "submitted_at": _utc(r["submitted_at"]),
                "answers": _validate_answers(questions, r["answers"]),
            }
            for r in rows
        ])
        print(f"insert answers {form_id} × {len(rows)}")

    for name in (FORMS, ANSWERS, LOGS):
        print(f"{DB_NAME}.{name}: {db[name].count_documents({})} docs")


if __name__ == "__main__":
    main()
