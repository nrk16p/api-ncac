"""Evidence for each candidate (spec §4.3) — pure functions.

Day-level evidence (computed once per truck-day): sensor_noise_parked and day_rise_l.
Per-candidate evidence: size, speed, excess over the truck's expected burn, whether the level came
back (fully: recovered_*, half-way: rebound_60), engine/motion during the change, data gaps, place,
night. both_boxes and the 30-day history counts are filled in later by events.py.
"""
import numpy as np

from detect import Candidate, Context, DaySeries, path_km_between, straight_km, unexplained_rise
from places import find_place

NIGHT_FROM, NIGHT_TO = 18 * 60, 6 * 60
RECOVERY_WINDOWS = (10, 30, 60, 120)


def minute_weights(day: DaySeries, gap_min: int) -> np.ndarray:
    """Minutes each bucket stands for (time to the next bucket, capped at gap_min) — Besttech sends a
    point only every ~3 min when parked, so counting buckets would under-count parked time."""
    if day.m.size == 0:
        return np.zeros(0)
    w = np.minimum(np.diff(day.m, append=day.m[-1] + 1), gap_min).astype(float)
    return w


def sensor_noise_pct(day: DaySeries, ctx: Context, parked_kmh: float) -> float:
    """p90 of |reading − rolling level| on parked minutes, % of tank (works for 1 reading/min too)."""
    ok = (day.speed <= parked_kmh) & ~np.isnan(ctx.clean) & ~np.isnan(ctx.level)
    if ok.sum() < 10:
        return 0.0
    return float(np.percentile(np.abs(ctx.clean[ok] - ctx.level[ok]), 90)) / day.tank_l * 100


def day_evidence(day: DaySeries, ctx: Context, settings: dict) -> dict:
    return {"sensor_noise_parked": round(sensor_noise_pct(day, ctx, settings["parked_kmh"]), 2),
            "day_rise_l": round(unexplained_rise(ctx, settings["refuel_min_l"]), 1),
            "spikes_removed": ctx.spikes}


def _parked_levels_after(day: DaySeries, ctx: Context, index: int, minutes: int, parked_kmh: float) -> np.ndarray:
    sel = (day.m > day.m[index]) & (day.m <= day.m[index] + minutes) & (day.speed <= parked_kmh)
    values = ctx.level[sel]
    return values[~np.isnan(values)]


def _position(day: DaySeries, index: int) -> tuple[float | None, float | None]:
    ok = np.flatnonzero(~np.isnan(day.lat))
    if ok.size == 0:
        return None, None
    j = ok[np.argmin(np.abs(ok - index))]
    return float(day.lat[j]), float(day.lng[j])


def expected_burn(day: DaySeries, c: Candidate, baseline: dict, weights: np.ndarray, parked_kmh: float) -> float:
    sl = slice(c.i0, c.i1)
    parked_on = (day.speed[sl] <= parked_kmh) & (day.engine[sl] == 1)
    hours = float(weights[sl][parked_on].sum()) / 60
    km = straight_km(day, c.i0, c.i1) if c.where == "gap" else path_km_between(day, c.i0, c.i1)
    return baseline["idle_lph"] * hours + baseline["l_per_km"] * km


def evidence(day: DaySeries, ctx: Context, c: Candidate, baseline: dict, settings: dict,
             places: list[dict], day_ev: dict) -> dict:
    parked_kmh = settings["parked_kmh"]
    tol = max(settings["recover_tol_l"], settings["recover_tol_pct"] / 100 * day.tank_l)
    weights = minute_weights(day, settings["gap_min"])
    litres = c.litres
    duration = max(1, int(day.m[c.i1] - day.m[c.i0]))
    burn = expected_burn(day, c, baseline, weights, parked_kmh)
    ev = {"kind": c.kind, "where": c.where, "litres": round(litres, 1),
          "pct_tank": round(litres / day.tank_l * 100, 1), "duration_min": duration,
          "rate_l_per_min": round(litres / duration, 2), "expected_burn_l": round(burn, 1),
          "excess_over_burn_l": round(litres - burn if c.kind != "refuel" else litres, 1),
          "level_before": round(c.before, 1), "level_after": round(c.after, 1)}
    if c.kind == "refuel":
        for n in RECOVERY_WINDOWS:
            ev[f"recovered_{n}"] = False
        ev["rebound_60"] = False
        for n in (30, 60):
            after = _parked_levels_after(day, ctx, c.i1, n, parked_kmh)
            ev[f"stays_up_{n}"] = bool(after.size and after.min() >= c.after - tol)
    else:
        for n in RECOVERY_WINDOWS:
            after = _parked_levels_after(day, ctx, c.i1, n, parked_kmh)
            ev[f"recovered_{n}"] = bool(after.size and after.max() >= c.before - tol)
        after = _parked_levels_after(day, ctx, c.i1, 60, parked_kmh)
        ev["rebound_60"] = bool(after.size and after.max() >= c.after + 0.5 * litres)
    window = slice(c.i0, c.i1 + 1)
    w = weights[window]
    total = float(w.sum()) or 1.0
    ev["engine_off_share"] = round(float(w[day.engine[window] == 0].sum()) / total, 2)
    ev["moving_share"] = round(float(w[day.speed[window] > parked_kmh].sum()) / total, 2)
    ev["gap_min"] = int(np.diff(day.m[window]).max()) if c.i1 > c.i0 else 0
    lat, lng = _position(day, c.i0)
    place = find_place(lat, lng, places)
    ev["at_place"] = place is not None
    ev["place_name"] = place["name"] if place else None
    ev["lat"], ev["lng"] = lat, lng
    start = int(day.m[c.i0])
    ev["night"] = start >= NIGHT_FROM or start < NIGHT_TO
    ev["source"] = day.source
    ev["start_min"], ev["end_min"] = start, int(day.m[c.i1])
    ev.update(day_ev)
    return ev
