"""สิทธิ์กดปุ่มตามแผนก (ผู้ใช้สั่ง 21/10/2026): จ่ายเงิน + บัญชีรับเงิน = การเงิน (4) · ปุ่มอื่น = บัญชี (6)"""
import pytest

from services.finance import permission_logic as pl

FIN, ACC, OPS = 4, 6, 11
FINANCE_BUTTONS = [pl.PERM_PAY, pl.PERM_PAYEE_ADMIN, pl.PERM_RECEIVE]
# ปิดรายการ = บัญชี หรือการเงินสายรับ (7/11/2026) จึงไม่อยู่ในสองกลุ่มนี้
ACCOUNTING_BUTTONS = [p for p in pl.ALL_PERMISSIONS if p not in FINANCE_BUTTONS and p != pl.PERM_CLOSE]


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
    # การเงินไม่ระบุสาย = ทั้งสองสาย: จ่าย ปิดรายการ บัญชีรับเงิน รับเงินคืน · บัญชี = ปุ่มบัญชี + ปิดรายการ
    assert pl.permissions_for("610197", FIN) == [pl.PERM_PAY, pl.PERM_CLOSE, pl.PERM_PAYEE_ADMIN, pl.PERM_RECEIVE]
    assert pl.permissions_for("660548", ACC) == [p for p in pl.ALL_PERMISSIONS if p in ACCOUNTING_BUTTONS or p == pl.PERM_CLOSE]


def test_superadmin_ได้ทุกปุ่มไม่ว่าอยู่แผนกไหน():
    assert pl.permissions_for("670108", 21) == list(pl.ALL_PERMISSIONS)


def test_สิทธิ์ที่ไม่รู้จักไม่ผ่าน():
    assert not pl.allowed("delete_everything", "660548", ACC)


def test_ข้อความปฏิเสธบอกว่าปุ่มเป็นของแผนกไหน():
    assert pl.denied_message(pl.PERM_PAY) == "คุณไม่มีสิทธิ์จ่ายเงิน ปุ่มนี้สำหรับแผนกการเงิน (สายการเงินจ่าย)"
    assert pl.denied_message(pl.PERM_CLOSE) == "คุณไม่มีสิทธิ์ปิดรายการ ปุ่มนี้สำหรับแผนกบัญชี หรือการเงินสายรับ"
    assert pl.denied_message(pl.PERM_PAYEE_ADMIN) == "คุณไม่มีสิทธิ์จัดการบัญชีรับเงิน ปุ่มนี้สำหรับแผนกการเงิน (สายการเงินจ่าย)"
    assert pl.denied_message(pl.PERM_RECEIVE) == "คุณไม่มีสิทธิ์รับเงินคืน ปุ่มนี้สำหรับแผนกการเงิน (สายการเงินรับ)"
    assert pl.denied_message(pl.PERM_VOUCHER) == "คุณไม่มีสิทธิ์ตั้งเบิก ปุ่มนี้สำหรับแผนกบัญชี"


# ---- การเงิน 2 สาย จากชื่อตำแหน่งใน master (ผู้ใช้สั่ง 7/11/2026) ----
PAY_POS = "Finance Supervisor - Payment"
RCV_POS = "เจ้าหน้าที่การเงิน Administrative and Financial Officer - Receivable"
MGR_POS = "ผู้จัดการฝ่าย Finance Manager"


@pytest.mark.parametrize("name, line", [
    (PAY_POS, pl.LINE_PAY), ("เจ้าหน้าที่การเงินจ่าย", pl.LINE_PAY), (RCV_POS, pl.LINE_RECEIVE), ("การเงินรับ", pl.LINE_RECEIVE),
    (MGR_POS, None), ("", None), (None, None), ("Payment and Receivable Officer", None)])
def test_สายอ่านจากชื่อตำแหน่ง(name, line):
    assert pl.finance_line(name) == line


def test_สายจ่ายจ่ายได้_ปิดรายการไม่ได้():
    assert pl.permissions_for("680179", FIN, PAY_POS) == [pl.PERM_PAY, pl.PERM_PAYEE_ADMIN]
    assert not pl.allowed(pl.PERM_CLOSE, "680179", FIN, PAY_POS)
    assert not pl.allowed(pl.PERM_RECEIVE, "680179", FIN, PAY_POS)


def test_สายรับรับเงินคืนและปิดรายการได้_จ่ายไม่ได้():
    assert pl.permissions_for("690289", FIN, RCV_POS) == [pl.PERM_CLOSE, pl.PERM_RECEIVE]
    assert not pl.allowed(pl.PERM_PAY, "690289", FIN, RCV_POS)
    assert not pl.allowed(pl.PERM_PAYEE_ADMIN, "690289", FIN, RCV_POS)


def test_ผู้จัดการฝ่ายการเงินทำได้ทั้งสองสาย():
    assert pl.permissions_for("620331", FIN, MGR_POS) == [pl.PERM_PAY, pl.PERM_CLOSE, pl.PERM_PAYEE_ADMIN, pl.PERM_RECEIVE]


def test_บัญชีไม่สนสาย_ปิดรายการได้เหมือนเดิม():
    assert pl.allowed(pl.PERM_CLOSE, "660548", ACC, PAY_POS)
    assert not pl.allowed(pl.PERM_RECEIVE, "660548", ACC, RCV_POS)


@pytest.mark.parametrize("perm", pl.ALL_PERMISSIONS)
def test_ทุกสิทธิ์มีคำอธิบายภาษาไทย(perm):
    assert pl.PERMISSION_LABELS.get(perm)


# ---- master แยกแผนกการเงินรับ (36) / จ่าย (37) — ข้อมูลจริงบน prod 17/11/2026 (ผู้ใช้สั่ง "ให้สายดูจากแผนกก่อน") ----
OFFICER_POS = "เจ้าหน้าที่ธุรการ (การเงิน) Administrative and Financial Officer"
REAL_FINANCE = [  # employee_id, department_id, ตำแหน่ง th+en, สิทธิ์ที่ต้องได้
    ("620331", 4, "ผู้จัดการฝ่ายการเงิน Finance Manager", [pl.PERM_PAY, pl.PERM_PAYEE_ADMIN, pl.PERM_CLOSE, pl.PERM_RECEIVE]),
    ("680179", 37, "หัวหน้าแผนกการเงินจ่าย Finance Supervisor - Payment", [pl.PERM_PAY, pl.PERM_PAYEE_ADMIN]),
    ("690254", 37, OFFICER_POS, [pl.PERM_PAY, pl.PERM_PAYEE_ADMIN]),
    ("610197", 37, OFFICER_POS, [pl.PERM_PAY, pl.PERM_PAYEE_ADMIN]),
    ("690289", 36, "หัวหน้าแผนกการเงินรับ Finance Supervisor - Receivable", [pl.PERM_CLOSE, pl.PERM_RECEIVE]),
    ("620420", 36, OFFICER_POS, [pl.PERM_CLOSE, pl.PERM_RECEIVE]),
]


@pytest.mark.parametrize("emp, dept, pos, want", REAL_FINANCE, ids=[r[0] for r in REAL_FINANCE])
def test_การเงิน6คน_สายตามแผนก(emp, dept, pos, want):
    got = pl.permissions_for(emp, dept, pos)
    assert sorted(got) == sorted(want)


@pytest.mark.parametrize("dept, line", [(37, pl.LINE_PAY), (36, pl.LINE_RECEIVE), ("37", pl.LINE_PAY), (4, None)])
def test_แผนกบอกสายก่อนชื่อตำแหน่ง(dept, line):
    assert pl.finance_line(OFFICER_POS, dept) == line


def test_แผนก37ชนะคำในตำแหน่ง():
    # ตำแหน่งเขียนว่า Receivable แต่อยู่แผนกการเงินจ่าย → ยึดแผนก
    assert pl.finance_line("Finance Supervisor - Receivable", 37) == pl.LINE_PAY


def test_แผนกการเงินรับจ่ายนับเป็นการเงิน_บัญชีไม่ใช่():
    assert pl.is_finance_department(36) and pl.is_finance_department(37) and pl.is_finance_department(4)
    assert not pl.is_finance_department(6)
    assert not pl.allowed(pl.PERM_PAY, "x", 6, "")
