"""Finance (Advance) email notifications — built, switched OFF by default (v3 §7).

* `send_finance_email` is the only sender. It returns at once unless FINANCE_EMAIL_ENABLED is "true"
  (case-insensitive) and it never falls back to the IT SMTP_USER credentials. Each recipient gets their own
  message, so nobody sees the other recipients' addresses.
* Render functions are pure: every value is HTML-escaped, and a link is built only when FE_BASE_URL is a bare
  http(s) origin.
* `queue_event` is what routes call (after their commit). It does nothing (no query, no render) while the switch
  is off, never raises (a failed lookup rolls the session back so the request can keep using it), and hands the
  actual send to FastAPI BackgroundTasks (the request session is closed by then, so every value is resolved
  before the task is queued).
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from services.finance import advance_repo
from services.finance import approval_logic as rules
from services.finance import approval_repo

log = logging.getLogger(__name__)

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
MAX_RECIPIENTS = 20
ACCOUNT_MAILBOX_DEFAULT = "accountbkk@menatransport.co.th"
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]+)?$")

# events
SUBMITTED = "submitted"
RESUBMITTED = "resubmitted"
STEP_PENDING = "step_pending"
APPROVED = "approved"
REJECTED = "rejected"
RETURNED = "returned"
PAID = "paid"
SENT_BACK = "sent_back"
CLOSED = "closed"
CLEAR_SUBMITTED = "clear_submitted"
APPROVER_EVENTS = (SUBMITTED, RESUBMITTED, STEP_PENDING)
REQUESTER_EVENTS = (APPROVED, REJECTED, RETURNED, PAID, SENT_BACK, CLOSED, CLEAR_SUBMITTED)
# งานถึงมือบัญชี: หน้าตาอิงข้อความไลน์กลุ่มบัญชี และ CC กล่องบัญชี (ผู้ใช้สั่ง 20/10/2026)
ACCOUNT_CC_EVENTS = (APPROVED, CLEAR_SUBMITTED)


# ---------------------------- switch + sender ----------------------------

def email_enabled() -> bool:
    return (os.getenv("FINANCE_EMAIL_ENABLED") or "").strip().lower() == "true"


def account_mailbox() -> str:
    """กล่องอีเมลฝ่ายบัญชี: FINANCE_ACCOUNT_EMAIL ถ้าไม่ตั้ง = accountbkk"""
    return (os.getenv("FINANCE_ACCOUNT_EMAIL") or "").strip() or ACCOUNT_MAILBOX_DEFAULT


def test_mailbox() -> str:
    """FINANCE_EMAIL_TEST_TO: ตั้งไว้ = อีเมล Finance ทุกฉบับเข้ากล่องนี้กล่องเดียว (ใช้ช่วง UAT)"""
    return (os.getenv("FINANCE_EMAIL_TEST_TO") or "").strip()


def _test_banner(recipients, cc) -> str:
    who = escape(", ".join(recipients))
    cc_line = f" · CC: {escape(', '.join(cc))}" if cc else ""
    return ('<div style="background:#fff4cc;border:1px solid #f0c64a;padding:10px 16px;margin:12px 24px;'
            'font-family:Tahoma,Arial,sans-serif;font-size:13px;color:#5b4500">'
            f"อีเมลทดสอบ · ระบบจริงจะส่งถึง: {who}{cc_line}</div>")


def _with_banner(html: str, banner: str) -> str:
    m = re.search(r"<body[^>]*>", html or "", re.I)
    return html[:m.end()] + banner + html[m.end():] if m else banner + (html or "")


def send_finance_email(to, subject: str, html: str, cc=None) -> None:
    deliver_finance_email(to, subject, html, cc=cc)


def deliver_finance_email(to, subject: str, html: str, cc=None) -> int:
    """Same as send_finance_email but returns how many recipients SMTP accepted (0 when off / failed).
    `to`: one address or a list (deduped, capped at MAX_RECIPIENTS). One message per recipient, each with only
    that recipient in To, over one SMTP connection per batch. A failed recipient is logged and the rest are still
    sent (on a fresh connection, as the failure may have broken the session); if no connection can be opened the
    remaining recipients are skipped. Never raises; opens no connection unless enabled and credentialed."""
    if not email_enabled():
        log.info("finance email skipped (FINANCE_EMAIL_ENABLED is not true): %s", subject)
        return 0
    user = os.getenv("FINANCE_SMTP_USER")
    password = os.getenv("FINANCE_SMTP_PASSWORD")
    if not user or not password:
        log.warning("finance email enabled but FINANCE_SMTP_USER / FINANCE_SMTP_PASSWORD missing; skipped: %s",
                    subject)
        return 0
    server = None
    sent = 0
    try:
        recipients = pick_recipients([to] if isinstance(to, str) else list(to or []))
        taken = {r.lower() for r in recipients}
        # CC goes on one message only (the first that is accepted), never once per recipient
        pending_cc = [c for c in pick_recipients([cc] if isinstance(cc, str) else list(cc or []))
                      if c.lower() not in taken]
        subject = _one_line(subject)
        test_to = test_mailbox()
        if test_to and recipients:
            # ช่วงทดสอบ: ทุกฉบับเข้ากล่องเดียว บอกในเนื้อหาว่าตัวจริงจะถึงใคร (ผู้ใช้สั่ง 20/10/2026)
            log.info("finance email redirected to FINANCE_EMAIL_TEST_TO: %s", subject)
            html = _with_banner(html, _test_banner(recipients, pending_cc))
            recipients, pending_cc = [test_to], []
        for index, addr in enumerate(recipients):
            if server is None:
                try:
                    server = _connect(user, password)
                except Exception:  # noqa: BLE001
                    log.exception("finance email: SMTP connection failed, %d recipient(s) not sent: %s",
                                  len(recipients) - index, subject)
                    return sent
            try:
                server.sendmail(user, [addr] + pending_cc, _message(user, addr, subject, html, pending_cc))
                sent += 1
                pending_cc = []
            except Exception:  # noqa: BLE001 - one recipient's failure must not stop the others
                log.exception("finance email to %s failed: %s", addr, subject)
                _quit(server)
                server = None
    except Exception:  # noqa: BLE001 - a mail failure must never affect the request
        log.exception("finance email failed: %s", subject)
    finally:
        _quit(server)
    return sent


def _connect(user, password):
    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
    try:
        server.starttls()
        server.login(user, password)
    except Exception:
        _quit(server)
        raise
    return server


def _quit(server) -> None:
    if server is None:
        return
    try:
        server.quit()
    except Exception:  # noqa: BLE001
        pass


def _message(sender, recipient, subject, html, cc=None) -> str:
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = _one_line(recipient)
    if cc:
        msg["Cc"] = _one_line(", ".join(cc))
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg.as_string()


def _one_line(text) -> str:
    return re.sub(r"[\r\n]+", " ", str(text or "")).strip()


# ---------------------------- rendering (pure) ----------------------------

def build_link(fe_base_url, path: str):
    if isinstance(fe_base_url, str) and _ORIGIN.fullmatch(fe_base_url):
        return f"{fe_base_url}{path}"
    return None


def advance_link(fe_base_url, form_id, for_approver: bool):
    if for_approver:
        return build_link(fe_base_url, f"/finance/approvals?doc={_q(form_id)}")
    return build_link(fe_base_url, f"/finance/advance/{_q(form_id)}")


def _q(value) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", str(value or ""))


def _fmt_money(value):
    try:
        return f"{float(value):,.2f} บาท"
    except (TypeError, ValueError):
        return None


def _fmt_date(value):
    if value is None or value == "":
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else str(value)


_TITLES = {
    SUBMITTED: ("คำขอใหม่", "รอท่านอนุมัติ", "มีคำขอเบิกเงิน Advance รอท่านพิจารณา", True),
    RESUBMITTED: ("ส่งใหม่", "รอท่านอนุมัติ (ส่งใหม่หลังแก้ไข)", "ผู้เบิกแก้ไขและส่งคำขอเบิกเงิน Advance ใหม่", True),
    STEP_PENDING: ("รออนุมัติขั้นถัดไป", "รอท่านอนุมัติ", "คำขอเบิกเงิน Advance ผ่านการอนุมัติขั้นก่อนหน้าแล้ว", True),
    APPROVED: ("เบิกเงิน Advance", "อนุมัติครบทุกขั้นแล้ว", "คำขอผ่านการอนุมัติครบทุกขั้นแล้ว รอฝ่ายบัญชีตั้งเบิก", False),
    REJECTED: ("ไม่อนุมัติ", "คำขอเบิกเงินไม่ได้รับการอนุมัติ", "คำขอของท่านถูกปฏิเสธ", False),
    RETURNED: ("ตีกลับ", "บัญชีตีกลับให้แก้ไข", "กรุณาแก้ไขและส่งคำขอใหม่", False),
    PAID: ("จ่ายเงินแล้ว", "การเงินจ่ายเงินเรียบร้อย", "โปรดเคลียร์เงินภายในกำหนด", False),
    SENT_BACK: ("ตีกลับเคลียร์", "บัญชีตีกลับเอกสารเคลียร์", "กรุณาแก้ไขเอกสารเคลียร์และส่งใหม่", False),
    CLOSED: ("ปิดรายการ", "รายการเบิกเงินปิดเรียบร้อย", "บัญชีตรวจสอบและปิดรายการแล้ว", False),
    CLEAR_SUBMITTED: ("เบิกเงิน Advance", "ส่งเคลียร์เงินแล้ว", "ผู้เบิกส่งเอกสารเคลียร์เงินแล้ว", False),
}


def _account_rows(event: str, ctx: dict) -> list:
    """แถวเดียวกับข้อความไลน์กลุ่มบัญชี (finance_line.render_line_message)"""
    who = ctx.get("requester_name") or "-"
    if ctx.get("department"):
        who = f"{who} ({ctx['department']})"
    rows = [("เลขที่", ctx.get("form_id") or "-"), ("ผู้เบิก", who)]
    money = lambda key: _fmt_money(ctx.get(key))  # noqa: E731
    if event == CLEAR_SUBMITTED:
        for label, key in (("จ่ายไปแล้ว", "amount_paid"), ("ยอดใช้จริง", "amount_actual")):
            if money(key):
                rows.append((label, money(key)))
        try:
            settle = float(ctx.get("settle_amount") or 0)
        except (TypeError, ValueError):
            settle = 0
        if settle > 0:
            rows.append(("คืนบริษัท", _fmt_money(settle)))
        elif settle < 0:
            rows.append(("บริษัทจ่ายเพิ่ม", _fmt_money(-settle)))
    elif money("amount"):
        rows.append(("ยอดเบิก", money("amount")))
    if ctx.get("purpose"):
        rows.append(("วัตถุประสงค์", ctx["purpose"]))
    if event == APPROVED:
        if _fmt_date(ctx.get("use_date")):
            rows.append(("วันที่ใช้เงิน", _fmt_date(ctx.get("use_date"))))
        if ctx.get("approver_name"):
            rows.append(("ผู้อนุมัติขั้นสุดท้าย", ctx["approver_name"]))
        rows.append(("สถานะ", "รอตั้งเบิก"))
    else:
        pending = bool(ctx.get("pending"))
        if pending and ctx.get("total_steps"):
            tail = f" (TOA ข้อ {ctx['clause']})" if ctx.get("clause") else ""
            rows.append(("ขั้นอนุมัติ", f"ขั้นที่ {ctx.get('step') or 1} จาก {ctx['total_steps']}{tail}"))
        rows.append(("สถานะ", "รออนุมัติเคลียร์" if pending else "รอบัญชีตรวจ"))
    return rows


def _legacy_rows(event: str, ctx: dict, form_id, requester) -> list:
    rows = [("เลขที่เอกสาร", form_id)]
    if requester:
        rows.append(("ผู้เบิก", requester))
    amount = _fmt_money(ctx.get("amount"))
    if amount:
        rows.append(("ยอดเงินที่ขอ", amount))
    if event in (SUBMITTED, RESUBMITTED, STEP_PENDING) and ctx.get("total_steps"):
        label = f" — {ctx['step_label']}" if ctx.get("step_label") else ""
        rows.append(("ขั้นอนุมัติ", f"ขั้นที่ {ctx.get('step') or 1} จาก {ctx['total_steps']}{label}"))
    if event == PAID:
        for label, key in (("ยอดที่จ่าย", "amount_paid"), ("วันที่โอนเงิน", "transfer_date"),
                           ("กำหนดเคลียร์", "clear_due_date")):
            value = _fmt_money(ctx.get(key)) if key == "amount_paid" else _fmt_date(ctx.get(key))
            if value:
                rows.append((label, value))
    if event in (REJECTED, RETURNED, SENT_BACK) and ctx.get("remark"):
        rows.append(("เหตุผล", ctx.get("remark")))
    return rows


def render_event_email(event: str, ctx: dict):
    """ctx: form_id, requester_name, amount, remark, step, total_steps, step_label, transfer_date, clear_due_date,
    fe_base_url. Returns (subject, html). Every interpolated value is HTML-escaped; the link only when
    FE_BASE_URL is a bare http(s) origin."""
    badge, headline, sub, for_approver = _TITLES[event]
    e = lambda v: escape(str(v), quote=True)  # noqa: E731
    form_id = ctx.get("form_id") or "-"
    requester = ctx.get("requester_name")
    if event in ACCOUNT_CC_EVENTS:
        subject = _one_line(f"{badge} · {headline} · {form_id}")
        rows = _account_rows(event, ctx)
    else:
        subject = _one_line(f"[Finance Advance] {form_id} — {headline}")
        rows = _legacy_rows(event, ctx, form_id, requester)
    table = "".join(
        f'<tr><td style="padding:6px 16px 6px 0;font-size:13px;color:#6b7280;white-space:nowrap">{e(label)}</td>'
        f'<td style="padding:6px 0;font-size:14px;color:#1f2937">{e(value)}</td></tr>' for label, value in rows)
    link = advance_link(ctx.get("fe_base_url"), form_id, for_approver)
    button = ""
    if link:
        button = ('<p style="margin:24px 0 0"><a href="' + escape(link, quote=True) + '" style="display:inline-block;'
                  'padding:11px 22px;background:#1a7a36;color:#ffffff;text-decoration:none;border-radius:8px;'
                  'font-size:14px;font-weight:700">เปิดรายการ</a></p>')
    html = (
        '<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8"></head>'
        '<body style="margin:0;padding:0;background-color:#eef2f7;font-family:Tahoma,Arial,sans-serif">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#eef2f7;'
        'padding:32px 16px 32px 24px"><tr><td align="left">'
        '<div style="font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#4a5568;'
        'margin-bottom:16px">&#9679; Finance &mdash; Notification</div>'
        '<table width="620" cellpadding="0" cellspacing="0" border="0" style="max-width:620px;width:100%;'
        'background-color:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e0e6ed">'
        '<tr><td style="background:#2ea84f;padding:28px 36px">'
        f'<div style="font-size:11px;font-weight:700;color:#ffffff;letter-spacing:0.1em;margin-bottom:10px">{e(badge)}</div>'
        f'<div style="font-size:24px;font-weight:700;color:#ffffff;margin-bottom:6px">{e(headline)}</div>'
        f'<div style="font-size:13px;color:#e8f5ec">{e(sub)}</div></td></tr>'
        f'<tr><td style="padding:28px 36px"><table cellpadding="0" cellspacing="0" border="0">{table}</table>'
        f'{button}</td></tr></table></td></tr></table></body></html>'
    )
    return subject, html


# ---------------------------- recipients ----------------------------

def pick_recipients(emails, cap: int = MAX_RECIPIENTS) -> list:
    """Drop blanks, dedupe (case-insensitive, first wins), cap the list and log when it was truncated."""
    seen, out = set(), []
    for raw in emails:
        addr = (raw or "").strip()
        if not addr or addr.lower() in seen:
            continue
        seen.add(addr.lower())
        out.append(addr)
    if len(out) > cap:
        log.warning("finance email recipients truncated from %d to %d", len(out), cap)
        out = out[:cap]
    return out


def step_approver_emails(db, submission) -> list:
    """Emails of every approver eligible for the submission's current step (never the requester, never the
    excluded step-1 approver of this round), lowest level first."""
    from models.user_model import User  # local: keeps the pure renders importable without the ORM graph

    ctx = approval_repo.ensure_people(db, approval_repo.load_context(db), submission.created_by)
    info = approval_repo.describe(db, submission.created_by, approval_repo._amount_of(db, submission.id), ctx)
    state = rules.evaluate_step(info, ctx.people, ctx.mappings, submission.created_by,
                                submission.current_approval_level,
                                approval_repo.current_round_logs(db, submission.id))
    requester = ctx.people.get(submission.created_by)
    if requester is None:
        return []
    eligible = rules.eligible_approvers(ctx.people.values(), requester, state["required_level"], ctx.mappings,
                                        state["excluded"])
    # อีเมลหยุดที่ระดับผู้จัดการ สิทธิ์กดอนุมัติไม่เปลี่ยน (ผู้ใช้สั่ง 19/10/2026)
    eligible = rules.notify_approvers(eligible, state["required_level"])
    eligible.sort(key=lambda p: (p["level"], p["employee_id"]))
    ids = [p["id"] for p in eligible if p.get("id") is not None]
    by_id = {u.id: u.email for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    return pick_recipients(by_id.get(p.get("id")) for p in eligible)


def requester_email(db, submission) -> list:
    from models.user_model import User

    user = db.query(User).filter(User.employee_id == submission.created_by).first()
    return pick_recipients([user.email]) if user is not None else []


# ---------------------------- queueing (called by routes) ----------------------------

def _rollback(db) -> None:
    """A failed lookup leaves the session in a failed transaction. The business action is already committed, so
    end it here, or the request's next DB use raises PendingRollbackError (500)."""
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass


def queue_event(background_tasks, db, event: str, submission, **extra) -> None:
    """Resolve recipients and content now, send in the background. A no-op while the switch is off."""
    if not email_enabled():
        return
    try:
        sid = submission.id
        values = advance_repo.request_values_by_submission(db, [sid]).get(sid) or {}
        people = advance_repo.people_by_employee_id(db, [submission.created_by])
        ctx = {"form_id": submission.form_id,
               "requester_name": (people.get(submission.created_by) or {}).get("name"),
               "amount": values.get("amount"), "fe_base_url": os.getenv("FE_BASE_URL"),
               "department": (people.get(submission.created_by) or {}).get("department"),
               "purpose": values.get("purpose"), "use_date": values.get("use_date"), **extra}
        if event in APPROVER_EVENTS:
            try:
                actx = approval_repo.ensure_people(db, approval_repo.load_context(db), submission.created_by)
                info = approval_repo.describe(db, submission.created_by, values.get("amount"), actx)
                state = rules.evaluate_step(info, actx.people, actx.mappings, submission.created_by,
                                            submission.current_approval_level)
                ctx.update(step=state["step"], total_steps=state["total_steps"], step_label=state["label"])
            except Exception:  # noqa: BLE001 - the step line is optional
                _rollback(db)
            to = step_approver_emails(db, submission)
        else:
            to = requester_email(db, submission)
        if not to:
            log.info("finance email %s for %s: no recipient with an email", event, submission.form_id)
            return
        subject, html = render_event_email(event, ctx)
        # งานถึงมือบัญชี: CC กล่องบัญชี · อีเมลขออนุมัติ: CC ผู้เบิกให้รู้ว่าใบไปถึงใครแล้ว (ผู้ใช้สั่ง 20/10/2026)
        if event in ACCOUNT_CC_EVENTS:
            cc = [account_mailbox()]
        elif event in APPROVER_EVENTS:
            try:
                cc = requester_email(db, submission)
            except Exception:  # noqa: BLE001 - the CC is optional, the approvers' email still goes
                _rollback(db)
                cc = None
        else:
            cc = None
        background_tasks.add_task(send_finance_email, to, subject, html, cc=cc)
    except Exception:  # noqa: BLE001 - never break the request
        _rollback(db)
        log.exception("finance email %s could not be queued", event)
