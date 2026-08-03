"""
ATMS — เปิด job แจ้งซ่อม / ขอเปลี่ยนยาง (www.mena-atms.com)

┌ SECTION 1  POST /veh/maintenance.request/add                         (urlencoded)
│            สร้างหัว job → 302 Location .../maintenance_request_id/<id>
└ SECTION 2  POST /veh/maintenance.request.item/add/maintenance_request_id/<id>
             เพิ่มรายการซ่อม (multipart — แนบรูปได้ 3 รูป) ใส่ได้หลายรายการ

ทำไมต้องอ่าน 302 เอง
    ฟอร์มเป็น PHP แบบ Post/Redirect/Get — สำเร็จแล้ว "ต้อง" ได้ 302 เสมอ
    ถ้าปล่อย requests ตาม redirect เองจะเห็นแค่ 200 ของหน้าปลายทาง จนแยกไม่ออก
    ว่าสำเร็จหรือ session หมดอายุ → ยิงด้วย allow_redirects=False แล้วดู Location:
      Location มี /account/user/login  → cookie หมดอายุ  → AtmsAuthError (401)
      Location มี .../id/<n>           → สำเร็จ ดึง id ออกมาได้
      ตอบ 200 (ไม่ redirect)           → validation ไม่ผ่าน → AtmsOpenJobError (422)

ออกแบบเป็น AtmsClient เพื่อ
  · ประหยัด  — login ครั้งเดียวใช้ยาว, cache dropdown/ผลค้นหา, ไม่ตาม redirect โดยไม่จำเป็น
  · ยืดหยุ่น — ส่งแค่ชื่อ (ทะเบียน/คนขับ/ช่าง) ให้ helper หา id เอง, ฟิลด์ใหม่ที่ ATMS
               เพิ่มมาส่งผ่านได้เลยโดยไม่ต้องแก้โค้ด

ใช้งานสั้นสุด
    from services.atms_openjob_service import AtmsClient
    c = AtmsClient(phpsessid="...")          # ไม่ใส่ = login ด้วย ATMS_USERNAME/PASSWORD
    c.create_job(
        {"flow": "request tire", "schedule_at": "30/07/2026 16:51", "branch_id": "4",
         "vehicle": "1ฒย-838", "driver": "Test Driver", "mechanic": "กฤษดา แน่นดี",
         "inform_mile_no": "120000", "tire_positions": ["F1", "F2"]},
        items=[{"maintenance_type_id": "9", "problem": "ยางหน้าสึก"}],
    )
    # vehicle_id / driver_id / mechanic_id / owner_type_id / เลขตำแหน่งยาง → หาให้อัตโนมัติ
"""

import base64
import os
import re
import time
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings()

BASE = "https://www.mena-atms.com"
LOGIN_URL = f"{BASE}/account/user/login"
JOB_ADD_URL = f"{BASE}/veh/maintenance.request/add"
ITEM_ADD_URL = f"{BASE}/veh/maintenance.request.item/add/maintenance_request_id/"
DOMAIN = "www.mena-atms.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# ฟิลด์ + ค่า default ของแต่ละ section (เรียงตามลำดับจริงในฟอร์ม)
# ฟิลด์นอกลิสต์นี้ถูกส่งต่อไปให้ ATMS ตามเดิม — ATMS เพิ่มช่องใหม่ก็ไม่ต้องแก้โค้ด
JOB_FIELDS = {
    "flow": "request tire", "schedule_at": "", "branch_id": "", "owner_type_id": "",
    "mechanic": "", "mechanic_id": "", "accident": "", "accident_id": "",
    "driver": "", "driver_id": "", "vehicle": "", "vehicle_id": "",
    "inform_mile_no": "", "is_broken": "0", "mode": "",
}
ITEM_FIELDS = {
    "maintenance_type_id": "", "problem": "", "has_refer": "0",
    "ref_maintenance_request": "", "ref_maintenance_request_id": "",
    "ref_problem": "", "mode": "",
}
IMAGE_FIELDS = ("image_01", "image_02", "image_03")
MAX_FILE_SIZE = "10485760"   # 10MB — ค่าที่ฟอร์มฝังมาเอง

# autocomplete ของ ATMS: ?q=<คำค้น> → [{"id":948,"name":"1ฒย-838 : ", ...}]
LOOKUP_URLS = {
    "vehicle":  f"{BASE}/veh/vehicle/plate.no.json/",
    "driver":   f"{BASE}/veh/driver/name.json/",
    "mechanic": f"{BASE}/account/user/mechanic.json/",
    "accident": f"{BASE}/veh/accident/code.json/",
}
# ฟิลด์ที่เติม id ให้อัตโนมัติเมื่อผู้เรียกส่งมาแต่ชื่อ  {ชื่อฟิลด์: kind}
AUTO_RESOLVE = {"vehicle": "vehicle", "driver": "driver",
                "mechanic": "mechanic", "accident": "accident"}

ID_PATTERN = re.compile(r"/(?:maintenance_request_id|id)/(\d+)")
TIRE_CODE = re.compile(r"^\s*([A-Za-z]+\s*\d+)")   # "RA3ล้อหลัง..." → "RA3"


class AtmsAuthError(RuntimeError):
    """PHPSESSID หมดอายุ / ไม่มีสิทธิ์ — ATMS เด้งกลับหน้า login"""


class AtmsOpenJobError(RuntimeError):
    """ฟอร์มไม่ผ่าน validation หรือ ATMS ตอบผิดรูปแบบ"""


def _s(v: Any) -> str:
    return "" if v is None else str(v)


class AtmsClient:
    """
    ลูกค้า HTTP ของ ATMS — สร้างครั้งเดียวแล้วใช้ซ้ำ (session/cache อยู่ในนี้)

    phpsessid  ใส่ cookie ตรง ๆ (ไม่ใส่ = login ด้วย env ATMS_USERNAME / ATMS_PASSWORD)
    dry_run    True = ประกอบฟอร์มให้ดูอย่างเดียว ไม่ยิงจริง (ไว้เทสไม่ให้เกิด job ค้าง)
    """

    def __init__(self, phpsessid: str | None = None, username: str | None = None,
                 password: str | None = None, timeout: int = 45,
                 retries: int = 2, dry_run: bool = False):
        self.phpsessid = phpsessid or os.getenv("PHPSESSID")
        self.username = username or os.getenv("ATMS_USERNAME")
        self.password = password or os.getenv("ATMS_PASSWORD")
        self.timeout = timeout
        self.retries = retries
        self.dry_run = dry_run
        self._sess: requests.Session | None = None
        self._options_cache: dict[str, dict[str, dict[str, str]]] = {}
        self._lookup_cache: dict[tuple[str, str], list[dict]] = {}

    # ── transport ───────────────────────────────────────────────────────────
    @property
    def session(self) -> requests.Session:
        """สร้าง/คืน session เดิม — login แค่ครั้งแรกที่ต้องใช้จริง (lazy)"""
        if self._sess is None:
            self._sess = self._new_session()
        return self._sess

    def _new_session(self) -> requests.Session:
        s = requests.Session()
        s.verify = False
        s.headers.update({"User-Agent": UA, "Accept-Language": "th,en;q=0.8"})
        if self.phpsessid:
            s.cookies.set("PHPSESSID", self.phpsessid.split("=")[-1].strip(), domain=DOMAIN)
            return s
        if not self.username or not self.password:
            raise AtmsAuthError("ไม่มี PHPSESSID และไม่ได้ตั้ง ATMS_USERNAME / ATMS_PASSWORD")
        s.get(LOGIN_URL, timeout=self.timeout)   # seed PHPSESSID
        s.post(LOGIN_URL, timeout=self.timeout, allow_redirects=True,
               data={"username": self.username, "password": self.password,
                     "submit": "login", "next": "", "forgotPasswd": "ลืมรหัสผ่าน"})
        if "account/user/login" in s.get(JOB_ADD_URL, timeout=self.timeout).url:
            raise AtmsAuthError("ATMS login ไม่ผ่าน — เช็ค ATMS_USERNAME / ATMS_PASSWORD")
        return s

    @property
    def _can_relogin(self) -> bool:
        """cookie ที่ผู้เรียกส่งมาเอง ต่ออายุแทนไม่ได้ — ต้องมี user/pass เท่านั้น"""
        return not self.phpsessid and bool(self.username and self.password)

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        """ยิงพร้อม retry backoff — ทน ConnectionAborted/throttle ของ ATMS"""
        kw.setdefault("timeout", self.timeout)
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self.session.request(method, url, **kw)
            except requests.RequestException as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise AtmsOpenJobError(f"ต่อ ATMS ไม่ได้: {last}")

    def _submit(self, url: str, fields: list[tuple[str, str]],
                files: list[tuple] | None = None, follow: bool = False) -> dict[str, Any]:
        """
        POST ฟอร์ม แล้วแปลผลจาก 302 — ใช้ร่วมกันทั้ง section 1 และ 2
        follow=False (default) ไม่ตามไปโหลดหน้าปลายทาง = ประหยัดไป 1 request ต่อครั้ง
        """
        if self.dry_run:
            return {"ok": True, "dry_run": True, "url": url, "fields": fields,
                    "files": [f[0] for f in (files or [])]}

        kw: dict[str, Any] = {"data": fields, "allow_redirects": False,
                              "headers": {"Referer": url, "Origin": BASE}}
        if files:
            kw["files"] = files          # ปล่อยให้ requests ตั้ง Content-Type multipart + boundary เอง

        r = self._request("POST", url, **kw)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location", "")
            if "account/user/login" in loc:
                if self._can_relogin:    # session หมดอายุระหว่างใช้งาน → login ใหม่แล้วลองอีกครั้ง
                    self._sess = None
                    r = self._request("POST", url, **kw)
                    loc = r.headers.get("Location", "")
                if "account/user/login" in loc:
                    raise AtmsAuthError("PHPSESSID หมดอายุ — ATMS เด้งกลับหน้า login")
            m = ID_PATTERN.search(loc)
            out = {"ok": True, "status": r.status_code, "location": loc,
                   "url": urljoin(BASE, loc), "id": int(m.group(1)) if m else None}
            if follow:
                out["final_status"] = self._request("GET", out["url"]).status_code
            return out

        if r.status_code == 200:
            raise AtmsOpenJobError("ฟอร์มไม่ผ่าน validation: " +
                                   ("; ".join(_form_errors(r.text)) or "ไม่พบข้อความ error"))
        raise AtmsOpenJobError(f"ATMS ตอบ HTTP {r.status_code}")

    # ── master data (cache ในหน่วยความจำ ยิงจริงครั้งเดียว) ──────────────────
    def options(self, form: str = "job") -> dict[str, dict[str, str]]:
        """
        ค่า dropdown ที่ ATMS ใช้จริง — ไม่ต้องฝัง magic number ไว้ในโค้ด
        form="job"  → flow, branch_id, owner_type_id, tire_positions[]
        form="item" → maintenance_type_id
        """
        if form not in self._options_cache:
            url = JOB_ADD_URL if form == "job" else f"{ITEM_ADD_URL}0"
            soup = BeautifulSoup(self._request("GET", url).text, "html.parser")
            self._options_cache[form] = {
                sel["name"]: {o.get("value", ""): o.get_text(strip=True)
                              for o in sel.find_all("option") if o.get("value")}
                for sel in soup.find_all("select") if sel.get("name")
            }
        return self._options_cache[form]

    def lookup(self, kind: str, q: str, limit: int = 20) -> list[dict]:
        """ค้น autocomplete ของ ATMS (vehicle / driver / mechanic / accident)"""
        if kind not in LOOKUP_URLS:
            raise ValueError(f"kind ต้องเป็นหนึ่งใน {sorted(LOOKUP_URLS)}")
        key = (kind, q.strip())
        if key not in self._lookup_cache:
            r = self._request("GET", LOOKUP_URLS[kind],
                              params={"q": key[1], "limit": limit},
                              headers={"X-Requested-With": "XMLHttpRequest"})
            try:
                self._lookup_cache[key] = r.json() or []
            except ValueError:
                self._lookup_cache[key] = []
        return self._lookup_cache[key]

    def resolve(self, kind: str, q: str) -> dict | None:
        """คืนรายการแรกที่ตรง — ใช้เติม id ให้อัตโนมัติ"""
        return next(iter(self.lookup(kind, q, limit=5)), None)

    def tire_value(self, pos: Any) -> str:
        """รับได้ทั้งเลข ('1'), รหัสตำแหน่ง ('F1', 'RA3') หรือชื่อเต็ม → คืน value ของ ATMS"""
        raw = _s(pos).strip()
        if raw.isdigit():
            return raw
        want = raw.replace(" ", "").upper()
        for value, label in self.options("job").get("tire_positions[]", {}).items():
            code = TIRE_CODE.match(label)
            if code and code.group(1).replace(" ", "").upper() == want:
                return value
            if label.replace(" ", "").upper().startswith(want):
                return value
        raise AtmsOpenJobError(f"ไม่รู้จักตำแหน่งยาง {raw!r}")

    # ── section 1 ───────────────────────────────────────────────────────────
    def build_job_form(self, payload: Mapping[str, Any] | Iterable[Mapping[str, Any]],
                       resolve: bool = True) -> tuple[list[tuple[str, str]], list[str]]:
        """
        ประกอบฟอร์ม section 1 → (form, unresolved)
        form เป็น list of (key, value) เพราะ tire_positions[] ส่งซ้ำได้
        payload รับได้ทั้ง dict และแบบ Postman [{"key":..., "value":...}]
        resolve=True: ฟิลด์ไหนมีชื่อแต่ไม่มี id → ยิง autocomplete เติมให้
        """
        data = _as_dict(payload)
        tires = data.pop("tire_positions", None) or data.pop("tire_positions[]", None) or []
        if isinstance(tires, (str, int)):
            tires = [tires]

        unresolved: list[str] = []
        if resolve:
            for field, kind in AUTO_RESOLVE.items():
                name, id_field = _s(data.get(field)).strip(), f"{field}_id"
                if not name or _s(data.get(id_field)).strip():
                    continue                       # ไม่มีชื่อ หรือมี id อยู่แล้ว → ไม่ต้องยิง
                hit = self.resolve(kind, name)
                if not hit:
                    # หาไม่เจอ = ปล่อยเป็น free text เหมือนที่ผู้ใช้พิมพ์เองในหน้าเว็บ
                    # แล้วให้ ATMS เป็นคนตัดสินว่าจำเป็นต้องมี id ไหม (ถ้าจำเป็นจะได้ 422)
                    unresolved.append(field)
                    continue
                data[field], data[id_field] = hit.get("name", name), _s(hit.get("id"))
                # ทะเบียนรถผูก owner_type มาด้วย — เติมให้ถ้าผู้เรียกไม่ได้ระบุ
                if field == "vehicle" and not _s(data.get("owner_type_id")).strip():
                    data["owner_type_id"] = _s(hit.get("owner_type_id"))

        form = [(k, _s(data.pop(k, d))) for k, d in JOB_FIELDS.items()]
        form += [(k, _s(v)) for k, v in data.items()]          # ฟิลด์นอกลิสต์ ส่งต่อตามเดิม
        form += [("tire_positions[]", self.tire_value(t)) for t in tires]
        return form, unresolved

    def open_job(self, payload: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
                 resolve: bool = True, follow: bool = False, **overrides) -> dict[str, Any]:
        """
        เปิดหัว job (section 1) → {"ok", "status", "location", "url", "id", "unresolved"}
        `id` คือ maintenance_request_id ที่เอาไปใช้ต่อ section 2
        `unresolved` = ฟิลด์ที่หา id ไม่เจอ ส่งเป็น free text ไปแทน (ไว้เตือนผู้เรียก)
        """
        merged = {**_as_dict(payload or {}), **overrides}
        form, unresolved = self.build_job_form(merged, resolve=resolve)
        res = self._submit(JOB_ADD_URL, form, follow=follow)
        res["maintenance_request_id"] = res.get("id")
        res["unresolved"] = unresolved
        return res

    # ── section 2 ───────────────────────────────────────────────────────────
    def add_item(self, request_id: int | str, item: Mapping[str, Any] | None = None,
                 follow: bool = False, **overrides) -> dict[str, Any]:
        """
        เพิ่ม 1 รายการซ่อมเข้า job (section 2 — multipart เพราะมีช่องแนบรูป)
        item["images"] รับได้: path ไฟล์ / bytes / base64 / data-URI / (ชื่อไฟล์, bytes, mime)
        """
        data = {**ITEM_FIELDS, **_as_dict(item or {}), **overrides}
        images = data.pop("images", None)
        fields = [("maintenance_request_id", _s(request_id)), ("MAX_FILE_SIZE", MAX_FILE_SIZE)]
        fields += [(k, _s(v)) for k, v in data.items()]

        res = self._submit(f"{ITEM_ADD_URL}{request_id}", fields,
                           files=_image_parts(images), follow=follow)
        res["maintenance_request_id"] = int(request_id) if _s(request_id).isdigit() else request_id
        return res

    def add_items(self, request_id: int | str,
                  items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """เพิ่มหลายรายการรวดเดียว (session เดียว ไม่ login ซ้ำ)"""
        return [self.add_item(request_id, it) for it in items]

    def create_job(self, payload: Mapping[str, Any] | Iterable[Mapping[str, Any]],
                   items: Iterable[Mapping[str, Any]] = (), **overrides) -> dict[str, Any]:
        """section 1 → section 2 ต่อกันในครั้งเดียว"""
        job = self.open_job(payload, **overrides)
        rid = job.get("id")
        if not rid and not self.dry_run:
            raise AtmsOpenJobError(f"เปิด job แล้วแต่หา id ใน Location ไม่เจอ: {job.get('location')}")
        return {"job": job, "items": self.add_items(rid, items) if items else []}


# ── helper ระดับโมดูล ────────────────────────────────────────────────────────
def _as_dict(payload: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """รองรับ payload แบบ Postman [{"key":..,"value":..}] ให้เท่ากับ dict ปกติ"""
    if isinstance(payload, Mapping):
        return dict(payload)
    out: dict[str, Any] = {}
    for row in payload:
        k, v = str(row["key"]), row.get("value")
        if k.endswith("[]"):                        # เช่น tire_positions[] ที่ส่งซ้ำหลายบรรทัด
            out.setdefault(k[:-2], []).append(v)
        else:
            out[k] = v
    return out


def _image_parts(images: Any) -> list[tuple[str, tuple]]:
    """
    เตรียม multipart ช่อง image_01..03 ให้เหมือน browser
    ช่องที่ไม่ได้แนบต้องส่งเป็น part ว่าง filename="" ไม่ใช่ตัดทิ้ง
    """
    if images is None:
        images = []
    elif isinstance(images, (str, bytes, bytearray, tuple)):
        images = [images]

    ready: list[tuple] = []
    for img in list(images)[:3]:
        if img is None:
            continue
        if isinstance(img, tuple):                       # (filename, bytes, mime)
            ready.append(img)
        elif isinstance(img, (bytes, bytearray)):
            ready.append(("image.jpg", bytes(img), "image/jpeg"))
        elif os.path.exists(str(img)):                   # path ไฟล์
            with open(str(img), "rb") as fh:
                ready.append((os.path.basename(str(img)), fh.read(), "image/jpeg"))
        else:                                            # base64 / data-URI
            raw = str(img).split(",", 1)[1] if str(img).startswith("data:") else str(img)
            try:
                ready.append(("image.jpg", base64.b64decode(raw, validate=True), "image/jpeg"))
            except Exception:
                raise AtmsOpenJobError("images: ต้องเป็น path ไฟล์, bytes, base64 หรือ data-URI")

    return [(f, ready[i] if i < len(ready) else ("", b"", "application/octet-stream"))
            for i, f in enumerate(IMAGE_FIELDS)]


def _form_errors(html: str) -> list[str]:
    """ดึงข้อความ error จากหน้าที่ ATMS render กลับมาเมื่อ validation ไม่ผ่าน"""
    msgs: list[str] = []
    for m in re.finditer(
        r'<[^>]*class="[^"]*(?:alert|error|has-error|help-block|text-danger)[^"]*"[^>]*>(.*?)</',
        html, re.S | re.I,
    ):
        txt = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
        if txt and txt not in msgs:
            msgs.append(txt)
    return msgs[:10]


# client ที่ใช้ซ้ำได้ต่อ 1 cookie/credential — กันการ login ใหม่ทุก request
_CLIENTS: dict[str, AtmsClient] = {}


def get_client(phpsessid: str | None = None, **kw) -> AtmsClient:
    """คืน AtmsClient ที่ cache ไว้ (session + master data ถูกใช้ซ้ำ)"""
    key = phpsessid or os.getenv("PHPSESSID") or os.getenv("ATMS_USERNAME") or "-"
    if kw or key not in _CLIENTS:
        client = AtmsClient(phpsessid, **kw)
        if kw:
            return client                     # ตั้งค่าพิเศษ (เช่น dry_run) ไม่เอาไป cache
        _CLIENTS[key] = client
    return _CLIENTS[key]


# ── wrapper แบบฟังก์ชัน (ของเดิม — เรียกใช้ได้เหมือนเคย) ─────────────────────
def open_job(payload, phpsessid=None, session=None, **kw):
    return _client_of(phpsessid, session).open_job(payload, **kw)


def add_job_item(maintenance_request_id, item, phpsessid=None, session=None, **kw):
    return _client_of(phpsessid, session).add_item(maintenance_request_id, item, **kw)


def add_job_items(maintenance_request_id, items, phpsessid=None, session=None, **kw):
    return _client_of(phpsessid, session).add_items(maintenance_request_id, items, **kw)


def open_job_with_items(payload, items, phpsessid=None, session=None, **kw):
    return _client_of(phpsessid, session).create_job(payload, items, **kw)


def _client_of(phpsessid: str | None, session: AtmsClient | None) -> AtmsClient:
    return session if isinstance(session, AtmsClient) else get_client(phpsessid)
