"""Detection thresholds and queue settings — analytics.fuel_settings, one document `_id: "default"`
(spec §4.1, §4.6). Values stored in Mongo override these defaults key by key; the page edits
auto_close_conf, audit_rate and price_per_litre."""

SETTINGS = "fuel_settings"
SETTINGS_ID = "default"

DEFAULTS = {
    # detection (spec §4.1 — starting values, tuned against labels)
    "min_drop_l": 8.0,          # candidate drop between two parked levels
    "refuel_min_l": 20.0,       # candidate rise
    "gap_min": 10,              # minutes without data that count as a gap
    "merge_min": 30,            # same-kind candidates closer than this merge
    "plateau_min": 5,           # a parked stretch this long gives a trusted level
    "parked_kmh": 5,            # speed at or below this counts as parked
    "hampel_window_min": 7,     # spike filter window
    "hampel_k": 3.0,            # spike filter threshold in MADs
    "spike_min_l": 3.0,         # never call a deviation smaller than this a spike …
    "spike_min_pct": 2.0,       # … nor smaller than this % of the tank
    "recover_tol_l": 3.0,       # "recovered" = back within max(tol_l, tol_pct % of tank) …
    "recover_tol_pct": 2.0,     # … of the level before the event
    "sparse_share": 0.1,        # trucks with fewer valid fuel minutes are not analysed
    # rules v1 (spec §4.4)
    "consumption_max_l": 5.0,   # excess over expected burn at or below this = consumption
    "clear_consumption_l": 2.0,  # … and at or below this = clear (auto-close)
    "noisy_sensor_pct": 3.0,    # parked noise above this lowers the score
    "faulty_sensor_pct": 10.0,  # parked noise above this = sensor_fault
    "noise_rise_factor": 1.5,   # a drop ≤ this × the sensor's unexplained rises that day = noise (starting rule)
    # burn baselines (spec §4.1)
    "baseline_days": 30,
    "baseline_min_days": 7,
    # queue (spec §4.6, §5.1)
    "auto_close_conf": 0.95,
    "audit_rate": 0.05,         # 1 in 20 auto-closed events goes back to the queue
    "price_per_litre": None,    # baht; the report shows litres only until this is set
}


def merge_settings(stored: dict | None) -> dict:
    stored = stored or {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS and v is not None}}


def load_settings(db) -> dict:
    return merge_settings(db[SETTINGS].find_one({"_id": SETTINGS_ID}))
