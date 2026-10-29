"""Pydantic v2 request bodies — Access Review (ทบทวนสิทธิ์ผู้ใช้). Responses are plain dicts (see services)."""
from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Decision = Literal["keep", "remove"]


def _strip(v: Optional[str]) -> Optional[str]:
    return v.strip() if isinstance(v, str) else v


class LineIn(BaseModel):
    """unchecked_item_ids = the items the requester UNTICKED (ticked items are not sent). decision is only read
    for item-less roles (Email group); for roles with items it is derived (all unticked → remove)."""
    user_role_id: int
    unchecked_item_ids: List[int] = Field(default_factory=list, max_length=5000)
    decision: Optional[Decision] = None
    note: Optional[str] = Field(default=None, max_length=1000)


class LinesIn(BaseModel):
    requester_note: Optional[str] = Field(default=None, max_length=2000)
    lines: List[LineIn] = Field(default_factory=list, max_length=2000)


class SubmitIn(BaseModel):
    requester_note: Optional[str] = Field(default=None, max_length=2000)
    lines: Optional[List[LineIn]] = Field(default=None, max_length=2000)


class DecisionIn(BaseModel):
    action: Literal["approve", "reject"]
    comment: Optional[str] = Field(default=None, max_length=2000)


# ---------------------------- admin ----------------------------

class SystemIn(BaseModel):
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=1, max_length=255)
    is_active: bool = True

    _s = field_validator("code", "name", mode="before")(_strip)


class SystemUpdate(BaseModel):
    code: Optional[str] = Field(default=None, min_length=2, max_length=30, pattern=r"^[A-Z0-9_]+$")
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    is_active: Optional[bool] = None

    _s = field_validator("code", "name", mode="before")(_strip)


class RoleIn(BaseModel):
    system_id: int
    name: str = Field(min_length=1, max_length=500)

    _s = field_validator("name", mode="before")(_strip)


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=500)
    is_active: Optional[bool] = None

    _s = field_validator("name", mode="before")(_strip)


class RoleItemIn(BaseModel):
    category: str = Field(min_length=1, max_length=500)
    item: Optional[str] = Field(default=None, max_length=500)
    permission: Optional[str] = Field(default=None, max_length=255)

    _s = field_validator("category", "item", "permission", mode="before")(_strip)


class RoleItemUpdate(BaseModel):
    category: Optional[str] = Field(default=None, min_length=1, max_length=500)
    item: Optional[str] = Field(default=None, max_length=500)
    permission: Optional[str] = Field(default=None, max_length=255)

    _s = field_validator("category", "item", "permission", mode="before")(_strip)


class UserRoleIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)
    role_id: int
    account_name: Optional[str] = Field(default=None, max_length=255)

    _s = field_validator("employee_id", "account_name", mode="before")(_strip)


class UserRoleUpdate(BaseModel):
    account_name: Optional[str] = Field(default=None, max_length=255)
    is_active: Optional[bool] = None

    _s = field_validator("account_name", mode="before")(_strip)


class CycleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    is_open: bool = True

    _s = field_validator("name", mode="before")(_strip)


class CycleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_open: Optional[bool] = None

    _s = field_validator("name", mode="before")(_strip)
