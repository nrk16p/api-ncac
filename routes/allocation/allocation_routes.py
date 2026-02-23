from fastapi import APIRouter, UploadFile, File
from fastapi.responses import StreamingResponse
from fastapi.concurrency import run_in_threadpool
from services.allocation_service import process_files
import io

router = APIRouter(
    prefix="/allocation",
    tags=["Allocation"]
)


@router.post("/upload")
async def upload_allocation_files(
    ldt_file: UploadFile = File(...),
    gpm_file: UploadFile = File(...),
    cost_file: UploadFile = File(...)
):
    """
    Upload 3 Excel files:
    - ldt_file
    - gpm_file
    - cost_file

    Returns:
        allocation_result.xlsx
    """

    # 👇 Reset file pointer (important for pandas)
    ldt_file.file.seek(0)
    gpm_file.file.seek(0)
    cost_file.file.seek(0)

    # 👇 Run heavy pandas logic in threadpool (prevents blocking event loop)
    output = await run_in_threadpool(
        process_files,
        ldt_file.file,
        gpm_file.file,
        cost_file.file
    )

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=allocation_result.xlsx"
        }
    )