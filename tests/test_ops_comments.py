"""OPS comments with image attachments — routes/ops/ops_routes.py's
create_project_comment / create_issue_comment / update_comment / delete_comment.

No real Mongo/S3: the repo layer (ops_repo) is monkeypatched onto a small
in-process dict standing in for the `comments` collection, and the S3 layer
(ops_files) is monkeypatched to record calls instead of touching DigitalOcean
Spaces. ops_logic (pure validation) and the route/parsing layer run unmodified.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")

from datetime import datetime, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Optional  # noqa: E402
import uuid as _uuid  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routes.ops import ops_routes  # noqa: E402
from routes.ops.ops_routes import Caller, get_caller, router  # noqa: E402

PROJECT_ID = "OPS-2026-001"
OTHER_PROJECT_ID = "OPS-2026-002"
ISSUE_ID = "ISS-2026-001"
MISSING_PROJECT_ID = "OPS-2026-999"

CALLER_EMPLOYEE_ID = "680043"


def _fake_caller() -> Caller:
    return Caller(
        person={
            "employee_id": CALLER_EMPLOYEE_ID,
            "name": "Test User",
            "username": "tester",
            "image_url": None,
            "department": "IT",
            "position": "Dev",
        },
        employee_id=CALLER_EMPLOYEE_ID,
        username="tester",
        is_manager=False,
    )


app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_caller] = _fake_caller
client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_store(monkeypatch):
    """Stands in for Mongo (comments collection + project/issue lookups) and S3."""
    comments: dict = {}
    projects = {
        PROJECT_ID: {"_id": PROJECT_ID, "project_id": PROJECT_ID},
        OTHER_PROJECT_ID: {"_id": OTHER_PROJECT_ID, "project_id": OTHER_PROJECT_ID},
    }
    issues = {
        ISSUE_ID: {"_id": ISSUE_ID, "issue_id": ISSUE_ID, "project_id": PROJECT_ID},
    }
    uploaded: dict = {}
    deleted: list = []

    def fake_get_project(pid):
        return projects.get(pid)

    def fake_get_issue(iid):
        return issues.get(iid)

    def fake_create_comment(ref_type, ref_id, body, author, *, comment_id=None, attachments=None):
        cid = comment_id or _uuid.uuid4().hex
        doc = {
            "_id": cid,
            "comment_id": cid,
            "ref_type": ref_type,
            "ref_id": ref_id,
            "author": author,
            "body": body,
            "attachments": attachments or [],
            "created_at": datetime.now(timezone.utc),
            "edited_at": None,
            "liked_by": [],
        }
        comments[cid] = doc
        return doc

    def fake_get_comment(cid):
        return comments.get(cid)

    def fake_update_comment(cid, body):
        doc = comments[cid]
        doc["body"] = body
        doc["edited_at"] = datetime.now(timezone.utc)
        return doc

    def fake_delete_comment(cid):
        comments.pop(cid, None)

    def fake_list_comments(ref_type, ref_id):
        return [d for d in comments.values() if d["ref_type"] == ref_type and d["ref_id"] == ref_id]

    monkeypatch.setattr(ops_routes.ops_repo, "get_project", fake_get_project)
    monkeypatch.setattr(ops_routes.ops_repo, "get_issue", fake_get_issue)
    monkeypatch.setattr(ops_routes.ops_repo, "create_comment", fake_create_comment)
    monkeypatch.setattr(ops_routes.ops_repo, "get_comment", fake_get_comment)
    monkeypatch.setattr(ops_routes.ops_repo, "update_comment", fake_update_comment)
    monkeypatch.setattr(ops_routes.ops_repo, "delete_comment", fake_delete_comment)
    monkeypatch.setattr(ops_routes.ops_repo, "list_comments", fake_list_comments)

    def fake_upload_bytes(key, data, content_type):
        uploaded[key] = (data, content_type)

    def fake_presigned_get_url(key):
        return f"https://fake.example/{key}"

    def fake_delete_objects(keys):
        deleted.extend(keys)

    monkeypatch.setattr(ops_routes.ops_files, "upload_bytes", fake_upload_bytes)
    monkeypatch.setattr(ops_routes.ops_files, "presigned_get_url", fake_presigned_get_url)
    monkeypatch.setattr(ops_routes.ops_files, "delete_objects", fake_delete_objects)

    return SimpleNamespace(comments=comments, projects=projects, issues=issues, uploaded=uploaded, deleted=deleted)


def _png_bytes() -> bytes:
    # Minimal valid-enough payload; content_type drives validation, not real decoding.
    return b"\x89PNG\r\n\x1a\n" + b"0" * 32


# ---------------------------------------------------------------------------
# 1. JSON still works (currently-deployed frontend, unchanged behaviour)
# ---------------------------------------------------------------------------

def test_json_comment_still_works():
    resp = client.post(f"/ops/projects/{PROJECT_ID}/comments", json={"body": "hello there"})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["body"] == "hello there"
    assert data["attachments"] == []
    assert data["ref_type"] == "project"
    assert data["ref_id"] == PROJECT_ID


def test_json_comment_project_not_found():
    resp = client.post(f"/ops/projects/{MISSING_PROJECT_ID}/comments", json={"body": "hi"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 2. multipart with text + images
# ---------------------------------------------------------------------------

def test_multipart_text_and_images(fake_store):
    files = [
        ("files", ("a.png", _png_bytes(), "image/png")),
        ("files", ("b.jpg", _png_bytes(), "image/jpeg")),
    ]
    resp = client.post(
        f"/ops/projects/{PROJECT_ID}/comments",
        data={"body": "look at this"},
        files=files,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["body"] == "look at this"
    assert len(data["attachments"]) == 2
    for att in data["attachments"]:
        assert att["url"].startswith("https://fake.example/")
        assert att["size"] > 0
    # uploaded under ops_project/{project_id}/comments/{comment_id}/...
    comment_id = data["comment_id"]
    assert len(fake_store.uploaded) == 2
    for key in fake_store.uploaded:
        assert key.startswith(f"ops_project/{PROJECT_ID}/comments/{comment_id}/")


def test_multipart_issue_comment_uses_issue_project_id(fake_store):
    files = [("files", ("a.png", _png_bytes(), "image/png"))]
    resp = client.post(f"/ops/issues/{ISSUE_ID}/comments", data={"body": "issue pic"}, files=files)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["ref_type"] == "issue"
    assert data["ref_id"] == ISSUE_ID
    comment_id = data["comment_id"]
    (key,) = fake_store.uploaded.keys()
    # issue comment images live under the issue's *project*, per contract item 3
    assert key.startswith(f"ops_project/{PROJECT_ID}/comments/{comment_id}/")


# ---------------------------------------------------------------------------
# 3. image-only (empty body, at least one image) is allowed
# ---------------------------------------------------------------------------

def test_multipart_image_only():
    files = [("files", ("a.png", _png_bytes(), "image/png"))]
    resp = client.post(f"/ops/projects/{PROJECT_ID}/comments", data={"body": ""}, files=files)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["body"] == ""
    assert len(data["attachments"]) == 1


def test_multipart_image_only_body_field_omitted():
    files = [("files", ("a.png", _png_bytes(), "image/png"))]
    resp = client.post(f"/ops/projects/{PROJECT_ID}/comments", files=files)
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# 4. empty body and no images -> 400
# ---------------------------------------------------------------------------

def test_multipart_empty_body_and_no_images_rejected():
    # httpx's test client only emits multipart/form-data when a `files=` entry is
    # present — plain `data=` becomes application/x-www-form-urlencoded. Build the
    # raw body so this genuinely exercises the multipart-with-no-files branch.
    boundary = "ncacdbtestboundary"
    raw = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="body"\r\n\r\n'
        "   \r\n"
        f"--{boundary}--\r\n"
    ).encode()
    resp = client.post(
        f"/ops/projects/{PROJECT_ID}/comments",
        content=raw,
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == ops_routes.ops_logic.MSG_COMMENT_EMPTY


def test_json_empty_body_rejected():
    resp = client.post(f"/ops/projects/{PROJECT_ID}/comments", json={"body": ""})
    assert resp.status_code == 400
    assert resp.json()["error"] == ops_routes.ops_logic.MSG_COMMENT_EMPTY


# ---------------------------------------------------------------------------
# 5. more than 4 images -> 400
# ---------------------------------------------------------------------------

def test_multipart_too_many_images_rejected(fake_store):
    files = [("files", (f"{i}.png", _png_bytes(), "image/png")) for i in range(5)]
    resp = client.post(f"/ops/projects/{PROJECT_ID}/comments", data={"body": "too many"}, files=files)
    assert resp.status_code == 400
    assert resp.json()["error"] == ops_routes.ops_logic.MSG_COMMENT_TOO_MANY_IMAGES
    # nothing should have been uploaded — validate-before-upload
    assert fake_store.uploaded == {}


# ---------------------------------------------------------------------------
# 6. non-image file -> 400
# ---------------------------------------------------------------------------

def test_multipart_non_image_file_rejected(fake_store):
    files = [("files", ("doc.pdf", b"%PDF-1.4 fake", "application/pdf"))]
    resp = client.post(f"/ops/projects/{PROJECT_ID}/comments", data={"body": "has a pdf"}, files=files)
    assert resp.status_code == 400
    assert resp.json()["error"] == ops_routes.ops_logic.MSG_COMMENT_IMAGE_TYPE_INVALID
    assert fake_store.uploaded == {}


# ---------------------------------------------------------------------------
# 7. response includes attachments with a url (covered above too; explicit check)
# ---------------------------------------------------------------------------

def test_list_comments_response_includes_attachments_with_url():
    files = [("files", ("a.png", _png_bytes(), "image/png"))]
    create_resp = client.post(f"/ops/projects/{PROJECT_ID}/comments", data={"body": "pic"}, files=files)
    assert create_resp.status_code == 201

    list_resp = client.get(f"/ops/projects/{PROJECT_ID}/comments")
    assert list_resp.status_code == 200
    found = [c for c in list_resp.json() if c["comment_id"] == create_resp.json()["comment_id"]]
    assert len(found) == 1
    att = found[0]["attachments"][0]
    assert att["url"].startswith("https://fake.example/ops_project/")
    assert att["file_name"] == "a.png"
    assert att["mime_type"] == "image/png"


# ---------------------------------------------------------------------------
# 8. PATCH empty body allowed only with images
# ---------------------------------------------------------------------------

def test_patch_empty_body_allowed_when_comment_has_image():
    files = [("files", ("a.png", _png_bytes(), "image/png"))]
    created = client.post(f"/ops/projects/{PROJECT_ID}/comments", data={"body": "with pic"}, files=files)
    comment_id = created.json()["comment_id"]

    resp = client.patch(f"/ops/comments/{comment_id}", json={"body": ""})
    assert resp.status_code == 200, resp.text
    assert resp.json()["body"] == ""
    assert len(resp.json()["attachments"]) == 1


def test_patch_empty_body_rejected_when_comment_has_no_image():
    created = client.post(f"/ops/projects/{PROJECT_ID}/comments", json={"body": "text only"})
    comment_id = created.json()["comment_id"]

    resp = client.patch(f"/ops/comments/{comment_id}", json={"body": ""})
    assert resp.status_code == 400
    assert resp.json()["error"] == ops_routes.ops_logic.MSG_COMMENT_EMPTY


# ---------------------------------------------------------------------------
# bonus: delete is best-effort on S3 cleanup (contract item 6)
# ---------------------------------------------------------------------------

def test_delete_comment_cleans_up_s3_objects(fake_store):
    files = [("files", ("a.png", _png_bytes(), "image/png"))]
    created = client.post(f"/ops/projects/{PROJECT_ID}/comments", data={"body": "pic"}, files=files)
    comment_id = created.json()["comment_id"]

    resp = client.delete(f"/ops/comments/{comment_id}")
    assert resp.status_code == 204
    assert len(fake_store.deleted) == 1
    assert fake_store.comments.get(comment_id) is None


def test_delete_comment_s3_failure_does_not_fail_request(fake_store, monkeypatch):
    files = [("files", ("a.png", _png_bytes(), "image/png"))]
    created = client.post(f"/ops/projects/{PROJECT_ID}/comments", data={"body": "pic"}, files=files)
    comment_id = created.json()["comment_id"]

    def boom(keys):
        raise RuntimeError("S3 unreachable")

    monkeypatch.setattr(ops_routes.ops_files, "delete_objects", boom)

    resp = client.delete(f"/ops/comments/{comment_id}")
    assert resp.status_code == 204
    assert fake_store.comments.get(comment_id) is None
