import pytest

from detect import find_candidates, prepare
from features import day_evidence, evidence
from fuel_settings import DEFAULTS
from synth import BASELINE, HOME, consumption_day, gap_day, noise_day, refuel_day, series, siphon_day

PLACE = {"name": "ACON A109", "lat": HOME[0], "lng": HOME[1], "radius_m": 300.0, "polygon": []}


def first_evidence(points, places=()):
    _, day = series(points)
    ctx = prepare(day, DEFAULTS)
    c = find_candidates(day, ctx, DEFAULTS)[0]
    return evidence(day, ctx, c, BASELINE, DEFAULTS, list(places), day_evidence(day, ctx, DEFAULTS))


def test_siphon_evidence():
    ev = first_evidence(siphon_day())
    assert ev["kind"] == "drop" and ev["litres"] == 30.0 and ev["pct_tank"] == 15.0
    assert ev["expected_burn_l"] == 0.0 and ev["excess_over_burn_l"] == 30.0     # engine off: nothing burns
    assert ev["engine_off_share"] == 1.0 and ev["moving_share"] == 0.0 and ev["night"]
    assert not ev["recovered_120"] and not ev["rebound_60"] and ev["rate_l_per_min"] >= 1
    assert ev["at_place"] is False and ev["day_rise_l"] == 0.0 and ev["source"] == "terminus"


def test_place_is_found():
    ev = first_evidence(siphon_day(), [PLACE])
    assert ev["at_place"] and ev["place_name"] == "ACON A109"


def test_noise_dip_recovers():
    ev = first_evidence(noise_day())
    assert ev["recovered_30"] and not ev["recovered_10"] and ev["day_rise_l"] == 19.0


def test_gap_evidence():
    ev = first_evidence(gap_day())
    assert ev["kind"] == "gap" and ev["gap_min"] == 41 and ev["excess_over_burn_l"] == 25.0


def test_refuel_stays_up():
    ev = first_evidence(refuel_day())
    assert ev["kind"] == "refuel" and ev["stays_up_30"] and ev["stays_up_60"] and not ev["recovered_30"]


def test_consumption_matches_expected_burn():
    ev = first_evidence(consumption_day())
    assert ev["where"] == "moving" and ev["expected_burn_l"] == pytest.approx(12.0, abs=0.6)
    assert abs(ev["excess_over_burn_l"]) <= 0.6


def test_recovery_window_follows_persist_min():
    _, day = series(noise_day())
    ctx = prepare(day, DEFAULTS)
    c = find_candidates(day, ctx, DEFAULTS)[0]
    tuned = {**DEFAULTS, "persist_min": 90}
    ev = evidence(day, ctx, c, BASELINE, tuned, [], day_evidence(day, ctx, tuned))
    assert ev["recovered_90"] is True and "recovered_120" in ev
