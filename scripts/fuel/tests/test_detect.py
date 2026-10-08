import numpy as np

from detect import find_candidates, hampel, prepare, rolling_median, segments, unexplained_rise
from fuel_settings import DEFAULTS
from synth import driving, gap_day, noise_day, parked, ramp, refuel_day, series, siphon_day, slosh_day


def test_hampel_removes_single_spike_only():
    m = np.arange(10)
    values = np.array([45.0, 45.1, 44.9, 45.0, 69.8, 45.0, 44.8, 45.0, 44.9, 45.1])
    clean, removed = hampel(m, values, 7, 3.0, 4.0)
    assert removed == 1 and np.isnan(clean[4]) and np.allclose(clean[[0, 5, 9]], values[[0, 5, 9]])


def test_rolling_median_ignores_missing():
    m = np.arange(5)
    out = rolling_median(m, np.array([1.0, np.nan, 3.0, 5.0, np.nan]), 3)
    assert out.tolist() == [1.0, 2.0, 4.0, 4.0, 5.0]


def test_segments_split_on_motion_and_gaps():
    _, day = series(gap_day())
    segs, gaps = segments(day, 5, 10)
    assert [(s.parked, int(day.m[s.i0]), int(day.m[s.i1])) for s in segs] == [(True, 0, 119), (True, 160, 299)]
    assert [(int(day.m[a]), int(day.m[b])) for a, b in gaps] == [(119, 160)]


def candidates(points):
    _, day = series(points)
    ctx = prepare(day, DEFAULTS)
    return day, ctx, find_candidates(day, ctx, DEFAULTS)


def test_siphon_is_one_parked_drop():
    day, _, found = candidates(siphon_day())
    assert len(found) == 1
    c = found[0]
    assert (c.kind, c.where) == ("drop", "parked") and c.litres == np.float64(30.0)
    assert 128 <= day.m[c.i0] <= 133 and 142 <= day.m[c.i1] <= 146


def test_gap_drop_is_a_gap_candidate():
    _, _, found = candidates(gap_day())
    assert [(c.kind, c.where, round(c.litres)) for c in found] == [("gap", "gap", 25)]


def test_refuel_is_a_rise():
    _, _, found = candidates(refuel_day())
    assert [(c.kind, round(c.litres)) for c in found] == [("refuel", 80)]


def test_slosh_while_driving_gives_nothing():
    _, _, found = candidates(slosh_day())
    assert found == []


def test_dip_then_back_is_found_and_rise_is_counted():
    _, ctx, found = candidates(noise_day())
    assert [(c.kind, c.where, round(c.litres)) for c in found] == [("drop", "parked", 20)]
    assert unexplained_rise(ctx, DEFAULTS["refuel_min_l"]) == np.float64(19.0)


def test_blip_hours_before_a_siphon_does_not_stretch_the_event():
    """A 4-min 10 L dip at 01:00, then a real 30 L siphon 05:00–05:15: the event is the siphon only
    (the start used to anchor on the dip → m 59 → 314, 255 min, 0.12 L/min)."""
    points = (parked(0, 60, 150.0) + parked(60, 64, 140.0) + parked(64, 300, 150.0)
              + parked(300, 315, ramp(300, 315, 150.0, 120.0)) + parked(315, 420, 120.0))
    _, day = series(points)
    ctx = prepare(day, DEFAULTS)
    (c,) = [c for c in find_candidates(day, ctx, DEFAULTS) if c.kind == "drop"]
    assert 295 <= day.m[c.i0] <= 302 and 312 <= day.m[c.i1] <= 318


def test_outage_mid_drive_takes_no_levels_from_sloshing_minutes():
    """20-min outage while driving; the moving minutes at its edges read 12 L low / 12 L high (slosh).
    Gap levels come from parked minutes only, so there is no +24 L 'refuel' / gap candidate."""
    points = (parked(0, 60, 152.0) + driving(60, 100, lambda m: 140.0 if m >= 94 else 152.0)[0]
              + driving(120, 160, lambda m: 164.0 if m < 126 else 152.0)[0] + parked(160, 240, 150.0))
    _, day = series(points)
    ctx = prepare(day, DEFAULTS)
    assert ctx.gaps and [c for c in find_candidates(day, ctx, DEFAULTS) if c.where == "gap"] == []
