"""AtmsWithdrawClient ฝั่งอ่าน — offline ทั้งหมด: session.request ถูกแทนด้วย fixture"""
import json
import pathlib

import pytest

from services.atms_withdraw_service import AtmsAuthError, AtmsWithdrawClient

FIX = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "atms_withdraw"
TOKEN_IN_FIXTURE = "LBMR26100579"      # หมายเหตุของแถว id 560446 ใน wd_index.html
ID_OF_THAT_ROW = 560446


class Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """คืน fixture ตาม path; เก็บทุก request ไว้ตรวจ (ห้าม POST)"""

    def __init__(self, login_page=False):
        self.calls, self.login_page = [], login_page

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("params")))
        assert method == "GET", "เทสอ่านห้ามเขียนไป ATMS"
        if self.login_page:
            return Resp(302, "", {"Location": "/account/user/login"})
        read = lambda n: Resp(200, (FIX / n).read_text(encoding="utf-8"))
        if "/inv/withdraw/add" in url:
            return read("wd_add.html")
        if "/inv/withdraw/view/id/560724" in url:
            return read("wd_view_empty.html")
        if "/inv/withdraw/view/id/560545" in url:
            return read("wd_view_approved.html")
        if "/inv/withdraw/index" in url:
            return read("wd_index.html")
        if "set.row.per.page" in url:
            return Resp(200, "")
        if "profile" in url:
            return read("profile.html")
        if "maintenance.request.item/code.json" in url:
            return read("mr_item_code.json")
        if "sku/sku.json" in url:
            return read("sku.json")
        if "withdraw/code.json" in url:
            return read("wd_code.json")
        if "serial.no.json" in url:
            return read("serial.json")
        return Resp(404, "")


def make(login_page=False):
    c = AtmsWithdrawClient(phpsessid="x", pace=0)
    c._sess = FakeSession(login_page)
    return c


@pytest.fixture
def fake_client():
    return make()


@pytest.fixture
def fake_client_login_page():
    return make(login_page=True)


def test_options_parses_inventories_and_pick_tos(fake_client):
    o = fake_client.options()
    assert {"id": 4, "name": "คลังลาดกระบัง"} in o["inventories"]
    assert {"id": 45, "name": "CM-งานซ่อม"} in o["pick_tos"]


def test_get_withdraw_empty_view(fake_client):
    w = fake_client.get_withdraw(560724)
    assert w["code"] == "LBWD26102206" and w["approved"] is False and w["items"] == []
    assert w["approver"] is None


def test_get_withdraw_approved_has_items(fake_client):
    w = fake_client.get_withdraw(560545)
    assert w["approved"] is True and w["code"] == "LBWD26102179"
    assert w["approver"] == "user1.t" and w["spare_part_returned"] is False
    it = w["items"][0]
    assert it["sku_code"] == "LB07AC00110" and it["amount"] == 2.0 and it["unit_price"] == 248.25
    assert it["mr_item_id"] == 247142 and len(w["items"]) == 1


def test_profile_fields(fake_client):
    p = fake_client.profile()
    assert p["username"] and p["email"].endswith("@menatransport.co.th")
    assert p["is_driver"] is False and p["is_mechanic"] is False


def test_login_page_raises_auth_error(fake_client_login_page):
    with pytest.raises(AtmsAuthError):
        fake_client_login_page.get_withdraw(560724)


def test_lookup_returns_atms_json(fake_client):
    assert fake_client.lookup("mr_item", "LBMR")[0]["id"] == 257041
    assert fake_client.lookup("sku", "LB07", inventory_id=4)[0]["code"] == "LB07AC00110"
    assert fake_client.lookup("wd", "LBWD", sku_id=7551)[0]["name"].startswith("LBWD")
    assert fake_client.lookup("serial", "", sku_id=7551) == []


def test_find_withdraw_by_remark(fake_client):
    assert fake_client.find_withdraw_by_remark(TOKEN_IN_FIXTURE, 4, "31/10/2026") == [ID_OF_THAT_ROW]
    assert fake_client.find_withdraw_by_remark("SW-9999-999", 4, "31/10/2026") == []


def test_list_withdraws_sets_and_restores_row_per_page(fake_client):
    rows = fake_client.list_withdraws(4, "31/10/2026")
    assert rows[0] == {"id": 560439, "code": "LBWD26102162", "pick_to": "CM-งานซ่อม",
                       "user": "user2.t", "remark": rows[0]["remark"],
                       "created_at": "30/10/2026", "approved": True}
    assert len(rows) == 12
    rpp = [c[2]["row-per-page"] for c in fake_client._sess.calls if "set.row.per.page" in c[1]]
    assert rpp == [1000, 25]
    assert all(c[0] == "GET" for c in fake_client._sess.calls)
