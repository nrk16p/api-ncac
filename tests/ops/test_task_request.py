"""Task requests (พัฒนาเพิ่ม): any signed-in user files a new task on an accepted project;
the OPS team takes it (claim / first status move); the requester attaches files.

No real Mongo/S3: ops_repo._col is monkeypatched onto a tiny in-process `tasks`
collection (only the operators ops_repo actually uses), so the real create_task /
claim_task / list_tasks / add_task_request_attachment queries — including the
owner-null claim filter — run unmodified. Projects are a stubbed get_project, S3 is stubbed.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.ops import ops_routes
from services.ops import ops_logic, ops_repo

REQUESTER = {"employee_id": "500", "name": "Req", "username": "req.r", "department": "HR"}
STRANGER = {"employee_id": "900", "name": "Out", "username": "out.o", "department": "Sales"}
OPS1 = {"employee_id": "100", "name": "Ops1", "username": "sutiwat.c", "department": "Operation Support"}
OPS2 = {"employee_id": "200", "name": "Ops2", "username": "narongkorn.a", "department": "Operation Support"}

ACTIVE = "OPS-2026-001"
OPEN = "OPS-2026-002"
REJECTED = "OPS-2026-003"
PROJECTS = {
    ACTIVE: {"_id": ACTIVE, "project_id": ACTIVE, "title": "ระบบ A", "status": "In Progress"},
    OPEN: {"_id": OPEN, "project_id": OPEN, "title": "ระบบ B", "status": "Open"},
    REJECTED: {"_id": REJECTED, "project_id": REJECTED, "title": "ระบบ C", "status": "Reject"},
}

REQUEST = {"project_id": ACTIVE, "title": "เพิ่มรายงาน", "detail": "  ขอรายงานสรุปรายเดือน  ", "priority": "High",
           "target_date": "2026-11-01"}


# ---------------------------------------------------------------------------
# a minimal stand-in for the Mongo `tasks` collection
# ---------------------------------------------------------------------------

def _values(doc, path):
    cur = [doc]
    for part in path.split("."):
        nxt = []
        for c in cur:
            if isinstance(c, list):
                nxt.extend(e[part] for e in c if isinstance(e, dict) and part in e)
            elif isinstance(c, dict) and part in c:
                nxt.append(c[part])
        cur = nxt
    return cur


def _match(doc, query):
    for key, want in query.items():
        if key == "$or":
            if not any(_match(doc, q) for q in want):
                return False
            continue
        values = _values(doc, key)
        if isinstance(want, dict) and "$nin" in want:
            ok = all(v not in want["$nin"] for v in values)
        elif want is None:  # Mongo: null matches a null or missing field
            ok = not values or None in values
        else:
            ok = want in values
        if not ok:
            return False
    return True


class _Cursor(list):
    def sort(self, key, direction):
        return _Cursor(sorted(self, key=lambda d: d[key], reverse=direction < 0))


class FakeTasks:
    def __init__(self):
        self.docs = {}

    def insert_one(self, doc):
        self.docs[doc["_id"]] = doc

    def find_one(self, query):
        return next((d for d in self.docs.values() if _match(d, query)), None)

    def find(self, query):
        return _Cursor(d for d in self.docs.values() if _match(d, query))

    def find_one_and_update(self, query, update, return_document=None):
        doc = self.find_one(query)
        if doc is None:
            return None
        doc.update(update.get("$set", {}))
        for field, item in update.get("$push", {}).items():
            doc.setdefault(field, []).append(item)
        for field, cond in update.get("$pull", {}).items():
            doc[field] = [e for e in doc.get(field) or [] if not _match(e, cond)]
        return doc


@pytest.fixture
def env(monkeypatch):
    tasks = FakeTasks()
    state = {"caller": REQUESTER, "uploads": [], "deleted": [], "seq": 0}

    def next_task_id():
        state["seq"] += 1
        return f"TSK-2026-{state['seq']:03d}"

    def col(name):
        assert name == ops_repo.TASKS, name
        return tasks

    def caller():
        p = state["caller"]
        return ops_routes.Caller(
            person=p, employee_id=p["employee_id"], username=p["username"],
            is_manager=ops_logic.is_manager(p["username"], None, p["employee_id"]),
        )

    monkeypatch.setattr(ops_repo, "_col", col)
    monkeypatch.setattr(ops_repo, "next_task_id", next_task_id)
    monkeypatch.setattr(ops_repo, "get_project", lambda pid: PROJECTS.get(pid))
    monkeypatch.setattr(ops_routes.ops_files, "upload_bytes", lambda key, data, ct: state["uploads"].append(key))
    monkeypatch.setattr(ops_routes.ops_files, "presigned_get_url", lambda key: f"https://s3/{key}")
    monkeypatch.setattr(ops_routes.ops_files, "delete_objects", lambda keys: state["deleted"].extend(keys))

    app = FastAPI()
    app.include_router(ops_routes.router)
    app.dependency_overrides[ops_routes.get_caller] = caller
    return TestClient(app), state, tasks


def _file_request(c, state, as_who=REQUESTER, **overrides):
    state["caller"] = as_who
    r = c.post("/ops/task-requests", json={**REQUEST, **overrides})
    assert r.status_code == 201, r.text
    return r.json()["task_id"]


def _ops_task(owner=OPS1):
    """A task the OPS team created the old way (owner set, no requester)."""
    return ops_repo.create_task({"project_id": ACTIVE, "title": "งานทีม", "due_date": None}, "ระบบ A", owner)


# ---------------------------------------------------------------------------
# filing a request
# ---------------------------------------------------------------------------

def test_create_request_ok(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    doc = tasks.docs[task_id]
    assert doc["owner"] is None and doc["assignees"] == []
    assert doc["requested_by"] == REQUESTER
    assert doc["status"] == "Open"
    assert doc["detail"] == "ขอรายงานสรุปรายเดือน"
    assert doc["priority"] == "High" and doc["target_date"] == "2026-11-01"
    assert doc["status_history"][0]["changed_by"] == REQUESTER

    r = c.get("/ops/tasks", params={"project_id": ACTIVE})
    assert r.status_code == 200, r.text
    [out] = r.json()
    assert out["owner"] is None
    assert out["requested_by"]["employee_id"] == REQUESTER["employee_id"]
    assert out["project_title"] == "ระบบ A"
    assert out["request_attachments"] == [] and out["attachments"] == []
    assert out["can_note"] is False


def test_old_ops_task_still_serialises(env):
    c, state, tasks = env
    _ops_task()
    state["caller"] = OPS1
    [out] = c.get("/ops/tasks").json()
    assert out["owner"]["employee_id"] == OPS1["employee_id"]
    assert out["requested_by"] is None and out["detail"] is None and out["priority"] is None
    assert out["request_attachments"] == []


@pytest.mark.parametrize("project_id,msg", [
    (OPEN, ops_logic.MSG_TASK_REQUEST_PROJECT_OPEN),
    (REJECTED, ops_logic.MSG_TASK_REQUEST_PROJECT_REJECTED),
])
def test_request_refused_on_open_or_rejected_project(env, project_id, msg):
    c, _, tasks = env
    r = c.post("/ops/task-requests", json={**REQUEST, "project_id": project_id})
    assert r.status_code == 409
    assert r.json()["error"] == msg
    assert tasks.docs == {}


def test_request_on_missing_project_is_404(env):
    c, _, _ = env
    r = c.post("/ops/task-requests", json={**REQUEST, "project_id": "OPS-2026-999"})
    assert r.status_code == 404


def test_short_detail_refused(env):
    c, _, tasks = env
    r = c.post("/ops/task-requests", json={**REQUEST, "detail": "   สั้นไป    "})
    assert r.status_code == 400
    assert r.json()["field_errors"] == {"detail": ops_logic.MSG_TASK_DETAIL_TOO_SHORT}
    assert tasks.docs == {}


def test_request_rejects_unknown_fields(env):
    c, _, _ = env
    r = c.post("/ops/task-requests", json={**REQUEST, "owner": OPS1})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# taking the work
# ---------------------------------------------------------------------------

def test_claim_ok_then_twice_refused(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    tasks.docs[task_id]["assignees"] = [OPS1, OPS2]

    state["caller"] = OPS1
    r = c.post(f"/ops/tasks/{task_id}/claim")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["owner"]["employee_id"] == OPS1["employee_id"]
    assert [a["employee_id"] for a in body["assignees"]] == [OPS2["employee_id"]]

    state["caller"] = OPS2
    r = c.post(f"/ops/tasks/{task_id}/claim")
    assert r.status_code == 409
    assert r.json()["error"] == ops_logic.MSG_TASK_ALREADY_CLAIMED
    assert tasks.docs[task_id]["owner"]["employee_id"] == OPS1["employee_id"]


def test_claim_by_non_manager_refused(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    r = c.post(f"/ops/tasks/{task_id}/claim")
    assert r.status_code == 403
    assert r.json()["error"] == ops_logic.MSG_MANAGER_ONLY
    assert tasks.docs[task_id]["owner"] is None


def test_claim_closed_task_refused(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    tasks.docs[task_id]["status"] = "Reject"
    state["caller"] = OPS1
    r = c.post(f"/ops/tasks/{task_id}/claim")
    assert r.status_code == 409
    assert r.json()["error"] == ops_logic.MSG_TASK_CLOSED_CLAIM


def test_claim_is_atomic_in_repo(env):
    """The owner-null filter is what stops a second claimer that passed the route's pre-check."""
    c, state, tasks = env
    task_id = _file_request(c, state)
    assert ops_repo.claim_task(task_id, OPS1)["owner"] == OPS1
    assert ops_repo.claim_task(task_id, OPS2) is None
    assert ops_repo.claim_task(task_id, OPS2, status="To-Do") is None
    assert tasks.docs[task_id]["owner"] == OPS1


def test_claim_race_reports_conflict(env, monkeypatch):
    c, state, tasks = env
    task_id = _file_request(c, state)
    real_claim = ops_repo.claim_task

    def someone_else_first(tid, owner, **kw):
        real_claim(tid, OPS2)  # another manager wins between the pre-check and the update
        return real_claim(tid, owner, **kw)

    monkeypatch.setattr(ops_repo, "claim_task", someone_else_first)
    state["caller"] = OPS1
    r = c.post(f"/ops/tasks/{task_id}/claim")
    assert r.status_code == 409
    assert r.json()["error"] == ops_logic.MSG_TASK_ALREADY_CLAIMED
    assert tasks.docs[task_id]["owner"] == OPS2


def test_status_move_auto_claims(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    tasks.docs[task_id]["assignees"] = [OPS2]

    state["caller"] = OPS2
    r = c.patch(f"/ops/tasks/{task_id}/status", json={"status": "To-Do"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "To-Do"
    assert body["owner"]["employee_id"] == OPS2["employee_id"]
    assert body["assignees"] == []
    assert [h["status"] for h in body["status_history"]] == ["Open", "To-Do"]
    assert body["status_history"][-1]["changed_by"]["employee_id"] == OPS2["employee_id"]

    # once owned, another manager's move leaves the owner alone
    state["caller"] = OPS1
    r = c.patch(f"/ops/tasks/{task_id}/status", json={"status": "In Progress"})
    assert r.status_code == 200, r.text
    assert r.json()["owner"]["employee_id"] == OPS2["employee_id"]
    assert r.json()["status"] == "In Progress"


def test_status_move_by_non_manager_refused(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    r = c.patch(f"/ops/tasks/{task_id}/status", json={"status": "To-Do"})
    assert r.status_code == 403
    assert tasks.docs[task_id]["owner"] is None


def test_assignees_and_note_rules_with_no_owner():
    assert ops_logic.exclude_owner(["sutiwat.c", "narongkorn.a"], None) == ["sutiwat.c", "narongkorn.a"]
    task = {"status": "Open", "owner": None, "assignees": [OPS2], "requested_by": REQUESTER}
    assert ops_logic.can_note_task(task, OPS2["employee_id"], False) is True
    assert ops_logic.can_note_task(task, REQUESTER["employee_id"], False) is False
    assert ops_logic.can_note_task(task, STRANGER["employee_id"], True) is True


# ---------------------------------------------------------------------------
# scope=mine
# ---------------------------------------------------------------------------

def test_mine_scope_includes_requested_tasks(env):
    c, state, tasks = env
    requested = _file_request(c, state)
    ops_only = _ops_task()["task_id"]

    state["caller"] = REQUESTER
    ids = [t["task_id"] for t in c.get("/ops/tasks", params={"scope": "mine"}).json()]
    assert ids == [requested]

    state["caller"] = OPS1
    ids = [t["task_id"] for t in c.get("/ops/tasks", params={"scope": "mine"}).json()]
    assert ids == [ops_only]

    state["caller"] = STRANGER
    assert c.get("/ops/tasks", params={"scope": "mine"}).json() == []


# ---------------------------------------------------------------------------
# requester's attachments
# ---------------------------------------------------------------------------

def _upload(c, task_id, name="spec.pdf", data=b"%PDF-1.4", mime="application/pdf"):
    return c.post("/ops/attachments", data={"ref_type": "task", "ref_id": task_id}, files={"file": (name, data, mime)})


def test_requester_uploads_attachment(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    r = _upload(c, task_id)
    assert r.status_code == 201, r.text
    att = r.json()
    doc = tasks.docs[task_id]
    assert doc.get("attachments", []) == []
    [stored] = doc["request_attachments"]
    assert stored["s3_key"] == f"ops_project/{ACTIVE}/tasks/{task_id}/request/{att['attachment_id']}-spec.pdf"
    assert stored["uploaded_by"] == REQUESTER
    assert state["uploads"] == [stored["s3_key"]]
    assert att["url"] == f"https://s3/{stored['s3_key']}"

    [out] = c.get("/ops/tasks", params={"project_id": ACTIVE}).json()
    assert out["request_attachments"][0]["attachment_id"] == att["attachment_id"]
    assert out["request_attachments"][0]["url"] == f"https://s3/{stored['s3_key']}"
    assert out["attachments"] == []


def test_stranger_upload_refused(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    state["caller"] = STRANGER
    r = _upload(c, task_id)
    assert r.status_code == 403
    assert r.json()["error"] == ops_logic.MSG_ATTACHMENT_OWN_ONLY
    assert state["uploads"] == []
    assert tasks.docs[task_id]["request_attachments"] == []


def test_task_without_requester_is_manager_only(env):
    c, state, tasks = env
    task_id = _ops_task()["task_id"]
    state["caller"] = REQUESTER
    assert _upload(c, task_id).status_code == 403
    state["caller"] = OPS2
    assert _upload(c, task_id).status_code == 201
    assert len(tasks.docs[task_id]["request_attachments"]) == 1


def test_upload_to_missing_task_is_404(env):
    c, _, _ = env
    assert _upload(c, "TSK-2026-999").status_code == 404


def test_upload_on_done_task_refused(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    tasks.docs[task_id]["status"] = "Done"
    r = _upload(c, task_id)
    assert r.status_code == 409
    assert r.json()["error"] == ops_logic.MSG_TASK_DONE_REQUEST_FILES
    assert state["uploads"] == []


# ---------------------------------------------------------------------------
# removing a request file
# ---------------------------------------------------------------------------

def _uploaded(c, state, as_who=REQUESTER):
    task_id = _file_request(c, state)
    state["caller"] = as_who
    r = _upload(c, task_id)
    assert r.status_code == 201, r.text
    return task_id, r.json()["attachment_id"]


@pytest.mark.parametrize("who", [REQUESTER, OPS1])
def test_delete_request_file_by_requester_or_manager(env, who):
    c, state, tasks = env
    task_id, att_id = _uploaded(c, state)
    key = tasks.docs[task_id]["request_attachments"][0]["s3_key"]
    state["caller"] = who
    r = c.delete(f"/ops/tasks/{task_id}/request-attachments/{att_id}")
    assert r.status_code == 200, r.text
    assert r.json()["request_attachments"] == []
    assert tasks.docs[task_id]["request_attachments"] == []
    assert state["deleted"] == [key]


def test_delete_request_file_by_stranger_refused(env):
    c, state, tasks = env
    task_id, att_id = _uploaded(c, state)
    state["caller"] = STRANGER
    r = c.delete(f"/ops/tasks/{task_id}/request-attachments/{att_id}")
    assert r.status_code == 403
    assert len(tasks.docs[task_id]["request_attachments"]) == 1
    assert state["deleted"] == []


def test_delete_request_file_missing_is_404(env):
    c, state, _ = env
    task_id, _ = _uploaded(c, state)
    r = c.delete(f"/ops/tasks/{task_id}/request-attachments/nope")
    assert r.status_code == 404
    assert r.json()["error"] == ops_logic.MSG_ATTACHMENT_NOT_FOUND


def test_delete_request_file_on_done_task_refused(env):
    c, state, tasks = env
    task_id, att_id = _uploaded(c, state)
    tasks.docs[task_id]["status"] = "Done"
    state["caller"] = OPS1
    r = c.delete(f"/ops/tasks/{task_id}/request-attachments/{att_id}")
    assert r.status_code == 409
    assert r.json()["error"] == ops_logic.MSG_TASK_DONE_REQUEST_FILES
    assert state["deleted"] == []


def test_s3_delete_failure_does_not_fail_request(env, monkeypatch):
    c, state, tasks = env
    task_id, att_id = _uploaded(c, state)

    def boom(keys):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(ops_routes.ops_files, "delete_objects", boom)
    r = c.delete(f"/ops/tasks/{task_id}/request-attachments/{att_id}")
    assert r.status_code == 200, r.text
    assert tasks.docs[task_id]["request_attachments"] == []


# ---------------------------------------------------------------------------
# OPS team edits detail / priority / target_date on any task
# ---------------------------------------------------------------------------

def test_ops_edits_detail_sheet_on_old_task(env):
    c, state, tasks = env
    task_id = _ops_task()["task_id"]
    state["caller"] = OPS2
    r = c.patch(f"/ops/tasks/{task_id}", json={"detail": "  สั้น  ", "priority": "Critical", "target_date": "2026-12-31"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detail"] == "สั้น"  # no 10-char minimum for the OPS team
    assert body["priority"] == "Critical"
    assert body["target_date"] == "2026-12-31"
    assert tasks.docs[task_id]["target_date"] == "2026-12-31"
    assert body["title"] == "งานทีม"


def test_explicit_null_clears_and_omitted_fields_stay(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    state["caller"] = OPS1
    r = c.patch(f"/ops/tasks/{task_id}", json={"priority": None, "target_date": None})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["priority"] is None and body["target_date"] is None
    assert body["detail"] == "ขอรายงานสรุปรายเดือน"

    r = c.patch(f"/ops/tasks/{task_id}", json={"detail": "   "})
    assert r.status_code == 200, r.text
    assert r.json()["detail"] is None
    assert tasks.docs[task_id]["detail"] is None


def test_title_only_edit_leaves_detail_sheet(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    state["caller"] = OPS1
    r = c.patch(f"/ops/tasks/{task_id}", json={"title": "ชื่อใหม่"})
    assert r.status_code == 200, r.text
    assert r.json()["priority"] == "High" and r.json()["target_date"] == "2026-11-01"


def test_detail_edit_by_non_manager_refused(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    r = c.patch(f"/ops/tasks/{task_id}", json={"detail": "แก้เองได้ไหม"})
    assert r.status_code == 403
    assert tasks.docs[task_id]["detail"] == "ขอรายงานสรุปรายเดือน"


def test_detail_edit_on_done_task_refused(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    tasks.docs[task_id]["status"] = "Done"
    state["caller"] = OPS1
    r = c.patch(f"/ops/tasks/{task_id}", json={"priority": "Low"})
    assert r.status_code == 409
    assert r.json()["error"] == ops_logic.MSG_TASK_DONE_DETAIL
    assert tasks.docs[task_id]["priority"] == "High"


@pytest.mark.parametrize("payload", [
    {},
    {"priority": "Urgent"},
    {"detail": "x" * 20001},
    {"target_date": "not-a-date"},
])
def test_detail_edit_validation(env, payload):
    c, state, _ = env
    task_id = _file_request(c, state)
    state["caller"] = OPS1
    assert c.patch(f"/ops/tasks/{task_id}", json=payload).status_code == 400


# ---------------------------------------------------------------------------
# detail is sanitized rich-text HTML (same TipTap editor / sanitizer as requirement)
# ---------------------------------------------------------------------------

def test_visible_text():
    assert ops_logic.visible_text("<p>a&nbsp;&amp;<br>  b</p>\n<ul><li>c</li></ul>") == "a & b c"
    assert ops_logic.visible_text("<p></p><p><br></p>") == ""
    assert ops_logic.visible_text(None) == ""
    assert ops_logic.visible_text("plain old text") == "plain old text"


def test_request_detail_html_is_sanitized(env):
    c, state, tasks = env
    html_in = ('<script>alert(1)</script><p onclick="x()">ขอรายงาน <strong>สรุป</strong>รายเดือน</p>'
               '<img src=x onerror=alert(1)><a href="javascript:alert(1)">ลิงก์</a>')
    task_id = _file_request(c, state, detail=html_in)
    stored = tasks.docs[task_id]["detail"]
    assert "<script" not in stored and "alert" not in stored
    assert "onclick" not in stored and "<img" not in stored and "javascript:" not in stored
    assert "<p>ขอรายงาน <strong>สรุป</strong>รายเดือน</p>" in stored
    [out] = c.get("/ops/tasks", params={"project_id": ACTIVE}).json()
    assert out["detail"] == stored


@pytest.mark.parametrize("detail", [
    "<p></p>",
    "<p>abc</p>",
    "<p><br></p><p>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</p>",
    "<p>   a    b   c   </p>",
    "<script>this text is long but never visible</script><p>สั้น</p>",
])
def test_request_detail_counts_visible_text(env, detail):
    c, _, tasks = env
    r = c.post("/ops/task-requests", json={**REQUEST, "detail": detail})
    assert r.status_code == 400, r.text
    assert r.json()["field_errors"] == {"detail": ops_logic.MSG_TASK_DETAIL_TOO_SHORT}
    assert tasks.docs == {}


def test_request_detail_long_html_accepted(env):
    c, state, tasks = env
    detail = "<p>" + "ก" * 15000 + "</p>"  # over the old 5000 cap, under 20000
    task_id = _file_request(c, state, detail=detail)
    assert tasks.docs[task_id]["detail"] == detail
    r = c.post("/ops/task-requests", json={**REQUEST, "detail": "x" * 20001})
    assert r.status_code == 400


def test_patch_detail_html_sanitized_and_empty_is_null(env):
    c, state, tasks = env
    task_id = _ops_task()["task_id"]
    state["caller"] = OPS1
    r = c.patch(f"/ops/tasks/{task_id}", json={"detail": "<p>สั้น</p><script>alert(1)</script>"})
    assert r.status_code == 200, r.text
    assert r.json()["detail"] == "<p>สั้น</p>"
    for empty in ("<p></p>", "<p><br></p>", "<p>&nbsp; </p>"):
        r = c.patch(f"/ops/tasks/{task_id}", json={"detail": empty})
        assert r.status_code == 200, r.text
        assert r.json()["detail"] is None
        assert tasks.docs[task_id]["detail"] is None


def test_old_plain_text_detail_reads_as_is(env):
    c, state, tasks = env
    task_id = _file_request(c, state)
    tasks.docs[task_id]["detail"] = "ข้อความเดิม a < b & c\nบรรทัดสอง"  # written before HTML, never migrated
    [out] = c.get("/ops/tasks", params={"project_id": ACTIVE}).json()
    assert out["detail"] == "ข้อความเดิม a < b & c\nบรรทัดสอง"
