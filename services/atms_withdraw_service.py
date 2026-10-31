"""
ATMS — ใบเบิก (WD) / ใบรับคืน (DD) ของคลังอะไหล่ (www.mena-atms.com)

ไฟล์นี้ = ฝั่ง "อ่าน" : dropdown ของฟอร์ม, autocomplete, profile, หน้า view/list ของ WD
(ฝั่งเขียน issue_withdraw / return_to_stock เพิ่มใน Task 2)
รายละเอียดหน้า/ฟิลด์/ป้ายทั้งหมดดู docs/atms-withdraw-recon.md

ใช้ AtmsClient เดิมของ openjob (login, retry, จับ session หมดอายุ) โดยไม่แก้พฤติกรรมเดิม
"""

import re
import time
from typing import Any, Literal

from bs4 import BeautifulSoup

from services.atms_openjob_service import (  # noqa: F401  (re-export ให้ผู้เรียกใช้ที่เดียว)
    BASE,
    AtmsAuthError,
    AtmsClient,
    AtmsNotFoundError,
    AtmsOpenJobError,
    _label_pairs,
    _rows_of,
    _s,
)

WD_ADD_URL = f"{BASE}/inv/withdraw/add"
WD_VIEW_URL = f"{BASE}/inv/withdraw/view/id/"
WD_INDEX_URL = f"{BASE}/inv/withdraw/index/"
PROFILE_URL = f"{BASE}/account/user/profile"
ROW_PER_PAGE_URL = f"{BASE}/account/user/set.row.per.page/"

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
_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


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
    """ฝั่งอ่านของ WD — pace = หน่วง (วินาที) ระหว่าง request เขียน ใช้ใน Task 2"""

    def __init__(self, *args, pace: float = 1.0, **kw):
        super().__init__(*args, **kw)
        self.pace = pace

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
        r = self._request("GET", WD_LOOKUP_URLS[kind], params=params,
                          headers={"X-Requested-With": "XMLHttpRequest"},
                          allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308) \
                and LOGIN_PATH in r.headers.get("Location", ""):
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
        want = _s(token).strip().lower()
        if not want:
            return []
        return [r["id"] for r in self.list_withdraws(inventory_id, date_ddmmyyyy)
                if want in r["remark"].lower()]

    def _set_rows_per_page(self, n: int) -> None:
        try:
            self._request("GET", ROW_PER_PAGE_URL, params={"row-per-page": n},
                          allow_redirects=False)
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
