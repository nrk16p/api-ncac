from decimal import Decimal

from services.finance import petty_atms


class _FakeCursor(list):
    def max_time_ms(self, _n): return self
    def limit(self, _n): return self


class _FakeColl:
    def __init__(self, rows, raises=None):
        self.rows, self.raises, self.last_filter = rows, raises, None

    def find(self, flt, _proj=None):
        self.last_filter = flt
        if self.raises:
            raise self.raises
        return _FakeCursor(self.rows)


ROWS = [
    {"DD": "LBDD26090931", "วันที่": "25/09/2026", "คลังสินค้า": "ลาดกระบัง",
     "ซัพพลายเออร์": "ลานชั่งภายนอก", "รหัสสินค้า": "LB00038", "ชื่อสินค้า": "ค่าชั่งน้ำหนัก",
     "กลุ่มสินค้า": "ค่าแรง", "รับ": 12, "ราคาทุน": 100, "ยอดเงิน": 1200,
     "เลขรถ": "161", "ทะเบียน": "71-4285", "พจส.": "อัญชัญ น่วมมี", "PR": "", "PO": ""},
    {"DD": "LBDD26090931", "วันที่": "25/09/2026", "รหัสสินค้า": "LB00038",
     "ชื่อสินค้า": "ค่าชั่งน้ำหนัก", "กลุ่มสินค้า": "ค่าแรง", "รับ": 3, "ราคาทุน": 120, "ยอดเงิน": 360},
    {"DD": "LBDD26090931", "วันที่": "25/09/2026", "รหัสสินค้า": "LB00038",
     "ชื่อสินค้า": "ค่าชั่งน้ำหนัก", "กลุ่มสินค้า": "ค่าแรง", "รับ": 1, "ราคาทุน": 80, "ยอดเงิน": 80},
]


def _patch(monkeypatch, rows, raises=None):
    coll = _FakeColl(rows, raises)
    monkeypatch.setattr(petty_atms, "_collection", lambda: coll)
    return coll


class TestFetch:
    def test_reads_every_line_of_the_document(self, monkeypatch):
        _patch(monkeypatch, ROWS)
        out = petty_atms.fetch_dd("LBDD26090931")
        assert out["found"] is True and len(out["lines"]) == 3

    def test_total_matches_the_real_voucher(self, monkeypatch):
        _patch(monkeypatch, ROWS)
        assert petty_atms.fetch_dd("LBDD26090931")["total"] == Decimal("1640.00")

    def test_query_is_keyed_on_the_dd_number(self, monkeypatch):
        coll = _patch(monkeypatch, ROWS)
        petty_atms.fetch_dd("LBDD26090931")
        assert coll.last_filter == {"DD": "LBDD26090931"}

    def test_pr_and_po_are_lifted_to_the_header(self, monkeypatch):
        # DD ของยางจริง LBDD26090680 มี PO LBPO26090431 และ PR LBPR26090380
        rows = [dict(ROWS[0], **{"PR": "LBPR26090380", "PO": "LBPO26090431"}), ROWS[1]]
        _patch(monkeypatch, rows)
        out = petty_atms.fetch_dd("LBDD26090680")
        assert out["pr"] == "LBPR26090380" and out["po"] == "LBPO26090431"

    def test_a_dd_without_a_po_does_not_break(self, monkeypatch):
        # ค่าชั่งน้ำหนักไม่มี PR/PO
        _patch(monkeypatch, ROWS)
        out = petty_atms.fetch_dd("LBDD26090931")
        assert out["pr"] is None and out["po"] is None

    def test_pr_is_taken_from_whichever_line_has_it(self, monkeypatch):
        _patch(monkeypatch, [ROWS[1], dict(ROWS[0], **{"PR": "LBPR26090380"})])
        assert petty_atms.fetch_dd("LBDD26090680")["pr"] == "LBPR26090380"

    def test_header_comes_from_the_first_line(self, monkeypatch):
        _patch(monkeypatch, ROWS)
        out = petty_atms.fetch_dd("LBDD26090931")
        assert out["warehouse"] == "ลาดกระบัง" and out["supplier"] == "ลานชั่งภายนอก"

    def test_not_found_is_reported_not_raised(self, monkeypatch):
        _patch(monkeypatch, [])
        out = petty_atms.fetch_dd("LBDD99999999")
        assert out["found"] is False and out["error"] is None

    def test_blank_number_never_touches_mongo(self, monkeypatch):
        def _boom():
            raise AssertionError("ไม่ควรเรียก Mongo")
        monkeypatch.setattr(petty_atms, "_collection", _boom)
        assert petty_atms.fetch_dd("")["found"] is False

    def test_mongo_failure_degrades_instead_of_breaking_the_page(self, monkeypatch):
        _patch(monkeypatch, [], raises=TimeoutError("server selection timeout"))
        out = petty_atms.fetch_dd("LBDD26090931")
        assert out["found"] is False and "ดึงข้อมูล" in out["error"]

    def test_unit_and_vat_come_from_the_type_spec_not_mongo(self, monkeypatch):
        _patch(monkeypatch, ROWS)
        out = petty_atms.fetch_dd("LBDD26090931", atms_spec={"unit": "งาน", "vat": 0})
        assert out["lines"][0]["unit"] == "งาน" and out["vat"] == 0

    def test_garbage_numbers_do_not_crash_the_total(self, monkeypatch):
        _patch(monkeypatch, [dict(ROWS[0], **{"ยอดเงิน": "ไม่ใช่เลข"})])
        assert petty_atms.fetch_dd("LBDD26090931")["total"] == Decimal("0.00")


class TestCompare:
    def test_equal_totals_match(self):
        dd = {"found": True, "total": Decimal("1640.00")}
        assert petty_atms.compare_total(dd, Decimal("1640.00")) == {"match": True,
                                                                    "difference": Decimal("0.00")}

    def test_difference_is_reported(self):
        dd = {"found": True, "total": Decimal("1540.00")}
        out = petty_atms.compare_total(dd, Decimal("1640.00"))
        assert out["match"] is False and out["difference"] == Decimal("100.00")

    def test_difference_is_absolute(self):
        dd = {"found": True, "total": Decimal("1740.00")}
        assert petty_atms.compare_total(dd, Decimal("1640.00"))["difference"] == Decimal("100.00")

    def test_missing_dd_never_claims_a_match(self):
        assert petty_atms.compare_total({"found": False, "total": Decimal("0")},
                                        Decimal("1640.00"))["match"] is False


class TestTheProjectionIsLegal:
    """เจอจากการลองกับ Mongo จริง 2026-10-13: ชื่อฟิลด์ "พจส." ลงท้ายด้วยจุด
    ใส่ใน projection แล้ว MongoDB ปฏิเสธทั้งคำสั่งด้วย FieldPath must not end with a '.'
    ฉะนั้นใบนี้เลิกเลือกฟิลด์ทีละตัว ตัดแค่ _id ออกแล้วอ่านจากเอกสารเต็ม
    """

    def test_no_projected_path_ends_with_a_dot(self):
        assert [k for k in petty_atms.PROJECTION if k.endswith(".")] == []

    def test_the_projection_only_excludes_the_mongo_id(self):
        assert petty_atms.PROJECTION == {"_id": 0}

    def test_the_driver_name_still_comes_through(self):
        row = {"DD": "LBDD26090931", "ยอดเงิน": 1200, "พจส.": "กฤตบุญ ลายเงิน"}
        out = petty_atms.fetch_dd("LBDD26090931", {"unit": "เส้น"},
                                  finder=lambda number: [row])
        assert out["lines"][0]["driver"] == "กฤตบุญ ลายเงิน"


class TestTheResultIsJsonSafe:
    """dd_snapshot เก็บเป็น jsonb — ค่าที่ fetch_dd คืนต้อง json.dumps ได้ทั้งก้อน
    (เจอ 2026-10-13: วันที่จาก Mongo เป็น datetime ทำให้ UPDATE dd_snapshot ล้ม
    และทิ้ง session ค้างในสถานะต้อง rollback)"""

    def test_received_date_is_a_plain_string(self):
        from datetime import datetime
        row = {"DD": "LBDD26090931", "วันที่": datetime(2026, 9, 25), "ยอดเงิน": 80}
        out = petty_atms.fetch_dd("LBDD26090931", {}, finder=lambda n: [row])
        assert out["received_date"] == "2026-09-25"

    def test_the_whole_result_serialises(self):
        import json
        from datetime import datetime
        row = {"DD": "LBDD26090931", "วันที่": datetime(2026, 9, 25), "ยอดเงิน": 80,
               "ราคาทุน": 80, "รับ": 1}
        out = petty_atms.fetch_dd("LBDD26090931", {}, finder=lambda n: [row])
        json.dumps(out, default=str)  # default=str คือสิ่งที่ _snapshot_dd ใช้
        assert isinstance(out["received_date"], str)
