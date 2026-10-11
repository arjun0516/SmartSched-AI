
import json
import os
import threading
from pathlib import Path

import numpy as np

DATA_FILE = Path(os.getenv("SMARTSCHED_ML_DATA", "ml_training_data.jsonl"))
MODEL_FILE = Path(os.getenv("SMARTSCHED_MODEL_PATH", "burst_model.joblib"))

FEATURES = [
    "ram_mb",
    "priority",
    "io_ratio_pct",
    "arrival_ms"
]
_lock = threading.Lock()
_model = None


def _load_rows():
    rows = []
    if not DATA_FILE.exists():
        return rows

    for line in DATA_FILE.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if all(key in row for key in FEATURES) and float(
                row.get("actual_burst_ms", 0)
            ) > 0:
                rows.append(row)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return rows


def predict(features):
    global _model

    with _lock:
        if _model is None and MODEL_FILE.exists():
            try:
                import joblib
                _model = joblib.load(MODEL_FILE)
            except Exception:
                _model = None
        model = _model

    if model is not None:
        try:
            values = [[float(features[k]) for k in FEATURES]]
            return max(1.0, float(model.predict(values)[0])), "random_forest"

        except Exception:
            pass

    ram = float(features["ram_mb"])
    priority = float(features["priority"])
    io = float(features["io_ratio_pct"])
    arrival = float(features["arrival_ms"])

    estimate = (
        8 + ram * 0.025
        + (6 - priority) * 3
        + (100 - io) * 0.12
        + min(arrival, 1000) * 0.002
    )

    return max(1.0, estimate), "heuristic_fallback"


def record(features, actual_burst_ms):
    row = {key: float(features[key]) for key in FEATURES}
    row["actual_burst_ms"] = float(actual_burst_ms)

    with _lock:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with DATA_FILE.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row) + "\n")

    return len(_load_rows())


def train():
    global _model
    rows = _load_rows()

    if len(rows) < 10:
        return {
            "trained": False,
            "samples": len(rows),
            "message": "At least 10 successful observations are required."
        }

    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.metrics import mean_absolute_error, mean_squared_error
        import joblib

        X = np.array([
            [float(row[key]) for key in FEATURES] for row in rows
        ])
        y = np.array([
            float(row["actual_burst_ms"]) for row in rows
        ])

        model = RandomForestRegressor(
            n_estimators=100,
            random_state=42
        )
        model.fit(X, y)

        predictions = model.predict(X)

        MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, MODEL_FILE)

        with _lock:
            _model = model

        return {
            "trained": True,
            "samples": len(rows),
            "mae_ms": float(mean_absolute_error(y, predictions)),
            "rmse_ms": float(np.sqrt(mean_squared_error(y, predictions))),
            "message": "Trained. Metrics shown are training-set metrics."
        }

    except ImportError:
        return {
            "trained": False,
            "samples": len(rows),
            "message": "Install scikit-learn and joblib first."
        }


def metrics():
    rows = _load_rows()

    if len(rows) < 2:
        return {"samples": len(rows), "mae_ms": None, "rmse_ms": None}

    errors = []
    for row in rows:
        prediction, _ = predict(row)
        errors.append(prediction - float(row["actual_burst_ms"]))

    return {
        "samples": len(rows),
        "mae_ms": float(np.mean(np.abs(errors))),
        "rmse_ms": float(np.sqrt(np.mean(np.square(errors))))
    }
