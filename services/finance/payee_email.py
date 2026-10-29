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
    old_account_no (มีเมื่อเป็นคำขอเปลี่ยน), app_origin. Returns (subject, html). Every value is HTML-escaped.
    หน้าตาเดียวกับอีเมลเบิกเงิน Advance (การ์ดหัวเขียว) ให้บัญชีเห็นเลขบัญชีชัด ๆ แล้วเทียบกับ bookbank"""
    e = lambda v: escape(str(v), quote=True)  # noqa: E731
    name = ctx.get("employee_name") or "-"
    emp = ctx.get("employee_id") or "-"
    old = ctx.get("old_account_no")
    kind = "ขอเปลี่ยนบัญชี" if old else "ขอเพิ่มบัญชีใหม่"
    subject = f"บัญชีรับเงินสำหรับเบิก Advance · {kind} · {name} ({emp})"

    who = " · ".join(e(v) for v in (f"รหัส {emp}", ctx.get("department"), ctx.get("position")) if v)
    old_row = ""
    if old:
        old_row = ('<tr><td style="padding:10px 0 0;font-size:12px;color:#6b7280">เลขเดิม '
                   f'<span style="text-decoration:line-through">{e(format_kbank_account(old))}</span>'
                   " จะถูกแทนด้วยเลขใหม่เมื่ออนุมัติ</td></tr>")
    remark = ""
    if ctx.get("remark"):
        remark = ('<tr><td style="padding:18px 0 0"><div style="font-size:12px;color:#6b7280;margin-bottom:4px">'
                  f'หมายเหตุจากผู้ขอ</div><div style="font-size:14px;color:#1f2937">{e(ctx["remark"])}</div></td></tr>')
    link = review_link(ctx.get("app_origin"), ctx.get("request_id"))
    button = ""
    if link:
        button = ('<tr><td style="padding:24px 0 0"><a href="' + escape(link, quote=True) + '" style="display:inline-block;'
                  'padding:11px 22px;background:#1a7a36;color:#ffffff;text-decoration:none;border-radius:8px;'
                  'font-size:14px;font-weight:700">เปิดคำขอในระบบ</a></td></tr>')
    html = (
        '<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8"></head>'
        '<body style="margin:0;padding:0;background-color:#eef2f7;font-family:Tahoma,Arial,sans-serif">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#eef2f7;'
        'padding:32px 16px 32px 24px"><tr><td align="left">'
        '<div style="font-size:12px;font-weight:700;letter-spacing:0.08em;color:#4a5568;margin-bottom:16px">'
        '&#9679; FINANCE &mdash; บัญชีรับเงินสำหรับเบิก Advance</div>'
        '<table width="620" cellpadding="0" cellspacing="0" border="0" style="max-width:620px;width:100%;'
        'background-color:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e0e6ed">'
        '<tr><td style="background:#2ea84f;padding:28px 36px">'
        '<div style="font-size:11px;font-weight:700;color:#ffffff;letter-spacing:0.1em;margin-bottom:10px">บัญชีรับเงิน · สำหรับเบิก Advance</div>'
        f'<div style="font-size:24px;font-weight:700;color:#ffffff;margin-bottom:6px">{kind}</div>'
        '<div style="font-size:13px;color:#e8f5ec">บัญชีที่บริษัทจะโอนเงินเบิก Advance ให้ · รอฝ่ายบัญชีเทียบ bookbank แล้วกดอนุมัติในระบบ</div></td></tr>'
        '<tr><td style="padding:28px 36px"><table width="100%" cellpadding="0" cellspacing="0" border="0">'
        '<tr><td style="font-size:12px;color:#6b7280;padding-bottom:4px">ผู้ขอ</td></tr>'
        f'<tr><td style="font-size:17px;font-weight:700;color:#1f2937">{e(name)}</td></tr>'
        f'<tr><td style="font-size:13px;color:#4b5563;padding-top:2px">{who}</td></tr>'
        '<tr><td style="padding:20px 0 0"><table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#f3faf5;border:1px solid #cfe9d6;border-radius:12px">'
        '<tr><td style="padding:16px 20px">'
        '<div style="font-size:12px;color:#2f6b3f;font-weight:700">ธนาคารกสิกรไทย</div>'
        f'<div style="font-size:24px;font-weight:700;color:#14532d;letter-spacing:0.04em;margin:6px 0 4px">'
        f'{e(format_kbank_account(ctx.get("account_no")))}</div>'
        f'<div style="font-size:14px;color:#1f2937">ชื่อบัญชี {e(ctx.get("account_name") or "-")}</div>'
        f'</td></tr></table></td></tr>{old_row}{remark}{button}'
        '<tr><td style="padding:20px 0 0;font-size:12px;color:#6b7280">รูป bookbank แนบอยู่ในคำขอ '
        'ดูให้ชื่อกับเลขตรงกันก่อนกดอนุมัติ</td></tr>'
        '</table></td></tr></table></td></tr></table></body></html>'
    )
    return subject, html
