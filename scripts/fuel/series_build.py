"""Turn one truck-day of GPS readings into an analytics.gps_series document (spec §3.1–3.2)."""
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import numpy as np

from series_codec import ENC_VERSION, deg_to_int, encode_columns, fuel_to_int, int_to_deg

TH_TZ = timezone(timedelta(hours=7))
STUCK_MIN_KM = 50.0       # a stuck sensor is only judged on a day the truck really drove
MAX_JUMP_KM = 5.0         # larger steps between consecutive minutes are GPS glitches
EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Reading:
    sec: int              # seconds since 00:00 Thai time (0–86399)
    fuel: float | None    # in the source unit (litres or %); None when invalid
    speed: float          # km/h
    engine: int           # 1 = engine on
    lat: float | None
    lng: float | None


def to_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def bucket_readings(readings: list[Reading], unit: str) -> dict[str, np.ndarray]:
    """Group readings per minute → integer columns ready for encode_columns()."""
    by_minute: dict[int, list[Reading]] = {}
    for reading in sorted(readings, key=lambda r: r.sec):
        minute = reading.sec // 60
        if 0 <= minute < 1440:
            by_minute.setdefault(minute, []).append(reading)
    minutes = sorted(by_minute)
    n = len(minutes)
    fuel, lo, hi = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    speed = np.zeros(n)
    engine = np.zeros(n, dtype="uint8")
    lat, lng = np.full(n, np.nan), np.full(n, np.nan)
    for i, minute in enumerate(minutes):
        group = by_minute[minute]
        values = [r.fuel for r in group if r.fuel is not None and math.isfinite(r.fuel) and r.fuel >= 0]
        if values:
            fuel[i], lo[i], hi[i] = float(np.median(values)), min(values), max(values)
        speed[i] = max((r.speed or 0.0) for r in group)
        engine[i] = 1 if any(r.engine for r in group) else 0
        positioned = [r for r in group if r.lat is not None and r.lng is not None]
        if positioned:
            lat[i], lng[i] = positioned[-1].lat, positioned[-1].lng
    return {
        "m": np.array(minutes, dtype="uint16"),
        "fuel": fuel_to_int(fuel, unit),
        "fuel_lo": fuel_to_int(lo, unit),
        "fuel_hi": fuel_to_int(hi, unit),
        "speed": np.clip(np.rint(speed), 0, 255).astype("uint8"),
        "engine": engine,
        "lat": deg_to_int(lat),
        "lng": deg_to_int(lng),
    }


def path_km(lat_int, lng_int) -> float:
    """Distance over consecutive positions (haversine), ignoring missing points and jumps > 5 km."""
    lat_int, lng_int = np.asarray(lat_int), np.asarray(lng_int)
    ok = (lat_int != 0) & (lng_int != 0)
    lat = np.radians(int_to_deg(lat_int[ok]))
    lng = np.radians(int_to_deg(lng_int[ok]))
    if len(lat) < 2:
        return 0.0
    a = np.sin(np.diff(lat) / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lng) / 2) ** 2
    steps = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return float(steps[steps <= MAX_JUMP_KM].sum())


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def coverage(cols: dict[str, np.ndarray], points: int, offline: bool = False) -> dict:
    n = len(cols["m"])
    if n == 0:
        return {"points": 0, "minutes": 0, "fuel_valid_share": 0.0, "max_gap_min": 1440,
                "first": None, "last": None, "moved_km": 0.0,
                "status": "offline" if offline else "no_data"}
    minutes = cols["m"].astype(int)
    valid = cols["fuel"] >= 0
    moved = round(path_km(cols["lat"], cols["lng"]), 1)
    if not valid.any():
        status = "no_sensor"
    else:
        on = valid & (cols["engine"] == 1)
        flat = bool(on.any()) and np.unique(cols["fuel"][on]).size == 1
        status = "stuck" if flat and moved >= STUCK_MIN_KM else "ok"
    return {
        "points": int(points),
        "minutes": int(n),
        "fuel_valid_share": round(float(valid.mean()), 3),
        "max_gap_min": int(np.diff(np.concatenate(([0], minutes, [1439]))).max()),
        "first": _hhmm(int(minutes[0])),
        "last": _hhmm(int(minutes[-1])),
        "moved_km": moved,
        "status": status,
    }


def thai_midnight_utc(day: date) -> datetime:
    """00:00 Thai time as a naive UTC datetime (pymongo stores naive datetimes as UTC)."""
    return datetime.combine(day, time(0, 0), tzinfo=TH_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def series_id(plate: str, day: date, source: str) -> str:
    return f"{plate}|{day.isoformat()}|{source}"


def build_series_doc(*, plate: str, truck_code: str | None, day: date, source: str, unit: str,
                     tank_l: float, tank_from: str, readings: list[Reading],
                     offline: bool = False, now: datetime | None = None) -> dict:
    cols = bucket_readings(readings, unit)
    n = int(len(cols["m"]))
    doc = {
        "_id": series_id(plate, day, source),
        "plate": plate,
        "truck_code": truck_code,
        "date_key": day.isoformat(),
        "date": thai_midnight_utc(day),
        "source": source,
        "fuel_unit": unit,
        "tank_l": float(tank_l),
        "tank_from": tank_from,
        "enc": ENC_VERSION,
        "n": n,
        "coverage": coverage(cols, points=len(readings), offline=offline),
        "ingested_at": now or datetime.now(timezone.utc).replace(tzinfo=None),
    }
    if n:
        doc["cols"] = encode_columns(cols)
    return doc
