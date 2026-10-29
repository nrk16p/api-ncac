"""Draft → submit → approve / reject → resubmit, through the HTTP router on SQLite."""
import pytest

from models.access_review_model import ArReview, ArReviewApprover
from tests.access_review.conftest import h


@pytest.fixture
def world(org, monkeypatch):
    """R (Lv3, dept 1) holds 2 ATMS roles + 1 email group; HEAD (Lv5, same division) approves."""
    calls = []
    import services.email_service as email_service
    monkeypatch.setattr(email_service, "send_email", lambda *a, **k: calls.append(a))
    org.user("R", 3, 1, username="r.user", email="r.user@menatransport.co.th")
    org.user("HEAD", 5, 2)
    org.user("OTHER", 3, 1)
    org.user("IT", 3, 21)  # IT admin (department 21)
    org.user("EXEC", 7, 1)
    org.user("NODATA", 2, 1)
    acc = org.role("ATMS", "ACC-Officer", [("คลังสินค้า", "คลัง IT", None), ("อุบัติเหตุ", None, "บันทึก")])
    fin = org.role("ATMS", "FIN-Officer")
    grp = org.role("EMAIL", "acc@menatransport.co.th")
    links = {
        "acc": org.grant("R", "ATMS", acc, account_name="r.user"),
        "fin": org.grant("R", "ATMS", fin, account_name="r.user"),
        "grp": org.grant("R", "EMAIL", grp),
    }
    org.grant("EXEC", "ATMS", acc, account_name="exec")
    cycle = org.cycle()
    return {"links": {k: v.id for k, v in links.items()}, "roles": {"acc": acc.id}, "cycle": cycle, "calls": calls}


def all_lines(world, acc="keep", fin="remove", grp="keep"):
    L = world["links"]
    return [{"user_role_id": L["acc"], "decision": acc}, {"user_role_id": L["fin"], "decision": fin, "note": " ไม่ใช้แล้ว "},
            {"user_role_id": L["grp"], "decision": grp}]


def test_caller_header_required(client, world):
    assert client.get("/access-review/me").status_code == 401
    assert client.get("/access-review/me", headers=h("NOPE")).status_code == 401


def test_me_without_data_hides_feature(client, world):
    body = client.get("/access-review/me", headers=h("NODATA")).json()
    assert body["systems"] == []
    assert body["review"] is None
    assert body["cycle"]["name"] == "H2-2569"


def test_me_lists_systems_roles_and_preview(client, world):
    body = client.get("/access-review/me", headers=h("R")).json()
    assert [s["code"] for s in body["systems"]] == ["ATMS", "EMAIL"]
    atms = body["systems"][0]["roles"]
    assert [r["role_name"] for r in atms] == ["ACC-Officer", "FIN-Officer"]
    assert atms[0]["item_count"] == 2 and atms[0]["account_name"] == "r.user"
    assert body["systems"][1]["roles"][0]["account_name"] == "r.user@menatransport.co.th"  # EMAIL → users.email
    assert body["route_preview"] == "dept_head"
    assert [a["employee_id"] for a in body["approvers_preview"]] == ["HEAD"]
    assert body["approvers_preview"][0]["email"] == "userHEAD@example.com"
    assert body["is_admin"] is False and body["has_pending_approvals"] == 0


def test_role_items(client, world):
    body = client.get("/access-review/role-items", params={"role_id": world["roles"]["acc"]}, headers=h("R")).json()
    assert body["role"]["system_code"] == "ATMS"
    assert {(i["category"], i["item"], i["permission"]) for i in body["items"]} == {
        ("คลังสินค้า", "คลัง IT", None), ("อุบัติเหตุ", None, "บันทึก")}
    assert client.get("/access-review/role-items", params={"role_id": 999}, headers=h("R")).status_code == 404


def test_full_flow(client, world, db):
    L = world["links"]
    # draft with one decision
    r = client.put("/access-review/me/lines", headers=h("R"),
                   json={"lines": [{"user_role_id": L["acc"], "decision": "keep"}]})
    assert r.status_code == 200, r.text
    assert r.json()["review"]["status"] == "draft"

    # someone else's user role → 400
    r = client.put("/access-review/me/lines", headers=h("OTHER"),
                   json={"lines": [{"user_role_id": L["acc"], "decision": "keep"}]})
    assert r.status_code == 400

    # nothing needs an explicit decision any more — but an item of another role is rejected
    r = client.put("/access-review/me/lines", headers=h("R"),
                   json={"lines": [{"user_role_id": L["fin"], "unchecked_item_ids": [999]}]})
    assert r.status_code == 400 and "999" in r.json()["detail"]
    assert client.get("/access-review/me", headers=h("R")).json()["review"]["status"] == "draft"

    # submit with all lines → pending to HEAD, mail NOT sent (switch off) but mailed_at stamped
    r = client.post("/access-review/me/submit", headers=h("R"), json={"lines": all_lines(world)})
    assert r.status_code == 200, r.text
    detail = r.json()
    rid = detail["id"]
    assert detail["status"] == "pending" and detail["route"] == "dept_head"
    assert [a["employee_id"] for a in detail["approvers"]] == ["HEAD"]
    assert detail["can_decide"] is False
    assert {l["role_name"]: l["note"] for l in detail["lines"]}["FIN-Officer"] == "ไม่ใช้แล้ว"
    assert world["calls"] == []
    assert db.get(ArReviewApprover, (rid, "HEAD")).mailed_at is not None

    # read-only while pending
    r = client.put("/access-review/me/lines", headers=h("R"), json={"lines": all_lines(world, acc="remove")})
    assert r.status_code == 409

    # approver inbox
    me_head = client.get("/access-review/me", headers=h("HEAD")).json()
    assert me_head["has_pending_approvals"] == 1
    pend = client.get("/access-review/pending", headers=h("HEAD")).json()
    assert [(p["id"], p["total_lines"], p["remove_count"]) for p in pend] == [(rid, 3, 1)]
    assert pend[0]["requester"]["division"] == "ฝ่าย 10"
    assert client.get(f"/access-review/{rid}", headers=h("HEAD")).json()["can_decide"] is True

    # visibility + permissions
    assert client.get(f"/access-review/{rid}", headers=h("OTHER")).status_code == 403
    assert client.get(f"/access-review/{rid}", headers=h("IT")).status_code == 200
    assert client.post(f"/access-review/{rid}/decision", headers=h("OTHER"),
                       json={"action": "approve"}).status_code == 403
    assert client.post(f"/access-review/{rid}/decision", headers=h("R"),
                       json={"action": "approve"}).status_code == 403
    assert client.post(f"/access-review/{rid}/decision", headers=h("IT"),
                       json={"action": "approve"}).status_code == 403  # not it_queue

    # reject needs a comment
    r = client.post(f"/access-review/{rid}/decision", headers=h("HEAD"), json={"action": "reject", "comment": "  "})
    assert r.status_code == 400
    r = client.post(f"/access-review/{rid}/decision", headers=h("HEAD"),
                    json={"action": "reject", "comment": "ตรวจ FIN อีกครั้ง"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert r.json()["decided_by_name"] == "FirstHEAD Last"

    # second decision → 409
    assert client.post(f"/access-review/{rid}/decision", headers=h("HEAD"),
                       json={"action": "approve"}).status_code == 409

    # rejected → editable again, comment visible
    me = client.get("/access-review/me", headers=h("R")).json()
    assert me["review"]["status"] == "rejected" and me["review"]["comment"] == "ตรวจ FIN อีกครั้ง"
    r = client.put("/access-review/me/lines", headers=h("R"), json={"lines": all_lines(world, fin="keep")})
    assert r.status_code == 200

    # resubmit → pending again, decision fields cleared
    r = client.post("/access-review/me/submit", headers=h("R"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "pending" and d["decided_by"] is None and d["comment"] is None
    assert d["id"] == rid

    # approve
    r = client.post(f"/access-review/{rid}/decision", headers=h("HEAD"), json={"action": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert client.get("/access-review/pending", headers=h("HEAD")).json() == []
    assert client.post("/access-review/me/submit", headers=h("R"), json={}).status_code == 409


def test_executive_bypass(client, world):
    L_exec = client.get("/access-review/me", headers=h("EXEC")).json()
    assert L_exec["route_preview"] == "bypass" and L_exec["approvers_preview"] == []
    ar_id = L_exec["systems"][0]["roles"][0]["user_role_id"]
    items = client.get("/access-review/me/items", headers=h("EXEC")).json()["roles"][0]["items"]
    r = client.post("/access-review/me/submit", headers=h("EXEC"),   # every item unticked → whole role removed
                    json={"lines": [{"user_role_id": ar_id, "unchecked_item_ids": [i["id"] for i in items]}]})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "approved" and d["route"] == "bypass" and d["decided_by"] == "EXEC"
    assert d["approvers"] == []
    removals = client.get("/access-review/admin/removals", headers=h("IT")).json()
    assert [(x["employee_id"], x["role_name"], x["system_code"]) for x in removals] == [
        ("EXEC", "ACC-Officer", "ATMS")]


def test_it_queue_is_decided_by_it_admin(client, world, org, db):
    org.user("LONE", 2, 3)  # division 20: nobody to approve
    role = org.role("WINSPEED", "AP ACC")
    link = org.grant("LONE", "WINSPEED", role, account_name="Fin_X")
    r = client.post("/access-review/me/submit", headers=h("LONE"),
                    json={"lines": [{"user_role_id": link.id, "decision": "keep"}]})
    d = r.json()
    assert d["route"] == "it_queue" and d["approvers"] == []
    assert client.get(f"/access-review/{d['id']}", headers=h("IT")).json()["can_decide"] is True
    assert [p["id"] for p in client.get("/access-review/pending", headers=h("IT")).json()] == [d["id"]]
    assert client.post(f"/access-review/{d['id']}/decision", headers=h("HEAD"),
                       json={"action": "approve"}).status_code == 403
    r = client.post(f"/access-review/{d['id']}/decision", headers=h("IT"), json={"action": "approve"})
    assert r.json()["status"] == "approved"


def test_closed_cycle_blocks_saving_and_submit(client, world, db):
    world["cycle"].is_open = False
    db.commit()
    r = client.post("/access-review/me/submit", headers=h("R"), json={"lines": all_lines(world)})
    assert r.status_code == 400 and r.json()["detail"] == "รอบการทบทวนสิทธิ์ปิดแล้ว"
    assert client.put("/access-review/me/lines", headers=h("R"), json={"lines": []}).status_code == 400
    assert client.get("/access-review/me", headers=h("R")).json()["cycle"]["is_open"] is False


def test_mail_preview_and_guard(client, world, db):
    rid = client.post("/access-review/me/submit", headers=h("R"), json={"lines": all_lines(world)}).json()["id"]
    assert world["calls"] == []  # mail is preview-only — send_email is never called
    prev = client.get(f"/access-review/{rid}/mail-preview", headers=h("HEAD")).json()
    assert prev["to"] == ["userHEAD@example.com"]
    assert "รอการอนุมัติ" in prev["subject"]
    assert f"https://menait-service.vercel.app/access-review/{rid}" in prev["html"]
    assert client.get(f"/access-review/{rid}/mail-preview", headers=h("R")).status_code == 403
    assert client.get(f"/access-review/{rid}/mail-preview", headers=h("IT")).status_code == 200


def test_admin_endpoints_require_it_admin(client, world):
    assert client.get("/access-review/admin/roles", headers=h("R")).status_code == 403
    roles = client.get("/access-review/admin/roles", headers=h("IT")).json()
    assert {r["name"]: r["user_count"] for r in roles}["ACC-Officer"] == 2


def test_admin_reviews_summary(client, world):
    client.put("/access-review/me/lines", headers=h("R"), json={"lines": all_lines(world)})
    body = client.get("/access-review/admin/reviews", headers=h("IT")).json()
    assert body["summary"] == {"total_people": 2, "not_started": 1, "draft": 1, "pending": 0, "approved": 0,
                               "rejected": 0}
    only_draft = client.get("/access-review/admin/reviews", params={"status": "draft"}, headers=h("IT")).json()
    assert [(i["employee_id"], i["total_lines"], i["remove_count"]) for i in only_draft["items"]] == [("R", 3, 1)]
    other_div = client.get("/access-review/admin/reviews", params={"division_id": 20}, headers=h("IT")).json()
    assert other_div["summary"]["total_people"] == 0


def test_admin_preview_shows_employee_view_read_only(client, world):
    assert client.get("/access-review/admin/preview/R", headers=h("R")).status_code == 403
    assert client.get("/access-review/admin/preview/NOBODY", headers=h("IT")).status_code == 404
    preview = client.get("/access-review/admin/preview/R", headers=h("IT")).json()
    own = client.get("/access-review/me", headers=h("R")).json()
    assert preview["systems"] == own["systems"]
    assert preview["review"] is None  # previewing never creates a draft
    assert client.get("/access-review/admin/preview/R/items", headers=h("IT")).json() == \
        client.get("/access-review/me/items", headers=h("R")).json()


def test_admin_crud_and_soft_delete(client, world, db):
    H = h("IT")
    sys_id = client.get("/access-review/admin/systems", headers=H).json()[0]["id"]
    r = client.post("/access-review/admin/roles", headers=H, json={"system_id": sys_id, "name": "NEW-Role"})
    assert r.status_code == 201
    role_id = r.json()["id"]
    assert client.post("/access-review/admin/roles", headers=H,
                       json={"system_id": sys_id, "name": "NEW-Role"}).status_code == 409
    item = client.post(f"/access-review/admin/roles/{role_id}/items", headers=H,
                       json={"category": "เมนู", "item": "x", "permission": "แสดง"}).json()
    assert client.put(f"/access-review/admin/role-items/{item['id']}", headers=H,
                      json={"permission": "แก้ไข"}).json()["permission"] == "แก้ไข"
    assert client.delete(f"/access-review/admin/role-items/{item['id']}", headers=H).status_code == 204

    r = client.post("/access-review/admin/user-roles", headers=H,
                    json={"employee_id": "OTHER", "role_id": role_id, "account_name": "other.u"})
    assert r.status_code == 201, r.text
    ur = r.json()
    assert (ur["system_code"], ur["role_name"], ur["employee_name"], ur["account_name"], ur["is_active"]) == (
        "ATMS", "NEW-Role", "FirstOTHER Last", "other.u", True)
    assert client.post("/access-review/admin/user-roles", headers=H,
                       json={"employee_id": "OTHER", "role_id": role_id}).status_code == 409
    assert client.post("/access-review/admin/user-roles", headers=H,
                       json={"employee_id": "ZZZ", "role_id": role_id}).status_code == 400
    assert client.post("/access-review/admin/user-roles", headers=H,
                       json={"employee_id": "OTHER", "role_id": 9999}).status_code == 400
    page = client.get("/access-review/admin/user-roles", headers=H, params={"q": "other", "page_size": 10}).json()
    assert page["total"] == 1 and page["items"][0]["id"] == ur["id"]
    assert client.get("/access-review/admin/user-roles", headers=H,
                      params={"role_id": role_id}).json()["total"] == 1
    assert client.get("/access-review/admin/user-roles", headers=H,
                      params={"q": "other.u"}).json()["total"] == 1  # account_name
    assert client.put(f"/access-review/admin/user-roles/{ur['id']}", headers=H,
                      json={"account_name": "other.x"}).json()["account_name"] == "other.x"

    assert client.get("/access-review/me", headers=h("OTHER")).json()["systems"][0]["roles"][0]["role_name"] == "NEW-Role"
    assert client.delete(f"/access-review/admin/user-roles/{ur['id']}", headers=H).json()["is_active"] is False
    assert client.get("/access-review/me", headers=h("OTHER")).json()["systems"] == []
    assert client.get("/access-review/admin/user-roles", headers=H, params={"role_id": role_id}).json()["total"] == 0
    assert client.get("/access-review/admin/user-roles", headers=H,
                      params={"role_id": role_id, "include_inactive": True}).json()["total"] == 1
    # re-adding a soft-deleted row reactivates it (same id)
    again = client.post("/access-review/admin/user-roles", headers=H, json={"employee_id": "OTHER", "role_id": role_id})
    assert again.status_code == 201 and again.json()["id"] == ur["id"] and again.json()["is_active"] is True
    client.delete(f"/access-review/admin/user-roles/{ur['id']}", headers=H)
    client.delete(f"/access-review/admin/roles/{role_id}", headers=H)
    assert "NEW-Role" not in [r["name"] for r in client.get("/access-review/admin/roles", headers=H).json()]
    assert "NEW-Role" in [r["name"] for r in client.get("/access-review/admin/roles", headers=H,
                                                          params={"include_inactive": True}).json()]

    c = client.post("/access-review/admin/cycles", headers=H,
                    json={"name": "H1-2570", "start_date": "2027-01-01", "end_date": "2026-12-01"})
    assert c.status_code == 400
    c = client.post("/access-review/admin/cycles", headers=H,
                    json={"name": "H1-2570", "start_date": "2027-01-01", "end_date": "2027-06-30"})
    assert c.status_code == 201
    assert client.put(f"/access-review/admin/cycles/{c.json()['id']}", headers=H,
                      json={"is_open": False}).json()["is_open"] is False


def test_review_row_is_unique_per_cycle(world, client, db):
    client.put("/access-review/me/lines", headers=h("R"), json={"lines": all_lines(world)})
    client.put("/access-review/me/lines", headers=h("R"), json={"lines": all_lines(world, acc="remove")})
    assert db.query(ArReview).filter(ArReview.employee_id == "R").count() == 1
