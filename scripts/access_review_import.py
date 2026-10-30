"""Import Access Review data from Excel — dry run by default.

    python scripts/access_review_import.py ATMS "Role ATMS.xlsx"                 # report only (SELECTs)
    python scripts/access_review_import.py WINSPEED WINSpeed_User_Security.xlsx
    python scripts/access_review_import.py EMAIL group-mails.xlsx --commit       # writes ar_* tables

Uses DATABASE_URL from .env. --commit writes to whatever database that is: run it against dev first.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import SessionLocal  # noqa: E402
from services.access_review import importer  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("system", type=str.upper, choices=importer.SYSTEMS)
    ap.add_argument("file")
    ap.add_argument("--commit", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)

    with open(args.file, "rb") as fh:
        parsed = importer.parse(args.system, importer.load_workbook(fh.read()))

    db = SessionLocal()
    try:
        report = importer.run_import(db, args.system, parsed, dry_run=not args.commit)
    finally:
        db.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    mode = "COMMIT" if args.commit else "DRY RUN (nothing written)"
    print(f"[{report['system']}] {mode}")
    for key in ("roles_in_file", "roles_added", "roles_updated", "items_replaced", "items_added",
                "items_reactivated", "items_deleted", "items_kept_for_history", "employees_in_file",
                "user_roles_in_file", "user_roles_added", "user_roles_updated", "user_roles_reactivated",
                "user_roles_deactivated"):
        print(f"  {key:26} {report[key]}")
    unmatched = report["unmatched"]
    print(f"  {'unmatched':26} {len(unmatched)}")
    by_account = Counter((u["account_name"] or u["employee_id"] or "-", u["reason"]) for u in unmatched)
    for (account, reason), n in by_account.most_common(30):
        print(f"    - {account}: {reason} ({n} role(s))")
    for w in report["warnings"]:
        print(f"  warning: {w}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
