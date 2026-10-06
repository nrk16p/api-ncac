"""BestTransport API client — /track and per-vehicle /history (spec §3.3).

/history_all is deliberately not offered: measured 2026-10-06, it answers error.TooManyRequests
after ~7 calls and then keeps the key locked out of that endpoint for 20+ minutes (retries do not
clear it), while /track and /history keep working.

Quirks confirmed in production (menatransport/mongodb-gps app/services/besttech.py):
- Auth is `Authorization: Bearer <key>`.
- Content-Type must be plain application/json (a "; charset=utf-8" suffix returns HTTP 415), so the
  body goes through requests' json= and no Content-Type header is set by hand.
- Calling too often returns HTTP 200 with error_code "error.TooManyRequests"; the server needs about
  30–45 s before it accepts the next call.
- mongodb-gps ingests concrete data at 09:25 BKK with the same key, so calls pause 09:00–10:00.
"""
import time
from datetime import datetime, timedelta, timezone

import requests

DEFAULT_BASE_URL = "https://besttransportservice.bestgeosystem.com/apiservices"
THROTTLE_WAITS = (15, 30, 45, 60, 60, 60)
NETWORK_WAITS = (10, 30, 60, 120)   # waits between attempts — 5 attempts in total
TH_TZ = timezone(timedelta(hours=7))
TIME_FMT = "%Y-%m-%d %H:%M:%S"


class BesttechError(RuntimeError):
    """The Besttech service failed or answered with an error envelope."""


class BesttechClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, spacing_s: float = 35.0,
                 pause_hours: tuple[int, int] | None = (9, 10), timeout_s: int = 180, session=None,
                 sleep=time.sleep, monotonic=time.monotonic, now=None):
        if not api_key:
            raise BesttechError("BESTTECH_API is not set")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.spacing_s = spacing_s
        self.pause_hours = pause_hours
        self.timeout_s = timeout_s
        self.session = session or requests.Session()
        self.sleep = sleep
        self.monotonic = monotonic
        self.now = now or (lambda: datetime.now(TH_TZ))
        self._last_call: float | None = None

    def _wait_turn(self) -> None:
        if self.pause_hours:
            start_hour, end_hour = self.pause_hours
            now = self.now()
            if start_hour <= now.hour < end_hour:
                resume = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
                self.sleep((resume - now).total_seconds())
        if self._last_call is not None:
            wait = self.spacing_s - (self.monotonic() - self._last_call)
            if wait > 0:
                self.sleep(wait)

    def _post(self, path: str, body: dict) -> dict:
        throttled = 0
        failures = 0
        while True:
            self._wait_turn()
            try:
                response = self.session.post(f"{self.base_url}/{path}", json=body,
                                             headers={"Authorization": f"Bearer {self.api_key}"},
                                             timeout=self.timeout_s)
                self._last_call = self.monotonic()
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                self._last_call = self.monotonic()
                failures += 1
                if failures > len(NETWORK_WAITS):
                    raise BesttechError(f"{path} failed after {failures} attempts: {exc}") from exc
                self.sleep(NETWORK_WAITS[failures - 1])
                continue
            if payload.get("what") != "error":
                return payload
            code = payload.get("error_code")
            if code == "error.TooManyRequests" and throttled < len(THROTTLE_WAITS):
                self.sleep(THROTTLE_WAITS[throttled])
                throttled += 1
                continue
            raise BesttechError(f"{path} returned {code}: {payload.get('msg')}")

    def track(self) -> list[dict]:
        payload = self._post("track", {"last_gps_time": ""})
        return (payload.get("info") or {}).get("vehicles") or []

    def history(self, vehicle_no: str, start: datetime, end: datetime) -> list[dict]:
        """GPS points of one vehicle (`vehicle_no` exactly as /track returns it, ≤ 24 h per call)."""
        body = {"vehicle_no": vehicle_no, "start_time": start.strftime(TIME_FMT), "end_time": end.strftime(TIME_FMT)}
        payload = self._post("history", body)
        return (payload.get("info") or {}).get("points") or []
