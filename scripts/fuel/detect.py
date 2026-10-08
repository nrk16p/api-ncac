"""Candidate fuel events in one truck-day of gps_series (spec §4.1) — pure functions, no I/O.

Steps: decode → litres → Hampel spike filter → 5-min rolling-median level → split the day into
parked / moving segments (a gap longer than gap_min also splits) → trusted levels only from parked
stretches of at least plateau_min minutes → candidates:
  drop   level falls ≥ min_drop_l inside a parked stretch, or between two parked stretches
  refuel level rises ≥ refuel_min_l, same places
  gap    the box is silent ≥ gap_min and the level is ≥ min_drop_l lower afterwards
The thresholds are raw litres on purpose (sensitive); scoring later compares each candidate
with the truck's expected burn.
"""
from dataclasses import dataclass

import numpy as np

from series_codec import decode_columns, fuel_to_litres, int_to_deg

EARTH_RADIUS_KM = 6371.0


@dataclass
class DaySeries:
    plate: str
    source: str
    date_key: str
    tank_l: float
    status: str           # coverage status from Part 1
    valid_share: float    # coverage.fuel_valid_share
    m: np.ndarray         # minute of day
    fuel: np.ndarray      # litres, NaN = no valid reading
    speed: np.ndarray
    engine: np.ndarray
    lat: np.ndarray       # degrees, NaN = no position
    lng: np.ndarray


@dataclass
class Segment:
    parked: bool
    i0: int               # first index (inclusive)
    i1: int               # last index (inclusive)


@dataclass
class Plateau:
    seg: Segment
    start_level: float
    end_level: float


@dataclass
class Context:
    clean: np.ndarray     # fuel after the spike filter (litres)
    level: np.ndarray     # rolling-median level (litres)
    spikes: int
    segments: list
    gaps: list            # (index before, index after)
    plateaus: list


@dataclass
class Candidate:
    kind: str             # drop | refuel | gap
    where: str            # parked | moving | gap
    i0: int               # index just before the change
    i1: int               # index where the change is complete
    before: float         # level before (litres)
    after: float          # level after (litres)

    @property
    def litres(self) -> float:
        return abs(self.after - self.before)


def day_series(doc: dict) -> DaySeries | None:
    """gps_series document → DaySeries (None when the day has no readings)."""
    if not doc.get("n"):
        return None
    cols = decode_columns(doc["cols"], doc["n"])
    lat, lng = int_to_deg(cols["lat"]), int_to_deg(cols["lng"])
    no_pos = (cols["lat"] == 0) | (cols["lng"] == 0)
    lat[no_pos] = np.nan
    lng[no_pos] = np.nan
    cov = doc.get("coverage") or {}
    return DaySeries(plate=doc["plate"], source=doc["source"], date_key=doc["date_key"],
                     tank_l=float(doc["tank_l"]), status=cov.get("status", "ok"),
                     valid_share=float(cov.get("fuel_valid_share", 0.0)),
                     m=cols["m"].astype(int), fuel=fuel_to_litres(cols["fuel"], doc["fuel_unit"], doc["tank_l"]),
                     speed=cols["speed"].astype(float), engine=cols["engine"].astype(int), lat=lat, lng=lng)


def _windows(m_valid: np.ndarray, half: float) -> tuple[np.ndarray, np.ndarray]:
    return (np.searchsorted(m_valid, m_valid - half, side="left"),
            np.searchsorted(m_valid, m_valid + half, side="right"))


def hampel(m: np.ndarray, values: np.ndarray, window_min: int, k: float, min_dev: float) -> tuple[np.ndarray, int]:
    """Replace single-reading spikes with NaN; returns (cleaned, number removed)."""
    out = values.copy()
    idx = np.flatnonzero(~np.isnan(values))
    if idx.size < 3:
        return out, 0
    vm, vv = m[idx], values[idx]
    lo, hi = _windows(vm, window_min // 2)
    removed = 0
    for i in range(idx.size):
        window = vv[lo[i]:hi[i]]
        med = float(np.median(window))
        mad = 1.4826 * float(np.median(np.abs(window - med)))
        if abs(vv[i] - med) > max(k * mad, min_dev):
            out[idx[i]] = np.nan
            removed += 1
    return out, removed


def rolling_median(m: np.ndarray, values: np.ndarray, window_min: int) -> np.ndarray:
    """Centred time-based rolling median over valid values (NaN where the window is empty)."""
    out = np.full(values.shape, np.nan)
    idx = np.flatnonzero(~np.isnan(values))
    if idx.size == 0:
        return out
    vm, vv = m[idx], values[idx]
    lo = np.searchsorted(vm, m - window_min // 2, side="left")
    hi = np.searchsorted(vm, m + window_min // 2, side="right")
    for i in range(m.size):
        if hi[i] > lo[i]:
            out[i] = float(np.median(vv[lo[i]:hi[i]]))
    return out


def segments(day: DaySeries, parked_kmh: float, gap_min: int) -> tuple[list[Segment], list[tuple[int, int]]]:
    """Split the day into parked / moving runs; a silence longer than gap_min also splits.
    Returns (segments, gaps) where a gap is (index before, index after)."""
    segs: list[Segment] = []
    gaps: list[tuple[int, int]] = []
    if day.m.size == 0:
        return segs, gaps
    parked = day.speed <= parked_kmh
    start = 0
    for i in range(1, day.m.size):
        gap = day.m[i] - day.m[i - 1] > gap_min
        if gap or parked[i] != parked[start]:
            segs.append(Segment(bool(parked[start]), start, i - 1))
            if gap:
                gaps.append((i - 1, i))
            start = i
    segs.append(Segment(bool(parked[start]), start, day.m.size - 1))
    return segs, gaps


def _edge_level(day: DaySeries, level: np.ndarray, seg: Segment, minutes: int, at_start: bool) -> float:
    if at_start:
        sel = (day.m >= day.m[seg.i0]) & (day.m < day.m[seg.i0] + minutes)
    else:
        sel = (day.m <= day.m[seg.i1]) & (day.m > day.m[seg.i1] - minutes)
    sel[: seg.i0] = False
    sel[seg.i1 + 1:] = False
    values = level[sel]
    values = values[~np.isnan(values)]
    return float(np.median(values)) if values.size else float("nan")


def plateaus(day: DaySeries, level: np.ndarray, segs: list[Segment], plateau_min: int) -> list[Plateau]:
    out = []
    for seg in segs:
        if not seg.parked or day.m[seg.i1] - day.m[seg.i0] + 1 < plateau_min:
            continue
        start = _edge_level(day, level, seg, plateau_min, True)
        end = _edge_level(day, level, seg, plateau_min, False)
        if not (np.isnan(start) or np.isnan(end)):
            out.append(Plateau(seg, start, end))
    return out


def _holds(day: DaySeries, level: np.ndarray, idx: list[int], k: int, test, minutes: int, forward: bool) -> bool:
    """level at idx[k] and every valid point within `minutes` after it (forward) or before it pass `test`."""
    t0 = int(day.m[idx[k]])
    j = k
    while 0 <= j < len(idx) and abs(int(day.m[idx[j]]) - t0) < minutes:
        if not test(level[idx[j]]):
            return False
        j += 1 if forward else -1
    return True


def _change_bounds(day: DaySeries, level: np.ndarray, seg: Segment, before: float, after: float,
                   minutes: int) -> tuple[int, int]:
    """Indices bracketing a change inside a segment: i1 = the first point from which the level stays near
    `after` for `minutes`; i0 = the last point before i1 that ends `minutes` near `before`. (Scanning
    forward for the first departure from `before` latched onto short wobbles hours earlier.)"""
    tol = 0.1 * abs(after - before)
    if after < before:
        near_after, near_before = (lambda v: v <= after + tol), (lambda v: v >= before - tol)
    else:
        near_after, near_before = (lambda v: v >= after - tol), (lambda v: v <= before + tol)
    idx = [i for i in range(seg.i0, seg.i1 + 1) if not np.isnan(level[i])]
    if not idx:
        return seg.i0, seg.i1
    k1 = next((k for k in range(len(idx)) if _holds(day, level, idx, k, near_after, minutes, True)), len(idx) - 1)
    k0 = next((k for k in range(k1, -1, -1) if _holds(day, level, idx, k, near_before, minutes, False)), 0)
    return idx[k0], max(idx[k1], idx[k0])


def _gap_level(day: DaySeries, level: np.ndarray, index: int, minutes: int, before: bool, parked_kmh: float) -> float:
    """Level next to a gap from parked minutes only (NaN if none): moving minutes slosh ±10 % of tank."""
    if before:
        sel = (day.m <= day.m[index]) & (day.m > day.m[index] - minutes)
        sel[index + 1:] = False
    else:
        sel = (day.m >= day.m[index]) & (day.m < day.m[index] + minutes)
        sel[:index] = False
    sel &= day.speed <= parked_kmh
    values = level[sel]
    values = values[~np.isnan(values)]
    return float(np.median(values)) if values.size else float("nan")


def prepare(day: DaySeries, settings: dict) -> Context:
    min_dev = max(settings["spike_min_l"], settings["spike_min_pct"] / 100 * day.tank_l)
    clean, spikes = hampel(day.m, day.fuel, settings["hampel_window_min"], settings["hampel_k"], min_dev)
    level = rolling_median(day.m, clean, settings["plateau_min"])
    segs, gaps = segments(day, settings["parked_kmh"], settings["gap_min"])
    return Context(clean, level, spikes, segs, gaps, plateaus(day, level, segs, settings["plateau_min"]))


def gap_between(ctx: Context, a: Plateau, b: Plateau) -> bool:
    return any(a.seg.i1 < after <= b.seg.i0 for _, after in ctx.gaps)


def find_candidates(day: DaySeries, ctx: Context, settings: dict) -> list[Candidate]:
    """All candidate events of the day, merged (same kind, closer than merge_min)."""
    min_drop, min_rise = settings["min_drop_l"], settings["refuel_min_l"]
    found: list[Candidate] = []

    def add(kind_if_drop: str, where: str, i0: int, i1: int, before: float, after: float) -> None:
        delta = after - before
        if delta <= -min_drop:
            found.append(Candidate(kind_if_drop, where, i0, i1, before, after))
        elif delta >= min_rise:
            found.append(Candidate("refuel", where, i0, i1, before, after))

    for p in ctx.plateaus:   # inside one parked stretch
        if abs(p.end_level - p.start_level) >= min(min_drop, min_rise):
            i0, i1 = _change_bounds(day, ctx.level, p.seg, p.start_level, p.end_level, settings["plateau_min"])
            add("drop", "parked", i0, i1, p.start_level, p.end_level)
    for a, b in zip(ctx.plateaus, ctx.plateaus[1:]):   # between two parked stretches, no gap between
        if not gap_between(ctx, a, b):
            add("drop", "moving", a.seg.i1, b.seg.i0, a.end_level, b.start_level)
    for i_before, i_after in ctx.gaps:
        before = _gap_level(day, ctx.level, i_before, settings["plateau_min"], True, settings["parked_kmh"])
        after = _gap_level(day, ctx.level, i_after, settings["plateau_min"], False, settings["parked_kmh"])
        if not (np.isnan(before) or np.isnan(after)):
            add("gap", "gap", i_before, i_after, before, after)
    return merge_candidates(day, found, settings["merge_min"])


def unexplained_rise(ctx: Context, refuel_min: float, min_step: float = 2.0) -> float:
    """Litres the level rose without a refuel during the day (between and inside parked stretches) —
    a sensor that climbs back by itself makes drops of the same size untrustworthy."""
    levels = []
    for p in ctx.plateaus:
        levels += [p.start_level, p.end_level]
    steps = np.diff(np.array(levels)) if len(levels) > 1 else np.array([])
    rises = steps[(steps >= min_step) & (steps < refuel_min)]
    return float(rises.sum())


def merge_candidates(day: DaySeries, found: list[Candidate], merge_min: int) -> list[Candidate]:
    out: list[Candidate] = []
    for c in sorted(found, key=lambda c: (day.m[c.i0], day.m[c.i1])):
        last = out[-1] if out else None
        if last and last.kind == c.kind and day.m[c.i0] - day.m[last.i1] < merge_min:
            out[-1] = Candidate(last.kind, last.where, last.i0, max(last.i1, c.i1), last.before, c.after)
        else:
            out.append(c)
    return out


def path_km_between(day: DaySeries, i0: int, i1: int) -> float:
    """Distance driven between two indices (haversine over consecutive positions, jumps > 5 km ignored)."""
    lat = np.radians(day.lat[i0:i1 + 1])
    lng = np.radians(day.lng[i0:i1 + 1])
    ok = ~(np.isnan(lat) | np.isnan(lng))
    lat, lng = lat[ok], lng[ok]
    if lat.size < 2:
        return 0.0
    a = np.sin(np.diff(lat) / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lng) / 2) ** 2
    steps = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return float(steps[steps <= 5.0].sum())


def straight_km(day: DaySeries, i0: int, i1: int) -> float:
    if np.isnan(day.lat[i0]) or np.isnan(day.lat[i1]):
        return 0.0
    la0, lo0, la1, lo1 = map(np.radians, (day.lat[i0], day.lng[i0], day.lat[i1], day.lng[i1]))
    a = np.sin((la1 - la0) / 2) ** 2 + np.cos(la0) * np.cos(la1) * np.sin((lo1 - lo0) / 2) ** 2
    return float(2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(min(max(a, 0.0), 1.0))))
