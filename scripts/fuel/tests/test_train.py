import random
from datetime import date, datetime, timedelta, timezone

import pytest

from fuel_settings import DEFAULTS
from test_rules import ev
from train import build_dataset, precision_recall_at_k, split_by_day, train

NOW = datetime(2026, 11, 2, 20, 30)


def event(_id, day, start_h=2, plate="สบ.71-0001", status="open", decision=None, **features):
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=start_h) - timedelta(hours=7)
    return {"_id": _id, "plate": plate, "date_key": day.isoformat(), "start": start, "end": start + timedelta(minutes=15),
            "kind": "drop", "class": "suspected_loss", "status": status, "decision": decision, "features": ev(**features)}


def ms(day, hour):
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone(timedelta(hours=7))).timestamp() * 1000 + hour * 3600_000


def test_build_dataset_queue_and_weak_labels():
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    events = [event("q1", d1, status="decided", decision="real_loss"),
              event("q2", d1, start_h=5, status="decided", decision="follow_up"),
              event("w1", d2, start_h=2, excess_over_burn_l=10.0), event("w2", d2, start_h=4, excess_over_burn_l=25.0),
              event("w3", d2, start_h=20, plate="สบ.71-0002")]
    reviews = [{"plate": "71-0001", "decision": "reviewed_suspicious", "start_ts": ms(d2, 0), "end_ts": ms(d2, 12)},
               {"plate": "71-0002", "decision": "reviewed_ok", "start_ts": ms(d2, 0), "end_ts": ms(d2, 23)},
               {"plate": "71-0001", "decision": "real_loss", "event_id": "q1", "start_ts": ms(d1, 0), "end_ts": ms(d1, 3)}]
    rows = {r["event"]["_id"]: (r["y"], r["w"]) for r in build_dataset(events, reviews)}
    assert rows == {"q1": (1, 1.0), "w2": (1, 0.5), "w3": (0, 0.5)}


def test_split_and_metrics():
    rows = [{"event": {"date_key": f"2026-10-{d:02d}"}, "y": y, "w": 1.0}
            for d in range(1, 11) for y in (1, 0)]
    train_rows, test_rows = split_by_day(rows)
    assert {r["event"]["date_key"] for r in test_rows} == {"2026-10-08", "2026-10-09", "2026-10-10"}
    assert len(train_rows) == 14
    p, r = precision_recall_at_k(test_rows, [1.0 if row["y"] else 0.0 for row in test_rows], k=1)
    assert (p, r) == (1.0, 1.0)
    p, r = precision_recall_at_k(test_rows, [0.0 if row["y"] else 1.0 for row in test_rows], k=1)
    assert (p, r) == (0.0, 0.0)


def test_train_skips_without_enough_labels():
    rows = [{"event": event(f"e{i}", date(2026, 10, 1)), "y": i % 2, "w": 1.0} for i in range(20)]
    assert train(rows, None, DEFAULTS, NOW)["status"] == "skipped"


def test_train_promotes_a_model_that_beats_the_rules():
    pytest.importorskip("sklearn")
    rng = random.Random(7)
    rows = []
    for d in range(40):
        day = date(2026, 9, 1) + timedelta(days=d)
        for i in range(25):          # more events than the top 20, so ranking matters
            loss = i < 3
            # the rules ignore repeat offenders; the label follows them, other evidence is random
            features = {"truck_confirmed_30d": 3 if loss else 0, "engine_off_share": rng.random(),
                        "night": rng.random() < 0.5, "rate_l_per_min": rng.uniform(0.2, 3.0)}
            rows.append({"event": event(f"{day}-{i}", day, start_h=i, **features), "y": int(loss), "w": 1.0})
    result = train(rows, None, DEFAULTS, NOW)
    assert result["status"] == "promoted"
    assert result["metrics"]["precision_at_20"] > result["metrics"]["active_precision_at_20"]
    model = result["model"]
    assert model["_id"] == "lr-2026-11-02" and model["active"] and len(model["coef"]) == len(model["features"])
