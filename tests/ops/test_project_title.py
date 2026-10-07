"""Project rename: OPS team / admin or can_edit, any status except Done (repo stubbed)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.ops import ops_routes
from services.ops import ops_logic

REQUESTER = {"employee_id": "500", "name": "Req", "username": "req.r", "department": "HR"}
OPS = {"employee_id": "100", "name": "Ops", "username": "sutiwat.c", "department": "Operation Support"}
OUTSIDER = {"employee_id": "900", "name": "Out", "username": "out.o", "department": "Sales"}


def _project(status="In Progress"):
    return {
        "_id": "OPS-2026-001", "project_id": "OPS-2026-001", "title": "เดิม", "objective": "o", "requirement": "r",
        "expected_benefit": "b", "estimated_users": 1, "priority": "Low", "status": status,
        "requested_by": REQUESTER, "assignees": [], "attachments": [], "status_history": [],
        "created_at": "2026-10-07T00:00:00Z", "updated_at": "2026-10-07T00:00:00Z",
    }


@pytest.mark.parametrize("status,mgr,can_edit,ok", [
    ("In Progress", True, False, True),
    ("Reject", True, False, True),
    ("Open", False, True, True),
    ("Open", False, False, False),
    ("Done", True, True, False),
])
def test_can_rename_project(status, mgr, can_edit, ok):
    assert ops_logic.can_rename_project({"status": status}, mgr, can_edit) is ok


@pytest.fixture
def client(monkeypatch):
    state = {"project": _project(), "caller": OPS}

    def caller():
        p = state["caller"]
        return ops_routes.Caller(person=p, employee_id=p["employee_id"], username=p["username"],
                                 is_manager=p is OPS)

    def update_fields(project_id, fields):
        state["project"] = {**state["project"], **fields}
        return state["project"]

    repo = ops_routes.ops_repo
    monkeypatch.setattr(repo, "get_project", lambda pid: state["project"])
    monkeypatch.setattr(repo, "update_project_fields", update_fields)
    monkeypatch.setattr(repo, "bulk_issue_counts", lambda ids: {})
    app = FastAPI()
    app.include_router(ops_routes.router)
    app.dependency_overrides[ops_routes.get_caller] = caller
    return TestClient(app), state


def test_ops_team_renames_in_progress_project(client):
    c, state = client
    r = c.patch("/ops/projects/OPS-2026-001/title", json={"title": "  ชื่อใหม่ของระบบ  "})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "ชื่อใหม่ของระบบ"
    assert r.json()["can_rename"] is True


def test_done_project_cannot_be_renamed(client):
    c, state = client
    state["project"] = _project("Done")
    r = c.patch("/ops/projects/OPS-2026-001/title", json={"title": "ชื่อใหม่"})
    assert r.status_code == 409
    assert r.json()["error"] == ops_logic.MSG_PROJECT_DONE_TITLE


def test_outsider_cannot_rename(client):
    c, state = client
    state["caller"] = OUTSIDER
    r = c.patch("/ops/projects/OPS-2026-001/title", json={"title": "ชื่อใหม่"})
    assert r.status_code == 403
