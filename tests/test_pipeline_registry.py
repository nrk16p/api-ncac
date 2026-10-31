from pathlib import Path

from routes.pipeline.pipeline_routes import PIPELINE_NAMES, PIPELINE_SCRIPTS, RUN_LOG_LOCATION, SCRIPTS_DIR

ROOT = Path(__file__).resolve().parent.parent


def test_every_pipeline_has_a_script_a_name_and_a_log_location():
    for name, script in PIPELINE_SCRIPTS.items():
        assert script.is_file(), name
        assert name in PIPELINE_NAMES and name in RUN_LOG_LOCATION, name


def test_jobs_tab_pipelines_log_to_etl_jobs():
    assert PIPELINE_SCRIPTS["overspeed"].relative_to(SCRIPTS_DIR).as_posix() == "overspeed/pipeline_overspeed.py"
    assert PIPELINE_SCRIPTS["rmc_compensation"].relative_to(SCRIPTS_DIR).as_posix() == "rmc/pipeline_rmc.py"
    for name in ("overspeed", "rmc_compensation"):
        assert PIPELINE_NAMES[name] == name  # the JobLog `pipeline` that GET /pipeline/status/{type} looks up
        assert RUN_LOG_LOCATION[name] == ("analytics", "etl_jobs")


def test_jobs_tab_pipelines_run_at_their_bangkok_times():
    source = (ROOT / "main.py").read_text(encoding="utf-8")  # CronTrigger hours are UTC (BKK − 7)
    assert 'CronTrigger(hour=21, minute=30), args=["overspeed"]' in source       # 04:30 BKK
    assert 'CronTrigger(hour=2, minute=0), args=["rmc_compensation"]' in source  # 09:00 BKK


def test_store_withdraw_drift_is_registered_and_posts_to_wms():
    assert PIPELINE_SCRIPTS["store-withdraw-drift"].relative_to(SCRIPTS_DIR).as_posix() == "store_withdraw/pipeline_store_withdraw_drift.py"
    assert PIPELINE_NAMES["store-withdraw-drift"] == "store-withdraw-drift"
    assert RUN_LOG_LOCATION["store-withdraw-drift"] == ("analytics", "etl_jobs")


def test_store_withdraw_drift_script_calls_drift_tick_with_the_key():
    import importlib.util

    spec = importlib.util.spec_from_file_location("sw_drift", PIPELINE_SCRIPTS["store-withdraw-drift"])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seen = {}

    class Resp:
        def __init__(self, status):
            self.status_code, self.text = status, "x"

        def json(self):
            return {"checked": 3, "drifted": 1, "skipped": 0}

    def fake_post(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers)
        return Resp(200)

    assert mod.tick("https://wms.test/", "k-1", post=fake_post) == {"checked": 3, "drifted": 1, "skipped": 0}
    assert seen == {"url": "https://wms.test/api/store-withdraw/drift-tick", "headers": {"x-api-key": "k-1"}}
    try:
        mod.tick("https://wms.test", "bad", post=lambda *a, **k: Resp(401))
    except RuntimeError as e:
        assert "401" in str(e)
    else:
        raise AssertionError("non-2xx must raise")
