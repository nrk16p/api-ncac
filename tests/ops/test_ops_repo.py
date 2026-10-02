"""Mongo repo smoke tests — uses mongomock, never touches a real MongoDB.

Optional per the task contract, kept light: one test per behaviour that's easy
to get subtly wrong (atomic id counters, the issue_count aggregate excluding
Done/Reject, like idempotency) rather than full CRUD coverage — that's already
exercised indirectly by whatever hits routes/ops/ops_routes.py in the real app.
"""
from datetime import datetime, timezone

import mongomock
import pytest
import pytz

from services.ops import ops_repo as repo


@pytest.fixture(autouse=True)
def _fresh_mongo(monkeypatch):
    client = mongomock.MongoClient(tz_aware=True)
    monkeypatch.setattr(repo, "get_mongo_db", lambda name: client[name])
    monkeypatch.setattr(repo, "_indexes_ready", False)
    yield


PERSON_A = {"employee_id": "E1", "name": "A", "username": "kittaboon.l", "image_url": None,
            "department": None, "position": None}
PERSON_B = {"employee_id": "E2", "name": "B", "username": "sutiwat.c", "image_url": None,
            "department": None, "position": None}


def _year():
    return datetime.now(pytz.timezone("Asia/Bangkok")).year


class TestIds:
    def test_next_project_id_increments_padded(self):
        year = _year()
        assert repo.next_project_id() == f"OPS-{year}-001"
        assert repo.next_project_id() == f"OPS-{year}-002"

    def test_next_issue_and_task_ids_have_independent_counters(self):
        year = _year()
        assert repo.next_issue_id() == f"ISS-{year}-001"
        assert repo.next_task_id() == f"TSK-{year}-001"
        assert repo.next_issue_id() == f"ISS-{year}-002"

    def test_id_grows_beyond_three_digits(self):
        for _ in range(1000):
            last = repo.next_project_id()
        assert last.endswith("-1000")


class TestProjectCrud:
    def test_create_and_get_roundtrip(self):
        fields = {
            "title": "t", "objective": "o", "requirement": "<p>r</p>", "expected_benefit": "b",
            "estimated_users": 5, "user_groups": None, "priority": "High", "priority_reason": "x",
            "target_date": None,
        }
        doc = repo.create_project(fields, PERSON_A)
        assert doc["status"] == "Open"
        assert doc["status_history"] == [
            {"status": "Open", "changed_at": doc["created_at"], "changed_by": PERSON_A, "remark": None}
        ]
        fetched = repo.get_project(doc["project_id"])
        assert fetched["_id"] == doc["project_id"]

    def test_get_missing_returns_none(self):
        assert repo.get_project("OPS-2026-999") is None

    def test_list_projects_mine_filters_by_requester(self):
        fields = {"title": "t", "objective": "o", "requirement": "r", "expected_benefit": "b",
                  "estimated_users": 1, "user_groups": None, "priority": "Low", "priority_reason": "x",
                  "target_date": None}
        repo.create_project(fields, PERSON_A)
        repo.create_project(fields, PERSON_B)
        mine = repo.list_projects(mine_employee_id="E1", status=None)
        assert len(mine) == 1
        assert mine[0]["requested_by"]["employee_id"] == "E1"
        assert len(repo.list_projects(mine_employee_id=None, status=None)) == 2

    def test_update_status_appends_history_and_sets_status(self):
        fields = {"title": "t", "objective": "o", "requirement": "r", "expected_benefit": "b",
                  "estimated_users": 1, "user_groups": None, "priority": "Low", "priority_reason": "x",
                  "target_date": None}
        doc = repo.create_project(fields, PERSON_A)
        updated = repo.update_project_status(doc["project_id"], "Reject", "ไม่อนุมัติ", PERSON_B)
        assert updated["status"] == "Reject"
        assert len(updated["status_history"]) == 2
        assert updated["status_history"][-1]["remark"] == "ไม่อนุมัติ"

    def test_update_plan_sets_only_given_fields(self):
        fields = {"title": "t", "objective": "o", "requirement": "r", "expected_benefit": "b",
                  "estimated_users": 1, "user_groups": None, "priority": "Low", "priority_reason": "x",
                  "target_date": None}
        doc = repo.create_project(fields, PERSON_A)
        updated = repo.update_project_plan(doc["project_id"], {"planned_end": "2027-01-01"})
        assert updated["planned_end"] == "2027-01-01"
        assert updated["planned_start"] is None


class TestIssueCounts:
    def test_excludes_done_and_reject(self):
        pfields = {"title": "t", "objective": "o", "requirement": "r", "expected_benefit": "b",
                   "estimated_users": 1, "user_groups": None, "priority": "Low", "priority_reason": "x",
                   "target_date": None}
        project = repo.create_project(pfields, PERSON_A)
        pid = project["project_id"]
        ifields = {"project_id": pid, "description": "a problem description here"}
        open_issue = repo.create_issue(ifields, PERSON_A)
        done_issue = repo.create_issue(ifields, PERSON_A)
        repo.update_issue_status(done_issue["issue_id"], "Done", None, PERSON_A)

        counts = repo.bulk_issue_counts([pid])
        assert counts[pid] == 1  # only the still-open one counts

    def test_no_issues_project_absent_from_counts(self):
        assert repo.bulk_issue_counts(["OPS-2026-999"]) == {}

    def test_empty_input_short_circuits(self):
        assert repo.bulk_issue_counts([]) == {}


class TestComments:
    def test_create_list_oldest_first(self):
        c1 = repo.create_comment("project", "OPS-2026-001", "first", PERSON_A)
        c2 = repo.create_comment("project", "OPS-2026-001", "second", PERSON_B)
        out = repo.list_comments("project", "OPS-2026-001")
        assert [c["comment_id"] for c in out] == [c1["comment_id"], c2["comment_id"]]

    def test_like_is_idempotent_and_reversible(self):
        c = repo.create_comment("issue", "ISS-2026-001", "hi", PERSON_A)
        repo.set_comment_like(c["comment_id"], "E2", True)
        repo.set_comment_like(c["comment_id"], "E2", True)  # liking twice doesn't double-count
        doc = repo.get_comment(c["comment_id"])
        assert doc["liked_by"] == ["E2"]

        repo.set_comment_like(c["comment_id"], "E2", False)
        doc = repo.get_comment(c["comment_id"])
        assert doc["liked_by"] == []

    def test_unlike_when_not_liked_is_a_noop(self):
        c = repo.create_comment("issue", "ISS-2026-002", "hi", PERSON_A)
        repo.set_comment_like(c["comment_id"], "E9", False)
        doc = repo.get_comment(c["comment_id"])
        assert doc["liked_by"] == []

    def test_delete_comment(self):
        c = repo.create_comment("issue", "ISS-2026-003", "hi", PERSON_A)
        repo.delete_comment(c["comment_id"])
        assert repo.get_comment(c["comment_id"]) is None


class TestTasks:
    def test_create_and_list_mine_by_owner_or_assignee(self):
        task = repo.create_task({"project_id": "OPS-2026-001", "title": "t", "due_date": None},
                                 "Project Title", PERSON_A)
        repo.update_task_assignees(task["task_id"], [PERSON_B])

        mine_a = repo.list_tasks(mine_employee_id="E1", project_id=None)
        mine_b = repo.list_tasks(mine_employee_id="E2", project_id=None)
        mine_c = repo.list_tasks(mine_employee_id="E3", project_id=None)
        assert len(mine_a) == 1  # owner
        assert len(mine_b) == 1  # assignee
        assert len(mine_c) == 0
