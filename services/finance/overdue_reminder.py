"""Overdue clearing reminder email (finance v3 §7b).

* Pure helpers (`due_for_reminder`, `thai_long_date`, `render_overdue_email`) are DB-free and unit tested.
* `run_overdue_reminders(db_factory)` is the daily 09:00 Asia/Bangkok scheduler job: a no-op while
  FINANCE_EMAIL_ENABLED is not true, never raises, writes an OVERDUE_REMIND fin log (remark "auto",
  action_by "system") only after a successful send.
* `send_for_items` is shared with the manual endpoint (it ignores the 7-day interval).
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from html import escape

from models.finance_model import FinAdvance, FinAdvanceLog
from models.user_model import User
from services.finance import advance_repo as repo
from services.finance import finance_mail as mail

log = logging.getLogger(__name__)

ACTION = "OVERDUE_REMIND"
INTERVAL_DAYS = 7

_THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม",
                "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]

SUBJECT_PREFIX = "แจ้งเตือน/ติดตาม ยอดเงินยืมทดรองจ่ายเกินกำหนดชำระ"

# The user's template, verbatim; {placeholders} are filled with HTML-escaped values.
_P1 = ("อีเมลฉบับนี้ส่งมาเพื่อแจ้งเตือนเกี่ยวกับยอดเงินยืมทดรองจ่าย จากใบคำขอเบิกเงิน {form_id} จำนวน {amount} บาท "
       "ซึ่งได้ครบกำหนดชำระและส่งมอบใบเสร็จเพื่อเคลียร์ค่าใช้จ่าย เมื่อวันที่ {due} ที่ผ่านมา")
_P2 = ("เนื่องจากเลยกำหนดเวลาดังกล่าวมาแล้ว ทางฝ่ายบัญชีจึงขอความกรุณาคุณ {name} "
       "ช่วยดำเนินการอย่างใดอย่างหนึ่งดังต่อไปนี้:")
_B1 = ("กรณีนำไปใช้จ่ายแล้ว: กรุณากรอกข้อมูลเคลียร์เงินในระบบขอเบิกเงินทดรองจ่าย "
       "พร้อมส่งมอบเอกสารฉบับจริงใบเสร็จรับเงินและเอกสารประกอบเพื่อเคลียร์ค่าใช้จ่าย")
_B2 = ("กรณีมีเงินคงเหลือหรือไม่ได้ใช้จ่าย: กรุณาดำเนินการคืนเงินส่วนที่เหลือเข้าบัญชีบริษัท "
       "และกรอกข้อมูลเคลียร์เงินในระบบขอเบิกเงินทดรองจ่าย พร้อมแนบหลักฐานการโอนเงิน "
       "และส่งมอบเอกสารฉบับจริงใบเสร็จรับเงิน หลักฐานการโอนเงินและเอกสารประกอบเพื่อเคลียร์ค่าใช้จ่าย")
_P3 = ("หากคุณ {name} มีข้อติดขัด หรือต้องการสอบถามรายละเอียดเพิ่มเติม "
       "สามารถติดต่อฝ่ายบัญชีได้โดยตรงตามอีเมล accountbkk@menatransport.co.th ค่ะ")
_P4 = "ขอขอบคุณสำหรับความร่วมมือค่ะ"


# ---------------------------- pure helpers ----------------------------

def thai_long_date(value) -> str:
    """12 ตุลาคม 2569 (Buddhist year = CE + 543). Accepts a date/datetime or an ISO 'YYYY-MM-DD…' string."""
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    if isinstance(value, datetime):
        value = value.date()
    return f"{value.day} {_THAI_MONTHS[value.month - 1]} {value.year + 543}"


def due_for_reminder(today: date, clear_due_date, last_reminded, interval: int = INTERVAL_DAYS) -> bool:
    if clear_due_date is None or (today - clear_due_date).days < interval:
        return False
    return last_reminded is None or (today - last_reminded).days >= interval


def _money(value) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "-"


def render_overdue_email(ctx: dict):
    """ctx: firstname, lastname, form_id, amount_paid, clear_due_date, fe_base_url. Returns (subject, html)."""
    e = lambda v: escape(str(v), quote=True)  # noqa: E731
    first = (ctx.get("firstname") or "").strip()
    full = f"{first} {(ctx.get('lastname') or '').strip()}".strip()
    form_id = ctx.get("form_id") or "-"
    subject = mail._one_line(f"{SUBJECT_PREFIX} {full}")
    fill = {"form_id": e(form_id), "amount": e(_money(ctx.get("amount_paid"))),
            "due": e(thai_long_date(ctx["clear_due_date"])) if ctx.get("clear_due_date") else "-",
            "name": e(first)}
    p = 'style="margin:0 0 14px;font-size:14px;line-height:1.7;color:#1f2937"'
    link = mail.build_link(ctx.get("fe_base_url"), f"/finance/advance/{mail._q(form_id)}")
    button = ""
    if link:
        button = ('<p style="margin:18px 0"><a href="' + escape(link, quote=True) + '" style="display:inline-block;'
                  'padding:11px 22px;background:#1a7a36;color:#ffffff;text-decoration:none;border-radius:8px;'
                  'font-size:14px;font-weight:700">เปิดรายการในระบบ</a></p>')
    html = (
        '<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8"></head>'
        '<body style="margin:0;padding:24px;background-color:#ffffff;font-family:Tahoma,Arial,sans-serif">'
        f'<p {p}>เรียน คุณ {fill["name"]},</p>'
        f'<p {p}>{_P1.format(**fill)}</p>'
        f'<p {p}>{_P2.format(**fill)}</p>'
        f'<ul style="margin:0 0 14px;padding-left:22px;font-size:14px;line-height:1.7;color:#1f2937">'
        f'<li>{_B1}</li><li>{_B2}</li></ul>'
        f'<p {p}>{_P3.format(**fill)}</p>'
        f'{button}'
        f'<p {p}>{_P4}</p>'
        '</body></html>'
    )
    return subject, html


# ---------------------------- data + sending ----------------------------

def load_overdue(db, form_ids=None):
    """All overdue advances (AWAITING_CLEARING / SENT_BACK, clear_due_date < today Bangkok), optionally narrowed
    to `form_ids`, with requester name/email, advance id and last reminder resolved in a fixed number of
    queries. Returns (items, today) where each item is a dict."""
    items = repo.list_advances(db, overdue=True)
    if form_ids is not None:
        items = [i for i in items if i["form_id"] in set(form_ids)]
    if not items:
        return [], repo.today_bkk()
    subs = [i["submission_id"] for i in items]
    adv_ids = {sid: aid for sid, aid in
               db.query(FinAdvance.submission_id, FinAdvance.id).filter(FinAdvance.submission_id.in_(subs)).all()}
    emp_ids = {i["requester"]["employee_id"] for i in items}
    users = {u.employee_id: u for u in db.query(User).filter(User.employee_id.in_(emp_ids)).all()}
    last = {}
    ids = list(adv_ids.values())
    if ids:
        from sqlalchemy import func
        for aid, ts in (db.query(FinAdvanceLog.advance_id, func.max(FinAdvanceLog.created_at))
                        .filter(FinAdvanceLog.advance_id.in_(ids), FinAdvanceLog.action == ACTION)
                        .group_by(FinAdvanceLog.advance_id).all()):
            last[aid] = ts
    out = []
    for i in items:
        user = users.get(i["requester"]["employee_id"])
        aid = adv_ids.get(i["submission_id"])
        ts = last.get(aid)
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                from datetime import timezone
                ts = ts.replace(tzinfo=timezone.utc)
            ts = ts.astimezone(repo.BKK).date()
        out.append({
            "form_id": i["form_id"], "advance_id": aid,
            "firstname": getattr(user, "firstname", None), "lastname": getattr(user, "lastname", None),
            "email": getattr(user, "email", None),
            "amount_paid": i["fin"]["amount_paid"] if i["fin"] else None,
            "clear_due_date": date.fromisoformat(i["fin"]["clear_due_date"][:10]),
            "last_reminded": ts,
        })
    return out, repo.today_bkk()


def send_one(item: dict) -> bool:
    """Render and send one reminder to the requester only. True only when SMTP accepted it."""
    to = mail.pick_recipients([item.get("email")])
    if not to:
        return False
    subject, html = render_overdue_email({**item, "fe_base_url": os.getenv("FE_BASE_URL")})
    return mail.deliver_finance_email(to, subject, html) > 0


def send_for_items(db, items, remark: str, action_by: str, send=None):
    """Send each item; after a successful send add the OVERDUE_REMIND log and commit. Per-item errors are
    swallowed. Returns (sent, skipped[{form_id, reason}])."""
    send = send or send_one
    sent, skipped = 0, []
    for item in items:
        try:
            if not (item.get("email") or "").strip():
                log.info("overdue reminder %s skipped: requester has no email", item["form_id"])
                skipped.append({"form_id": item["form_id"], "reason": "ผู้เบิกไม่มีอีเมล"})
                continue
            if not send(item):
                skipped.append({"form_id": item["form_id"], "reason": "ส่งอีเมลไม่สำเร็จ"})
                continue
            db.add(FinAdvanceLog(advance_id=item["advance_id"], action=ACTION, remark=remark, action_by=action_by))
            db.commit()
            sent += 1
        except Exception:  # noqa: BLE001 - one item must not stop the rest
            log.exception("overdue reminder %s failed", item.get("form_id"))
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            skipped.append({"form_id": item.get("form_id"), "reason": "เกิดข้อผิดพลาด"})
    return sent, skipped


def run_overdue_reminders(db_factory) -> None:
    """Daily job. No-op (no queries, no logs) while emails are off. Never raises."""
    if not mail.email_enabled():
        return
    db = None
    try:
        db = db_factory()
        items, today = load_overdue(db)
        due = [i for i in items if due_for_reminder(today, i["clear_due_date"], i["last_reminded"])]
        sent, skipped = send_for_items(db, due, "auto", "system")
        log.info("overdue reminders: %d due, %d sent, %d skipped", len(due), sent, len(skipped))
    except Exception:  # noqa: BLE001
        log.exception("overdue reminder job failed")
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:  # noqa: BLE001
            pass
