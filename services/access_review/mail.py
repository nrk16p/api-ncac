"""Access Review — approver email (Thai HTML). Sent on submit to the review's approvers (Bew 2026-11-05):
dispatch() queues ONE services.email_service.send_email on BackgroundTasks, shaped like the IT form mail
(Bew 2026-11-06): To = first approver, Cc = the other approvers + the requester + itcenter@. Bypass and it_queue reviews have no
approvers, so nothing is sent. GET /access-review/{id}/mail-preview renders the same mail.
The link uses FE_BASE_URL (same env as finance mail), default menait-service.vercel.app.
"""
from __future__ import annotations

import logging
import os
import re
from html import escape
from typing import Iterable, List, Optional, Tuple

from fastapi import BackgroundTasks


log = logging.getLogger(__name__)

DEFAULT_FE_BASE_URL = "https://menait-service.vercel.app"
MAX_RECIPIENTS = 20
IT_CC = "itcenter@menatransport.co.th"
SUBJECT_PREFIX = "[IT ทบทวนสิทธิ์]"
EMAIL_SYSTEM = "EMAIL"  # whole-role system — the mail lists its group names instead of item counts
_BASE_URL = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$")


def fe_base_url() -> str:
    value = (os.getenv("FE_BASE_URL") or "").strip().rstrip("/")
    return value if value and _BASE_URL.match(value) else DEFAULT_FE_BASE_URL


def review_link(review_id: int) -> str:
    return f"{fe_base_url()}/access-review/{int(review_id)}"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


_LABEL_TD = 'style="padding:6px 12px;color:#555;white-space:nowrap;vertical-align:top;width:45%"'
_VALUE_TD = 'style="padding:6px 12px;color:#111;vertical-align:top"'
_SECTION_TD = ('colspan="2" style="padding:10px 12px 6px;font-weight:bold;color:#1f4e79;'
               'border-bottom:1px solid #e3e5e8"')


def render_approver_mail(ctx: dict) -> Tuple[str, str]:
    """ctx: review_id, requester_name, requester_employee_id, department, division, cycle_name, total_lines,
    remove_count, unchecked_count, requested_count, requester_note. Every value is HTML-escaped."""
    e = lambda v: escape(str(v), quote=True)  # noqa: E731
    emp_id = ctx.get("requester_employee_id") or "-"
    raw_name = ctx.get("requester_name")
    name = raw_name or emp_id
    polite = f"คุณ{raw_name}" if raw_name else name  # "คุณ" only before a real name, never an employee id
    cycle = ctx.get("cycle_name")
    cycle_text = f" ประจำรอบ {cycle}" if cycle else ""
    subject = _one_line(f"{SUBJECT_PREFIX} แบบทบทวนสิทธิ์การใช้งานระบบของ {polite}{cycle_text} รอการพิจารณาอนุมัติ")

    systems = ctx.get("systems") or []
    red, green, grey = "#b42318", "#067647", "#888"

    def counts_cell(s: dict) -> str:
        """"ยืนยัน kept/total รายการ" — red when something was dropped; plus "ขอเพิ่ม n รายการ" in green."""
        kept, total = int(s.get("kept") or 0), int(s.get("total") or 0)
        color = red if kept < total else "#111"
        out = f'<span style="font-weight:bold;color:{color}">{kept:,}/{total:,}</span> รายการ'
        if kept < total:
            out += f' <span style="color:{red}">(ขอยกเลิก {total - kept:,} รายการ)</span>'
        if s.get("requested"):
            out += f'<br><span style="color:{green}">ขอเพิ่ม {int(s["requested"]):,} รายการ</span>'
        return out

    def groups_cell(s: dict) -> str:
        """Email groups one per line: kept plain, removed struck through, requested in green."""
        parts = []
        for group, state in s.get("groups") or []:
            if state == "remove":
                parts.append(f'<span style="color:{red};text-decoration:line-through">{e(group)}</span>'
                             f' <span style="color:{red}">(ขอยกเลิก)</span>')
            elif state == "request":
                parts.append(f'<span style="color:{green}">{e(group)} (ขอเพิ่ม)</span>')
            else:
                parts.append(e(group))
        return "<br>".join(parts) or f'<span style="color:{grey}">-</span>'

    requester_rows = [("ชื่อ-นามสกุล", e(name)), ("รหัสพนักงาน", e(emp_id))]
    if ctx.get("department"):
        requester_rows.append(("แผนก", e(ctx["department"])))
    if ctx.get("division"):
        requester_rows.append(("ฝ่าย", e(ctx["division"])))
    if cycle:
        requester_rows.append(("รอบการทบทวน", e(cycle)))

    summary_rows = [("ระบบที่ทบทวน", e(", ".join(s["name"] for s in systems)) or "-")]
    for s in systems:
        if s.get("code") == EMAIL_SYSTEM:
            summary_rows.append((f"{e(s['name'])}", groups_cell(s)))
        else:
            summary_rows.append((f"{e(s['name'])}<br><span style=\"font-size:12px;color:{grey}\">"
                                 "(จำนวนที่ยืนยัน/จำนวนเดิม)</span>", counts_cell(s)))
    if ctx.get("requester_note"):
        summary_rows.append(("ความเห็นเพิ่มเติมจากผู้ขอ", e(ctx["requester_note"]).replace("\n", "<br>")))

    def section(title: str, rows: list) -> str:
        # Labels are fixed strings in this file (some carry markup); values are escaped above.
        body = "".join(f"<tr><td {_LABEL_TD}>{k}</td><td {_VALUE_TD}>{v}</td></tr>" for k, v in rows)
        return f"<tr><td {_SECTION_TD}>{e(title)}</td></tr>{body}"

    table = section("ข้อมูลผู้ขอ", requester_rows) + section("สรุปผลการทบทวนสิทธิ์", summary_rows)
    link = e(review_link(ctx["review_id"]))
    html = f"""<!DOCTYPE html>
<html lang="th"><body style="margin:0;padding:24px;background:#f4f5f7;font-family:Tahoma,Arial,sans-serif">
<div style="max-width:600px;margin:0 auto;background:#fff;border:1px solid #e3e5e8;border-radius:8px;overflow:hidden">
  <div style="padding:16px 20px;background:#1f4e79;color:#fff">
    <div style="font-size:16px;font-weight:bold">แบบทบทวนสิทธิ์การใช้งานระบบ</div>
    <div style="font-size:13px;opacity:.85;margin-top:2px">รอการพิจารณาอนุมัติ</div>
  </div>
  <div style="padding:20px;font-size:14px;line-height:1.6;color:#333">
    <p style="margin:0 0 12px">เรียนผู้อนุมัติ</p>
    <p style="margin:0 0 16px">{e(name)} ได้ส่งแบบทบทวนสิทธิ์การใช้งานระบบ (ATMS / Winspeed / Email Group)
    กรุณาตรวจสอบและพิจารณาอนุมัติ</p>
    <table style="border-collapse:collapse;font-size:14px;width:100%;margin:0 0 20px">{table}</table>
    <a href="{link}" style="display:inline-block;padding:10px 20px;background:#1f4e79;color:#fff;
    text-decoration:none;border-radius:4px;font-weight:bold">ตรวจสอบและพิจารณาอนุมัติ</a>
  </div>
  <div style="padding:12px 20px;border-top:1px solid #e3e5e8;background:#fafbfc;font-size:12px;color:#888">
    อีเมลฉบับนี้ส่งโดยอัตโนมัติจากระบบ กรุณาอย่าตอบกลับ หากมีข้อสงสัยกรุณาติดต่อ {e(IT_CC)}
  </div>
</div>
</body></html>"""
    return subject, html


def pick_recipients(to: Iterable[Optional[str]]) -> List[str]:
    out = list(dict.fromkeys(a.strip() for a in to if a and a.strip() and "@" in a))
    return out[:MAX_RECIPIENTS]


def mail_cc(to: List[str], extra: Iterable[Optional[str]] = ()) -> List[str]:
    """Cc for a mail whose To is to[0]: the other approvers, then `extra` (the requester), then itcenter@ —
    valid addresses only, none twice (case-insensitive)."""
    seen = {to[0].lower()}
    out = []
    for a in to[1:] + pick_recipients(extra) + [IT_CC]:
        if a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def _send(to_email: str, subject: str, html: str, cc: List[str]) -> None:
    """A failure is logged and never breaks the submit."""
    from services import email_service  # looked up at call time so tests can stub send_email
    try:
        email_service.send_email(to_email, subject, html, cc)
        log.info("access review mail sent: %s → %s cc %s", subject, to_email, cc)
    except Exception:  # noqa: BLE001 — SMTP errors must not break the submit
        log.exception("access review mail failed: %s → %s cc %s", subject, to_email, cc)


def dispatch(to: Iterable[Optional[str]], subject: str, html: str,
             background_tasks: Optional[BackgroundTasks], cc: Iterable[Optional[str]] = ()) -> bool:
    """Queue one mail to every approver, Cc `cc` + itcenter@ (sent after the response).
    Returns True when it was queued; no approver address → nothing is sent."""
    recipients = pick_recipients(to)
    if not recipients:
        return False
    cc = mail_cc(recipients, cc)
    if background_tasks is not None:
        background_tasks.add_task(_send, recipients[0], subject, html, cc)
    else:
        _send(recipients[0], subject, html, cc)
    return True
