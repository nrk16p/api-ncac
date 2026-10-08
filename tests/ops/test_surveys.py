"""Satisfaction surveys → Mongo ops.surveys (repo stubbed)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.ops import ops_routes
from services.ops import ops_logic

OPS = {"employee_id": "100", "name": "Ops", "username": "sutiwat.c"}
USER = {"employee_id": "500", "name": "User", "username": "user.u"}
FULL = {str(q): 4 for q in range(1, 6)}


def test_clean_survey_ratings_requires_all_five():
    assert ops_logic.clean_survey_ratings({1: 5, 2: 4, 3: 3, 4: 2, 5: 1}, "section2") == {"1": 5, "2": 4, "3": 3, "4": 2, "5": 1}
    with pytest.raises(ops_logic.OpsError):
        ops_logic.clean_survey_ratings({1: 5, 2: 4}, "section2")


def test_survey_summary():
    docs = [
        {"section2": {"1": 5, "2": 5, "3": 5, "4": 5, "5": 5}, "section3": {"1": 3, "2": 3, "3": 3, "4": 3, "5": 3}},
        {"section2": {"1": 3, "2": 3, "3": 3, "4": 3, "5": 3}, "section3": {"1": 5, "2": 5, "3": 5, "4": 5, "5": 5}},
    ]
    s = ops_logic.survey_summary(docs)
    assert s["count"] == 2 and s["average"] == 4.0
    assert s["section2_avg"] == [4.0] * 5 and s["section3_avg"] == [4.0] * 5
    assert ops_logic.survey_summary([])["average"] is None


@pytest.fixture
def client(monkeypatch):
    state = {"status": "Review", "caller": USER, "store": {}}

    def caller():
        p = state["caller"]
        return ops_routes.Caller(person=p, employee_id=p["employee_id"], username=p["username"], is_manager=p is OPS)

    def upsert(system_id, fields, respondent):
        sid = f"{system_id}:{respondent['employee_id']}"
        old = state["store"].get(sid, {"created_at": "2026-10-07T00:00:00Z"})
        doc = {**old, **fields, "_id": sid, "survey_id": sid, "system_id": system_id, "respondent": respondent,
               "updated_at": "2026-10-07T01:00:00Z"}
        state["store"][sid] = doc
        return doc

    repo = ops_routes.ops_repo
    monkeypatch.setattr(repo, "get_project", lambda pid: {"project_id": pid, "title": "Mena IT-Service", "status": state["status"]})
    monkeypatch.setattr(repo, "upsert_survey", upsert)
    monkeypatch.setattr(repo, "list_surveys", lambda sid: [d for d in state["store"].values() if d["system_id"] == sid])
    app = FastAPI()
    app.include_router(ops_routes.router)
    app.dependency_overrides[ops_routes.get_caller] = caller
    return TestClient(app), state


def _post(c, **extra):
    return c.post("/ops/surveys", json={"system_id": "OPS-2026-003", "section2": FULL, "section3": FULL, **extra})


def test_submit_links_project_and_replaces_on_resubmit(client):
    c, state = client
    r = _post(c, comment="  ดีมาก  ")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["project_id"] == "OPS-2026-003" and body["system_name"] == "Mena IT-Service" and body["comment"] == "ดีมาก"
    _post(c, comment="แก้ความเห็น")
    assert len(state["store"]) == 1


def test_project_must_be_review_or_done(client):
    c, state = client
    state["status"] = "In Progress"
    assert _post(c).status_code == 409


def test_incomplete_or_out_of_range_rejected(client):
    c, _ = client
    assert _post(c, section2={"1": 5}).status_code == 400
    assert _post(c, section2={**FULL, "1": 9}).status_code == 400


def test_non_ops_system_is_stored_without_project(client):
    c, _ = client
    r = c.post("/ops/surveys", json={"system_id": "SYS-01", "system_name": "ERP", "section2": FULL, "section3": FULL})
    assert r.status_code == 201, r.text
    assert r.json()["project_id"] is None and r.json()["system_name"] == "ERP"


def test_results_for_ops_team_only(client):
    c, state = client
    _post(c)
    assert c.get("/ops/projects/OPS-2026-003/surveys").status_code == 403
    state["caller"] = OPS
    r = c.get("/ops/projects/OPS-2026-003/surveys")
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1 and r.json()["average"] == 4.0
    assert r.json()["responses"][0]["respondent"]["employee_id"] == USER["employee_id"]


def test_my_survey_returns_own_answer_or_null(client, monkeypatch):
    c, state = client
    monkeypatch.setattr(ops_routes.ops_repo, "get_survey", lambda sid, emp: state["store"].get(f"{sid}:{emp}"))
    r = c.get("/ops/surveys/mine", params={"system_id": "OPS-2026-003"})
    assert r.status_code == 200 and r.json() is None
    _post(c, comment="ครั้งแรก")
    r = c.get("/ops/surveys/mine", params={"system_id": "OPS-2026-003"})
    assert r.status_code == 200, r.text
    assert r.json()["comment"] == "ครั้งแรก" and r.json()["section2"]["1"] == 4
    state["caller"] = OPS
    assert c.get("/ops/surveys/mine", params={"system_id": "OPS-2026-003"}).json() is None
