import os
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Dict, List

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

DO_SPACES_BUCKET = "mn-bucket"
DO_SPACES_BASE_PATH = "trainer-app"
PRESIGNED_URL_EXPIRES_IN = 3600


@lru_cache(maxsize=1)
def _get_s3_client():
    return boto3.client(
        "s3",
        region_name="sgp1",
        endpoint_url="https://sgp1.digitaloceanspaces.com",
        aws_access_key_id=os.getenv("DO_SPACES_KEY"),
        aws_secret_access_key=os.getenv("DO_SPACES_SECRET"),
    )


def get_image_presigned_urls(inspection_task_id: str, base_path: str = DO_SPACES_BASE_PATH) -> List[str]:
    """Presigned URLs for every top-level image under this folder in
    DigitalOcean Spaces (default folder convention: trainer-app/{id}/).

    Delimiter="/" excludes driver subfolders (drug/ppe/vehicle attachments),
    matching the "overview" image convention used by the trainer-app frontend.
    """
    prefix = f"{base_path}/{inspection_task_id}/"

    try:
        client = _get_s3_client()
        response = client.list_objects_v2(
            Bucket=DO_SPACES_BUCKET,
            Prefix=prefix,
            Delimiter="/",
        )
        contents = response.get("Contents") or []

        return [
            client.generate_presigned_url(
                "get_object",
                Params={"Bucket": DO_SPACES_BUCKET, "Key": obj["Key"]},
                ExpiresIn=PRESIGNED_URL_EXPIRES_IN,
            )
            for obj in contents
        ]
    except (BotoCoreError, ClientError) as exc:
        logger.warning("DigitalOcean Spaces lookup failed for %s: %s", inspection_task_id, exc)
        return []


def get_image_presigned_urls_bulk(
    inspection_task_ids: List[str], base_path: str = DO_SPACES_BASE_PATH
) -> Dict[str, List[str]]:
    """Presigned URLs for multiple folders, fetched concurrently.

    boto3 is synchronous, so a small thread pool is used to run the
    per-id S3 lookups (list_objects_v2 + generate_presigned_url) in
    parallel instead of paying for N sequential network round-trips.
    A failed lookup for a given id still yields an empty list for that
    id (see get_image_presigned_urls), it never raises or aborts the
    other lookups.
    """
    if not inspection_task_ids:
        return {}

    with ThreadPoolExecutor(max_workers=min(10, len(inspection_task_ids))) as executor:
        results = executor.map(
            lambda tid: get_image_presigned_urls(tid, base_path), inspection_task_ids
        )
        return dict(zip(inspection_task_ids, results))
