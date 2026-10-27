"""SageMaker entry point for the evaluated moving-mean baseline."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from train_baseline import make_forecasts, train


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--horizon-days", type=int, default=7)
    args, _ = parser.parse_known_args()
    input_file = Path(os.environ.get("SM_CHANNEL_TRAIN", "/opt/ml/input/data/train")) / "sales_daily.csv"
    model_dir = Path(os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))
    model = train(input_file, model_dir / "model.json", args.window_days, args.horizon_days)
    pd.DataFrame(make_forecasts(model)).to_csv(model_dir / "training_forecasts.csv", index=False)
    evaluation_dir = Path(os.environ.get("SM_OUTPUT_DATA_DIR", "/opt/ml/output")) / "evaluation"
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    (evaluation_dir / "evaluation.json").write_text(
        json.dumps(model["evaluation"], indent=2) + "\n", encoding="utf-8"
    )
    print(f"evaluation_metrics: {json.dumps(model['evaluation'], sort_keys=True)}")
    print(f"evaluation_mae: {model['evaluation']['mae']}")
    print(f"evaluation_rmse: {model['evaluation']['rmse']}")
    if model["evaluation"]["wape"] is not None:
        print(f"evaluation_wape: {model['evaluation']['wape']}")


def model_fn(model_dir: str):
    with open(Path(model_dir) / "model.json", encoding="utf-8") as model_file:
        return json.load(model_file)


def input_fn(request_body: str, content_type: str):
    if content_type != "application/json":
        raise ValueError("Only application/json is supported")
    payload = json.loads(request_body)
    if not isinstance(payload, dict) or not isinstance(payload.get("series"), list):
        raise ValueError('Expected JSON object with a "series" list')
    return payload["series"]


def predict_fn(series: list[dict], model: dict):
    unknown = sorted({row.get("product_id", "") for row in series} - set(model["products"]))
    if unknown:
        raise ValueError(f"No trained history for product(s): {', '.join(unknown)}")
    predictions = []
    for row in series:
        product_id = row["product_id"]
        quantities = row.get("recent_quantities")
        if not isinstance(quantities, list) or not quantities or len(quantities) > model["window_days"]:
            raise ValueError(f"{product_id}: provide 1 to {model['window_days']} recent quantities")
        values = [float(value) for value in quantities]
        if any(not (value >= 0 and value < float("inf")) for value in values):
            raise ValueError(f"{product_id}: quantities must be finite and nonnegative")
        last_day = date.fromisoformat(row["last_observed_date"])
        daily_mean = sum(values) / len(values)
        for offset in range(1, model["horizon_days"] + 1):
            predictions.append({
                "product_id": product_id,
                "forecast_date": (last_day + timedelta(days=offset)).isoformat(),
                "predicted_quantity": round(daily_mean, 4),
            })
    return predictions


def output_fn(prediction, accept: str):
    if accept != "application/json":
        raise ValueError("Only application/json responses are supported")
    return json.dumps({"forecasts": prediction}), accept


if __name__ == "__main__":
    main()
