"""แจ้งเตือนงานเบิกเงิน Advance เข้ากลุ่มไลน์ (ผู้ใช้สั่ง 19/10/2026).

ยิงเฉพาะใบที่ "ผู้เบิกอยู่แผนกบัญชี" 3 จังหวะ
  1. ยื่นใบคำขอเบิก              → รออนุมัติ
  2. อนุมัติครบทุกขั้น            → รอตั้งเบิก
  3. ส่งเคลียร์แล้วเข้าด่านหัวหน้า → รออนุมัติเคลียร์

ข้อความเป็น text ธรรมดาตามสไตล์เดิมของระบบ (routes/forms/form_submission_routes.py)
ทุกฟังก์ชัน render เป็น pure function ทดสอบได้โดยไม่ต้องต่อ DB และการส่งจริงไม่เคย raise
ออกมานอกโมดูล งานหลักที่ commit ไปแล้วจึงไม่พังเพราะไลน์ล่ม
"""
import logging
import os
import re

import requests

from services.finance import advance_repo

log = logging.getLogger(__name__)

SUBMITTED = "submitted"
READY_FOR_VOUCHER = "ready_for_voucher"
CLEARING_APPROVAL = "clearing_approval"
# ส่งเข้ากลุ่มทุกแผนก: อนุมัติครบ = ใบเข้าคิวบัญชีตั้งเบิก (ผู้ใช้สั่ง 20/10/2026)
# ใบคำขอเบิกใหม่ กับ ส่งเคลียร์เงินแล้ว ยังส่งเฉพาะผู้เบิกแผนกบัญชี
ANY_DEPARTMENT_EVENTS = frozenset({READY_FOR_VOUCHER})

PUSH_URL = "https://api.line.me/v2/bot/message/push"
DEFAULT_GROUP_ID = "C1a62beae8781d841fb849d1cc7ee48cc"
DEFAULT_DEPARTMENT_IDS = "6"  # 6 = แผนกบัญชี (4 = แผนกการเงิน)
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]+)?$")


def token() -> str:
    """โทเคนของบอทที่อยู่ในกลุ่มบัญชี — คนละตัวกับ LINE_TOKEN ของแจ้งเตือน IT Service โดยตั้งใจ
    ไม่ตั้งค่า = ไม่ส่ง ดีกว่าหลุดไปใช้บอทตัวอื่นแล้วยิงไม่เข้ากลุ่ม

    อ่านตอนเรียกใช้ ไม่ใช่ตอน import (services/line_service.py อ่านตอน import ซึ่งทดสอบยาก)
    """
    return (os.getenv("FINANCE_LINE_TOKEN") or "").strip()


def line_enabled() -> bool:
    """ต้องมีโทเคนและไม่ได้ปิดสวิตช์ไว้ ค่าเริ่มต้นคือเปิด"""
    if not token():
        return False
    return (os.getenv("FINANCE_LINE_ENABLED", "true") or "").strip().lower() == "true"


def group_id() -> str:
    return (os.getenv("FINANCE_LINE_GROUP_ID") or DEFAULT_GROUP_ID).strip()


def department_ids() -> set:
    raw = os.getenv("FINANCE_LINE_DEPARTMENT_IDS", DEFAULT_DEPARTMENT_IDS) or ""
    return {int(p.strip()) for p in raw.split(",") if p.strip().isdigit()}


def should_notify(department_id, allowed=None) -> bool:
    """ยิงเข้ากลุ่มเฉพาะใบที่ผู้เบิกอยู่แผนกที่กำหนดไว้ (ค่าเริ่มต้น: แผนกบัญชี)"""
    allowed = department_ids() if allowed is None else set(allowed)
    try:
        return int(department_id) in allowed
    except (TypeError, ValueError):
        return False


def _money(value):
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return None


def _date(value):
    if value in (None, ""):
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else str(value)


def _link(fe_base_url, form_id):
    if isinstance(fe_base_url, str) and _ORIGIN.fullmatch(fe_base_url):
        return f"{fe_base_url}/finance/advance/{re.sub(r'[^A-Za-z0-9._-]', '', str(form_id or ''))}"
    return None


_HEADS = {
    SUBMITTED: ("ใบคำขอเบิกใหม่", "รออนุมัติ"),
    READY_FOR_VOUCHER: ("อนุมัติครบทุกขั้นแล้ว", "รอตั้งเบิก"),
    CLEARING_APPROVAL: ("ส่งเคลียร์เงินแล้ว", "รออนุมัติเคลียร์"),
}


def render_line_message(event: str, ctx: dict) -> str:
    """ctx: form_id, requester_name, department, amount, purpose, use_date, step, total_steps, clause,
    amount_paid, amount_actual, settle_amount, approver_name, fe_base_url"""
    head, status = _HEADS[event]
    lines = [f"เบิกเงิน Advance · {head}", ""]
    lines.append(f"เลขที่: {ctx.get('form_id') or '-'}")

    who = ctx.get("requester_name") or "-"
    if ctx.get("department"):
        who = f"{who} ({ctx['department']})"
    lines.append(f"ผู้เบิก: {who}")

    if event == CLEARING_APPROVAL:
        paid, actual = _money(ctx.get("amount_paid")), _money(ctx.get("amount_actual"))
        if paid:
            lines.append(f"จ่ายไปแล้ว: {paid} บาท")
        if actual:
            lines.append(f"ยอดใช้จริง: {actual} บาท")
        settle = _money(ctx.get("settle_amount"))
        if settle and float(ctx.get("settle_amount") or 0) != 0:
            lines.append(f"คืนบริษัท: {settle} บาท")
    else:
        amount = _money(ctx.get("amount"))
        if amount:
            lines.append(f"ยอดเบิก: {amount} บาท")

    if ctx.get("purpose"):
        lines.append(f"วัตถุประสงค์: {ctx['purpose']}")

    use_date = _date(ctx.get("use_date"))
    if use_date and event in (SUBMITTED, READY_FOR_VOUCHER):
        lines.append(f"วันที่ใช้เงิน: {use_date}")

    if event == READY_FOR_VOUCHER and ctx.get("approver_name"):
        lines.append(f"ผู้อนุมัติขั้นสุดท้าย: {ctx['approver_name']}")

    if event == CLEARING_APPROVAL and ctx.get("total_steps"):
        step = ctx.get("step") or 1
        tail = f" (TOA ข้อ {ctx['clause']})" if ctx.get("clause") else ""
        lines.append(f"ขั้นอนุมัติ: ขั้นที่ {step} จาก {ctx['total_steps']}{tail}")

    lines.append(f"สถานะ: {status}")

    link = _link(ctx.get("fe_base_url"), ctx.get("form_id"))
    if link:
        lines += ["", "เปิดรายการ", link]
    return "\n".join(lines)


def _department_of(db, employee_id):
    from models.user_model import User  # local: keeps the renders importable without the ORM graph

    user = db.query(User).filter(User.employee_id == employee_id).first()
    return None if user is None else user.department_id


def queue_line(background_tasks, db, event: str, submission, **extra) -> None:
    """เตรียมข้อความตอนนี้ ส่งเบื้องหลัง — เงียบเมื่อปิดสวิตช์หรือผู้เบิกไม่ได้อยู่แผนกที่กำหนด
    ยกเว้น อนุมัติครบ รอตั้งเบิก ส่งทุกแผนก เพราะเป็นงานของบัญชีเสมอ"""
    if not line_enabled():
        return
    try:
        if event not in ANY_DEPARTMENT_EVENTS and not should_notify(_department_of(db, submission.created_by)):
            return
        sid = submission.id
        values = advance_repo.request_values_by_submission(db, [sid]).get(sid) or {}
        person = (advance_repo.people_by_employee_id(db, [submission.created_by])
                  .get(submission.created_by) or {})
        ctx = {"form_id": submission.form_id,
               "requester_name": person.get("name"),
               "department": person.get("department"),
               "amount": values.get("amount"),
               "purpose": values.get("purpose"),
               "use_date": values.get("use_date"),
               "fe_base_url": os.getenv("FE_BASE_URL"), **extra}
        text = render_line_message(event, ctx)
        background_tasks.add_task(send_line, text)
    except Exception:  # noqa: BLE001 - ไลน์ล้มต้องไม่ทำให้คำขอพัง
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        log.exception("finance line %s could not be queued", event)


def send_line(text: str) -> bool:
    """ส่งจริง คืน True เมื่อ LINE รับแล้ว ไม่เคย raise"""
    try:
        resp = requests.post(
            PUSH_URL,
            json={"to": group_id(), "messages": [{"type": "text", "text": text}]},
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token()}"},
            timeout=10,
        )
        if resp.status_code != 200:
            log.error("finance line push rejected: HTTP %s %s", resp.status_code, resp.text[:300])
            return False
        return True
    except Exception:  # noqa: BLE001
        log.exception("finance line push failed")
        return False
