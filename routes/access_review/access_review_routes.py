"""Access Review (ทบทวนสิทธิ์ผู้ใช้) — /access-review.

The Next.js server verifies the login cookie and forwards the employee id as `X-Employee-Id` (same as /ops).
Missing/unknown → 401. IT admin = services.ops.ops_logic.is_admin (department 21 + listed employee ids).
Errors use FastAPI's {"detail": "<Thai message>"} shape; ArError from the services is mapped by ArAPIRoute.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from database import get_db
from schemas import access_review_schema as s
from services.access_review import admin, importer, logic, repo
from services.access_review.logic import ArError, Caller
from services.ops import ops_logic

MAX_IMPORT_BYTES = 25 * 1024 * 1024
MSG_ADMIN_ONLY = "เฉพาะผู้ดูแลระบบ IT เท่านั้น"


class ArAPIRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except ArError as exc:
                return JSONResponse(status_code=exc.http_status, content={"detail": exc.message})

        return handler


router = APIRouter(prefix="/access-review", tags=["Access Review"], route_class=ArAPIRoute)


def get_caller(
    x_employee_id: Optional[str] = Header(None, alias="X-Employee-Id"),
    db: Session = Depends(get_db),
) -> Caller:
    user = repo.get_user(db, (x_employee_id or "").strip())
    if user is None:
        raise HTTPException(status_code=401, detail=logic.MSG_LOGIN_REQUIRED)
    return Caller(user=user, employee_id=user.employee_id,
                  is_admin=ops_logic.is_admin(user.department_id, user.employee_id))


def require_admin(caller: Caller = Depends(get_caller)) -> Caller:
    if not caller.is_admin:
        raise HTTPException(status_code=403, detail=MSG_ADMIN_ONLY)
    return caller


def _lines(items) -> Optional[List[dict]]:
    return None if items is None else [i.model_dump(exclude_unset=True) for i in items]


def _note(body) -> dict:
    """requester_note only when the client sent the field (absent = leave as is, null/"" = clear)."""
    return {"requester_note": body.requester_note} if body is not None and "requester_note" in body.model_fields_set else {}


# ---------------------------- user ----------------------------

@router.get("/me")
def get_me(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return logic.build_me(db, caller)


@router.get("/role-items")
def get_role_items(role_id: int = Query(...), caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return logic.role_items_view(db, role_id)


@router.get("/me/items")
def get_my_items(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return logic.my_items(db, caller)


@router.put("/me/lines")
def put_my_lines(body: s.LinesIn, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return logic.save_lines(db, caller, _lines(body.lines), **_note(body))


@router.post("/me/submit")
def submit_mine(background_tasks: BackgroundTasks, body: Optional[s.SubmitIn] = None,
                caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return logic.submit(db, caller, _lines(body.lines) if body else None, background_tasks, **_note(body))


@router.get("/pending")
def get_pending(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return logic.pending_list(db, caller)


@router.get("/history")
def get_history(caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return logic.history_list(db, caller)


# ---------------------------- admin ----------------------------

@router.get("/admin/systems")
def admin_list_systems(caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.list_systems(db)


@router.post("/admin/systems", status_code=201)
def admin_create_system(body: s.SystemIn, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.create_system(db, body.model_dump())


@router.put("/admin/systems/{system_id}")
def admin_update_system(system_id: int, body: s.SystemUpdate, caller: Caller = Depends(require_admin),
                        db: Session = Depends(get_db)):
    return admin.update_system(db, system_id, body.model_dump(exclude_unset=True))


@router.get("/admin/roles")
def admin_list_roles(system_id: Optional[int] = None, q: Optional[str] = Query(None, max_length=200),
                     include_inactive: bool = False, caller: Caller = Depends(require_admin),
                     db: Session = Depends(get_db)):
    return admin.list_roles(db, system_id, q, include_inactive)


@router.post("/admin/roles", status_code=201)
def admin_create_role(body: s.RoleIn, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.create_role(db, body.model_dump())


@router.put("/admin/roles/{role_id}")
def admin_update_role(role_id: int, body: s.RoleUpdate, caller: Caller = Depends(require_admin),
                      db: Session = Depends(get_db)):
    return admin.update_role(db, role_id, body.model_dump(exclude_unset=True))


@router.delete("/admin/roles/{role_id}")
def admin_delete_role(role_id: int, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.delete_role(db, role_id)


@router.get("/admin/roles/{role_id}/items")
def admin_list_items(role_id: int, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.list_items(db, role_id)


@router.post("/admin/roles/{role_id}/items", status_code=201)
def admin_create_item(role_id: int, body: s.RoleItemIn, caller: Caller = Depends(require_admin),
                      db: Session = Depends(get_db)):
    return admin.create_item(db, role_id, body.model_dump())


@router.post("/admin/roles/{role_id}/copy-items")
def admin_copy_items(role_id: int, body: s.CopyItemsIn, caller: Caller = Depends(require_admin),
                     db: Session = Depends(get_db)):
    return admin.copy_items(db, role_id, body.source_role_id)


@router.put("/admin/role-items/{item_id}")
def admin_update_item(item_id: int, body: s.RoleItemUpdate, caller: Caller = Depends(require_admin),
                      db: Session = Depends(get_db)):
    return admin.update_item(db, item_id, body.model_dump(exclude_unset=True))


@router.delete("/admin/role-items/{item_id}", status_code=204)
def admin_delete_item(item_id: int, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    admin.delete_item(db, item_id)
    return Response(status_code=204)


@router.get("/admin/user-roles")
def admin_list_user_roles(system_id: Optional[int] = None, role_id: Optional[int] = None,
                          q: Optional[str] = Query(None, max_length=200), include_inactive: bool = False,
                          page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                          caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.list_user_roles(db, system_id, role_id, q, include_inactive, page, page_size)


@router.post("/admin/user-roles", status_code=201)
def admin_create_user_role(body: s.UserRoleIn, caller: Caller = Depends(require_admin),
                           db: Session = Depends(get_db)):
    return admin.create_user_role(db, body.model_dump())


@router.put("/admin/user-roles/{user_role_id}")
def admin_update_user_role(user_role_id: int, body: s.UserRoleUpdate, caller: Caller = Depends(require_admin),
                           db: Session = Depends(get_db)):
    return admin.update_user_role(db, user_role_id, body.model_dump(exclude_unset=True))


@router.delete("/admin/user-roles/{user_role_id}")
def admin_delete_user_role(user_role_id: int, caller: Caller = Depends(require_admin),
                           db: Session = Depends(get_db)):
    return admin.delete_user_role(db, user_role_id)


@router.get("/admin/cycles")
def admin_list_cycles(caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.list_cycles(db)


@router.post("/admin/cycles", status_code=201)
def admin_create_cycle(body: s.CycleIn, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.create_cycle(db, body.model_dump())


@router.put("/admin/cycles/{cycle_id}")
def admin_update_cycle(cycle_id: int, body: s.CycleUpdate, caller: Caller = Depends(require_admin),
                       db: Session = Depends(get_db)):
    return admin.update_cycle(db, cycle_id, body.model_dump(exclude_unset=True))


@router.delete("/admin/cycles/{cycle_id}")
def admin_delete_cycle(cycle_id: int, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.delete_cycle(db, cycle_id)


@router.post("/admin/import")
def admin_import(file: UploadFile = File(...), system: str = Form(...), dry_run: bool = Form(True),
                 caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    system = system.strip().upper()
    if system not in importer.SYSTEMS:
        raise HTTPException(status_code=400, detail="system ต้องเป็น ATMS | WINSPEED | EMAIL")
    data = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="ไฟล์ใหญ่เกิน 25 MB")
    try:
        parsed = importer.parse(system, importer.load_workbook(data))
    except importer.ImportFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return importer.run_import(db, system, parsed, dry_run=dry_run)


@router.get("/admin/reviews")
def admin_reviews(cycle_id: Optional[int] = None,
                  status: Optional[str] = Query(None, pattern="^(not_started|draft|pending|approved|rejected)$"),
                  division_id: Optional[int] = None, caller: Caller = Depends(require_admin),
                  db: Session = Depends(get_db)):
    return admin.reviews_report(db, cycle_id, status, division_id)


@router.get("/admin/removals")
def admin_removals(cycle_id: Optional[int] = None, include_pending: bool = False,
                   caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.removals_report(db, cycle_id, include_pending)


@router.get("/admin/reviews/{review_id}/changes")
def admin_review_changes(review_id: int, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return admin.review_changes(db, review_id)


def _preview_caller(db: Session, employee_id: str) -> Caller:
    """Read-only stand-in for an employee, so IT can see exactly what that person's dialog shows."""
    user = repo.get_user(db, employee_id.strip())
    if user is None:
        raise HTTPException(status_code=404, detail="ไม่พบพนักงาน")
    return Caller(user=user, employee_id=user.employee_id, is_admin=False)


@router.get("/admin/preview/{employee_id}")
def admin_preview_me(employee_id: str, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return logic.build_me(db, _preview_caller(db, employee_id))


@router.get("/admin/preview/{employee_id}/items")
def admin_preview_items(employee_id: str, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)):
    return logic.my_items(db, _preview_caller(db, employee_id))


# ---------------------------- review by id (keep last: /{id}) ----------------------------

@router.get("/{review_id:int}")
def get_review(review_id: int, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return logic.review_detail(db, caller, review_id)


@router.get("/{review_id:int}/items")
def get_review_items(review_id: int, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return logic.review_items(db, caller, review_id)


@router.post("/{review_id:int}/decision")
def post_decision(review_id: int, body: s.DecisionIn, caller: Caller = Depends(get_caller),
                  db: Session = Depends(get_db)):
    return logic.decide(db, caller, review_id, body.action, body.comment)


@router.get("/{review_id:int}/mail-preview")
def get_mail_preview(review_id: int, caller: Caller = Depends(get_caller), db: Session = Depends(get_db)):
    return logic.mail_preview(db, caller, review_id)
