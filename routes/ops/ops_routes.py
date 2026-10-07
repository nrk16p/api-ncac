"""Group OPS — project requests, issues, tasks, comments, attachments.

Contract: menaIT-v2's app/ops/schema/ops.schema.json + app/ops/types.ts + app/ops/api.ts.
The Next.js server verifies the login cookie and forwards the employee id as the
`X-Employee-Id` header on every request here — there is no API key, see get_caller().

Every response from this router (success or error) is JSON. Errors use the shared
{"error": "...", "field_errors"?: {...}} shape from ops.schema.json's ApiError — see
OpsAPIRoute below, which maps ops_logic.OpsError / HTTPException / pydantic
validation errors into that shape *only for routes on this router* (no global
exception handler is installed, so every other router's error behaviour is
untouched).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile

from database import get_db
from schemas import ops_schema as schemas
from services.ops import ops_files, ops_logic, ops_repo, people_repo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router with a scoped error-shaping route class (see module docstring)
# ---------------------------------------------------------------------------


class OpsAPIRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request):
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                field_errors: Dict[str, str] = {}
                for err in exc.errors():
                    loc = err.get("loc", ())
                    field = str(loc[-1]) if loc else "body"
                    field_errors[field] = err.get("msg", "ข้อมูลไม่ถูกต้อง")
                return JSONResponse(
                    status_code=400, content={"error": "ข้อมูลไม่ถูกต้อง", "field_errors": field_errors}
                )
            except ops_logic.OpsError as exc:
                detail = exc.detail()
                content = detail if isinstance(detail, dict) else {"error": detail}
                return JSONResponse(status_code=exc.http_status, content=content)
            except HTTPException as exc:
                detail = exc.detail
                content = detail if isinstance(detail, dict) and "error" in detail else {"error": str(detail)}
                return JSONResponse(status_code=exc.status_code, content=content)

        return custom_handler


router = APIRouter(prefix="/ops", tags=["OPS"], route_class=OpsAPIRoute)


# ---------------------------------------------------------------------------
# Caller identity (X-Employee-Id → Person + manager flag)
# ---------------------------------------------------------------------------

@dataclass
class Caller:
    person: dict
    employee_id: str
    username: Optional[str]
    is_manager: bool
    is_admin: bool = False


def get_caller(
    x_employee_id: Optional[str] = Header(None, alias="X-Employee-Id"),
    db: Session = Depends(get_db),
) -> Caller:
    if not x_employee_id:
        raise HTTPException(status_code=401, detail=ops_logic.MSG_LOGIN_REQUIRED)
    user = people_repo.get_user_by_employee_id(db, x_employee_id)
    if user is None:
        raise HTTPException(status_code=401, detail=ops_logic.MSG_LOGIN_REQUIRED)
    person = people_repo.person_from_user(user)
    is_mgr = ops_logic.is_manager(user.username, user.department_id, user.employee_id)
    return Caller(
        person=person,
        employee_id=person["employee_id"],
        username=user.username,
        is_manager=is_mgr,
        is_admin=ops_logic.is_admin(user.department_id, user.employee_id),
    )


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _date_str(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _plan_fields(changes: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("planned_start", "planned_end"):
        if key in changes:
            changes[key] = _date_str(changes[key])
    return changes


def _attachments_out(attachments: List[dict]) -> List[dict]:
    out = []
    for a in attachments or []:
        out.append(
            {
                "attachment_id": a["attachment_id"],
                "file_name": a["file_name"],
                "mime_type": a["mime_type"],
                "size": a["size"],
                "url": ops_files.presigned_get_url(a["s3_key"]) or "",
                "uploaded_at": a.get("uploaded_at"),
            }
        )
    return out


def _can_edit(doc: dict, caller: Caller) -> bool:
    return ops_logic.can_edit_project(doc, caller.employee_id, caller.username, caller.person.get("department"))


def _project_out(doc: dict, issue_count: int, caller: Caller) -> dict:
    out = {k: v for k, v in doc.items() if k not in ("_id", "attachments")}
    out["attachments"] = _attachments_out(doc.get("attachments", []))
    out["issue_count"] = issue_count
    out["can_edit"] = _can_edit(doc, caller)
    out["can_rename"] = ops_logic.can_rename_project(doc, caller.is_manager, out["can_edit"])
    return out


def _issue_out(doc: dict, project_title: str, project_assignees: List[dict]) -> dict:
    out = {k: v for k, v in doc.items() if k not in ("_id", "attachments")}
    out["attachments"] = _attachments_out(doc.get("attachments", []))
    out["project_title"] = project_title
    out["project_assignees"] = project_assignees
    return out


def _task_out(doc: dict, caller: Caller) -> dict:
    out = {k: v for k, v in doc.items() if k not in ("_id", "attachments")}
    out["attachments"] = _attachments_out(doc.get("attachments", []))
    out["can_note"] = ops_logic.can_note_task(doc, caller.employee_id, caller.is_admin)
    return out


def _comment_out(doc: dict, caller_employee_id: str) -> dict:
    liked_by = doc.get("liked_by", [])
    return {
        "comment_id": doc["comment_id"],
        "ref_type": doc["ref_type"],
        "ref_id": doc["ref_id"],
        "author": doc["author"],
        "body": doc["body"],
        "attachments": _attachments_out(doc.get("attachments", [])),
        "created_at": doc["created_at"],
        "edited_at": doc.get("edited_at"),
        "like_count": len(liked_by),
        "liked_by_me": caller_employee_id in liked_by,
    }


def _like_state(doc: dict, caller_employee_id: str) -> dict:
    liked_by = doc.get("liked_by", [])
    return {"like_count": len(liked_by), "liked_by_me": caller_employee_id in liked_by}


def _require_project(project_id: str) -> dict:
    doc = ops_repo.get_project(project_id)
    if doc is None:
        raise ops_logic.OpsNotFound(ops_logic.MSG_PROJECT_NOT_FOUND)
    return doc


def _require_issue(issue_id: str) -> dict:
    doc = ops_repo.get_issue(issue_id)
    if doc is None:
        raise ops_logic.OpsNotFound(ops_logic.MSG_ISSUE_NOT_FOUND)
    return doc


def _require_task(task_id: str) -> dict:
    doc = ops_repo.get_task(task_id)
    if doc is None:
        raise ops_logic.OpsNotFound(ops_logic.MSG_TASK_NOT_FOUND)
    return doc


def _require_comment(comment_id: str) -> dict:
    doc = ops_repo.get_comment(comment_id)
    if doc is None:
        raise ops_logic.OpsNotFound(ops_logic.MSG_COMMENT_NOT_FOUND)
    return doc


def _validate_status_filter(status: Optional[str]) -> Optional[str]:
    if status is not None and status not in ops_logic.STATUSES:
        raise ops_logic.OpsError(f"สถานะไม่ถูกต้อง: {status}")
    return status


def _validation_error_to_ops_error(exc: ValidationError) -> ops_logic.OpsError:
    """Mirrors OpsAPIRoute's RequestValidationError → field_errors mapping, for the
    rare case a hand-parsed JSON body fails schemas.CommentInput's own validation
    (e.g. body > 2000 chars or an unexpected extra field)."""
    field_errors: Dict[str, str] = {}
    for err in exc.errors():
        loc = err.get("loc", ())
        field = str(loc[-1]) if loc else "body"
        field_errors[field] = err.get("msg", "ข้อมูลไม่ถูกต้อง")
    return ops_logic.OpsError("ข้อมูลไม่ถูกต้อง", field_errors)


async def _parse_comment_input(request: Request) -> Tuple[str, List[UploadFile]]:
    """POST /ops/.../comments accepts both application/json {"body"} (unchanged,
    for the currently-deployed frontend) and multipart/form-data with a "body" text
    field (optional/empty) plus zero or more repeated "files" image fields."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            payload = await request.json()
        except ValueError:
            raise ops_logic.OpsError(ops_logic.MSG_UNSUPPORTED_CONTENT_TYPE)
        try:
            comment_in = schemas.CommentInput(**(payload or {}))
        except ValidationError as exc:
            raise _validation_error_to_ops_error(exc)
        return comment_in.body, []
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw_body = form.get("body")
        body = raw_body if isinstance(raw_body, str) else ""
        # request.form() hands back plain starlette.datastructures.UploadFile instances
        # (fastapi.UploadFile is a *subclass* used for declared File()/UploadFile params,
        # which isn't what a hand-parsed multipart body yields) — check the base class.
        files = [f for f in form.getlist("files") if isinstance(f, StarletteUploadFile) and f.filename]
        return body, files
    raise ops_logic.OpsError(ops_logic.MSG_UNSUPPORTED_CONTENT_TYPE)


async def _read_comment_images(files: List[UploadFile]) -> List[Tuple[str, str, bytes]]:
    """Only reads each file's bytes (UploadFile.read() is a coroutine that Starlette
    already offloads to a thread for the actual file I/O, so this part is fine to
    await directly on the event loop). No validation, no S3, no Mongo here — see
    _create_comment_sync, which does all of that off the event loop in one thread."""
    reads: List[Tuple[str, str, bytes]] = []
    for f in files:
        data = await f.read()
        mime_type = f.content_type or "application/octet-stream"
        reads.append((f.filename or "file", mime_type, data))
    return reads


def _upload_comment_images(
    reads: List[Tuple[str, str, bytes]], project_id: str, comment_id: str
) -> List[dict]:
    now = ops_repo.utc_now()
    attachments: List[dict] = []
    for filename, mime_type, data in reads:
        attachment_id = uuid.uuid4().hex
        safe_name = ops_logic.safe_file_name(filename)
        key = ops_files.comment_attachment_key(project_id, comment_id, attachment_id, safe_name)
        ops_files.upload_bytes(key, data, mime_type)
        attachments.append(
            {
                "attachment_id": attachment_id,
                "file_name": filename or safe_name,
                "mime_type": mime_type,
                "size": len(data),
                "s3_key": key,
                "uploaded_at": now,
            }
        )
    return attachments


def _create_comment_sync(
    ref_type: Literal["project", "issue"],
    ref_id: str,
    raw_body: str,
    reads: List[Tuple[str, str, bytes]],
    caller: Caller,
) -> dict:
    """Everything blocking for a comment create — the project/issue existence check
    (pymongo), file validation, the S3 upload(s) (boto3), the Mongo insert (pymongo),
    and building the response (presigned URLs, also boto3) — run via run_in_threadpool
    from the two async route handlers below, so none of it blocks uvicorn's event loop.
    Image count/type/size and the body are both validated before any S3 upload, so a
    rejection here never leaves an orphaned upload behind."""
    if ref_type == "project":
        _require_project(ref_id)
        s3_project_id = ref_id
    else:
        issue = _require_issue(ref_id)
        s3_project_id = issue["project_id"]

    ops_logic.check_comment_image_count(len(reads))
    for _filename, mime_type, data in reads:
        ops_logic.check_comment_image_file(mime_type, len(data))
    cleaned_body = ops_logic.validate_comment_body(raw_body, has_images=bool(reads))

    comment_id = uuid.uuid4().hex
    attachments = _upload_comment_images(reads, s3_project_id, comment_id)
    doc = ops_repo.create_comment(
        ref_type, ref_id, cleaned_body, caller.person, comment_id=comment_id, attachments=attachments
    )
    return _comment_out(doc, caller.employee_id)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@router.get("/projects", response_model=List[schemas.Project])
def list_projects(
    scope: Literal["mine", "all"] = Query("all"),
    status: Optional[str] = Query(None),
    caller: Caller = Depends(get_caller),
):
    status = _validate_status_filter(status)
    mine_id = caller.employee_id if scope == "mine" else None
    docs = ops_repo.list_projects(mine_employee_id=mine_id, status=status)
    counts = ops_repo.bulk_issue_counts([d["_id"] for d in docs])
    return [_project_out(d, counts.get(d["_id"], 0), caller) for d in docs]


@router.post("/projects", status_code=201)
def create_project(
    body: schemas.ProjectRequestInput,
    caller: Caller = Depends(get_caller),
):
    fields = {
        "title": body.title,
        "objective": body.objective,
        "requirement": ops_logic.sanitize_requirement_html(body.requirement),
        "expected_benefit": body.expected_benefit,
        "estimated_users": body.estimated_users,
        "user_groups": body.user_groups,
        "priority": body.priority,
        "target_date": _date_str(body.target_date),
    }
    doc = ops_repo.create_project(fields, caller.person)
    return {"project_id": doc["project_id"]}


@router.get("/projects/{project_id}", response_model=schemas.Project)
def get_project(project_id: str, caller: Caller = Depends(get_caller)):
    doc = _require_project(project_id)
    count = ops_repo.bulk_issue_counts([project_id]).get(project_id, 0)
    return _project_out(doc, count, caller)


@router.patch("/projects/{project_id}", response_model=schemas.Project)
def update_project(
    project_id: str,
    body: schemas.ProjectEditInput,
    caller: Caller = Depends(get_caller),
):
    doc = _require_project(project_id)
    ops_logic.require_project_editable(doc, caller.employee_id, caller.username, caller.person.get("department"))
    changes = body.model_dump(exclude_unset=True)
    if "requirement" in changes:
        changes["requirement"] = ops_logic.sanitize_requirement_html(changes["requirement"])
    if "target_date" in changes:
        changes["target_date"] = _date_str(changes["target_date"])
    if "user_groups" in changes:
        changes["user_groups"] = (changes["user_groups"] or "").strip() or None
    updated = ops_repo.update_project_fields(project_id, changes)
    count = ops_repo.bulk_issue_counts([project_id]).get(project_id, 0)
    return _project_out(updated, count, caller)


@router.patch("/projects/{project_id}/title", response_model=schemas.Project)
def update_project_title(
    project_id: str,
    body: schemas.ProjectTitleInput,
    caller: Caller = Depends(get_caller),
):
    doc = _require_project(project_id)
    ops_logic.require_project_renamable(doc, caller.is_manager, _can_edit(doc, caller))
    updated = ops_repo.update_project_fields(project_id, {"title": ops_logic.validate_project_title(body.title)})
    count = ops_repo.bulk_issue_counts([project_id]).get(project_id, 0)
    return _project_out(updated, count, caller)


@router.patch("/projects/{project_id}/link", response_model=schemas.Project)
def update_project_link(
    project_id: str,
    body: schemas.LinkInput,
    caller: Caller = Depends(get_caller),
):
    ops_logic.require_manager(caller.is_manager)
    doc = _require_project(project_id)
    url = ops_logic.validate_link_url(doc["status"], body.url)
    updated = ops_repo.update_project_fields(project_id, {"link_url": url})
    count = ops_repo.bulk_issue_counts([project_id]).get(project_id, 0)
    return _project_out(updated, count, caller)


@router.patch("/projects/{project_id}/status", response_model=schemas.Project)
def update_project_status(
    project_id: str,
    body: schemas.StatusInput,
    caller: Caller = Depends(get_caller),
):
    ops_logic.require_manager(caller.is_manager)
    _require_project(project_id)
    remark = ops_logic.validate_status_input(body.status, body.remark)
    updated = ops_repo.update_project_status(project_id, body.status, remark, caller.person)
    count = ops_repo.bulk_issue_counts([project_id]).get(project_id, 0)
    return _project_out(updated, count, caller)


@router.patch("/projects/{project_id}/plan", response_model=schemas.Project)
def update_project_plan(
    project_id: str,
    body: schemas.PlanInput,
    caller: Caller = Depends(get_caller),
):
    ops_logic.require_manager(caller.is_manager)
    doc = _require_project(project_id)
    ops_logic.check_project_plan_editable(doc["status"])
    changes = _plan_fields(body.model_dump(exclude_unset=True))
    updated = ops_repo.update_project_plan(project_id, changes)
    count = ops_repo.bulk_issue_counts([project_id]).get(project_id, 0)
    return _project_out(updated, count, caller)


@router.patch("/projects/{project_id}/assignees", response_model=schemas.Project)
def update_project_assignees(
    project_id: str,
    body: schemas.AssignInput,
    caller: Caller = Depends(get_caller),
    db: Session = Depends(get_db),
):
    ops_logic.require_manager(caller.is_manager)
    doc = _require_project(project_id)
    ops_logic.check_project_assignees_editable(doc["status"])
    usernames = ops_logic.validate_assignee_usernames(body.usernames)
    people = people_repo.resolve_people(db, usernames)
    updated = ops_repo.update_project_assignees(project_id, people)
    count = ops_repo.bulk_issue_counts([project_id]).get(project_id, 0)
    return _project_out(updated, count, caller)


@router.post("/projects/{project_id}/review", response_model=schemas.Project)
def submit_project_review(
    project_id: str,
    body: schemas.ReviewInput,
    caller: Caller = Depends(get_caller),
):
    doc = ops_repo.get_project(project_id)
    if doc is None:
        raise ops_logic.OpsNotFound(ops_logic.MSG_REVIEW_ITEM_NOT_FOUND)
    note = ops_logic.validate_review_input(doc["status"], body.result, body.note)
    review = {"result": body.result, "by": caller.person, "at": ops_repo.utc_now(), "note": note}
    updated = ops_repo.update_project_review(project_id, review)
    count = ops_repo.bulk_issue_counts([project_id]).get(project_id, 0)
    return _project_out(updated, count, caller)


@router.get("/projects/{project_id}/comments", response_model=List[schemas.Comment])
def list_project_comments(project_id: str, caller: Caller = Depends(get_caller)):
    _require_project(project_id)
    docs = ops_repo.list_comments("project", project_id)
    return [_comment_out(d, caller.employee_id) for d in docs]


@router.post("/projects/{project_id}/comments", response_model=schemas.Comment, status_code=201)
async def create_project_comment(
    project_id: str,
    request: Request,
    caller: Caller = Depends(get_caller),
):
    raw_body, files = await _parse_comment_input(request)
    reads = await _read_comment_images(files)
    return await run_in_threadpool(_create_comment_sync, "project", project_id, raw_body, reads, caller)


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

@router.get("/issues", response_model=List[schemas.ProjectIssue])
def list_issues(
    scope: Literal["mine", "all"] = Query("all"),
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    caller: Caller = Depends(get_caller),
):
    status = _validate_status_filter(status)
    mine_id = caller.employee_id if scope == "mine" else None
    docs = ops_repo.list_issues(mine_employee_id=mine_id, project_id=project_id, status=status)
    projects = ops_repo.bulk_projects_by_id([d["project_id"] for d in docs])
    out = []
    for d in docs:
        proj = projects.get(d["project_id"])
        title = proj["title"] if proj else d["project_id"]
        assignees = proj["assignees"] if proj else []
        out.append(_issue_out(d, title, assignees))
    return out


@router.post("/issues", status_code=201)
def create_issue(
    body: schemas.ProjectIssueInput,
    caller: Caller = Depends(get_caller),
):
    _require_project(body.project_id)
    fields = {"project_id": body.project_id, "description": body.description}
    doc = ops_repo.create_issue(fields, caller.person)
    return {"issue_id": doc["issue_id"]}


@router.patch("/issues/{issue_id}/status", response_model=schemas.ProjectIssue)
def update_issue_status(
    issue_id: str,
    body: schemas.StatusInput,
    caller: Caller = Depends(get_caller),
):
    ops_logic.require_manager(caller.is_manager)
    _require_issue(issue_id)
    remark = ops_logic.validate_status_input(body.status, body.remark)
    updated = ops_repo.update_issue_status(issue_id, body.status, remark, caller.person)
    proj = ops_repo.get_project(updated["project_id"])
    title = proj["title"] if proj else updated["project_id"]
    assignees = proj["assignees"] if proj else []
    return _issue_out(updated, title, assignees)


@router.post("/issues/{issue_id}/review", response_model=schemas.ProjectIssue)
def submit_issue_review(
    issue_id: str,
    body: schemas.ReviewInput,
    caller: Caller = Depends(get_caller),
):
    doc = ops_repo.get_issue(issue_id)
    if doc is None:
        raise ops_logic.OpsNotFound(ops_logic.MSG_REVIEW_ITEM_NOT_FOUND)
    note = ops_logic.validate_review_input(doc["status"], body.result, body.note)
    review = {"result": body.result, "by": caller.person, "at": ops_repo.utc_now(), "note": note}
    updated = ops_repo.update_issue_review(issue_id, review)
    proj = ops_repo.get_project(updated["project_id"])
    title = proj["title"] if proj else updated["project_id"]
    assignees = proj["assignees"] if proj else []
    return _issue_out(updated, title, assignees)


@router.get("/issues/{issue_id}/comments", response_model=List[schemas.Comment])
def list_issue_comments(issue_id: str, caller: Caller = Depends(get_caller)):
    _require_issue(issue_id)
    docs = ops_repo.list_comments("issue", issue_id)
    return [_comment_out(d, caller.employee_id) for d in docs]


@router.post("/issues/{issue_id}/comments", response_model=schemas.Comment, status_code=201)
async def create_issue_comment(
    issue_id: str,
    request: Request,
    caller: Caller = Depends(get_caller),
):
    raw_body, files = await _parse_comment_input(request)
    reads = await _read_comment_images(files)
    return await run_in_threadpool(_create_comment_sync, "issue", issue_id, raw_body, reads, caller)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@router.get("/tasks", response_model=List[schemas.ProjectTask])
def list_tasks(
    scope: Literal["mine", "all"] = Query("all"),
    project_id: Optional[str] = Query(None),
    caller: Caller = Depends(get_caller),
):
    mine_id = caller.employee_id if scope == "mine" else None
    docs = ops_repo.list_tasks(mine_employee_id=mine_id, project_id=project_id)
    return [_task_out(d, caller) for d in docs]


@router.post("/tasks", response_model=schemas.ProjectTask, status_code=201)
def create_task(
    body: schemas.ProjectTaskInput,
    caller: Caller = Depends(get_caller),
):
    ops_logic.require_manager(caller.is_manager)
    proj = _require_project(body.project_id)
    ops_logic.check_task_create_allowed(proj["status"])
    title = ops_logic.validate_task_title(body.title)
    fields = {"project_id": body.project_id, "title": title, "due_date": _date_str(body.due_date)}
    doc = ops_repo.create_task(fields, proj["title"], caller.person)
    return _task_out(doc, caller)


@router.patch("/tasks/{task_id}", response_model=schemas.ProjectTask)
def update_task(
    task_id: str,
    body: schemas.TaskEditInput,
    caller: Caller = Depends(get_caller),
):
    ops_logic.require_manager(caller.is_manager)
    task = _require_task(task_id)
    changes: Dict[str, Any] = {}
    if body.title is not None:
        ops_logic.check_task_title_editable(task["status"])
        changes["title"] = ops_logic.validate_task_title(body.title)
    if body.project_id is not None and body.project_id != task["project_id"]:
        ops_logic.check_task_editable(task["status"])
        proj = _require_project(body.project_id)
        ops_logic.check_task_move_target(proj["status"])
        changes["project_id"] = body.project_id
        changes["project_title"] = proj["title"]
    updated = ops_repo.update_task_fields(task_id, changes)
    return _task_out(updated, caller)


@router.patch("/tasks/{task_id}/status", response_model=schemas.ProjectTask)
def update_task_status(
    task_id: str,
    body: schemas.StatusInput,
    caller: Caller = Depends(get_caller),
):
    ops_logic.require_manager(caller.is_manager)
    _require_task(task_id)
    remark = ops_logic.validate_status_input(body.status, body.remark)
    updated = ops_repo.update_task_status(task_id, body.status, remark, caller.person)
    return _task_out(updated, caller)


@router.patch("/tasks/{task_id}/assignees", response_model=schemas.ProjectTask)
def update_task_assignees(
    task_id: str,
    body: schemas.AssignInput,
    caller: Caller = Depends(get_caller),
    db: Session = Depends(get_db),
):
    ops_logic.require_manager(caller.is_manager)
    task = _require_task(task_id)
    ops_logic.check_task_assignees_editable(task["status"])
    usernames = ops_logic.validate_assignee_usernames(body.usernames)
    usernames = ops_logic.exclude_owner(usernames, (task.get("owner") or {}).get("username"))
    people = people_repo.resolve_people(db, usernames)
    updated = ops_repo.update_task_assignees(task_id, people)
    return _task_out(updated, caller)


@router.patch("/tasks/{task_id}/plan", response_model=schemas.ProjectTask)
def update_task_plan(
    task_id: str,
    body: schemas.TaskPlanInput,
    caller: Caller = Depends(get_caller),
):
    ops_logic.require_manager(caller.is_manager)
    task = _require_task(task_id)
    ops_logic.check_task_plan_editable(task["status"])
    updated = ops_repo.update_task_plan(task_id, _date_str(body.due_date))
    return _task_out(updated, caller)


@router.patch("/tasks/{task_id}/note", response_model=schemas.ProjectTask)
def update_task_note(
    task_id: str,
    body: schemas.TaskNoteInput,
    caller: Caller = Depends(get_caller),
):
    task = _require_task(task_id)
    ops_logic.require_task_note_editor(task, caller.employee_id, caller.is_admin)
    updated = ops_repo.update_task_note(task_id, ops_logic.clean_task_note(body.note), caller.person)
    return _task_out(updated, caller)


@router.post("/tasks/{task_id}/attachments", response_model=schemas.Attachment, status_code=201)
def upload_task_image(
    task_id: str,
    file: UploadFile = File(...),
    caller: Caller = Depends(get_caller),
):
    """Pictures under the task note — images only, up to MAX_TASK_IMAGES."""
    task = _require_task(task_id)
    ops_logic.require_task_note_editor(task, caller.employee_id, caller.is_admin)
    data = file.file.read()
    mime_type = file.content_type or "application/octet-stream"
    ops_logic.check_attachment_file(mime_type, len(data))
    ops_logic.check_task_image(mime_type, len(task.get("attachments") or []))

    attachment_id = uuid.uuid4().hex
    safe_name = ops_logic.safe_file_name(file.filename or "image")
    key = ops_files.task_attachment_key(task["project_id"], task_id, attachment_id, safe_name)
    ops_files.upload_bytes(key, data, mime_type)

    attachment = {
        "attachment_id": attachment_id,
        "file_name": file.filename or safe_name,
        "mime_type": mime_type,
        "size": len(data),
        "s3_key": key,
        "uploaded_at": ops_repo.utc_now(),
        "uploaded_by": caller.person,
    }
    ops_repo.add_task_attachment(task_id, attachment)
    return _attachments_out([attachment])[0]


@router.delete("/tasks/{task_id}/attachments/{attachment_id}", response_model=schemas.ProjectTask)
def delete_task_image(task_id: str, attachment_id: str, caller: Caller = Depends(get_caller)):
    task = _require_task(task_id)
    ops_logic.require_task_note_editor(task, caller.employee_id, caller.is_admin)
    found = next((a for a in task.get("attachments") or [] if a["attachment_id"] == attachment_id), None)
    if found is None:
        raise ops_logic.OpsNotFound(ops_logic.MSG_ATTACHMENT_NOT_FOUND)
    updated = ops_repo.remove_task_attachment(task_id, attachment_id)
    try:
        ops_files.delete_objects([found["s3_key"]])
    except Exception:  # the record is gone already; a stray object in S3 is harmless
        pass
    return _task_out(updated, caller)


# ---------------------------------------------------------------------------
# Satisfaction surveys (menaIT /survey-ops)
# ---------------------------------------------------------------------------

def _survey_out(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "_id"}


@router.post("/surveys", response_model=schemas.SurveyResponse, status_code=201)
def submit_survey(body: schemas.SurveyInput, caller: Caller = Depends(get_caller)):
    """Signed in only; answering the same system again replaces the earlier answer.
    An OPS project must be in Review or Done; other systems (Apps Script list) are stored as-is."""
    system_id = body.system_id.strip()
    project_id = None
    system_name = (body.system_name or "").strip() or None
    if ops_logic.PROJECT_ID_RE.match(system_id):
        project = _require_project(system_id)
        ops_logic.check_project_surveyable(project["status"])
        project_id = system_id
        system_name = project["title"]
    fields = {
        "project_id": project_id,
        "system_name": system_name,
        "section2": ops_logic.clean_survey_ratings(body.section2, "section2"),
        "section3": ops_logic.clean_survey_ratings(body.section3, "section3"),
        "comment": (body.comment or "").strip() or None,
    }
    return _survey_out(ops_repo.upsert_survey(system_id, fields, caller.person))


@router.get("/surveys/mine", response_model=Optional[schemas.SurveyResponse])
def my_survey(system_id: str = Query(..., min_length=1, max_length=100), caller: Caller = Depends(get_caller)):
    """The caller's own earlier answer for this system (null if none) — the form pre-fills it for editing."""
    doc = ops_repo.get_survey(system_id.strip(), caller.employee_id)
    return _survey_out(doc) if doc else None


@router.get("/projects/{project_id}/surveys", response_model=schemas.SurveyResults)
def list_project_surveys(project_id: str, caller: Caller = Depends(get_caller)):
    ops_logic.require_manager(caller.is_manager)
    _require_project(project_id)
    docs = ops_repo.list_surveys(project_id)
    return {"project_id": project_id, **ops_logic.survey_summary(docs), "responses": [_survey_out(d) for d in docs]}


# ---------------------------------------------------------------------------
# Comments (generic actions by comment_id)
# ---------------------------------------------------------------------------

@router.patch("/comments/{comment_id}", response_model=schemas.Comment)
def update_comment(
    comment_id: str,
    body: schemas.CommentInput,
    caller: Caller = Depends(get_caller),
):
    """Text-only edit — images are unchanged here. An empty body is allowed only
    when the comment already has at least one image attachment to fall back on."""
    comment = _require_comment(comment_id)
    ops_logic.check_comment_author(comment["author"]["employee_id"], caller.employee_id)
    has_images = bool(comment.get("attachments"))
    cleaned_body = ops_logic.validate_comment_body(body.body, has_images=has_images)
    updated = ops_repo.update_comment(comment_id, cleaned_body)
    return _comment_out(updated, caller.employee_id)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(comment_id: str, caller: Caller = Depends(get_caller)):
    comment = _require_comment(comment_id)
    ops_logic.check_comment_author(comment["author"]["employee_id"], caller.employee_id)
    ops_repo.delete_comment(comment_id)
    keys = [a["s3_key"] for a in comment.get("attachments") or [] if a.get("s3_key")]
    if keys:
        try:
            ops_files.delete_objects(keys)
        except Exception:
            logger.warning("ops: failed to delete S3 objects for comment %s", comment_id, exc_info=True)
    return Response(status_code=204)


@router.put("/comments/{comment_id}/like", response_model=schemas.LikeState)
def like_comment(comment_id: str, caller: Caller = Depends(get_caller)):
    _require_comment(comment_id)
    updated = ops_repo.set_comment_like(comment_id, caller.employee_id, True)
    return _like_state(updated, caller.employee_id)


@router.delete("/comments/{comment_id}/like", response_model=schemas.LikeState)
def unlike_comment(comment_id: str, caller: Caller = Depends(get_caller)):
    _require_comment(comment_id)
    updated = ops_repo.set_comment_like(comment_id, caller.employee_id, False)
    return _like_state(updated, caller.employee_id)


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

@router.post("/attachments", response_model=schemas.Attachment, status_code=201)
def upload_attachment(
    ref_type: Literal["project", "issue"] = Form(...),
    ref_id: str = Form(...),
    file: UploadFile = File(...),
    caller: Caller = Depends(get_caller),
):
    data = file.file.read()
    size = len(data)
    mime_type = file.content_type or "application/octet-stream"
    ops_logic.check_attachment_file(mime_type, size)

    if ref_type == "project":
        doc = ops_repo.get_project(ref_id)
        if doc is None:
            raise ops_logic.OpsNotFound(ops_logic.MSG_PROJECT_NOT_FOUND)
        owner_employee_id = doc["requested_by"]["employee_id"]
    else:
        doc = ops_repo.get_issue(ref_id)
        if doc is None:
            raise ops_logic.OpsNotFound(ops_logic.MSG_ISSUE_NOT_FOUND)
        owner_employee_id = doc["reported_by"]["employee_id"]

    ops_logic.check_attachment_owner(owner_employee_id, caller.employee_id, caller.is_manager)

    attachment_id = uuid.uuid4().hex
    safe_name = ops_logic.safe_file_name(file.filename or "file")
    if ref_type == "project":
        key = ops_files.project_attachment_key(ref_id, attachment_id, safe_name)
    else:
        key = ops_files.issue_attachment_key(doc["project_id"], ref_id, attachment_id, safe_name)

    ops_files.upload_bytes(key, data, mime_type)

    now = ops_repo.utc_now()
    attachment = {
        "attachment_id": attachment_id,
        "file_name": file.filename or safe_name,
        "mime_type": mime_type,
        "size": size,
        "s3_key": key,
        "uploaded_at": now,
        "uploaded_by": caller.person,
    }
    if ref_type == "project":
        ops_repo.add_project_attachment(ref_id, attachment)
    else:
        ops_repo.add_issue_attachment(ref_id, attachment)

    return {
        "attachment_id": attachment_id,
        "file_name": attachment["file_name"],
        "mime_type": mime_type,
        "size": size,
        "url": ops_files.presigned_get_url(key) or "",
        "uploaded_at": now,
    }
