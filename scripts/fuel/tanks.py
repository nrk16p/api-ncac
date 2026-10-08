"""Tank-size helpers for fuel_tanks (spec §3.4) — pure functions."""
import math
import re

import numpy as np

MIN_TANK_L, MAX_TANK_L = 40.0, 1000.0
OBSERVED_PCT = 99.5
DEFAULT_TANK_L = 200.0
CALIB_MIN_R2, CALIB_MIN_PAIRS = 0.9, 200
PARKED_KMH = 5
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def parse_capacity(raw) -> float | None:
    """ATMS ความจุถังน้ำมัน → litres ("200", "200L", 200 → 200.0); None when missing or implausible."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        match = _NUMBER.search(str(raw).replace(",", ""))
        if not match:
            return None
        value = float(match.group(0))
    if not math.isfinite(value) or not MIN_TANK_L <= value <= MAX_TANK_L:
        return None
    return value


def pair_minutes(bt: dict, te: dict) -> list[tuple[float, float]]:
    """(percent, litres) for minutes where both boxes have valid fuel and both trucks read as parked.
    bt: decoded Besttech columns (centi-percent); te: decoded Terminus columns (deci-litres)."""
    _, bi, ti = np.intersect1d(bt["m"], te["m"], return_indices=True)
    bt_fuel = bt["fuel"][bi].astype(float)
    te_fuel = te["fuel"][ti].astype(float)
    ok = ((bt_fuel > 0) & (te_fuel > 0)
          & (bt["speed"][bi] <= PARKED_KMH) & (te["speed"][ti] <= PARKED_KMH))
    return list(zip((bt_fuel[ok] / 100.0).tolist(), (te_fuel[ok] / 10.0).tolist()))


def fit_tank(pairs: list[tuple[float, float]]) -> tuple[float, float, int] | None:
    """Least squares through the origin, litres = k × percent → (tank_l = 100 k, r², n)."""
    if len(pairs) < 2:
        return None
    x = np.array([p for p, _ in pairs], dtype=float)
    y = np.array([litres for _, litres in pairs], dtype=float)
    sxx = float((x * x).sum())
    if sxx == 0:
        return None
    k = float((x * y).sum()) / sxx
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(((y - k * x) ** 2).sum()) / sst if sst > 0 else 0.0
    return 100.0 * k, r2, len(pairs)


def high_reading(litres, pct: float = OBSERVED_PCT) -> float | None:
    """The p99.5 of the valid litre readings: the level a full tank shows, without the few minutes a
    spike or a stuck-high value adds (a raw maximum of fuel_hi oversizes tanks)."""
    values = np.asarray(litres, dtype="float64")
    values = values[np.isfinite(values) & (values >= 0)]
    return float(np.percentile(values, pct)) if values.size else None


def observed_tank(max_litres: float | None) -> float | None:
    """High litre reading (high_reading) → tank size rounded up to 10 L (Terminus levels cluster at ~80/200/390)."""
    if max_litres is None or not math.isfinite(max_litres) or max_litres <= 0:
        return None
    value = math.ceil(max_litres / 10.0) * 10.0
    return value if MIN_TANK_L <= value <= MAX_TANK_L else None


def resolve_tank(atms_l: float | None = None, fit: tuple[float, float, int] | None = None,
                 observed_l: float | None = None) -> dict:
    fit_info = {"fit_r2": round(fit[1], 4), "n_pairs": fit[2]} if fit else {}
    if atms_l:
        return {"tank_l": float(atms_l), "tank_from": "atms", **fit_info}
    if fit and fit[1] >= CALIB_MIN_R2 and fit[2] >= CALIB_MIN_PAIRS and MIN_TANK_L <= fit[0] <= MAX_TANK_L:
        return {"tank_l": round(fit[0], 1), "tank_from": "calibrated", **fit_info}
    if observed_l:
        return {"tank_l": float(observed_l), "tank_from": "observed", **fit_info}
    return {"tank_l": DEFAULT_TANK_L, "tank_from": "default", **fit_info}
