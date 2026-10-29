"""อีเมลแจ้งเตือนของเบิกเงินสดย่อย

ส่งผ่านตัวเดิมของ Advance (finance_mail.send_finance_email) จึงปิด/เปิดด้วย
FINANCE_EMAIL_ENABLED ตัวเดียวกัน · ปลายทาง **รวมเจ้าหน้าที่ผู้เปิดใบทุกครั้ง**
เพราะคนที่ต้องแก้คือเจ้าหน้าที่ ไม่ใช่ผู้อนุมัติ
"""
from __future__ import annotations

import logging
from html import escape
from typing import Iterable, Mapping, Optional

from services.finance import finance_mail as mail

log = logging.getLogger(__name__)


def gate_recipients(clerk_email: Optional[str], approver_email: Optional[str]) -> list[str]:
    out: list[str] = []
    for address in (clerk_email, approver_email):
        if address and address not in out:
            out.append(address)
    return out


def _bullets(lines: Iterable[str]) -> str:
    items = "".join(f"<li>{escape(line)}</li>" for line in lines)
    return f"<ul>{items}</ul>" if items else ""


def _frame(title: str, form_id: str, body: str) -> str:
    return (
        f"<div style='font-family:sans-serif;font-size:14px;line-height:1.6'>"
        f"<p style='font-size:16px;margin:0 0 8px'><strong>{escape(title)}</strong></p>"
        f"<p style='margin:0 0 8px'>ใบเบิกเงินสดย่อยเลขที่ <strong>{escape(form_id)}</strong></p>"
        f"{body}</div>"
    )


def render_gate_blocked(form_id: str, missing: Iterable[Mapping]) -> str:
    lines = [str(m.get("label") or m.get("key") or "") for m in missing]
    return _frame("เอกสารไม่ครบ ยังอนุมัติไม่ได้", form_id,
                  "<p style='margin:0 0 4px'>สิ่งที่ยังขาด</p>" + _bullets(lines)
                  + "<p style='margin:8px 0 0'>แนบเอกสารหรือแก้ไขให้ครบ แล้วส่งให้ผู้อนุมัติอีกครั้ง</p>")


def render_returned(form_id: str, reason: Optional[str]) -> str:
    body = (f"<p style='margin:0'>เหตุผล: {escape(reason)}</p>" if reason
            else "<p style='margin:0'>ผู้อนุมัติไม่ได้ระบุเหตุผล</p>")
    return _frame("ผู้อนุมัติตีกลับใบเบิก", form_id, body)


def render_duplicate(form_id: str, duplicates: Iterable[Mapping]) -> str:
    lines = []
    for dup in duplicates:
        line = str(dup.get("message") or "อาจซ้ำ")
        if dup.get("line_no"):
            line = f"แถวที่ {dup['line_no']}: {line}"
        if dup.get("form_id"):
            line = f"{line} (ใบ {dup['form_id']})"
        lines.append(line)
    return _frame("มีรายการที่อาจซ้ำ", form_id,
                  _bullets(lines)
                  + "<p style='margin:8px 0 0'>ถ้าถูกต้องให้ผู้อนุมัติยืนยันทับพร้อมเหตุผลได้</p>")


def notify_gate_blocked(form_id: str, missing, clerk_email=None, approver_email=None) -> None:
    to = gate_recipients(clerk_email, approver_email)
    if not to:
        log.info("petty gate email skipped (no recipients): %s", form_id)
        return
    mail.send_finance_email(to, f"[เงินสดย่อย] เอกสารไม่ครบ {form_id}",
                            render_gate_blocked(form_id, missing))


def notify_returned(form_id: str, reason, clerk_email=None) -> None:
    to = gate_recipients(clerk_email, None)
    if not to:
        return
    mail.send_finance_email(to, f"[เงินสดย่อย] ตีกลับใบ {form_id}", render_returned(form_id, reason))


def notify_duplicate(form_id: str, duplicates, clerk_email=None, approver_email=None) -> None:
    to = gate_recipients(clerk_email, approver_email)
    if not to:
        return
    mail.send_finance_email(to, f"[เงินสดย่อย] รายการอาจซ้ำ {form_id}",
                            render_duplicate(form_id, duplicates))


def _round_frame(title: str, batch_no: str, body: str) -> str:
    return (
        f"<div style='font-family:sans-serif;font-size:14px;line-height:1.6'>"
        f"<p style='font-size:16px;margin:0 0 8px'><strong>{escape(title)}</strong></p>"
        f"<p style='margin:0 0 8px'>รอบส่งเคลียร์เงินสดย่อย <strong>{escape(batch_no)}</strong></p>"
        f"{body}</div>"
    )


def _round_link(batch_id: int) -> str:
    import os
    url = mail.build_link(os.getenv("FE_BASE_URL"), f"/finance/petty/rounds/{int(batch_id)}")
    return (f"<p style='margin:8px 0 0'><a href='{escape(url, quote=True)}'>เปิดดูรอบนี้</a></p>"
            if url else "")


def render_round_submitted(batch_id: int, batch_no: str, holder_name: Optional[str], count: int, total) -> str:
    who = escape(holder_name) if holder_name else "เจ้าของเงินสด"
    return _round_frame("มีรอบเงินสดย่อยรอคุณอนุมัติเคลียร์", batch_no,
                        f"<p style='margin:0'>{who} ส่งมา {int(count)} ใบ รวม {float(total):,.2f} บาท</p>"
                        + _round_link(batch_id))


def render_round_rejected(batch_id: int, batch_no: str, reason: str) -> str:
    return _round_frame("หัวหน้าไม่อนุมัติเคลียร์รอบนี้", batch_no,
                        f"<p style='margin:0'>เหตุผล: {escape(reason)}</p>"
                        "<p style='margin:8px 0 0'>ใบในรอบกลับไปรอส่งเคลียร์ แก้แล้วจัดรอบใหม่ได้เลย</p>"
                        + _round_link(batch_id))


def notify_round_submitted(batch_id: int, batch_no: str, holder_name, count: int, total, to: list) -> None:
    to = [a for a in dict.fromkeys(to or []) if a]
    if not to:
        log.info("petty round email skipped (no head email): %s", batch_no)
        return
    mail.send_finance_email(to, f"[เงินสดย่อย] รออนุมัติเคลียร์ {batch_no}",
                            render_round_submitted(batch_id, batch_no, holder_name, count, total))


def notify_round_rejected(batch_id: int, batch_no: str, reason: str, holder_email=None) -> None:
    if not holder_email:
        return
    mail.send_finance_email([holder_email], f"[เงินสดย่อย] ไม่อนุมัติเคลียร์ {batch_no}",
                            render_round_rejected(batch_id, batch_no, reason))
