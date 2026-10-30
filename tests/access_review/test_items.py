"""Item-level review (ยืนยันสิทธิ์การเข้าถึง): everything starts ticked, the requester unticks what they don't need."""
import pytest

from models.access_review_model import ArReviewLineItem, ArRoleItem
from services.access_review import importer as I
from tests.access_review.conftest import h

ACC_ITEMS = [("คลังสินค้า", "คลัง IT", None), ("คลังสินค้า", "คลัง OPS", None), ("อุบัติเหตุ", None, "บันทึก")]


@pytest.fixture
def w(org, db):
    org.user("R", 3, 1, username="r.user", email="r.user@menatransport.co.th")
    org.user("HEAD", 5, 2)
    org.user("HEAD2", 6, 1)
    org.user("OTHER", 3, 1)
    org.user("IT", 3, 21)
    acc = org.role("ATMS", "ACC-Officer", ACC_ITEMS)
    gl = org.role("WINSPEED", "GL ACC", [("GL", "B1", None), ("GL", "B2", None)])
    grp = org.role("EMAIL", "acc@menatransport.co.th")
    other = org.role("ATMS", "FIN-Officer", [("เมนู", None, "แสดง")])
    links = {"acc": org.grant("R", "ATMS", acc, "r.user").id, "gl": org.grant("R", "WINSPEED", gl, "Fin_1").id,
             "grp": org.grant("R", "EMAIL", grp).id}
    org.grant("OTHER", "ATMS", other, "other.u")
    org.cycle()
    items = {r.id: [i.id for i in db.query(ArRoleItem).filter(ArRoleItem.role_id == r.id).order_by(ArRoleItem.id)]
             for r in (acc, gl, other)}
    return {"links": links, "acc_items": items[acc.id], "gl_items": items[gl.id], "foreign_item": items[other.id][0],
            "acc_role": acc.id}


def _roles(me):
    return {r["role_name"]: r for s in me["systems"] for r in s["roles"]}


def test_me_items_returns_every_item_of_every_role(client, w):
    body = client.get("/access-review/me/items", headers=h("R")).json()
    by_role = {r["role_name"]: r for r in body["roles"]}
    assert set(by_role) == {"ACC-Officer", "GL ACC", "acc@menatransport.co.th"}
    assert {i["id"] for i in by_role["ACC-Officer"]["items"]} == set(w["acc_items"])
    assert by_role["ACC-Officer"]["user_role_id"] == w["links"]["acc"]
    assert by_role["acc@menatransport.co.th"]["items"] == []
    me = _roles(client.get("/access-review/me", headers=h("R")).json())
    assert me["ACC-Officer"]["unchecked_item_ids"] == [] and me["ACC-Officer"]["decision"] == "keep"


def test_unticked_items_round_trip_and_derive_decision(client, w, db):
    L, acc, gl = w["links"], w["acc_items"], w["gl_items"]
    r = client.put("/access-review/me/lines", headers=h("R"), json={
        "requester_note": "  ไม่ได้ใช้คลัง OPS แล้ว  ",
        "lines": [{"user_role_id": L["acc"], "unchecked_item_ids": [acc[1]]},
                  {"user_role_id": L["gl"], "unchecked_item_ids": gl},           # all unticked → remove
                  {"user_role_id": L["grp"], "decision": "remove"}]})            # Email group unticked
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["requester_note"] == "ไม่ได้ใช้คลัง OPS แล้ว" and me["review"]["requester_note"] == me["requester_note"]
    roles = _roles(me)
    assert (roles["ACC-Officer"]["unchecked_item_ids"], roles["ACC-Officer"]["decision"]) == ([acc[1]], "keep")
    assert (sorted(roles["GL ACC"]["unchecked_item_ids"]), roles["GL ACC"]["decision"]) == (sorted(gl), "remove")
    assert roles["acc@menatransport.co.th"]["decision"] == "remove"

    # re-tick: a listed line's unticked set is replaced; omitted requester_note is left as is
    me = client.put("/access-review/me/lines", headers=h("R"), json={
        "lines": [{"user_role_id": L["gl"], "unchecked_item_ids": [gl[0]]}]}).json()
    assert (_roles(me)["GL ACC"]["unchecked_item_ids"], _roles(me)["GL ACC"]["decision"]) == ([gl[0]], "keep")
    assert _roles(me)["ACC-Officer"]["unchecked_item_ids"] == [acc[1]]
    assert me["requester_note"] == "ไม่ได้ใช้คลัง OPS แล้ว"

    # submit needs nothing else; detail carries item counts
    d = client.post("/access-review/me/submit", headers=h("R"), json={}).json()
    assert d["status"] == "pending" and d["requester_note"] == "ไม่ได้ใช้คลัง OPS แล้ว"
    lines = {l["role_name"]: l for l in d["lines"]}
    assert (lines["ACC-Officer"]["item_count"], lines["ACC-Officer"]["unchecked_count"],
            lines["ACC-Officer"]["unchecked_item_ids"]) == (3, 1, [acc[1]])
    assert lines["acc@menatransport.co.th"]["decision"] == "remove"
    assert sorted(d["next_approver_names"]) == ["FirstHEAD Last", "FirstHEAD2 Last"]
    assert client.get("/access-review/me", headers=h("R")).json()["review"]["next_approver_names"] == \
        d["next_approver_names"]

    row = client.get("/access-review/pending", headers=h("HEAD")).json()[0]
    assert (row["total_lines"], row["remove_count"], row["unchecked_count"]) == (3, 1, 2)
    assert row["next_approver_names"] == d["next_approver_names"]

    items = client.get(f"/access-review/{d['id']}/items", headers=h("HEAD")).json()
    assert {r["role_name"] for r in items["roles"]} == {"ACC-Officer", "GL ACC", "acc@menatransport.co.th"}
    assert client.get(f"/access-review/{d['id']}/items", headers=h("OTHER")).status_code == 403

    prev = client.get(f"/access-review/{d['id']}/mail-preview", headers=h("HEAD")).json()
    assert "ไม่ติ๊ก" in prev["html"] and "2 รายการ" in prev["html"]


def test_foreign_or_unknown_item_ids_are_rejected(client, w, db):
    L = w["links"]
    for bad in ([w["foreign_item"]], [w["gl_items"][0]], [123456]):
        r = client.put("/access-review/me/lines", headers=h("R"),
                       json={"lines": [{"user_role_id": L["acc"], "unchecked_item_ids": bad}]})
        assert r.status_code == 400, bad
    r = client.put("/access-review/me/lines", headers=h("R"),   # an Email group has no items at all
                   json={"lines": [{"user_role_id": L["grp"], "unchecked_item_ids": [w["acc_items"][0]]}]})
    assert r.status_code == 400
    assert db.query(ArReviewLineItem).count() == 0


def test_approver_cannot_change_ticks(client, w, db):
    L, acc = w["links"], w["acc_items"]
    rid = client.post("/access-review/me/submit", headers=h("R"), json={
        "lines": [{"user_role_id": L["acc"], "unchecked_item_ids": [acc[0]]}]}).json()["id"]
    before = sorted((x.review_line_id, x.role_item_id) for x in db.query(ArReviewLineItem))
    # the approver's own PUT only ever targets the approver's own review; the requester's rows are not theirs
    r = client.put("/access-review/me/lines", headers=h("HEAD"),
                   json={"lines": [{"user_role_id": L["acc"], "unchecked_item_ids": []}]})
    assert r.status_code == 400
    # the decision endpoint takes no lines (extra fields are ignored) and keeps the ticks
    r = client.post(f"/access-review/{rid}/decision", headers=h("HEAD"),
                    json={"action": "approve", "comment": "ok", "lines": [{"user_role_id": L["acc"]}]})
    assert r.status_code == 200 and r.json()["comment"] == "ok"
    db.expire_all()
    assert sorted((x.review_line_id, x.role_item_id) for x in db.query(ArReviewLineItem)) == before
    # requester cannot change them either once submitted/approved
    assert client.put("/access-review/me/lines", headers=h("R"),
                      json={"lines": [{"user_role_id": L["acc"], "unchecked_item_ids": []}]}).status_code == 409


def test_history_lists_own_and_decided(client, w):
    L, acc = w["links"], w["acc_items"]
    rid = client.post("/access-review/me/submit", headers=h("R"), json={
        "lines": [{"user_role_id": L["acc"], "unchecked_item_ids": [acc[2]]}]}).json()["id"]
    assert client.get("/access-review/history", headers=h("R")).json() == []  # pending is not finished
    client.post(f"/access-review/{rid}/decision", headers=h("HEAD"), json={"action": "approve"})
    for who in ("R", "HEAD"):
        rows = client.get("/access-review/history", headers=h(who)).json()
        assert [(x["id"], x["status"], x["decided_by_name"]) for x in rows] == [(rid, "approved", "FirstHEAD Last")]
        assert rows[0]["next_approver_names"] == [] and rows[0]["unchecked_count"] == 1
    assert client.get("/access-review/history", headers=h("HEAD2")).json() == []  # approver who did not decide
    assert client.get("/access-review/pending", headers=h("HEAD2")).json() == []


def test_removals_one_row_per_role_or_item(client, w):
    L, acc = w["links"], w["acc_items"]
    rid = client.post("/access-review/me/submit", headers=h("R"), json={
        "requester_note": "ย้ายงาน",
        "lines": [{"user_role_id": L["acc"], "unchecked_item_ids": [acc[0], acc[2]]},
                  {"user_role_id": L["grp"], "decision": "remove"}]}).json()["id"]
    assert client.get("/access-review/admin/removals", headers=h("IT")).json() == []  # not approved yet
    client.post(f"/access-review/{rid}/decision", headers=h("HEAD"), json={"action": "approve"})
    rows = client.get("/access-review/admin/removals", headers=h("IT")).json()
    got = sorted((x["system_code"], x["role_name"], x["removal_type"], x["category"], x["item"], x["permission"])
                 for x in rows)
    assert got == sorted([
        ("ATMS", "ACC-Officer", "item", "คลังสินค้า", "คลัง IT", None),
        ("ATMS", "ACC-Officer", "item", "อุบัติเหตุ", None, "บันทึก"),
        ("EMAIL", "acc@menatransport.co.th", "role", None, None, None),
    ])
    assert {x["requester_note"] for x in rows} == {"ย้ายงาน"}
    assert {x["account_name"] for x in rows} == {"r.user", "r.user@menatransport.co.th"}


def test_reimport_keeps_items_referenced_by_reviews(client, w, db, org):
    """Item ids stay stable on re-import; a referenced item that left the file is hidden, not deleted."""
    L, acc = w["links"], w["acc_items"]
    org.user("R2", 3, 1, username="r2.user")
    client.post("/access-review/me/submit", headers=h("R"), json={
        "lines": [{"user_role_id": L["acc"], "unchecked_item_ids": [acc[1]]}]})   # references คลัง OPS

    parsed = I.Parsed("ATMS", roles={"ACC-Officer": [ACC_ITEMS[0], ("รายงาน", None, "แสดง")]},
                      assignments=[I.RawAssignment(role_name="ACC-Officer", account_name="r.user", username="r.user")])
    rep = I.run_import(db, "ATMS", parsed, dry_run=True)
    assert (rep["items_added"], rep["items_deleted"], rep["items_kept_for_history"]) == (1, 1, 1)
    assert db.query(ArRoleItem).filter(ArRoleItem.role_id == w["acc_role"]).count() == 3   # dry run

    I.run_import(db, "ATMS", parsed, dry_run=False)
    db.expire_all()
    rows = {i.id: i for i in db.query(ArRoleItem).filter(ArRoleItem.role_id == w["acc_role"])}
    assert acc[0] in rows and rows[acc[0]].is_active               # unchanged item keeps its id
    assert acc[1] in rows and rows[acc[1]].is_active is False      # referenced → kept, hidden
    assert acc[2] not in rows                                      # unreferenced → deleted
    assert len([i for i in rows.values() if i.is_active]) == 2

    # role-items / item_count only show active items; the review still shows its unticked (now hidden) item
    assert len(client.get("/access-review/role-items", params={"role_id": w["acc_role"]},
                          headers=h("R")).json()["items"]) == 2
    rid = client.get("/access-review/me", headers=h("R")).json()["review"]["id"]
    acc_items = {r["role_name"]: r for r in client.get(f"/access-review/{rid}/items",
                                                       headers=h("HEAD")).json()["roles"]}["ACC-Officer"]["items"]
    assert {i["id"]: i["is_active"] for i in acc_items}[acc[1]] is False

    # back in the next file → reactivated with the same id
    parsed.roles["ACC-Officer"].append(ACC_ITEMS[1])
    assert I.run_import(db, "ATMS", parsed, dry_run=False)["items_reactivated"] == 1
    db.expire_all()
    assert db.get(ArRoleItem, acc[1]).is_active is True


def test_admin_item_delete_is_soft_when_referenced(client, w, db):
    L, acc = w["links"], w["acc_items"]
    client.put("/access-review/me/lines", headers=h("R"),
               json={"lines": [{"user_role_id": L["acc"], "unchecked_item_ids": [acc[0]]}]})
    assert client.delete(f"/access-review/admin/role-items/{acc[0]}", headers=h("IT")).status_code == 204
    assert client.delete(f"/access-review/admin/role-items/{acc[2]}", headers=h("IT")).status_code == 204
    db.expire_all()
    assert db.get(ArRoleItem, acc[0]).is_active is False and db.get(ArRoleItem, acc[2]) is None
    # submitting drops the now-hidden item from the draft's unticked set
    d = client.post("/access-review/me/submit", headers=h("R"), json={}).json()
    assert {l["role_name"]: l["unchecked_item_ids"] for l in d["lines"]}["ACC-Officer"] == []


def test_admin_copy_items_adds_only_missing_items(client, org, w):
    target = org.role("ATMS", "ACC-Officer copy", [("คลังสินค้า", "คลัง IT", None)])
    res = client.post(f"/access-review/admin/roles/{target.id}/copy-items", json={"source_role_id": w["acc_role"]},
                      headers=h("IT"))
    assert res.status_code == 200, res.text
    assert res.json()["added"] == 2 and res.json()["role"]["item_count"] == 3
    again = client.post(f"/access-review/admin/roles/{target.id}/copy-items", json={"source_role_id": w["acc_role"]},
                        headers=h("IT"))
    assert again.json()["added"] == 0


def test_admin_copy_items_rejects_other_system_same_role_and_non_admin(client, org, w):
    target = org.role("ATMS", "Empty", [])
    gl = next(r for r in client.get("/access-review/admin/roles", headers=h("IT")).json() if r["name"] == "GL ACC")
    url = f"/access-review/admin/roles/{target.id}/copy-items"
    assert client.post(url, json={"source_role_id": gl["id"]}, headers=h("IT")).status_code == 400
    assert client.post(url, json={"source_role_id": target.id}, headers=h("IT")).status_code == 400
    assert client.post(url, json={"source_role_id": w["acc_role"]}, headers=h("R")).status_code == 403
