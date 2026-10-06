from fake_mongo import FakeClient
from fuel_settings import DEFAULTS, ensure_settings, merge_settings


def test_defaults_when_nothing_stored():
    assert merge_settings(None) == DEFAULTS
    assert DEFAULTS["auto_close_conf"] == 0.95 and DEFAULTS["audit_rate"] == 0.05 and DEFAULTS["price_per_litre"] is None


def test_stored_values_override_known_keys_only():
    merged = merge_settings({"_id": "default", "auto_close_conf": 0.9, "price_per_litre": 31.5,
                             "min_drop_l": None, "unknown": 1})
    assert merged["auto_close_conf"] == 0.9 and merged["price_per_litre"] == 31.5
    assert merged["min_drop_l"] == DEFAULTS["min_drop_l"] and "unknown" not in merged and "_id" not in merged


def test_ensure_settings_creates_the_doc_and_never_overwrites():
    db = FakeClient()["analytics"]
    assert ensure_settings(db) == DEFAULTS
    stored = db["fuel_settings"].find_one({"_id": "default"})
    assert set(DEFAULTS) <= set(stored) and stored["price_per_litre"] is None   # the page reads it without fallbacks
    db["fuel_settings"].replace_one({"_id": "default"}, {"_id": "default", "audit_rate": 0.1})
    assert ensure_settings(db)["audit_rate"] == 0.1
    stored = db["fuel_settings"].find_one({"_id": "default"})
    assert stored["audit_rate"] == 0.1 and stored["min_drop_l"] == DEFAULTS["min_drop_l"]
