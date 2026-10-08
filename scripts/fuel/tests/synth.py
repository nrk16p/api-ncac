"""Synthetic truck-days for the detection tests (one reading per minute, litres)."""
from datetime import date

from detect import day_series
from series_build import Reading, build_series_doc

DAY = date(2026, 10, 5)
HOME = (13.70, 100.50)
BASELINE = {"idle_lph": 3.0, "l_per_km": 0.4, "from": "truck"}


def parked(m0, m1, fuel, engine=0, pos=HOME):
    return [(m, fuel(m) if callable(fuel) else fuel, 0.0, engine, pos[0], pos[1]) for m in range(m0, m1)]


def driving(m0, m1, fuel, km_per_min=0.75, start=HOME, engine=1, speed=45.0):
    """Due north at km_per_min; returns the points and the final position."""
    pts = [(m, fuel(m) if callable(fuel) else fuel, speed, engine,
            start[0] + (m - m0) * km_per_min / 111.2, start[1]) for m in range(m0, m1)]
    return pts, (start[0] + (m1 - m0) * km_per_min / 111.2, start[1])


def series(points, unit="dl", tank=200.0, plate="สบ.71-0001", source="terminus"):
    readings = [Reading(sec=m * 60, fuel=f, speed=s, engine=e, lat=la, lng=lo) for m, f, s, e, la, lo in points]
    doc = build_series_doc(plate=plate, truck_code=None, day=DAY, source=source, unit=unit, tank_l=tank,
                           tank_from="default", readings=readings)
    return doc, day_series(doc)


def ramp(m0, m1, v0, v1):
    return lambda m: v0 + (v1 - v0) * (m - m0) / (m1 - m0)


def siphon_day():
    """Parked, engine off, all night; 30 L leave between 02:10 and 02:25 and never come back."""
    return parked(0, 130, 150.0) + parked(130, 145, ramp(130, 145, 150.0, 120.0)) + parked(145, 360, 120.0)


def gap_day():
    """Box silent 02:00–02:40; the level is 25 L lower when it comes back."""
    return parked(0, 120, 150.0) + parked(160, 300, 125.0)


def refuel_day():
    return parked(0, 60, 80.0, engine=1) + parked(60, 65, ramp(60, 65, 80.0, 160.0), engine=1) + parked(65, 240, 160.0)


def slosh_day():
    """Readings swing ±16 L (8 % of tank) while driving; parked levels only 3 L apart."""
    pts, pos = driving(30, 90, lambda m: 150.0 - 0.05 * (m - 30) + (16.0 if m % 2 else -16.0))
    return parked(0, 30, 150.0, engine=1) + pts + parked(90, 120, 147.0, engine=1, pos=pos)


def noise_day():
    """Parked dip of 20 L before a short drive; the next parked level (20 min later) is back at 149 L."""
    pts, pos = driving(80, 90, 130.0)
    return (parked(0, 60, 150.0) + parked(60, 70, ramp(60, 70, 150.0, 130.0)) + parked(70, 80, 130.0) + pts
            + parked(90, 180, 149.0, pos=pos))


def consumption_day():
    """30 km drive burning 12 L — exactly the 0.4 L/km baseline."""
    pts, pos = driving(30, 70, ramp(30, 70, 150.0, 138.0))
    return parked(0, 30, 150.0, engine=1) + pts + parked(70, 100, 138.0, engine=1, pos=pos)


def rates_day():
    """2 h idling burning 6 L (3 L/h), a 30 km drive burning 12 L (0.4 L/km), 1 h parked engine on."""
    pts, pos = driving(120, 160, ramp(120, 160, 144.0, 132.0))
    return parked(0, 120, ramp(0, 120, 150.0, 144.0), engine=1) + pts + parked(160, 220, 132.0, engine=1, pos=pos)
