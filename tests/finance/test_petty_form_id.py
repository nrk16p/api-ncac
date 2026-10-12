from routes.forms.form_submission_routes import generate_form_id
from services.finance import petty_logic as L


def test_petty_code_is_per_branch_and_division():
    assert L.petty_form_code("03", "B02") == "PC-03-B02"


def test_generate_form_id_formats_petty(seq_stub, frozen_bkk):
    first = generate_form_id(None, "PC-03-B02")
    second = generate_form_id(None, "PC-03-B02")
    assert first == "03-B02-2610-001"
    assert second == "03-B02-2610-002"


def test_other_funds_have_their_own_series(seq_stub, frozen_bkk):
    assert generate_form_id(None, "PC-03-B02") == "03-B02-2610-001"
    assert generate_form_id(None, "PC-01-A01") == "01-A01-2610-001"


def test_petty_series_resets_per_month(seq_stub, monkeypatch):
    from datetime import datetime
    import routes.forms.form_submission_routes as mod
    monkeypatch.setattr(mod, "_now_bkk", lambda: datetime(2026, 10, 31))
    assert generate_form_id(None, "PC-03-B02") == "03-B02-2610-001"
    monkeypatch.setattr(mod, "_now_bkk", lambda: datetime(2026, 11, 1))
    assert generate_form_id(None, "PC-03-B02") == "03-B02-2611-001"


def test_adv_numbering_unchanged(seq_stub, frozen_bkk):
    assert generate_form_id(None, "ADV") == "ADV-2610-001"


def test_plain_form_code_unchanged(seq_stub, frozen_bkk):
    # ฟอร์ม IT เดิมยังเป็น {code}-{ปี ค.ศ.}-{0000}
    from datetime import datetime
    assert generate_form_id(None, "IT").startswith(f"IT-{datetime.utcnow().year}-")


# ---- Project Code: ชุดเลขของตัวเอง รันตามสาขา-ดิวิชัน สาขาเป็นตัวอักษรแบบ ATMS ----

def test_branch_letters_follow_atms():
    assert L.branch_letters("03") == "LB"
    assert L.branch_letters("02") == "SB"


def test_unknown_branch_keeps_its_number():
    # สาขาที่ยังไม่ยืนยันตัวอักษร ใช้เลขเดิมไปก่อน ดีกว่าเดาแล้วผิด
    assert L.branch_letters("04") == "04"


def test_project_code_is_per_branch_and_division():
    assert L.project_form_code("03", "B02") == "PJ-LB-B02"


def test_generate_form_id_formats_project_code(seq_stub, frozen_bkk):
    assert generate_form_id(None, "PJ-LB-B02") == "PJ-LB-B02-2610-001"
    assert generate_form_id(None, "PJ-LB-B02") == "PJ-LB-B02-2610-002"


def test_project_code_series_is_separate_from_the_form_id(seq_stub, frozen_bkk):
    assert generate_form_id(None, "PC-03-B02") == "03-B02-2610-001"
    assert generate_form_id(None, "PJ-LB-B02") == "PJ-LB-B02-2610-001"


def test_project_code_resets_per_month(seq_stub, monkeypatch):
    from datetime import datetime
    import routes.forms.form_submission_routes as mod
    monkeypatch.setattr(mod, "_now_bkk", lambda: datetime(2026, 10, 31))
    assert generate_form_id(None, "PJ-SB-B02") == "PJ-SB-B02-2610-001"
    monkeypatch.setattr(mod, "_now_bkk", lambda: datetime(2026, 11, 1))
    assert generate_form_id(None, "PJ-SB-B02") == "PJ-SB-B02-2611-001"
