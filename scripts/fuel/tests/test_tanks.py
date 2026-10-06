import numpy as np
import pytest

from tanks import fit_tank, observed_tank, pair_minutes, parse_capacity, resolve_tank


@pytest.mark.parametrize("raw,expected", [
    ("200", 200.0), ("200L", 200.0), (200, 200.0), ("1,000", 1000.0),
    (float("nan"), None), ("", None), (None, None), (5, None), ("abc", None),
])
def test_parse_capacity(raw, expected):
    assert parse_capacity(raw) == expected


def cols(m, fuel, speed):
    return {"m": np.array(m, dtype="uint16"), "fuel": np.array(fuel, dtype="int16"),
            "speed": np.array(speed, dtype="uint8")}


def test_pair_minutes_same_minute_both_parked_valid():
    bt = cols([10, 11, 12, 13], [5000, 5000, -1, 4000], [0, 30, 0, 0])
    te = cols([10, 11, 12, 14], [1000, 1000, 900, 800], [0, 0, 0, 0])
    assert pair_minutes(bt, te) == [(50.0, 100.0)]


def test_fit_tank_recovers_size():
    rng = np.random.default_rng(1)
    pct = rng.uniform(20, 100, 300)
    tank_l, r2, n = fit_tank(list(zip(pct, 2.0 * pct + rng.normal(0, 1, 300))))
    assert tank_l == pytest.approx(200, abs=1) and r2 > 0.99 and n == 300


def test_fit_tank_needs_usable_pairs():
    assert fit_tank([]) is None and fit_tank([(50.0, 100.0)]) is None
    assert fit_tank([(0.0, 1.0), (0.0, 2.0)]) is None


def test_observed_tank_rounds_up():
    assert observed_tank(199.0) == 200.0 and observed_tank(76.2) == 80.0
    assert observed_tank(None) is None and observed_tank(12.0) is None


def test_resolve_priority():
    good = (183.0, 0.95, 400)
    assert resolve_tank(atms_l=200.0, fit=good)["tank_from"] == "atms"
    assert resolve_tank(fit=good) == {"tank_l": 183.0, "tank_from": "calibrated", "fit_r2": 0.95, "n_pairs": 400}
    assert resolve_tank(fit=(183.0, 0.7, 400), observed_l=200.0)["tank_from"] == "observed"
    assert resolve_tank(fit=(183.0, 0.95, 50))["tank_from"] == "default"
    assert resolve_tank() == {"tank_l": 200.0, "tank_from": "default"}
