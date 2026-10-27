"""Evaluate and fit an auditable recent-daily-mean forecast baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {"product_id", "date", "quantity_sold"}


def load_sales(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
    frame = frame[["product_id", "date", "quantity_sold"]].copy()
    frame["product_id"] = frame["product_id"].astype("string").str.strip()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["quantity_sold"] = pd.to_numeric(frame["quantity_sold"], errors="coerce")
    if frame.empty or frame.isna().any().any() or (frame["product_id"] == "").any():
        raise ValueError("Input must contain nonblank product IDs, dates, and quantities")
    if not frame["quantity_sold"].map(lambda x: pd.notna(x) and float(x) >= 0 and float(x) < float("inf")).all():
        raise ValueError("quantity_sold must contain finite nonnegative values")
    if frame.duplicated(["product_id", "date"]).any():
        raise ValueError("Expected one aggregated row per product_id and date")
    for product_id, group in frame.groupby("product_id"):
        observed = set(group["date"])
        expected = set(pd.date_range(min(observed), max(observed), freq="D").date)
        if observed != expected:
            raise ValueError(
                f"Product {product_id} has missing calendar days; include zero-sale days only when source coverage is confirmed"
            )
    return frame.sort_values(["product_id", "date"]).reset_index(drop=True)


def _holdout_size(row_count: int, horizon_days: int) -> int:
    return max(1, min(horizon_days, row_count // 5))


def evaluate(frame: pd.DataFrame, window_days: int, horizon_days: int) -> dict:
    errors: list[float] = []
    actuals: list[float] = []
    product_metrics: dict[str, dict] = {}
    for product_id, group in frame.groupby("product_id", sort=True):
        if len(group) < 4:
            raise ValueError(f"Product {product_id} needs at least four consecutive days for evaluation")
        holdout_size = _holdout_size(len(group), horizon_days)
        training = group.iloc[:-holdout_size]
        holdout = group.iloc[-holdout_size:]
        if training.empty:
            raise ValueError(f"Product {product_id} needs training days before the evaluation holdout")
        baseline = float(training.tail(window_days)["quantity_sold"].mean())
        product_actuals = [float(value) for value in holdout["quantity_sold"]]
        product_errors = [abs(baseline - value) for value in product_actuals]
        errors.extend(product_errors)
        actuals.extend(product_actuals)
        product_metrics[str(product_id)] = {
            "training_rows": int(len(training)),
            "holdout_rows": int(len(holdout)),
            "mae": round(sum(product_errors) / len(product_errors), 4),
            "wape": round(sum(product_errors) / sum(product_actuals), 4) if sum(product_actuals) else None,
            "holdout_start": holdout.iloc[0]["date"].isoformat(),
            "holdout_end": holdout.iloc[-1]["date"].isoformat(),
        }
    return {
        "evaluation": "chronological_last_up_to_horizon_days_per_product",
        "holdout_observations": len(errors),
        "mae": round(sum(errors) / len(errors), 4),
        "rmse": round((sum(error * error for error in errors) / len(errors)) ** 0.5, 4),
        "wape": round(sum(errors) / sum(actuals), 4) if sum(actuals) else None,
        "products": product_metrics,
    }


def fit(frame: pd.DataFrame, window_days: int, horizon_days: int, evaluation: dict, source_sha256: str) -> dict:
    products = {}
    for product_id, group in frame.groupby("product_id", sort=True):
        recent = group.tail(window_days)
        products[str(product_id)] = {
            "daily_mean": round(float(recent["quantity_sold"].mean()), 4),
            "history_rows": int(len(group)),
            "last_observed_date": max(group["date"]).isoformat(),
        }
    return {
        "model_name": "recent_daily_mean_baseline",
        "model_version": "0.2.0",
        "window_days": window_days,
        "horizon_days": horizon_days,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_min_date": min(frame["date"]).isoformat(),
        "training_max_date": max(frame["date"]).isoformat(),
        "training_rows": int(len(frame)),
        "source_sha256": source_sha256,
        "evaluation": evaluation,
        "products": products,
    }


def make_forecasts(model: dict) -> list[dict]:
    results = []
    for product_id, product in model["products"].items():
        last_day = date.fromisoformat(product["last_observed_date"])
        for offset in range(1, model["horizon_days"] + 1):
            results.append({
                "product_id": product_id,
                "forecast_date": (last_day + timedelta(days=offset)).isoformat(),
                "predicted_quantity": product["daily_mean"],
            })
    return results


def train(input_path: Path, output_path: Path, window_days: int, horizon_days: int) -> dict:
    if window_days < 1 or horizon_days < 1:
        raise ValueError("window_days and horizon_days must be positive")
    frame = load_sales(input_path)
    evaluation = evaluate(frame, window_days, horizon_days)
    source_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
    model = fit(frame, window_days, horizon_days, evaluation, source_sha256)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("../data/processed/sales_daily.csv"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/baseline_model.json"))
    parser.add_argument("--forecasts-output", type=Path, default=Path("artifacts/forecasts.csv"))
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--horizon-days", type=int, default=7)
    args = parser.parse_args()
    try:
        model = train(args.input, args.output, args.window_days, args.horizon_days)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.forecasts_output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(make_forecasts(model)).to_csv(args.forecasts_output, index=False)
    print(json.dumps({"model": str(args.output), "forecasts": str(args.forecasts_output), "metrics": model["evaluation"]}, indent=2))


if __name__ == "__main__":
    main()
