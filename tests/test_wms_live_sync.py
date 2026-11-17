import asyncio
from datetime import datetime

import routes.pipeline.pipeline_routes as pr


def test_live_windows_cover_the_requested_days():
    # 17/11 ย้อน 14 วัน = 03/11 → เดือนเดียว
    assert pr._live_windows(14, datetime(2026, 11, 17, 9)) == (1, "03/11/2026")
    # 05/11 ย้อน 14 วัน = 22/10 → ต้องดึง ต.ค. ด้วย
    assert pr._live_windows(14, datetime(2026, 11, 5, 9)) == (2, "22/10/2026")
    # ข้ามปี
    assert pr._live_windows(14, datetime(2027, 1, 3, 9)) == (2, "20/12/2026")


def test_pr_quick_syncs_sku_before_build():
    assert "atms_pr_quick" in pr.SYNC_BEFORE_BUILD


def test_live_sync_runs_steps_in_order(monkeypatch):
    calls = []
    monkeypatch.setenv("SAFETY_STOCK_BUILD_URL", "https://wms.example/api/cron/safety-stock-build")
    monkeypatch.setenv("SAFETY_STOCK_BUILD_TOKEN", "t")
    monkeypatch.setattr(pr, "_call_webhook", lambda url, token: (calls.append(url), (200, "ok"))[1])

    async def fake_run(kind, params=None, fire_webhook=True):
        calls.append((kind, params, fire_webhook))
        return True

    monkeypatch.setattr(pr, "_run", fake_run)
    pr._live_sync.clear()
    pr._live_sync.update({"status": "running", "steps": {k: {"status": "pending"} for k in pr.LIVE_SYNC_STEPS}})
    asyncio.run(pr.run_wms_live_sync("27", 14, "tester"))

    assert pr._live_sync["status"] == "ok"
    assert calls[0] == "https://wms.example/api/cron/atms-sku-sync?inventory=27"
    assert "source=live-sync" in calls[1] and "inventory=27" in calls[1]
    assert calls[2][0] == "atms_stockmovement_light" and calls[2][2] is False
    assert calls[3][0] == "atms_procurement_light" and calls[3][1]["ATMS_PR_PRUNE"] == "0"
    assert calls[4] == calls[1]


def test_live_sync_marks_error_but_keeps_going(monkeypatch):
    monkeypatch.setenv("SAFETY_STOCK_BUILD_URL", "https://wms.example/api/cron/safety-stock-build")
    monkeypatch.setattr(pr, "_call_webhook", lambda url, token: (500, "boom") if "atms-sku-sync" in url else (200, "ok"))

    async def fake_run(kind, params=None, fire_webhook=True):
        return True

    monkeypatch.setattr(pr, "_run", fake_run)
    pr._live_sync.clear()
    pr._live_sync.update({"status": "running", "steps": {k: {"status": "pending"} for k in pr.LIVE_SYNC_STEPS}})
    asyncio.run(pr.run_wms_live_sync("27", 14, None))
    assert pr._live_sync["status"] == "error"
    assert pr._live_sync["steps"]["sku"]["status"] == "error"
    assert pr._live_sync["steps"]["build2"]["status"] == "ok"
