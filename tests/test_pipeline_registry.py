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
