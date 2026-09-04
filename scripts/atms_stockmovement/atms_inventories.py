#!/usr/bin/env python3
"""Single source of truth for ATMS inventories (คลังสินค้า).

Scraped from the `<select name="inventory_id">` on the ATMS stock.movement
report form (2026-08-26). Note the gaps — 14, 19, 20, 27-30 do not exist, so
never probe ids by counting 1..40; read the form.

The pipeline historically pulled only LEGACY_4. Everything downstream
(dw_stockmovement, /cost/*, the stock-onhand and dead-stock KPIs) is calibrated
on those four, so LEGACY_4 stays the default for every consumer except
/price-benchmark, which opts into the full set.
"""

# id -> ชื่อคลัง exactly as it appears in the report's `คลังสินค้า` column.
INVENTORIES = {
    "3":  "คลังสระบุรี",
    "4":  "คลังลาดกระบัง",
    "5":  "คลัง HR กรุงเทพ",
    "6":  "คลัง HR ลาดกระบัง",
    "7":  "คลัง HR สระบุรี",
    "8":  "คลัง จป.สระบุรี",
    "9":  "คลัง จป.ลาดกระบัง",
    "10": "คลังทรัพย์สิน",
    "11": "คลังขอนแก่น",
    "12": "คลัง IT",
    "13": "คลังฝ่ายขาย",
    "15": "คลังไม่มีสต๊อก ลาดกระบัง",
    "16": "คลัง จป. ขอนแก่น",
    "17": "คลังทรัพย์สินลาดกระบัง",
    "18": "คลังทรัพย์สินสระบุรี",
    "21": "คลังไม่มีสต๊อก สระบุรี",
    "22": "คลังไม่มีสต๊อก กรุงเทพฯ",
    "23": "คลังจัดส่ง ลาดกระบัง",
    "24": "คลัง DIST",
    "25": "คลัง DIST จป.สระบุรี",
    "26": "คลัง DIST HR สระบุรี",
    "31": "คลัง DIST จป.ขอนแก่น",
    "32": "คลัง DIST จัดส่ง ขอนแก่น",
    "33": "คลัง DIST ขอนแก่น (SB)",
    "34": "คลัง TDM",
    "35": "คลัง OPS",
    "36": "คลังฝ่ายสำนักเลขา",
    "37": "คลัง HR-ศูนย์จัดส่งบางปะกง",
    "38": "คลังจัดส่ง (บางปะกง)",
    "39": "คลัง บัญชีการเงิน สกท.",
    "40": "คลังจัดส่่ง (สระบุรี)",   # sic — ATMS has a double ่ in this label
}

# Warehouses that appear in movement rows but whose ATMS inventory_id we cannot
# resolve. Two flavours, same problem:
#
#   901 "คลัง DIST ขอนแก่น" — deleted from ATMS. Absent from the dropdown AND
#       the report rejects its id (probed 2026-08-26: ids 1, 2, 14, 19, 20,
#       27-30 and 41-52 all return an HTML error page, while a live-but-empty
#       inventory returns a valid empty sheet). Distinct from 33
#       "คลัง DIST ขอนแก่น (SB)".
#   902 "คลังฝ่ายขายและรถร่วม" — still active (rows in 2026-06 and 2026-08) but
#       missing from the dropdown, and no id in it reports its rows. Traced by
#       hunting its SKU (SE01-01) across every known inventory: no match.
#
# Neither can be fetched by id, so there is no real id to stay consistent with.
# Both get a synthetic one from a reserved 900+ range ATMS will never allocate.
# Stable and explicit beats guessing an id and silently mis-attributing rows.
#
# TODO: if the report form's URL turns up, re-scrape <select name="inventory_id">
# — it may list 902 under a permission the extract account lacks.
RETIRED = {
    "คลัง DIST ขอนแก่น":     "901",
    "คลังฝ่ายขายและรถร่วม": "902",
}

# Reverse map, used to recover inventory_id from a row's `คลังสินค้า` value when
# the report is pulled with inventory_id="" (ทั้งหมด). Without this the rows
# would be stamped with an empty inventory_id and duplicate the existing data
# under fresh row_keys.
BY_NAME = {name: inv for inv, name in INVENTORIES.items()}
BY_NAME.update(RETIRED)

RETIRED_IDS = list(RETIRED.values())

# The four spare-parts warehouses the pipeline has always pulled.
LEGACY_4 = ["4", "3", "11", "24"]
LEGACY_4_NAMES = [INVENTORIES[i] for i in LEGACY_4]

ALL = list(INVENTORIES) + RETIRED_IDS
NEW = [i for i in ALL if i not in LEGACY_4]

# Functional grouping, used to render the warehouse filter in /price-benchmark
# as sections rather than one flat 31-item list.
GROUPS = {
    "อะไหล่/สต็อกหลัก": LEGACY_4,
    "HR":              ["5", "6", "7", "26", "37"],
    "จป.":              ["8", "9", "16", "25", "31"],
    "ทรัพย์สิน":         ["10", "17", "18"],
    "จัดส่ง":            ["23", "32", "38", "40"],
    "ไม่มีสต๊อก":         ["15", "21", "22"],
    "หน่วยงานสนับสนุน":  ["12", "13", "35", "36", "39"],
    "อื่น ๆ":            ["33", "34"] + RETIRED_IDS,
}


def group_of(inv_id: str) -> str:
    for g, ids in GROUPS.items():
        if inv_id in ids:
            return g
    return "อื่น ๆ"


NAME_OF = dict(INVENTORIES)
NAME_OF.update({inv: name for name, inv in RETIRED.items()})


def _self_check() -> None:
    covered = [i for ids in GROUPS.values() for i in ids]
    assert sorted(covered) == sorted(ALL), "GROUPS must cover every inventory exactly once"
    assert len(covered) == len(set(covered)), "an inventory appears in two groups"
    assert len(BY_NAME) == len(INVENTORIES) + len(RETIRED), "duplicate warehouse name"
    assert not set(RETIRED_IDS) & set(INVENTORIES), "synthetic id collides with a real one"


_self_check()

if __name__ == "__main__":
    for g, ids in GROUPS.items():
        print(f"{g} ({len(ids)})")
        for i in ids:
            print(f"   {i:>3}  {NAME_OF[i]}")
    print(f"\ntotal={len(ALL)}  legacy={len(LEGACY_4)}  new={len(NEW)}")
