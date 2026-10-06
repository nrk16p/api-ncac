"""Model v2 training (spec §4.5) — scikit-learn, imported only by pipeline_fuel_train.

Labels:
  queue decisions   real_loss = 1, noise / legit = 0, weight 1 (follow_up and undecided ignored)
  old reviews       (fuel_drop_reviews without event_id, weight 0.5) — reviewed_ok / false_positive
                    windows make every overlapping event 0; a reviewed_suspicious window makes its
                    largest-excess overlapping event 1 (the others in that window stay unlabelled)
Evaluation: the most recent 30 % of labelled days are held out. precision@20 = share of each day's
top-20 ranked events that are real losses, averaged over days; recall = share of held-out real
losses ranked in their day's top 20. The new model replaces the active scorer (rules v1 or the
previous model) only when its precision@20 is higher and its recall is not lower.
"""
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from model import FEATURES, feature_vector, predict
from plates import normalize_plate
from rules import SCORER_V1, score_v1

POSITIVE, NEGATIVE = {"real_loss"}, {"noise", "legit"}
OLD_OK, OLD_SUSPICIOUS = {"reviewed_ok", "false_positive"}, {"reviewed_suspicious"}
WEAK_WEIGHT = 0.5
MIN_PER_CLASS = 30
HOLDOUT_SHARE = 0.3
TOP_K = 20


def _utc(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).replace(tzinfo=None)


def build_dataset(events: list[dict], reviews: list[dict]) -> list[dict]:
    """Rows {"event", "y", "w"}; queue decisions first, each event labelled at most once."""
    rows = [{"event": e, "y": int(e["decision"] in POSITIVE), "w": 1.0} for e in events
            if e.get("status") == "decided" and e.get("decision") in POSITIVE | NEGATIVE]
    per_plate = defaultdict(list)
    for e in events:
        if e.get("kind") != "refuel":
            per_plate[e["plate"]].append(e)
    for review in reviews:
        decision, plate = review.get("decision"), normalize_plate(review.get("plate"))
        if review.get("event_id") or decision not in OLD_OK | OLD_SUSPICIOUS or not plate:
            continue
        if review.get("start_ts") is None or review.get("end_ts") is None:
            continue
        start, end = _utc(review["start_ts"]), _utc(review["end_ts"])
        inside = [e for e in per_plate[plate] if e["start"] <= end and e["end"] >= start]
        if decision in OLD_OK:
            rows += [{"event": e, "y": 0, "w": WEAK_WEIGHT} for e in inside]
        elif inside:
            best = max(inside, key=lambda e: e["features"]["excess_over_burn_l"])
            rows.append({"event": best, "y": 1, "w": WEAK_WEIGHT})
    seen, out = set(), []
    for row in rows:
        if row["event"]["_id"] not in seen:
            seen.add(row["event"]["_id"])
            out.append(row)
    return out


def split_by_day(rows: list[dict], holdout_share: float = HOLDOUT_SHARE) -> tuple[list[dict], list[dict]]:
    days = sorted({r["event"]["date_key"] for r in rows})
    if len(days) < 2:
        return rows, []
    test_days = set(days[-max(1, round(len(days) * holdout_share)):])
    return ([r for r in rows if r["event"]["date_key"] not in test_days],
            [r for r in rows if r["event"]["date_key"] in test_days])


def precision_recall_at_k(rows: list[dict], scores: list[float], k: int = TOP_K) -> tuple[float, float]:
    per_day = defaultdict(list)
    for row, score in zip(rows, scores):
        per_day[row["event"]["date_key"]].append((score, row["y"]))
    precisions, hits, positives = [], 0, 0
    for items in per_day.values():
        top = sorted(items, key=lambda item: item[0], reverse=True)[:k]
        precisions.append(sum(y for _, y in top) / len(top))
        hits += sum(y for _, y in top)
        positives += sum(y for _, y in items)
    precision = float(np.mean(precisions)) if precisions else 0.0
    recall = hits / positives if positives else 0.0
    return round(precision, 4), round(recall, 4)


def rules_scores(rows: list[dict], settings: dict) -> list[float]:
    return [score_v1(r["event"]["class"], r["event"]["features"], settings) / 100 for r in rows]


def model_scores(model: dict, rows: list[dict]) -> list[float]:
    return [predict(model, r["event"]["features"])[0] for r in rows]


def fit(rows: list[dict]) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    X = np.array([feature_vector(r["event"]["features"]) for r in rows])
    y = np.array([r["y"] for r in rows])
    w = np.array([r["w"] for r in rows])
    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000)
    clf.fit(scaler.transform(X), y, sample_weight=w)
    return {"features": FEATURES, "coef": clf.coef_[0].tolist(), "intercept": float(clf.intercept_[0]),
            "scaler_mean": scaler.mean_.tolist(), "scaler_scale": scaler.scale_.tolist()}


def train(rows: list[dict], active_model: dict | None, settings: dict, now: datetime) -> dict:
    positives = sum(r["y"] for r in rows)
    report = {"n_labels": len(rows), "positives": positives, "negatives": len(rows) - positives}
    if positives < MIN_PER_CLASS or report["negatives"] < MIN_PER_CLASS:
        return {**report, "status": "skipped", "reason": f"needs ≥ {MIN_PER_CLASS} labels of each kind"}
    train_rows, test_rows = split_by_day(rows)
    if not test_rows or not any(r["y"] for r in test_rows) or not any(r["y"] for r in train_rows):
        return {**report, "status": "skipped", "reason": "held-out days need real losses on both sides"}
    params = fit(train_rows)
    version = f"lr-{now:%Y-%m-%d}"
    new_p, new_r = precision_recall_at_k(test_rows, model_scores({**params, "version": version}, test_rows))
    if active_model:
        current, compared_with = model_scores(active_model, test_rows), active_model["version"]
    else:
        current, compared_with = rules_scores(test_rows, settings), SCORER_V1
    cur_p, cur_r = precision_recall_at_k(test_rows, current)
    promote = new_p > cur_p and new_r >= cur_r
    metrics = {"precision_at_20": new_p, "recall_at_20": new_r, "active_precision_at_20": cur_p,
               "active_recall_at_20": cur_r, "compared_with": compared_with,
               "train_days": len({r["event"]["date_key"] for r in train_rows}),
               "test_days": len({r["event"]["date_key"] for r in test_rows})}
    model_doc = {"_id": version, "version": version, "created_at": now, "active": promote,
                 "algo": "logistic_regression", **params, "metrics": metrics, "n_labels": len(rows)}
    return {**report, "status": "promoted" if promote else "kept", "metrics": metrics, "model": model_doc}
