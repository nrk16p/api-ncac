from datetime import datetime

from events import (audit_pick, daily_summary, event_id, merge_sources, minute_utc, plan_rerun, raw_event,
                    score_event)
from fuel_settings import DEFAULTS
from test_model import toy_model
from test_rules import ev

NOW = datetime(2026, 10, 6, 21, 30)
KEY = "2026-10-05"


def raw(source="terminus", plate="สบ.71-0001", status="ok", **features):
    base = ev(**{"source": source, "start_min": 130, "end_min": 145, "lat": 13.7, "lng": 100.5, **features})
    return raw_event(plate, "ME001", KEY, status, base)


def test_ids_and_times():
    assert event_id("สบ.71-8635", KEY, 130) == "สบ.71-8635|2026-10-05T02:10"
    assert minute_utc(KEY, 130) == datetime(2026, 10, 4, 19, 10)


def test_merge_two_boxes_into_one():
    merged = merge_sources([raw("terminus", litres=30.0), raw("besttech", litres=28.0, start_min=132, end_min=150)], 30)
    assert len(merged) == 1
    e = merged[0]
    assert e["sources"] == ["besttech", "terminus"] and e["features"]["both_boxes"]
    assert e["features"]["litres"] == 30.0 and (e["features"]["start_min"], e["features"]["end_min"]) == (130, 150)


def test_same_box_or_other_direction_stays_apart():
    assert len(merge_sources([raw("terminus"), raw("terminus", start_min=140, end_min=150)], 30)) == 2
    assert len(merge_sources([raw("terminus"), raw("besttech", kind="refuel", stays_up_30=True)], 30)) == 2


def test_score_v1_suspected_loss_is_open():
    e = score_event(raw(), DEFAULTS, None, "สมชาย", NOW)
    assert (e["class"], e["status"], e["suggestion"], e["scorer"]) == ("suspected_loss", "open", "real_loss", "rules-v1")
    assert e["score"] == 100 and e["p_real_loss"] == 1.0 and e["confidence"] == 1.0
    assert e["_id"] == "สบ.71-0001|2026-10-05T02:10" and e["driver"] == "สมชาย"
    assert e["action"] == "เทียบใบเติมน้ำมัน + สอบถามคนขับ" and e["decision"] is None and not e["stale"]
    assert e["start"] == datetime(2026, 10, 4, 19, 10) and e["place"] == {"name": None, "lat": 13.7, "lng": 100.5}


def test_vehicle_master_fields_are_top_level():
    e = score_event(raw(), DEFAULTS, None, None, NOW, vehicle={"fleet": "Asia", "branch": "ลาดกระบัง", "plant": "พะเยา"})
    assert (e["fleet"], e["branch"], e["plant"]) == ("Asia", "ลาดกระบัง", "พะเยา")
    bare = score_event(raw(), DEFAULTS, None, None, NOW)
    assert (bare["fleet"], bare["branch"], bare["plant"]) == (None, None, None)


def test_score_v1_clear_consumption_auto_closes_or_audits():
    e = score_event(raw(excess_over_burn_l=1.0), DEFAULTS, None, None, NOW)
    assert e["class"] == "consumption" and e["suggestion"] == "legit" and e["confidence"] == 0.95
    assert e["status"] == ("audit" if audit_pick(e["_id"], 0.05) else "auto_closed")


def test_score_v1_unclear_noise_goes_to_queue():
    e = score_event(raw(recovered_30=True), DEFAULTS, None, None, NOW)
    assert (e["class"], e["status"], e["suggestion"], e["confidence"]) == ("noise", "open", "noise", 0.7)


def test_audit_pick_is_deterministic_and_about_the_rate():
    ids = [f"สบ.71-{i:04d}|{KEY}T02:10" for i in range(2000)]
    picked = [i for i in ids if audit_pick(i, 0.05)]
    assert picked == [i for i in ids if audit_pick(i, 0.05)]
    assert 60 <= len(picked) <= 140 and not audit_pick(ids[0], 0)


def test_score_v2_uses_model_and_never_auto_closes_a_suggested_loss():
    model = toy_model(engine_off_share=5.0)
    e = score_event(raw(excess_over_burn_l=1.0), DEFAULTS, model, None, NOW)   # rules say consumption
    assert e["scorer"] == "lr-test" and e["suggestion"] == "real_loss" and e["status"] == "open"
    calm = score_event(raw(excess_over_burn_l=1.0, engine_off_share=0.0, recovered_120=True), DEFAULTS,
                       toy_model(engine_off_share=5.0, recovered_120=-4.0), None, NOW)   # p ≈ 0.018
    assert calm["suggestion"] == "legit" and calm["confidence"] >= 0.95 and calm["status"] in ("auto_closed", "audit")
    doubted = score_event(raw(night=True), DEFAULTS, toy_model(night=-6.0), None, NOW)
    assert doubted["class"] == "suspected_loss" and doubted["suggestion"] == "noise" and doubted["status"] == "open"


def test_plan_rerun_keeps_decisions():
    old_decided = score_event(raw(), DEFAULTS, None, None, NOW) | {"status": "decided", "decision": "real_loss",
                                                                  "review_id": "r1", "_id": "สบ.71-0001|2026-10-05T02:09"}
    old_open = score_event(raw(start_min=600, end_min=610), DEFAULTS, None, None, NOW)
    gone_decided = score_event(raw(start_min=900, end_min=910), DEFAULTS, None, None, NOW) | {"status": "decided", "decision": "noise"}
    new = [score_event(raw(), DEFAULTS, None, None, NOW)]
    upserts, stale, delete = plan_rerun(new, [old_decided, old_open, gone_decided])
    assert upserts[0]["_id"] == old_decided["_id"] and upserts[0]["status"] == "decided"
    assert upserts[0]["decision"] == "real_loss" and upserts[0]["review_id"] == "r1"
    assert stale == [gone_decided["_id"]] and delete == [old_open["_id"]]


def series_doc(plate, source, status, share=1.0):
    return {"plate": plate, "source": source, "coverage": {"status": status, "fuel_valid_share": share}}


def test_daily_summary():
    docs = [series_doc("A", "terminus", "ok"), series_doc("A", "besttech", "offline"),
            series_doc("B", "terminus", "ok", share=0.05), series_doc("C", "terminus", "no_data")]
    loss = score_event(raw(plate="A"), DEFAULTS, None, None, NOW)
    small = score_event(raw(plate="A", start_min=700, end_min=710, litres=10.0), DEFAULTS, None, None, NOW)
    closed = score_event(raw(plate="A", start_min=800, end_min=810, excess_over_burn_l=1.0), DEFAULTS, None, None, NOW)
    s = daily_summary(KEY, docs, [loss, small, closed], DEFAULTS, NOW)
    assert s["_id"] == KEY and s["trucks_expected"] == 3 and s["trucks_analysed"] == 1
    assert s["by_status"] == {"ok": 1, "stuck": 0, "no_sensor": 0, "offline": 0, "no_data": 1, "sparse": 1}
    assert s["events"] == 3 and s["open"] == 2 and s["auto_closed"] + s["audit"] == 1
    assert s["likely_litres"] == 40.0 and s["check_first"] == [loss["_id"], small["_id"]]
    assert s["sources_missing"] == [] and s["ai_text"] is None
    assert daily_summary(KEY, docs[2:], [], DEFAULTS, NOW)["sources_missing"] == ["besttech"]


def test_broken_model_falls_back_to_rules():
    broken = {"version": "lr-old", "coef": [1.0], "intercept": 0.0, "scaler_mean": [0.0], "scaler_scale": [1.0]}
    e = score_event(raw(), DEFAULTS, broken, None, NOW)
    assert e["scorer"] == "rules-v1" and e["class"] == "suspected_loss" and e["score"] == 100


def test_place_drop_stays_open_with_a_noise_suggestion():
    e = score_event(raw(at_place=True, place_name="ACON A109"), DEFAULTS, None, None, NOW)
    assert (e["class"], e["status"], e["suggestion"], e["confidence"]) == ("place_drop", "open", "noise", 0.7)
    assert e["score"] == 95 and e["p_real_loss"] == 0.95 and e["reasons"][0] == "อยู่ที่ ACON A109"
    calm = score_event(raw(at_place=True, place_name="ACON A109"), DEFAULTS, toy_model(engine_off_share=-6.0), None, NOW)
    assert calm["suggestion"] == "noise" and calm["confidence"] >= 0.95 and calm["status"] == "open"   # never auto-closed


def test_failed_source_reaches_the_summary_even_with_docs():
    docs = [series_doc("A", "terminus", "ok"), series_doc("A", "besttech", "ok")]
    s = daily_summary(KEY, docs, [], DEFAULTS, NOW, sources_failed=["terminus"])
    assert s["sources_missing"] == ["terminus"] and s["sources_failed"] == ["terminus"]
    clean = daily_summary(KEY, docs, [], DEFAULTS, NOW)
    assert clean["sources_missing"] == [] and clean["sources_failed"] == []
