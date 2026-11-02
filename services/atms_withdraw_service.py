"""
ATMS — ใบเบิก (WD) / ใบรับคืน (DD) ของคลังอะไหล่ (www.mena-atms.com)

ไฟล์นี้ = ฝั่ง "อ่าน" : dropdown ของฟอร์ม, autocomplete, profile, หน้า view/list ของ WD
(ฝั่งเขียน issue_withdraw / return_to_stock เพิ่มใน Task 2)
รายละเอียดหน้า/ฟิลด์/ป้ายทั้งหมดดู docs/atms-withdraw-recon.md

ใช้ AtmsClient เดิมของ openjob (login, retry, จับ session หมดอายุ) โดยไม่แก้พฤติกรรมเดิม
"""

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
PO_INDEX_URL = f"{BASE}/inv/purchase.order/index/"
PO_VIEW_URL = f"{BASE}/inv/purchase.order/view/id/"
PR_INDEX_URL = f"{BASE}/inv/purchase.request/index"
PR_VIEW_URL = f"{BASE}/inv/purchase.request/view/id/"
MR_INDEX_URL = f"{BASE}/veh/maintenance.request/index"
MR_VIEW_URL = f"{BASE}/veh/maintenance.request/view/id/"

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
_PO_ROW_ID = re.compile(r"/inv/purchase\.order/(?:view|edit)/id/(\d+)")
_PR_ROW_ID = re.compile(r"/inv/purchase\.request/(?:view|edit)/id/(\d+)")
_MR_ROW_ID = re.compile(r"/veh/maintenance\.request/view/id/(\d+)")
# MR: หัวคอลัมน์หน้า list (ยืนยันกับหน้าจริงแล้ว 2/11/2026)
_MR_COLS = {
    "code": lambda h: h == "เลขที่แจ้งซ่อม",
    "step": lambda h: h == "ขั้นตอน",
    "branch": lambda h: h == "สาขา",
    "project": lambda h: h == "โปรเจค",
    "vehicle_no": lambda h: h == "เลขรถ",
    "plate": lambda h: h == "ยานพาหนะ",
    "driver": lambda h: h == "พจส.",
    "mechanic": lambda h: h.lower() == "mechanic",
    "date": lambda h: h == "แจ้งซ่อม",
}
MR_SEARCH_LIMIT_DEFAULT = 5
MR_SEARCH_LIMIT_MAX = 20
MR_VIEW_FETCH_MAX = 5         # เพดาน GET หน้า view ต่อการค้นหนึ่งครั้ง (อ่านหมายเหตุ) ไม่ว่า limit เท่าไร
MR_DETAIL_CHARS = 120         # รายละเอียดต่อรายการ
MR_REMARK_CHARS = 300         # หมายเหตุรวม
_NUM = re.compile(r"-?\d[\d,]*\.?\d*")
_REDIRECTS = (301, 302, 303, 307, 308)
_LOC_ID = re.compile(r"/id/(\d+)")
_TZ = ZoneInfo("Asia/Bangkok")


# คอลัมน์ของหน้า list: key → (ตัวเทียบหัวคอลัมน์, index สำรองเมื่อหา header ไม่เจอ)
_WD_COLS = {
    "created_at": (lambda h: h == "วันที่เบิก", 0),
    "code": (lambda h: h == "รหัส", 1),
    "pick_to": (lambda h: h == "จุดประสงค์ในการเบิก", 3),
    "approver": (lambda h: h == "approver", 10),
    "user": (lambda h: h == "ผู้ใช้งาน", 11),
    "remark": (lambda h: h == "หมายเหตุ", 12),
}
_DD_COLS = {
    "code": (lambda h: h == "เลขที่", 0),
    "withdraw": (lambda h: h == "เบิก", 3),
    "supplier_ref_no": (lambda h: h.startswith("เลขที่ใบส่งของ"), 5),
    "created_at": (lambda h: h == "บันทึกเมื่อ", 7),
    "approver": (lambda h: h == "approver", 9),
    "user": (lambda h: h == "ผู้ใช้งาน", 10),
}


# ค้น PO/DD เพื่ออ้างอิง: ต้องมีแค่ "รหัส" ส่วนคอลัมน์อื่นไม่มี = ค่าว่าง (ไม่ถอยไปใช้ index ทั้งชุดแบบ _list_table)
_DOC_COLS = {
    "po": {
        "code": lambda h: h == "รหัส",
        "ref": lambda h: h.startswith("ใบขอสั่งซื้อ"),
        "status": lambda h: h == "สถานะการรับสินค้า",
        "date": lambda h: h == "วันที่",
        "total": lambda h: h == "รวม",
        "supplier": lambda h: h == "ซัพพลายเออร์",
    },
    "dd": {
        "code": lambda h: h == "เลขที่",
        "po": lambda h: h.startswith("ใบสั่งซื้อ"),
        "withdraw": lambda h: h == "เบิก",
        "supplier": lambda h: h == "ซัพพลายเออร์",
        "total": lambda h: h == "จำนวนเงิน",
        "date": lambda h: h == "บันทึกเมื่อ",
    },
}
_DOC_URLS = {
    "po": (PO_INDEX_URL, PO_VIEW_URL, _PO_ROW_ID, ("from_t_date", "to_t_date")),
    "dd": (DD_INDEX_URL, DD_VIEW_URL, _DD_ROW_ID, ("from_created_at", "to_created_at")),
    "pr": (PR_INDEX_URL, PR_VIEW_URL, _PR_ROW_ID, ("from_t_date", "to_t_date")),
}
# PR: หัวคอลัมน์หน้า list (ยืนยันกับหน้า list จริงแล้ว 2026-11-02)
_PR_COLS = {
    "code": lambda h: h.startswith("ใบขอสั่งซื้อ") or h == "รหัส",
    "date": lambda h: h == "วันที่",
    "total": lambda h: h == "รวม",
    "remark": lambda h: h == "หมายเหตุ",
    "plate": lambda h: h == "ทะเบียน",
    "approved": lambda h: h.lower() == "is approved",
}
DOC_SEARCH_DAYS = 90
PR_SEARCH_DAYS = 180
PR_SEARCH_LIMIT_DEFAULT = 5
PR_SEARCH_LIMIT_MAX = 20
PR_DETAIL_FETCH_MAX = 40      # เพดาน GET หน้า view ต่อการค้นหนึ่งครั้ง (เมื่อ list ไม่มีหมายเหตุ)
PR_DETAIL_PACE = 3.0          # วินาทีขั้นต่ำระหว่าง GET หน้า view
DOC_QUERY_MIN = 2


def norm_remark(text: Any) -> str:
    """เทียบหมายเหตุ/ทะเบียน: ตัวพิมพ์เล็ก ตัดช่องว่าง - . / ทิ้ง"""
    return re.sub(r"[\s\-./\u2010-\u2015]+", "", _s(text).lower())


def _check_kind(kind: str) -> None:
    if kind not in _DOC_URLS:
        raise AtmsOpenJobError(f"kind ต้องเป็น po, dd หรือ pr (ได้ {kind!r})")


def _list_table(soup: BeautifulSoup, spec: dict) -> tuple[Any, dict[str, int]]:
    """ตารางที่ใหญ่สุด + แผนที่ key→index จากข้อความหัวคอลัมน์ (หาไม่ครบ = ใช้ index เดิมทั้งชุด)"""
    tables = soup.find_all("table")
    table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)
    fallback = {k: i for k, (_, i) in spec.items()}
    if table is None:
        return None, fallback
    head = table.find("tr")
    heads = [c.get_text(" ", strip=True) for c in head.find_all(["th", "td"])] if head else []
    cols: dict[str, int] = {}
    for key, (match, _) in spec.items():
        idx = next((i for i, h in enumerate(heads) if match(h)), None)
        if idx is None:
            return table, fallback
        cols[key] = idx
    return table, cols


def _same_sku(item: dict, sku_name: str) -> bool:
    n = " ".join(_s(sku_name).split())
    return n in {item.get("sku_full", ""), item.get("sku_name", ""), item.get("sku_code", "")} \
        or n.startswith(f"{item.get('sku_code', '')} :")


def _check_items(view: dict, lines: list) -> None:
    """รายการใน ATMS ต้องตรงกับที่ส่ง (จำนวนบรรทัด + sku + amount แบบจับคู่ไม่สนลำดับ) ไม่งั้นห้ามอนุมัติ"""
    items = list(view["items"])
    if len(items) != len(lines):
        raise AtmsOpenJobError(
            f"ใบ {view['code']} มี {len(items)} บรรทัด ควรเป็น {len(lines)} — ไม่อนุมัติ ให้ตรวจมือ")
    for ln in lines:
        hit = next((it for it in items
                    if _same_sku(it, ln.sku_name) and abs(it["amount"] - float(ln.amount)) < 1e-6), None)
        if hit is None:
            raise AtmsOpenJobError(
                f"ใบ {view['code']} มีรายการไม่ตรงกับที่ส่ง ({ln.sku_name} x {ln.amount:g}) — "
                "ไม่อนุมัติ ให้ตรวจมือ")
        items.remove(hit)


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
        # rows-per-page ผูกกับ user ฝั่ง ATMS → list ที่ทับกันจะตัดหน้าของกันและกัน
        self._list_lock = threading.RLock()

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
    def list_withdraws(self, inventory_id: int, date_ddmmyyyy: str, remark: str = "") -> list[dict]:
        """
        ใบเบิกของคลัง+วันที่ (DD/MM/YYYY) — ตั้ง rows-per-page 1000 ก่อนแล้วคืนค่าเดิมเสมอ
        (ค่านี้ผูกกับ user ฝั่ง ATMS ไม่ใช่ต่อ request → lock ต่อ client ตลอด ตั้ง→อ่าน→คืน)
        remark = กรองหมายเหตุฝั่ง ATMS (ฟอร์มมี name="remark")
        """
        params = {
            "inventory_id": inventory_id,
            "from_created_at": date_ddmmyyyy,
            "to_created_at": date_ddmmyyyy,
            "submit": "ค้นหา",
        }
        if remark:
            params["remark"] = remark
        with self._list_lock:
            self._set_rows_per_page(ROW_PER_PAGE_LIST)
            try:
                soup = self._get_soup(WD_INDEX_URL, params)
            finally:
                self._set_rows_per_page(ROW_PER_PAGE_DEFAULT)
        table, cols = _list_table(soup, _WD_COLS)
        out: list[dict] = []
        for tr in (table.find_all("tr")[1:] if table is not None else []):
            m = next((_ROW_ID.search(a["href"]) for a in tr.find_all("a", href=True)
                      if _ROW_ID.search(a["href"])), None)
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not m or len(cells) <= max(cols.values()):
                continue
            out.append({
                "id": int(m.group(1)),
                "code": cells[cols["code"]],
                "pick_to": cells[cols["pick_to"]],
                "user": cells[cols["user"]],
                "remark": cells[cols["remark"]],
                "created_at": cells[cols["created_at"]],
                "approved": bool(cells[cols["approver"]]),
            })
        return out

    def find_withdraw_by_remark(self, token: str, inventory_id: int,
                                date_ddmmyyyy: str) -> list[int]:
        """id ของใบเบิกในวันนั้นที่หมายเหตุมี token (ไว้กันสร้างซ้ำ / หา id ที่ 302 ไม่บอก)"""
        pat = _token_re(token)
        if pat is None:
            return []
        return [r["id"] for r in self.list_withdraws(inventory_id, date_ddmmyyyy, _s(token).strip())
                if pat.search(r["remark"])]

    def list_deposits(self, inventory_id: int, date_ddmmyyyy: str,
                      supplier_ref_no: str = "") -> list[dict]:
        """ใบรับคืน (DD) ของคลัง+วันที่สร้าง — กรอง supplier_ref_no ฝั่ง ATMS ได้"""
        params = {"inventory_id": inventory_id, "from_created_at": date_ddmmyyyy,
                  "to_created_at": date_ddmmyyyy, "submit": "ค้นหา"}
        if supplier_ref_no:
            params["supplier_ref_no"] = supplier_ref_no
        with self._list_lock:
            self._set_rows_per_page(ROW_PER_PAGE_LIST)
            try:
                soup = self._get_soup(DD_INDEX_URL, params)
            finally:
                self._set_rows_per_page(ROW_PER_PAGE_DEFAULT)
        table, cols = _list_table(soup, _DD_COLS)
        out: list[dict] = []
        for tr in (table.find_all("tr")[1:] if table is not None else []):
            m = next((_DD_ROW_ID.search(a["href"]) for a in tr.find_all("a", href=True)
                      if _DD_ROW_ID.search(a["href"])), None)
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not m or len(cells) <= max(cols.values()):
                continue
            out.append({"id": int(m.group(1)), "code": cells[cols["code"]],
                        "withdraw": cells[cols["withdraw"]],
                        "supplier_ref_no": cells[cols["supplier_ref_no"]],
                        "created_at": cells[cols["created_at"]],
                        "approved": bool(cells[cols["approver"]]), "user": cells[cols["user"]]})
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

    # ── ค้น/ดู PO และ DD (อ่านอย่างเดียว) ──────────────────────────────────────
    def search_docs(self, kind: str, q: str, inventory_id: int | None = None, limit: int = 20,
                    date_from: str | None = None, date_to: str | None = None) -> list[dict]:
        """
        ค้นเลขใบจากหน้า index ด้วย filter code (ATMS ไม่มี autocomplete JSON ของ PO/DD)
        q สั้นกว่า 2 ตัว = [] โดยไม่ยิง ATMS · ไม่ใส่วันที่ = ย้อนหลัง 90 วัน · เรียงใหม่สุด (id มาก→น้อย)
        rows-per-page ใช้ล็อก+ตั้ง/คืนชุดเดียวกับ list_withdraws
        """
        _check_kind(kind)
        q = _s(q).strip()
        if len(q) < DOC_QUERY_MIN:
            return []
        if kind == "pr":
            return self._search_pr(q, inventory_id, limit, date_from, date_to)
        index_url, _, row_id, (k_from, k_to) = _DOC_URLS[kind]
        today = datetime.now(_TZ)
        params: dict[str, Any] = {
            "code": q,
            k_from: date_from or (today - timedelta(days=DOC_SEARCH_DAYS)).strftime("%d/%m/%Y"),
            k_to: date_to or today.strftime("%d/%m/%Y"),
            "submit": "ค้นหา",
        }
        if inventory_id:
            params["inventory_id"] = inventory_id
        with self._list_lock:
            self._set_rows_per_page(ROW_PER_PAGE_LIST)
            try:
                soup = self._get_soup(index_url, params)
            finally:
                self._set_rows_per_page(ROW_PER_PAGE_DEFAULT)
        tables = soup.find_all("table")
        table = max(tables, key=lambda x: len(x.find_all("tr")), default=None)
        if table is None:
            return []
        heads = [c.get_text(" ", strip=True) for c in table.find("tr").find_all(["th", "td"])]
        cols = {k: next((i for i, h in enumerate(heads) if m(h)), None) for k, m in _DOC_COLS[kind].items()}
        if cols["code"] is None:
            return []

        def cell(cells: list[str], key: str) -> str:
            i = cols.get(key)
            return cells[i] if i is not None and i < len(cells) else ""

        out: list[dict] = []
        for tr in table.find_all("tr")[1:]:
            m = next((row_id.search(a["href"]) for a in tr.find_all("a", href=True)
                      if row_id.search(a["href"])), None)
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not m or cols["code"] >= len(cells):
                continue
            total = cell(cells, "total")
            out.append({
                "id": int(m.group(1)),
                "code": cells[cols["code"]],
                "kind": kind,
                "date": cell(cells, "date")[:10],
                "supplier": cell(cells, "supplier"),
                "status": cell(cells, "status") or None,
                "total": _num(total) if total.strip() else None,
                # PO = เลข PR · DD = เลข PO ถ้ามี ไม่งั้นเลข WD (ใบรับคืน)
                "ref": (cell(cells, "ref") if kind == "po"
                        else cell(cells, "po") or cell(cells, "withdraw")),
            })
        out.sort(key=lambda r: r["id"], reverse=True)
        return out[:max(1, int(limit))]

    def search_mrs(self, q: str, limit: int = MR_SEARCH_LIMIT_DEFAULT) -> list[dict]:
        """
        ค้นใบแจ้งซ่อม (MR) จากทะเบียน/เลขรถ — อ่านอย่างเดียว · q < 2 ตัว = [] ไม่ยิง ATMS
        ยิง 2 GET (plate_no, vehicle_no) หน่วงตาม pace · ATMS กรอง substring เอง · รวม+ตัดซ้ำด้วย id · ใหม่สุดก่อน
        ไม่แตะ rows-per-page (หน้าละ 25 แถวพอสำหรับ 5 ใบล่าสุด)
        หลังเลือกผลแล้ว GET หน้า view ทีละใบ (สูงสุด MR_VIEW_FETCH_MAX) เพื่อสร้าง remark + items
        """
        q = _s(q).strip()
        if len(q) < DOC_QUERY_MIN:
            return []
        limit = max(1, min(int(limit or MR_SEARCH_LIMIT_DEFAULT), MR_SEARCH_LIMIT_MAX))
        found: dict[int, dict] = {}
        for key in ("plate_no", "vehicle_no"):
            soup = self._get_soup(MR_INDEX_URL, {key: q, "order_by": "mr.id desc", "submit": "ค้นหา"})
            for row in self._mr_rows(soup):
                found.setdefault(row["id"], row)
        out = sorted(found.values(), key=lambda r: r["id"], reverse=True)[:limit]
        for n, r in enumerate(out):
            r["remark"], r["items"] = "", []
            if n >= MR_VIEW_FETCH_MAX:
                continue
            if n and self.pace > 0:
                time.sleep(max(0.0, PR_DETAIL_PACE - self.pace))
            try:
                r["items"] = self._mr_items(self._get_soup(f"{MR_VIEW_URL}{r['id']}"))
            except Exception:       # หน้า view ใบเดียวพัง ห้ามทำให้การค้นล้ม
                continue
            r["remark"] = self._mr_remark(r["items"])
        return out

    @staticmethod
    def _mr_items(soup: BeautifulSoup) -> list[dict]:
        """รายการซ่อมจากหน้า view: จับคอลัมน์จากข้อความหัว (ประเภทการซ่อม / รายละเอียด)"""
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            heads = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
            ti = next((i for i, h in enumerate(heads) if "ประเภทการซ่อม" in h), None)
            di = next((i for i, h in enumerate(heads) if "รายละเอียด" in h), None)
            if ti is None or di is None:
                continue
            items = []
            for tr in rows[1:]:
                cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip() for c in tr.find_all(["th", "td"])]
                if max(ti, di) >= len(cells):
                    continue
                typ, detail = cells[ti], cells[di][:MR_DETAIL_CHARS].strip()
                if typ or detail:
                    items.append({"type": typ, "detail": detail})
            return items
        return []

    @staticmethod
    def _mr_remark(items: list[dict]) -> str:
        text = " · ".join(f"{i['type']}: {i['detail']}" if i["detail"] else i["type"] for i in items)
        return text[:MR_REMARK_CHARS].strip()

    @staticmethod
    def _mr_rows(soup: BeautifulSoup) -> list[dict]:
        table = soup.find("table", class_="defaultTable")
        if table is None:
            tables = soup.find_all("table")
            table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)
        if table is None or table.find("tr") is None:
            return []
        heads = [c.get_text(" ", strip=True) for c in table.find("tr").find_all(["th", "td"])]
        cols = {k: next((i for i, h in enumerate(heads) if m(h)), None) for k, m in _MR_COLS.items()}
        out: list[dict] = []
        for tr in table.find_all("tr")[1:]:
            m = next((_MR_ROW_ID.search(a["href"]) for a in tr.find_all("a", href=True)
                      if _MR_ROW_ID.search(a["href"])), None)
            if not m:
                continue
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]

            def cell(key: str) -> str:
                i = cols.get(key)
                return cells[i] if i is not None and i < len(cells) else ""
            out.append({
                "id": int(m.group(1)), "code": cell("code"), "date": cell("date")[:10],
                "plate": cell("plate"), "vehicle_no": cell("vehicle_no"), "step": cell("step"),
                "branch": cell("branch"), "project": cell("project"),
                "driver": cell("driver"), "mechanic": cell("mechanic"),
            })
        return out

    def _search_pr(self, q: str, inventory_id: int | None, limit: int,
                   date_from: str | None, date_to: str | None) -> list[dict]:
        """
        ค้น PR จาก "หมายเหตุ" เท่านั้น (ระบุรถจากทะเบียน/เบอร์รถที่พิมพ์ในหมายเหตุ)
        เทียบแบบ norm_remark ทั้งสองข้าง → ไม่ใช้ filter remark ฝั่ง ATMS (มันเทียบตรงตัว ไม่ normalise)
        ดึง list หน้าเดียว 1000 แถว (ย้อน 180 วัน) เรียงใหม่สุด · ถ้าหน้า list ไม่มีคอลัมน์หมายเหตุ
        ต้องเปิดหน้า view ทีละใบ (ใหม่→เก่า) เพื่ออ่านหมายเหตุ: เพดาน PR_DETAIL_FETCH_MAX GET/ครั้ง
        เว้น PR_DETAIL_PACE วินาทีต่อ GET และหยุดทันทีเมื่อได้ครบ limit
        """
        limit = max(1, min(int(limit or PR_SEARCH_LIMIT_DEFAULT), PR_SEARCH_LIMIT_MAX))
        needle = norm_remark(q)
        if len(needle) < DOC_QUERY_MIN:
            return []
        today = datetime.now(_TZ)
        params: dict[str, Any] = {
            "code": "", "user": "", "remark": "", "inventory_id": inventory_id or "",
            "department_id": "", "is_approved": "", "has_po": "",
            "from_t_date": date_from or (today - timedelta(days=PR_SEARCH_DAYS)).strftime("%d/%m/%Y"),
            "to_t_date": date_to or today.strftime("%d/%m/%Y"),
            "submit": "ค้นหา", "order_by": "pr.code desc",
        }
        with self._list_lock:
            self._set_rows_per_page(ROW_PER_PAGE_LIST)
            try:
                soup = self._get_soup(PR_INDEX_URL, params)
            finally:
                self._set_rows_per_page(ROW_PER_PAGE_DEFAULT)
        tables = soup.find_all("table")
        table = max(tables, key=lambda x: len(x.find_all("tr")), default=None)
        if table is None:
            return []
        heads = [c.get_text(" ", strip=True) for c in table.find("tr").find_all(["th", "td"])]
        cols = {k: next((i for i, h in enumerate(heads) if m(h)), None) for k, m in _PR_COLS.items()}
        if cols["code"] is None:
            return []
        rows: list[dict] = []
        for tr in table.find_all("tr")[1:]:
            m = next((_PR_ROW_ID.search(a["href"]) for a in tr.find_all("a", href=True)
                      if _PR_ROW_ID.search(a["href"])), None)
            tds = tr.find_all(["th", "td"])
            cells = [c.get_text(" ", strip=True) for c in tds]
            if not m or cols["code"] >= len(cells):
                continue

            def cell(key: str) -> str:
                i = cols.get(key)
                return cells[i] if i is not None and i < len(cells) else ""

            status = None
            ai = cols["approved"]
            if ai is not None and ai < len(tds):
                img = tds[ai].find("img")
                src = img.get("src", "") if img else ""
                status = "อนุมัติแล้ว" if "check-square" in src else "ยังไม่อนุมัติ" if "x-square" in src else None
            total = cell("total")
            rows.append({
                "id": int(m.group(1)), "code": cells[cols["code"]], "kind": "pr",
                "date": cell("date")[:10], "supplier": "", "status": status,
                "total": _num(total) if total.strip() else None, "ref": "",
                "remark": cell("remark") if cols["remark"] is not None else None,
                "_plate": cell("plate"),
            })
        rows.sort(key=lambda r: r["id"], reverse=True)
        out: list[dict] = []
        fetched = 0
        for r in rows:
            if len(out) >= limit:
                break
            if r["remark"] is None:           # list ไม่มีหมายเหตุ → อ่านจากหน้า view (มีเพดาน)
                if fetched >= PR_DETAIL_FETCH_MAX:
                    break
                if fetched and self.pace > 0:
                    time.sleep(max(0.0, PR_DETAIL_PACE - self.pace))
                fetched += 1
                info = _label_pairs(self._get_soup(f"{PR_VIEW_URL}{r['id']}"))
                r["remark"] = info.get("เหตุผลในการขอ", "")
            # หมายเหตุเป็นหลัก · คอลัมน์ "ทะเบียน" ของ ATMS เป็นตัวช่วย (ใบที่เลือกรถไว้แต่ไม่ได้เขียนในหมายเหตุ)
            if needle in norm_remark(f"{r['remark']} {r.get('_plate', '')}"):
                out.append(r)
        for r in out:
            r.pop("_plate", None)
        return out

    def get_doc(self, kind: str, doc_id: int) -> dict:
        """หน้า view ของ PO/DD/PR → หัวใบ + รายการสินค้า (ไม่รวมแถว grand total/ภาษี/รวมสุทธิ)"""
        _check_kind(kind)
        if kind == "pr":
            return self._get_pr(doc_id)
        soup = self._get_soup(f"{_DOC_URLS[kind][1]}{int(doc_id)}")
        info = _label_pairs(soup)
        code = info.get("รหัส" if kind == "po" else "เลขที่", "")
        if not code:
            raise AtmsNotFoundError(f"ไม่พบ{'ใบสั่งซื้อ' if kind == 'po' else 'ใบรับ'} id {doc_id} ใน ATMS")
        is_po = kind == "po"
        po_label = next((v for k, v in info.items() if k.startswith("ใบสั่งซื้อ") or k.lower().startswith("purchase order")), "")
        when = info.get("วันที่") if is_po else (info.get("รับเมื่อ") or info.get("บันทึกเมื่อ") or "")
        return {
            "id": int(doc_id),
            "code": code,
            "kind": kind,
            "date": _s(when)[:10],
            "supplier": info.get("ซัพพลายเออร์", ""),
            "inventory": info.get("คลังสินค้า", ""),
            "pr": (info.get("ใบขอสั่งซื้อ (PR)") or None) if is_po else None,
            "po": None if is_po else (po_label.strip() or None),
            "withdraw": None if is_po else (info.get("เบิก", "").strip() or None),
            "remark": info.get("หมายเหตุ", ""),
            "lines": _doc_lines(soup, is_po),
        }

    def _get_pr(self, doc_id: int) -> dict:
        soup = self._get_soup(f"{PR_VIEW_URL}{int(doc_id)}")
        info = _label_pairs(soup)
        code = info.get("ใบขอสั่งซื้อ (PR)") or info.get("รหัส") or info.get("เลขที่") or ""
        if not code:
            raise AtmsNotFoundError(f"ไม่พบใบขอซื้อ id {doc_id} ใน ATMS")
        return {
            "id": int(doc_id), "code": code, "kind": "pr",
            "date": _s(info.get("วันที่", ""))[:10],
            "supplier": "",
            "inventory": info.get("คลังสินค้า", ""),
            "pr": None, "po": None, "withdraw": None,
            "remark": info.get("เหตุผลในการขอ", ""),     # หน้า view PR: "หมายเหตุ" คือหมายเหตุอนุมัติ ไม่ใช่เหตุผลขอ
            "plate": info.get("ทะเบียน", ""),
            "lines": _doc_lines(soup, kind="pr"),
        }

    # ═══ ฝั่งเขียน ═══════════════════════════════════════════════════════════
    # กติกาเหล็ก: POST ไม่มี auto-retry เด็ดขาด (POST ซ้ำ = บรรทัด/ใบซ้ำใน ATMS)
    # _request ของ AtmsClient retry เมื่อ connection หลุด → ใช้กับ POST ไม่ได้ จึงมี _post_once
    def _post_once(self, url: str, data: dict) -> Any:
        self._pause()
        for attempt in (0, 1):
            try:
                sess = self.session          # login ล้มเหลว = ยังไม่ได้ POST อะไร → ไม่ใช่ "ไม่ทราบผล"
            except AtmsAuthError:
                raise
            except Exception as e:
                raise AtmsOpenJobError(f"login ATMS ไม่สำเร็จ (ยังไม่ได้ส่งอะไร): {e}") from e
            try:
                r = sess.request("POST", url, data=data, allow_redirects=False,
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
                if gm is not None:
                    p.gm_id = gm        # เก็บไว้ก่อน — ใบมีอยู่จริงแล้ว ห้ามสร้างซ้ำ
                try:
                    ids = self.find_withdraw_by_remark(remark_token, inventory_id,
                                                       date_ddmmyyyy or _today())
                    if gm is None:
                        if len(ids) != 1:
                            raise AtmsOpenJobError(
                                f"สร้างหัวใบแล้วแต่หา id ไม่เจอ (เจอ {len(ids)} ใบ) เลข {remark_token}")
                        gm = ids[0]
                        p.gm_id = gm
                    elif ids != [gm]:
                        raise AtmsOpenJobError(
                            "ค้นใบจากเลขคำขอใน ATMS ไม่เจอ — ตรวจมือก่อนลองใหม่")
                except Exception as e:
                    if p.gm_id:
                        raise WithdrawPartialError(p, e) from e
                    raise
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
                pre = self.get_withdraw(p.gm_id)     # อ่านสดก่อนกดอนุมัติ — ห้ามอนุมัติใบที่ไม่ครบ/ไม่ตรง
                p.code = pre["code"]
                _check_items(pre, lines)
                if not pre["approved"]:
                    self._click(f"{WD_APPROVE_URL}{p.gm_id}")
                    v = self.get_withdraw(p.gm_id)
                    p.code = v["code"]
                    if not v["approved"]:
                        raise AtmsOpenJobError(
                            f"ATMS ยังไม่อนุมัติใบ {v['code']} — บัญชีนี้อาจไม่มีสิทธิ์อนุมัติ")
                    view = v
                else:
                    view = pre
                p.approved = True
            _check_items(view, lines)
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
                if dd is not None:
                    p.gm_id, fresh = dd, True
                try:
                    ids = self.find_deposit_by_remark(remark_token, inventory_id, day)
                    if dd is None:
                        if len(ids) != 1:
                            raise AtmsOpenJobError(
                                f"สร้างใบรับคืนแล้วแต่หา id ไม่เจอ (เจอ {len(ids)} ใบ) เลข {remark_token}"
                                " — ตรวจมือก่อนลองใหม่")
                        dd = ids[0]
                        p.gm_id, fresh = dd, True
                    elif ids != [dd]:
                        raise AtmsOpenJobError(
                            "ค้นใบจากเลขคำขอใน ATMS ไม่เจอ — ตรวจมือก่อนลองใหม่")
                except Exception as e:
                    if p.gm_id:
                        raise WithdrawPartialError(p, e) from e
                    raise
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
                pre = self.get_deposit(p.gm_id)      # อ่านสดก่อนกดอนุมัติ — ห้ามอนุมัติใบที่ไม่ครบ/ไม่ตรง
                p.code = pre["code"]
                _check_items(pre, lines)
                if not pre["approved"]:
                    self._click(f"{DD_APPROVE_URL}{p.gm_id}")
                    v = self.get_deposit(p.gm_id)
                    p.code = v["code"]
                    if not v["approved"]:
                        raise AtmsOpenJobError(
                            f"ATMS ยังไม่อนุมัติใบ {v['code']} — บัญชีนี้อาจไม่มีสิทธิ์อนุมัติ")
                    pre = v
                p.approved = True
            else:
                pre = self.get_deposit(p.gm_id)
            _check_items(pre, lines)
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
        full = _s(row.get("สินค้า")).strip()
        if " : " not in full:          # แถวสรุปใน <tfoot> (ภาษี / รวมสุทธิ) ไม่มี "รหัส : ชื่อ"
            continue
        code, _, name = full.partition(" : ")
        if not code.strip():
            continue
        mr = _s(row.get("รายการแจ้งซ่อม")).strip()
        items.append({
            "sku_full": " ".join(full.split()),
            "sku_code": code.strip(),
            "sku_name": name.strip(),
            "amount": _num(row.get("amount", "")),
            "unit_price": _num(row.get("unit price", "")),
            "mr_item_id": int(mr) if mr.isdigit() else None,
        })
    return items


def _doc_lines(soup: BeautifulSoup, is_po: bool = False, kind: str | None = None) -> list[dict]:
    """
    ตารางรายการของ PO/DD — สินค้าต้องเป็น "รหัส : ชื่อ" (แถวสรุปไม่มี → ข้าม)
    PO: amount=สั่ง, received=คอลัมน์ "รับ", remaining="ค้างรับ" · DD: amount=ที่รับ, received=amount, remaining=None
    is_labour = กลุ่มสินค้าขึ้นต้น "ค่าแรง" (ติดป้ายเฉยๆ ไม่กรองทิ้ง)
    """
    is_pr = kind == "pr"       # PR: amount = จำนวนที่ขอ · received=0 · remaining=amount
    table = soup.find("table", class_="defaultTable")
    if table is None:
        return []
    lines: list[dict] = []
    for row in _rows_of(table):
        full = " ".join(_s(row.get("สินค้า")).split())
        code, sep, _ = full.partition(" : ")
        if not sep or not code.strip():
            continue
        amount = _num(row.get("amount", ""))
        group = _s(row.get("กลุ่มสินค้า")).strip()
        lines.append({
            "sku_code": code.strip(),
            "sku_name": full,
            "group": group,
            "amount": amount,
            "received": 0.0 if is_pr else _num(row.get("รับ", "")) if is_po else amount,
            "remaining": amount if is_pr else _num(row.get("ค้างรับ", "")) if is_po else None,
            "unit_price": _num(row.get("unit price", "")),
            "total": _num(row.get("รวม", "")),
            "remark": _s(row.get("หมายเหตุ")).strip(),
            "is_labour": group.startswith("ค่าแรง"),
        })
    return lines
