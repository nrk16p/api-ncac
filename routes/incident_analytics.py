"""
Incident Analytics — read-model รวม NC (case_reports) + AC (accident_cases)
สำหรับหน้า Dashboard ของ acnc-project

ทำไมต้องมี endpoint นี้แยกจาก /case_reports และ /accident-cases
------------------------------------------------------------------
สอง endpoint เดิมเป็น *transactional list* — คืน `{items, total, page, ...}`
พร้อม joinedload ของ docs/damage_items/investigation ครบทุกแถว ซึ่ง:
  1. หน้า Dashboard ต้องขอ page_size สูงมากเพื่อให้ได้ข้อมูลครบทั้งปี
     (ค่า default 25 → ตัวเลขบน dashboard ผิดเงียบ ๆ โดยไม่มีใครรู้)
  2. payload หนักหลาย MB ต่อการโหลดหนึ่งครั้ง ทั้งที่หน้าจอใช้แค่ตัวเลขสรุป
  3. ตรรกะการวิเคราะห์กระจายอยู่ฝั่ง FE และ NC/AC ที่ schema ไม่เหมือนกัน
     ต้อง normalize ซ้ำทุกที่ที่เรียกใช้

endpoint นี้จึงดึง "แถวดิบเท่าที่จำเป็น" ด้วย UNION ALL ครั้งเดียว (23 คอลัมน์
ไม่มี text ก้อนใหญ่ / JSONB) แล้วรวมยอดใน Python — ใช้ 4 query ต่อการเรียก
หนึ่งครั้ง เพื่อไม่กิน connection pool ที่มีแค่ 2+3 (ดูคำอธิบายใน database.py)

เรื่อง timezone (สำคัญ — สองตารางเก็บไม่เหมือนกัน)
------------------------------------------------------------------
  · case_reports.record_date       = DateTime ไม่มี tz และเก็บเป็น "เวลาไทย" อยู่แล้ว
  · accident_cases.record_datetime = DateTime(timezone=True) เก็บเป็น UTC
ถ้าเทียบตรง ๆ ช่วงวันของ AC จะเหลื่อม 7 ชั่วโมง (เคสหลัง 17:00 น. ของวันสิ้นเดือน
จะตกไปเดือนถัดไป) ที่นี่จึงแปลง AC เป็นเวลาไทยด้วย `timezone('Asia/Bangkok', ...)`
ก่อนกรองและก่อนจัดกลุ่มทุกครั้ง
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Numeric, String, and_, case, cast, func, literal, null, or_, select, union_all
from sqlalchemy.orm import Session

from database import get_db
from models import (
    AccidentCase,
    AccidentCaseInvestigate,
    AccidentCaseInvestigateRootCause,
    CaseReport,
    CaseReportInvestigate,
    Client,
    MasterCause,
    MasterDriver,
    Site,
)

router = APIRouter(prefix="/analytics/incidents", tags=["Incident Analytics"])
logger = logging.getLogger("incident_analytics")

BKK = "Asia/Bangkok"

PRIORITY_ORDER = ["Crisis", "Major", "Minor"]
# ระบบเริ่มบันทึกข้อมูลจริงตั้งแต่ต้นปี 2569 (1 ม.ค. 2026) — ข้อมูลก่อนหน้านี้ถ้ามี
# ไม่ใช่การใช้งานจริง เทียบ "ช่วงก่อนหน้า" ข้ามเส้นนี้จะได้ % เปลี่ยนแปลงหลอกตา
# (เทียบของจริงกับข้อมูลที่ไม่มีความหมาย) จึงต้องกันไว้ไม่ให้ period เปรียบเทียบ
# ย้อนไปก่อนวันนี้ได้ — ปรับวันที่นี้ถ้า go-live จริงไม่ใช่วันนี้
SYSTEM_GO_LIVE = date(2026, 1, 1)
WEEKDAY_LABELS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
HOUR_BUCKETS = [
    ("00-03", 0, 3),
    ("04-07", 4, 7),
    ("08-11", 8, 11),
    ("12-15", 12, 15),
    ("16-19", 16, 19),
    ("20-23", 20, 23),
]
# ค่าที่ระบบบันทึกเมื่อ "ตรวจแล้วไม่พบสารเสพติด" — ต้องไม่นับเป็นผลบวก
# (ชุดเดียวกับที่ calculate_priority ใน routes/accident_cases.py ใช้)
SAFE_DRUG_VALUES = ["", "none", "negative", "ไม่ใส่ชนิดสารเสพติด", "-", "no"]
OPEN_STATUSES = {"pending", "in progress", "open", "completed investigate"}
AGING_BUCKETS = [
    ("0-7 วัน", 0, 7),
    ("8-15 วัน", 8, 15),
    ("16-30 วัน", 16, 30),
    ("31-60 วัน", 31, 60),
    ("60+ วัน", 61, 10**6),
]


# ============================================================
# Helpers
# ============================================================
def _f(value: Any) -> float:
    """แปลงค่าเป็น float โดยถือว่า None = 0 (Numeric ของ SQLAlchemy คืน Decimal)"""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _i(value: Any) -> int:
    return int(_f(value))


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _change_pct(current: float, previous: float) -> Optional[float]:
    """
    เปอร์เซ็นต์เปลี่ยนแปลงเทียบช่วงก่อนหน้า

    คืน None เมื่อช่วงก่อนหน้าเป็น 0 — เพราะ "เพิ่มขึ้น ∞%" ไม่ใช่ข้อมูลที่ใช้
    ตัดสินใจได้ ฝั่ง FE จะแสดงเป็น "ใหม่" แทนตัวเลขหลอกตา
    """
    if not previous:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def _metric(current: float, previous: float, decimals: int = 0) -> Dict[str, Any]:
    cur = round(current, decimals) if decimals else int(round(current))
    prev = round(previous, decimals) if decimals else int(round(previous))
    return {
        "value": cur,
        "previous": prev,
        "change_pct": _change_pct(current, previous),
    }


def _parse_date(value: Optional[str], fallback: date) -> date:
    if not value:
        return fallback
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"รูปแบบวันที่ไม่ถูกต้อง: {value}")


def _clean(value: Optional[str], default: str = "ไม่ระบุ") -> str:
    text = (value or "").strip()
    return text if text else default


# ============================================================
# Unified NC + AC row source
# ============================================================
def _nc_select(start: date, end_exclusive: date):
    has_inv = (
        select(literal(1))
        .select_from(CaseReportInvestigate)
        .where(CaseReportInvestigate.document_no == CaseReport.document_no)
        .correlate(CaseReport)
        .exists()
    )
    return (
        select(
            cast(literal("NC"), String).label("source"),
            CaseReport.document_no.label("doc_no"),
            CaseReport.site_id.label("site_id"),
            Site.site_name_th.label("site_name"),
            CaseReport.client_id.label("client_id"),
            Client.client_name.label("client_name"),
            cast(CaseReport.driver_id, String).label("driver_id"),
            func.concat(MasterDriver.first_name, " ", MasterDriver.last_name).label("driver_name"),
            CaseReport.vehicle_truckno.label("truck_no"),
            CaseReport.record_date.label("record_local"),
            func.coalesce(CaseReport.incident_date, CaseReport.record_date).label("incident_local"),
            CaseReport.priority.label("priority"),
            CaseReport.casestatus.label("casestatus"),
            MasterCause.cause_name.label("cause"),
            cast(func.coalesce(CaseReport.estimated_cost, 0), Numeric(14, 2)).label("estimated_cost"),
            cast(func.coalesce(CaseReport.actual_price, 0), Numeric(14, 2)).label("actual_cost"),
            literal(0).label("fatalities"),
            literal(0).label("injured_hospitalized"),
            literal(0).label("injured_not_hospitalized"),
            literal(0).label("alcohol_positive"),
            literal(0).label("drug_positive"),
            case((has_inv, 1), else_=0).label("has_investigation"),
            cast(null(), String).label("fault_party"),
        )
        .select_from(CaseReport)
        .outerjoin(Site, CaseReport.site_id == Site.site_id)
        .outerjoin(Client, CaseReport.client_id == Client.client_id)
        .outerjoin(MasterDriver, CaseReport.driver_id == MasterDriver.driver_id)
        .outerjoin(MasterCause, CaseReport.incident_cause_id == MasterCause.cause_id)
        .where(
            CaseReport.record_date >= start,
            CaseReport.record_date < end_exclusive,
        )
    )


def _ac_select(start: date, end_exclusive: date):
    # cast zone เป็น text ให้ชัด — timezone() ของ Postgres มีทั้งเวอร์ชัน (text, timestamptz)
    # และ (interval, timestamptz) การส่ง literal ที่ยังไม่มีชนิดทำให้ resolution กำกวมได้
    zone = cast(literal(BKK), String)
    record_local = func.timezone(zone, AccidentCase.record_datetime)
    incident_local = func.timezone(zone, func.coalesce(AccidentCase.incident_datetime, AccidentCase.record_datetime))

    has_inv = (
        select(literal(1))
        .select_from(AccidentCaseInvestigate)
        .where(AccidentCaseInvestigate.document_no_ac == AccidentCase.document_no_ac)
        .correlate(AccidentCase)
        .exists()
    )
    drug_raw = func.lower(func.trim(func.coalesce(AccidentCase.drug_test_result, "")))

    return (
        select(
            cast(literal("AC"), String).label("source"),
            AccidentCase.document_no_ac.label("doc_no"),
            AccidentCase.site_id.label("site_id"),
            Site.site_name_th.label("site_name"),
            AccidentCase.client_id.label("client_id"),
            Client.client_name.label("client_name"),
            cast(AccidentCase.driver_id, String).label("driver_id"),
            func.concat(MasterDriver.first_name, " ", MasterDriver.last_name).label("driver_name"),
            AccidentCase.vehicle_truckno.label("truck_no"),
            record_local.label("record_local"),
            incident_local.label("incident_local"),
            AccidentCase.priority.label("priority"),
            AccidentCase.casestatus.label("casestatus"),
            cast(null(), String).label("cause"),
            cast(
                func.coalesce(AccidentCase.estimated_goods_damage_value, 0)
                + func.coalesce(AccidentCase.estimated_vehicle_damage_value, 0),
                Numeric(14, 2),
            ).label("estimated_cost"),
            cast(
                func.coalesce(AccidentCase.actual_goods_damage_value, 0)
                + func.coalesce(AccidentCase.actual_vehicle_damage_value, 0),
                Numeric(14, 2),
            ).label("actual_cost"),
            func.coalesce(AccidentCase.fatalities, 0).label("fatalities"),
            func.coalesce(AccidentCase.injured_hospitalized, 0).label("injured_hospitalized"),
            func.coalesce(AccidentCase.injured_not_hospitalized, 0).label("injured_not_hospitalized"),
            case((func.coalesce(AccidentCase.alcohol_test_result, 0) > 0, 1), else_=0).label("alcohol_positive"),
            case((drug_raw.notin_(SAFE_DRUG_VALUES), 1), else_=0).label("drug_positive"),
            case((has_inv, 1), else_=0).label("has_investigation"),
            AccidentCase.fault_party.label("fault_party"),
        )
        .select_from(AccidentCase)
        .outerjoin(Site, AccidentCase.site_id == Site.site_id)
        .outerjoin(Client, AccidentCase.client_id == Client.client_id)
        .outerjoin(MasterDriver, AccidentCase.driver_id == MasterDriver.driver_id)
        .where(
            record_local >= start,
            record_local < end_exclusive,
        )
    )


def _fetch_rows(
    db: Session,
    start: date,
    end_exclusive: date,
    case_type: str,
    site_ids: Optional[List[int]],
    client_ids: Optional[List[int]],
    priorities: Optional[List[str]],
    casestatuses: Optional[List[str]],
) -> List[Dict[str, Any]]:
    parts = []
    if case_type in ("all", "nc"):
        parts.append(_nc_select(start, end_exclusive))
    if case_type in ("all", "ac"):
        parts.append(_ac_select(start, end_exclusive))
    if not parts:
        return []

    stmt = parts[0] if len(parts) == 1 else union_all(*parts)
    unified = stmt.subquery("unified")

    query = select(unified)
    if site_ids:
        query = query.where(unified.c.site_id.in_(site_ids))
    if client_ids:
        query = query.where(unified.c.client_id.in_(client_ids))
    if priorities:
        query = query.where(unified.c.priority.in_(priorities))
    if casestatuses:
        query = query.where(unified.c.casestatus.in_(casestatuses))

    rows = db.execute(query).mappings().all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        record_at: Optional[datetime] = r["record_local"]
        incident_at: Optional[datetime] = r["incident_local"] or record_at
        out.append(
            {
                "source": r["source"],
                "doc_no": r["doc_no"],
                "site_id": r["site_id"],
                "site_name": _clean(r["site_name"], "ไม่ระบุศูนย์"),
                "client_id": r["client_id"],
                "client_name": _clean(r["client_name"], "ไม่ระบุลูกค้า"),
                "driver_id": (r["driver_id"] or "").strip() or None,
                "driver_name": _clean(r["driver_name"], "ไม่ระบุพนักงานขับรถ"),
                "truck_no": _clean(r["truck_no"], "ไม่ระบุทะเบียน"),
                "record_at": record_at,
                "incident_at": incident_at,
                "priority": _clean(r["priority"], "Minor"),
                "casestatus": _clean(r["casestatus"], "Pending"),
                "cause": (r["cause"] or "").strip() or None,
                "estimated_cost": _f(r["estimated_cost"]),
                "actual_cost": _f(r["actual_cost"]),
                "fatalities": _i(r["fatalities"]),
                "injured_hospitalized": _i(r["injured_hospitalized"]),
                "injured_not_hospitalized": _i(r["injured_not_hospitalized"]),
                "alcohol_positive": _i(r["alcohol_positive"]),
                "drug_positive": _i(r["drug_positive"]),
                "has_investigation": _i(r["has_investigation"]),
                "fault_party": (r["fault_party"] or "").strip() or None,
            }
        )
    return out


# ============================================================
# Aggregations
# ============================================================
def _severity_mix(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    total = len(rows)
    buckets: Dict[str, Dict[str, int]] = {p: {"nc": 0, "ac": 0} for p in PRIORITY_ORDER}
    for r in rows:
        bucket = buckets.setdefault(r["priority"], {"nc": 0, "ac": 0})
        bucket["nc" if r["source"] == "NC" else "ac"] += 1

    ordered = PRIORITY_ORDER + [p for p in buckets if p not in PRIORITY_ORDER]
    return [
        {
            "priority": p,
            "nc": buckets[p]["nc"],
            "ac": buckets[p]["ac"],
            "total": buckets[p]["nc"] + buckets[p]["ac"],
            "pct": _pct(buckets[p]["nc"] + buckets[p]["ac"], total),
        }
        for p in ordered
        if p in buckets
    ]


def _trend(rows: List[Dict[str, Any]], granularity: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        when: Optional[datetime] = r["record_at"]
        if not when:
            continue
        key = when.strftime("%Y-%m-%d") if granularity == "day" else when.strftime("%Y-%m")
        b = buckets.setdefault(
            key,
            {
                "key": key,
                "nc": 0,
                "ac": 0,
                "total": 0,
                "Crisis": 0,
                "Major": 0,
                "Minor": 0,
                "actual_cost": 0.0,
                "estimated_cost": 0.0,
            },
        )
        b["nc" if r["source"] == "NC" else "ac"] += 1
        b["total"] += 1
        if r["priority"] in b:
            b[r["priority"]] += 1
        b["actual_cost"] += r["actual_cost"]
        b["estimated_cost"] += r["estimated_cost"]

    out = []
    for key in sorted(buckets):
        b = buckets[key]
        b["actual_cost"] = round(b["actual_cost"], 2)
        b["estimated_cost"] = round(b["estimated_cost"], 2)
        out.append(b)
    return out


def _by_dimension(
    rows: List[Dict[str, Any]],
    prev_rows: List[Dict[str, Any]],
    key_field: str,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    total = len(rows)
    total_cost = sum(r["actual_cost"] for r in rows)

    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        k = r[key_field]
        a = agg.setdefault(
            k,
            {
                "name": k,
                "total": 0,
                "nc": 0,
                "ac": 0,
                "Crisis": 0,
                "Major": 0,
                "Minor": 0,
                "actual_cost": 0.0,
                "estimated_cost": 0.0,
                "last_incident": None,
            },
        )
        a["total"] += 1
        a["nc" if r["source"] == "NC" else "ac"] += 1
        if r["priority"] in a:
            a[r["priority"]] += 1
        a["actual_cost"] += r["actual_cost"]
        a["estimated_cost"] += r["estimated_cost"]
        when = r["record_at"]
        if when and (a["last_incident"] is None or when > a["last_incident"]):
            a["last_incident"] = when

    prev_counts: Dict[str, int] = defaultdict(int)
    for r in prev_rows:
        prev_counts[r[key_field]] += 1

    out = []
    for a in agg.values():
        prev = prev_counts.get(a["name"], 0)
        out.append(
            {
                **a,
                "actual_cost": round(a["actual_cost"], 2),
                "estimated_cost": round(a["estimated_cost"], 2),
                "share_pct": _pct(a["total"], total),
                "cost_share_pct": _pct(a["actual_cost"], total_cost),
                "previous": prev,
                "change_pct": _change_pct(a["total"], prev),
                "last_incident": a["last_incident"].isoformat() if a["last_incident"] else None,
            }
        )
    out.sort(key=lambda x: (-x["total"], -x["actual_cost"]))
    return out[:limit] if limit else out


def _causes(rows: List[Dict[str, Any]], limit: int = 12) -> List[Dict[str, Any]]:
    """
    Pareto ของสาเหตุ

    NC มี incident_cause_id ชี้ไป mastercauses ส่วน AC ไม่มีคอลัมน์สาเหตุ
    เคส AC ที่ยังไม่มีสาเหตุจึงถูกจัดเป็น "อุบัติเหตุ (รอระบุสาเหตุ)" แทนที่จะ
    ถูกซ่อน — ถ้าซ่อนไป สัดส่วนใน Pareto จะเพี้ยนและอ่านผิดทันที
    """
    total = len(rows)
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        label = r["cause"] or ("อุบัติเหตุ (รอระบุสาเหตุ)" if r["source"] == "AC" else "ไม่ระบุสาเหตุ")
        a = agg.setdefault(
            label, {"cause": label, "count": 0, "actual_cost": 0.0, "Crisis": 0, "Major": 0, "nc": 0, "ac": 0}
        )
        a["count"] += 1
        a["actual_cost"] += r["actual_cost"]
        a["nc" if r["source"] == "NC" else "ac"] += 1
        if r["priority"] in ("Crisis", "Major"):
            a[r["priority"]] += 1

    ordered = sorted(agg.values(), key=lambda x: -x["count"])[:limit]
    cumulative = 0
    out = []
    for a in ordered:
        cumulative += a["count"]
        out.append(
            {
                **a,
                "actual_cost": round(a["actual_cost"], 2),
                "pct": _pct(a["count"], total),
                "cum_pct": _pct(cumulative, total),
            }
        )
    return out


def _timing(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    weekday = [{"weekday": i, "label": WEEKDAY_LABELS[i], "total": 0, "severe": 0} for i in range(7)]
    hours = [{"hour": h, "total": 0} for h in range(24)]
    heat: Dict[tuple, int] = defaultdict(int)

    for r in rows:
        when: Optional[datetime] = r["incident_at"]
        if not when:
            continue
        wd, hr = when.weekday(), when.hour
        weekday[wd]["total"] += 1
        if r["priority"] in ("Crisis", "Major"):
            weekday[wd]["severe"] += 1
        hours[hr]["total"] += 1
        for label, lo, hi in HOUR_BUCKETS:
            if lo <= hr <= hi:
                heat[(wd, label)] += 1
                break

    total_timed = sum(h["total"] for h in hours)

    # ช่วงเสี่ยงสูงสุด: เลื่อนหน้าต่าง 3 ชั่วโมงหาช่วงที่มีเคสหนาแน่นที่สุด
    peak = {"label": "-", "count": 0, "pct": 0.0}
    if total_timed:
        best_start, best_count = 0, -1
        for start_h in range(24):
            window = sum(hours[(start_h + k) % 24]["total"] for k in range(3))
            if window > best_count:
                best_start, best_count = start_h, window
        peak = {
            "label": f"{best_start:02d}:00–{(best_start + 3) % 24:02d}:00 น.",
            "count": best_count,
            "pct": _pct(best_count, total_timed),
        }

    return {
        "by_weekday": weekday,
        "by_hour": hours,
        "heatmap": [
            {"weekday": wd, "weekday_label": WEEKDAY_LABELS[wd], "bucket": label, "total": heat.get((wd, label), 0)}
            for wd in range(7)
            for label, _lo, _hi in HOUR_BUCKETS
        ],
        "peak_window": peak,
    }


def _status_and_aging(rows: List[Dict[str, Any]], today: date) -> Dict[str, Any]:
    funnel: Dict[str, int] = defaultdict(int)
    for r in rows:
        funnel[r["casestatus"]] += 1

    aging = {label: 0 for label, _lo, _hi in AGING_BUCKETS}
    ages: List[int] = []
    overdue = 0

    for r in rows:
        if r["casestatus"].strip().lower() not in OPEN_STATUSES:
            continue
        when = r["record_at"]
        if not when:
            continue
        age = (today - when.date()).days
        ages.append(age)
        if age > 30:
            overdue += 1
        for label, lo, hi in AGING_BUCKETS:
            if lo <= age <= hi:
                aging[label] += 1
                break

    total = len(rows)
    return {
        "funnel": sorted(
            [{"status": k, "count": v, "pct": _pct(v, total)} for k, v in funnel.items()],
            key=lambda x: -x["count"],
        ),
        "aging": [{"bucket": label, "count": aging[label]} for label, _lo, _hi in AGING_BUCKETS],
        "open_cases": len(ages),
        "overdue_cases": overdue,
        "avg_open_age_days": round(sum(ages) / len(ages), 1) if ages else 0.0,
    }


def _cost_section(rows: List[Dict[str, Any]], recovery: Dict[str, Any]) -> Dict[str, Any]:
    total_actual = sum(r["actual_cost"] for r in rows)
    total_estimated = sum(r["estimated_cost"] for r in rows)

    by_priority: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        b = by_priority.setdefault(r["priority"], {"priority": r["priority"], "count": 0, "actual": 0.0, "estimated": 0.0})
        b["count"] += 1
        b["actual"] += r["actual_cost"]
        b["estimated"] += r["estimated_cost"]

    priority_rows = []
    for p in PRIORITY_ORDER + [k for k in by_priority if k not in PRIORITY_ORDER]:
        if p not in by_priority:
            continue
        b = by_priority[p]
        priority_rows.append(
            {
                "priority": p,
                "count": b["count"],
                "actual": round(b["actual"], 2),
                "estimated": round(b["estimated"], 2),
                "avg_actual": round(b["actual"] / b["count"], 2) if b["count"] else 0.0,
            }
        )

    top_cases = sorted(rows, key=lambda r: -r["actual_cost"])[:10]

    # เทียบเฉพาะเคสที่มีทั้งประเมินและค่าจริง — ถ้ารวมเคสที่ยังไม่ปิดยอด
    # (actual = 0) เข้าไปด้วย ตัวเลขจะบอกว่า "ประเมินสูงเกินจริง" ทุกครั้ง
    both = [r for r in rows if r["estimated_cost"] > 0 and r["actual_cost"] > 0]
    settled_est = sum(r["estimated_cost"] for r in both)
    settled_act = sum(r["actual_cost"] for r in both)

    return {
        "total_actual": round(total_actual, 2),
        "total_estimated": round(total_estimated, 2),
        "settled_cases": len(both),
        "settled_estimated": round(settled_est, 2),
        "settled_actual": round(settled_act, 2),
        "estimate_accuracy_pct": _change_pct(settled_act, settled_est),
        "by_priority": priority_rows,
        "recovery": recovery,
        "top_cases": [
            {
                "doc_no": r["doc_no"],
                "source": r["source"],
                "date": r["record_at"].isoformat() if r["record_at"] else None,
                "site_name": r["site_name"],
                "client_name": r["client_name"],
                "driver_name": r["driver_name"],
                "priority": r["priority"],
                "cause": r["cause"] or ("อุบัติเหตุ" if r["source"] == "AC" else "ไม่ระบุ"),
                "actual_cost": round(r["actual_cost"], 2),
                "estimated_cost": round(r["estimated_cost"], 2),
                "casestatus": r["casestatus"],
            }
            for r in top_cases
            if r["actual_cost"] > 0
        ],
    }


def _safety(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    ac_rows = [r for r in rows if r["source"] == "AC"]
    at_fault = sum(1 for r in ac_rows if r["fault_party"] and "ไม่" not in r["fault_party"] and "ผิด" in r["fault_party"])
    not_at_fault = sum(1 for r in ac_rows if r["fault_party"] and "ไม่" in r["fault_party"])
    return {
        "fatalities": sum(r["fatalities"] for r in rows),
        "injured_hospitalized": sum(r["injured_hospitalized"] for r in rows),
        "injured_not_hospitalized": sum(r["injured_not_hospitalized"] for r in rows),
        "alcohol_positive": sum(r["alcohol_positive"] for r in rows),
        "drug_positive": sum(r["drug_positive"] for r in rows),
        "at_fault": at_fault,
        "not_at_fault": not_at_fault,
        "fault_unknown": len(ac_rows) - at_fault - not_at_fault,
        "ac_cases": len(ac_rows),
    }


# ============================================================
# Supplementary queries (cost recovery / 5M1E)
# ============================================================
def _recovery(db: Session, doc_nos: List[str]) -> Dict[str, Any]:
    """
    ยอดการรับผิดชอบความเสียหายจากใบสอบสวน NC (case_report_investigate)

    AC ยังไม่มีฟิลด์เงินในใบสอบสวน (accident_case_investigate เก็บ 5M1E/มาตรการ)
    ตัวเลขชุดนี้จึงเป็นของ NC ล้วน และ FE ต้องระบุกำกับให้ผู้อ่านทราบ
    """
    empty = {
        "insurance_claim": 0.0,
        "product_resellable": 0.0,
        "driver_cost": 0.0,
        "company_cost": 0.0,
        "penalty": 0.0,
        "remaining_damage_cost": 0.0,
        "documented_cases": 0,
        "scope": "NC",
    }
    if not doc_nos:
        return empty

    row = db.execute(
        select(
            func.coalesce(func.sum(CaseReportInvestigate.insurance_claim), 0),
            func.coalesce(func.sum(CaseReportInvestigate.product_resellable), 0),
            func.coalesce(func.sum(CaseReportInvestigate.driver_cost), 0),
            func.coalesce(func.sum(CaseReportInvestigate.company_cost), 0),
            func.coalesce(func.sum(CaseReportInvestigate.penalty), 0),
            func.coalesce(func.sum(CaseReportInvestigate.remaining_damage_cost), 0),
            func.count(CaseReportInvestigate.investigate_id),
        ).where(CaseReportInvestigate.document_no.in_(doc_nos))
    ).first()

    if not row:
        return empty
    return {
        "insurance_claim": round(_f(row[0]), 2),
        "product_resellable": round(_f(row[1]), 2),
        "driver_cost": round(_f(row[2]), 2),
        "company_cost": round(_f(row[3]), 2),
        "penalty": round(_f(row[4]), 2),
        "remaining_damage_cost": round(_f(row[5]), 2),
        "documented_cases": _i(row[6]),
        "scope": "NC",
    }


def _root_cause_categories(db: Session, doc_nos: List[str]) -> List[Dict[str, Any]]:
    """สัดส่วน 5M1E จากใบสอบสวน AC (accident_case_investigate_root_causes.category)"""
    if not doc_nos:
        return []
    rows = db.execute(
        select(
            AccidentCaseInvestigateRootCause.category,
            func.count(AccidentCaseInvestigateRootCause.root_cause_pk),
        )
        .join(
            AccidentCaseInvestigate,
            AccidentCaseInvestigate.investigate_ac_id == AccidentCaseInvestigateRootCause.investigate_ac_id,
        )
        .where(AccidentCaseInvestigate.document_no_ac.in_(doc_nos))
        .group_by(AccidentCaseInvestigateRootCause.category)
    ).all()

    total = sum(_i(r[1]) for r in rows)
    out = [
        {"category": _clean(r[0], "ไม่ระบุหมวด"), "count": _i(r[1]), "pct": _pct(_i(r[1]), total)}
        for r in rows
    ]
    out.sort(key=lambda x: -x["count"])
    return out


def _last_crisis(db: Session) -> Dict[str, Any]:
    """เคส Crisis ล่าสุด (ไม่จำกัดช่วงที่กรอง) เพื่อคำนวณ 'ปลอดเหตุร้ายแรงมาแล้วกี่วัน'"""
    nc = select(
        CaseReport.document_no.label("doc_no"),
        CaseReport.record_date.label("at"),
    ).where(CaseReport.priority == "Crisis")
    ac = select(
        AccidentCase.document_no_ac.label("doc_no"),
        func.timezone(cast(literal(BKK), String), AccidentCase.record_datetime).label("at"),
    ).where(AccidentCase.priority == "Crisis")

    sub = union_all(nc, ac).subquery("crisis")
    row = db.execute(select(sub).order_by(sub.c.at.desc()).limit(1)).mappings().first()
    if not row or not row["at"]:
        return {"days_since_last_crisis": None, "last_crisis_date": None, "last_crisis_doc": None}

    when: datetime = row["at"]
    return {
        "days_since_last_crisis": (date.today() - when.date()).days,
        "last_crisis_date": when.isoformat(),
        "last_crisis_doc": row["doc_no"],
    }


# ============================================================
# Insight engine
# ============================================================
def _build_insights(payload: Dict[str, Any], rows: List[Dict[str, Any]], prev_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    แปลงตัวเลขเป็นข้อสังเกตที่พร้อมนำเข้าประชุม

    เกณฑ์ทุกข้อตั้งใจให้ "เงียบเมื่อไม่มีอะไรผิดปกติ" — insight ที่โผล่ทุกครั้ง
    จะถูกมองข้ามภายในสองสัปดาห์ ที่นี่จึงคัดเฉพาะข้อที่ผ่าน threshold จริง
    """
    out: List[Dict[str, Any]] = []
    total = len(rows)
    if not total:
        return out

    kpis = payload["kpis"]
    cost = payload["cost"]
    safety = payload["safety"]
    status = payload["status"]

    def add(severity: str, title: str, detail: str, metric: str = "", weight: float = 0.0):
        out.append({"severity": severity, "title": title, "detail": detail, "metric": metric, "_w": weight})

    # 1) ปริมาณเคสเทียบช่วงก่อนหน้า
    change = kpis["total_cases"]["change_pct"]
    if change is not None and abs(change) >= 10:
        rising = change > 0
        top_site = payload["sites"][0] if payload["sites"] else None
        driver_txt = ""
        if top_site and top_site.get("change_pct") is not None and rising and top_site["change_pct"] > 0:
            driver_txt = f" โดยมาจาก{top_site['name']}เป็นหลัก ({top_site['previous']} → {top_site['total']} เคส)"
        add(
            "warning" if rising else "positive",
            f"ปริมาณเคส{'เพิ่มขึ้น' if rising else 'ลดลง'} {abs(change):.0f}% เทียบช่วงก่อนหน้า",
            f"{len(prev_rows)} → {total} เคส{driver_txt}",
            f"{change:+.0f}%",
            abs(change),
        )

    # 2) เคส Crisis
    crisis = kpis["crisis_cases"]["value"]
    if crisis:
        add(
            "critical",
            f"มีเคสระดับ Crisis {crisis} เคส",
            f"คิดเป็น {_pct(crisis, total):.1f}% ของเคสทั้งหมด — ต้องมีใบสอบสวนและมาตรการครบทุกเคส",
            f"{crisis} เคส",
            100 + crisis,
        )

    # 3) การสูญเสียชีวิต / บาดเจ็บเข้ารักษา
    if safety["fatalities"]:
        add("critical", f"มีผู้เสียชีวิต {safety['fatalities']} ราย", "ต้องรายงานผู้บริหารและทบทวนมาตรการทันที", f"{safety['fatalities']} ราย", 200)
    elif safety["injured_hospitalized"]:
        add(
            "warning",
            f"มีผู้บาดเจ็บต้องเข้ารักษาในโรงพยาบาล {safety['injured_hospitalized']} ราย",
            "ตรวจสอบความครบถ้วนของเอกสารเคลมและการติดตามอาการ",
            f"{safety['injured_hospitalized']} ราย",
            60,
        )

    # 4) แอลกอฮอล์ / สารเสพติด
    positives = safety["alcohol_positive"] + safety["drug_positive"]
    if positives:
        add(
            "critical",
            f"พบผลตรวจแอลกอฮอล์/สารเสพติดเป็นบวก {positives} เคส",
            f"แอลกอฮอล์ {safety['alcohol_positive']} เคส · สารเสพติด {safety['drug_positive']} เคส — เข้าเงื่อนไข Crisis อัตโนมัติ",
            f"{positives} เคส",
            150,
        )

    # 5) ความแม่นยำของการประเมินค่าเสียหาย
    acc = cost["estimate_accuracy_pct"]
    if acc is not None and abs(acc) >= 15 and cost["settled_cases"] >= 5:
        over = acc > 0
        add(
            "warning" if over else "info",
            f"ค่าเสียหายจริง{'สูงกว่า' if over else 'ต่ำกว่า'}ที่ประเมินไว้ {abs(acc):.0f}%",
            f"จาก {cost['settled_cases']} เคสที่ปิดยอดแล้ว — ประเมิน {cost['settled_estimated']:,.0f} บาท เทียบกับจริง {cost['settled_actual']:,.0f} บาท",
            f"{acc:+.0f}%",
            abs(acc),
        )

    # 6) การกระจุกตัวของศูนย์ปฏิบัติการ
    if payload["sites"]:
        top = payload["sites"][0]
        if top["share_pct"] >= 35 and len(payload["sites"]) > 1:
            add(
                "warning",
                f"{top['name']} คิดเป็น {top['share_pct']:.0f}% ของเคสทั้งหมด",
                f"{top['total']} จาก {total} เคส · ค่าเสียหายจริง {top['actual_cost']:,.0f} บาท",
                f"{top['share_pct']:.0f}%",
                top["share_pct"],
            )

    # 7) พนักงานขับรถที่เกิดเคสซ้ำ
    conc = payload["entities"]["concentration"]
    if conc["repeat_driver_count"]:
        add(
            "warning",
            f"พนักงานขับรถ {conc['repeat_driver_count']} คนเกิดเคสซ้ำตั้งแต่ 3 ครั้งขึ้นไป",
            f"รวม {conc['repeat_driver_cases']} เคส ({_pct(conc['repeat_driver_cases'], total):.0f}% ของทั้งหมด) — ควรเข้าโปรแกรมอบรมเฉพาะบุคคล",
            f"{conc['repeat_driver_count']} คน",
            50 + conc["repeat_driver_count"],
        )
    elif conc["top5_driver_share_pct"] >= 30:
        add(
            "info",
            f"พนักงานขับรถ 5 อันดับแรกคิดเป็น {conc['top5_driver_share_pct']:.0f}% ของเคส",
            "การกระจุกตัวสูง — มาตรการเฉพาะกลุ่มจะให้ผลเร็วกว่ามาตรการทั้งองค์กร",
            f"{conc['top5_driver_share_pct']:.0f}%",
            conc["top5_driver_share_pct"],
        )

    # 8) เคสค้างเกิน 30 วัน
    if status["overdue_cases"]:
        add(
            "warning",
            f"เคสค้างดำเนินการเกิน 30 วัน {status['overdue_cases']} เคส",
            f"อายุเฉลี่ยของเคสที่ยังไม่ปิด {status['avg_open_age_days']:.0f} วัน จาก {status['open_cases']} เคสที่เปิดอยู่",
            f"{status['overdue_cases']} เคส",
            40 + status["overdue_cases"],
        )

    # 9) ความครอบคลุมของการสอบสวน
    coverage = kpis["investigation_coverage_pct"]["value"]
    if coverage < 70:
        add(
            "warning",
            f"มีใบสอบสวนเพียง {coverage:.0f}% ของเคสทั้งหมด",
            "เคสที่ไม่มีใบสอบสวนจะไม่ปรากฏใน 5M1E และไม่มีมาตรการติดตาม",
            f"{coverage:.0f}%",
            70 - coverage,
        )

    # 10) สาเหตุหลักตามหลัก Pareto
    causes = payload["causes"]
    if causes:
        top_cause = causes[0]
        if top_cause["pct"] >= 20:
            add(
                "info",
                f"สาเหตุอันดับ 1 คือ “{top_cause['cause']}” ({top_cause['pct']:.0f}%)",
                f"{top_cause['count']} เคส · ค่าเสียหายจริง {top_cause['actual_cost']:,.0f} บาท",
                f"{top_cause['pct']:.0f}%",
                top_cause["pct"],
            )
        vital_few = [c for c in causes if c["cum_pct"] <= 80]
        if len(vital_few) >= 2 and len(causes) > len(vital_few):
            add(
                "info",
                f"สาเหตุเพียง {len(vital_few)} ข้อ อธิบายเคสได้ {vital_few[-1]['cum_pct']:.0f}%",
                "แก้ที่กลุ่มนี้ก่อนจะให้ผลลัพธ์ต่อความพยายามสูงสุด",
                f"{len(vital_few)} สาเหตุ",
                20,
            )

    # 11) ช่วงเวลาเสี่ยง
    peak = payload["timing"]["peak_window"]
    if peak["pct"] >= 25:
        add(
            "info",
            f"ช่วงเวลาเสี่ยงสูงสุดคือ {peak['label']}",
            f"{peak['count']} เคส ({peak['pct']:.0f}% ของเคสที่ระบุเวลาได้) — ใช้จัดรอบ toolbox talk ให้ตรงจุด",
            peak["label"],
            peak["pct"],
        )

    # 12) ปลอดเหตุร้ายแรง
    days = payload["highlights"]["days_since_last_crisis"]
    if days is not None and days >= 30 and not crisis:
        add("positive", f"ไม่มีเคสระดับ Crisis มาแล้ว {days} วัน", "รักษาระดับนี้ไว้และใช้เป็นตัวชี้วัดเชิงบวกในการสื่อสารกับหน้างาน", f"{days} วัน", 10)

    rank = {"critical": 0, "warning": 1, "info": 2, "positive": 3}
    out.sort(key=lambda x: (rank[x["severity"]], -x["_w"]))
    for item in out:
        item.pop("_w", None)
    return out[:8]


# ============================================================
# Endpoints
# ============================================================
@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD (ค่าเริ่มต้น: 1 ม.ค. ปีปัจจุบัน)"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD (ค่าเริ่มต้น: วันนี้)"),
    case_type: str = Query("all", pattern="^(all|nc|ac)$"),
    site_id: Optional[List[int]] = Query(None),
    client_id: Optional[List[int]] = Query(None),
    priority: Optional[List[str]] = Query(None),
    casestatus: Optional[List[str]] = Query(None),
    granularity: str = Query("auto", pattern="^(auto|day|month)$"),
):
    today = date.today()
    start = _parse_date(start_date, date(today.year, 1, 1))
    end = _parse_date(end_date, today)
    if end < start:
        raise HTTPException(status_code=400, detail="end_date ต้องไม่น้อยกว่า start_date")

    span_days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span_days - 1)

    # ช่วงเปรียบเทียบใช้ได้ก็ต่อเมื่ออยู่ทั้งช่วงหลัง SYSTEM_GO_LIVE เท่านั้น —
    # เทียบกับช่วงก่อนระบบเริ่มใช้งานจริงจะได้ % เปลี่ยนแปลงที่ไม่มีความหมาย
    comparison_available = prev_start >= SYSTEM_GO_LIVE

    if granularity == "auto":
        granularity = "day" if span_days <= 62 else "month"

    try:
        if comparison_available:
            # ดึงทั้งช่วงปัจจุบันและช่วงเปรียบเทียบใน query เดียว แล้วค่อยแยกใน Python
            # (ประหยัด round trip และ connection ซึ่งเป็นทรัพยากรที่ตึงที่สุดของ service นี้)
            all_rows = _fetch_rows(
                db, prev_start, end + timedelta(days=1), case_type, site_id, client_id, priority, casestatus
            )
        else:
            all_rows = _fetch_rows(db, start, end + timedelta(days=1), case_type, site_id, client_id, priority, casestatus)
    except Exception as exc:  # noqa: BLE001
        logger.exception("incident analytics query failed")
        raise HTTPException(status_code=500, detail=f"ดึงข้อมูลวิเคราะห์ไม่สำเร็จ: {exc}")

    rows = [r for r in all_rows if r["record_at"] and start <= r["record_at"].date() <= end]
    prev_rows = (
        [r for r in all_rows if r["record_at"] and prev_start <= r["record_at"].date() <= prev_end]
        if comparison_available
        else []
    )

    nc_docs = [r["doc_no"] for r in rows if r["source"] == "NC"]
    ac_docs = [r["doc_no"] for r in rows if r["source"] == "AC"]

    total = len(rows)
    prev_total = len(prev_rows)

    def count(source_rows, **conds) -> int:
        return sum(1 for r in source_rows if all(r[k] == v for k, v in conds.items()))

    actual_cost = sum(r["actual_cost"] for r in rows)
    prev_actual_cost = sum(r["actual_cost"] for r in prev_rows)
    investigated = sum(1 for r in rows if r["has_investigation"])
    prev_investigated = sum(1 for r in prev_rows if r["has_investigation"])

    status = _status_and_aging(rows, today)
    prev_status = _status_and_aging(prev_rows, today)

    payload: Dict[str, Any] = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days": span_days,
            "compare_start_date": prev_start.isoformat() if comparison_available else None,
            "compare_end_date": prev_end.isoformat() if comparison_available else None,
            "comparison_available": comparison_available,
            "system_go_live_date": SYSTEM_GO_LIVE.isoformat(),
            "case_type": case_type,
            "granularity": granularity,
            "row_count": total,
        },
        "kpis": {
            "total_cases": _metric(total, prev_total),
            "nc_cases": _metric(count(rows, source="NC"), count(prev_rows, source="NC")),
            "ac_cases": _metric(count(rows, source="AC"), count(prev_rows, source="AC")),
            "crisis_cases": _metric(count(rows, priority="Crisis"), count(prev_rows, priority="Crisis")),
            "major_cases": _metric(count(rows, priority="Major"), count(prev_rows, priority="Major")),
            "minor_cases": _metric(count(rows, priority="Minor"), count(prev_rows, priority="Minor")),
            "actual_cost": _metric(actual_cost, prev_actual_cost, decimals=2),
            "estimated_cost": _metric(
                sum(r["estimated_cost"] for r in rows), sum(r["estimated_cost"] for r in prev_rows), decimals=2
            ),
            "cost_per_case": _metric(
                actual_cost / total if total else 0,
                prev_actual_cost / prev_total if prev_total else 0,
                decimals=2,
            ),
            "cases_per_day": _metric(total / span_days, prev_total / span_days, decimals=2),
            "fatalities": _metric(
                sum(r["fatalities"] for r in rows), sum(r["fatalities"] for r in prev_rows)
            ),
            "injuries": _metric(
                sum(r["injured_hospitalized"] + r["injured_not_hospitalized"] for r in rows),
                sum(r["injured_hospitalized"] + r["injured_not_hospitalized"] for r in prev_rows),
            ),
            "open_cases": _metric(status["open_cases"], prev_status["open_cases"]),
            "investigation_coverage_pct": _metric(
                _pct(investigated, total), _pct(prev_investigated, prev_total), decimals=1
            ),
        },
        "highlights": _last_crisis(db),
        "severity_mix": _severity_mix(rows),
        "trend": _trend(rows, granularity),
        "sites": _by_dimension(rows, prev_rows, "site_name"),
        "causes": _causes(rows),
        "root_cause_categories": _root_cause_categories(db, ac_docs),
        "timing": _timing(rows),
        "status": status,
        "safety": _safety(rows),
        "cost": _cost_section(rows, _recovery(db, nc_docs)),
        "entities": {
            "drivers": _by_dimension(rows, prev_rows, "driver_name", limit=10),
            "vehicles": _by_dimension(rows, prev_rows, "truck_no", limit=10),
            "clients": _by_dimension(rows, prev_rows, "client_name", limit=10),
            "concentration": {},
        },
    }

    drivers_all = _by_dimension(rows, prev_rows, "driver_name")
    named_drivers = [d for d in drivers_all if d["name"] != "ไม่ระบุพนักงานขับรถ"]
    repeat = [d for d in named_drivers if d["total"] >= 3]
    payload["entities"]["concentration"] = {
        "driver_count": len(named_drivers),
        "top5_driver_share_pct": _pct(sum(d["total"] for d in named_drivers[:5]), total),
        "repeat_driver_count": len(repeat),
        "repeat_driver_cases": sum(d["total"] for d in repeat),
    }

    payload["insights"] = _build_insights(payload, rows, prev_rows)
    return payload


CASE_LIST_MAX = 500


def _case_row(r: Dict[str, Any]) -> Dict[str, Any]:
    when = r["record_at"]
    return {
        "doc_no": r["doc_no"],
        "source": r["source"],
        "date": when.date().isoformat() if when else None,
        "site_name": r["site_name"],
        "client_name": r["client_name"],
        "driver_name": r["driver_name"],
        "truck_no": r["truck_no"],
        "priority": r["priority"],
        "casestatus": r["casestatus"],
        "cause": r["cause"] or "ไม่ระบุ",
        "actual_cost": r["actual_cost"],
        "estimated_cost": r["estimated_cost"],
    }


@router.get("/cases")
def cases(
    db: Session = Depends(get_db),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    case_type: str = Query("all", pattern="^(all|nc|ac)$"),
    site_id: Optional[List[int]] = Query(None),
    client_id: Optional[List[int]] = Query(None),
    priority: Optional[List[str]] = Query(None),
    casestatus: Optional[List[str]] = Query(None),
    site_name: Optional[str] = Query(None, description="กรองตามชื่อศูนย์ — ต้องตรงกับ 'name' ในแถวมิติของ /overview"),
    driver_name: Optional[str] = Query(None),
    vehicle: Optional[str] = Query(None, description="ทะเบียนรถ (truck_no)"),
    client_name: Optional[str] = Query(None),
    cause: Optional[str] = Query(None),
    weekday: Optional[int] = Query(None, ge=0, le=6, description="0=จันทร์ ... 6=อาทิตย์"),
    hour: Optional[int] = Query(None, ge=0, le=23, description="ชั่วโมงเดียว จาก timing.by_hour"),
    hour_bucket: Optional[str] = Query(None, description="ค่าจาก timing.heatmap เช่น '08-11'"),
    aging_bucket: Optional[str] = Query(None, description="ค่าจาก status.aging เช่น '31-60 วัน'"),
    sort: str = Query("date_desc", pattern="^(date_desc|cost_desc)$"),
    limit: int = Query(100, ge=1, le=CASE_LIST_MAX),
):
    """
    รายการเคสดิบสำหรับ drill-down จาก Dashboard

    ใช้ตัวกรองชุดเดียวกับ /overview (ช่วงวันที่ + case_type + site/priority/status
    ที่กำลังเลือกอยู่บนจอ) บวกตัวกรอง "มิติที่กดดู" อีกหนึ่งชั้น เพื่อให้รายการเคส
    ที่ได้ตรงกับตัวเลขที่ผู้ใช้เห็นบนการ์ด/แถว/แท่งกราฟที่กดไปเป๊ะ ๆ
    """
    today = date.today()
    start = _parse_date(start_date, date(today.year, 1, 1))
    end = _parse_date(end_date, today)
    if end < start:
        raise HTTPException(status_code=400, detail="end_date ต้องไม่น้อยกว่า start_date")

    try:
        rows = _fetch_rows(db, start, end + timedelta(days=1), case_type, site_id, client_id, priority, casestatus)
    except Exception as exc:  # noqa: BLE001
        logger.exception("incident cases query failed")
        raise HTTPException(status_code=500, detail=f"ดึงรายการเคสไม่สำเร็จ: {exc}")

    rows = [r for r in rows if r["record_at"] and start <= r["record_at"].date() <= end]

    if site_name:
        rows = [r for r in rows if r["site_name"] == site_name]
    if driver_name:
        rows = [r for r in rows if r["driver_name"] == driver_name]
    if vehicle:
        rows = [r for r in rows if r["truck_no"] == vehicle]
    if client_name:
        rows = [r for r in rows if r["client_name"] == client_name]
    if cause:
        rows = [r for r in rows if (r["cause"] or "ไม่ระบุ") == cause]

    if weekday is not None or hour is not None or hour_bucket:
        hour_range = next((b for b in HOUR_BUCKETS if b[0] == hour_bucket), None) if hour_bucket else None

        def _in_timing(r: Dict[str, Any]) -> bool:
            when = r["incident_at"]
            if not when:
                return False
            if weekday is not None and when.weekday() != weekday:
                return False
            if hour is not None and when.hour != hour:
                return False
            if hour_range and not (hour_range[1] <= when.hour <= hour_range[2]):
                return False
            return True

        rows = [r for r in rows if _in_timing(r)]

    if aging_bucket:
        # ค้าง (aging) มีความหมายเฉพาะเคสที่ "ยังเปิดอยู่" เท่านั้น — กติกาเดียวกับ
        # _status_and_aging ทุกประการ ไม่งั้นตัวเลขในไดอะล็อกจะไม่ตรงกับการ์ดที่กด
        age_range = next((b for b in AGING_BUCKETS if b[0] == aging_bucket), None)

        def _in_aging(r: Dict[str, Any]) -> bool:
            if r["casestatus"].strip().lower() not in OPEN_STATUSES:
                return False
            when = r["record_at"]
            if not when or not age_range:
                return False
            age = (today - when.date()).days
            return age_range[1] <= age <= age_range[2]

        rows = [r for r in rows if _in_aging(r)]

    rows.sort(
        key=lambda r: r["actual_cost"] if sort == "cost_desc" else (r["record_at"] or datetime.min),
        reverse=True,
    )

    total = len(rows)
    return {"total": total, "truncated": total > limit, "rows": [_case_row(r) for r in rows[:limit]]}


@router.get("/filters")
def filters(db: Session = Depends(get_db)):
    """ตัวเลือกสำหรับ dropdown ของหน้า Dashboard (ดึงจาก master โดยตรง)"""
    sites = db.execute(
        select(Site.site_id, Site.site_code, Site.site_name_th, Site.site_name_en).order_by(Site.site_id)
    ).all()
    clients = db.execute(select(Client.client_id, Client.client_name).order_by(Client.client_name)).all()
    return {
        "sites": [
            {"site_id": s[0], "site_code": s[1], "label": _clean(s[2], s[1] or "-"), "label_en": s[3]}
            for s in sites
        ],
        "clients": [{"client_id": c[0], "label": _clean(c[1])} for c in clients],
        "priorities": PRIORITY_ORDER,
        "case_types": [
            {"value": "all", "label": "ทั้งหมด"},
            {"value": "nc", "label": "NC — Non-Conformance"},
            {"value": "ac", "label": "AC — Accident Case"},
        ],
    }
