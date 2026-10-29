from models.finance_petty_model import (
    FinPettyBatch, FinPettyClaim, FinPettyClaimItem, FinPettyExpenseType, FinPettyFund, FinPettyLog,
)

PETTY_TABLES = {
    FinPettyFund: "fin_petty_funds",
    FinPettyExpenseType: "fin_petty_expense_types",
    FinPettyClaim: "fin_petty_claims",
    FinPettyClaimItem: "fin_petty_claim_items",
    FinPettyBatch: "fin_petty_batches",
    FinPettyLog: "fin_petty_logs",
}


def test_table_names():
    for model, name in PETTY_TABLES.items():
        assert model.__tablename__ == name


def test_claim_has_no_submission_id():
    # แผน ข้อ 1: petty ไม่ผูกกับ form_submissions เลย
    assert "submission_id" not in FinPettyClaim.__table__.columns


def test_claim_columns_match_migration():
    expected = {
        "id", "form_id", "fund_id", "expense_type", "expense_date", "period_from", "period_to",
        "driver_employee_id", "driver_name", "payee_name", "amount", "detail", "dd_no", "dd_snapshot",
        "receipt_no", "wht_applicable", "created_by", "source", "dup_override_reason", "return_reason",
        "approved_by", "approved_at", "transfer_date", "transfer_slip_at", "submitted_to_acc_at",
        "review_remark", "reviewed_by", "reviewed_at", "batch_id", "debt_doc_no", "status", "created_at", "updated_at",
    }
    assert set(FinPettyClaim.__table__.columns.keys()) == expected


def test_item_columns_match_migration():
    expected = {
        "id", "claim_id", "line_no", "event_date", "truck_code", "truck_plate", "truck_type",
        "ref_no", "qty", "rate", "amount", "remark", "driver_name", "driver_role", "tire_position",
    }
    assert set(FinPettyClaimItem.__table__.columns.keys()) == expected


def test_status_check_constraint_lists_the_v2_statuses():
    ck = [c for c in FinPettyClaim.__table__.constraints if c.name == "ck_fin_petty_claims_status"]
    assert len(ck) == 1
    sql = str(ck[0].sqltext)
    # TRANSFERRED ยังอยู่ใน CHECK เพื่อให้ SQL ย้ายใบเก่าได้โดยไม่ล้ม และไม่ทำให้แถวเก่าผิดกติกา
    for status in ("PENDING_APPROVAL", "RETURNED", "APPROVED", "CLEAR_PENDING", "AWAITING_REVIEW",
                   "SENT_BACK", "REVIEWED", "CLOSED", "TRANSFERRED"):
        assert status in sql


def test_batch_columns_match_migration():
    expected = {
        "id", "fund_id", "batch_no", "total_amount", "remark", "status",
        "submitted_by", "submitted_at", "clear_required_level",
        "clear_approved_by", "clear_approved_at", "clear_reject_reason",
        "voucher_no", "voucher_date", "vouchered_by", "vouchered_at",
        "pay_date", "pay_doc_no", "paid_by", "paid_at",
        "closed_by", "closed_at",
    }
    assert set(FinPettyBatch.__table__.columns.keys()) == expected


def test_new_round_columns_are_in_the_v2_migration():
    # ไฟล์ v2 ยังไม่เคยรันบน prod และต้องรันซ้ำได้ — คอลัมน์ที่เพิ่มทีหลังต่อท้ายไฟล์เดิมแบบ IF NOT EXISTS
    import pathlib
    sql = (pathlib.Path(__file__).parents[2] / "scripts/migrations/2026-10-29_petty_flow_v2.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS voucher_date" in sql
    assert "ADD COLUMN IF NOT EXISTS pay_doc_no" in sql
    assert FinPettyBatch.__table__.columns["pay_doc_no"].type.length == 50


def test_batch_closed_at_is_empty_until_paid():
    col = FinPettyBatch.__table__.columns["closed_at"]
    assert col.nullable and col.server_default is None


def test_batch_status_check_lists_round_statuses():
    ck = [c for c in FinPettyBatch.__table__.constraints if c.name == "ck_fin_petty_batches_status"]
    assert len(ck) == 1
    for status in ("CLEAR_PENDING", "CLEAR_REJECTED", "AWAITING_REVIEW", "REVIEWED", "VOUCHERED", "CLOSED"):
        assert status in str(ck[0].sqltext)
