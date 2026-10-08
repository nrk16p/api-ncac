from fuel_settings import DEFAULTS
from rules import (ACTION_CHECK, ACTION_ESCALATE, ACTION_GAP, ACTION_PLACE, ACTION_SENSOR, SUGGESTION, action_for,
                   classify, is_clear, rule_reasons, score_v1)


def ev(**overrides):
    base = {"kind": "drop", "where": "parked", "litres": 30.0, "pct_tank": 15.0, "duration_min": 15,
            "rate_l_per_min": 2.0, "expected_burn_l": 0.0, "excess_over_burn_l": 30.0,
            "recovered_10": False, "recovered_30": False, "recovered_60": False, "recovered_120": False,
            "rebound_60": False, "engine_off_share": 1.0, "moving_share": 0.0, "gap_min": 1,
            "at_place": False, "place_name": None, "night": True, "both_boxes": False,
            "sensor_noise_parked": 0.5, "day_rise_l": 0.0, "source": "terminus"}
    return {**base, **overrides}


def test_classes():
    assert classify(ev(), DEFAULTS, "ok") == "suspected_loss"
    assert classify(ev(kind="gap", where="gap", gap_min=40), DEFAULTS, "ok") == "gap_loss"
    assert classify(ev(), DEFAULTS, "stuck") == "sensor_fault"
    assert classify(ev(sensor_noise_parked=12.0), DEFAULTS, "ok") == "sensor_fault"
    assert classify(ev(recovered_30=True), DEFAULTS, "ok") == "noise"
    assert classify(ev(rebound_60=True), DEFAULTS, "ok") == "noise"
    assert classify(ev(day_rise_l=25.0), DEFAULTS, "ok") == "noise"           # 30 ≤ 1.5 × 25
    assert classify(ev(excess_over_burn_l=4.0), DEFAULTS, "ok") == "consumption"
    assert classify(ev(recovered_60=True), DEFAULTS, "ok") == "noise"
    assert classify(ev(kind="refuel", stays_up_30=True), DEFAULTS, "ok") == "refuel"
    assert classify(ev(kind="refuel", stays_up_30=False), DEFAULTS, "ok") == "noise"


def test_noise_rise_factor_is_a_setting():
    assert DEFAULTS["noise_rise_factor"] == 1.5
    tuned = {**DEFAULTS, "noise_rise_factor": 1.0}
    assert classify(ev(day_rise_l=25.0), tuned, "ok") == "suspected_loss"     # 30 > 1.0 × 25


def test_score_v1():
    assert score_v1("suspected_loss", ev(), DEFAULTS) == 100      # 50+15+10+10+10+10, capped
    assert score_v1("suspected_loss", ev(engine_off_share=0.2, night=False, at_place=True, recovered_120=True,
                                         rate_l_per_min=0.5, sensor_noise_parked=4.0), DEFAULTS) == 30
    assert score_v1("consumption", ev(), DEFAULTS) == 0


def test_clear_cases():
    assert is_clear("noise", ev(recovered_10=True), DEFAULTS)
    assert not is_clear("noise", ev(recovered_30=True), DEFAULTS)
    assert is_clear("consumption", ev(excess_over_burn_l=1.5), DEFAULTS)
    assert not is_clear("consumption", ev(excess_over_burn_l=4.0), DEFAULTS)
    assert is_clear("refuel", ev(kind="refuel", stays_up_60=True), DEFAULTS)
    assert not is_clear("suspected_loss", ev(), DEFAULTS)


def test_reasons_and_actions_in_thai():
    assert rule_reasons("suspected_loss", ev()) == ["จอดดับเครื่อง", "ระดับไม่กลับขึ้นหลัง 2 ชม.", "ไม่ได้อยู่ในแพลนท์/อู่"]
    assert rule_reasons("gap_loss", ev(kind="gap", gap_min=40))[0] == "กล่อง GPS ขาดสัญญาณ 40 นาที"
    assert rule_reasons("noise", ev(recovered_30=True))[0] == "ระดับกลับขึ้นภายใน 30 นาที"
    assert action_for("suspected_loss", ev(), repeat=False) == ACTION_CHECK
    assert action_for("gap_loss", ev(), repeat=True) == f"{ACTION_GAP} + {ACTION_ESCALATE}"
    assert action_for("sensor_fault", ev(), repeat=True) == ACTION_SENSOR
    assert action_for("noise", ev(), repeat=True) is None


def test_strict_suspected_loss():
    """Big (excess ≥ 15 L), away from plants/POIs, still down after 2 h, and engine off or fast."""
    assert classify(ev(engine_off_share=0.9, rate_l_per_min=0.5), DEFAULTS, "ok") == "suspected_loss"
    assert classify(ev(engine_off_share=0.0, rate_l_per_min=1.0), DEFAULTS, "ok") == "suspected_loss"
    assert classify(ev(excess_over_burn_l=14.9), DEFAULTS, "ok") == "consumption"
    assert classify(ev(recovered_120=True), DEFAULTS, "ok") == "noise"                     # back within 2 h
    assert classify(ev(engine_off_share=0.79, rate_l_per_min=0.99), DEFAULTS, "ok") == "consumption"   # slow drain, engine on
    assert classify(ev(kind="gap", where="gap", gap_min=40, engine_off_share=0.0, rate_l_per_min=0.3),
                    DEFAULTS, "ok") == "consumption"


def test_place_drop():
    at_plant = ev(at_place=True, place_name="ACON A109")
    assert classify(at_plant, DEFAULTS, "ok") == "place_drop"
    assert classify({**at_plant, "engine_off_share": 0.0, "rate_l_per_min": 0.2}, DEFAULTS, "ok") == "place_drop"
    assert classify({**at_plant, "recovered_120": True}, DEFAULTS, "ok") == "noise"
    assert classify({**at_plant, "excess_over_burn_l": 10.0}, DEFAULTS, "ok") == "consumption"
    assert classify(ev(kind="gap", where="gap", gap_min=40, at_place=True, place_name="x"), DEFAULTS, "ok") == "place_drop"
    assert SUGGESTION["place_drop"] == "noise" and not is_clear("place_drop", at_plant, DEFAULTS)
    assert score_v1("place_drop", at_plant, DEFAULTS) == 95      # normal score, without the 'away from places' 10
    assert rule_reasons("place_drop", at_plant)[0] == "อยู่ที่ ACON A109"
    assert action_for("place_drop", at_plant, repeat=True) == ACTION_PLACE


def test_calibration_thresholds_are_settings():
    tuned = {**DEFAULTS, "min_excess_l": 40.0, "min_engine_off_share": 0.5, "min_rate_l_per_min": 3.0, "persist_min": 60}
    assert classify(ev(), tuned, "ok") == "consumption"                                    # 30 < 40
    big = {"excess_over_burn_l": 50.0}
    assert classify(ev(**big, engine_off_share=0.6, rate_l_per_min=0.1), tuned, "ok") == "suspected_loss"
    assert classify(ev(**big, engine_off_share=0.4, rate_l_per_min=2.0), tuned, "ok") == "consumption"
    assert classify(ev(**big, recovered_60=True), tuned, "ok") == "noise"
    assert classify(ev(**big, recovered_120=True), tuned, "ok") == "suspected_loss"         # back only after 60 min


def test_change_seen_only_while_moving_is_noise():
    """spec §4.4: a change that appears only in moving minutes is noise (slosh)."""
    assert DEFAULTS["moving_noise_share"] == 0.8
    assert classify(ev(moving_share=0.8), DEFAULTS, "ok") == "noise"
    assert classify(ev(kind="gap", where="gap", gap_min=40, moving_share=0.9), DEFAULTS, "ok") == "noise"
    assert classify(ev(moving_share=0.79), DEFAULTS, "ok") == "suspected_loss"
    assert classify(ev(kind="refuel", stays_up_30=True, moving_share=1.0), DEFAULTS, "ok") == "refuel"
    assert rule_reasons("noise", ev(moving_share=0.9))[0] == "ระดับเปลี่ยนเฉพาะตอนรถวิ่ง"
    # a drop between two parked stretches is measured on parked levels: a drive's steady fall stays consumption
    assert classify(ev(where="moving", moving_share=1.0, excess_over_burn_l=1.0), DEFAULTS, "ok") == "consumption"
