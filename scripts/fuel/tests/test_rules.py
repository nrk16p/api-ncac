from fuel_settings import DEFAULTS
from rules import ACTION_CHECK, ACTION_ESCALATE, ACTION_GAP, ACTION_SENSOR, action_for, classify, is_clear, rule_reasons, score_v1


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
