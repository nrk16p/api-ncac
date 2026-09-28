"""Advance (Finance) forms stay silent until the finance email token exists.
Set ADVANCE_NOTIFY_ENABLED=true to turn email/LINE on for them. IT forms are unaffected."""
import os

ADVANCE_FORM_TYPE = "Advance"


def notifications_enabled(form_master) -> bool:
    if form_master is None or getattr(form_master, "form_type", None) != ADVANCE_FORM_TYPE:
        return True
    return os.getenv("ADVANCE_NOTIFY_ENABLED", "false").strip().lower() == "true"
