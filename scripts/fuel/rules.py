"""Rules v1 (spec §4.2, §4.4): class, score, clear flag, suggestion, reasons and action in Thai."""

LOSS_CLASSES = ("suspected_loss", "gap_loss")
SUGGESTION = {"suspected_loss": "real_loss", "gap_loss": "real_loss", "noise": "noise",
              "sensor_fault": "noise", "consumption": "legit", "refuel": "legit"}
SCORER_V1 = "rules-v1"
# v1 has no probability for non-loss events; these confidences only drive the card's wording
RULE_CONF_CLEAR, RULE_CONF_OTHER = 0.95, 0.7
# a drop no bigger than this × the sensor's unexplained rises that day is noise; the tunable value is
# fuel_settings "noise_rise_factor" (this constant is its default and the threshold the phrase uses)
NOISE_RISE_FACTOR = 1.5

ACTION_CHECK = "เทียบใบเติมน้ำมัน + สอบถามคนขับ"
ACTION_GAP = "ตรวจกล่อง GPS/สายไฟ ว่าถูกตัดไฟหรือไม่"
ACTION_SENSOR = "แจ้งผู้ให้บริการ GPS ตรวจเซนเซอร์"
ACTION_ESCALATE = "ส่งเรื่องหัวหน้าฟลีท"


def classify(ev: dict, settings: dict, day_status: str) -> str:
    if ev["kind"] == "refuel":
        return "refuel" if ev["stays_up_30"] else "noise"
    if day_status == "stuck" or ev["sensor_noise_parked"] > settings["faulty_sensor_pct"]:
        return "sensor_fault"
    rise_factor = settings.get("noise_rise_factor", NOISE_RISE_FACTOR)
    if ev["recovered_30"] or ev["rebound_60"] or ev["litres"] <= rise_factor * ev["day_rise_l"]:
        return "noise"
    if ev["excess_over_burn_l"] < settings["min_drop_l"]:
        return "consumption"
    if ev["kind"] == "gap" and ev["gap_min"] >= settings["gap_min"] and not ev["recovered_60"]:
        return "gap_loss"
    if not ev["recovered_60"]:
        return "suspected_loss"
    return "noise"


def score_v1(cls: str, ev: dict, settings: dict) -> int:
    if cls not in LOSS_CLASSES:
        return 0
    score = 50
    score += 15 if ev["engine_off_share"] >= 0.8 else 0
    score += 10 if ev["night"] else 0
    score += 10 if not ev["at_place"] else 0
    score += 10 if not ev["recovered_120"] else 0
    score += 10 if ev["rate_l_per_min"] >= 1 else 0
    score += 10 if ev.get("both_boxes") else 0
    score -= 20 if ev["sensor_noise_parked"] > settings["noisy_sensor_pct"] else 0
    return max(0, min(100, score))


def is_clear(cls: str, ev: dict, settings: dict) -> bool:
    """v1 auto-close: a dip that is fully back within 10 min, consumption within 2 L, a refuel that stays."""
    if cls == "noise":
        return bool(ev.get("recovered_10"))
    if cls == "consumption":
        return ev["excess_over_burn_l"] <= settings["clear_consumption_l"]
    if cls == "refuel":
        return bool(ev.get("stays_up_60"))
    return False


def phrases(ev: dict) -> dict[str, str]:
    """Thai evidence phrases keyed by the feature they describe (shared by rules and the model)."""
    out = {}
    if ev["kind"] == "refuel":
        out["litres"] = f"เติมน้ำมัน +{ev['litres']:.0f} L"
    else:
        out["litres"] = f"ลดลง {ev['litres']:.0f} L ({ev['pct_tank']:.0f}% ของถัง)"
    if ev["engine_off_share"] >= 0.8:
        out["engine_off_share"] = "จอดดับเครื่อง"
    if ev.get("recovered_30"):
        out["recovered_30"] = "ระดับกลับขึ้นภายใน 30 นาที"
    elif ev.get("rebound_60"):
        out["rebound_60"] = "ระดับกลับขึ้นเกินครึ่งภายใน 1 ชม."
    elif ev["kind"] != "refuel" and not ev.get("recovered_120"):
        out["recovered_120"] = "ระดับไม่กลับขึ้นหลัง 2 ชม."
    out["at_place"] = f"อยู่ที่ {ev['place_name']}" if ev["at_place"] else "ไม่ได้อยู่ในแพลนท์/อู่"
    if ev["night"]:
        out["night"] = "กลางคืน"
    if ev["rate_l_per_min"] >= 1:
        out["rate_l_per_min"] = f"ลดเร็ว {ev['rate_l_per_min']:.1f} L/นาที"
    if ev.get("both_boxes"):
        out["both_boxes"] = "กล่อง GPS ทั้ง 2 เจ้าเห็นตรงกัน"
    if ev["gap_min"] >= 10:
        out["gap_min"] = f"กล่อง GPS ขาดสัญญาณ {ev['gap_min']} นาที"
    if ev["kind"] != "refuel" and ev["excess_over_burn_l"] < 8:
        out["excess_over_burn_l"] = "ใกล้เคียงอัตราสิ้นเปลืองปกติ"
    if ev["day_rise_l"] > 0 and ev["litres"] <= NOISE_RISE_FACTOR * ev["day_rise_l"]:
        out["day_rise_l"] = f"วันเดียวกันระดับขึ้นเองรวม {ev['day_rise_l']:.0f} L"
    if ev["sensor_noise_parked"] > 3:
        out["sensor_noise_parked"] = "เซนเซอร์แกว่งมาก"
    return out


# which phrases explain each class, most telling first
_REASON_ORDER = {
    "suspected_loss": ["engine_off_share", "recovered_120", "at_place", "night", "rate_l_per_min", "both_boxes", "litres"],
    "gap_loss": ["gap_min", "recovered_120", "at_place", "night", "litres"],
    "noise": ["recovered_30", "rebound_60", "day_rise_l", "sensor_noise_parked", "litres"],
    "consumption": ["excess_over_burn_l", "litres"],
    "refuel": ["litres", "at_place"],
    "sensor_fault": ["sensor_noise_parked", "litres"],
}


def rule_reasons(cls: str, ev: dict) -> list[str]:
    available = phrases(ev)
    out = [available[key] for key in _REASON_ORDER[cls] if key in available]
    if cls == "sensor_fault" and "sensor_noise_parked" not in available:
        out.insert(0, "เซนเซอร์ค่าค้างทั้งวัน")
    return out[:3]


def action_for(cls: str, ev: dict, repeat: bool) -> str | None:
    if cls == "gap_loss":
        action = ACTION_GAP
    elif cls == "suspected_loss":
        action = ACTION_CHECK
    elif cls == "sensor_fault":
        action = ACTION_SENSOR
    else:
        return None
    return f"{action} + {ACTION_ESCALATE}" if repeat and cls in LOSS_CLASSES else action
