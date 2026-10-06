"""Two real Besttech truck-days (2026-10-05) — spec §7: noisy sensors must not raise loss events."""
import json
from datetime import date
from pathlib import Path

import pytest

from baseline import day_rates, fleet_baseline, truck_baseline
from detect import day_series, find_candidates, prepare
from features import day_evidence, evidence
from fuel_settings import DEFAULTS
from rules import LOSS_CLASSES, classify
from series_besttech import besttech_day_docs

DATA = Path(__file__).parent / "data"
FIELDS = ["gps_time", "fuel_percentage", "speed", "engine", "lat", "lng"]


def load(code):
    fixture = json.loads((DATA / f"besttech_{code}_2026-10-05.json").read_text())
    points = [dict(zip(FIELDS, [f"2026-10-05 {t}", fuel, speed, "ON" if engine else "OFF", lat, lng]))
              for t, fuel, speed, engine, lat, lng in fixture["points"]]
    doc = besttech_day_docs(date(2026, 10, 5), [], [[{"vehicle_no": fixture["vehicle_no"], "points": points}]], {})[0]
    return day_series(doc)


@pytest.mark.parametrize("code", ["ME152", "ME081"])
def test_noisy_real_day_has_no_loss_event(code):
    day = load(code)
    ctx = prepare(day, DEFAULTS)
    base = truck_baseline([day_rates(day, ctx)], fleet_baseline([day_rates(day, ctx)]), 1)
    day_ev = day_evidence(day, ctx, DEFAULTS)
    found = find_candidates(day, ctx, DEFAULTS)
    assert found, "the noisy sensor should still produce candidates to classify"
    classes = [classify(evidence(day, ctx, c, base, DEFAULTS, [], day_ev), DEFAULTS, day.status) for c in found]
    assert not set(classes) & set(LOSS_CLASSES)


def test_me152_spikes_are_removed():
    day = load("ME152")
    assert prepare(day, DEFAULTS).spikes >= 1      # 16:21:58 reads 69.8 % between 44–45 % readings
