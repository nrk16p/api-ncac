from datetime import date

import pytest

from dates import ddmmyyyy, parse_date_list, parse_days


def test_parse_days_is_inclusive():
    assert parse_days("30/09/2026", "02/10/2026") == [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]


def test_parse_days_single_day():
    assert parse_days("05/10/2026", "05/10/2026") == [date(2026, 10, 5)]


def test_parse_days_rejects_reversed_range():
    with pytest.raises(ValueError):
        parse_days("05/10/2026", "01/10/2026")


def test_parse_date_list_sorts_and_dedupes():
    assert parse_date_list("15/06/2026, 01/06/2026,15/06/2026,") == [date(2026, 6, 1), date(2026, 6, 15)]


def test_ddmmyyyy():
    assert ddmmyyyy(date(2026, 3, 1)) == "01/03/2026"
