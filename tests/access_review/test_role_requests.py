"""ต้องการเพิ่มอีเมลกลุ่ม: the requester asks for whole roles (Email groups) they don't hold yet."""
import pytest

from tests.access_review.conftest import h


@pytest.fixture
def w(org):
    org.user("R", 3, 1, username="r.user")
    org.user("HEAD", 5, 1)
    acc = org.role("ATMS", "ACC-Officer", [("อุบัติเหตุ", None, "บันทึก")])
    mine = org.role("EMAIL", "acc@menatransport.co.th")
    hr = org.role("EMAIL", "hr@menatransport.co.th")
    ops = org.role("EMAIL", "ops@menatransport.co.th")
    gone = org.role("EMAIL", "old@menatransport.co.th")
    gone.is_active = False
    org.grant("R", "ATMS", acc, "r.user")
    org.grant("R", "EMAIL", mine)
    org.cycle()
    org.db.commit()
    return {"mine": mine.id, "hr": hr.id, "ops": ops.id, "gone": gone.id}


def test_catalog_roles_lists_active_roles(client, w):
    body = client.get("/access-review/catalog/roles?system=email", headers=h("R")).json()
    names = [r["name"] for r in body["roles"]]
    assert body["system_code"] == "EMAIL"
    assert names == ["acc@menatransport.co.th", "hr@menatransport.co.th", "ops@menatransport.co.th"]


def test_requested_roles_round_trip_and_submit(client, w):
    me = client.put("/access-review/me/lines", headers=h("R"), json={
        "lines": [], "requested_role_ids": [w["ops"], w["hr"], w["mine"]]}).json()  # held one is dropped
    assert [r["name"] for r in me["requested_roles"]] == ["hr@menatransport.co.th", "ops@menatransport.co.th"]
    assert all(r["system_code"] == "EMAIL" for r in me["requested_roles"])

    # omitted = unchanged
    me = client.put("/access-review/me/lines", headers=h("R"), json={"lines": []}).json()
    assert len(me["requested_roles"]) == 2

    d = client.post("/access-review/me/submit", headers=h("R"), json={"requested_role_ids": [w["hr"]]}).json()
    assert [r["id"] for r in d["requested_roles"]] == [w["hr"]]
    pending = client.get("/access-review/pending", headers=h("HEAD")).json()
    assert pending[0]["requested_count"] == 1
    assert client.get(f"/access-review/{d['id']}", headers=h("HEAD")).json()["requested_roles"][0]["id"] == w["hr"]


def test_requested_role_must_be_active(client, w):
    r = client.put("/access-review/me/lines", headers=h("R"), json={"lines": [], "requested_role_ids": [w["gone"]]})
    assert r.status_code == 400
