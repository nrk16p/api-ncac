"""MongoDB persistence for the OPS module (db `ops`).

Collections: projects, issues, tasks, comments, surveys, counters.

The business id (project_id / issue_id / task_id) is used as Mongo's `_id` —
ids are minted from an atomic counter (strictly increasing, no retry-on-collision
needed unlike routes/campaign_forms.py's aggregate-max approach) so this can't
collide, and it means the public id *is* `_id` instead of a separate indexed
field: nothing extra is ever leaked, and lookups are a plain `_id` match.
Comment ids are a uuid4 hex string, for the same reason.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import pytz
from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from services.mongo_service import get_mongo_db

logger = logging.getLogger(__name__)

DB_NAME = "ops"
PROJECTS = "projects"
ISSUES = "issues"
TASKS = "tasks"
COMMENTS = "comments"
SURVEYS = "surveys"
COUNTERS = "counters"

OPEN_STATUS = "Open"
CLOSED_STATUSES = ("Done", "Reject")

_BKK = pytz.timezone("Asia/Bangkok")
_indexes_ready = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now() -> datetime:
    """Public alias of _now() for routes that need to stamp a sub-document
    (review.at, attachment.uploaded_at) before handing it to a repo function."""
    return _now()


def ensure_indexes() -> None:
    db = get_mongo_db(DB_NAME)
    db[PROJECTS].create_index([("requested_by.employee_id", ASCENDING)], name="requested_by_employee_id")
    db[ISSUES].create_index([("project_id", ASCENDING)], name="project_id")
    db[TASKS].create_index([("project_id", ASCENDING)], name="project_id")
    db[TASKS].create_index([("owner.employee_id", ASCENDING)], name="owner_employee_id")
    db[TASKS].create_index([("assignees.employee_id", ASCENDING)], name="assignees_employee_id")
    db[COMMENTS].create_index(
        [("ref_type", ASCENDING), ("ref_id", ASCENDING), ("created_at", ASCENDING)], name="ref_created_at"
    )
    db[SURVEYS].create_index([("system_id", ASCENDING), ("updated_at", ASCENDING)], name="system_updated_at")


def _col(name: str) -> Collection:
    """Lazy, once-per-process index creation — mirrors routes/campaign_forms.py's _col()
    so a Mongo outage at boot doesn't take down routes that don't use it."""
    global _indexes_ready

    try:
        db = get_mongo_db(DB_NAME)
    except RuntimeError as exc:  # pragma: no cover - requires MONGO_URI unset
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not _indexes_ready:
        try:
            ensure_indexes()
            _indexes_ready = True
        except PyMongoError:
            logger.warning("ops: create_index failed, will retry", exc_info=True)

    return db[name]


def mongo_error(exc: PyMongoError):
    from fastapi import HTTPException

    return HTTPException(status_code=503, detail=f"MongoDB ใช้งานไม่ได้: {exc}")


# ---------------------------------------------------------------------------
# Running ids — counters collection, find_one_and_update atomic $inc
# ---------------------------------------------------------------------------

def _next_id(prefix: str) -> str:
    year = datetime.now(_BKK).year
    counter_id = f"{prefix}-{year}"
    doc = _col(COUNTERS).find_one_and_update(
        {"_id": counter_id}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER
    )
    return f"{counter_id}-{doc['seq']:03d}"


def next_project_id() -> str:
    return _next_id("OPS")


def next_issue_id() -> str:
    return _next_id("ISS")


def next_task_id() -> str:
    return _next_id("TSK")


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def create_project(fields: Dict[str, Any], requested_by: dict) -> dict:
    now = _now()
    project_id = next_project_id()
    doc = {
        "_id": project_id,
        "project_id": project_id,
        **fields,
        "status": OPEN_STATUS,
        "requested_by": requested_by,
        "assignees": [],
        "progress": None,
        "planned_start": None,
        "planned_end": None,
        "attachments": [],
        "status_history": [{"status": OPEN_STATUS, "changed_at": now, "changed_by": requested_by, "remark": None}],
        "review": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        _col(PROJECTS).insert_one(doc)
    except PyMongoError as exc:
        raise mongo_error(exc) from exc
    return doc


def get_project(project_id: str) -> Optional[dict]:
    try:
        return _col(PROJECTS).find_one({"_id": project_id})
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def list_projects(*, mine_employee_id: Optional[str], status: Optional[str]) -> List[dict]:
    query: Dict[str, Any] = {}
    if mine_employee_id is not None:
        query["requested_by.employee_id"] = mine_employee_id
    if status:
        query["status"] = status
    try:
        return list(_col(PROJECTS).find(query).sort("created_at", -1))
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def bulk_issue_counts(project_ids: Iterable[str]) -> Dict[str, int]:
    """Issues not yet Done/Reject, grouped by project_id — one aggregate, no N+1."""
    ids = list(set(project_ids))
    if not ids:
        return {}
    pipeline = [
        {"$match": {"project_id": {"$in": ids}, "status": {"$nin": list(CLOSED_STATUSES)}}},
        {"$group": {"_id": "$project_id", "n": {"$sum": 1}}},
    ]
    try:
        return {row["_id"]: row["n"] for row in _col(ISSUES).aggregate(pipeline)}
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_project_status(project_id: str, status: str, remark: Optional[str], changed_by: dict) -> Optional[dict]:
    now = _now()
    change = {"status": status, "changed_at": now, "changed_by": changed_by, "remark": remark}
    try:
        return _col(PROJECTS).find_one_and_update(
            {"_id": project_id},
            {"$set": {"status": status, "updated_at": now}, "$push": {"status_history": change}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_project_fields(project_id: str, fields: Dict[str, Any]) -> Optional[dict]:
    """Request fields edited after filing (title, objective, requirement, …).
    A new title is copied onto the project's tasks, which keep it denormalised for their cards."""
    try:
        doc = _col(PROJECTS).find_one_and_update(
            {"_id": project_id},
            {"$set": {**fields, "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is not None and "title" in fields:
            _col(TASKS).update_many({"project_id": project_id}, {"$set": {"project_title": fields["title"]}})
        return doc
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_project_plan(project_id: str, fields: Dict[str, Any]) -> Optional[dict]:
    try:
        return _col(PROJECTS).find_one_and_update(
            {"_id": project_id},
            {"$set": {**fields, "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_project_assignees(project_id: str, people: List[dict]) -> Optional[dict]:
    try:
        return _col(PROJECTS).find_one_and_update(
            {"_id": project_id},
            {"$set": {"assignees": people, "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_project_review(project_id: str, review: dict) -> Optional[dict]:
    try:
        return _col(PROJECTS).find_one_and_update(
            {"_id": project_id},
            {"$set": {"review": review, "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def add_project_attachment(project_id: str, attachment: dict) -> Optional[dict]:
    try:
        return _col(PROJECTS).find_one_and_update(
            {"_id": project_id},
            {"$push": {"attachments": attachment}, "$set": {"updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

def create_issue(fields: Dict[str, Any], reported_by: dict) -> dict:
    now = _now()
    issue_id = next_issue_id()
    doc = {
        "_id": issue_id,
        "issue_id": issue_id,
        **fields,
        "status": OPEN_STATUS,
        "reported_by": reported_by,
        "attachments": [],
        "status_history": [{"status": OPEN_STATUS, "changed_at": now, "changed_by": reported_by, "remark": None}],
        "review": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        _col(ISSUES).insert_one(doc)
    except PyMongoError as exc:
        raise mongo_error(exc) from exc
    return doc


def get_issue(issue_id: str) -> Optional[dict]:
    try:
        return _col(ISSUES).find_one({"_id": issue_id})
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def list_issues(*, mine_employee_id: Optional[str], project_id: Optional[str], status: Optional[str]) -> List[dict]:
    query: Dict[str, Any] = {}
    if mine_employee_id is not None:
        query["reported_by.employee_id"] = mine_employee_id
    if project_id:
        query["project_id"] = project_id
    if status:
        query["status"] = status
    try:
        return list(_col(ISSUES).find(query).sort("created_at", -1))
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def bulk_projects_by_id(project_ids: Iterable[str]) -> Dict[str, dict]:
    ids = list(set(project_ids))
    if not ids:
        return {}
    try:
        docs = _col(PROJECTS).find({"_id": {"$in": ids}})
        return {d["_id"]: d for d in docs}
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_issue_status(issue_id: str, status: str, remark: Optional[str], changed_by: dict) -> Optional[dict]:
    now = _now()
    change = {"status": status, "changed_at": now, "changed_by": changed_by, "remark": remark}
    try:
        return _col(ISSUES).find_one_and_update(
            {"_id": issue_id},
            {"$set": {"status": status, "updated_at": now}, "$push": {"status_history": change}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_issue_review(issue_id: str, review: dict) -> Optional[dict]:
    try:
        return _col(ISSUES).find_one_and_update(
            {"_id": issue_id},
            {"$set": {"review": review, "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def add_issue_attachment(issue_id: str, attachment: dict) -> Optional[dict]:
    try:
        return _col(ISSUES).find_one_and_update(
            {"_id": issue_id},
            {"$push": {"attachments": attachment}, "$set": {"updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def create_task(fields: Dict[str, Any], project_title: str, owner: dict) -> dict:
    now = _now()
    task_id = next_task_id()
    doc = {
        "_id": task_id,
        "task_id": task_id,
        **fields,
        "project_title": project_title,
        "status": OPEN_STATUS,
        "owner": owner,
        "assignees": [],
        "status_history": [{"status": OPEN_STATUS, "changed_at": now, "changed_by": owner, "remark": None}],
        "created_at": now,
        "updated_at": now,
    }
    try:
        _col(TASKS).insert_one(doc)
    except PyMongoError as exc:
        raise mongo_error(exc) from exc
    return doc


def get_task(task_id: str) -> Optional[dict]:
    try:
        return _col(TASKS).find_one({"_id": task_id})
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def list_tasks(*, mine_employee_id: Optional[str], project_id: Optional[str]) -> List[dict]:
    query: Dict[str, Any] = {}
    if mine_employee_id is not None:
        query["$or"] = [
            {"owner.employee_id": mine_employee_id},
            {"assignees.employee_id": mine_employee_id},
        ]
    if project_id:
        query["project_id"] = project_id
    try:
        return list(_col(TASKS).find(query).sort("created_at", -1))
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_task_fields(task_id: str, fields: Dict[str, Any]) -> Optional[dict]:
    try:
        return _col(TASKS).find_one_and_update(
            {"_id": task_id},
            {"$set": {**fields, "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_task_status(task_id: str, status: str, remark: Optional[str], changed_by: dict) -> Optional[dict]:
    now = _now()
    change = {"status": status, "changed_at": now, "changed_by": changed_by, "remark": remark}
    try:
        return _col(TASKS).find_one_and_update(
            {"_id": task_id},
            {"$set": {"status": status, "updated_at": now}, "$push": {"status_history": change}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_task_assignees(task_id: str, people: List[dict]) -> Optional[dict]:
    try:
        return _col(TASKS).find_one_and_update(
            {"_id": task_id},
            {"$set": {"assignees": people, "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_task_plan(task_id: str, due_date) -> Optional[dict]:
    try:
        return _col(TASKS).find_one_and_update(
            {"_id": task_id},
            {"$set": {"due_date": due_date, "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_task_note(task_id: str, note: Optional[str], by: dict) -> Optional[dict]:
    now = _now()
    try:
        return _col(TASKS).find_one_and_update(
            {"_id": task_id},
            {"$set": {"note": note, "note_updated_at": now, "note_updated_by": by, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def add_task_attachment(task_id: str, attachment: dict) -> Optional[dict]:
    try:
        return _col(TASKS).find_one_and_update(
            {"_id": task_id},
            {"$push": {"attachments": attachment}, "$set": {"updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def remove_task_attachment(task_id: str, attachment_id: str) -> Optional[dict]:
    try:
        return _col(TASKS).find_one_and_update(
            {"_id": task_id},
            {"$pull": {"attachments": {"attachment_id": attachment_id}}, "$set": {"updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def create_comment(
    ref_type: str,
    ref_id: str,
    body: str,
    author: dict,
    *,
    comment_id: Optional[str] = None,
    attachments: Optional[List[dict]] = None,
) -> dict:
    now = _now()
    comment_id = comment_id or uuid.uuid4().hex
    doc = {
        "_id": comment_id,
        "comment_id": comment_id,
        "ref_type": ref_type,
        "ref_id": ref_id,
        "author": author,
        "body": body,
        "attachments": attachments or [],
        "created_at": now,
        "edited_at": None,
        "liked_by": [],
    }
    try:
        _col(COMMENTS).insert_one(doc)
    except PyMongoError as exc:
        raise mongo_error(exc) from exc
    return doc


def get_comment(comment_id: str) -> Optional[dict]:
    try:
        return _col(COMMENTS).find_one({"_id": comment_id})
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def list_comments(ref_type: str, ref_id: str) -> List[dict]:
    try:
        return list(_col(COMMENTS).find({"ref_type": ref_type, "ref_id": ref_id}).sort("created_at", 1))
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def update_comment(comment_id: str, body: str) -> Optional[dict]:
    try:
        return _col(COMMENTS).find_one_and_update(
            {"_id": comment_id},
            {"$set": {"body": body, "edited_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def delete_comment(comment_id: str) -> None:
    try:
        _col(COMMENTS).delete_one({"_id": comment_id})
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def set_comment_like(comment_id: str, employee_id: str, like: bool) -> Optional[dict]:
    op = {"$addToSet": {"liked_by": employee_id}} if like else {"$pull": {"liked_by": employee_id}}
    try:
        return _col(COMMENTS).find_one_and_update(
            {"_id": comment_id}, op, return_document=ReturnDocument.AFTER
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


# ---------------------------------------------------------------------------
# Satisfaction surveys — one answer per person per system; answering again replaces it
# ---------------------------------------------------------------------------

def upsert_survey(system_id: str, fields: Dict[str, Any], respondent: dict) -> dict:
    now = _now()
    survey_id = f"{system_id}:{respondent['employee_id']}"
    try:
        return _col(SURVEYS).find_one_and_update(
            {"_id": survey_id},
            {
                "$set": {**fields, "system_id": system_id, "respondent": respondent, "updated_at": now},
                "$setOnInsert": {"survey_id": survey_id, "created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def get_survey(system_id: str, employee_id: str) -> Optional[dict]:
    try:
        return _col(SURVEYS).find_one({"_id": f"{system_id}:{employee_id}"})
    except PyMongoError as exc:
        raise mongo_error(exc) from exc


def list_surveys(system_id: str) -> List[dict]:
    try:
        return list(_col(SURVEYS).find({"system_id": system_id}).sort("updated_at", -1))
    except PyMongoError as exc:
        raise mongo_error(exc) from exc
