"""Task note + pictures: who may edit, and the route wiring (repo / S3 stubbed)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.ops import ops_routes
from services.ops import ops_logic

OWNER = {"employee_id": "100", "name": "Owner", "username": "sutiwat.c"}
HELPER = {"employee_id": "200", "name": "Helper", "username": "narongkorn.a"}
OTHER = {"employee_id": "300", "name": "Other", "username": "patcharapan.p"}


def _task(**extra):
    return {
        "_id": "TSK-2026-001", "task_id": "TSK-2026-001", "project_id": "OPS-2026-001", "title": "t",
        "project_title": "p", "status": "In Progress", "owner": OWNER, "assignees": [HELPER],
        "status_history": [], "created_at": "2026-10-07T00:00:00Z", "updated_at": "2026-10-07T00:00:00Z",
        **extra,
    }


@pytest.mark.parametrize("who,admin,ok", [(OWNER, False, True), (HELPER, False, True), (OTHER, False, False), (OTHER, True, True)])
def test_can_note_task(who, admin, ok):
    assert ops_logic.can_note_task(_task(), who["employee_id"], admin) is ok


def test_clean_task_note():
    assert ops_logic.clean_task_note("  hi \n") == "hi"
    assert ops_logic.clean_task_note("   ") is None
    assert ops_logic.clean_task_note(None) is None


def test_check_task_image():
    ops_logic.check_task_image("image/png", 0)
    with pytest.raises(ops_logic.OpsError):
        ops_logic.check_task_image("application/pdf", 0)
    with pytest.raises(ops_logic.OpsConflict):
        ops_logic.check_task_image("image/png", ops_logic.MAX_TASK_IMAGES)


@pytest.fixture
def client(monkeypatch):
    state = {"task": _task(attachments=[]), "caller": OWNER, "deleted": []}

    def caller():
        p = state["caller"]
        return ops_routes.Caller(person=p, employee_id=p["employee_id"], username=p["username"], is_manager=True)

    def update_note(task_id, note, by):
        state["task"] = {**state["task"], "note": note, "note_updated_by": by, "note_updated_at": "2026-10-07T01:00:00Z"}
        return state["task"]

    def add_att(task_id, a):
        state["task"] = {**state["task"], "attachments": [*state["task"]["attachments"], a]}
        return state["task"]

    def remove_att(task_id, aid):
        state["task"] = {**state["task"], "attachments": [a for a in state["task"]["attachments"] if a["attachment_id"] != aid]}
        return state["task"]

    repo = ops_routes.ops_repo
    monkeypatch.setattr(repo, "get_task", lambda task_id: state["task"])
    monkeypatch.setattr(repo, "update_task_note", update_note)
    monkeypatch.setattr(repo, "add_task_attachment", add_att)
    monkeypatch.setattr(repo, "remove_task_attachment", remove_att)
    monkeypatch.setattr(ops_routes.ops_files, "upload_bytes", lambda key, data, ct: None)
    monkeypatch.setattr(ops_routes.ops_files, "presigned_get_url", lambda key: f"https://s3/{key}")
    monkeypatch.setattr(ops_routes.ops_files, "delete_objects", lambda keys: state["deleted"].extend(keys))

    app = FastAPI()
    app.include_router(ops_routes.router)
    app.dependency_overrides[ops_routes.get_caller] = caller
    return TestClient(app), state


def test_note_roundtrip(client):
    c, state = client
    r = c.patch("/ops/tasks/TSK-2026-001/note", json={"note": "  เสร็จครึ่งนึง  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["note"] == "เสร็จครึ่งนึง"
    assert body["note_updated_by"]["employee_id"] == OWNER["employee_id"]
    assert body["can_note"] is True


def test_note_forbidden_for_non_responsible(client):
    c, state = client
    state["caller"] = OTHER
    r = c.patch("/ops/tasks/TSK-2026-001/note", json={"note": "x"})
    assert r.status_code == 403
    assert r.json()["error"] == ops_logic.MSG_TASK_NOTE_FORBIDDEN


def test_image_upload_and_delete(client):
    c, state = client
    state["caller"] = HELPER
    r = c.post("/ops/tasks/TSK-2026-001/attachments", files={"file": ("shot.png", b"\x89PNG", "image/png")})
    assert r.status_code == 201, r.text
    att = r.json()
    key = state["task"]["attachments"][0]["s3_key"]
    assert key.startswith("ops_project/OPS-2026-001/tasks/TSK-2026-001/")
    assert att["url"] == f"https://s3/{key}"

    r = c.delete(f"/ops/tasks/TSK-2026-001/attachments/{att['attachment_id']}")
    assert r.status_code == 200, r.text
    assert r.json()["attachments"] == []
    assert state["deleted"] == [key]


def test_image_upload_rejects_non_images(client):
    c, _ = client
    r = c.post("/ops/tasks/TSK-2026-001/attachments", files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 400


def test_done_task_locks_note_and_title():
    done = _task(status="Done")
    assert ops_logic.can_note_task(done, OWNER["employee_id"], True) is False
    with pytest.raises(ops_logic.OpsConflict):
        ops_logic.require_task_note_editor(done, OWNER["employee_id"], False)
    with pytest.raises(ops_logic.OpsConflict):
        ops_logic.check_task_title_editable("Done")
    for status in ("Open", "To-Do", "In Progress", "Review", "Reject"):
        ops_logic.check_task_title_editable(status)
        assert ops_logic.can_note_task(_task(status=status), HELPER["employee_id"], False) is True


def test_note_on_done_task_is_409(client):
    c, state = client
    state["task"] = {**state["task"], "status": "Done"}
    r = c.patch("/ops/tasks/TSK-2026-001/note", json={"note": "x"})
    assert r.status_code == 409
    assert r.json()["error"] == ops_logic.MSG_TASK_DONE_NOTE


def test_rename_and_move_in_progress_ok(client, monkeypatch):
    c, state = client
    monkeypatch.setattr(ops_routes.ops_repo, "update_task_fields", lambda task_id, ch: {**state["task"], **ch})
    r = c.patch("/ops/tasks/TSK-2026-001", json={"title": "ชื่อใหม่"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "ชื่อใหม่"
    monkeypatch.setattr(ops_routes.ops_repo, "get_project", lambda pid: {"project_id": pid, "title": "x", "status": "To-Do"})
    r = c.patch("/ops/tasks/TSK-2026-001", json={"project_id": "OPS-2026-002"})
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == "OPS-2026-002" and r.json()["project_title"] == "x"
