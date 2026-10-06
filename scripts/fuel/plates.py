"""Plate normalisation shared by the fuel series jobs (spec §3.1).

Same rule as menatransport/mongodb-gps app/utils/plate.py so gps_series joins gps.distance_*
and ATMS: digits "xx-xxxx" -> "สบ.xx-xxxx". Other non-empty formats (about 15 of 424 Terminus
trucks, e.g. "กว4506", "3ฒภ5383") are kept trimmed instead of dropped.
"""
import re

PLATE_PREFIX = "สบ."
_PLATE_RE = re.compile(r"(\d{2})\s*[-–—]\s*(\d{4})")
_BESTTECH_RE = re.compile(r"^(\S+)\s*\((.*)\)\s*$")


def normalize_plate(raw) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return None
    match = _PLATE_RE.search(text)
    if not match:
        return text
    return f"{PLATE_PREFIX}{match.group(1)}-{match.group(2)}"


def terminus_plate(plate: str) -> str:
    """'สบ.71-8623' -> '71-8623', the form stored in terminus.driving_log."""
    text = (plate or "").strip()
    match = _PLATE_RE.search(text)
    return f"{match.group(1)}-{match.group(2)}" if match else text


def split_besttech_vehicle(raw) -> tuple[str | None, str | None]:
    """'ME152 (71-8635 สบ.)' -> ('ME152', 'สบ.71-8635'); '70-6294 สบ.' -> (None, 'สบ.70-6294')."""
    if not raw:
        return None, None
    text = str(raw).strip()
    match = _BESTTECH_RE.match(text)
    if match:
        return match.group(1), normalize_plate(match.group(2))
    return None, normalize_plate(text)
