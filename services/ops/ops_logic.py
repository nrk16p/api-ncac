"""Pure permission + state rules for the OPS module (no DB, no FastAPI).

Mirrors the contract in menaIT-v2's app/ops/schema/ops.schema.json + app/ops/api.ts,
with one deliberate change from the mock: management actions (status / assignees /
plan on projects, issues and tasks; task create/edit) are gated on "is this person a
manager" instead of "is this person a project assignee / task owner" — assignees and
task owners are now just labels of who works on it, per the backend contract §Identity
& permissions. Everyone signed in may still read, create project requests / issues,
comment (own edit/delete), like, submit a review while Review, and upload attachments
to a project/issue they created.
"""
from __future__ import annotations

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
MSG_PROJECT_REJECTED_TASK_CREATE = "โปรเจกต์นี้ไม่อนุมัติ สร้าง Task ไม่ได้"
MSG_PROJECT_REJECTED_TASK_MOVE = "โปรเจกต์นี้ไม่อนุมัติ ย้าย Task เข้าไม่ได้"
MSG_TASK_NOT_OPEN = "แก้ไขชื่อและโปรเจกต์ได้เฉพาะ Task ที่ยัง Open"
MSG_TASK_CLOSED = "Task นี้ปิดแล้ว แก้ไขไม่ได้"
MSG_TASK_TITLE_REQUIRED = "กรุณาระบุชื่อ Task"
MSG_REVIEW_NOT_IN_REVIEW = "รายการนี้ไม่ได้อยู่ในขั้นรีวิว"
MSG_REJECT_REMARK_REQUIRED = "กรุณาระบุเหตุผลที่ไม่อนุมัติ"
MSG_CHANGES_REQUESTED_NOTE_REQUIRED = "กรุณาระบุรายละเอียดสิ่งที่ต้องแก้ไข"
MSG_ASSIGNEE_INVALID = "ผู้รับผิดชอบต้องเป็นสมาชิกทีม OPS เท่านั้น"
MSG_ATTACHMENT_TOO_LARGE = "ไฟล์แนบต้องมีขนาดไม่เกิน 10 MB"
MSG_ATTACHMENT_TYPE_INVALID = "ไม่รองรับไฟล์ประเภทนี้"
MSG_ATTACHMENT_OWN_ONLY = "แนบไฟล์ได้เฉพาะโปรเจกต์/ปัญหาที่ตัวเองสร้าง"
MSG_REF_TYPE_INVALID = "ref_type ต้องเป็น project หรือ issue"


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

def validate_status_input(status: str, remark: Optional[str]) -> Optional[str]:
    """Returns the cleaned remark (stripped, or None). Raises if Reject has no remark."""
    if status not in STATUSES:
        raise OpsError(f"สถานะไม่ถูกต้อง: {status}")
    remark = (remark or "").strip() or None
    if status == "Reject" and not remark:
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


def check_task_editable(task_status: str) -> None:
    """Title / project_id edits: only while the task is Open."""
    if task_status != "Open":
        raise OpsConflict(MSG_TASK_NOT_OPEN)


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
    """Task owner is never in the assignees list."""
    owner = (owner_username or "").strip().lower()
    return [u for u in usernames if u != owner]


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def check_comment_author(author_employee_id: str, caller_employee_id: str) -> None:
    if author_employee_id != caller_employee_id:
        raise OpsForbidden(MSG_COMMENT_OWN_ONLY)


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


def check_attachment_owner(owner_employee_id: str, caller_employee_id: str, is_mgr: bool) -> None:
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
