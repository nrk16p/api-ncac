import math

import pytest

from model import FEATURES, feature_vector, model_reasons, predict
from test_rules import ev


def toy_model(**coef):
    weights = [coef.get(name, 0.0) for name in FEATURES]
    return {"version": "lr-test", "coef": weights, "intercept": 0.0,
            "scaler_mean": [0.0] * len(FEATURES), "scaler_scale": [1.0] * len(FEATURES)}


def test_feature_vector_order_and_values():
    x = feature_vector(ev(day_rise_l=15.0, truck_confirmed_30d=2, both_boxes=True))
    assert len(x) == len(FEATURES)
    assert x[FEATURES.index("log_litres")] == pytest.approx(math.log(30.0))
    assert x[FEATURES.index("day_rise_ratio")] == 0.5
    assert x[FEATURES.index("both_boxes")] == 1.0 and x[FEATURES.index("truck_confirmed_30d")] == 2.0
    assert x[FEATURES.index("is_gap")] == 0.0 and x[FEATURES.index("is_besttech")] == 0.0


def test_predict_and_contributions():
    model = toy_model(engine_off_share=2.0, recovered_30=-3.0)
    p, contributions = predict(model, ev())
    assert p == pytest.approx(1 / (1 + math.exp(-2.0)))
    assert contributions["engine_off_share"] == 2.0 and contributions["recovered_30"] == 0.0


def test_zero_scale_is_safe():
    model = toy_model(engine_off_share=1.0)
    model["scaler_scale"] = [0.0] * len(FEATURES)
    assert predict(model, ev())[0] == pytest.approx(1 / (1 + math.exp(-1.0)))


def test_reasons_follow_contributions():
    _, contributions = predict(toy_model(engine_off_share=2.0, night=1.0, at_place=-1.0), ev())
    assert model_reasons(contributions, ev(), towards_loss=True) == ["จอดดับเครื่อง", "กลางคืน"]
    _, contributions = predict(toy_model(recovered_30=-2.0), ev(recovered_30=True))
    assert model_reasons(contributions, ev(recovered_30=True), towards_loss=False) == ["ระดับกลับขึ้นภายใน 30 นาที"]


def test_model_that_does_not_match_features_is_rejected():
    with pytest.raises(ValueError):
        predict({"version": "lr-old", "coef": [1.0], "intercept": 0.0, "scaler_mean": [0.0], "scaler_scale": [1.0]}, ev())
    stale = toy_model() | {"features": FEATURES[:-1] + ["something_else"]}
    with pytest.raises(ValueError):
        predict(stale, ev())
