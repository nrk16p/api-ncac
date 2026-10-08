"""Pydantic v2 schemas — Group OPS (project request / project issue / task).

Mirrors menaIT-v2's app/ops/schema/ops.schema.json + app/ops/types.ts exactly
(snake_case, ISO-8601 datetimes, YYYY-MM-DD date strings). Keep both in sync.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Status = Literal["Open", "To-Do", "In Progress", "Review", "Done", "Reject"]
Priority = Literal["Critical", "High", "Medium", "Low"]
ReviewResult = Literal["passed", "changes_requested"]
CommentRef = Literal["project", "issue"]


class Person(BaseModel):
    employee_id: str
    name: str
    username: Optional[str] = None
    image_url: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None


class Attachment(BaseModel):
    attachment_id: str
    file_name: str
    mime_type: str
    size: int = Field(ge=0)
    url: str
    uploaded_at: Optional[datetime] = None


class Review(BaseModel):
    result: ReviewResult
    by: Person
    at: datetime
    note: Optional[str] = None


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: ReviewResult
    note: Optional[str] = Field(default=None, max_length=2000)


class StatusChange(BaseModel):
    status: Status
    changed_at: datetime
    changed_by: Person
    remark: Optional[str] = None


class StatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status
    remark: Optional[str] = Field(default=None, max_length=2000)


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planned_start: Optional[date] = None
    planned_end: Optional[date] = None
    progress: Optional[int] = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _at_least_one(self) -> "PlanInput":
        if self.model_fields_set.isdisjoint({"planned_start", "planned_end", "progress"}):
            raise ValueError("ต้องระบุอย่างน้อย 1 ฟิลด์")
        return self


class AssignInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usernames: List[str] = Field(default_factory=list)


class ProjectRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=200)
    objective: str = Field(min_length=1, max_length=5000)
    requirement: str = Field(min_length=1, max_length=20000)
    expected_benefit: str = Field(min_length=1, max_length=5000)
    estimated_users: int = Field(ge=1)
    user_groups: Optional[str] = Field(default=None, max_length=2000)
    priority: Priority
    target_date: Optional[date] = None


class ProjectEditInput(BaseModel):
    """PATCH /ops/projects/{id} — any subset of the request fields."""
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(default=None, min_length=3, max_length=200)
    objective: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    requirement: Optional[str] = Field(default=None, min_length=1, max_length=20000)
    expected_benefit: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    estimated_users: Optional[int] = Field(default=None, ge=1)
    user_groups: Optional[str] = Field(default=None, max_length=2000)
    priority: Optional[Priority] = None
    target_date: Optional[date] = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "ProjectEditInput":
        if not self.model_fields_set:
            raise ValueError("ต้องระบุอย่างน้อย 1 ฟิลด์")
        # only user_groups / target_date may be cleared
        for name in self.model_fields_set - {"user_groups", "target_date"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} ห้ามว่าง")
        return self


class ProjectTitleInput(BaseModel):
    """PATCH /ops/projects/{id}/title — OPS team / admin or can_edit, any status except Done."""
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=200)


class SurveyInput(BaseModel):
    """POST /ops/surveys — menaIT /survey-ops. system_id = an OPS project id (OPS-…) or a system
    from the Apps Script list; ratings are question id → 1..5, all 5 questions per section."""
    model_config = ConfigDict(extra="forbid")
    system_id: str = Field(min_length=1, max_length=100)
    system_name: Optional[str] = Field(default=None, max_length=300)
    section2: Dict[int, int]
    section3: Dict[int, int]
    comment: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("section2", "section3")
    @classmethod
    def _scores_1_to_5(cls, v: Dict[int, int]) -> Dict[int, int]:
        if any(not 1 <= score <= 5 for score in v.values()):
            raise ValueError("คะแนนต้องอยู่ระหว่าง 1–5")
        return v


class SurveyResponse(BaseModel):
    survey_id: str
    system_id: str
    system_name: Optional[str] = None
    project_id: Optional[str] = None
    respondent: Person
    section2: Dict[str, int]
    section3: Dict[str, int]
    comment: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SurveyResults(BaseModel):
    """GET /ops/projects/{id}/surveys — OPS team / admin."""
    project_id: str
    count: int
    average: Optional[float] = None
    section2_avg: List[Optional[float]]
    section3_avg: List[Optional[float]]
    responses: List[SurveyResponse]


class LinkInput(BaseModel):
    """PATCH /ops/projects/{id}/link — null clears it."""
    model_config = ConfigDict(extra="forbid")
    url: Optional[str] = Field(default=None, max_length=2000)


class Project(BaseModel):
    project_id: str
    title: str
    objective: str
    requirement: str
    expected_benefit: str
    estimated_users: int
    user_groups: Optional[str] = None
    priority: Priority
    target_date: Optional[date] = None

    status: Status
    requested_by: Person
    assignees: List[Person] = Field(default_factory=list)
    progress: Optional[int] = None
    planned_start: Optional[date] = None
    planned_end: Optional[date] = None
    attachments: List[Attachment] = Field(default_factory=list)
    status_history: List[StatusChange] = Field(default_factory=list)
    review: Optional[Review] = None
    # where the finished system is used — set by the OPS team once Done
    link_url: Optional[str] = None
    issue_count: int = 0
    # whether the caller may PATCH /ops/projects/{id} (ops_logic.can_edit_project)
    can_edit: bool = False
    # whether the caller may PATCH /ops/projects/{id}/title (ops_logic.can_rename_project)
    can_rename: bool = False
    created_at: datetime
    updated_at: datetime


class ProjectIssueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    description: str = Field(min_length=10, max_length=5000)


class ProjectIssue(BaseModel):
    issue_id: str
    project_id: str
    description: str
    project_title: str
    project_assignees: List[Person] = Field(default_factory=list)
    status: Status
    reported_by: Person
    attachments: List[Attachment] = Field(default_factory=list)
    status_history: List[StatusChange] = Field(default_factory=list)
    review: Optional[Review] = None
    created_at: datetime
    updated_at: datetime


class ProjectTaskInput(BaseModel):
    """POST /ops/tasks — OPS team. project_id null / omitted / blank = a standalone task
    ("คำร้อง (ไม่มีในโปรเจกต์เดิม)")."""
    model_config = ConfigDict(extra="forbid")
    project_id: Optional[str] = None
    title: str = Field(min_length=1, max_length=200)
    due_date: Optional[date] = None


class TaskRequestInput(BaseModel):
    """POST /ops/task-requests — any signed-in user asks for more work (พัฒนาเพิ่ม) on an
    accepted project, or standalone (project_id null). detail is rich-text HTML; it's sanitized and its ">= 10 visible chars" rule
    is checked in ops_logic.validate_task_request_detail."""
    model_config = ConfigDict(extra="forbid")
    project_id: Optional[str] = None  # null / omitted = standalone, not tied to a project
    title: str = Field(min_length=3, max_length=200)
    detail: str = Field(max_length=20000)  # sanitized HTML (TipTap), like Project.requirement
    priority: Priority
    target_date: Optional[date] = None


class TaskEditInput(BaseModel):
    """PATCH /ops/tasks/{id} — OPS team. project_id: another project moves it, an explicit
    null detaches it (standalone) — both until the task is Done / Reject. detail / priority /
    target_date: an explicit null clears it (model_fields_set); detail (sanitized HTML) has no minimum here, unlike
    TaskRequestInput — no visible text stores null."""
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    project_id: Optional[str] = None
    detail: Optional[str] = Field(default=None, max_length=20000)
    priority: Optional[Priority] = None
    target_date: Optional[date] = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "TaskEditInput":
        if self.model_fields_set.isdisjoint({"title", "project_id", "detail", "priority", "target_date"}):
            raise ValueError("ต้องระบุอย่างน้อย 1 ฟิลด์")
        return self


class TaskPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    due_date: Optional[date] = None


class TaskNoteInput(BaseModel):
    """PATCH /ops/tasks/{id}/note — empty / null clears it."""
    model_config = ConfigDict(extra="forbid")
    note: Optional[str] = Field(default=None, max_length=5000)


class ProjectTask(BaseModel):
    task_id: str
    # both null = a standalone task request (no project yet)
    project_id: Optional[str] = None
    title: str
    due_date: Optional[date] = None

    project_title: Optional[str] = None
    status: Status
    # null = requested by a user (TaskRequestInput) and nobody on the OPS team has taken it yet
    owner: Optional[Person] = None
    assignees: List[Person] = Field(default_factory=list)
    status_history: List[StatusChange] = Field(default_factory=list)
    # set only on a user's request (พัฒนาเพิ่ม); tasks created by the OPS team leave these empty
    requested_by: Optional[Person] = None
    detail: Optional[str] = None  # sanitized HTML (older tasks may hold plain text)
    priority: Optional[Priority] = None
    target_date: Optional[date] = None
    request_attachments: List[Attachment] = Field(default_factory=list)
    # the responsible people's work note + pictures (owner / co-assignees / admin edit them)
    note: Optional[str] = None
    note_updated_at: Optional[datetime] = None
    note_updated_by: Optional[Person] = None
    attachments: List[Attachment] = Field(default_factory=list)
    can_note: bool = False
    created_at: datetime
    updated_at: datetime


class CommentInput(BaseModel):
    """body may be empty here — the real "non-empty body OR has an image" rule is
    enforced in ops_logic.validate_comment_body, since a PATCH on a comment that
    already has an image attachment is allowed to clear the body entirely."""
    model_config = ConfigDict(extra="forbid")
    body: str = Field(default="", max_length=2000)


class Comment(BaseModel):
    comment_id: str
    ref_type: CommentRef
    ref_id: str
    author: Person
    body: str
    attachments: List[Attachment] = Field(default_factory=list)
    created_at: datetime
    edited_at: Optional[datetime] = None
    like_count: int = 0
    liked_by_me: bool = False


class LikeState(BaseModel):
    like_count: int
    liked_by_me: bool


class ApiError(BaseModel):
    error: str
    field_errors: Optional[dict] = None
