from datetime import date, datetime, timedelta, timezone

from backfill_terminus_reviews import review_truck_days

TH = timezone(timedelta(hours=7))


def ts(y, m, d, h=12):
    return int(datetime(y, m, d, h, tzinfo=TH).timestamp() * 1000)


def test_multi_day_window():
    plan = review_truck_days([{"plate": "71-4247", "start_ts": ts(2026, 3, 10), "end_ts": ts(2026, 3, 12)}])
    assert plan == {date(2026, 3, 10): {"สบ.71-4247"}, date(2026, 3, 11): {"สบ.71-4247"},
                    date(2026, 3, 12): {"สบ.71-4247"}}


def test_window_clamped_to_terminus_start():
    plan = review_truck_days([{"plate": "71-4247", "start_ts": ts(2026, 2, 27), "end_ts": ts(2026, 3, 1)}])
    assert list(plan) == [date(2026, 3, 1)]


def test_old_or_invalid_reviews_skipped():
    assert review_truck_days([
        {"plate": "71-4247", "start_ts": ts(2026, 1, 5), "end_ts": ts(2026, 1, 9)},
        {"plate": "", "start_ts": ts(2026, 3, 5), "end_ts": ts(2026, 3, 5)},
        {"plate": "71-0001", "start_ts": None, "end_ts": ts(2026, 3, 5)},
    ]) == {}
