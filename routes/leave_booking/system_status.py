from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List
from datetime import datetime, timezone
import asyncio

router = APIRouter()

# In-memory store (mirrors GAS PropertiesService)
_schedule = {
    "override": False,
    "start": "",
    "end": ""
}


class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


def compute_status() -> dict:
    """คำนวณ system status เหมือน getSystemStatus() ใน GAS"""
    override = _schedule["override"]
    start = _schedule["start"]
    end = _schedule["end"]
    is_open = False

    if override:
        is_open = True
    elif start and end:
        try:
            now = datetime.now(timezone.utc)
            s = datetime.fromisoformat(start.replace("Z", "+00:00"))
            e = datetime.fromisoformat(end.replace("Z", "+00:00"))
            is_open = s <= now <= e
        except ValueError:
            is_open = False

    return {
        "isOpen": is_open,
        "override": override,
        "start": start,
        "end": end,
        "checkedAt": datetime.now(timezone.utc).isoformat()
    }


async def periodic_broadcast():
    """Broadcast status ทุก 60 วินาที เพื่อ sync isOpen เมื่อถึงเวลา start/end"""
    while True:
        await asyncio.sleep(60)
        await manager.broadcast(compute_status())


# ── Pydantic Models ──────────────────────────────────────────────────────────

class ScheduleIn(BaseModel):
    override: bool
    start: str = ""
    end: str = ""


class OverrideIn(BaseModel):
    override: bool


# ── REST Endpoints ───────────────────────────────────────────────────────────

@router.get("/system-status")
async def get_system_status():
    """ดึง status ปัจจุบัน (output เหมือน getSystemStatus() ใน GAS)"""
    return compute_status()


@router.get("/system-schedule")
async def get_system_schedule():
    """ดึก raw schedule settings"""
    return _schedule.copy()


@router.post("/system-schedule")
async def post_system_schedule(payload: ScheduleIn):
    """อัปเดต schedule → broadcast ไปทุก WebSocket client"""
    _schedule["override"] = payload.override
    _schedule["start"] = payload.start
    _schedule["end"] = payload.end
    status = compute_status()
    await manager.broadcast(status)
    return {"success": True, "status": status}


@router.put("/system-schedule/override")
async def toggle_override(payload: OverrideIn):
    """เปิด/ปิดระบบทันทีโดยไม่ต้องกำหนด start/end"""
    _schedule["override"] = payload.override
    status = compute_status()
    await manager.broadcast(status)
    return {"success": True, "status": status}


# ── WebSocket ────────────────────────────────────────────────────────────────

@router.websocket("/ws/system-status")
async def ws_system_status(ws: WebSocket):
    await manager.connect(ws)
    try:
        # ส่ง status ทันทีที่ client เชื่อมต่อ
        await ws.send_json(compute_status())
        while True:
            text = await ws.receive_text()
            if text == "get-status":
                await ws.send_json(compute_status())
    except WebSocketDisconnect:
        manager.disconnect(ws)
