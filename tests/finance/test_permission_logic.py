"""สิทธิ์กดปุ่มตามแผนก (ผู้ใช้สั่ง 21/10/2026): จ่ายเงิน + บัญชีรับเงิน = การเงิน (4) · ปุ่มอื่น = บัญชี (6)"""
import pytest

from services.finance import permission_logic as pl

FIN, ACC, OPS = 4, 6, 11
FINANCE_BUTTONS = [pl.PERM_PAY, pl.PERM_PAYEE_ADMIN]
ACCOUNTING_BUTTONS = [p for p in pl.ALL_PERMISSIONS if p not in FINANCE_BUTTONS]


@pytest.mark.parametrize("perm", FINANCE_BUTTONS)
def test_จ่ายเงินกับบัญชีรับเงินเป็นของการเงิน(perm):
    assert pl.allowed(perm, "680179", FIN)
    assert not pl.allowed(perm, "660548", ACC)


@pytest.mark.parametrize("perm", ACCOUNTING_BUTTONS)
def test_ปุ่มอื่นเป็นของบัญชีทั้งหมด(perm):
    assert pl.allowed(perm, "670163", ACC)
    assert not pl.allowed(perm, "620420", FIN)


def test_แผนกอื่นไม่ได้สักปุ่ม():
    assert pl.permissions_for("570245", OPS) == []
    assert pl.permissions_for("570245", None) == []


def test_department_id_เป็นข้อความก็ได้():
    assert pl.allowed(pl.PERM_PAY, "610197", "4")


def test_permissions_for_ตามแผนก():
    assert pl.permissions_for("610197", FIN) == FINANCE_BUTTONS
    assert pl.permissions_for("660548", ACC) == ACCOUNTING_BUTTONS


def test_superadmin_ได้ทุกปุ่มไม่ว่าอยู่แผนกไหน():
    assert pl.permissions_for("670108", 21) == list(pl.ALL_PERMISSIONS)


def test_สิทธิ์ที่ไม่รู้จักไม่ผ่าน():
    assert not pl.allowed("delete_everything", "660548", ACC)


def test_ข้อความปฏิเสธบอกว่าปุ่มเป็นของแผนกไหน():
    assert pl.denied_message(pl.PERM_PAY) == "คุณไม่มีสิทธิ์จ่ายเงิน ปุ่มนี้สำหรับแผนกการเงิน"
    assert pl.denied_message(pl.PERM_CLOSE) == "คุณไม่มีสิทธิ์ปิดรายการ ปุ่มนี้สำหรับแผนกบัญชี"
    assert pl.denied_message(pl.PERM_PAYEE_ADMIN) == "คุณไม่มีสิทธิ์จัดการบัญชีรับเงิน ปุ่มนี้สำหรับแผนกการเงิน"


@pytest.mark.parametrize("perm", pl.ALL_PERMISSIONS)
def test_ทุกสิทธิ์มีคำอธิบายภาษาไทย(perm):
    assert pl.PERMISSION_LABELS.get(perm)
