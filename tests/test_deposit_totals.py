"""POST /atms/deposit/refresh — ดึงยอดรวมสุทธิของใบ DD สดตามคำขอ

ไม่แตะ ATMS และไม่แตะ Mongo: ปลอม _fetch (หน้าใบ DD) + deposit_header ไว้ในหน่วยความจำ
เพราะสิ่งที่ต้องกันไม่ให้พังคือ *การตัดสินใจ* ของ endpoint — ใบไหนต้องยิงซ้ำ ใบไหนพอแล้ว
ใบไหนถือว่ายอดเปลี่ยน — ไม่ใช่การ parse HTML (มี parse_deposit_totals คุมอยู่แล้ว)
"""
from datetime import datetime, timedelta, timezone

import pytest

import routes.atms.deposit_totals as m


class FakeCol:
    def __init__(self, docs):
        self.docs = {d["deposit_code"]: dict(d) for d in docs}
        self.writes = []

    def find(self, q, proj=None):
        want = set(q["deposit_code"]["$in"])
        return [dict(d) for c, d in self.docs.items() if c in want]

    def update_one(self, q, u):
        self.writes.append((q, u))
        for d in self.docs.values():
            if d.get("deposit_id") == q.get("deposit_id"):
                d.update(u.get("$set", {}))
                for k in u.get("$unset", {}):
                    d.pop(k, None)


@pytest.fixture
def col(monkeypatch):
    now = datetime.now(timezone.utc)
    c = FakeCol([
        # ยอดในฐานค้างที่ 1.00 ขณะที่หน้า ATMS เป็น 3,959.00 (ถูกแก้ VAT ทีหลัง)
        {"deposit_code": "KKDD26090025", "deposit_id": 554657, "amount": "3,700.00",
         "amount_net": "1.00", "vat_rate": "0", "totals_at": now - timedelta(days=6)},
        # เพิ่งดึงไป 10 วินาที
        {"deposit_code": "LBDD26100363", "deposit_id": 584001, "amount": "400.00",
         "amount_net": "428.00", "vat_rate": "7", "totals_at": now - timedelta(seconds=10)},
        # ใบยกเลิก: หน้า DD ไม่มีตารางยอด แต่หัวใบยังมียอดค้างอยู่
        {"deposit_code": "KKDD26030043", "deposit_id": 522972, "amount": "2,500.00"},
        # ใบเปล่ายอด 0.00 — ไม่มีตารางยอดเหมือนกัน แต่ถือว่า "ไม่มียอดจริง" ไม่ใช่ความผิดพลาด
        {"deposit_code": "LBDD26010001", "deposit_id": 400001, "amount": "0.00"},
    ])
    monkeypatch.setattr(m, "_db", lambda: {"deposit_header": c})
    monkeypatch.setattr(m, "_fetch", lambda h: {
        554657: {"grand_total": "3,700.00", "vat_rate": "7", "vat_amount": "259.00", "amount_net": "3,959.00"},
        584001: {"grand_total": "400.00", "vat_rate": "7", "vat_amount": "28.00", "amount_net": "428.00"},
    }.get(h["deposit_id"], {}))
    return c


def rows_of(out):
    return {r["deposit_code"]: r for r in out["rows"] if r.get("deposit_code")}


def test_stale_bill_is_refetched_and_flagged(col):
    out = m.refresh_totals(m.RefreshRequest(codes=["KKDD26090025"]))
    r = rows_of(out)["KKDD26090025"]
    assert (r["source"], r["amount_net"], r["vat_rate"], r["changed"]) == ("atms", "3,959.00", "7", True)
    assert out["changed"] == 1 and out["fetched"] == 1
    assert col.docs["KKDD26090025"]["amount_net"] == "3,959.00"   # เขียนกลับลงฐานด้วย


def test_recently_fetched_bill_is_not_refetched(col):
    out = m.refresh_totals(m.RefreshRequest(codes=["LBDD26100363"]))
    assert rows_of(out)["LBDD26100363"]["source"] == "cache"
    assert col.writes == []


def test_force_ignores_the_fresh_window(col):
    """จุดจ่ายเงินต้องได้ของสด ไม่ว่าจะเพิ่งดึงไปเมื่อไหร่"""
    out = m.refresh_totals(m.RefreshRequest(codes=["LBDD26100363"], force=True))
    assert rows_of(out)["LBDD26100363"]["source"] == "atms"


def test_cancelled_bill_with_an_amount_is_an_error_not_a_zero(col):
    """ใบยกเลิกที่หัวใบยังมียอด — ห้ามลง totals_missing (ยอดอาจกลับมาถ้าเขาแก้ใบ)"""
    out = m.refresh_totals(m.RefreshRequest(codes=["KKDD26030043"]))
    assert rows_of(out)["KKDD26030043"]["source"] == "error"
    assert out["errors"] == 1
    assert col.writes == []


def test_blank_zero_bill_is_marked_once(col):
    out = m.refresh_totals(m.RefreshRequest(codes=["LBDD26010001"]))
    assert rows_of(out)["LBDD26010001"]["totals_missing"] is True
    assert col.docs["LBDD26010001"]["totals_missing"] is True


def test_unknown_code_is_reported_not_guessed(col):
    out = m.refresh_totals(m.RefreshRequest(codes=["LBDD29999999"]))
    assert rows_of(out)["LBDD29999999"]["source"] == "not_found"


def test_duplicate_codes_collapse(col):
    out = m.refresh_totals(m.RefreshRequest(codes=["KKDD26090025", " KKDD26090025 "]))
    assert len(out["rows"]) == 1


def test_over_the_cap_is_rejected(col):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        m.refresh_totals(m.RefreshRequest(codes=[f"X{i}" for i in range(m.MAX_CODES + 1)]))
    assert e.value.status_code == 400
