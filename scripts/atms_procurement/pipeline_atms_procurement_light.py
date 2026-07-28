"""
Light variant ของ atms_procurement — ดึงเฉพาะ 30 วันล่าสุด (~11-12 นาที)
ใช้กับปุ่ม "รีเฟรชข้อมูล" ในหน้า /pr (mena-wms) · run-log แยก label "atms_procurement_light"
"""

import os
from datetime import datetime, timedelta

# window = 30 วันล่าสุด (Asia/Bangkok) + label แยก
_ict = datetime.utcnow() + timedelta(hours=7)
os.environ["ATMS_FROM_DATE"] = (_ict - timedelta(days=30)).strftime("%d/%m/%Y")
os.environ.setdefault("ATMS_RUN_LABEL", "atms_procurement_light")

from pipeline_atms_procurement import main  # noqa: E402

if __name__ == "__main__":
    main()
