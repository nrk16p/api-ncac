from datetime import date

import pipeline_fuel_train as pft
from fake_mongo import FakeClient
from pipeline_fuel_train import labelled_events
from test_train import event, ms


def test_labelled_events_reads_only_decided_and_review_truck_days():
    db = FakeClient()["analytics"]
    d1 = date(2026, 10, 1)
    for e in [event("dec", d1, status="decided", decision="real_loss"),
              event("open-same-day", d1, start_h=6),                 # plate + day of an old review → read
              event("other-plate", d1, plate="สบ.71-0002"),          # its review is already an event decision
              event("other-day", date(2026, 10, 3)),                 # no review that day
              event("too-old", date(2026, 8, 1), status="decided", decision="noise")]:
        db["fuel_events"].replace_one({"_id": e["_id"]}, e)
    reviews = [{"plate": "71-0001", "decision": "reviewed_ok", "start_ts": ms(d1, 0), "end_ts": ms(d1, 12)},
               {"plate": "71-0002", "decision": "real_loss", "event_id": "x", "start_ts": ms(d1, 0), "end_ts": ms(d1, 12)}]
    got = sorted(e["_id"] for e in labelled_events(db, date(2026, 9, 1), reviews))
    assert got == ["dec", "open-same-day"]


class FakeJob:
    finished: list = []

    def __init__(self, job_type, pipeline, meta=None):
        pass

    def finish(self, status="success", **extra):
        FakeJob.finished.append((status, extra))


def wire_train(monkeypatch):
    client = FakeClient()
    FakeJob.finished = []
    monkeypatch.setattr(pft, "MongoClient", lambda uri: client)
    monkeypatch.setattr(pft, "JobLog", FakeJob)
    return client


def test_main_logs_the_training_outcome(monkeypatch):
    """train() returns its own "status" — passing it through as a keyword collided with finish(status)."""
    wire_train(monkeypatch)
    pft.main()
    status, extra = FakeJob.finished[-1]
    assert status == "success" and extra["train_status"] == "skipped" and extra["n_labels"] == 0


def test_main_stores_a_promoted_model_and_logs_it(monkeypatch):
    client = wire_train(monkeypatch)
    models = client["analytics"]["fuel_models"]
    models.replace_one({"_id": "lr-old"}, {"_id": "lr-old", "version": "lr-old", "active": True})
    promoted = {"n_labels": 80, "positives": 40, "negatives": 40, "status": "promoted",
                "metrics": {"precision_at_20": 0.5}, "model": {"_id": "lr-new", "version": "lr-new", "active": True}}
    monkeypatch.setattr(pft, "train", lambda *args, **kwargs: dict(promoted))
    pft.main()
    assert models.find_one({"_id": "lr-new"})["active"] and not models.find_one({"_id": "lr-old"})["active"]
    status, extra = FakeJob.finished[-1]
    assert status == "success" and extra["train_status"] == "promoted" and extra["precision_at_20"] == 0.5
