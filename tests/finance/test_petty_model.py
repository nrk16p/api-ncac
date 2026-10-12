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
        "review_remark", "reviewed_by", "reviewed_at", "batch_id", "status", "created_at", "updated_at",
    }
    assert set(FinPettyClaim.__table__.columns.keys()) == expected


def test_item_columns_match_migration():
    expected = {
        "id", "claim_id", "line_no", "event_date", "truck_code", "truck_plate", "truck_type",
        "ref_no", "qty", "rate", "amount", "remark",
    }
    assert set(FinPettyClaimItem.__table__.columns.keys()) == expected


def test_status_check_constraint_lists_seven_statuses():
    ck = [c for c in FinPettyClaim.__table__.constraints if c.name == "ck_fin_petty_claims_status"]
    assert len(ck) == 1
    sql = str(ck[0].sqltext)
    for status in ("PENDING_APPROVAL", "RETURNED", "APPROVED", "TRANSFERRED",
                   "AWAITING_REVIEW", "SENT_BACK", "CLOSED"):
        assert status in sql
