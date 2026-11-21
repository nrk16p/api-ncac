import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import services.finance.overdue_reminder as overdue
from routes.pipeline.pipeline_routes import router

URL = "/pipeline/finance/overdue-reminders"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PIPELINE_API_KEY", "k")
    monkeypatch.setattr(database, "SessionLocal", object())  # ไม่แตะ DB จริง
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_rejects_missing_or_wrong_key(client, monkeypatch):
    calls = []
    monkeypatch.setattr(overdue, "run_overdue_reminders", lambda f: calls.append(f))
    assert client.post(URL).status_code == 422
    assert client.post(URL, headers={"x-api-key": "bad"}).status_code == 401
    assert calls == []


def test_runs_job_once_with_session_factory(client, monkeypatch):
    calls = []
    monkeypatch.setattr(overdue, "run_overdue_reminders", lambda f: calls.append(f))
    r = client.post(URL, headers={"x-api-key": "k"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "result": None}
    assert calls == [database.SessionLocal]


def test_job_exception_becomes_500(client, monkeypatch):
    def boom(f):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(overdue, "run_overdue_reminders", boom)
    r = client.post(URL, headers={"x-api-key": "k"})
    assert r.status_code == 500
    assert "smtp down" in r.json()["detail"]
