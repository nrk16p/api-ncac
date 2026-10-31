import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.atms import withdraw as w
from services.atms_openjob_service import AtmsAuthError, AtmsNotFoundError, AtmsOpenJobError
from services.atms_withdraw_service import AtmsWithdrawClient, IssueProgress, WithdrawPartialError

KEY = {"x-api-key": "k"}
CREDS = {"X-ATMS-Username": "u1", "X-ATMS-Password": "pw"}
LINE = {"sku_id": 1, "amount": 2, "sku_name": "กรอง"}
ISSUE = {"inventory_id": 4, "pick_to_id": 9, "remark_token": "SW-1", "lines": [LINE]}
RETURN = {"inventory_id": 4, "withdraw_id": 5, "withdraw_code": "WD1", "remark_token": "SW-1",
          "lines": [{"sku_id": 1, "amount": 1, "unit_price": 3, "sku_name": "x"}]}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("PIPELINE_API_KEY", "k")
    w._cache.clear()
    w._user_locks.clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(w.router)
    return TestClient(app)


class Fake:
    made = []
    behaviour = "ok"

    def __init__(self, **kw):
        self.kw = kw
        Fake.made.append(self)

    def options(self):
        return {"o": 1}

    def lookup(self, kind, q, limit, inventory_id, sku_id):
        return [{"kind": kind, "q": q, "limit": limit, "inv": inventory_id}]

    def get_withdraw(self, gm_id):
        if Fake.behaviour == "404":
            raise AtmsNotFoundError("none")
        return {"id": gm_id}

    def list_withdraws(self, inv, d):
        return [{"inv": inv, "d": d}]

    def issue_withdraw(self, *a, **kw):
        if Fake.behaviour == "partial":
            raise WithdrawPartialError(IssueProgress(gm_id=560800, done=[0]), RuntimeError("boom"))
        if Fake.behaviour == "auth":
            raise AtmsAuthError("expired")
        if Fake.behaviour == "422":
            raise AtmsOpenJobError("bad")
        return IssueProgress(gm_id=1, done=[0], approved=True, code="WD9")

    def return_to_stock(self, *a, **kw):
        return IssueProgress(gm_id=2, approved=True)


@pytest.fixture
def fake(monkeypatch):
    Fake.made, Fake.behaviour = [], "ok"
    monkeypatch.setattr(w, "AtmsWithdrawClient", Fake)
    return Fake


def test_missing_api_key_403(client):
    r = client.get("/atms/withdraw/options")
    assert r.status_code == 403 and r.json() == {"detail": "api key ไม่ถูกต้อง"}
    r = client.get("/atms/withdraw/options", headers={"x-api-key": "wrong"} | CREDS)
    assert r.status_code == 403 and r.json() == {"detail": "api key ไม่ถูกต้อง"}


def test_api_key_unset_env_is_403(client, monkeypatch):
    monkeypatch.delenv("PIPELINE_API_KEY")
    assert client.get("/atms/withdraw/options", headers=KEY | CREDS).status_code == 403


def test_no_atms_creds_401(client, fake):
    assert client.get("/atms/withdraw/options", headers=KEY).status_code == 401
    assert client.get("/atms/withdraw/options", headers=KEY | {"X-ATMS-Username": "u"}).status_code == 401
    assert fake.made == []


def test_reads(client, fake):
    h = KEY | CREDS
    assert client.get("/atms/withdraw/options", headers=h).json() == {"o": 1}
    r = client.get("/atms/withdraw/lookup/sku?q=ก&limit=5&inventory_id=4", headers=h).json()
    assert r["results"][0]["inv"] == 4
    assert client.get("/atms/withdraw/list?inventory_id=4&date=01/11/2026", headers=h).json()["rows"]
    assert client.get("/atms/withdraw/77", headers=h).json() == {"id": 77}


def test_not_found_404(client, fake):
    fake.behaviour = "404"
    assert client.get("/atms/withdraw/77", headers=KEY | CREDS).status_code == 404


def test_issue_ok(client, fake):
    r = client.post("/atms/withdraw/issue", headers=KEY | CREDS, json=ISSUE)
    assert r.status_code == 200 and r.json()["progress"]["approved"] is True


def test_issue_partial_returns_409_with_progress(client, fake):
    fake.behaviour = "partial"
    r = client.post("/atms/withdraw/issue", headers=KEY | CREDS, json=ISSUE)
    assert r.status_code == 409
    assert r.json()["progress"]["gm_id"] == 560800 and r.json()["detail"] == "boom"


def test_issue_auth_401_and_422(client, fake):
    fake.behaviour = "auth"
    assert client.post("/atms/withdraw/issue", headers=KEY | CREDS, json=ISSUE).status_code == 401
    fake.behaviour = "422"
    assert client.post("/atms/withdraw/issue", headers=KEY | CREDS, json=ISSUE).status_code == 422


def test_return_ok(client, fake):
    r = client.post("/atms/withdraw/return", headers=KEY | CREDS, json=RETURN)
    assert r.json()["progress"]["gm_id"] == 2


def test_issue_requires_sku_name(client, fake):
    bad = ISSUE | {"lines": [{"sku_id": 1, "amount": 1}]}
    assert client.post("/atms/withdraw/issue", headers=KEY | CREDS, json=bad).status_code == 422


def test_client_cached_per_username_and_dropped_on_new_password(client, fake):
    h = KEY | CREDS
    client.get("/atms/withdraw/options", headers=h)
    client.get("/atms/withdraw/options", headers=h)
    assert len(fake.made) == 1
    client.get("/atms/withdraw/options", headers=KEY | CREDS | {"X-ATMS-Password": "new"})
    assert len(fake.made) == 2
    assert w._cache["u1"]["pw"] not in ("pw", "new")  # เก็บเป็น hash


def test_cache_expires(client, fake, monkeypatch):
    client.get("/atms/withdraw/options", headers=KEY | CREDS)
    w._cache["u1"]["exp"] = 0
    client.get("/atms/withdraw/options", headers=KEY | CREDS)
    assert len(fake.made) == 2


def test_phpsessid_not_cached(client, fake):
    h = KEY | {"X-PHPSESSID": "abc"}
    client.get("/atms/withdraw/options", headers=h)
    client.get("/atms/withdraw/options", headers=h)
    assert len(fake.made) == 2 and not w._cache


def test_auth_error_drops_cached_client(client, fake):
    client.get("/atms/withdraw/options", headers=KEY | CREDS)
    fake.behaviour = "auth"
    client.post("/atms/withdraw/issue", headers=KEY | CREDS, json=ISSUE)
    assert "u1" not in w._cache


def test_dry_run_makes_zero_posts(client, monkeypatch):
    def boom(self, *a, **k):
        raise AssertionError("network call in dry_run")
    monkeypatch.setattr(AtmsWithdrawClient, "_request", boom)
    monkeypatch.setattr(AtmsWithdrawClient, "_post_once", boom)
    monkeypatch.setattr(AtmsWithdrawClient, "_new_session", boom)
    r = client.post("/atms/withdraw/issue", headers=KEY | CREDS, json=ISSUE | {"dry_run": True})
    assert r.status_code == 200
    r = client.post("/atms/withdraw/return", headers=KEY | CREDS, json=RETURN | {"dry_run": True})
    assert r.status_code == 200
    assert not w._cache


def test_partial_with_auth_cause_drops_cached_client(client, fake, monkeypatch):
    client.get("/atms/withdraw/options", headers=KEY | CREDS)
    assert "u1" in w._cache

    def boom(self, *a, **k):
        raise WithdrawPartialError(IssueProgress(gm_id=7, done=[0]), AtmsAuthError("expired"))
    monkeypatch.setattr(Fake, "issue_withdraw", boom)
    r = client.post("/atms/withdraw/issue", headers=KEY | CREDS, json=ISSUE)
    assert r.status_code == 409 and r.json()["progress"]["gm_id"] == 7
    assert "u1" not in w._cache


def test_partial_with_other_cause_keeps_cached_client(client, fake):
    client.get("/atms/withdraw/options", headers=KEY | CREDS)
    fake.behaviour = "partial"
    client.post("/atms/withdraw/issue", headers=KEY | CREDS, json=ISSUE)
    assert "u1" in w._cache


@pytest.mark.parametrize("bad", ["2026-10-31", "1/1/2026", "31/10/26", "x", ""])
def test_date_must_be_ddmmyyyy(client, fake, bad):
    r = client.get("/atms/withdraw/list", headers=KEY | CREDS, params={"inventory_id": 4, "date": bad})
    assert r.status_code == 422 and "DD/MM/YYYY" in r.json()["detail"]
    r = client.post("/atms/withdraw/issue", headers=KEY | CREDS, json=ISSUE | {"date": bad})
    assert r.status_code == 422 and "DD/MM/YYYY" in r.json()["detail"]
    r = client.post("/atms/withdraw/return", headers=KEY | CREDS, json=RETURN | {"date": bad})
    assert r.status_code == 422 and "DD/MM/YYYY" in r.json()["detail"]
    assert not fake.made


def test_valid_date_accepted(client, fake):
    r = client.get("/atms/withdraw/list", headers=KEY | CREDS, params={"inventory_id": 4, "date": "31/10/2026"})
    assert r.status_code == 200
    assert client.post("/atms/withdraw/issue", headers=KEY | CREDS,
                       json=ISSUE | {"date": "31/10/2026"}).status_code == 200
