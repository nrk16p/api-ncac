import os
import logging
from functools import lru_cache
from typing import List

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

DO_SPACES_BUCKET = os.getenv("DO_SPACES_BUCKET", "mn-bucket")
DO_SPACES_BASE_PATH = os.getenv("DO_SPACES_BASE_PATH", "trainer-app")
PRESIGNED_URL_EXPIRES_IN = 3600


@lru_cache(maxsize=1)
def _get_s3_client():
    return boto3.client(
        "s3",
        region_name=os.getenv("DO_SPACES_REGION"),
        endpoint_url=os.getenv("DO_SPACES_ENDPOINT"),
        aws_access_key_id=os.getenv("DO_SPACES_KEY"),
        aws_secret_access_key=os.getenv("DO_SPACES_SECRET"),
    )


def get_image_presigned_urls(inspection_task_id: str) -> List[str]:
    """Presigned URLs for every top-level image under this inspection
    task's folder in DigitalOcean Spaces.

    Delimiter="/" excludes driver subfolders (drug/ppe/vehicle attachments),
    matching the "overview" image convention used by the trainer-app frontend.
    """
    prefix = f"{DO_SPACES_BASE_PATH}/{inspection_task_id}/"

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
