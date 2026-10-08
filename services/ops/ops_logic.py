"""Pure permission + state rules for the OPS module (no DB, no FastAPI).

Mirrors the contract in menaIT-v2's app/ops/schema/ops.schema.json + app/ops/api.ts,
with one deliberate change from the mock: management actions (status / assignees /
plan on projects, issues and tasks; task create/edit) are gated on "is this person a
manager" instead of "is this person a project assignee / task owner" — assignees and
task owners are now just labels of who works on it, per the backend contract §Identity
& permissions. Everyone signed in may still read, create project requests / issues /
task requests (พัฒนาเพิ่ม, owner None until a manager takes it), comment (own edit/delete),
like, submit a review while Review, and upload attachments to a project/issue/task
request they created.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse
from typing import Iterable, List, Optional

try:
    import nh3
except ImportError:  # pragma: no cover - nh3 is a required dependency; only missing in a broken env
    nh3 = None

# ---------------------------------------------------------------------------
# OPS team / manager rule
# ---------------------------------------------------------------------------

OPS_TEAM = ["patcharapan.p", "narongkorn.a", "sutiwat.c", "kittaboon.l"]
_OPS_TEAM_SET = {u.lower() for u in OPS_TEAM}

# menaIT admin (role "a") is not stored anywhere — app/api/login/route.ts derives it from these on login.
# Keep both in sync.
ADMIN_DEPARTMENT_IDS = {21}
ADMIN_EMPLOYEE_IDS = {"680043", "670108"}

STATUSES = ("Open", "To-Do", "In Progress", "Review", "Done", "Reject")
CLOSED_STATUSES = ("Done", "Reject")
PRIORITIES = ("Critical", "High", "Medium", "Low")
REVIEW_RESULTS = ("passed", "changes_requested")

PROJECT_ID_RE = re.compile(r"^OPS-\d{4}-\d{3,}$")
ISSUE_ID_RE = re.compile(r"^ISS-\d{4}-\d{3,}$")
TASK_ID_RE = re.compile(r"^TSK-\d{4}-\d{3,}$")


# ---------------------------------------------------------------------------
# Thai error messages (reused from app/ops/api.ts where the mock already had one)
# ---------------------------------------------------------------------------

MSG_LOGIN_REQUIRED = "กรุณาเข้าสู่ระบบ"
MSG_MANAGER_ONLY = "สิทธิ์นี้สำหรับทีม OPS เท่านั้น"
MSG_PROJECT_NOT_FOUND = "ไม่พบโปรเจกต์"
MSG_ISSUE_NOT_FOUND = "ไม่พบรายการปัญหา"
MSG_TASK_NOT_FOUND = "ไม่พบ Task"
MSG_COMMENT_NOT_FOUND = "ไม่พบความคิดเห็น"
MSG_COMMENT_OWN_ONLY = "แก้ไขได้เฉพาะความคิดเห็นของตัวเอง"
MSG_REVIEW_ITEM_NOT_FOUND = "ไม่พบรายการ"
MSG_PROJECT_CLOSED_ASSIGNEES = "โปรเจกต์นี้ปิดแล้ว แก้ไขผู้รับผิดชอบไม่ได้"
MSG_PROJECT_CLOSED_PLAN = "โปรเจกต์นี้ปิดแล้ว แก้ไขแผนงานไม่ได้"
MSG_LINK_DONE_ONLY = "ใส่ลิงก์ได้เมื่อโปรเจกต์เป็น Done แล้ว"
MSG_LINK_INVALID = "ลิงก์ต้องขึ้นต้นด้วย http:// หรือ https://"
MSG_PROJECT_CLOSED_EDIT = "โปรเจกต์นี้ปิดแล้ว แก้ไขคำขอไม่ได้"
MSG_PROJECT_EDIT_FORBIDDEN = "แก้ไขคำขอได้เฉพาะผู้รับผิดชอบ (คำขอจากระบบ) หรือคนในแผนกเดียวกับผู้ยื่น"
MSG_PROJECT_DONE_TITLE = "โปรเจกต์นี้ Done แล้ว แก้ไขชื่อไม่ได้"
MSG_PROJECT_RENAME_FORBIDDEN = "แก้ไขชื่อโปรเจกต์ได้เฉพาะทีม OPS หรือผู้มีสิทธิ์แก้ไขคำขอ"
MSG_PROJECT_TITLE_TOO_SHORT = "ชื่อโปรเจกต์ต้องมีอย่างน้อย 3 ตัวอักษร"
MSG_SURVEY_NOT_READY = "ประเมินได้เมื่อโปรเจกต์อยู่ในขั้น Review หรือ Done"
MSG_SURVEY_INCOMPLETE = "กรุณาให้คะแนนให้ครบทุกข้อ"
MSG_PROJECT_REJECTED_TASK_CREATE = "โปรเจกต์นี้ไม่อนุมัติ สร้าง Task ไม่ได้"
MSG_PROJECT_REJECTED_TASK_MOVE = "โปรเจกต์นี้ไม่อนุมัติ ย้าย Task เข้าไม่ได้"
MSG_TASK_CLOSED_MOVE = "Task ที่ Done / Reject แล้ว ย้ายโปรเจกต์ไม่ได้"
MSG_TASK_DONE_TITLE = "Task นี้ Done แล้ว แก้ไขชื่อไม่ได้"
MSG_TASK_DONE_NOTE = "Task นี้ Done แล้ว แก้ไขโน้ตและรูปไม่ได้"
MSG_TASK_CLOSED = "Task นี้ปิดแล้ว แก้ไขไม่ได้"
MSG_TASK_TITLE_REQUIRED = "กรุณาระบุชื่อ Task"
MSG_REVIEW_NOT_IN_REVIEW = "รายการนี้ไม่ได้อยู่ในขั้นรีวิว"
MSG_REJECT_REMARK_REQUIRED = "กรุณาระบุเหตุผลที่ไม่อนุมัติ"
MSG_CHANGES_REQUESTED_NOTE_REQUIRED = "กรุณาระบุรายละเอียดสิ่งที่ต้องแก้ไข"
MSG_ASSIGNEE_INVALID = "ผู้รับผิดชอบต้องเป็นสมาชิกทีม OPS เท่านั้น"
MSG_ATTACHMENT_TOO_LARGE = "ไฟล์แนบต้องมีขนาดไม่เกิน 10 MB"
MSG_ATTACHMENT_TYPE_INVALID = "ไม่รองรับไฟล์ประเภทนี้"
MSG_ATTACHMENT_OWN_ONLY = "แนบไฟล์ได้เฉพาะโปรเจกต์/ปัญหา/คำขอพัฒนาเพิ่มที่ตัวเองสร้าง"
MSG_REF_TYPE_INVALID = "ref_type ต้องเป็น project, issue หรือ task"
MSG_COMMENT_EMPTY = "กรุณาพิมพ์ความคิดเห็นหรือแนบรูปภาพ"
MSG_COMMENT_BODY_TOO_LONG = "ความคิดเห็นต้องไม่เกิน 2000 ตัวอักษร"
MSG_COMMENT_TOO_MANY_IMAGES = "แนบรูปได้สูงสุด 4 รูปต่อความคิดเห็น"
MSG_COMMENT_IMAGE_TYPE_INVALID = "แนบได้เฉพาะไฟล์รูปภาพ"
MSG_UNSUPPORTED_CONTENT_TYPE = "รูปแบบข้อมูลไม่ถูกต้อง"
MSG_TASK_NOTE_FORBIDDEN = "แก้ไขโน้ตและรูปได้เฉพาะผู้รับผิดชอบ Task นี้"
MSG_TASK_IMAGE_ONLY = "แนบได้เฉพาะไฟล์รูปภาพ (PNG, JPEG, GIF, WebP)"
MSG_TASK_IMAGE_LIMIT = "แนบรูปได้สูงสุด 10 รูปต่อ Task"
MSG_ATTACHMENT_NOT_FOUND = "ไม่พบไฟล์แนบ"
MSG_TASK_REQUEST_PROJECT_OPEN = "โปรเจกต์นี้ยังไม่ได้รับเรื่อง กรุณาแก้ไขคำขอโปรเจกต์แทนการขอพัฒนาเพิ่ม"
MSG_TASK_REQUEST_PROJECT_REJECTED = "โปรเจกต์นี้ไม่อนุมัติ ขอพัฒนาเพิ่มไม่ได้"
MSG_TASK_DETAIL_TOO_SHORT = "รายละเอียดต้องมีอย่างน้อย 10 ตัวอักษร"
MSG_TASK_ALREADY_CLAIMED = "Task นี้มีผู้รับผิดชอบแล้ว"
MSG_TASK_CLOSED_CLAIM = "Task นี้ปิดแล้ว รับงานไม่ได้"
MSG_TASK_DONE_DETAIL = "Task นี้ Done แล้ว แก้ไขรายละเอียดไม่ได้"
MSG_TASK_DONE_REQUEST_FILES = "Task นี้ Done แล้ว แก้ไขไฟล์แนบไม่ได้"


# ---------------------------------------------------------------------------
# Errors — caught in routes/ops/ops_routes.py and turned into HTTPException
# ---------------------------------------------------------------------------

class OpsError(Exception):
    """Validation failure → HTTP 400 by default."""
    http_status = 400

    def __init__(self, message: str, field_errors: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.field_errors = field_errors

    def detail(self):
        if self.field_errors:
            return {"error": self.message, "field_errors": self.field_errors}
        return self.message


class OpsUnauthorized(OpsError):
    """Missing/unknown X-Employee-Id → HTTP 401."""
    http_status = 401


class OpsForbidden(OpsError):
    """Signed in, but not allowed to do this → HTTP 403."""
    http_status = 403


class OpsNotFound(OpsError):
    http_status = 404


class OpsConflict(OpsError):
    """Action not allowed in the current state → HTTP 409."""
    http_status = 409


# ---------------------------------------------------------------------------
# Manager rule
# ---------------------------------------------------------------------------

def is_manager(username: Optional[str], department_id: Optional[int], employee_id: Optional[str]) -> bool:
    if username and username.strip().lower() in _OPS_TEAM_SET:
        return True
    return department_id in ADMIN_DEPARTMENT_IDS or employee_id in ADMIN_EMPLOYEE_IDS


def require_manager(is_mgr: bool) -> None:
    if not is_mgr:
        raise OpsForbidden(MSG_MANAGER_ONLY)


# ---------------------------------------------------------------------------
# Editing a project request
# ---------------------------------------------------------------------------

# requested_by.employee_id of projects imported by the system (not filed by a user)
SYSTEM_EMPLOYEE_ID = "system"


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def can_edit_project(project: dict, caller_employee_id: str, caller_username: Optional[str], caller_department: Optional[str]) -> bool:
    """System-imported → the project's named assignees; filed by a user → anyone in the
    requester's department (requester included). Not once the project is Done/Reject."""
    if project.get("status") in CLOSED_STATUSES:
        return False
    requester = project.get("requested_by") or {}
    if requester.get("employee_id") == SYSTEM_EMPLOYEE_ID:
        me = _norm(caller_username)
        return bool(me) and any(_norm(a.get("username")) == me for a in project.get("assignees") or [])
    if requester.get("employee_id") == caller_employee_id:
        return True
    dept = _norm(caller_department)
    return bool(dept) and dept == _norm(requester.get("department"))


def can_rename_project(project: dict, is_mgr: bool, can_edit: bool) -> bool:
    """Title only: the OPS team / admin, or whoever may edit the request — any status except Done."""
    return project.get("status") != "Done" and (is_mgr or can_edit)


def require_project_renamable(project: dict, is_mgr: bool, can_edit: bool) -> None:
    if project.get("status") == "Done":
        raise OpsConflict(MSG_PROJECT_DONE_TITLE)
    if not can_rename_project(project, is_mgr, can_edit):
        raise OpsForbidden(MSG_PROJECT_RENAME_FORBIDDEN)


def validate_project_title(title: Optional[str]) -> str:
    title = (title or "").strip()
    if len(title) < 3:
        raise OpsError(MSG_PROJECT_TITLE_TOO_SHORT, field_errors={"title": MSG_PROJECT_TITLE_TOO_SHORT})
    return title


def validate_link_url(project_status: str, url: Optional[str]) -> Optional[str]:
    """Done projects only; http(s) with a host, or empty/None to clear."""
    if project_status != "Done":
        raise OpsConflict(MSG_LINK_DONE_ONLY)
    url = (url or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OpsError(MSG_LINK_INVALID, {"url": MSG_LINK_INVALID})
    return url


def require_project_editable(project: dict, caller_employee_id: str, caller_username: Optional[str], caller_department: Optional[str]) -> None:
    if project.get("status") in CLOSED_STATUSES:
        raise OpsConflict(MSG_PROJECT_CLOSED_EDIT)
    if not can_edit_project(project, caller_employee_id, caller_username, caller_department):
        raise OpsForbidden(MSG_PROJECT_EDIT_FORBIDDEN)


# ---------------------------------------------------------------------------
# Status / review state rules
# ---------------------------------------------------------------------------

def validate_status_input(status: str, remark: Optional[str], require_reject_remark: bool = True) -> Optional[str]:
    """Returns the cleaned remark (stripped, or None). Raises if Reject has no remark,
    unless require_reject_remark is False (a task's Reject = cancelled, no reason asked)."""
    if status not in STATUSES:
        raise OpsError(f"สถานะไม่ถูกต้อง: {status}")
    remark = (remark or "").strip() or None
    if status == "Reject" and require_reject_remark and not remark:
        raise OpsError(MSG_REJECT_REMARK_REQUIRED, field_errors={"remark": MSG_REJECT_REMARK_REQUIRED})
    return remark


def validate_review_input(current_status: str, result: str, note: Optional[str]) -> Optional[str]:
    """Returns the cleaned note (stripped, or None). Review is allowed only while
    current_status == 'Review' and never changes the status itself."""
    if result not in REVIEW_RESULTS:
        raise OpsError(f"ผลรีวิวไม่ถูกต้อง: {result}")
    if current_status != "Review":
        raise OpsConflict(MSG_REVIEW_NOT_IN_REVIEW)
    note = (note or "").strip() or None
    if result == "changes_requested" and not note:
        raise OpsError(MSG_CHANGES_REQUESTED_NOTE_REQUIRED, field_errors={"note": MSG_CHANGES_REQUESTED_NOTE_REQUIRED})
    return note


def check_project_assignees_editable(status: str) -> None:
    if status in CLOSED_STATUSES:
        raise OpsConflict(MSG_PROJECT_CLOSED_ASSIGNEES)


def check_project_plan_editable(status: str) -> None:
    if status in CLOSED_STATUSES:
        raise OpsConflict(MSG_PROJECT_CLOSED_PLAN)


def check_task_create_allowed(project_status: str) -> None:
    """Done is allowed (follow-up fixes on a delivered system); Reject is not."""
    if project_status == "Reject":
        raise OpsConflict(MSG_PROJECT_REJECTED_TASK_CREATE)


def check_task_move_target(project_status: str) -> None:
    if project_status == "Reject":
        raise OpsConflict(MSG_PROJECT_REJECTED_TASK_MOVE)


def check_task_project_movable(task_status: str) -> None:
    """Moving it to another project / attaching / detaching: any status except Done / Reject."""
    if task_status in CLOSED_STATUSES:
        raise OpsConflict(MSG_TASK_CLOSED_MOVE)


def check_task_title_editable(task_status: str) -> None:
    """Renaming: any status except Done (Reject included)."""
    if task_status == "Done":
        raise OpsConflict(MSG_TASK_DONE_TITLE)


def check_task_assignees_editable(task_status: str) -> None:
    if task_status in CLOSED_STATUSES:
        raise OpsConflict(MSG_TASK_CLOSED)


def check_task_plan_editable(task_status: str) -> None:
    if task_status in CLOSED_STATUSES:
        raise OpsConflict(MSG_TASK_CLOSED)


def validate_task_title(title: Optional[str]) -> str:
    title = (title or "").strip()
    if not title:
        raise OpsError(MSG_TASK_TITLE_REQUIRED, field_errors={"title": MSG_TASK_TITLE_REQUIRED})
    return title


# ---------------------------------------------------------------------------
# Task requests (พัฒนาเพิ่ม) — any signed-in user files a new task on an accepted
# project; it starts with owner = None until an OPS team member takes it (claim,
# or the first status move by a manager).
# ---------------------------------------------------------------------------

# detail is rich-text HTML from the same TipTap editor as a project's requirement —
# sanitized with sanitize_requirement_html; length rules count the visible text only.
# Old plain-text details are left as they are (no migration).
TASK_DETAIL_MIN = 10

_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"\s+")


def visible_text(html_text: Optional[str]) -> str:
    """Tags stripped, entities unescaped, whitespace collapsed — what the reader actually sees."""
    text = html.unescape(_TAG_RE.sub(" ", html_text or ""))
    return _SPACE_RE.sub(" ", text).strip()


def check_task_request_allowed(project_status: str) -> None:
    """Open = the OPS team hasn't accepted the project yet (edit the project request
    instead); Reject = not approved. Every other status, Done included, is fine."""
    if project_status == "Open":
        raise OpsConflict(MSG_TASK_REQUEST_PROJECT_OPEN)
    if project_status == "Reject":
        raise OpsConflict(MSG_TASK_REQUEST_PROJECT_REJECTED)


def validate_task_request_detail(detail: Optional[str]) -> str:
    """Sanitized HTML; refused unless its visible text is >= TASK_DETAIL_MIN chars
    (so "<p></p>" or "<p>abc</p>" doesn't pass)."""
    detail = (sanitize_requirement_html(detail or "") or "").strip()
    if len(visible_text(detail)) < TASK_DETAIL_MIN:
        raise OpsError(MSG_TASK_DETAIL_TOO_SHORT, field_errors={"detail": MSG_TASK_DETAIL_TOO_SHORT})
    return detail


def check_task_detail_editable(task_status: str) -> None:
    """OPS team editing detail / priority / target_date (any task): any status except Done."""
    if task_status == "Done":
        raise OpsConflict(MSG_TASK_DONE_DETAIL)


def clean_task_detail(detail: Optional[str]) -> Optional[str]:
    """OPS edit: sanitized HTML; no visible text → None (no 10-char minimum, unlike a user's request)."""
    detail = (sanitize_requirement_html(detail or "") or "").strip()
    return detail if visible_text(detail) else None


def check_task_request_files_editable(task_status: str) -> None:
    """Adding / removing the request files (requester or OPS team): not once the task is Done."""
    if task_status == "Done":
        raise OpsConflict(MSG_TASK_DONE_REQUEST_FILES)


def check_task_claimable(task: dict) -> None:
    if task.get("owner") is not None:
        raise OpsConflict(MSG_TASK_ALREADY_CLAIMED)
    if task.get("status") in CLOSED_STATUSES:
        raise OpsConflict(MSG_TASK_CLOSED_CLAIM)


# ---------------------------------------------------------------------------
# Task note + pictures — the people responsible for the task (owner / co-assignees)
# and menaIT admin; any status except Done (Bew's call 2026-10-07).
# ---------------------------------------------------------------------------

TASK_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_TASK_IMAGES = 10


def is_admin(department_id: Optional[int], employee_id: Optional[str]) -> bool:
    return department_id in ADMIN_DEPARTMENT_IDS or employee_id in ADMIN_EMPLOYEE_IDS


def can_note_task(task: dict, caller_employee_id: str, caller_is_admin: bool) -> bool:
    if task.get("status") == "Done":
        return False
    if caller_is_admin:
        return True
    people = [task.get("owner") or {}, *(task.get("assignees") or [])]
    return any(p.get("employee_id") == caller_employee_id for p in people)


def require_task_note_editor(task: dict, caller_employee_id: str, caller_is_admin: bool) -> None:
    if task.get("status") == "Done":
        raise OpsConflict(MSG_TASK_DONE_NOTE)
    if not can_note_task(task, caller_employee_id, caller_is_admin):
        raise OpsForbidden(MSG_TASK_NOTE_FORBIDDEN)


def clean_task_note(note: Optional[str]) -> Optional[str]:
    return (note or "").strip() or None


def check_task_image(mime_type: str, current_count: int) -> None:
    if mime_type not in TASK_IMAGE_MIME:
        raise OpsError(MSG_TASK_IMAGE_ONLY)
    if current_count >= MAX_TASK_IMAGES:
        raise OpsConflict(MSG_TASK_IMAGE_LIMIT)



# ---------------------------------------------------------------------------
# Satisfaction surveys (menaIT /survey-ops) — 2 sections × 5 questions, 1–5 each
# ---------------------------------------------------------------------------

SURVEY_QUESTION_IDS = (1, 2, 3, 4, 5)
SURVEY_STATUSES = ("Review", "Done")


def check_project_surveyable(project_status: str) -> None:
    if project_status not in SURVEY_STATUSES:
        raise OpsConflict(MSG_SURVEY_NOT_READY)


def clean_survey_ratings(ratings: dict, field: str) -> dict:
    """Every question answered → {"1": n, … "5": n} (Mongo keys must be strings)."""
    if sorted(int(k) for k in ratings) != list(SURVEY_QUESTION_IDS):
        raise OpsError(MSG_SURVEY_INCOMPLETE, field_errors={field: MSG_SURVEY_INCOMPLETE})
    return {str(int(k)): int(v) for k, v in sorted(ratings.items(), key=lambda kv: int(kv[0]))}


def survey_summary(docs: List[dict]) -> dict:
    """Averages per question and overall (1 decimal); None when nobody answered."""
    def avg(values: List[int]) -> Optional[float]:
        return round(sum(values) / len(values), 2) if values else None

    out: dict = {"count": len(docs)}
    every: List[int] = []
    for section in ("section2", "section3"):
        per_q = []
        for q in SURVEY_QUESTION_IDS:
            vals = [d[section][str(q)] for d in docs if str(q) in (d.get(section) or {})]
            every.extend(vals)
            per_q.append(avg(vals))
        out[f"{section}_avg"] = per_q
    out["average"] = avg(every)
    return out

# ---------------------------------------------------------------------------
# Assignees
# ---------------------------------------------------------------------------

def validate_assignee_usernames(usernames: Iterable[str]) -> List[str]:
    """Dedupes (case-insensitive) and checks every username is an OPS_TEAM member."""
    seen: List[str] = []
    for raw in usernames or []:
        u = (raw or "").strip().lower()
        if not u:
            continue
        if u not in _OPS_TEAM_SET:
            raise OpsError(MSG_ASSIGNEE_INVALID, field_errors={"usernames": MSG_ASSIGNEE_INVALID})
        if u not in seen:
            seen.append(u)
    return seen


def exclude_owner(usernames: Iterable[str], owner_username: Optional[str]) -> List[str]:
    """Task owner is never in the assignees list (owner None = unclaimed request → nothing to drop)."""
    owner = (owner_username or "").strip().lower()
    return [u for u in usernames if u != owner]


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def check_comment_author(author_employee_id: str, caller_employee_id: str) -> None:
    if author_employee_id != caller_employee_id:
        raise OpsForbidden(MSG_COMMENT_OWN_ONLY)


MAX_COMMENT_IMAGES = 4
# images only (no office docs / pdf / zip — those stay attachment-upload-only)
ALLOWED_COMMENT_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def validate_comment_body(body: Optional[str], has_images: bool) -> str:
    """Stripped body, <=2000 chars, non-empty unless at least one image backs it up
    (a newly-uploaded image on create, or an existing attachment on a PATCH edit)."""
    body = (body or "").strip()
    if len(body) > 2000:
        raise OpsError(MSG_COMMENT_BODY_TOO_LONG, field_errors={"body": MSG_COMMENT_BODY_TOO_LONG})
    if not body and not has_images:
        raise OpsError(MSG_COMMENT_EMPTY, field_errors={"body": MSG_COMMENT_EMPTY})
    return body


def check_comment_image_count(count: int) -> None:
    if count > MAX_COMMENT_IMAGES:
        raise OpsError(MSG_COMMENT_TOO_MANY_IMAGES)


def check_comment_image_file(mime_type: str, size: int) -> None:
    """Same size ceiling as check_attachment_file, but images only — comments never
    accept pdf/office/zip the way project & issue attachments do."""
    if size > MAX_ATTACHMENT_BYTES:
        raise OpsError(MSG_ATTACHMENT_TOO_LARGE)
    if mime_type not in ALLOWED_COMMENT_IMAGE_MIME:
        raise OpsError(MSG_COMMENT_IMAGE_TYPE_INVALID)


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

ALLOWED_ATTACHMENT_MIME = {
    # images
    "image/jpeg", "image/png", "image/gif", "image/webp",
    # pdf
    "application/pdf",
    # office docs
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    # text / csv
    "text/plain", "text/csv",
    # zip
    "application/zip", "application/x-zip-compressed",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def check_attachment_file(mime_type: str, size: int) -> None:
    if size > MAX_ATTACHMENT_BYTES:
        raise OpsError(MSG_ATTACHMENT_TOO_LARGE)
    if mime_type not in ALLOWED_ATTACHMENT_MIME:
        raise OpsError(MSG_ATTACHMENT_TYPE_INVALID)


def check_attachment_owner(owner_employee_id: Optional[str], caller_employee_id: str, is_mgr: bool) -> None:
    """owner_employee_id None (e.g. a task the OPS team created, no requester) → managers only."""
    if is_mgr:
        return
    if owner_employee_id != caller_employee_id:
        raise OpsForbidden(MSG_ATTACHMENT_OWN_ONLY)


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_file_name(name: str) -> str:
    """Strips any path, keeps only [A-Za-z0-9._-], collapses the rest to '_'."""
    base = (name or "file").strip().replace("\\", "/").split("/")[-1]
    base = _SAFE_NAME_RE.sub("_", base).strip("._") or "file"
    return base[:150]


# ---------------------------------------------------------------------------
# requirement HTML sanitizer — allow-list from ops.schema.json's description:
# p, ol/ul/li, strong, u, mark, a[href] (http/https/mailto only), span[style=font-size only], br
# ---------------------------------------------------------------------------

_ALLOWED_TAGS = {"p", "ol", "ul", "li", "strong", "u", "mark", "a", "span", "br"}
_ALLOWED_ATTRS = {"a": {"href"}, "span": {"style"}}
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}
_ALLOWED_STYLE_PROPERTIES = {"font-size"}


def sanitize_requirement_html(html: Optional[str]) -> Optional[str]:
    if html is None:
        return None
    if nh3 is None:  # pragma: no cover - nh3 is in requirements.txt; only hit in a broken env
        raise RuntimeError("nh3 is not installed — cannot sanitize requirement HTML")
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        clean_content_tags={"script", "style"},
        attributes=_ALLOWED_ATTRS,
        url_schemes=_ALLOWED_URL_SCHEMES,
        filter_style_properties=_ALLOWED_STYLE_PROPERTIES,
    )
