"""ต้องการเพิ่มสิทธิ์: the requester picks menus from other roles of the same system to add to a role."""
import pytest

from models.access_review_model import ArRoleItem
from tests.access_review.conftest import h

ACC_ITEMS = [("คลังสินค้า", "คลัง IT", None), ("อุบัติเหตุ", None, "บันทึก")]
FIN_ITEMS = [("อุบัติเหตุ", None, "บันทึก"), ("ยาง", None, "แสดง"), ("ยาง", None, "แก้ไข")]


@pytest.fixture
def w(org, db):
    org.user("R", 3, 1, username="r.user")
    org.user("HEAD", 5, 1)
    org.user("OTHER", 3, 1)
    acc = org.role("ATMS", "ACC-Officer", ACC_ITEMS)
    fin = org.role("ATMS", "FIN-Officer", FIN_ITEMS)
    gl = org.role("WINSPEED", "GL ACC", [("GL", "B1", None)])
    links = {"acc": org.grant("R", "ATMS", acc, "r.user").id}
    org.grant("OTHER", "ATMS", fin, "other.u")
    org.cycle()

    def ids(role):
        return {(i.category, i.item, i.permission): i.id
                for i in db.query(ArRoleItem).filter(ArRoleItem.role_id == role.id)}
    return {"links": links, "acc": ids(acc), "fin": ids(fin), "gl": ids(gl)}


def _acc_role(body):
    return next(r for r in body["roles"] if r["role_name"] == "ACC-Officer")


def test_catalog_lists_each_label_once(client, w):
    body = client.get("/access-review/catalog?system=atms", headers=h("R")).json()
    labels = [(i["category"], i["item"], i["permission"]) for i in body["items"]]
    assert body["system_code"] == "ATMS"
    assert len(labels) == len(set(labels)) == 4  # คลัง IT, อุบัติเหตุ/บันทึก (shared), ยาง/แสดง, ยาง/แก้ไข
    shared = next(i for i in body["items"] if (i["category"], i["permission"]) == ("อุบัติเหตุ", "บันทึก"))
    assert shared["id"] == min(w["acc"][("อุบัติเหตุ", None, "บันทึก")], w["fin"][("อุบัติเหตุ", None, "บันทึก")])
    assert client.get("/access-review/catalog?system=NOPE", headers=h("R")).status_code == 404


def test_requested_items_round_trip_submit_and_show_to_approver(client, w):
    L, fin = w["links"], w["fin"]
    tyre_show, tyre_edit = fin[("ยาง", None, "แสดง")], fin[("ยาง", None, "แก้ไข")]
    dup = fin[("อุบัติเหตุ", None, "บันทึก")]  # ACC already has this label → dropped silently
    r = client.put("/access-review/me/lines", headers=h("R"), json={
        "lines": [{"user_role_id": L["acc"], "requested_item_ids": [tyre_show, tyre_edit, dup]}]})
    assert r.status_code == 200, r.text
    role = r.json()["systems"][0]["roles"][0]
    assert role["requested_item_ids"] == sorted([tyre_show, tyre_edit])

    # a later save without the field leaves the requested set alone
    client.put("/access-review/me/lines", headers=h("R"), json={"lines": [{"user_role_id": L["acc"]}]})
    items = _acc_role(client.get("/access-review/me/items", headers=h("R")).json())
    assert [i["id"] for i in items["requested_items"]] == [tyre_edit, tyre_show]  # sorted by label
    assert {i["id"] for i in items["items"]} == set(w["acc"].values())

    d = client.post("/access-review/me/submit", headers=h("R"), json={}).json()
    line = d["lines"][0]
    assert (line["requested_count"], sorted(line["requested_item_ids"])) == (2, sorted([tyre_show, tyre_edit]))

    pending = client.get("/access-review/pending", headers=h("HEAD")).json()
    assert pending[0]["requested_count"] == 2
    seen = _acc_role(client.get(f"/access-review/{d['id']}/items", headers=h("HEAD")).json())
    assert len(seen["requested_items"]) == 2
    preview = client.get(f"/access-review/{d['id']}/mail-preview", headers=h("HEAD")).json()
    assert "ขอเพิ่ม 2 รายการ" in preview["html"]


def test_requested_items_must_belong_to_the_same_system(client, w):
    L = w["links"]
    r = client.put("/access-review/me/lines", headers=h("R"), json={
        "lines": [{"user_role_id": L["acc"], "requested_item_ids": [w["gl"][("GL", "B1", None)]]}]})
    assert r.status_code == 400
    r = client.put("/access-review/me/lines", headers=h("R"), json={
        "lines": [{"user_role_id": L["acc"], "requested_item_ids": [999999]}]})
    assert r.status_code == 400


def test_clearing_requested_items(client, w):
    L, fin = w["links"], w["fin"]
    client.put("/access-review/me/lines", headers=h("R"), json={
        "lines": [{"user_role_id": L["acc"], "requested_item_ids": [fin[("ยาง", None, "แสดง")]]}]})
    me = client.put("/access-review/me/lines", headers=h("R"), json={
        "lines": [{"user_role_id": L["acc"], "requested_item_ids": []}]}).json()
    assert me["systems"][0]["roles"][0]["requested_item_ids"] == []
