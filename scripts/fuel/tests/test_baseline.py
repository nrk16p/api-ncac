import pytest

from baseline import day_rates, fleet_baseline, truck_baseline
from detect import prepare
from fuel_settings import DEFAULTS
from synth import rates_day, series


def test_day_rates_idle_and_per_km():
    _, day = series(rates_day())
    rates = day_rates(day, prepare(day, DEFAULTS))
    assert rates["idle_rates"][0] == pytest.approx(3.0, abs=0.2)     # 6 L over 2 h idling
    assert rates["idle_rates"][1] == pytest.approx(0.0, abs=0.1)     # 1 h idling, level flat
    assert rates["km_rates"] == [pytest.approx(0.4, abs=0.02)]       # 12 L over 30 km
    assert all(type(r) is float for r in rates["idle_rates"] + rates["km_rates"])


def test_fleet_and_truck_baselines():
    stats = [{"idle_rates": [2.0, 4.0], "km_rates": [0.5]}, {"idle_rates": [3.0], "km_rates": []}]
    fleet = fleet_baseline(stats)
    assert fleet == {"idle_lph": 3.0, "l_per_km": 0.5}
    own = [{"idle_rates": [1.0], "km_rates": [0.2]}] * 7
    assert truck_baseline(own, fleet, 7) == {"idle_lph": 1.0, "l_per_km": 0.2, "from": "truck"}
    assert truck_baseline(own[:3], fleet, 7) == {"idle_lph": 3.0, "l_per_km": 0.5, "from": "fleet"}
    assert truck_baseline([], {"idle_lph": None, "l_per_km": None}, 7) == {"idle_lph": 0.0, "l_per_km": 0.0, "from": "fleet"}
