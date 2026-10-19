"""สิทธิ์ตามหน้าที่ของงาน Advance — ตรรกะล้วน ไม่แตะฐานข้อมูล"""
import pytest

from services.finance import permission_logic as pl

BOW = "610197"      # แพรวนภา — จ่ายเงิน
JOY = "620420"      # ดารณี — ปิดรายการ
NONG = "660548"     # ชญาดา — ตรวจเคลียร์
PEACH = "690041"    # สุขพัฒน์ — ตั้งเบิก
SOM = "680179"      # นวรัตน์ — บัญชีรับเงิน

GRANTS = {
    pl.PERM_VOUCHER: {PEACH},
    pl.PERM_PAY: {BOW},
    pl.PERM_RETURN: {PEACH, BOW},
    pl.PERM_CLEARING_REVIEW: {NONG, "670163"},
    pl.PERM_CLOSE: {JOY},
    pl.PERM_PAYEE_ADMIN: {SOM},
    pl.PERM_OVERDUE_REMIND: {NONG, "670163", JOY},
}


def test_คนที่มีสิทธิ์ทำได้():
    assert pl.allowed(pl.PERM_PAY, BOW, GRANTS, is_finance=True)
    assert pl.allowed(pl.PERM_VOUCHER, PEACH, GRANTS, is_finance=True)
    assert pl.allowed(pl.PERM_CLOSE, JOY, GRANTS, is_finance=True)


def test_คนการเงินที่ไม่ได้รับสิทธิ์นั้นทำไม่ได้():
    # คุณโบว์อยู่การเงิน แต่ไม่ได้ถือสิทธิ์เคลียร์หรือปิดรายการ
    assert not pl.allowed(pl.PERM_CLEARING_REVIEW, BOW, GRANTS, is_finance=True)
    assert not pl.allowed(pl.PERM_CLOSE, BOW, GRANTS, is_finance=True)
    # คุณน้องอยู่บัญชี แต่กดจ่ายเงินไม่ได้
    assert not pl.allowed(pl.PERM_PAY, NONG, GRANTS, is_finance=True)


def test_สิทธิ์ที่ยังไม่ได้ตั้งค่าใช้กติกาเดิม():
    """กันไม่ให้ระบบล็อกตัวเองตอน deploy ก่อนรัน SQL"""
    assert pl.allowed(pl.PERM_PAY, BOW, {}, is_finance=True)
    assert not pl.allowed(pl.PERM_PAY, "999999", {}, is_finance=False)


def test_ตั้งค่าแล้วบางสิทธิ์_สิทธิ์อื่นยังใช้กติกาเดิม():
    partial = {pl.PERM_PAY: {BOW}}
    assert not pl.allowed(pl.PERM_PAY, NONG, partial, is_finance=True)
    assert pl.allowed(pl.PERM_VOUCHER, NONG, partial, is_finance=True)


def test_คนนอกการเงินไม่ได้สิทธิ์อะไรเลย():
    for perm in pl.ALL_PERMISSIONS:
        assert not pl.allowed(perm, "570245", GRANTS, is_finance=False)


def test_permissions_for_คืนเฉพาะที่ถือจริง():
    assert pl.permissions_for(BOW, GRANTS, is_finance=True) == [pl.PERM_PAY, pl.PERM_RETURN]
    assert pl.permissions_for(JOY, GRANTS, is_finance=True) == [pl.PERM_CLOSE, pl.PERM_OVERDUE_REMIND]
    assert pl.permissions_for(SOM, GRANTS, is_finance=True) == [pl.PERM_PAYEE_ADMIN]


def test_permissions_for_ยังไม่ตั้งค่าได้ครบทุกสิทธิ์():
    assert pl.permissions_for(BOW, {}, is_finance=True) == list(pl.ALL_PERMISSIONS)


def test_ข้อความปฏิเสธเป็นภาษาคน():
    assert pl.denied_message(pl.PERM_PAY) == "คุณไม่มีสิทธิ์จ่ายเงิน กรุณาติดต่อผู้ดูแลระบบ"
    assert "ตรวจเคลียร์" in pl.denied_message(pl.PERM_CLEARING_REVIEW)


def test_employee_id_ว่างไม่ผ่าน():
    assert not pl.allowed(pl.PERM_PAY, None, GRANTS, is_finance=True)
    assert not pl.allowed(pl.PERM_PAY, "", GRANTS, is_finance=True)


@pytest.mark.parametrize("perm", pl.ALL_PERMISSIONS)
def test_ทุกสิทธิ์มีคำอธิบายภาษาไทย(perm):
    assert pl.PERMISSION_LABELS.get(perm)
