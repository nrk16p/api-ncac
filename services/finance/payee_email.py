"""Email to Accounting when an employee requests a payee account (pure rendering, no I/O)."""
from __future__ import annotations

import re
from html import escape

_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]+)?$")


def format_kbank_account(account_no) -> str:
    digits = str(account_no or "")
    if len(digits) == 10 and digits.isascii() and digits.isdigit():
        return f"{digits[:3]}-{digits[3]}-{digits[4:9]}-{digits[9]}"
    return digits


def review_link(app_origin, request_id):
    if isinstance(app_origin, str) and _ORIGIN.fullmatch(app_origin):
        return f"{app_origin}/finance/payee-accounts?request={request_id}"
    return None


def render_payee_request_email(ctx: dict):
    """ctx: request_id, employee_id, employee_name, department, position, account_no, account_name, remark,
    app_origin. Returns (subject, html). Every interpolated value is HTML-escaped."""
    e = lambda v: escape(str(v)) if v not in (None, "") else "-"  # noqa: E731
    subject = f"[ขอเพิ่มบัญชีรับเงิน] {ctx.get('employee_name') or '-'} ({ctx.get('employee_id') or '-'})"
    link = review_link(ctx.get("app_origin"), ctx.get("request_id"))
    button = ""
    if link:
        button = (f'<p><a href="{escape(link, quote=True)}" style="display:inline-block;padding:10px 20px;'
                  'background:#0f766e;color:#ffffff;text-decoration:none;border-radius:6px">'
                  "ตรวจสอบคำขอ</a></p>")
    rows = [
        ("ผู้ขอ", ctx.get("employee_name")),
        ("รหัสพนักงาน", ctx.get("employee_id")),
        ("แผนก", ctx.get("department")),
        ("ตำแหน่ง", ctx.get("position")),
        ("ธนาคาร", "ธนาคารกสิกรไทย"),
        ("เลขที่บัญชี", format_kbank_account(ctx.get("account_no"))),
        ("ชื่อบัญชี", ctx.get("account_name")),
        ("หมายเหตุ", ctx.get("remark")),
    ]
    table = "".join(f'<tr><td style="padding:4px 12px 4px 0;color:#555">{label}</td><td>{e(value)}</td></tr>'
                    for label, value in rows)
    html = ('<div style="font-family:sans-serif;font-size:14px">'
            "<p>มีคำขอเพิ่ม/เปลี่ยนบัญชีรับเงินพนักงาน รอบัญชีตรวจสอบ</p>"
            f"<table>{table}</table>{button}"
            "<p>ไฟล์ bookbank ดูได้ในระบบ</p></div>")
    return subject, html
