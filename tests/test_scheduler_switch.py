import importlib

import pytest


@pytest.fixture
def main_module(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    return importlib.import_module("main")


@pytest.mark.parametrize("value,expected", [
    (None, True), ("true", True), ("1", True), ("TRUE", True),
    ("false", False), ("0", False), ("no", False), (" Off ", False),
])
def test_scheduler_enabled(main_module, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SCHEDULER_ENABLED", value)
    assert main_module.scheduler_enabled() is expected


def test_health_reports_scheduler(main_module, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    assert main_module.health() == {"ok": True, "scheduler": False}
