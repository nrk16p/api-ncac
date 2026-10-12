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
