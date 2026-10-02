"""S3 (DigitalOcean Spaces) attachments for the OPS module.

Reuses the shared client / bucket from services/s3_service.py. Only the S3 *key*
is ever stored in Mongo — presigned GET URLs (1h) are generated at read time so
a key never leaks as a public/permanent URL.
"""
from __future__ import annotations

import logging
from typing import Optional

from botocore.exceptions import BotoCoreError, ClientError

from services.s3_service import DO_SPACES_BUCKET, PRESIGNED_URL_EXPIRES_IN, _get_s3_client

logger = logging.getLogger(__name__)


def project_attachment_key(project_id: str, attachment_id: str, safe_name: str) -> str:
    return f"ops_project/{project_id}/{attachment_id}-{safe_name}"


def issue_attachment_key(project_id: str, issue_id: str, attachment_id: str, safe_name: str) -> str:
    return f"ops_project/{project_id}/issues/{issue_id}/{attachment_id}-{safe_name}"


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
