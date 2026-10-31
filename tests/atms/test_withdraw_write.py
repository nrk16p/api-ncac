"""issue_withdraw / return_to_stock — offline: fake session จดลำดับ request และจำลองสถานะใบเบิก"""
import re

import pytest
import requests

from services.atms_openjob_service import AtmsOpenJobError
from services.atms_withdraw_service import (
    AtmsAuthError, AtmsWithdrawClient, IssueLine, IssueProgress, ReturnLine,
    WithdrawPartialError,
)
from tests.atms.test_withdraw_read import FIX, Resp, fake_client  # noqa: F401

NEW_ID = 560800
EXISTING_ID = 560446
TOKEN = "SW-2610-001"
LINES2 = [IssueLine(6106, 2, "แบตเตอรี่ N50ZL", 257041), IssueLine(133, 1, "น้ำมันเครื่อง")]
LINES3 = LINES2 + [IssueLine(77, 5, "กรองอากาศ")]
RETURN_LINE = ReturnLine(6106, 1, 248.25, "แบตเตอรี่ N50ZL")


def _view(code, items, approver, code_label="รหัส", reject_id=None):
    rows = "".join(
        f"<tr><td>g</td><td>SKU{i} : name</td><td></td><td>{a}</td><td>10.00</td><td>x</td>"
        f"<td>{m}</td><td></td></tr>" for i, (a, m) in enumerate(items))
    return (f"<dl><dt>{code_label} :</dt><dd>{code}</dd>"
            f"<dt>ผู้ใช้งาน :</dt><dd>u</dd>"
            + (f"<dt>approver :</dt><dd>{approver}</dd>" if approver else "")
            + "</dl><table class='defaultTable'><thead><tr><th>กลุ่มสินค้า</th><th>สินค้า</th>"
              "<th>serial no</th><th>amount</th><th>unit price</th><th>รวม</th>"
              "<th>รายการแจ้งซ่อม</th><th></th></tr></thead><tbody>" + rows + "</tbody></table>")


class WriterSession:
    def __init__(self, existing=False, fail_item_n=None, approve_works=True,
                 header_location=True, form_error_on=None, exc_on_item=None):
        self.calls, self.reqs = [], []
        self.existing, self.fail_item_n = existing, fail_item_n
        self.approve_works, self.header_location = approve_works, header_location
        self.form_error_on, self.exc_on_item = form_error_on, exc_on_item
        self.items, self.approved, self.item_posts = [], False, 0
        self.dd_items, self.dd_approved = [], False
        self.dd_existing, self.wd_approved_preseed = False, False
        self.preseed = 0          # จำนวนบรรทัดที่มีอยู่ใน ATMS ก่อนเริ่ม (จำลองใบที่ทำค้าง)

    def request(self, method, url, **kw):
        self.reqs.append((method, url, kw))
        if "set.row.per.page" in url:
            return Resp(200, "")
        if "/inv/withdraw/index" in url:
            self.calls.append("GET index(remark)")
            html = (FIX / "wd_index.html").read_text(encoding="utf-8")
            if self.existing:
                return Resp(200, html)
            return Resp(200, re.sub(r"LBMR26100579", "OTHER", html))
        if method == "POST" and url.endswith("/inv/withdraw/add"):
            self.calls.append("POST withdraw/add")
            if self.form_error_on == "header":
                return Resp(200, '<div class="error">เลือกคลัง</div>')
            loc = f"/inv/withdraw/view/id/{NEW_ID}" if self.header_location else "/inv/withdraw/index"
            return Resp(302, "", {"Location": loc})
        if method == "POST" and "/inv/withdraw.item/add/" in url:
            self.calls.append("POST item")
            self.item_posts += 1
            if self.exc_on_item == self.item_posts:
                raise requests.ConnectionError("boom")
            if self.fail_item_n == self.item_posts:
                return Resp(200, '<div class="error">amount ผิด</div>')
            self.items.append((kw["data"]["amount"], kw["data"].get("maintenance_request_item_id", "")))
            return Resp(302, "", {"Location": f"/inv/withdraw/view/id/{NEW_ID}"})
        if "/inv/withdraw/approve/id/" in url:
            self.calls.append("approve")
            self.approved = self.approve_works
            return Resp(302, "", {"Location": f"/inv/withdraw/view/id/{NEW_ID}"})
        if "/inv/withdraw/view/id/" in url:
            self.calls.append("GET view")
            if self.wd_approved_preseed:
                self.approved = True
            items = [("1.00", "")] * self.preseed + self.items
            return Resp(200, _view("LBWD26102300", items, "boss" if self.approved else None))
        if "/inv/deposit/index" in url:
            self.calls.append("GET dd index")
            html = (FIX / "dd_index.html").read_text(encoding="utf-8")
            return Resp(200, html if self.dd_existing else html.replace("ORVCM6909000054", "OTHER"))
        if method == "POST" and url.endswith("/inv/deposit/add"):
            self.calls.append("POST deposit/add")
            return Resp(302, "", {"Location": "/inv/deposit/view/id/777"})
        if method == "POST" and "/inv/deposit.item/add/" in url:
            self.calls.append("POST deposit item")
            self.dd_items.append(kw["data"])
            return Resp(302, "", {"Location": "/inv/deposit/view/id/777"})
        if "/inv/deposit/approve/id/" in url:
            self.calls.append("approve dd")
            self.dd_approved = True
            return Resp(302, "", {"Location": "/inv/deposit/view/id/777"})
        if "/inv/deposit/view/id/" in url:
            self.calls.append("GET dd view")
            items = [("1.00", "")] * len(self.dd_items)
            return Resp(200, _view("LBDD26100001", items, "boss" if self.dd_approved else None,
                                   code_label="เลขที่"))
        if "spare.part.returned" in url:
            self.calls.append("returned")
            return Resp(302, "", {"Location": "/inv/withdraw/index"})
        return Resp(404, "")


def make(**kw):
    c = AtmsWithdrawClient(phpsessid="x", pace=0)
    c._sess = WriterSession(**kw)
    c.calls = c._sess.calls
    return c


@pytest.fixture
def fake_writer():
    return make()


def test_issue_full_sequence(fake_writer):
    p = fake_writer.issue_withdraw(4, 45, TOKEN, LINES2)
    assert p.gm_id == NEW_ID and p.done == [0, 1] and p.approved and p.code.startswith("LBWD")
    assert fake_writer.calls == ["GET index(remark)", "POST withdraw/add", "POST item",
                                 "POST item", "approve", "GET view"]


def test_header_and_item_fields(fake_writer):
    fake_writer.issue_withdraw(4, 45, TOKEN, [IssueLine(6106, 2, "แบตเตอรี่ N50ZL", 257041, remark="r")], remark="ทะเบียน")
    posts = [r for r in fake_writer._sess.reqs if r[0] == "POST"]
    h = posts[0][2]["data"]
    assert h["type"] == "withdraw" and h["inventory_id"] == 4 and h["pick_to_id"] == 45
    assert h["remark"] == f"{TOKEN} ทะเบียน" and h["extra_cost"] == "0.00"
    assert posts[1][1].endswith(f"/inv/withdraw.item/add/good_movement_id/{NEW_ID}/inventory_id/4")
    d = posts[1][2]["data"]
    assert d["sku_id"] == 6106 and d["amount"] == "2" and d["maintenance_request_item_id"] == 257041
    assert all(r[2]["allow_redirects"] is False for r in posts)


def test_posts_never_use_retrying_request(fake_writer, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("POST ต้องไม่ผ่าน _request ที่ retry")
    real = fake_writer._request
    monkeypatch.setattr(fake_writer, "_request",
                        lambda m, *a, **k: boom() if m == "POST" else real(m, *a, **k))
    assert fake_writer.issue_withdraw(4, 45, TOKEN, LINES2).approved


def test_post_connection_error_not_retried():
    c = make(exc_on_item=1)
    with pytest.raises(WithdrawPartialError) as e:
        c.issue_withdraw(4, 45, TOKEN, LINES2)
    assert c._sess.item_posts == 1
    assert e.value.progress.gm_id == NEW_ID and e.value.progress.done == []


def test_resume_skips_done_lines():
    c = make()
    c._sess.preseed = 1
    p = c.issue_withdraw(4, 45, TOKEN, LINES3, progress=IssueProgress(gm_id=NEW_ID, done=[0]))
    assert c.calls.count("POST item") == 2
    assert "POST withdraw/add" not in c.calls and "GET index(remark)" not in c.calls
    assert p.done == [0, 1, 2] and p.approved


def test_resume_refuses_when_atms_line_count_disagrees():
    c = make()
    c._sess.preseed = 2        # ATMS มี 2 บรรทัด แต่ progress บอกว่าส่งแล้ว 1
    with pytest.raises(WithdrawPartialError):
        c.issue_withdraw(4, 45, TOKEN, LINES3, progress=IssueProgress(gm_id=NEW_ID, done=[0]))
    assert "POST item" not in c.calls


def test_issue_without_gm_id_searches_remark_first():
    c = make(existing=True)
    c._sess.preseed = 0
    p = c.issue_withdraw(4, 45, "LBMR26100579", LINES2)
    assert "POST withdraw/add" not in c.calls and p.gm_id == EXISTING_ID


def test_multiple_docs_with_token_raises():
    c = make(existing=True)
    c.find_withdraw_by_remark = lambda *a, **k: [1, 2]
    with pytest.raises(AtmsOpenJobError, match="พบหลายใบ"):
        c.issue_withdraw(4, 45, TOKEN, LINES2)
    assert "POST withdraw/add" not in c.calls


def test_header_302_without_id_falls_back_to_remark_search():
    c = make(header_location=False)
    seq = iter([[], [NEW_ID]])
    c.find_withdraw_by_remark = lambda *a, **k: next(seq)
    p = c.issue_withdraw(4, 45, TOKEN, LINES2)
    assert p.gm_id == NEW_ID


def test_failure_mid_lines_returns_progress():
    c = make(fail_item_n=2)
    with pytest.raises(WithdrawPartialError) as e:
        c.issue_withdraw(4, 45, TOKEN, LINES3)
    assert e.value.progress.gm_id == NEW_ID and e.value.progress.done == [0]
    assert "amount ผิด" in str(e.value)


def test_header_validation_error_raises_plain_error():
    c = make(form_error_on="header")
    with pytest.raises(AtmsOpenJobError, match="เลือกคลัง"):
        c.issue_withdraw(4, 45, TOKEN, LINES2)


def test_approve_not_confirmed_raises():
    c = make(approve_works=False)
    with pytest.raises(WithdrawPartialError, match="ATMS ยังไม่อนุมัติใบ LBWD26102300") as e:
        c.issue_withdraw(4, 45, TOKEN, LINES2)
    assert e.value.progress.done == [0, 1] and not e.value.progress.approved


def test_dry_run_makes_no_request(fake_writer):
    fake_writer.dry_run = True
    p = fake_writer.issue_withdraw(4, 45, TOKEN, LINES2)
    assert fake_writer._sess.reqs == [] and p.gm_id == 0


def test_pace_sleeps_before_every_request(monkeypatch):
    c = make()
    c.pace = 0.5
    slept = []
    monkeypatch.setattr("services.atms_withdraw_service.time.sleep", slept.append)
    c.issue_withdraw(4, 45, TOKEN, LINES2)
    assert len(slept) == len(c._sess.reqs) and len(slept) >= 8   # ทุก request ทั้งอ่านและเขียน


def test_return_to_stock_sequence(fake_writer):
    p = fake_writer.return_to_stock(4, NEW_ID, "LBWD26102300", TOKEN + "-R1", [RETURN_LINE],
                                    mark_complete=True)
    assert p.approved and p.gm_id == 777 and p.done == [0] and p.code == "LBDD26100001"
    assert fake_writer.calls == ["GET dd index", "POST deposit/add", "POST deposit item", "approve dd",
                                 "GET dd view", "returned"]
    posts = [r for r in fake_writer._sess.reqs if r[0] == "POST"]
    h = posts[0][2]["data"]
    assert h["type"] == "deposit" and h["purchase_order_id"] == "" and h["withdraw"] == "LBWD26102300"
    assert h["withdraw_id"] == NEW_ID and h["supplier_ref_no"] == TOKEN + "-R1" and h["extra_cost"] == "0.00"
    assert re.fullmatch(r"\d\d/\d\d/\d{4}", h["supplier_inv_date"])
    assert posts[1][2]["data"]["unit_price"] == "248.25"
    assert posts[1][1].endswith("/inv/deposit.item/add/good_movement_id/777/inventory_id/4")


def test_return_dry_run(fake_writer):
    fake_writer.dry_run = True
    p = fake_writer.return_to_stock(4, NEW_ID, "X", TOKEN, [RETURN_LINE])
    assert fake_writer._sess.reqs == [] and p.gm_id == 0


def test_return_not_approved_raises():
    c = make()
    c._sess.dd_approved = False
    orig = c._sess.request

    def req(method, url, **kw):
        r = orig(method, url, **kw)
        if "/inv/deposit/approve/id/" in url:
            c._sess.dd_approved = False
        return r
    c._sess.request = req
    with pytest.raises(WithdrawPartialError, match="ยังไม่อนุมัติ"):
        c.return_to_stock(4, NEW_ID, "X", TOKEN, [RETURN_LINE])


# ── แก้ของ Task 1 ──────────────────────────────────────────────────────────
class _LoginThenOk:
    def __init__(self, first_login):
        self.n = 0
        self.first_login = first_login

    def request(self, method, url, **kw):
        self.n += 1
        if self.first_login and self.n == 1:
            return Resp(302, "", {"Location": "/account/user/login"})
        return Resp(200, "[]")


def test_lookup_relogins_once_when_possible(monkeypatch):
    c = AtmsWithdrawClient(username="u", password="p", pace=0)
    c._sess = _LoginThenOk(True)
    fresh = _LoginThenOk(False)
    monkeypatch.setattr(c, "_new_session", lambda: fresh)
    assert c.lookup("sku", "x") == []
    assert fresh.n == 1


def test_lookup_login_redirect_without_credentials_still_raises():
    c = AtmsWithdrawClient(phpsessid="x", pace=0)
    c._sess = _LoginThenOk(True)
    with pytest.raises(AtmsAuthError):
        c.lookup("sku", "x")


def test_set_rows_per_page_raises_on_http_error():
    class S:
        def request(self, *a, **k):
            return Resp(500, "")
    c = AtmsWithdrawClient(phpsessid="x", pace=0)
    c._sess = S()
    with pytest.raises(AtmsOpenJobError):
        c._set_rows_per_page(1000)


# ── fix round 1 ────────────────────────────────────────────────────────────
def test_item_payload_has_sku_name_and_mr_text(fake_writer):
    fake_writer.issue_withdraw(4, 45, TOKEN, LINES2)
    items = [r[2]["data"] for r in fake_writer._sess.reqs
             if r[0] == "POST" and "withdraw.item" in r[1]]
    assert items[0]["sku"] == "แบตเตอรี่ N50ZL" and items[0]["maintenance_request_item"] == "257041"
    assert items[1]["sku"] == "น้ำมันเครื่อง" and items[1]["maintenance_request_item"] == ""


def test_return_item_payload_has_sku_name(fake_writer):
    fake_writer.return_to_stock(4, NEW_ID, "LBWD1", TOKEN, [RETURN_LINE])
    d = [r[2]["data"] for r in fake_writer._sess.reqs if r[0] == "POST" and "deposit.item" in r[1]][0]
    assert d["sku"] == "แบตเตอรี่ N50ZL"


def test_return_searches_existing_dd_first_and_reuses():
    c = make()
    c._sess.dd_existing = True
    p = c.return_to_stock(4, NEW_ID, "LBWD1", "ORVCM6909000054", [RETURN_LINE])
    assert "POST deposit/add" not in c.calls and p.gm_id == 555232


def test_return_multiple_dd_raises():
    c = make()
    c.find_deposit_by_remark = lambda *a, **k: [1, 2]
    with pytest.raises(AtmsOpenJobError, match="พบหลายใบ"):
        c.return_to_stock(4, NEW_ID, "LBWD1", TOKEN, [RETURN_LINE])
    assert "POST deposit/add" not in c.calls


def test_return_header_302_without_id_falls_back_to_search():
    c = make()
    seq = iter([[], [777]])
    c.find_deposit_by_remark = lambda *a, **k: next(seq)
    orig = c._sess.request

    def req(m, url, **kw):
        if m == "POST" and url.endswith("/inv/deposit/add"):
            c.calls.append("POST deposit/add")
            return Resp(302, "", {"Location": "/inv/deposit/index"})
        return orig(m, url, **kw)
    c._sess.request = req
    assert c.return_to_stock(4, NEW_ID, "LBWD1", TOKEN, [RETURN_LINE]).gm_id == 777


def test_find_deposit_by_remark_matches_ref_no_on_boundary(fake_writer):
    fake_writer._sess.dd_existing = True
    assert fake_writer.find_deposit_by_remark("ORVCM6909000054", 4, "01/10/2026") == [555232]
    assert fake_writer.find_deposit_by_remark("ORVCM690900005", 4, "01/10/2026") == []
    rpp = [r[2]["params"]["row-per-page"] for r in fake_writer._sess.reqs if "set.row.per.page" in r[1]]
    assert rpp[:2] == [1000, 25]


def test_pace_applies_to_login(monkeypatch):
    slept = []
    monkeypatch.setattr("services.atms_withdraw_service.time.sleep", slept.append)
    c = AtmsWithdrawClient(username="u", password="p", pace=0.7)
    calls = []

    class S:
        headers = {}
        cookies = {}
        verify = True
        url = "https://x/inv/job/add"

        def get(self, *a, **k):
            calls.append("get")
            return self

        def post(self, *a, **k):
            calls.append("post")
            return self
    monkeypatch.setattr("services.atms_withdraw_service.requests.Session", lambda: S())
    c.session
    assert calls == ["get", "post", "get"] and len(slept) == 3


def test_no_env_fallback(monkeypatch):
    monkeypatch.setenv("PHPSESSID", "envsess")
    monkeypatch.setenv("ATMS_USERNAME", "envu")
    monkeypatch.setenv("ATMS_PASSWORD", "envp")
    with pytest.raises(AtmsAuthError, match="ต้องส่งบัญชี ATMS ของผู้ใช้มา"):
        AtmsWithdrawClient()
    c = AtmsWithdrawClient(phpsessid="mine")
    assert c.username is None and c.password is None and c.phpsessid == "mine"


def test_approved_doc_with_missing_lines_not_posted_to():
    c = make()
    c._sess.preseed = 1
    c._sess.wd_approved_preseed = True
    with pytest.raises(WithdrawPartialError, match="อนุมัติแล้วแต่รายการยังไม่ครบ"):
        c.issue_withdraw(4, 45, TOKEN, LINES3, progress=IssueProgress(gm_id=NEW_ID, done=[0]))
    assert "POST item" not in c.calls


def test_progress_approved_but_lines_missing_not_posted_to():
    c = make()
    c._sess.preseed = 1
    c._sess.wd_approved_preseed = True
    with pytest.raises(WithdrawPartialError, match="อนุมัติแล้วแต่รายการยังไม่ครบ"):
        c.issue_withdraw(4, 45, TOKEN, LINES3,
                         progress=IssueProgress(gm_id=NEW_ID, done=[0], approved=True))
    assert "POST item" not in c.calls


def test_approved_and_complete_runs_final_check_without_approve():
    c = make()
    c._sess.preseed = 2
    c._sess.wd_approved_preseed = True
    p = c.issue_withdraw(4, 45, TOKEN, LINES2, progress=IssueProgress(gm_id=NEW_ID, done=[0, 1]))
    assert p.approved and "approve" not in c.calls and "POST item" not in c.calls


def test_remark_token_boundary(fake_client):
    from tests.atms.test_withdraw_read import TOKEN_IN_FIXTURE
    assert fake_client.find_withdraw_by_remark(TOKEN_IN_FIXTURE[:-1], 4, "31/10/2026") == []
    assert fake_client.find_withdraw_by_remark(TOKEN_IN_FIXTURE, 4, "31/10/2026") != []
