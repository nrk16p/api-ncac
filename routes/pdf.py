"""
PDF conversion proxy → Gotenberg (self-hosted LibreOffice container)

ส่งไฟล์ Office (.docx/.xlsx/.pptx ฯลฯ) มาที่ endpoint นี้ แล้วได้ PDF กลับ
ตัวแปลงจริงคือ Gotenberg (LibreOffice) ที่รันเป็น container แยก
ตั้งค่า env: GOTENBERG_URL = URL ของ Gotenberg (เช่น http://gotenberg:3000
หรือ https://<gotenberg-service>.onrender.com)
"""
import os
import mimetypes
from urllib.parse import quote

import requests
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response, JSONResponse

router = APIRouter(prefix="/pdf", tags=["PDF"])

GOTENBERG_URL = os.getenv("GOTENBERG_URL", "").rstrip("/")
CONVERT_TIMEOUT = int(os.getenv("GOTENBERG_TIMEOUT", "120"))

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/health")
def pdf_health():
    """เช็คว่า Gotenberg เชื่อมต่อได้ไหม"""
    if not GOTENBERG_URL:
        return JSONResponse({"ok": False, "error": "GOTENBERG_URL not set"}, status_code=503)
    try:
        r = requests.get(f"{GOTENBERG_URL}/health", timeout=10)
        return {"ok": r.status_code == 200, "gotenberg": GOTENBERG_URL, "status": r.status_code}
    except requests.RequestException as e:
        return JSONResponse(
            {"ok": False, "gotenberg": GOTENBERG_URL, "error": str(e)}, status_code=502
        )


@router.post("/convert")
def convert_office_to_pdf(file: UploadFile = File(...)):
    """รับไฟล์ Office (.docx ฯลฯ) → คืน PDF (แปลงด้วย Gotenberg/LibreOffice)"""
    if not GOTENBERG_URL:
        raise HTTPException(500, "GOTENBERG_URL is not configured on the server")

    filename = file.filename or "document.docx"
    content = file.file.read()
    if not content:
        raise HTTPException(400, "empty file")

    content_type = file.content_type or mimetypes.guess_type(filename)[0] or DOCX_MIME

    try:
        resp = requests.post(
            f"{GOTENBERG_URL}/forms/libreoffice/convert",
            files={"files": (filename, content, content_type)},
            timeout=CONVERT_TIMEOUT,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"Gotenberg unreachable: {e}")

    if resp.status_code != 200:
        raise HTTPException(502, f"Gotenberg error {resp.status_code}: {resp.text[:300]}")

    pdf_name = os.path.splitext(os.path.basename(filename))[0] + ".pdf"
    return Response(
        content=resp.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(pdf_name)}",
        },
    )
