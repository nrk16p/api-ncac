"""Expected burn per truck (spec §4.1) — pure functions.

Each truck-day gives raw rate observations (no event filtering — medians shrug off the rare theft):
  idle_rates  litres per engine-on hour on parked stretches ≥ 60 min with the engine on ≥ 80 % of the time
  km_rates    litres per km between two parked levels at least 5 km apart (no data gap between)
A truck's baseline is the median of its observations over the last `baseline_days`; with fewer than
`baseline_min_days` days of observations the fleet median is used instead.
"""
import numpy as np

from detect import Context, DaySeries, gap_between, path_km_between

MIN_IDLE_MIN = 60
MIN_IDLE_ON_SHARE = 0.8
MIN_KM = 5.0


def day_rates(day: DaySeries, ctx: Context) -> dict:
    idle, per_km = [], []
    for p in ctx.plateaus:
        minutes = day.m[p.seg.i1] - day.m[p.seg.i0] + 1
        on_share = float(day.engine[p.seg.i0:p.seg.i1 + 1].mean())
        if minutes >= MIN_IDLE_MIN and on_share >= MIN_IDLE_ON_SHARE:
            idle.append(float(max(0.0, p.start_level - p.end_level) / (minutes * on_share / 60)))
    for a, b in zip(ctx.plateaus, ctx.plateaus[1:]):
        if gap_between(ctx, a, b):
            continue
        km = path_km_between(day, a.seg.i1, b.seg.i0)
        if km >= MIN_KM:
            per_km.append(float(max(0.0, a.end_level - b.start_level) / km))
    return {"idle_rates": [round(r, 3) for r in idle], "km_rates": [round(r, 4) for r in per_km]}


def _median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def fleet_baseline(stats: list[dict]) -> dict:
    """Median over every observation of every truck (stats = fuel_day_stats documents)."""
    return {"idle_lph": _median([r for s in stats for r in s.get("idle_rates", [])]),
            "l_per_km": _median([r for s in stats for r in s.get("km_rates", [])])}


def truck_baseline(stats: list[dict], fleet: dict, min_days: int) -> dict:
    """stats = this truck's fuel_day_stats documents in the window."""
    idle_days = [s for s in stats if s.get("idle_rates")]
    km_days = [s for s in stats if s.get("km_rates")]
    idle = _median([r for s in idle_days for r in s["idle_rates"]]) if len(idle_days) >= min_days else None
    per_km = _median([r for s in km_days for r in s["km_rates"]]) if len(km_days) >= min_days else None
    return {"idle_lph": idle if idle is not None else fleet.get("idle_lph") or 0.0,
            "l_per_km": per_km if per_km is not None else fleet.get("l_per_km") or 0.0,
            "from": "truck" if idle is not None and per_km is not None else "fleet"}
