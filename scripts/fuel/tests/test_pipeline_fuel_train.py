from datetime import date

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
