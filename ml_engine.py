"""
PlantPulse ML Engine — XGBoost models trained on the farm's own history.

Models:
  1. irrigation  (XGBClassifier)  — will soil cross the dry threshold in the next 24h?
  2. water       (XGBRegressor)   — how many % of moisture will be lost in 24h? -> ml of water
  3. disease     (XGBClassifier)  — will a fungal-risk humidity streak occur within 48h?
  4. anomaly     (IsolationForest)— is any recent sensor reading abnormal?

Why gradient-boosted trees and not LSTM: with ~365 daily samples, an LSTM would
memorize, not learn — boosted trees are the honest, state-of-the-art choice for
small tabular datasets. Revisit sequence models after multiple real seasons.

Models are persisted to ./models and retrained on demand via /api/ml/train.
"""
import os, math, sqlite3, json
from datetime import datetime

import numpy as np
import joblib
from xgboost import XGBClassifier, XGBRegressor
from sklearn.ensemble import IsolationForest
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, mean_absolute_error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "plantpulse.db")
MODEL_DIR = os.path.join(BASE_DIR, "models")

FEATURES = ["avg_m", "min_m", "max_m", "drop", "avg_t", "max_t", "avg_h", "max_h",
            "prev_drop", "prev_avg_m", "month_sin", "month_cos"]

ML_PER_PCT = 20        # ml of water to restore 1% moisture in a typical pot
_state = {"models": None, "metrics": None, "trained_ts": None}


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _daily_rows():
    conn = _connect()
    rows = conn.execute("SELECT ts, moisture_pct, temp, humidity FROM readings ORDER BY ts").fetchall()
    conn.close()
    by_day = {}
    for r in rows:
        by_day.setdefault(r["ts"][:10], []).append(r)
    days = []
    for d in sorted(by_day):
        rs = by_day[d]
        ms = [x["moisture_pct"] for x in rs if x["moisture_pct"] is not None]
        ts = [x["temp"] for x in rs if x["temp"] is not None]
        hs = [x["humidity"] for x in rs if x["humidity"] is not None]
        if not ms or not ts:
            continue
        month = int(d[5:7])
        days.append({
            "date": d,
            "avg_m": float(np.mean(ms)), "min_m": float(min(ms)), "max_m": float(max(ms)),
            "drop": float(ms[0] - ms[-1]),
            "avg_t": float(np.mean(ts)), "max_t": float(max(ts)),
            "avg_h": float(np.mean(hs)) if hs else 60.0,
            "max_h": float(max(hs)) if hs else 60.0,
            "month_sin": math.sin(2 * math.pi * month / 12),
            "month_cos": math.cos(2 * math.pi * month / 12),
            "risk_hours": len([h for h in hs if h > 75]),
        })
    return days


def _build_samples(days, dry_threshold):
    samples = []
    for i in range(1, len(days) - 2):
        f = dict(days[i])
        f["prev_drop"] = days[i - 1]["drop"]
        f["prev_avg_m"] = days[i - 1]["avg_m"]
        nxt1, nxt2 = days[i + 1], days[i + 2]
        f["label_irrigation"] = 1 if nxt1["min_m"] < dry_threshold else 0
        f["label_drop24"] = max(0.0, f["avg_m"] - nxt1["min_m"])
        f["label_disease"] = 1 if (nxt1["risk_hours"] + nxt2["risk_hours"]) >= 6 else 0
        samples.append(f)
    return samples


def _hourly_matrix():
    conn = _connect()
    rows = conn.execute("SELECT ts, moisture_pct, temp, humidity FROM readings ORDER BY ts").fetchall()
    conn.close()
    feats, meta = [], []
    prev_m = None
    for r in rows:
        m, t, h = r["moisture_pct"], r["temp"], r["humidity"]
        if m is None or t is None:
            prev_m = m
            continue
        dm = 0.0 if prev_m is None else m - prev_m
        feats.append([m, dm, t, h if h is not None else 60.0])
        meta.append(r["ts"])
        prev_m = m
    return np.array(feats), meta


def train_all(dry_threshold):
    days = _daily_rows()
    samples = _build_samples(days, dry_threshold)
    if len(samples) < 60:
        return {"ready": False, "message": f"Only {len(samples)} usable days of data — need at least 60 to train"}

    X = np.array([[s[k] for k in FEATURES] for s in samples])
    y_irr = np.array([s["label_irrigation"] for s in samples])
    y_dis = np.array([s["label_disease"] for s in samples])
    y_drop = np.array([s["label_drop24"] for s in samples])

    models, metrics = {}, {}

    def fit_classifier(name, y):
        strat = y if 5 <= y.sum() <= len(y) - 5 else None
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=strat)
        m = XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.08,
                          eval_metric="logloss", verbosity=0)
        m.fit(Xtr, ytr)
        metrics[f"{name}_accuracy_pct"] = round(float(accuracy_score(yte, m.predict(Xte))) * 100, 1)
        metrics[f"{name}_positive_days"] = int(y.sum())
        models[name] = m

    fit_classifier("irrigation", y_irr)
    fit_classifier("disease", y_dis)

    Xtr, Xte, ytr, yte = train_test_split(X, y_drop, test_size=0.2, random_state=42)
    reg = XGBRegressor(n_estimators=250, max_depth=4, learning_rate=0.08, verbosity=0)
    reg.fit(Xtr, ytr)
    metrics["water_mae_pct"] = round(float(mean_absolute_error(yte, reg.predict(Xte))), 2)
    models["water"] = reg

    H, _ = _hourly_matrix()
    iso = IsolationForest(n_estimators=150, contamination=0.01, random_state=42)
    iso.fit(H)
    models["anomaly"] = iso
    metrics["anomaly_training_hours"] = int(len(H))

    os.makedirs(MODEL_DIR, exist_ok=True)
    for name, m in models.items():
        joblib.dump(m, os.path.join(MODEL_DIR, f"{name}.joblib"))

    ts = datetime.now().isoformat(timespec="seconds")
    meta = {"metrics": metrics, "trained_ts": ts, "samples": len(samples), "dry_threshold": dry_threshold}
    with open(os.path.join(MODEL_DIR, "meta.json"), "w") as f:
        json.dump(meta, f)
    _state.update(models=models, metrics=metrics, trained_ts=ts)
    return {"ready": True, **meta}


def ensure_models(dry_threshold):
    if _state["models"]:
        return True
    meta_path = os.path.join(MODEL_DIR, "meta.json")
    if os.path.exists(meta_path):
        try:
            models = {name: joblib.load(os.path.join(MODEL_DIR, f"{name}.joblib"))
                      for name in ("irrigation", "disease", "water", "anomaly")}
            with open(meta_path) as f:
                meta = json.load(f)
            _state.update(models=models, metrics=meta["metrics"], trained_ts=meta["trained_ts"])
            return True
        except Exception:
            pass
    return train_all(dry_threshold).get("ready", False)


def predict_today(crop_info):
    """All ML predictions for right now. Returns None if models can't be ready."""
    if not ensure_models(crop_info["dry"]):
        return None
    days = _daily_rows()
    if len(days) < 3:
        return None
    today, prev = days[-1], days[-2]
    x = dict(today)
    x["prev_drop"] = prev["drop"]
    x["prev_avg_m"] = prev["avg_m"]
    vec = np.array([[x[k] for k in FEATURES]])

    models = _state["models"]
    p_irr = float(models["irrigation"].predict_proba(vec)[0][1])
    p_dis = float(models["disease"].predict_proba(vec)[0][1])
    drop24 = max(0.0, float(models["water"].predict(vec)[0]))
    water_ml = int(min(1000, drop24 * ML_PER_PCT)) if p_irr >= 0.5 else 0

    # sensor anomaly check over the last 24h of readings
    H, meta = _hourly_matrix()
    recent = H[-24:] if len(H) >= 24 else H
    flags = models["anomaly"].predict(recent)
    anomaly_count = int((flags == -1).sum())
    anomaly_times = [meta[len(meta) - len(recent) + i] for i, fl in enumerate(flags) if fl == -1][:3]

    # yield outlook: transparent index from the last 90 days (heuristic until
    # real harvest data exists — do not present as a trained model)
    last90 = days[-90:]
    # stress = genuinely dry soil, or heat well past the crop limit (3°C margin so
    # borderline-warm days don't zero the score)
    stress = len([d for d in last90 if d["min_m"] < crop_info["dry"] or d["max_t"] >= crop_info["heat"] + 3])
    risk = len([d for d in last90 if d["risk_hours"] >= 6])
    # proportional: stress can cost up to 70 points, fungal risk up to 30
    n = max(1, len(last90))
    yield_index = max(0, min(100, round(100 - (stress / n) * 70 - (risk / n) * 30)))
    grade = "Excellent" if yield_index >= 80 else "Good" if yield_index >= 60 else "Fair" if yield_index >= 40 else "At risk"

    return {
        "irrigation_probability_pct": round(p_irr * 100),
        "disease_probability_pct": round(p_dis * 100),
        "predicted_moisture_drop_24h_pct": round(drop24, 1),
        "suggested_water_ml": water_ml,
        "sensor_anomalies_24h": anomaly_count,
        "sensor_anomaly_times": anomaly_times,
        "yield_index": yield_index,
        "yield_grade": grade,
        "yield_note": f"{stress} stress days and {risk} fungal-risk days in the last 90 days (heuristic index until real harvest data)",
        "metrics": _state["metrics"],
        "trained_ts": _state["trained_ts"],
    }
