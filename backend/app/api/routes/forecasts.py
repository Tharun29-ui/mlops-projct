import csv
import io
import json
import math
import hashlib
import random
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_db
from app.models import ForecastFeedback, ForecastRun
from app.repositories import dynamodb_store
from app.models import Invoice, Product
from app.services import demand_model

router = APIRouter(prefix="/forecasts", tags=["forecasts"])
REQUIRED = {"product_id", "date", "quantity_sold"}


class FeedbackInput(BaseModel):
    product_id: str = Field(min_length=1, max_length=80)
    forecast_date: date
    actual_quantity: Decimal = Field(ge=0)
    comment: str | None = Field(default=None, max_length=2000)


class ApprovedSalesForecastInput(BaseModel):
    confirm_complete_sales_history: bool
    horizon_days: int = Field(default=7, ge=1, le=365)


def _linear_trend(values: list[float], steps: int = 1) -> float:
    """Fit a small ordinary-least-squares trend to sequential daily quantities."""
    count = len(values)
    if count == 1:
        return max(0.0, values[0])
    mean_x = (count - 1) / 2
    mean_y = sum(values) / count
    denominator = sum((index - mean_x) ** 2 for index in range(count))
    slope = (sum((index - mean_x) * (value - mean_y)
                 for index, value in enumerate(values)) / denominator) if denominator else 0.0
    return max(0.0, mean_y + slope * (count - 1 + steps - mean_x))


def _read_series(content: bytes) -> dict[str, list[tuple[date, float]]]:
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded.") from exc
    if not reader.fieldnames or not REQUIRED.issubset(set(reader.fieldnames)):
        raise HTTPException(status_code=422, detail="CSV needs product_id,date,quantity_sold columns.")
    rows: dict[str, list[tuple[date, float]]] = {}
    seen: set[tuple[str, date]] = set()
    for line_no, row in enumerate(reader, start=2):
        product = (row.get("product_id") or "").strip()
        try:
            day = date.fromisoformat((row.get("date") or "").strip())
            quantity = float(Decimal((row.get("quantity_sold") or "").strip()))
        except (ValueError, InvalidOperation):
            raise HTTPException(status_code=422, detail=f"Invalid product/date/quantity on CSV row {line_no}.")
        if not product or not math.isfinite(quantity) or quantity < 0:
            raise HTTPException(status_code=422, detail=f"Invalid product or negative/non-finite quantity on CSV row {line_no}.")
        key = (product, day)
        if key in seen:
            raise HTTPException(status_code=422, detail=f"Duplicate product/date on CSV row {line_no}.")
        seen.add(key)
        rows.setdefault(product, []).append((day, quantity))
    if not rows:
        raise HTTPException(status_code=422, detail="CSV has no sales rows.")
    for product, series in rows.items():
        series.sort()
        expected = (series[-1][0] - series[0][0]).days + 1
        if len(series) != expected:
            raise HTTPException(status_code=422, detail=f"{product} has missing days; include zeros only if source coverage confirms no sales.")
        if len(series) < 4:
            raise HTTPException(status_code=422, detail=f"{product} needs at least four consecutive daily rows for a minimal chronological evaluation.")
    return rows


@router.post("/import", status_code=201)
async def create_forecast(file: UploadFile = File(...), db: Session = Depends(get_db),
                          horizon_days: Annotated[int, Query(ge=1, le=365)] = 7) -> dict:
    if (file.content_type or "") not in {"text/csv", "application/vnd.ms-excel", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Upload a CSV file.")
    content = await file.read(get_settings().max_upload_bytes + 1)
    if len(content) > get_settings().max_upload_bytes:
        raise HTTPException(status_code=413, detail="CSV exceeds the local upload size limit.")
    series_by_product = _read_series(content)
    return _train_demand_run(series_by_product, db, content, horizon=horizon_days)


def _train_demand_run(series_by_product, db, content, demo=False, horizon=7, source=None):
    if horizon < 1 or horizon > 365:
        raise HTTPException(422, "Forecast horizon must be between 1 and 365 days.")
    if len(series_by_product)*horizon > 20000:
        raise HTTPException(422, "Choose fewer products or a shorter horizon; a run supports up to 20,000 forecast rows.")
    forecasts, actuals, test_predictions, models, product_metrics = [], [], [], {}, {}
    warnings = []
    for product_id, series in sorted(series_by_product.items()):
        trained = demand_model.train(series, horizon=horizon)
        if horizon > max(7, len(series) // 2):
            warnings.append(f"{product_id}: {horizon}-day extrapolation exceeds half the {len(series)}-day history; use as a planning scenario.")
        if horizon > 90 and len(series) < 730:
            warnings.append(f"{product_id}: annual seasonality is not established by this history.")
        models[product_id] = trained['model']
        product_metrics[product_id] = trained['metrics']
        actuals.extend(trained['test_actuals'])
        test_predictions.extend(trained['test_predictions'])
        for step, quantity in enumerate(trained['predictions'], 1):
            forecasts.append({'product_id':product_id,
                              'forecast_date':(series[-1][0]+timedelta(days=step)).isoformat(),
                              'predicted_quantity':quantity})
    evaluation = demand_model.metrics(actuals,test_predictions)
    evaluation.update({'evaluation':'chronological_validation_then_untouched_test',
                       'evaluation_model_name':'per_product_validation_selected_model',
                       'data_source':source or ('synthetic_presentation_demo' if demo else 'uploaded_sales_csv'),
                       'demo':demo,'products':product_metrics,
                       'training_backend':'aws_lambda','inference_backend':'aws_lambda',
                       'horizon_days':horizon,'forecast_warnings':warnings,
                       'source_sha256':hashlib.sha256(content).hexdigest(),
                       'trained_models':models})
    record = {'model_name':'validated_demand_model','window_days':28,'horizon_days':horizon,
              'metrics':evaluation,'forecasts':forecasts}
    if get_settings().business_data_table_name:
        saved = dynamodb_store.create_forecast(record)
        return {key:saved[key] for key in ('id','model_name','created_at','metrics','forecasts')}
    saved = ForecastRun(**record)
    db.add(saved); db.commit(); db.refresh(saved)
    return {key:getattr(saved,key) for key in ('id','model_name','created_at','metrics','forecasts')}


@router.post("/from-approved-sales", status_code=201)
async def forecast_from_approved_sales(payload: ApprovedSalesForecastInput, db: Session = Depends(get_db)) -> dict:
    if not payload.confirm_complete_sales_history:
        raise HTTPException(status_code=422, detail="Confirm that all sales invoices for the covered dates are entered before forecasting.")
    if get_settings().business_data_table_name:
        invoices = dynamodb_store.list_invoices()
        products = dynamodb_store.list_products()
    else:
        invoices = db.scalars(select(Invoice).where(Invoice.status == "APPROVED")).all()
        products = db.scalars(select(Product)).all()
    from app.services.analysis_sources import approved_analysis_invoices
    invoices, demo_only, _ = approved_analysis_invoices(invoices)
    product_by_name: dict[str, list[str]] = {}
    for product in products:
        product_get = product.get if isinstance(product, dict) else lambda key, default=None: getattr(product, key, default)
        key = "".join(c for c in str(product_get("name")).casefold() if c.isalnum())
        product_by_name.setdefault(key, []).append(str(product_get("id")))
    daily: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for invoice in invoices:
        get = invoice.get if isinstance(invoice, dict) else lambda key, default=None: getattr(invoice, key, default)
        if get("status") != "APPROVED" or get("transaction_type") != "SALE" or not get("invoice_date"):
            continue
        day = get("invoice_date")
        if isinstance(day, str):
            day = date.fromisoformat(day[:10])
        lines = get("lines", []) or []
        for line in lines:
            line_get = line.get if isinstance(line, dict) else lambda key, default=None: getattr(line, key, default)
            if line_get("is_stock_item", True) is False:
                continue
            product_id = str(line_get("product_id") or "")
            if not product_id:
                name = "".join(c for c in str(line_get("description") or "").casefold() if c.isalnum())
                matches = product_by_name.get(name, [])
                if len(matches) == 1:
                    product_id = matches[0]
            if not product_id:
                continue
            daily[product_id][day] += float(line_get("quantity", 0))
    if not daily:
        raise HTTPException(status_code=422, detail="No approved, dated SALE invoices with matched products yet.")

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["product_id", "date", "quantity_sold"])
    # The confirmation covers the whole sales period. All products must share
    # the same cutoff so a 90-day forecast spans 90 calendar days, not staggered
    # windows ending on each product's last nonzero sale.
    coverage_start = min(min(observations) for observations in daily.values())
    coverage_end = max(max(observations) for observations in daily.values())
    eligible = []
    for product_id, observations in sorted(daily.items()):
        first_day, last_day = coverage_start, coverage_end
        if (last_day - first_day).days + 1 < 4:
            continue
        eligible.append(product_id)
        current = first_day
        while current <= last_day:
            writer.writerow([product_id, current.isoformat(), observations.get(current, 0)])
            current += timedelta(days=1)
    if not eligible:
        raise HTTPException(status_code=422, detail="Need at least four consecutive calendar days between approved sales invoices for one product.")
    content = csv_buffer.getvalue().encode("utf-8")

    return _train_demand_run(_read_series(content), db, content, demo=demo_only,
        horizon=payload.horizon_days, source='reviewed_demo_invoices' if demo_only else 'approved_complete_sales_history')


@router.get("/latest")
def latest_forecast(db: Session = Depends(get_db)) -> dict | None:
    if get_settings().business_data_table_name:
        record = dynamodb_store.latest_forecast()
        if not record:
            return None
        return {"id": record["id"], "model_name": record["model_name"],
                "created_at": record["created_at"], "metrics": record["metrics"],
                "forecasts": record["forecasts"]}
    record = db.scalar(select(ForecastRun).order_by(ForecastRun.created_at.desc()).limit(1))
    if not record:
        return None
    return {"id": record.id, "model_name": record.model_name, "created_at": record.created_at,
            "metrics": record.metrics, "forecasts": record.forecasts}


@router.post("/{run_id}/feedback", status_code=201)
def create_feedback(run_id: str, payload: FeedbackInput, db: Session = Depends(get_db)) -> dict:
    if get_settings().business_data_table_name:
        record = dynamodb_store.get_forecast(run_id)
        if not record:
            raise HTTPException(status_code=404, detail="Forecast run not found.")
        exists = any(row["product_id"] == payload.product_id
                     and row["forecast_date"] == payload.forecast_date.isoformat()
                     for row in record["forecasts"])
        if not exists:
            raise HTTPException(status_code=422, detail="Feedback must reference a product/date in this forecast run.")
        feedback = dynamodb_store.create_feedback(run_id, payload.model_dump())
        return feedback
    record = db.get(ForecastRun, run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Forecast run not found.")
    exists = any(row["product_id"] == payload.product_id and row["forecast_date"] == payload.forecast_date.isoformat()
                 for row in record.forecasts)
    if not exists:
        raise HTTPException(status_code=422, detail="Feedback must reference a product/date in this forecast run.")
    feedback = ForecastFeedback(forecast_run_id=run_id, **payload.model_dump())
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return {"id": feedback.id, "forecast_run_id": run_id, "product_id": feedback.product_id,
            "forecast_date": feedback.forecast_date, "actual_quantity": feedback.actual_quantity,
            "comment": feedback.comment, "created_at": feedback.created_at}
