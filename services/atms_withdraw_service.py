"""
ATMS — ใบเบิก (WD) / ใบรับคืน (DD) ของคลังอะไหล่ (www.mena-atms.com)

ไฟล์นี้ = ฝั่ง "อ่าน" : dropdown ของฟอร์ม, autocomplete, profile, หน้า view/list ของ WD
(ฝั่งเขียน issue_withdraw / return_to_stock เพิ่มใน Task 2)
รายละเอียดหน้า/ฟิลด์/ป้ายทั้งหมดดู docs/atms-withdraw-recon.md

ใช้ AtmsClient เดิมของ openjob (login, retry, จับ session หมดอายุ) โดยไม่แก้พฤติกรรมเดิม
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from services.atms_openjob_service import (  # noqa: F401  (re-export ให้ผู้เรียกใช้ที่เดียว)
    BASE,
    AtmsAuthError,
    AtmsClient,
    AtmsNotFoundError,
    AtmsOpenJobError,
    DOMAIN,
    JOB_ADD_URL,
    LOGIN_URL,
    UA,
    _form_errors,
    _label_pairs,
    _rows_of,
    _s,
)

WD_ADD_URL = f"{BASE}/inv/withdraw/add"
WD_VIEW_URL = f"{BASE}/inv/withdraw/view/id/"
WD_INDEX_URL = f"{BASE}/inv/withdraw/index/"
PROFILE_URL = f"{BASE}/account/user/profile"
ROW_PER_PAGE_URL = f"{BASE}/account/user/set.row.per.page/"
WD_APPROVE_URL = f"{BASE}/inv/withdraw/approve/id/"
WD_RETURNED_URL = f"{BASE}/inv/withdraw/spare.part.returned/type/y/id/"
DD_INDEX_URL = f"{BASE}/inv/deposit/index/"
DD_ADD_URL = f"{BASE}/inv/deposit/add"
DD_VIEW_URL = f"{BASE}/inv/deposit/view/id/"
DD_APPROVE_URL = f"{BASE}/inv/deposit/approve/id/"

# ATMS เก็บ row-per-page ไว้ฝั่งเซิร์ฟเวอร์ต่อผู้ใช้ และไม่มีหน้าให้อ่านค่าปัจจุบัน
# → คืนเป็นค่า default ของระบบ (เหมือน pipeline_atms_supplier.py)
ROW_PER_PAGE_DEFAULT = 25
ROW_PER_PAGE_LIST = 1000

WD_LOOKUP_URLS = {
    "mr_item": f"{BASE}/veh/maintenance.request.item/code.json/",
    "sku": f"{BASE}/inv/sku/sku.json/",
    "serial": f"{BASE}/inv/deposit.item/serial.no.json/",
    "wd": f"{BASE}/inv/withdraw/code.json/",
}

LOGIN_PATH = "account/user/login"
_ROW_ID = re.compile(r"/inv/withdraw/(?:view|edit)/id/(\d+)")
_DD_ROW_ID = re.compile(r"/inv/deposit/(?:view|edit)/id/(\d+)")
_NUM = re.compile(r"-?\d[\d,]*\.?\d*")
_REDIRECTS = (301, 302, 303, 307, 308)
_LOC_ID = re.compile(r"/id/(\d+)")
_TZ = ZoneInfo("Asia/Bangkok")


def _today() -> str:
    return datetime.now(_TZ).strftime("%d/%m/%Y")


class WithdrawPartialError(AtmsOpenJobError):
    """พังกลางทาง — progress = สิ่งที่ ATMS มีอยู่จริงแล้ว ผู้เรียกต้องเก็บไว้ส่งกลับตอนลองใหม่"""

    def __init__(self, progress: "IssueProgress", cause: Exception):
        super().__init__(str(cause))
        self.progress, self.cause = progress, cause


@dataclass
class IssueLine:
    sku_id: int
    amount: float
    sku_name: str              # ATMS บังคับช่อง sku (text) — ชื่อตาม sku.json
    mr_item_id: int | None = None
    serial_no_id: int | None = None
    remark: str = ""


@dataclass
class ReturnLine:
    sku_id: int
    amount: float
    unit_price: float          # ATMS บังคับ — ใช้ราคาจากบรรทัด WD เดิม
    sku_name: str              # ATMS บังคับช่อง sku (text)
    serial_no: str = ""
    remark: str = ""


@dataclass
class IssueProgress:
    gm_id: int | None = None
    done: list[int] = field(default_factory=list)   # index ของ line ที่ส่งแล้ว
    approved: bool = False
    code: str | None = None


def _token_re(token: str):
    """token ต้องอยู่ติดขอบ: ก่อน/หลังไม่ใช่ตัวอักษร ตัวเลข หรือ - (SW-001 ไม่ชน SW-0010)"""
    t = _s(token).strip()
    if not t:
        return None
    return re.compile(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", re.I)


def _num(text: str) -> float:
    m = _NUM.search(_s(text))
    return float(m.group(0).replace(",", "")) if m else 0.0


def _ticked(dd) -> bool:
    """ช่อง boolean ของ ATMS แสดงเป็นไอคอน (x-square-fill = ไม่ใช่) ไม่ใช่ข้อความ"""
    if dd is None:
        return False
    img = dd.find("img")
    if img is not None:
        return "check" in _s(img.get("src")).lower()
    return dd.get_text(strip=True).lower() in ("y", "yes", "true", "1", "ใช่")


def _select_options(soup: BeautifulSoup, name: str) -> list[dict]:
    sel = soup.find("select", attrs={"name": name})
    if sel is None:
        return []
    return [{"id": int(o["value"]), "name": o.get_text(strip=True)}
            for o in sel.find_all("option")
            if _s(o.get("value")).strip().isdigit()]


class AtmsWithdrawClient(AtmsClient):
    """WD/DD client — pace = หน่วง (วินาที) ก่อนทุก request ไป ATMS"""

    def __init__(self, phpsessid: str | None = None, username: str | None = None,
                 password: str | None = None, *, pace: float = 1.0, **kw):
        # ใช้บัญชีของผู้เรียกเท่านั้น — ห้ามตกไปใช้ PHPSESSID / ATMS_* จาก env
        if not phpsessid and not (username and password):
            raise AtmsAuthError("ต้องส่งบัญชี ATMS ของผู้ใช้มา")
        super().__init__(phpsessid, username, password, **kw)
        self.phpsessid, self.username, self.password = phpsessid, username, password
        self.pace = pace

    def _new_session(self) -> requests.Session:
        """เหมือนของ AtmsClient แต่หน่วง pace ก่อนทุก request ของการ login"""
        s = requests.Session()
        s.verify = False
        s.headers.update({"User-Agent": UA, "Accept-Language": "th,en;q=0.8"})
        if self.phpsessid:
            s.cookies.set("PHPSESSID", self.phpsessid.split("=")[-1].strip(), domain=DOMAIN)
            return s
        self._pause()
        s.get(LOGIN_URL, timeout=self.timeout)
        self._pause()
        s.post(LOGIN_URL, timeout=self.timeout, allow_redirects=True,
               data={"username": self.username, "password": self.password,
                     "submit": "login", "next": "", "forgotPasswd": "ลืมรหัสผ่าน"})
        self._pause()
        if "account/user/login" in s.get(JOB_ADD_URL, timeout=self.timeout).url:
            raise AtmsAuthError("ATMS login ไม่ผ่าน — เช็ค username / password")
        return s

    def _request(self, method: str, url: str, **kw):
        """หน่วง pace ก่อนทุก request (อ่านและเขียน) — ATMS ถูกยิงถี่ไม่ได้"""
        self._pause()
        return super()._request(method, url, **kw)

    def _pause(self) -> None:
        if self.pace > 0:
            time.sleep(self.pace)

    # ── dropdown ของฟอร์มเบิก ───────────────────────────────────────────────
    def options(self, form: str | None = None) -> dict:
        """
        form=None → {"inventories": [...], "pick_tos": [...]} จาก /inv/withdraw/add
        form อื่น ("job"/"item") ส่งต่อให้ของ openjob เดิม
        """
        if form is not None:
            return super().options(form)
        if "withdraw" not in self._options_cache:
            soup = self._get_soup(WD_ADD_URL)
            self._options_cache["withdraw"] = {
                "inventories": _select_options(soup, "inventory_id"),
                "pick_tos": _select_options(soup, "pick_to_id"),
            }
        return self._options_cache["withdraw"]

    # ── autocomplete (JSON) ────────────────────────────────────────────────
    def lookup(self, kind: Literal["mr_item", "sku", "serial", "wd"], q: str,
               limit: int = 20, inventory_id: int | None = None,
               sku_id: int | None = None) -> list[dict]:
        """คืน JSON ของ ATMS ตรง ๆ — kind ของ openjob (vehicle ฯลฯ) ส่งต่อให้ของเดิม"""
        if kind not in WD_LOOKUP_URLS:
            return super().lookup(kind, q, limit)
        params: dict[str, Any] = {"q": q, "limit": limit}
        if inventory_id is not None:
            params["inventory_id"] = inventory_id
        if sku_id is not None:
            params["sku_id"] = sku_id
        def fetch():
            return self._request("GET", WD_LOOKUP_URLS[kind], params=params,
                                 headers={"X-Requested-With": "XMLHttpRequest"},
                                 allow_redirects=False)

        def is_login(r) -> bool:
            return r.status_code in _REDIRECTS and LOGIN_PATH in r.headers.get("Location", "")

        r = fetch()
        if is_login(r) and self._can_relogin:
            self._sess = None            # login ใหม่แล้วลองอีกครั้งเดียว (เหมือน _get_html)
            r = fetch()
        if is_login(r):
            raise AtmsAuthError("PHPSESSID หมดอายุ — ATMS เด้งกลับหน้า login")
        if r.status_code != 200:
            raise AtmsOpenJobError(f"ATMS ตอบ HTTP {r.status_code} ที่ lookup {kind}")
        try:
            data = r.json()
        except ValueError:
            raise AtmsOpenJobError(f"lookup {kind}: ATMS ไม่ได้ตอบ JSON")
        return data or []

    # ── profile ผู้ใช้ที่ login ─────────────────────────────────────────────
    def profile(self) -> dict:
        soup = self._get_soup(PROFILE_URL)
        info = _label_pairs(soup)
        flags = {dt.get_text(" ", strip=True).rstrip(":").strip(): _ticked(dt.find_next_sibling("dd"))
                 for dt in soup.find_all("dt")}
        if not info.get("ผู้ใช้งาน"):
            raise AtmsOpenJobError("อ่านหน้า profile ของ ATMS ไม่ได้")
        return {
            "username": info.get("ผู้ใช้งาน", ""),
            "email": info.get("อีเมล์", ""),
            "name": info.get("ชื่อ", ""),
            "branch": info.get("สาขา", ""),
            "department": info.get("แผนก", ""),
            "is_driver": flags.get("เป็น พจส.", False),
            "is_mechanic": flags.get("เป็นช่าง", False),
        }

    # ── อ่านใบเบิก 1 ใบ ─────────────────────────────────────────────────────
    def get_withdraw(self, gm_id: int) -> dict:
        soup = self._get_soup(f"{WD_VIEW_URL}{int(gm_id)}")
        info = _label_pairs(soup)
        if not info.get("รหัส"):
            raise AtmsNotFoundError(f"ไม่พบใบเบิก id {gm_id} ใน ATMS")
        approver = info.get("approver", "").strip()
        rejectable = soup.find("a", href=re.compile(r"/inv/withdraw/reject/id/")) is not None
        returned_dd = next((dt.find_next_sibling("dd") for dt in soup.find_all("dt")
                            if dt.get_text(strip=True).startswith("คืนอะไหล่ครบแล้ว")), None)
        return {
            "id": int(gm_id),
            "code": info["รหัส"],
            "inventory": info.get("คลังสินค้า", ""),
            "pick_to": info.get("จุดประสงค์ในการเบิก", ""),
            "remark": info.get("หมายเหตุ", ""),
            "user": info.get("ผู้ใช้งาน", ""),
            "approver": approver or None,
            "created_at": info.get("บันทึกเมื่อ", ""),
            "approved": bool(approver) or rejectable,
            "spare_part_returned": _ticked(returned_dd),
            "items": _wd_items(soup),
        }

    # ── รายการใบเบิกของวัน ──────────────────────────────────────────────────
    def list_withdraws(self, inventory_id: int, date_ddmmyyyy: str) -> list[dict]:
        """
        ใบเบิกของคลัง+วันที่ (DD/MM/YYYY) — ตั้ง rows-per-page 1000 ก่อนแล้วคืนค่าเดิมเสมอ
        (ค่านี้ผูกกับ user ฝั่ง ATMS ไม่ใช่ต่อ request)
        """
        self._set_rows_per_page(ROW_PER_PAGE_LIST)
        try:
            soup = self._get_soup(WD_INDEX_URL, {
                "inventory_id": inventory_id,
                "from_created_at": date_ddmmyyyy,
                "to_created_at": date_ddmmyyyy,
                "submit": "ค้นหา",
            })
        finally:
            self._set_rows_per_page(ROW_PER_PAGE_DEFAULT)
        tables = soup.find_all("table")
        table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)
        out: list[dict] = []
        for tr in (table.find_all("tr")[1:] if table is not None else []):
            m = next((_ROW_ID.search(a["href"]) for a in tr.find_all("a", href=True)
                      if _ROW_ID.search(a["href"])), None)
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not m or len(cells) < 13:
                continue
            out.append({
                "id": int(m.group(1)),
                "code": cells[1],
                "pick_to": cells[3],
                "user": cells[11],
                "remark": cells[12],
                "created_at": cells[0],
                "approved": bool(cells[10]),
            })
        return out

    def find_withdraw_by_remark(self, token: str, inventory_id: int,
                                date_ddmmyyyy: str) -> list[int]:
        """id ของใบเบิกในวันนั้นที่หมายเหตุมี token (ไว้กันสร้างซ้ำ / หา id ที่ 302 ไม่บอก)"""
        pat = _token_re(token)
        if pat is None:
            return []
        return [r["id"] for r in self.list_withdraws(inventory_id, date_ddmmyyyy)
                if pat.search(r["remark"])]

    def list_deposits(self, inventory_id: int, date_ddmmyyyy: str,
                      supplier_ref_no: str = "") -> list[dict]:
        """ใบรับคืน (DD) ของคลัง+วันที่สร้าง — กรอง supplier_ref_no ฝั่ง ATMS ได้"""
        params = {"inventory_id": inventory_id, "from_created_at": date_ddmmyyyy,
                  "to_created_at": date_ddmmyyyy, "submit": "ค้นหา"}
        if supplier_ref_no:
            params["supplier_ref_no"] = supplier_ref_no
        self._set_rows_per_page(ROW_PER_PAGE_LIST)
        try:
            soup = self._get_soup(DD_INDEX_URL, params)
        finally:
            self._set_rows_per_page(ROW_PER_PAGE_DEFAULT)
        tables = soup.find_all("table")
        table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)
        out: list[dict] = []
        for tr in (table.find_all("tr")[1:] if table is not None else []):
            m = next((_DD_ROW_ID.search(a["href"]) for a in tr.find_all("a", href=True)
                      if _DD_ROW_ID.search(a["href"])), None)
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not m or len(cells) < 11:
                continue
            out.append({"id": int(m.group(1)), "code": cells[0], "withdraw": cells[3],
                        "supplier_ref_no": cells[5], "created_at": cells[7],
                        "approved": bool(cells[9]), "user": cells[10]})
        return out

    def find_deposit_by_remark(self, token: str, inventory_id: int,
                               date_ddmmyyyy: str) -> list[int]:
        """
        id ของใบรับคืนในวันนั้นที่ "เลขที่ใบส่งของ" (supplier_ref_no) ตรงกับ token
        (หน้า list ของ DD ไม่แสดงหมายเหตุ → ฝัง token ไว้ที่ supplier_ref_no ตอนสร้างหัวใบ)
        """
        pat = _token_re(token)
        if pat is None:
            return []
        return [r["id"] for r in self.list_deposits(inventory_id, date_ddmmyyyy, token)
                if pat.search(r["supplier_ref_no"])]

    # ═══ ฝั่งเขียน ═══════════════════════════════════════════════════════════
    # กติกาเหล็ก: POST ไม่มี auto-retry เด็ดขาด (POST ซ้ำ = บรรทัด/ใบซ้ำใน ATMS)
    # _request ของ AtmsClient retry เมื่อ connection หลุด → ใช้กับ POST ไม่ได้ จึงมี _post_once
    def _post_once(self, url: str, data: dict) -> Any:
        self._pause()
        for attempt in (0, 1):
            try:
                r = self.session.request("POST", url, data=data, allow_redirects=False,
                                         timeout=self.timeout,
                                         headers={"Referer": url, "Origin": BASE})
            except Exception as e:      # ไม่รู้ว่า ATMS รับไปหรือยัง → ห้ามยิงซ้ำ ให้ผู้เรียกตัดสิน
                raise AtmsOpenJobError(f"POST {url} ไม่ทราบผล (ห้ามยิงซ้ำ): {e}") from e
            if r.status_code in _REDIRECTS and LOGIN_PATH in r.headers.get("Location", ""):
                # เด้งไป login = ATMS ยังไม่ได้ประมวลผลอะไร → login ใหม่แล้วยิงอีกครั้งได้ปลอดภัย
                if attempt == 0 and self._can_relogin:
                    self._sess = None
                    continue
                raise AtmsAuthError("PHPSESSID หมดอายุ — ATMS เด้งกลับหน้า login")
            return r
        raise AtmsAuthError("PHPSESSID หมดอายุ — ATMS เด้งกลับหน้า login")

    def _post_form(self, url: str, data: dict) -> int | None:
        """POST ฟอร์ม → id จาก Location (302) · 200 = ฟอร์มถูก re-render = validation error"""
        r = self._post_once(url, data)
        if r.status_code in _REDIRECTS:
            m = _LOC_ID.search(r.headers.get("Location", ""))
            return int(m.group(1)) if m else None
        if r.status_code == 200:
            raise AtmsOpenJobError("ฟอร์มไม่ผ่าน validation: " +
                                   ("; ".join(_form_errors(r.text)) or "ไม่พบข้อความ error"))
        raise AtmsOpenJobError(f"ATMS ตอบ HTTP {r.status_code}")

    def _click(self, url: str) -> None:
        """ลิงก์ GET ที่เปลี่ยนสถานะ (อนุมัติ / คืนครบ) — ทำซ้ำได้ปลอดภัย"""
        r = self._request("GET", url, allow_redirects=False)
        if r.status_code in _REDIRECTS and LOGIN_PATH in r.headers.get("Location", ""):
            raise AtmsAuthError("PHPSESSID หมดอายุ — ATMS เด้งกลับหน้า login")
        if r.status_code >= 400:
            raise AtmsOpenJobError(f"ATMS ตอบ HTTP {r.status_code} ที่ {url}")

    def get_deposit(self, dd_id: int) -> dict:
        soup = self._get_soup(f"{DD_VIEW_URL}{int(dd_id)}")
        info = _label_pairs(soup)
        if not info.get("เลขที่"):
            raise AtmsNotFoundError(f"ไม่พบใบรับคืน id {dd_id} ใน ATMS")
        approver = info.get("approver", "").strip()
        rejectable = soup.find("a", href=re.compile(r"/inv/deposit/reject/id/")) is not None
        return {"id": int(dd_id), "code": info["เลขที่"], "approver": approver or None,
                "approved": bool(approver) or rejectable, "items": _wd_items(soup)}

    def _dry_progress(self, n: int) -> IssueProgress:
        return IssueProgress(gm_id=0, done=list(range(n)), approved=False, code=None)

    @staticmethod
    def _reconcile(view: dict, progress: IssueProgress, n_lines: int) -> None:
        """จำนวนบรรทัดที่ ATMS มีต้องตรงกับที่ progress บอก ไม่งั้นยิงต่อแล้วจะซ้ำ/ตกหล่น"""
        have = len(view["items"])
        if have == len(progress.done):
            return
        if have == n_lines:                       # ครบแล้วแต่ progress หาย (เช่น พังตอนอนุมัติ)
            progress.done = list(range(n_lines))
            return
        raise WithdrawPartialError(progress, AtmsOpenJobError(
            f"ใบ {view['code']} ใน ATMS มี {have} บรรทัด แต่ progress บอกว่าส่งแล้ว "
            f"{len(progress.done)} จาก {n_lines} — ไม่ยิงต่อเพื่อกันบรรทัดซ้ำ ให้ตรวจมือ"))

    # ── ยิงใบเบิก ───────────────────────────────────────────────────────────
    def issue_withdraw(self, inventory_id: int, pick_to_id: int, remark_token: str,
                       lines: list[IssueLine], progress: IssueProgress | None = None,
                       remark: str = "", date_ddmmyyyy: str | None = None) -> IssueProgress:
        """
        ยิงใบเบิกแบบทำต่อได้ — คืน IssueProgress ผู้เรียกต้องเก็บไว้ส่งกลับตอนลองใหม่

        คำเตือน: การหาใบเดิมจาก remark_token ค้นเฉพาะ "วันที่สร้างใบ" (date_ddmmyyyy,
        default = วันนี้) → ถ้าลองใหม่ข้ามวันโดยยังไม่มี progress.gm_id ต้องส่งวันที่สร้างเดิมมาด้วย
        ไม่งั้นหาไม่เจอแล้วจะสร้างใบซ้ำ
        """
        if self.dry_run:
            return self._dry_progress(len(lines))
        p = progress or IssueProgress()
        p.done = list(p.done)
        view: dict | None = None
        if not p.gm_id:
            found = self.find_withdraw_by_remark(remark_token, inventory_id,
                                                 date_ddmmyyyy or _today())
            if len(found) > 1:
                raise AtmsOpenJobError(f"พบหลายใบที่มีเลข {remark_token} ใน ATMS: {found}")
            if found:
                p.gm_id = found[0]
            else:
                header = {"type": "withdraw", "inventory_id": inventory_id,
                          "pick_to_id": pick_to_id, "extra_cost": "0.00", "extra_type": "",
                          "remark": f"{remark_token} {remark}".strip()}
                gm = self._post_form(WD_ADD_URL, header)
                if gm is None:
                    ids = self.find_withdraw_by_remark(remark_token, inventory_id,
                                                       date_ddmmyyyy or _today())
                    if len(ids) != 1:
                        raise AtmsOpenJobError(
                            f"สร้างหัวใบแล้วแต่หา id ไม่เจอ (เจอ {len(ids)} ใบ) เลข {remark_token}")
                    gm = ids[0]
                p.gm_id = gm
                view = {}                          # ใบใหม่ ไม่ต้องตรวจจำนวนบรรทัดก่อนยิง
        try:
            if view is None:                       # ใบที่มีอยู่แล้ว → ตรวจก่อนยิงต่อ
                view = self.get_withdraw(p.gm_id)
                p.code = view["code"]
                self._reconcile(view, p, len(lines))
                p.approved = view["approved"]      # ATMS คือความจริง ไม่ใช่ progress
                if p.approved and len(p.done) < len(lines):
                    raise WithdrawPartialError(p, AtmsOpenJobError(
                        f"ใบ {view['code']} อนุมัติแล้วแต่รายการยังไม่ครบ — ตรวจมือ"))
            item_url = f"{BASE}/inv/withdraw.item/add/good_movement_id/{p.gm_id}/inventory_id/{inventory_id}"
            for i, ln in enumerate(lines):
                if i in p.done:
                    continue
                self._post_form(item_url, {
                    "inventory_id": inventory_id, "good_movement_id": p.gm_id,
                    "maintenance_request_item": str(ln.mr_item_id) if ln.mr_item_id else "",
                    "maintenance_request_item_id": ln.mr_item_id if ln.mr_item_id else "",
                    "sku_tag_id": "", "ajax_sku_tag_id": "",
                    "sku": ln.sku_name, "sku_id": ln.sku_id,
                    "serial_no": "", "serial_no_id": ln.serial_no_id or "",
                    "has_serial_no": "", "amount": f"{ln.amount:g}", "remark": ln.remark,
                })
                p.done.append(i)
            if not p.approved:
                self._click(f"{WD_APPROVE_URL}{p.gm_id}")
                v = self.get_withdraw(p.gm_id)
                p.code = v["code"]
                if not v["approved"]:
                    raise AtmsOpenJobError(
                        f"ATMS ยังไม่อนุมัติใบ {v['code']} — บัญชีนี้อาจไม่มีสิทธิ์อนุมัติ")
                p.approved = True
                view = v
            if len(view["items"]) != len(lines):
                raise AtmsOpenJobError(
                    f"ใบ {view['code']} มี {len(view['items'])} บรรทัด ควรเป็น {len(lines)}")
        except WithdrawPartialError:
            raise
        except Exception as e:
            raise WithdrawPartialError(p, e) from e
        return p

    # ── ใบรับคืนเข้าสต็อก (DD) ──────────────────────────────────────────────
    def return_to_stock(self, inventory_id: int, withdraw_id: int, withdraw_code: str,
                        remark_token: str, lines: list[ReturnLine],
                        progress: IssueProgress | None = None,
                        mark_complete: bool = False,
                        date_ddmmyyyy: str | None = None) -> IssueProgress:
        """ใบรับคืน — ค้นใบเดิมจาก supplier_ref_no = remark_token ในวันสร้าง (ดูคำเตือนที่ issue_withdraw)"""
        if self.dry_run:
            return self._dry_progress(len(lines))
        p = progress or IssueProgress()
        p.done = list(p.done)
        fresh = False
        if not p.gm_id:
            day = date_ddmmyyyy or _today()
            found = self.find_deposit_by_remark(remark_token, inventory_id, day)
            if len(found) > 1:
                raise AtmsOpenJobError(f"พบหลายใบที่มีเลข {remark_token} ใน ATMS: {found}")
            if found:
                p.gm_id = found[0]
            else:
                # token ฝังที่ supplier_ref_no เพราะหน้า list ของ DD ค้นจากหมายเหตุไม่ได้
                dd = self._post_form(DD_ADD_URL, {
                    "type": "deposit", "purchase_order_id": "", "inventory_id": inventory_id,
                    "supplier_ref_no": remark_token, "supplier_inv_date": _today(),
                    "extra_cost": "0.00", "withdraw": withdraw_code, "withdraw_id": withdraw_id,
                    "remark": remark_token, "extra_type": ""})
                if dd is None:
                    ids = self.find_deposit_by_remark(remark_token, inventory_id, day)
                    if len(ids) != 1:
                        raise AtmsOpenJobError(
                            f"สร้างใบรับคืนแล้วแต่หา id ไม่เจอ (เจอ {len(ids)} ใบ) เลข {remark_token}"
                            " — ตรวจมือก่อนลองใหม่")
                    dd = ids[0]
                p.gm_id, fresh = dd, True
        try:
            if not fresh:
                v = self.get_deposit(p.gm_id)
                p.code = v["code"]
                self._reconcile(v, p, len(lines))
                p.approved = v["approved"]
                if p.approved and len(p.done) < len(lines):
                    raise WithdrawPartialError(p, AtmsOpenJobError(
                        f"ใบ {v['code']} อนุมัติแล้วแต่รายการยังไม่ครบ — ตรวจมือ"))
            item_url = f"{BASE}/inv/deposit.item/add/good_movement_id/{p.gm_id}/inventory_id/{inventory_id}"
            for i, ln in enumerate(lines):
                if i in p.done:
                    continue
                self._post_form(item_url, {
                    "inventory_id": inventory_id, "good_movement_id": p.gm_id,
                    "purchase_order_item_id": "", "sku_tag_id": "", "ajax_sku_tag_id": "",
                    "sku": ln.sku_name, "sku_id": ln.sku_id, "serial_no": ln.serial_no,
                    "has_serial_no": "", "amount": f"{ln.amount:g}",
                    "unit_price": f"{ln.unit_price:.2f}", "remark": ln.remark,
                })
                p.done.append(i)
            if not p.approved:
                self._click(f"{DD_APPROVE_URL}{p.gm_id}")
                v = self.get_deposit(p.gm_id)
                p.code = v["code"]
                if not v["approved"]:
                    raise AtmsOpenJobError(
                        f"ATMS ยังไม่อนุมัติใบ {v['code']} — บัญชีนี้อาจไม่มีสิทธิ์อนุมัติ")
                p.approved = True
            if mark_complete:
                self._click(f"{WD_RETURNED_URL}{int(withdraw_id)}")
        except WithdrawPartialError:
            raise
        except Exception as e:
            raise WithdrawPartialError(p, e) from e
        return p

    def _set_rows_per_page(self, n: int) -> None:
        try:
            r = self._request("GET", ROW_PER_PAGE_URL, params={"row-per-page": n},
                              allow_redirects=False)
            if not (200 <= r.status_code < 400):
                raise AtmsOpenJobError(f"ตั้ง rows-per-page ไม่ได้ (HTTP {r.status_code})")
        except AtmsOpenJobError:
            if n == ROW_PER_PAGE_LIST:
                raise            # ตั้งไม่ได้ = list จะได้แค่หน้าแรก ห้ามเงียบ
            # คืนค่าไม่ได้ไม่ร้ายแรงพอจะบัง error จริง


def _wd_items(soup: BeautifulSoup) -> list[dict]:
    """ตาราง defaultTable ในหน้า view — ข้ามแถว grand total"""
    table = soup.find("table", class_="defaultTable")
    if table is None:
        return []
    items: list[dict] = []
    for row in _rows_of(table):
        code, _, name = _s(row.get("สินค้า")).partition(" : ")
        if not code:
            continue
        mr = _s(row.get("รายการแจ้งซ่อม")).strip()
        items.append({
            "sku_code": code.strip(),
            "sku_name": name.strip(),
            "amount": _num(row.get("amount", "")),
            "unit_price": _num(row.get("unit price", "")),
            "mr_item_id": int(mr) if mr.isdigit() else None,
        })
    return items
