"""Date helpers for the fuel jobs — env dates use dd/mm/YYYY like the engine-on pipeline."""
from datetime import date, datetime, timedelta


def parse_day(text: str) -> date:
    return datetime.strptime(text.strip(), "%d/%m/%Y").date()


def parse_days(start: str, end: str) -> list[date]:
    first, last = parse_day(start), parse_day(end)
    if last < first:
        raise ValueError(f"END_DATE {end} is before START_DATE {start}")
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def parse_date_list(text: str) -> list[date]:
    return sorted({parse_day(part) for part in text.split(",") if part.strip()})


def ddmmyyyy(day: date) -> str:
    return day.strftime("%d/%m/%Y")
