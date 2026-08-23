"""
รัน migration .sql กับฐานข้อมูลที่ชี้ด้วย DATABASE_URL — ใช้แทน psql (เครื่อง dev ไม่มี psql)

    set DATABASE_URL=postgresql://...        (PowerShell: $env:DATABASE_URL = "postgresql://...")
    python scripts/migrations/run_migration.py scripts/migrations/2026-08-21_ac_form_investigate.sql

- ไฟล์ .sql คุม transaction เอง (BEGIN/COMMIT) สคริปต์นี้จึงเปิด autocommit ให้
- พังกลางทาง = ROLLBACK ทั้งชุด ไม่มีสภาพครึ่ง ๆ กลาง ๆ
- ใส่ --dry-run เพื่อดูว่าจะต่อไปที่ไหน + อ่าน SQL โดยไม่รันจริง
"""

import os
import sys
from urllib.parse import urlparse

import psycopg2


def describe(url: str) -> str:
    """แสดงปลายทางแบบไม่หลุดรหัสผ่าน"""
    parsed = urlparse(url)
    return f"{parsed.hostname}:{parsed.port or 5432}/{(parsed.path or '').lstrip('/')} (user={parsed.username})"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    if len(args) != 1:
        print("usage: python scripts/migrations/run_migration.py <file.sql> [--dry-run]")
        return 2

    sql_path = args[0]
    if not os.path.isfile(sql_path):
        print(f"ไม่พบไฟล์: {sql_path}")
        return 2

    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL ยังไม่ได้ตั้ง — ดึงจาก Render > PostgreSQL > External Database URL")
        return 2

    with open(sql_path, encoding="utf-8") as handle:
        sql = handle.read()

    print(f"ไฟล์      : {sql_path}")
    print(f"ปลายทาง   : {describe(url)}")

    if dry_run:
        print("\n--- dry run: ไม่ได้รันจริง ---\n")
        print(sql)
        return 0

    conn = psycopg2.connect(url)
    try:
        conn.autocommit = True  # ให้ BEGIN/COMMIT ในไฟล์ .sql เป็นตัวคุมเอง
        with conn.cursor() as cur:
            cur.execute(sql)
        print("\n✅ migration สำเร็จ")

        # ตรวจผลจริงจาก information_schema ไม่ใช่เชื่อว่าคำสั่งผ่านแล้วพอ
        checks = [
            ("accident_cases", "repair_request_no"),
            ("accident_cases", "breakdown_status"),
            ("accident_case_damage_items", "damage_category"),
            ("accident_case_investigate_whys", "root_cause_client_id"),
            ("accident_case_investigate_root_causes", "problem"),
            ("accident_case_investigate_investigators", "employee_id"),
        ]
        print("\nตรวจผล:")
        with conn.cursor() as cur:
            for table, column in checks:
                cur.execute(
                    """
                    SELECT data_type, character_maximum_length
                      FROM information_schema.columns
                     WHERE table_name = %s AND column_name = %s
                    """,
                    (table, column),
                )
                row = cur.fetchone()
                if row:
                    kind = f"{row[0]}({row[1]})" if row[1] else row[0]
                    print(f"  ✅ {table}.{column} — {kind}")
                else:
                    print(f"  ❌ {table}.{column} — ไม่พบ")
        return 0
    except Exception as exc:
        print(f"\n❌ migration ไม่สำเร็จ (rollback แล้ว): {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
