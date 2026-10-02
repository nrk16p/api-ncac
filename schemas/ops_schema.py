"""Pydantic v2 schemas — Group OPS (project request / project issue / task).

Mirrors menaIT-v2's app/ops/schema/ops.schema.json + app/ops/types.ts exactly
(snake_case, ISO-8601 datetimes, YYYY-MM-DD date strings). Keep both in sync.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    priority_reason: str = Field(min_length=1, max_length=2000)
    target_date: Optional[date] = None


class Project(BaseModel):
    project_id: str
    title: str
    objective: str
    requirement: str
    expected_benefit: str
    estimated_users: int
    user_groups: Optional[str] = None
    priority: Priority
    priority_reason: str
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
    issue_count: int = 0
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
    model_config = ConfigDict(extra="forbid")
    project_id: str
    title: str = Field(min_length=1, max_length=200)
    due_date: Optional[date] = None


class TaskEditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    project_id: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "TaskEditInput":
        if self.model_fields_set.isdisjoint({"title", "project_id"}):
            raise ValueError("ต้องระบุอย่างน้อย 1 ฟิลด์")
        return self


class TaskPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    due_date: Optional[date] = None


class ProjectTask(BaseModel):
    task_id: str
    project_id: str
    title: str
    due_date: Optional[date] = None

    project_title: str
    status: Status
    owner: Person
    assignees: List[Person] = Field(default_factory=list)
    status_history: List[StatusChange] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CommentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)


class Comment(BaseModel):
    comment_id: str
    ref_type: CommentRef
    ref_id: str
    author: Person
    body: str
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
