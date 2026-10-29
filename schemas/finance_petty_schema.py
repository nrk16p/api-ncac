"""Pydantic schema ของเบิกเงินสดย่อย"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class FundCreate(BaseModel):
    branch_code: str = Field(min_length=2, max_length=4, pattern=r"^\d{2,4}$")
    division_code: str = Field(min_length=2, max_length=8)
    holder_employee_id: str = Field(min_length=1, max_length=50)
    fund_limit: Optional[Decimal] = Field(default=None, ge=0)
    is_active: bool = True
    updated_by: Optional[str] = None


class FundUpdate(BaseModel):
    holder_employee_id: Optional[str] = Field(default=None, min_length=1, max_length=50)
    fund_limit: Optional[Decimal] = Field(default=None, ge=0)
    is_active: Optional[bool] = None
    updated_by: Optional[str] = None


def _check_spec(spec: Any) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("spec ต้องเป็น object")
    if spec.get("item_based") and not spec.get("item_fields"):
        raise ValueError("ประเภทที่กรอกเป็นรายการต้องมี item_fields อย่างน้อย 1 ช่อง")
    for group in ("header_fields", "item_fields", "attachments"):
        for entry in spec.get(group, []) or []:
            if not isinstance(entry, dict) or not entry.get("key"):
                raise ValueError(f"{group} ทุกรายการต้องมี key")
    return spec


class ExpenseTypeUpdate(BaseModel):
    name_th: str = Field(min_length=1, max_length=120)
    atms_sku: Optional[str] = Field(default=None, max_length=20)
    sort_order: int = 0
    is_active: bool = True
    spec: dict = Field(default_factory=dict)
    updated_by: Optional[str] = None

    @field_validator("spec")
    @classmethod
    def validate_spec(cls, v: Any) -> dict:
        return _check_spec(v)


DRIVER_ROLES = ("พจส.", "พจร.")
# รหัสตำแหน่งยางชุดเดียวกับผังล้อของ WMS (components/tire/fleet-page.tsx): หน้า F1–F4 · หลัง RA1–RA8 · หาง RB1–RB12 · อะไหล่ RB13
TIRE_POSITIONS = frozenset([f"F{i}" for i in range(1, 5)] + [f"RA{i}" for i in range(1, 9)]
                           + [f"RB{i}" for i in range(1, 14)])


class ClaimItemIn(BaseModel):
    event_date: date
    truck_code: Optional[str] = Field(default=None, max_length=20)
    truck_plate: Optional[str] = Field(default=None, max_length=20)
    truck_type: Optional[str] = Field(default=None, max_length=30)
    ref_no: Optional[str] = Field(default=None, max_length=50)
    qty: Decimal = Field(default=Decimal("1"), gt=0)
    rate: Decimal = Field(ge=0)
    remark: Optional[str] = Field(default=None, max_length=255)
    driver_name: Optional[str] = Field(default=None, max_length=150)
    driver_role: Optional[str] = None
    tire_position: Optional[str] = Field(default=None, max_length=120)

    @field_validator("driver_role")
    @classmethod
    def _role(cls, v):
        v = (v or "").strip()
        if not v:
            return None
        if v not in DRIVER_ROLES:
            raise ValueError("เลือกได้แค่ พจส. หรือ พจร.")
        return v

    @field_validator("tire_position")
    @classmethod
    def _positions(cls, v):
        codes = [c.strip().upper() for c in (v or "").split(",") if c.strip()]
        bad = [c for c in codes if c not in TIRE_POSITIONS]
        if bad:
            raise ValueError(f"ตำแหน่งยางไม่ถูกต้อง: {', '.join(bad)}")
        return ",".join(codes) or None


class ClaimIn(BaseModel):
    fund_id: int
    expense_type: str = Field(min_length=1, max_length=20)
    expense_date: date
    driver_employee_id: Optional[str] = Field(default=None, max_length=50)
    driver_name: Optional[str] = Field(default=None, max_length=150)
    payee_name: Optional[str] = Field(default=None, max_length=150)
    detail: dict = Field(default_factory=dict)
    dd_no: Optional[str] = Field(default=None, max_length=50)
    receipt_no: Optional[str] = Field(default=None, max_length=50)
    wht_applicable: bool = False
    # ศูนย์และแผนกของผู้เบิก (ผู้ใช้สั่ง 29/10/2026) — FE บังคับเลือก · ช่องทางเก่าที่ไม่ส่งมา = ว่าง
    cost_center: Optional[str] = Field(default=None, max_length=8)
    items: list[ClaimItemIn] = Field(min_length=1)
    created_by: str = Field(min_length=1, max_length=50)

    @field_validator("cost_center", mode="before")
    @classmethod
    def _known_cost_center(cls, value):
        from services.finance.petty_cost_centers import COST_CENTERS
        code = str(value).strip() if value is not None else ""
        if not code:
            return None
        if code not in COST_CENTERS:
            raise ValueError(f"รหัสศูนย์และแผนก {code} ไม่อยู่ในรายการ")
        return code


class ApproveIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)
    dup_override_reason: Optional[str] = None
    # flow v2: อนุมัติ = จ่ายเงินในจังหวะเดียวกัน · ไม่ส่งมา = วันนี้ตามเวลาไทย
    transfer_date: Optional[date] = None
    # มี พจร. ในใบ → เลขที่ใบรับสภาพหนี้ ส่งมาตอนอนุมัติได้แต่ไม่บังคับแล้ว (ผู้ใช้สั่ง 29/10/2026)
    # ใส่ทีหลังทาง /debt-doc ได้ · ด่านบังคับอยู่ตอนส่งเคลียร์
    debt_doc_no: Optional[str] = Field(default=None, max_length=50)
    # จำนวนไฟล์ที่แนบจริงต่อหมวด เช่น {"weigh_slip": 16, "transfer": 1}
    # BE ไม่มี client S3 ของตัวเอง (ของเดิมก็เป็นแบบนี้) FE นับจาก lib/s3 แล้วส่งมาให้ด่านตรวจ
    attachments: dict[str, int] = Field(default_factory=dict)


class ReturnIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1)


class DdIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)
    dd_no: str = Field(min_length=1, max_length=50)


class DebtDocIn(BaseModel):
    """เจ้าของเงินสดใส่เลขใบรับสภาพหนี้หลังโอนเงิน · ไม่ใส่ min/max ที่นี่
    ให้ route ตอบเป็นภาษาไทย (Pydantic พูดอังกฤษใส่ผู้ใช้)"""
    employee_id: str = Field(min_length=1, max_length=50)
    debt_doc_no: str = ""


class ReviewIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)
    action: Literal["PASS", "SEND_BACK"]
    remark: Optional[str] = None


class RoundIn(BaseModel):
    """เจ้าของเงินสดรวมใบเป็นรอบส่งเคลียร์ · attachments = จำนวนไฟล์ต่อหมวด แยกตามเลขที่ใบ"""
    employee_id: str = Field(min_length=1, max_length=50)
    fund_id: int
    claim_ids: list[int] = Field(min_length=1)
    remark: Optional[str] = None
    attachments: dict[str, dict[str, int]] = Field(default_factory=dict)


class RoundActionIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)


class RoundRejectIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1)


class VoucherIn(BaseModel):
    """เหมือน Advance: เลขที่ตั้งเบิกเว้นว่างได้ วันที่ตั้งเบิกต้องมี"""
    employee_id: str = Field(min_length=1, max_length=50)
    voucher_no: Optional[str] = Field(default=None, max_length=50)
    voucher_date: date


class PayIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=50)
    pay_date: date  # วันที่โอนเงิน
    pay_doc_no: Optional[str] = Field(default=None, max_length=50)


class DocNumbersIn(BaseModel):
    """แก้เลขเอกสารของรอบที่พิมพ์ผิด · ช่องที่ไม่ส่งมา = ไม่แก้ · เลขที่ส่ง null/ว่าง = ลบเลขออก"""
    employee_id: str = Field(min_length=1, max_length=50)
    voucher_no: Optional[str] = Field(default=None, max_length=50)
    voucher_date: Optional[date] = None
    pay_doc_no: Optional[str] = Field(default=None, max_length=50)
