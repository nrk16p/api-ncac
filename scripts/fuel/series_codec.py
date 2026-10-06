"""Packed binary columns for analytics.gps_series (spec §3.1, enc version 1).

Each column is little-endian bytes holding n values (one per minute bucket).
fuel / fuel_lo / fuel_hi are integers in the document's fuel_unit:
  "dl"   deci-litres   (Terminus — the sensor reports litres)
  "cpct" centi-percent (Besttech — the sensor reports % of tank)
-1 means no valid fuel reading in that minute. lat/lng are degrees × 1e5; 0 means no position.
Mirrored by fuel-control-center src/lib/series-codec.ts — change both together.
"""
import numpy as np

ENC_VERSION = 1
FUEL_MISSING = -1
COLUMNS = {
    "m": "<u2",
    "fuel": "<i2",
    "fuel_lo": "<i2",
    "fuel_hi": "<i2",
    "speed": "u1",
    "engine": "u1",
    "lat": "<i4",
    "lng": "<i4",
}
_FUEL_SCALE = {"dl": 10.0, "cpct": 100.0}
_DEG_SCALE = 1e5


def encode_columns(cols: dict[str, np.ndarray]) -> dict[str, bytes]:
    n = None
    out = {}
    for name, dtype in COLUMNS.items():
        if name not in cols:
            raise ValueError(f"missing column {name}")
        values = np.asarray(cols[name])
        if n is None:
            n = len(values)
        elif len(values) != n:
            raise ValueError(f"column {name} has {len(values)} values, expected {n}")
        out[name] = values.astype(dtype).tobytes()
    return out


def decode_column(raw: bytes, name: str, n: int) -> np.ndarray:
    return np.frombuffer(bytes(raw), dtype=COLUMNS[name], count=n)


def decode_columns(raw: dict[str, bytes], n: int) -> dict[str, np.ndarray]:
    return {name: decode_column(raw[name], name, n) for name in COLUMNS}


def fuel_to_int(values, unit: str) -> np.ndarray:
    scale = _FUEL_SCALE[unit]
    arr = np.asarray(values, dtype="float64")
    out = np.full(arr.shape, FUEL_MISSING, dtype="int16")
    ok = np.isfinite(arr) & (arr >= 0)
    out[ok] = np.clip(np.rint(arr[ok] * scale), 0, 32767).astype("int16")
    return out


def fuel_to_litres(values, unit: str, tank_l: float) -> np.ndarray:
    raw = np.asarray(values, dtype="float64")
    litres = raw / _FUEL_SCALE[unit]
    if unit == "cpct":
        litres = litres * tank_l / 100.0
    litres[raw < 0] = np.nan
    return litres


def deg_to_int(values) -> np.ndarray:
    arr = np.asarray(values, dtype="float64")
    out = np.zeros(arr.shape, dtype="int32")
    ok = np.isfinite(arr)
    out[ok] = np.rint(arr[ok] * _DEG_SCALE).astype("int32")
    return out


def int_to_deg(values) -> np.ndarray:
    return np.asarray(values, dtype="float64") / _DEG_SCALE
