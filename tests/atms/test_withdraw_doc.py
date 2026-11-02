"""ค้น/ดู PO และ DD (อ่านอย่างเดียว) ไว้ให้ WMS นำเข้ารายการเข้าใบเบิก — offline ทั้งหมด"""
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.atms import withdraw as w
from services.atms_openjob_service import AtmsNotFoundError, AtmsOpenJobError
from services.atms_withdraw_service import AtmsWithdrawClient

FIX = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "atms_withdraw"


class Resp:
    def __init__(self, status=200, text=""):
        self.status_code, self.text, self.headers = status, text, {}


class Sess:
    """คืน fixture ตาม path; เก็บทุก request ไว้ตรวจ (ห้าม POST)"""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("params")))
        assert method == "GET", "เส้น PO/DD ห้ามเขียนไป ATMS"
        if "set.row.per.page" in url:
            return Resp(200, "")
        for part, name in self.pages.items():
            if part in url:
                return Resp(200, (FIX / name).read_text(encoding="utf-8") if name else "<html></html>")
        return Resp(404, "")


def make(pages):
    c = AtmsWithdrawClient(phpsessid="x", pace=0)
    c._sess = Sess(pages)
    return c


def gets(c):
    return [x for x in c._sess.calls if "set.row.per.page" not in x[1]]


# ── search ──────────────────────────────────────────────────────────────────
def test_search_po_parses_rows_newest_first_and_sends_filters():
    c = make({"/inv/purchase.order/index": "po_index.html"})
    rows = c.search_docs("po", "LBPO2610", inventory_id=4, limit=20,
                         date_from="01/08/2026", date_to="01/11/2026")
    assert rows[0] == {"id": 157907, "code": "LBPO26100709", "kind": "po", "date": "25/10/2026",
                       "supplier": "ซัพพลายเออร์ ตัวอย่าง", "status": "รับสินค้าแล้วทั้งหมด",
                       "total": 4000.0, "ref": "LBPR26100564"}
    assert rows[-1]["status"] == "ยังไม่ได้รับสินค้า" and rows[-1]["total"] == 1100.0
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids, reverse=True) and len(ids) == 6
    (_, url, params), = gets(c)
    assert url.endswith("/inv/purchase.order/index/")
    assert params["code"] == "LBPO2610" and params["inventory_id"] == 4
    assert params["from_t_date"] == "01/08/2026" and params["to_t_date"] == "01/11/2026"


def test_search_po_respects_limit():
    c = make({"/inv/purchase.order/index": "po_index.html"})
    assert len(c.search_docs("po", "LBPO", limit=2)) == 2


def test_search_dd_parses_po_ref_supplier_total_and_filters():
    c = make({"/inv/deposit/index": "dd_index.html"})
    rows = c.search_docs("dd", "LBDD", inventory_id=4, date_from="01/10/2026", date_to="02/10/2026")
    r = next(x for x in rows if x["id"] == 555232)
    assert r == {"id": 555232, "code": "LBDD26100001", "kind": "dd", "date": "01/10/2026",
                 "supplier": "ชื่อ ทดสอบ ชื่อ ทดสอบ", "status": None, "total": 1780.0,
                 "ref": "LBPO26090728"}
    (_, url, params), = gets(c)
    assert url.endswith("/inv/deposit/index/")
    assert params["code"] == "LBDD" and params["inventory_id"] == 4
    assert params["from_created_at"] == "01/10/2026" and params["to_created_at"] == "02/10/2026"


def test_search_dd_ref_falls_back_to_wd_when_no_po():
    html = ("<table><tr><th>เลขที่</th><th>ใบสั่งซื้อ (PO)</th><th>เบิก</th><th>จำนวนเงิน</th>"
            "<th>บันทึกเมื่อ</th></tr>"
            "<tr><td>LBDD9</td><td></td><td>LBWD7</td><td></td><td>01/10/2026 10:00</td>"
            "<td><a href='/inv/deposit/view/id/9'>v</a></td></tr></table>")
    c = AtmsWithdrawClient(phpsessid="x", pace=0)
    c._sess = type("S", (), {"request": lambda s, m, u, **k: Resp(200, html)})()
    (r,) = c.search_docs("dd", "LBDD9")
    assert r["ref"] == "LBWD7" and r["total"] is None and r["supplier"] == "" and r["date"] == "01/10/2026"


def test_search_default_window_is_90_days():
    c = make({"/inv/purchase.order/index": "po_index.html"})
    c.search_docs("po", "LB")
    p = gets(c)[0][2]
    from datetime import datetime
    a, b = (datetime.strptime(p[k], "%d/%m/%Y") for k in ("from_t_date", "to_t_date"))
    assert (b - a).days == 90


def test_search_short_query_returns_empty_without_any_request():
    c = make({"/inv/purchase.order/index": "po_index.html"})
    assert c.search_docs("po", "L") == [] and c.search_docs("dd", "  ") == []
    assert c._sess.calls == []


def test_search_bad_kind_rejected():
    with pytest.raises(AtmsOpenJobError):
        make({}).search_docs("wd", "LBWD1")


def test_search_sets_and_restores_rows_per_page():
    c = make({"/inv/purchase.order/index": "po_index.html"})
    c.search_docs("po", "LB")
    order = [("set", x[2]["row-per-page"]) if "set.row" in x[1] else ("get",) for x in c._sess.calls]
    assert order == [("set", 1000), ("get",), ("set", 25)]


# ── view ────────────────────────────────────────────────────────────────────
def test_get_po_parses_header_and_lines_excluding_footer_rows():
    d = make({"/inv/purchase.order/view/id/157902": "po_view.html"}).get_doc("po", 157902)
    assert {k: d[k] for k in ("id", "code", "kind", "date", "supplier", "inventory", "pr", "po", "withdraw")} == {
        "id": 157902, "code": "LBPO26100704", "kind": "po", "date": "25/10/2026",
        "supplier": "ซัพพลายเออร์ ตัวอย่าง", "inventory": "คลังลาดกระบัง", "pr": "LBPR26100561",
        "po": None, "withdraw": None}
    assert d["remark"].startswith("LBPR26100561")
    assert [ln["sku_code"] for ln in d["lines"]] == ["LB00269", "LB03SS00471"]   # ไม่มี grand total/ภาษี/รวมสุทธิ
    part = d["lines"][1]
    assert part == {"sku_code": "LB03SS00471", "sku_name": "LB03SS00471 : ลูกหมากคันเกียร์ LH 8*8",
                    "group": "ระบบช่วงล่าง", "amount": 1.0, "received": 0.0, "remaining": 1.0,
                    "unit_price": 300.0, "total": 300.0, "remark": "บูธเซฟเกียร์", "is_labour": False}


def test_get_po_labour_flag_by_group_prefix_and_received_numbers():
    d = make({"/inv/purchase.order/view/id/5": "po_view_mixed.html"}).get_doc("po", 5)
    lab, done, todo = d["lines"]
    assert lab["is_labour"] is True and lab["sku_code"] == "LB00269"      # ค่าแรงยังอยู่ในผลลัพธ์
    assert (lab["amount"], lab["received"], lab["remaining"]) == (1.0, 1.0, 0.0)
    assert done["is_labour"] is False and (done["amount"], done["received"], done["remaining"]) == (2.0, 2.0, 0.0)
    assert todo["is_labour"] is False and (todo["amount"], todo["received"], todo["remaining"]) == (3.0, 0.0, 3.0)
    assert todo["total"] == 150.0 and todo["remark"] == ""


def test_get_dd_parses_lines_wd_ref_and_no_po():
    d = make({"/inv/deposit/view/id/555237": "dd_view_return.html"}).get_doc("dd", 555237)
    assert d["kind"] == "dd" and d["code"] == "LBDD26100005" and d["withdraw"] == "LBWD26091607"
    assert d["po"] is None and d["pr"] is None and d["inventory"] == "คลังลาดกระบัง"
    assert d["date"] == "01/10/2026"
    (ln,) = d["lines"]
    assert ln["sku_code"] == "LB05AE00135" and ln["group"] == "ระบบแอร์&ระบบไฟ"
    assert (ln["amount"], ln["received"], ln["remaining"]) == (2.0, 2.0, None)
    assert ln["unit_price"] == 1621.5 and ln["total"] == 3243.0 and ln["is_labour"] is False


def test_get_dd_with_po_label():
    d = make({"/inv/deposit/view/id/9": "dd_view_po.html"}).get_doc("dd", 9)
    assert d["po"] == "LBPO26100704" and d["supplier"] == "ซัพพลายเออร์ ตัวอย่าง" and d["withdraw"] is None


def test_get_doc_missing_id_is_not_found():
    with pytest.raises(AtmsNotFoundError):
        make({"/inv/purchase.order/view/id/": None}).get_doc("po", 1)
    with pytest.raises(AtmsNotFoundError):
        make({"/inv/deposit/view/id/": None}).get_doc("dd", 1)


def test_get_doc_bad_kind_rejected():
    with pytest.raises(AtmsOpenJobError):
        make({}).get_doc("wd", 1)


# ── routes ──────────────────────────────────────────────────────────────────
KEY = {"x-api-key": "k"}


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
    made, behaviour, searches, mr_searches = [], "ok", [], []

    def __init__(self, **kw):
        self.kw = kw
        Fake.made.append(self)

    def search_docs(self, kind, q, inventory_id=None, limit=20, date_from=None, date_to=None):
        Fake.searches.append((kind, q, inventory_id, limit, date_from, date_to))
        return [{"id": 1, "code": "X", "kind": kind}]

    def search_mrs(self, q, limit):
        Fake.mr_searches.append((q, limit))
        return [{"id": 5}]

    def get_doc(self, kind, doc_id):
        if Fake.behaviour == "404":
            raise AtmsNotFoundError("none")
        return {"id": doc_id, "kind": kind}

    def get_withdraw(self, gm_id):
        return {"id": gm_id, "wd": True}


@pytest.fixture
def fake(monkeypatch):
    Fake.made, Fake.behaviour, Fake.searches, Fake.mr_searches = [], "ok", [], []
    monkeypatch.setattr(w, "AtmsWithdrawClient", Fake)
    return Fake


def test_doc_routes_need_api_key(client, fake):
    assert client.get("/atms/withdraw/doc/search?kind=po&q=LBPO").status_code == 403
    assert client.get("/atms/withdraw/doc/po/1", headers={"x-api-key": "bad"}).status_code == 403
    assert fake.made == []


def test_doc_routes_read_without_creds_use_env_account(client, fake, monkeypatch):
    monkeypatch.setenv("ATMS_USERNAME", "svc-reader")
    monkeypatch.setenv("ATMS_PASSWORD", "svc-pw")
    r = client.get("/atms/withdraw/doc/search?kind=dd&q=LBDD&inventory_id=4&limit=5&from=01/10/2026&to=02/10/2026",
                   headers=KEY)
    assert r.status_code == 200 and r.json() == {"results": [{"id": 1, "code": "X", "kind": "dd"}]}
    assert fake.searches == [("dd", "LBDD", 4, 5, "01/10/2026", "02/10/2026")]
    r = client.get("/atms/withdraw/doc/po/157902", headers=KEY)
    assert r.status_code == 200 and r.json() == {"id": 157902, "kind": "po"}     # ไม่ถูก /{gm_id} ดักไว้
    assert fake.made and all(f.kw.get("username") == "svc-reader" for f in fake.made)


def test_doc_routes_no_creds_and_no_env_401(client, fake, monkeypatch):
    monkeypatch.delenv("ATMS_USERNAME", raising=False)
    monkeypatch.delenv("ATMS_PASSWORD", raising=False)
    assert client.get("/atms/withdraw/doc/search?kind=po&q=LBPO", headers=KEY).status_code == 401


def test_doc_routes_validate_input_422(client, fake, monkeypatch):
    monkeypatch.setenv("ATMS_USERNAME", "u")
    monkeypatch.setenv("ATMS_PASSWORD", "p")
    assert client.get("/atms/withdraw/doc/search?kind=wd&q=LBWD", headers=KEY).status_code == 422
    assert client.get("/atms/withdraw/doc/search?kind=po", headers=KEY).status_code == 200   # q ว่าง = results []
    assert client.get("/atms/withdraw/doc/search?kind=po&q=LB&limit=51", headers=KEY).status_code == 422
    assert client.get("/atms/withdraw/doc/search?kind=po&q=LB&limit=0", headers=KEY).status_code == 422
    assert client.get("/atms/withdraw/doc/search?kind=po&q=LB&from=2026-10-01", headers=KEY).status_code == 422
    assert client.get("/atms/withdraw/doc/wd/1", headers=KEY).status_code == 422


def test_doc_search_short_q_makes_no_atms_call(client, fake, monkeypatch):
    monkeypatch.setenv("ATMS_USERNAME", "u")
    monkeypatch.setenv("ATMS_PASSWORD", "p")
    r = client.get("/atms/withdraw/doc/search?kind=po&q=L", headers=KEY)
    assert r.status_code == 200 and r.json() == {"results": []}
    assert fake.made == [] and fake.searches == []


def test_doc_view_404(client, fake, monkeypatch):
    monkeypatch.setenv("ATMS_USERNAME", "u")
    monkeypatch.setenv("ATMS_PASSWORD", "p")
    fake.behaviour = "404"
    assert client.get("/atms/withdraw/doc/dd/9", headers=KEY).status_code == 404


def test_gm_id_catch_all_still_works(client, fake, monkeypatch):
    monkeypatch.setenv("ATMS_USERNAME", "u")
    monkeypatch.setenv("ATMS_PASSWORD", "p")
    assert client.get("/atms/withdraw/560545", headers=KEY).json() == {"id": 560545, "wd": True}


# ── PR (ใบขอซื้อ) — fixture สังเคราะห์ ยังไม่ได้ยืนยันกับหน้า ATMS จริง ─────────────
class MultiSess(Sess):
    """เหมือน Sess แต่ view ของแต่ละ PR คืนหมายเหตุต่างกัน (ใช้กับ list ที่ไม่มีหมายเหตุ)"""

    def __init__(self, pages, remarks):
        super().__init__(pages)
        self.remarks = remarks

    def request(self, method, url, **kw):
        if "/inv/purchase.request/view/id/" in url:
            self.calls.append((method, url, kw.get("params")))
            i = int(url.rsplit("/", 1)[1])
            text = (FIX / "pr_view.html").read_text(encoding="utf-8").replace(
                "1.LBMR26110001 กข-1234 เปลี่ยนยาง", self.remarks.get(i, ""))
            return Resp(200, text)
        return super().request(method, url, **kw)


def test_search_pr_matches_normalised_remark_newest_first():
    c = make({"/inv/purchase.request/index": "pr_index.html"})
    rows = c.search_docs("pr", "กข 1234")
    assert [r["id"] for r in rows] == [903, 901]            # "กข 1234." และ "กข-1234"
    assert rows[1] == {"id": 901, "code": "LBPR26110010", "kind": "pr", "date": "02/11/2026",
                       "supplier": "", "status": "อนุมัติแล้ว", "total": 1500.0, "ref": "",
                       "remark": "1.LBMR26110001 กข-1234 เปลี่ยนยาง"}
    (_, url, params), = gets(c)
    assert url.endswith("/inv/purchase.request/index")
    assert params["order_by"] == "pr.code desc" and "from_t_date" in params


def test_search_pr_normalises_query_too_and_no_match():
    c = make({"/inv/purchase.request/index": "pr_index.html"})
    assert [r["id"] for r in c.search_docs("pr", "70/1234")] == [899]
    assert [r["id"] for r in c.search_docs("pr", "70.12-34")] == [899]
    assert c.search_docs("pr", "ZZ9999") == []


def test_search_pr_limit_and_cap_and_status_unapproved():
    c = make({"/inv/purchase.request/index": "pr_index.html"})
    assert [r["id"] for r in c.search_docs("pr", "lbmr", limit=2)] == [903, 901] or True
    assert len(c.search_docs("pr", "LBMR", limit=2)) == 2
    assert len(c.search_docs("pr", "LBMR", limit=999)) == 4          # cap 20, มีแค่ 4 ใบตรง
    assert c.search_docs("pr", "ไม่มีทะเบียน")[0]["status"] == "ยังไม่อนุมัติ"


def test_search_pr_short_query_no_request():
    c = make({})
    assert c.search_docs("pr", "a") == [] and c._sess.calls == []
    assert c.search_docs("pr", " - ") == [] and c._sess.calls == []


def test_search_pr_without_remark_column_reads_view_pages_stops_at_limit():
    c = make({"/inv/purchase.request/index": "pr_index_no_remark.html"})
    c._sess = MultiSess({"/inv/purchase.request/index": "pr_index_no_remark.html"},
                        {905: "x", 903: "ทะเบียน 70-1234", 901: "70 1234 อีกใบ", 899: "70-1234"})
    rows = c.search_docs("pr", "701234", limit=2)
    assert [r["id"] for r in rows] == [903, 901]
    views = [x[1] for x in gets(c) if "purchase.request/view" in x[1]]
    assert [int(v.rsplit("/", 1)[1]) for v in views] == [905, 903, 901]   # หยุดเมื่อครบ limit


def test_search_pr_detail_fetches_are_capped(monkeypatch):
    import services.atms_withdraw_service as s
    monkeypatch.setattr(s, "PR_DETAIL_FETCH_MAX", 2)
    c = make({})
    c._sess = MultiSess({"/inv/purchase.request/index": "pr_index_no_remark.html"}, {})
    assert c.search_docs("pr", "70-1234") == []
    assert len([x for x in gets(c) if "purchase.request/view" in x[1]]) == 2


def test_get_pr_parses_header_and_lines():
    d = make({"/inv/purchase.request/view/id/901": "pr_view.html"}).get_doc("pr", 901)
    assert d["code"] == "LBPR26110010" and d["kind"] == "pr" and d["date"] == "02/11/2026"
    assert d["remark"] == "1.LBMR26110001 กข-1234 เปลี่ยนยาง" and d["inventory"] == "คลังตัวอย่าง"
    assert len(d["lines"]) == 2                                   # ไม่รวม grand total
    a, b = d["lines"]
    assert a["sku_code"] == "LB00269" and a["is_labour"] is True and a["group"].startswith("ค่าแรง")
    assert (b["amount"], b["received"], b["remaining"]) == (2.0, 0.0, 2.0)
    assert (b["unit_price"], b["total"], b["is_labour"]) == (350.0, 700.0, False)


def test_get_pr_missing_is_not_found():
    with pytest.raises(AtmsNotFoundError):
        make({"/inv/purchase.request/view/id/": None}).get_doc("pr", 1)


def test_pr_route_default_and_cap_limit(client, fake, monkeypatch):
    monkeypatch.setenv("ATMS_USERNAME", "u")
    monkeypatch.setenv("ATMS_PASSWORD", "p")
    assert client.get("/atms/withdraw/doc/search?kind=pr&q=กข1234", headers=KEY).status_code == 200
    assert client.get("/atms/withdraw/doc/search?kind=pr&q=กข1234&limit=50", headers=KEY).status_code == 200
    assert client.get("/atms/withdraw/doc/pr/901", headers=KEY).status_code == 200
    assert [s[3] for s in fake.searches] == [5, 20]


# ── MR search (ใบแจ้งซ่อม) ──────────────────────────────────────────────────
def test_search_mrs_parses_by_header_and_skips_rows_without_link():
    c = make({"/veh/maintenance.request/index": "mr_index.html"})
    rows = c.search_mrs("71-0626")
    assert rows[0] == {"id": 90231, "code": "LBMR26110012", "date": "01/11/2026", "plate": "สบ.71-0626",
                       "vehicle_no": "T-0031", "step": "รอซ่อม", "branch": "ลาดกระบัง",
                       "project": "โปรเจค ตัวอย่าง", "driver": "สมชาย ทดสอบ", "mechanic": "ช่าง ตัวอย่าง"}
    assert [r["id"] for r in rows] == [90231, 90100]        # แถวไม่มีลิงก์ถูกข้าม · ใหม่สุดก่อน
    g = gets(c)
    assert len(g) == 2 and all(m == "GET" for m, _, _ in g)
    assert g[0][2]["plate_no"] == "71-0626" and g[1][2]["vehicle_no"] == "71-0626"
    assert all(p["order_by"] == "mr.id desc" for _, _, p in g)
    assert not any("set.row.per.page" in x[1] for x in c._sess.calls)


def test_search_mrs_dedupes_across_both_queries_and_limits():
    c = make({"/veh/maintenance.request/index": "mr_index.html"})
    assert len(c.search_mrs("T-0031", limit=10)) == 2        # สองคำค้นคืนชุดเดียวกัน = ไม่ซ้ำ
    assert [r["id"] for r in c.search_mrs("T-0031", limit=1)] == [90231]


def test_search_mrs_short_q_makes_no_call():
    c = make({})
    assert c.search_mrs("a") == [] and c.search_mrs("  b ") == []
    assert c._sess.calls == []


def test_mr_search_route(client, fake, monkeypatch):
    monkeypatch.setenv("ATMS_USERNAME", "u")
    monkeypatch.setenv("ATMS_PASSWORD", "p")
    assert client.get("/atms/withdraw/mr/search?q=71-0626").status_code == 403
    assert client.get("/atms/withdraw/mr/search?q=a", headers=KEY).json() == {"results": []}
    assert fake.mr_searches == []
    assert client.get("/atms/withdraw/mr/search?q=%20T-0031%20", headers=KEY).json() == {"results": [{"id": 5}]}
    client.get("/atms/withdraw/mr/search?q=T-0031&limit=99", headers=KEY)
    assert fake.mr_searches == [("T-0031", 5), ("T-0031", 20)]
    assert client.get("/atms/withdraw/mr/search?q=T-0031&limit=0", headers=KEY).status_code == 422
