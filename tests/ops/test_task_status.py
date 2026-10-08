"""Task status: Reject = cancelled, no reason asked (projects / issues still need one)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.ops import ops_routes
from services.ops import ops_logic

ME = {"employee_id": "100", "name": "Owner", "username": "sutiwat.c"}


def test_reject_remark_required_by_default():
    with pytest.raises(ops_logic.OpsError):
        ops_logic.validate_status_input("Reject", "  ")
    assert ops_logic.validate_status_input("Reject", " why ") == "why"


def test_task_reject_without_remark():
    assert ops_logic.validate_status_input("Reject", None, require_reject_remark=False) is None


@pytest.fixture
def client(monkeypatch):
    task = {
        "_id": "TSK-2026-001", "task_id": "TSK-2026-001", "project_id": "OPS-2026-001", "title": "t",
        "project_title": "p", "status": "In Progress", "owner": ME, "assignees": [],
        "status_history": [], "created_at": "2026-10-07T00:00:00Z", "updated_at": "2026-10-07T00:00:00Z",
    }

    def update_status(task_id, status, remark, by):
        task.update(status=status, status_history=[{"status": status, "remark": remark, "changed_by": by, "changed_at": "2026-10-08T00:00:00Z"}])
        return task

    monkeypatch.setattr(ops_routes.ops_repo, "get_task", lambda tid: task)
    monkeypatch.setattr(ops_routes.ops_repo, "update_task_status", update_status)
    app = FastAPI()
    app.include_router(ops_routes.router)
    app.dependency_overrides[ops_routes.get_caller] = lambda: ops_routes.Caller(
        person=ME, employee_id=ME["employee_id"], username=ME["username"], is_manager=True)
    return TestClient(app)


def test_route_task_reject_without_remark(client):
    r = client.patch("/ops/tasks/TSK-2026-001/status", json={"status": "Reject"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "Reject"
