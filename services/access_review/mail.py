"""Access Review — approver email (Thai HTML). Not sent yet (Bew 2026-10-29): dispatch() only logs, and the
rendered mail is served by GET /access-review/{id}/mail-preview. To start sending, call
services.email_service.send_email per recipient from dispatch() (on BackgroundTasks).
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
_BASE_URL = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$")


def fe_base_url() -> str:
    value = (os.getenv("FE_BASE_URL") or "").strip().rstrip("/")
    return value if value and _BASE_URL.match(value) else DEFAULT_FE_BASE_URL


def review_link(review_id: int) -> str:
    return f"{fe_base_url()}/access-review/{int(review_id)}"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def render_approver_mail(ctx: dict) -> Tuple[str, str]:
    """ctx: review_id, requester_name, requester_employee_id, department, division, cycle_name, total_lines,
    remove_count, unchecked_count, requester_note. Every value is HTML-escaped."""
    e = lambda v: escape(str(v), quote=True)  # noqa: E731
    name = ctx.get("requester_name") or ctx.get("requester_employee_id") or "-"
    cycle = ctx.get("cycle_name")
    subject = _one_line(f"[ทบทวนสิทธิ์ผู้ใช้] {name} ส่งแบบทบทวนสิทธิ์{f' รอบ {cycle}' if cycle else ''} รอการอนุมัติ")
    rows = [("ผู้ขอ", f"{name} ({ctx.get('requester_employee_id') or '-'})")]
    if ctx.get("department"):
        rows.append(("แผนก", ctx["department"]))
    if ctx.get("division"):
        rows.append(("ฝ่าย", ctx["division"]))
    if cycle:
        rows.append(("รอบการทบทวน", cycle))
    rows.append(("จำนวนสิทธิ์ทั้งหมด", f"{int(ctx.get('total_lines') or 0)} รายการ"))
    rows.append(("ขอยกเลิกทั้งกลุ่มสิทธิ์", f"{int(ctx.get('remove_count') or 0)} กลุ่ม"))
    rows.append(("รายการสิทธิ์ที่ไม่ติ๊ก (ไม่ขอรับสิทธิ์)", f"{int(ctx.get('unchecked_count') or 0)} รายการ"))
    if ctx.get("requester_note"):
        rows.append(("ข้อเสนอแนะ (ผู้แจ้ง)", ctx["requester_note"]))
    table = "".join(
        f'<tr><td style="padding:6px 12px;color:#555;white-space:nowrap">{e(k)}</td>'
        f'<td style="padding:6px 12px;color:#111">{e(v)}</td></tr>'
        for k, v in rows
    )
    link = review_link(ctx["review_id"])
    html = f"""<!DOCTYPE html>
<html lang="th"><body style="margin:0;padding:24px;background:#f4f5f7;font-family:Tahoma,Arial,sans-serif">
<div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #e3e5e8;border-radius:8px;overflow:hidden">
  <div style="padding:16px 20px;border-bottom:1px solid #e3e5e8;font-size:16px;font-weight:bold;color:#111">
    แบบทบทวนสิทธิ์ผู้ใช้รอการอนุมัติ
  </div>
  <div style="padding:16px 20px;font-size:14px;color:#333">
    <p style="margin:0 0 12px">เรียนผู้อนุมัติ</p>
    <p style="margin:0 0 12px">{e(name)} ได้ส่งแบบทบทวนสิทธิ์การใช้งานระบบ (ATMS / Winspeed / Email Group)
    กรุณาตรวจสอบและพิจารณาอนุมัติ</p>
    <table style="border-collapse:collapse;font-size:14px;margin:0 0 16px">{table}</table>
    <a href="{e(link)}" style="display:inline-block;padding:10px 18px;background:#1f4e79;color:#fff;
    text-decoration:none;border-radius:4px">เปิดแบบทบทวนสิทธิ์</a>
    <p style="margin:16px 0 0;font-size:12px;color:#888">อีเมลนี้ส่งอัตโนมัติจากระบบ กรุณาอย่าตอบกลับ</p>
  </div>
</div>
</body></html>"""
    return subject, html


def pick_recipients(to: Iterable[Optional[str]]) -> List[str]:
    out = list(dict.fromkeys(a.strip() for a in to if a and a.strip() and "@" in a))
    return out[:MAX_RECIPIENTS]


def dispatch(to: Iterable[Optional[str]], subject: str, html: str,
             background_tasks: Optional[BackgroundTasks]) -> bool:
    """Not sending yet — logs only and returns False (the caller still stamps mailed_at)."""
    recipients = pick_recipients(to)
    log.info("access review mail not sent (preview only): %s → %d recipient(s)", subject, len(recipients))
    return False
