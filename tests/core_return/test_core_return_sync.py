import importlib.util
from datetime import datetime, timezone

from routes.pipeline.pipeline_routes import PIPELINE_NAMES, PIPELINE_SCRIPTS, RUN_LOG_LOCATION, SCRIPTS_DIR

_spec = importlib.util.spec_from_file_location("crs", SCRIPTS_DIR / "core_return" / "pipeline_core_return_sync.py")
crs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crs)


# ── tiny in-memory Mongo (no network) ───────────────────────────────
def _match(doc, flt):
    for k, cond in flt.items():
        v = doc.get(k)
        if isinstance(cond, dict):
            for op, a in cond.items():
                if op == "$gt" and not (v is not None and v > a): return False
                if op == "$gte" and not (v is not None and v >= a): return False
                if op == "$nin" and v in a: return False
                if op == "$in" and v not in a: return False
        elif v != cond:
            return False
    return True


class Cur(list):
    def max_time_ms(self, _): return self
    def batch_size(self, _): return self
    def limit(self, n): return Cur(self[:n])


class Res:
    upserted_count = modified_count = 0


class Col:
    def __init__(self): self.docs = []; self.bulk_sizes = []

    def find(self, flt=None, proj=None):
        drop = [k for k, v in (proj or {}).items() if v == 0]
        return Cur({k: v for k, v in d.items() if k not in drop} for d in self.docs if _match(d, flt or {}))

    def distinct(self, field, flt=None, maxTimeMS=None):
        return sorted({d[field] for d in self.docs if field in d and _match(d, flt or {})})

    def bulk_write(self, ops, ordered=True):
        assert ordered is False
        self.bulk_sizes.append(len(ops))
        for op in ops:
            cur = next((d for d in self.docs if d.get("row_key") == op._filter["row_key"]), None)
            if cur is None:
                cur = {**op._doc.get("$setOnInsert", {})}
                self.docs.append(cur)
            cur.update(op._doc["$set"])
        return Res()


class Client(dict):
    def __init__(self):
        super().__init__()
        self.cols = {}

    def __getitem__(self, db):
        return type("DB", (), {"__getitem__": lambda s, c: self.cols.setdefault((db, c), Col())})()


def row(**kw):
    base = {"row_key": "k1", "วันที่": "2026-11-01", "year_month": "2026-11", "คลังสินค้า": "คลังลาดกระบัง", "WD": "W1", "MR": "LKMR1234567",
            "จุดประสงค์ในการเบิก": "ซ่อม", "รหัสสินค้า": "S1", "ชื่อสินค้า": "A1 : ผ้าเบรก", "กลุ่มสินค้า": "เบรก", "ทะเบียน": "70-1111",
            "จ่าย": 1, "ราคาทุน": 900, "ยอดเงิน": 900, "หมายเหตุ": ""}
    return {**base, **kw}


NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)
nosleep = lambda s: None


def keep(**kw):
    out, _ = crs.apply_filters([row(**kw)], {"รถร่วม": {"99-9999"}}, "2026-09")
    return len(out) == 1


# ── filters ─────────────────────────────────────────────────────────
def test_filters_each_step():
    assert keep()
    assert not keep(ยอดเงิน=500)
    assert not keep(จ่าย=0)
    assert not keep(**{"กลุ่มสินค้า": "ค่าแรงซ่อม"})
    assert not keep(**{"จุดประสงค์ในการเบิก": "ยาง"})
    assert not keep(**{"จุดประสงค์ในการเบิก": "T-ยาง"})
    assert not keep(**{"จุดประสงค์ในการเบิก": "CM-งานซ่อมร่วมภายนอก"})
    assert not keep(**{"จุดประสงค์ในการเบิก": "งานสำนักงาน"})          # ไม่อยู่ใน keep-list
    assert not keep(**{"กลุ่มสินค้า": "น้ำมันเชื้อเพลิง"})
    assert not keep(ทะเบียน="99 - 9999".replace(" ", ""))             # รถร่วม
    assert not keep(ทะเบียน="99-9999 ")
    assert not keep(**{"คลังสินค้า": "คลังเชียงใหม่"})
    assert not keep(**{"กลุ่มสินค้า": "วัสดุสิ้นเปลือง"})
    assert not keep(**{"กลุ่มสินค้า": "ระบบวัสดุสิ้นเปลือง"})
    assert not keep(ชื่อสินค้า="OIL01 : น้ำมันเครื่อง 15W40")
    assert not keep(ชื่อสินค้า="จาระบี ลูกปืน")
    assert keep(ชื่อสินค้า="X1 : ถังน้ำมัน")                           # ถังน้ำมันมีซาก ต้องอยู่


# ── garage resolution order ─────────────────────────────────────────
def test_garage_resolution_order():
    pr = [{"ใบขอสั่งซื้อ (PR)": "LKPR0000001", "หมายเหตุ": "ซ่อม LKMR0000002"},
          {"ใบขอสั่งซื้อ (PR)": "LKPR0000003", "หมายเหตุ": "x LKMR0000003"}]
    po = [{"ใบขอสั่งซื้อ (PR)": "LKPR0000001", "ซัพพลายเออร์": " อู่ ก "},
          {"ใบขอสั่งซื้อ (PR)": "LKPR0000003", "ซัพพลายเออร์": "ร้านอะไหล่ ข"}]
    labour = [{"กลุ่มสินค้า": "ค่าแรงซ่อม", "MR": "LKMR0000001", "หมายเหตุ": "PR LKPR0000001"},
              {"กลุ่มสินค้า": "ค่าแรงซ่อม", "MR": "LKMR0000003"},
              {"กลุ่มสินค้า": "ค่าแรงซ่อม", "MR": "LKMR0000004"},
              {"กลุ่มสินค้า": "ค่าแรงซ่อม", "MR": "LKMR0000005", "PR": "LKPR0000001"}]
    g = crs.GarageResolver([{"mrNo": "LKMR0000005", "garage": "อู่ WMS"}], pr, po, labour)
    assert g.resolve("LKMR0000005", "คลังลาดกระบัง") == ("อู่นอก", "อู่ WMS", "งานซ่อมอู่นอก (WMS)")   # WMS ชนะ PO ค่าแรง
    assert g.resolve("LKMR0000001", "คลังลาดกระบัง") == ("อู่นอก", "อู่ ก", "PO ค่าแรงของ MR")
    assert g.resolve("LKMR0000003", "คลังลาดกระบัง") == ("อู่นอก", "ร้านอะไหล่ ข", "PO ของ MR (อาจเป็นร้านอะไหล่)")
    assert g.resolve("LKMR0000004", "คลังลาดกระบัง") == ("อู่นอก", "ไม่ทราบชื่ออู่", "มีค่าแรงแต่ไม่พบ PO")
    assert g.resolve("LKMR9999999", "คลังสระบุรี") == ("อู่ใน", "อู่ใน สระบุรี", "ไม่มีค่าแรงใน MR")


# ── run(): upsert / skip / dry-run ──────────────────────────────────
def seeded(rows):
    c = Client()
    c.cols[("atms", "stockmovement_v5")] = Col()
    c.cols[("atms", "stockmovement_v5")].docs = rows
    c.cols[("datawarehouse", "dw_stockmovement")] = Col()
    c.cols[("datawarehouse", "dw_stockmovement")].docs = [{"ทะเบียน": "99-9999", "month_year": "2026-10", "partner_flag": "รถร่วม"}]
    return c


def test_run_inserts_then_keeps_existing_status_and_skips_wms():
    c = seeded([row(), row(row_key="k2", WD="W2", รหัสสินค้า="S2"), row(row_key="k3", WD="WX", รหัสสินค้า="SX")])
    tgt = c.cols.setdefault(("master_data", "core_return_base"), Col())
    tgt.docs = [{"row_key": "w", "source": "wms.store-withdraw", "wdCode": "WX", "skuCode": "SX"}]
    res = crs.run(c, 3, sleep=nosleep, now=NOW)
    assert res["months"] == ["2026-09", "2026-10", "2026-11"]
    assert (res["fetched"], res["after_filters"], res["inserted"], res["updated"], res["skipped_wms"]) == (3, 3, 2, 0, 1)
    d = next(x for x in tgt.docs if x["row_key"] == "k1")
    assert d["coreStatus"] == "pending" and d["coreQtyReturned"] == 0 and d["source"] == "atms.stockmovement_v5"
    assert d["garageType"] == "อู่ใน" and d["issuedDate"] == "2026-11-01" and d["amount"] == 900.0
    assert not any(x["row_key"] == "k3" for x in tgt.docs)
    # สโตร์บันทึกสถานะ แล้ว ATMS แก้ราคา · รันซ้ำต้องไม่ทับสถานะ
    d.update(coreStatus="returned", coreQtyReturned=1, roundNo=2)
    c.cols[("atms", "stockmovement_v5")].docs[0]["ยอดเงิน"] = 1000
    res2 = crs.run(c, 3, sleep=nosleep, now=NOW)
    assert (res2["inserted"], res2["updated"]) == (0, 2)
    d = next(x for x in tgt.docs if x["row_key"] == "k1")
    assert (d["coreStatus"], d["coreQtyReturned"], d["roundNo"], d["amount"]) == ("returned", 1, 2, 1000.0)


def test_run_counts_missing_in_atms_and_never_deletes():
    c = seeded([row()])
    tgt = c.cols.setdefault(("master_data", "core_return_base"), Col())
    tgt.docs = [{"row_key": "gone", "source": "atms.stockmovement_v5", "yearMonth": "2026-10"}]
    res = crs.run(c, 3, sleep=nosleep, now=NOW)
    assert res["missing_in_atms"] == 1
    assert any(x["row_key"] == "gone" for x in tgt.docs)


def test_dry_run_writes_nothing():
    c = seeded([row()])
    res = crs.run(c, 3, dry_run=True, sleep=nosleep, now=NOW)
    assert res["inserted"] == 1 and res["dry_run"] is True
    assert c.cols.get(("master_data", "core_return_base"), Col()).docs == []


def test_batches_of_500_with_pause():
    rows = [row(row_key=f"k{i}", WD=f"W{i}") for i in range(1201)]
    c = seeded(rows)
    sleeps = []
    crs.run(c, 1, sleep=sleeps.append, now=NOW)
    assert c.cols[("master_data", "core_return_base")].bulk_sizes == [500, 500, 201]
    assert sleeps == [0.5, 0.5, 0.5]


def test_registered_in_pipeline_runner():
    assert PIPELINE_SCRIPTS["core-return-sync"].relative_to(SCRIPTS_DIR).as_posix() == "core_return/pipeline_core_return_sync.py"
    assert PIPELINE_SCRIPTS["core-return-sync"].is_file()
    assert PIPELINE_NAMES["core-return-sync"] == "core-return-sync"
    assert RUN_LOG_LOCATION["core-return-sync"] == ("analytics", "etl_jobs")
