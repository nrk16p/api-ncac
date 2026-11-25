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


PREFIX = "[Petty Cash]"  # หัวเรื่องอีเมล (ผู้ใช้สั่ง 25/11/2026 เดิม [เงินสดย่อย])


def _e(value) -> str:
    return escape(str(value), quote=True)


def _money(amount) -> str:
    try:
        return f"{float(amount):,.2f} บาท"
    except (TypeError, ValueError):
        return "-"


def _bullets(lines: Iterable[str]) -> str:
    items = "".join(f'<li style="margin:0 0 4px">{_e(line)}</li>' for line in lines if line)
    return (f'<ul style="margin:16px 0 0;padding-left:20px;font-size:14px;color:#1f2937;line-height:1.6">{items}</ul>'
            if items else "")


def _note(text: str) -> str:
    return f'<p style="margin:16px 0 0;font-size:14px;color:#1f2937;line-height:1.6">{_e(text)}</p>'


def _card(badge: str, headline: str, sub: str, rows, link: Optional[str] = None,
          button: str = "เปิดรายการ", extra: str = "") -> str:
    """การ์ดหน้าตาเดียวกับอีเมล Advance (finance_mail.render_event_email): แถบเขียว หัวเรื่อง ตารางสรุป ปุ่มเปิดรายการ"""
    table = "".join(
        f'<tr><td style="padding:6px 16px 6px 0;font-size:13px;color:#6b7280;white-space:nowrap;vertical-align:top">{_e(k)}</td>'
        f'<td style="padding:6px 0;font-size:14px;color:#1f2937">{_e(v)}</td></tr>' for k, v in rows if v not in (None, ""))
    btn = ""
    if link:
        btn = ('<p style="margin:24px 0 0"><a href="' + escape(link, quote=True) + '" style="display:inline-block;'
               'padding:11px 22px;background:#1a7a36;color:#ffffff;text-decoration:none;border-radius:8px;'
               f'font-size:14px;font-weight:700">{_e(button)}</a></p>')
    return (
        '<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8"></head>'
        '<body style="margin:0;padding:0;background-color:#eef2f7;font-family:Tahoma,Arial,sans-serif">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#eef2f7;'
        'padding:32px 16px 32px 24px"><tr><td align="left">'
        '<div style="font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#4a5568;'
        'margin-bottom:16px">&#9679; Finance &mdash; Petty Cash</div>'
        '<table width="620" cellpadding="0" cellspacing="0" border="0" style="max-width:620px;width:100%;'
        'background-color:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e0e6ed">'
        '<tr><td style="background:#2ea84f;padding:28px 36px">'
        f'<div style="font-size:11px;font-weight:700;color:#ffffff;letter-spacing:0.1em;margin-bottom:10px">{_e(badge)}</div>'
        f'<div style="font-size:24px;font-weight:700;color:#ffffff;margin-bottom:6px">{_e(headline)}</div>'
        f'<div style="font-size:13px;color:#e8f5ec">{_e(sub)}</div></td></tr>'
        f'<tr><td style="padding:28px 36px"><table cellpadding="0" cellspacing="0" border="0">{table}</table>'
        f'{extra}{btn}</td></tr></table></td></tr></table></body></html>'
    )


def _claim_url(form_id: str) -> Optional[str]:
    import os
    return mail.build_link(os.getenv("FE_BASE_URL"), f"/finance/petty/{form_id}")


def _round_url(batch_id: int) -> Optional[str]:
    import os
    return mail.build_link(os.getenv("FE_BASE_URL"), f"/finance/petty/rounds/{int(batch_id)}")


def render_gate_blocked(form_id: str, missing: Iterable[Mapping]) -> str:
    lines = [str(m.get("label") or m.get("key") or "") for m in missing]
    return _card("เอกสารไม่ครบ", "ยังอนุมัติไม่ได้", "แนบเอกสารหรือแก้ให้ครบ แล้วส่งให้ผู้อนุมัติอีกครั้ง",
                 [("เลขที่", form_id), ("สิ่งที่ยังขาด", f"{len(lines)} รายการ")],
                 _claim_url(form_id), extra=_bullets(lines))


def render_returned(form_id: str, reason: Optional[str]) -> str:
    return _card("ตีกลับ", "ผู้อนุมัติตีกลับใบเบิก", "แก้ตามเหตุผลด้านล่าง แล้วส่งใหม่",
                 [("เลขที่", form_id), ("เหตุผล", reason or "ผู้อนุมัติไม่ได้ระบุเหตุผล")], _claim_url(form_id))


def render_duplicate(form_id: str, duplicates: Iterable[Mapping]) -> str:
    lines = []
    for dup in duplicates:
        line = str(dup.get("message") or "อาจซ้ำ")
        if dup.get("line_no"):
            line = f"แถวที่ {dup['line_no']}: {line}"
        if dup.get("form_id"):
            line = f"{line} (ใบ {dup['form_id']})"
        lines.append(line)
    return _card("รายการอาจซ้ำ", "มีรายการที่อาจซ้ำ", "ถ้าถูกต้อง ผู้อนุมัติยืนยันทับพร้อมเหตุผลได้",
                 [("เลขที่", form_id)], _claim_url(form_id), extra=_bullets(lines))


def notify_gate_blocked(form_id: str, missing, clerk_email=None, approver_email=None) -> None:
    to = gate_recipients(clerk_email, approver_email)
    if not to:
        log.info("petty gate email skipped (no recipients): %s", form_id)
        return
    mail.send_finance_email(to, f"{PREFIX} เอกสารไม่ครบ {form_id}",
                            render_gate_blocked(form_id, missing))


def notify_returned(form_id: str, reason, clerk_email=None) -> None:
    to = gate_recipients(clerk_email, None)
    if not to:
        return
    mail.send_finance_email(to, f"{PREFIX} ตีกลับใบ {form_id}", render_returned(form_id, reason))


def notify_duplicate(form_id: str, duplicates, clerk_email=None, approver_email=None) -> None:
    to = gate_recipients(clerk_email, approver_email)
    if not to:
        return
    mail.send_finance_email(to, f"{PREFIX} รายการอาจซ้ำ {form_id}",
                            render_duplicate(form_id, duplicates))


def render_head_pending(form_id: str, requester_name: Optional[str], amount) -> str:
    return _card("เงินสดย่อย", "รอท่านอนุมัติ", "มีใบเบิกเงินสดย่อยรอท่านพิจารณา อนุมัติแล้วใบจะไปที่เจ้าของเงินสดเพื่อโอนเงิน",
                 [("เลขที่", form_id), ("ผู้ขอเบิก", requester_name or "-"), ("ยอดเบิก", _money(amount)),
                  ("สถานะ", "รอหัวหน้าอนุมัติ")], _claim_url(form_id))


def render_head_approved(form_id: str, head_name: Optional[str], amount, direct: bool = False) -> str:
    if direct:
        # TOA ให้เจ้าของเงินสดอนุมัติเอง ไม่มีขั้นหัวหน้า — head_name ตรงนี้คือชื่อผู้ขอเบิก
        return _card("ใบเบิกใหม่", "รอท่านอนุมัติจ่าย", "ยอดอยู่ในอำนาจอนุมัติของท่านตาม TOA ไม่ต้องผ่านหัวหน้า",
                     [("เลขที่", form_id), ("ผู้ขอเบิก", head_name or "-"), ("ยอดเบิก", _money(amount)),
                      ("สถานะ", "รอเจ้าของเงินสดอนุมัติจ่าย")], _claim_url(form_id))
    return _card("หัวหน้าอนุมัติแล้ว", "รอท่านอนุมัติจ่าย", "หัวหน้าอนุมัติใบนี้แล้ว ตรวจแล้วโอนเงินพร้อมแนบสลิป",
                 [("เลขที่", form_id), ("หัวหน้าผู้อนุมัติ", head_name or "-"), ("ยอดเบิก", _money(amount)),
                  ("สถานะ", "รอเจ้าของเงินสดอนุมัติจ่าย")], _claim_url(form_id))


def notify_head_pending(form_id: str, requester_name, amount, to: list) -> None:
    to = [a for a in dict.fromkeys(to or []) if a]
    if not to:
        log.info("petty head email skipped (no head email): %s", form_id)
        return
    mail.send_finance_email(to, f"{PREFIX} รอหัวหน้าอนุมัติ {form_id}",
                            render_head_pending(form_id, requester_name, amount))


def notify_head_approved(form_id: str, head_name, amount, holder_email=None, direct: bool = False) -> None:
    """direct=True = ใบข้ามขั้นหัวหน้าตาม TOA มาถึงเจ้าของเงินสดเลย (head_name = ชื่อผู้ขอเบิก)"""
    if not holder_email:
        return
    mail.send_finance_email([holder_email], f"{PREFIX} รออนุมัติจ่าย {form_id}",
                            render_head_approved(form_id, head_name, amount, direct=direct))


def render_round_submitted(batch_id: int, batch_no: str, holder_name: Optional[str], count: int, total) -> str:
    return _card("รอบส่งเคลียร์", "รอท่านอนุมัติเคลียร์", "เจ้าของเงินสดส่งรอบเคลียร์มาแล้ว ตรวจรายการก่อนส่งต่อให้บัญชี",
                 [("รอบ", batch_no), ("เจ้าของเงินสด", holder_name or "-"), ("จำนวนใบ", f"{int(count)} ใบ"),
                  ("ยอดรวม", _money(total)), ("สถานะ", "รอหัวหน้าอนุมัติเคลียร์")],
                 _round_url(batch_id), button="เปิดรอบนี้")


def render_round_rejected(batch_id: int, batch_no: str, reason: str) -> str:
    return _card("ไม่อนุมัติเคลียร์", "หัวหน้าไม่อนุมัติเคลียร์รอบนี้", "ใบในรอบกลับไปรอส่งเคลียร์ แก้แล้วจัดรอบใหม่ได้เลย",
                 [("รอบ", batch_no), ("เหตุผล", reason or "-")], _round_url(batch_id), button="เปิดรอบนี้")


def notify_round_submitted(batch_id: int, batch_no: str, holder_name, count: int, total, to: list) -> None:
    to = [a for a in dict.fromkeys(to or []) if a]
    if not to:
        log.info("petty round email skipped (no head email): %s", batch_no)
        return
    mail.send_finance_email(to, f"{PREFIX} รออนุมัติเคลียร์ {batch_no}",
                            render_round_submitted(batch_id, batch_no, holder_name, count, total))


def notify_round_rejected(batch_id: int, batch_no: str, reason: str, holder_email=None) -> None:
    if not holder_email:
        return
    mail.send_finance_email([holder_email], f"{PREFIX} ไม่อนุมัติเคลียร์ {batch_no}",
                            render_round_rejected(batch_id, batch_no, reason))
