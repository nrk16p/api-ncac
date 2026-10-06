"""Assemble fuel_events for one day (spec §4.2, §4.5–§4.8) — pure functions, no I/O.

raw candidates per source → merge sources (both_boxes) → class + score (rules v1 or model v2)
→ suggestion / reasons / action → status (open / auto_closed / audit) → re-run plan against the
events already stored → daily summary.
"""
import hashlib
from datetime import date, datetime, timedelta

from model import model_reasons, predict
from rules import (LOSS_CLASSES, RULE_CONF_CLEAR, RULE_CONF_OTHER, SCORER_V1, SUGGESTION, action_for,
                   classify, is_clear, rule_reasons, score_v1)
from series_build import thai_midnight_utc

STATUS_ORDER = ["ok", "stuck", "no_sensor", "offline", "no_data"]   # best first, when two sources disagree
OVERLAP_TOL_MIN = 5


def event_id(plate: str, date_key: str, start_min: int) -> str:
    return f"{plate}|{date_key}T{start_min // 60:02d}:{start_min % 60:02d}"


def minute_utc(date_key: str, minute: int) -> datetime:
    return thai_midnight_utc(date.fromisoformat(date_key)) + timedelta(minutes=minute)


def raw_event(plate: str, truck_code: str | None, date_key: str, day_status: str, ev: dict) -> dict:
    return {"plate": plate, "truck_code": truck_code, "date_key": date_key, "day_status": day_status,
            "sources": [ev["source"]], "features": {**ev, "both_boxes": False}}


def _direction(e: dict) -> str:
    return "up" if e["features"]["kind"] == "refuel" else "down"


def merge_sources(raw: list[dict], merge_min: int) -> list[dict]:
    """Same plate, same direction, overlapping or closer than merge_min, different boxes → one event."""
    out: list[dict] = []
    for e in sorted(raw, key=lambda e: (e["plate"], e["features"]["start_min"])):
        match = next((o for o in out if o["plate"] == e["plate"] and _direction(o) == _direction(e)
                      and e["sources"][0] not in o["sources"]
                      and e["features"]["start_min"] - o["features"]["end_min"] < merge_min
                      and o["features"]["start_min"] - e["features"]["end_min"] < merge_min), None)
        if match is None:
            out.append(e)
            continue
        primary, other = (match, e) if match["features"]["litres"] >= e["features"]["litres"] else (e, match)
        merged = {**primary, "sources": sorted(set(match["sources"]) | set(e["sources"])),
                  "features": {**primary["features"], "both_boxes": True,
                               "start_min": min(match["features"]["start_min"], e["features"]["start_min"]),
                               "end_min": max(match["features"]["end_min"], e["features"]["end_min"])}}
        out[out.index(match)] = merged
    return out


def audit_pick(_id: str, rate: float) -> bool:
    """Deterministic 1-in-(1/rate) sample, so a re-run picks the same events."""
    if rate <= 0:
        return False
    return int(hashlib.sha1(_id.encode("utf-8")).hexdigest()[:8], 16) % max(1, round(1 / rate)) == 0


def score_event(raw: dict, settings: dict, model: dict | None, driver: str | None, now: datetime,
                vehicle: dict | None = None) -> dict:
    """`vehicle` = {"fleet", "branch", "plant"} from the vehicle master (top-level fields Part 3 filters on)."""
    ev = raw["features"]
    cls = classify(ev, settings, raw["day_status"])
    if model:
        try:
            p, contributions = predict(model, ev)
        except (KeyError, TypeError, ValueError):
            model = None   # broken or out-of-date model → rules v1 (spec §6)
    if model:
        towards_loss = p >= 0.5
        suggestion = "real_loss" if towards_loss else ("noise" if cls in LOSS_CLASSES else SUGGESTION[cls])
        confidence = p if towards_loss else 1 - p
        reasons = model_reasons(contributions, ev, towards_loss)
        scorer = model["version"]
        clear = cls not in LOSS_CLASSES and not towards_loss and confidence >= settings["auto_close_conf"]
        score = round(p * 100)
    else:
        score = score_v1(cls, ev, settings)
        p = score / 100
        suggestion = SUGGESTION[cls]
        clear = is_clear(cls, ev, settings)
        confidence = p if cls in LOSS_CLASSES else (RULE_CONF_CLEAR if clear else RULE_CONF_OTHER)
        reasons = rule_reasons(cls, ev)
        scorer = SCORER_V1
    _id = event_id(raw["plate"], raw["date_key"], ev["start_min"])
    if cls in LOSS_CLASSES or suggestion == "real_loss" or not clear:
        status = "open"
    else:
        status = "audit" if audit_pick(_id, settings["audit_rate"]) else "auto_closed"
    repeat = max(ev.get("truck_confirmed_30d", 0), ev.get("driver_confirmed_30d", 0)) >= 2
    vehicle = vehicle or {}
    return {
        "_id": _id, "plate": raw["plate"], "truck_code": raw["truck_code"], "driver": driver,
        "fleet": vehicle.get("fleet"), "branch": vehicle.get("branch"), "plant": vehicle.get("plant"),
        "date_key": raw["date_key"],
        "start": minute_utc(raw["date_key"], ev["start_min"]), "end": minute_utc(raw["date_key"], ev["end_min"]),
        "sources": raw["sources"], "kind": ev["kind"], "class": cls,
        "litres": ev["litres"], "pct_tank": ev["pct_tank"],
        "score": score, "p_real_loss": round(p, 4), "suggestion": suggestion, "confidence": round(confidence, 4),
        "reasons": reasons, "action": action_for(cls, ev, repeat), "features": ev,
        "place": {"name": ev["place_name"], "lat": ev["lat"], "lng": ev["lng"]},
        "status": status, "decision": None, "review_id": None, "scorer": scorer, "stale": False,
        "created_at": now, "updated_at": now,
    }


def _overlaps(a: dict, b: dict) -> bool:
    tol = timedelta(minutes=OVERLAP_TOL_MIN)
    return a["plate"] == b["plate"] and a["start"] <= b["end"] + tol and b["start"] <= a["end"] + tol


def plan_rerun(new_events: list[dict], existing: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """Re-running a day: decided events keep their _id and decision when a new event overlaps them
    (matched by plate + window, not _id — a start can shift by a minute); decided events no longer
    found are marked stale; every other stored event of the day is replaced.
    Returns (documents to write, _ids to mark stale, _ids to delete)."""
    decided = [e for e in existing if e["status"] == "decided"]
    upserts: list[dict] = []
    matched: set[str] = set()
    for event in new_events:
        match = next((d for d in decided if d["_id"] not in matched and _overlaps(d, event)), None)
        if match:
            matched.add(match["_id"])
            event = {**event, "_id": match["_id"], "status": "decided", "decision": match.get("decision"),
                     "review_id": match.get("review_id"), "created_at": match.get("created_at", event["created_at"])}
        upserts.append(event)
    written = {e["_id"] for e in upserts}
    stale = [d["_id"] for d in decided if d["_id"] not in matched]
    delete = [e["_id"] for e in existing if e["status"] != "decided" and e["_id"] not in written]
    return upserts, stale, delete


def plate_statuses(series_docs: list[dict], sparse_share: float) -> dict[str, str]:
    """One status per plate: the best of its sources; 'sparse' when readings exist but too few are valid."""
    out: dict[str, str] = {}
    for doc in series_docs:
        cov = doc.get("coverage") or {}
        status = cov.get("status", "no_data")
        if status == "ok" and cov.get("fuel_valid_share", 0) < sparse_share:
            status = "sparse"
        current = out.get(doc["plate"])
        order = STATUS_ORDER + ["sparse"]
        if current is None or order.index(status) < order.index(current):
            out[doc["plate"]] = status
    return out


def daily_summary(date_key: str, series_docs: list[dict], events: list[dict], settings: dict, now: datetime) -> dict:
    statuses = plate_statuses(series_docs, settings["sparse_share"])
    by_status = {s: 0 for s in STATUS_ORDER + ["sparse"]}
    for status in statuses.values():
        by_status[status] += 1
    open_loss = [e for e in events if e["status"] in ("open", "audit") and e["suggestion"] == "real_loss"]
    ranked = sorted(open_loss, key=lambda e: e["p_real_loss"] * e["litres"], reverse=True)
    sources = {d["source"] for d in series_docs}
    return {
        "_id": date_key,
        "trucks_expected": len(statuses),
        "trucks_analysed": sum(1 for s in statuses.values() if s in ("ok", "stuck")),
        "by_status": by_status,
        "events": len(events),
        "auto_closed": sum(1 for e in events if e["status"] == "auto_closed"),
        "open": sum(1 for e in events if e["status"] == "open"),
        "audit": sum(1 for e in events if e["status"] == "audit"),
        "decided": sum(1 for e in events if e["status"] == "decided"),
        "likely_litres": round(sum(e["p_real_loss"] * e["litres"] for e in open_loss), 1),
        "check_first": [e["_id"] for e in ranked[:3]],
        "sources_missing": [s for s in ("besttech", "terminus") if s not in sources],
        "ai_text": None,
        "updated_at": now,
    }
