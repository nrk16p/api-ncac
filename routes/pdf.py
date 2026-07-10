"""
PDF conversion — Office (.docx ฯลฯ) → PDF

ค่าเริ่มต้น: แปลงด้วย LibreOffice (soffice) ที่ติดตั้งมากับ image เอง (self-contained)
ถ้าตั้ง env GOTENBERG_URL ไว้ → จะ proxy ไป Gotenberg แทน (ทางเลือก)
"""
import os
import shutil
import mimetypes
import subprocess
import tempfile
from urllib.parse import quote

import requests
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response, JSONResponse

router = APIRouter(prefix="/pdf", tags=["PDF"])

GOTENBERG_URL = os.getenv("GOTENBERG_URL", "").rstrip("/")
CONVERT_TIMEOUT = int(os.getenv("GOTENBERG_TIMEOUT", "120"))
SOFFICE = os.getenv("SOFFICE_BIN") or shutil.which("soffice") or shutil.which("libreoffice")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _convert_local(data: bytes, filename: str) -> bytes:
    """แปลงด้วย LibreOffice headless — ใช้ UserInstallation แยกต่อครั้ง กันชนกันตอนเรียกพร้อมกัน"""
    if not SOFFICE:
        raise HTTPException(500, "LibreOffice (soffice) not found in image")
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, filename)
        with open(src, "wb") as f:
            f.write(data)
        profile = os.path.join(d, "lo_profile")
        try:
            subprocess.run(
                [
                    SOFFICE, "--headless", "--nologo", "--nofirststartwizard",
                    f"-env:UserInstallation=file://{profile}",
                    "--convert-to", "pdf", "--outdir", d, src,
                ],
                check=True, timeout=CONVERT_TIMEOUT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, "HOME": d},
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "LibreOffice conversion timed out")
        except subprocess.CalledProcessError as e:
            raise HTTPException(500, f"LibreOffice failed: {e.stderr.decode('utf-8', 'ignore')[:300]}")
        pdf_path = os.path.join(d, os.path.splitext(os.path.basename(filename))[0] + ".pdf")
        if not os.path.exists(pdf_path):
            raise HTTPException(500, "LibreOffice produced no PDF")
        with open(pdf_path, "rb") as f:
            return f.read()


def _convert_gotenberg(data: bytes, filename: str, content_type: str) -> bytes:
    try:
        resp = requests.post(
            f"{GOTENBERG_URL}/forms/libreoffice/convert",
            files={"files": (filename, data, content_type)},
            timeout=CONVERT_TIMEOUT,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"Gotenberg unreachable: {e}")
    if resp.status_code != 200:
        raise HTTPException(502, f"Gotenberg error {resp.status_code}: {resp.text[:300]}")
    return resp.content


@router.get("/health")
def pdf_health():
    """เช็คตัวแปลง PDF พร้อมใช้ไหม"""
    if GOTENBERG_URL:
        try:
            r = requests.get(f"{GOTENBERG_URL}/health", timeout=10)
            return {"ok": r.status_code == 200, "engine": "gotenberg", "url": GOTENBERG_URL}
        except requests.RequestException as e:
            return JSONResponse({"ok": False, "engine": "gotenberg", "error": str(e)}, status_code=502)
    return {"ok": bool(SOFFICE), "engine": "libreoffice", "soffice": SOFFICE}


@router.post("/convert")
def convert_office_to_pdf(file: UploadFile = File(...)):
    """รับไฟล์ Office (.docx ฯลฯ) → คืน PDF"""
    filename = file.filename or "document.docx"
    data = file.file.read()
    if not data:
        raise HTTPException(400, "empty file")
    content_type = file.content_type or mimetypes.guess_type(filename)[0] or DOCX_MIME

    pdf = _convert_gotenberg(data, filename, content_type) if GOTENBERG_URL else _convert_local(data, filename)

    pdf_name = os.path.splitext(os.path.basename(filename))[0] + ".pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(pdf_name)}"},
    )
