"""Model v2 scorer (spec §4.5) — logistic regression stored as plain numbers; numpy only, so the
nightly job never imports scikit-learn. Training lives in train.py."""
import math

import numpy as np

from rules import phrases

FEATURES = ["log_litres", "pct_tank", "duration_min", "rate_l_per_min", "excess_over_burn_l",
            "recovered_30", "recovered_60", "recovered_120", "rebound_60", "engine_off_share",
            "moving_share", "gap_min", "sensor_noise_parked", "day_rise_ratio", "at_place", "night",
            "both_boxes", "truck_confirmed_30d", "driver_confirmed_30d", "is_besttech", "is_gap"]

# feature → key of rules.phrases() that explains it to a person
PHRASE_KEY = {"log_litres": "litres", "pct_tank": "litres", "day_rise_ratio": "day_rise_l",
              "is_gap": "gap_min", "duration_min": "rate_l_per_min"}


def feature_vector(ev: dict) -> np.ndarray:
    litres = max(float(ev["litres"]), 0.1)
    values = {
        "log_litres": math.log(litres),
        "pct_tank": ev["pct_tank"],
        "duration_min": ev["duration_min"],
        "rate_l_per_min": ev["rate_l_per_min"],
        "excess_over_burn_l": ev["excess_over_burn_l"],
        "recovered_30": ev["recovered_30"],
        "recovered_60": ev["recovered_60"],
        "recovered_120": ev["recovered_120"],
        "rebound_60": ev["rebound_60"],
        "engine_off_share": ev["engine_off_share"],
        "moving_share": ev["moving_share"],
        "gap_min": ev["gap_min"],
        "sensor_noise_parked": ev["sensor_noise_parked"],
        "day_rise_ratio": min(ev["day_rise_l"] / litres, 10.0),
        "at_place": ev["at_place"],
        "night": ev["night"],
        "both_boxes": ev.get("both_boxes", False),
        "truck_confirmed_30d": ev.get("truck_confirmed_30d", 0),
        "driver_confirmed_30d": ev.get("driver_confirmed_30d", 0),
        "is_besttech": ev["source"] == "besttech",
        "is_gap": ev["kind"] == "gap",
    }
    return np.array([float(values[name]) for name in FEATURES])


def predict(model: dict, ev: dict) -> tuple[float, dict[str, float]]:
    """P(real loss) and each feature's contribution (coef × standardized value) to the log-odds.
    Raises ValueError for a model trained on a different feature list (numpy would broadcast it)."""
    if (model.get("features", FEATURES) != FEATURES
            or not len(model["coef"]) == len(model["scaler_mean"]) == len(model["scaler_scale"]) == len(FEATURES)):
        raise ValueError(f"model {model.get('version')} does not match the current features")
    x = feature_vector(ev)
    scale = np.where(np.array(model["scaler_scale"]) == 0, 1.0, np.array(model["scaler_scale"]))
    z = (x - np.array(model["scaler_mean"])) / scale
    contributions = np.array(model["coef"]) * z
    logit = float(contributions.sum() + model["intercept"])
    return 1.0 / (1.0 + math.exp(-logit)), dict(zip(FEATURES, contributions.tolist()))


def model_reasons(contributions: dict[str, float], ev: dict, towards_loss: bool, k: int = 3) -> list[str]:
    """Thai phrases for the features that pushed hardest toward the suggestion."""
    available = phrases(ev)
    ranked = sorted(contributions.items(), key=lambda item: item[1], reverse=towards_loss)
    out: list[str] = []
    for name, value in ranked:
        if (value > 0) != towards_loss or value == 0:
            continue
        phrase = available.get(PHRASE_KEY.get(name, name))
        if phrase and phrase not in out:
            out.append(phrase)
        if len(out) == k:
            break
    return out or [available["litres"]]
