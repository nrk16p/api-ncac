from datetime import datetime, timedelta, timezone

import pytest
import requests

from besttech_client import NETWORK_WAITS, THROTTLE_WAITS, BesttechClient, BesttechError

TH = timezone(timedelta(hours=7))


class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    def __init__(self):
        self.t, self.sleeps = 1000.0, []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


OK_TRACK = FakeResp({"what": "ok", "info": {"vehicles": [{"vehicle_no": "ME152 (71-8635 สบ.)"}]}})
THROTTLED = FakeResp({"what": "error", "error_code": "error.TooManyRequests", "msg": "busy"})


def make(responses, hour=12, spacing=35.0):
    clock = Clock()
    session = FakeSession(responses)
    client = BesttechClient("KEY", spacing_s=spacing, session=session, sleep=clock.sleep,
                            monotonic=clock.monotonic, now=lambda: datetime(2026, 10, 6, hour, 30, tzinfo=TH))
    return client, session, clock


def test_track_sends_bearer_and_returns_vehicles():
    client, session, _ = make([OK_TRACK])
    assert client.track() == [{"vehicle_no": "ME152 (71-8635 สบ.)"}]
    call = session.calls[0]
    assert call["url"].endswith("/apiservices/track")
    assert call["headers"] == {"Authorization": "Bearer KEY"}
    assert call["json"] == {"last_gps_time": ""}


def test_calls_are_spaced():
    client, _, clock = make([OK_TRACK, OK_TRACK])
    client.track()
    client.track()
    assert clock.sleeps == [35.0]


def test_throttle_then_success():
    client, _, clock = make([THROTTLED, OK_TRACK])
    assert len(client.track()) == 1
    assert clock.sleeps[0] == THROTTLE_WAITS[0]


def test_persistent_throttle_raises():
    client, _, _ = make([THROTTLED] * (len(THROTTLE_WAITS) + 1), spacing=0)
    with pytest.raises(BesttechError, match="TooManyRequests"):
        client.track()


def test_other_error_raises_immediately():
    bad = FakeResp({"what": "error", "error_code": "error.APIKeyNotFound", "msg": "nope"})
    client, session, _ = make([bad])
    with pytest.raises(BesttechError, match="APIKeyNotFound"):
        client.track()
    assert len(session.calls) == 1


def test_network_error_is_retried():
    client, _, clock = make([requests.ConnectionError("down"), OK_TRACK], spacing=0)
    assert len(client.track()) == 1
    assert clock.sleeps == [NETWORK_WAITS[0]]


def test_network_errors_back_off_longer():
    client, _, clock = make([requests.ConnectionError("down")] * 4 + [OK_TRACK], spacing=0)
    assert len(client.track()) == 1
    assert clock.sleeps == list(NETWORK_WAITS)


def test_network_errors_give_up_after_five_attempts():
    client, session, _ = make([requests.ConnectionError("down")] * 5, spacing=0)
    with pytest.raises(BesttechError, match="5 attempts"):
        client.track()
    assert len(session.calls) == 5


def test_http_error_status_is_retried():
    client, _, _ = make([FakeResp({}, status=502), OK_TRACK], spacing=0)
    assert len(client.track()) == 1


def test_pause_window_waits_until_it_ends():
    client, _, clock = make([OK_TRACK], hour=9)
    client.track()
    assert clock.sleeps[0] == 30 * 60


def test_history_body_and_points():
    resp = FakeResp({"what": "ok", "info": {"vehicle_no": "ME152 (71-8635 สบ.)", "count": 1,
                                            "points": [{"gps_time": "2026-10-05 10:00:00"}]}})
    client, session, _ = make([resp])
    out = client.history("ME152 (71-8635 สบ.)", datetime(2026, 10, 5, 0, 0, 0), datetime(2026, 10, 5, 23, 59, 59))
    assert out == [{"gps_time": "2026-10-05 10:00:00"}]
    call = session.calls[0]
    assert call["url"].endswith("/apiservices/history")
    assert call["json"] == {"vehicle_no": "ME152 (71-8635 สบ.)",
                            "start_time": "2026-10-05 00:00:00", "end_time": "2026-10-05 23:59:59"}


def test_history_all_is_not_offered():
    # /history_all locks the key out for 20+ minutes after ~7 calls (measured 2026-10-06)
    assert not hasattr(BesttechClient, "history_all")


def test_missing_key_raises():
    with pytest.raises(BesttechError):
        BesttechClient("")
