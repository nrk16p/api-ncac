"""S3 (DigitalOcean Spaces) attachments for the OPS module.

Reuses the shared client / bucket from services/s3_service.py. Only the S3 *key*
is ever stored in Mongo — presigned GET URLs (1h) are generated at read time so
a key never leaks as a public/permanent URL.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from botocore.exceptions import BotoCoreError, ClientError

from services.s3_service import DO_SPACES_BUCKET, PRESIGNED_URL_EXPIRES_IN, _get_s3_client

logger = logging.getLogger(__name__)


def project_attachment_key(project_id: str, attachment_id: str, safe_name: str) -> str:
    return f"ops_project/{project_id}/{attachment_id}-{safe_name}"


def issue_attachment_key(project_id: str, issue_id: str, attachment_id: str, safe_name: str) -> str:
    return f"ops_project/{project_id}/issues/{issue_id}/{attachment_id}-{safe_name}"


# folder for tasks with no project (a standalone task request); project ids are OPS-YYYY-NNN, so no clash
STANDALONE_FOLDER = "_standalone"


def _task_folder(project_id: Optional[str]) -> str:
    return project_id or STANDALONE_FOLDER


def task_attachment_key(project_id: Optional[str], task_id: str, attachment_id: str, safe_name: str) -> str:
    """project_id at upload time — a task moved to (or attached to) a project keeps its old keys."""
    return f"ops_project/{_task_folder(project_id)}/tasks/{task_id}/{attachment_id}-{safe_name}"


def task_request_attachment_key(project_id: Optional[str], task_id: str, attachment_id: str, safe_name: str) -> str:
    """Files the requester attaches to a task request (พัฒนาเพิ่ม) — kept apart from the note pictures."""
    return f"ops_project/{_task_folder(project_id)}/tasks/{task_id}/request/{attachment_id}-{safe_name}"


def comment_attachment_key(project_id: str, comment_id: str, attachment_id: str, safe_name: str) -> str:
    """For an issue comment, project_id is the issue's project_id (see ops_routes)."""
    return f"ops_project/{project_id}/comments/{comment_id}/{attachment_id}_{safe_name}"


def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    client = _get_s3_client()
    try:
        client.put_object(Bucket=DO_SPACES_BUCKET, Key=key, Body=data, ContentType=content_type)
    except (BotoCoreError, ClientError) as exc:
        logger.error("ops attachment upload failed for key=%s: %s", key, exc)
        raise


def presigned_get_url(key: str) -> Optional[str]:
    client = _get_s3_client()
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": DO_SPACES_BUCKET, "Key": key},
            ExpiresIn=PRESIGNED_URL_EXPIRES_IN,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.warning("ops attachment presign failed for key=%s: %s", key, exc)
        return None


def delete_objects(keys: Iterable[str]) -> None:
    """Best-effort bulk delete (used when a comment is deleted). Raises on failure —
    callers that must never fail the request (e.g. delete_comment) catch and log it."""
    keys = [k for k in keys if k]
    if not keys:
        return
    client = _get_s3_client()
    try:
        client.delete_objects(Bucket=DO_SPACES_BUCKET, Delete={"Objects": [{"Key": k} for k in keys]})
    except (BotoCoreError, ClientError) as exc:
        logger.error("ops attachment delete failed for keys=%s: %s", keys, exc)
        raise
