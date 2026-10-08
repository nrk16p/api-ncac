import io
from datetime import date

import pandas as pd
import pytest
import requests

from rmc_client import RETRY_WAITS, fetch_report, push_records


class Resp:
    def __init__(self, payload=None, content=b"", status=200):
        self.payload, self.content, self.status_code = payload, content, status
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:  # requests puts the full URL in the message
            raise requests.HTTPError(f"{self.status_code} for url: https://files/x.xlsx?sig=SECRET", response=self)

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def excel_bytes():
    buf = io.BytesIO()
    pd.DataFrame({"หมายเลข DP": ["D1"], "รหัสรถ": [6496]}).to_excel(buf, startrow=3, index=False)
    return buf.getvalue()


def test_fetch_report_requests_the_day_and_reads_the_excel():
    session = Session([Resp({"result": "https://files/x.xlsx"}), Resp(content=excel_bytes())])
    df = fetch_report(date(2026, 10, 5), "https://fleet/report", [11, 22], session=session, sleep=lambda s: None)
    assert list(df["หมายเลข DP"]) == ["D1"]
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", "https://fleet/report")
    body = kwargs["json"]
    assert body["date_start"] == "2026-10-05 00:00:00" and body["date_end"] == "2026-10-05 23:59:59"
    assert body["vehicle_list"] == [11, 22] and body["vehicle_visibility"] == "11,22" and body["company_id"] == 1231
    assert session.calls[1][:2] == ("GET", "https://files/x.xlsx")


def test_fetch_report_retries_then_fails():
    waits = []
    session = Session([requests.ConnectionError("down")] * 3)
    with pytest.raises(RuntimeError, match="POST https://fleet/report failed after 3 attempts: ConnectionError"):
        fetch_report(date(2026, 10, 5), "https://fleet/report", [1], session=session, sleep=waits.append)
    assert waits == list(RETRY_WAITS)


def test_failed_download_does_not_leak_the_signed_link():
    session = Session([Resp({"result": "https://files/x.xlsx?sig=SECRET"})] + [Resp(status=403)] * 3)
    with pytest.raises(RuntimeError) as err:
        fetch_report(date(2026, 10, 5), "https://fleet/report", [1], session=session, sleep=lambda s: None)
    assert "HTTP 403" in str(err.value) and "SECRET" not in str(err.value)


def test_fetch_report_without_file_url_fails():
    with pytest.raises(RuntimeError, match="no result file"):
        fetch_report(date(2026, 10, 5), "https://fleet/report", [1], session=Session([Resp({"result": None})]))


def test_push_returns_api_counts_and_needs_a_url():
    session = Session([Resp({"created": 2, "updated": 1, "total": 3})])
    assert push_records([{"a": 1}], "https://push", session=session) == {"created": 2, "updated": 1, "total": 3}
    assert session.calls[0][2]["json"] == [{"a": 1}]
    with pytest.raises(RuntimeError):
        push_records([{"a": 1}], "")
