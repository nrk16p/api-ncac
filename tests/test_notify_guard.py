from types import SimpleNamespace

from services.notify_guard import notifications_enabled


def test_it_forms_always_notify(monkeypatch):
    monkeypatch.delenv("ADVANCE_NOTIFY_ENABLED", raising=False)
    assert notifications_enabled(SimpleNamespace(form_type="Service"))
    assert notifications_enabled(SimpleNamespace(form_type="Issue"))


def test_missing_form_keeps_old_behaviour():
    assert notifications_enabled(None)


def test_advance_silent_by_default(monkeypatch):
    monkeypatch.delenv("ADVANCE_NOTIFY_ENABLED", raising=False)
    assert not notifications_enabled(SimpleNamespace(form_type="Advance"))


def test_advance_enabled_by_env(monkeypatch):
    monkeypatch.setenv("ADVANCE_NOTIFY_ENABLED", "true")
    assert notifications_enabled(SimpleNamespace(form_type="Advance"))


def test_submit_response_shape():
    import os
    os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    from routes.forms.form_submission_routes import _submit_response
    sub = SimpleNamespace(id=7, form_id="ADV-2026-0001", status="Open", status_approve="In Progress",
                          current_approval_level=1)
    form = SimpleNamespace(id=3, version=1)
    assert _submit_response(sub, form) == {
        "message": "Form submitted", "submission_id": 7, "form_id": "ADV-2026-0001", "form_master_id": 3,
        "form_version": 1, "status": "Open", "status_approve": "In Progress", "current_approval_level": 1,
    }
