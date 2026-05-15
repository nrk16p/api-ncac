from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List
from datetime import datetime, timezone, timedelta
import asyncio

BANGKOK = timezone(timedelta(hours=7))

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
        async def _send(ws: WebSocket):
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(ws)

        await asyncio.gather(*[_send(ws) for ws in list(self.active)])


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
            now = datetime.now(BANGKOK)
            s = datetime.fromisoformat(start)
            e = datetime.fromisoformat(end)
            # ถ้า naive (ไม่มี timezone) → ถือว่าเป็น Bangkok time
            if s.tzinfo is None:
                s = s.replace(tzinfo=BANGKOK)
            if e.tzinfo is None:
                e = e.replace(tzinfo=BANGKOK)
            is_open = s <= now <= e
        except (ValueError, TypeError):
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

PING_INTERVAL = 25  # วินาที — ต่ำกว่า Render load balancer timeout (~55s)

@router.websocket("/ws/system-status")
async def ws_system_status(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_json(compute_status())
        while True:
            try:
                # รอ message จาก client ไม่เกิน PING_INTERVAL วินาที
                text = await asyncio.wait_for(ws.receive_text(), timeout=PING_INTERVAL)
                if text == "get-status":
                    await ws.send_json(compute_status())
            except asyncio.TimeoutError:
                # ไม่มี traffic → ส่ง ping เพื่อ keep-alive
                await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)
