"""Known places for the "at a place" evidence (spec §4.3) — pure functions.

analytics.fuel_places documents: {_id, kind: "plant" | "poi", code, name, lat, lng, radius_m, polygon}
  plant  atms.plants (the coordinates engine-on uses), a 300 m circle
  poi    Besttech /location: polygon geofence when given, else its radius, else a 300 m circle
"""
import math

PLANT_RADIUS_M = 300.0
EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (lat1, lng1, lat2, lng2))
    a = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(max(a, 0.0), 1.0)))


def point_in_polygon(lat: float, lng: float, polygon: list[dict]) -> bool:
    """Ray casting on (lng, lat); polygon = [{"lat", "lng"}, ...] with at least 3 points."""
    inside = False
    n = len(polygon)
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        if (a["lat"] > lat) != (b["lat"] > lat):
            x = a["lng"] + (lat - a["lat"]) * (b["lng"] - a["lng"]) / (b["lat"] - a["lat"])
            if lng < x:
                inside = not inside
    return inside


def find_place(lat: float | None, lng: float | None, places: list[dict]) -> dict | None:
    """The nearest known place containing the point, or None."""
    if lat is None or lng is None or math.isnan(lat) or math.isnan(lng):
        return None
    best, best_d = None, float("inf")
    for place in places:
        d = haversine_m(lat, lng, place["lat"], place["lng"])
        polygon = place.get("polygon") or []
        inside = point_in_polygon(lat, lng, polygon) if len(polygon) >= 3 else d <= place.get("radius_m", PLANT_RADIUS_M)
        if inside and d < best_d:
            best, best_d = place, d
    return best


def _float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def plant_places(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        lat, lng = _float(row.get("Latitude")), _float(row.get("Longitude"))
        code = str(row.get("plant_code") or "").strip()
        if lat is None or lng is None or not code:
            continue
        name = f"{row.get('client') or ''} {code}".strip()
        out.append({"_id": f"plant:{code}", "kind": "plant", "code": code, "name": name,
                    "lat": lat, "lng": lng, "radius_m": PLANT_RADIUS_M, "polygon": []})
    return out


def besttech_places(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        lat, lng = _float(row.get("lat")), _float(row.get("lng"))
        code = str(row.get("code") or "").strip()
        if row.get("status", "A") != "A" or lat is None or lng is None or not code:
            continue
        polygon = [{"lat": float(p["lat"]), "lng": float(p["lng"])} for p in row.get("geofence") or []
                   if _float(p.get("lat")) is not None and _float(p.get("lng")) is not None]
        radius = _float(row.get("radius")) or 0.0
        out.append({"_id": f"besttech:{code}", "kind": "poi", "code": code, "name": row.get("name") or code,
                    "lat": lat, "lng": lng, "polygon": polygon if len(polygon) >= 3 else [],
                    "radius_m": radius if radius > 0 else PLANT_RADIUS_M})
    return out
