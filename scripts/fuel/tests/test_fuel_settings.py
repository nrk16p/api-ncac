from fuel_settings import DEFAULTS, merge_settings


def test_defaults_when_nothing_stored():
    assert merge_settings(None) == DEFAULTS
    assert DEFAULTS["auto_close_conf"] == 0.95 and DEFAULTS["audit_rate"] == 0.05 and DEFAULTS["price_per_litre"] is None


def test_stored_values_override_known_keys_only():
    merged = merge_settings({"_id": "default", "auto_close_conf": 0.9, "price_per_litre": 31.5,
                             "min_drop_l": None, "unknown": 1})
    assert merged["auto_close_conf"] == 0.9 and merged["price_per_litre"] == 31.5
    assert merged["min_drop_l"] == DEFAULTS["min_drop_l"] and "unknown" not in merged and "_id" not in merged
