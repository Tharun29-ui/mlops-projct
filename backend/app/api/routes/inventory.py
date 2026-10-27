from fastapi import APIRouter, Depends, HTTPException
from datetime import date
from typing import Literal
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AppSetting, Product, StockMovement
from app.models import SerialAsset
from app.core.config import get_settings
from app.repositories import dynamodb_store
from app.services.stock_dates import validate_dated_stock

router = APIRouter(prefix="/inventory", tags=["inventory"])


class ProductInput(BaseModel):
    opening_stock_date: date | None = None
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    product_type: str | None = Field(default=None, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=160)
    hsn_sac: str | None = Field(default=None, max_length=32)
    quantity_on_hand: int = Field(ge=0)
    reorder_point: int = Field(default=0, ge=0)
    unit_cost: float = Field(default=0, ge=0)
    minimum_margin_percent: float | None = Field(default=None, ge=0, lt=100)
    target_margin_percent: float | None = Field(default=None, ge=0, lt=100)


class PricingRulesInput(BaseModel):
    minimum_margin_percent: float | None = Field(default=None, ge=0, lt=100)
    target_margin_percent: float | None = Field(default=None, ge=0, lt=100)


class ProductDetailsInput(BaseModel):
    aliases: list[str] | None = Field(default=None, max_length=100)
    product_type: str | None = Field(default=None, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=160)
    hsn_sac: str | None = Field(default=None, max_length=32)


class PricingPolicyInput(PricingRulesInput):
    pass


class MovementInput(BaseModel):
    quantity_change: int
    reason: str = Field(min_length=1, max_length=255)
    effective_date: date = Field(default_factory=date.today)
    movement_type: Literal["ADJUSTMENT", "OPENING_STOCK"] = "ADJUSTMENT"
    confirmed_opening_balance: bool = False


def _product_dict(p: Product) -> dict:
    if isinstance(p, dict):
        return {"product_type": None, "brand": None, "model": None, "hsn_sac": None, "aliases": [],
                "minimum_margin_percent": None, "target_margin_percent": None,
                **p, "low_stock": p["quantity_on_hand"] <= p["reorder_point"]}
    return {"id": p.id, "sku": p.sku, "name": p.name,
            "product_type": p.product_type, "brand": p.brand, "model": p.model,
            "hsn_sac": p.hsn_sac, "aliases": p.aliases or [],
            "quantity_on_hand": p.quantity_on_hand, "reorder_point": p.reorder_point,
            "unit_cost": p.unit_cost, "low_stock": p.quantity_on_hand <= p.reorder_point,
            "minimum_margin_percent": p.minimum_margin_percent,
            "target_margin_percent": p.target_margin_percent,
            "stock_count_verified": p.stock_count_verified}


@router.get("")
def list_products(db: Session = Depends(get_db)) -> list[dict]:
    if get_settings().business_data_table_name:
        return [_product_dict(p) for p in dynamodb_store.list_products()]
    return [_product_dict(p) for p in db.scalars(select(Product).order_by(Product.name)).all()]


def _get_pricing_policy(db: Session) -> dict:
    if get_settings().business_data_table_name:
        return dynamodb_store.get_pricing_policy()
    row = db.get(AppSetting, "pricing_policy")
    return row.value if row else {"minimum_margin_percent": None, "target_margin_percent": None}


@router.get("/pricing-policy")
def get_pricing_policy(db: Session = Depends(get_db)) -> dict:
    return _get_pricing_policy(db)


@router.put("/pricing-policy")
def save_pricing_policy(payload: PricingPolicyInput, db: Session = Depends(get_db)) -> dict:
    minimum = payload.minimum_margin_percent
    target = payload.target_margin_percent
    if minimum is not None and target is not None and target < minimum:
        raise HTTPException(status_code=422, detail="Target margin must be at least the minimum margin.")
    policy = {"minimum_margin_percent": minimum, "target_margin_percent": target}
    if get_settings().business_data_table_name:
        return dynamodb_store.save_pricing_policy(policy)
    row = db.get(AppSetting, "pricing_policy")
    if row:
        row.value = policy
    else:
        db.add(AppSetting(key="pricing_policy", value=policy))
    db.commit()
    return policy


@router.get("/serial-assets")
def list_serial_assets(status: str | None = None, query: str | None = None,
                       db: Session = Depends(get_db)) -> list[dict]:
    if status and status not in {"IN_STOCK", "SOLD"}:
        raise HTTPException(status_code=422, detail="status must be IN_STOCK or SOLD.")
    if get_settings().business_data_table_name:
        assets = dynamodb_store.list_serial_assets()
    else:
        statement = select(SerialAsset).order_by(SerialAsset.product_name, SerialAsset.serial_number)
        if status:
            statement = statement.where(SerialAsset.status == status)
        if query:
            needle = f"%{query.strip()}%"
            statement = statement.where(SerialAsset.serial_number.ilike(needle) |
                                       SerialAsset.product_name.ilike(needle))
        assets = db.scalars(statement).all()
    rows = [asset if isinstance(asset, dict) else {
        "serial_number": asset.serial_number, "product_id": asset.product_id,
        "product_name": asset.product_name, "purchase_invoice_id": asset.purchase_invoice_id,
        "sale_invoice_id": asset.sale_invoice_id, "warranty_months": asset.warranty_months,
        "warranty_start": asset.warranty_start.isoformat() if asset.warranty_start else None,
        "warranty_until": asset.warranty_until.isoformat() if asset.warranty_until else None,
        "status": asset.status,
    } for asset in assets]
    if status and get_settings().business_data_table_name:
        rows = [row for row in rows if row.get("status") == status]
    if query and get_settings().business_data_table_name:
        needle = query.strip().casefold()
        rows = [row for row in rows if needle in str(row.get("serial_number", "")).casefold()
                or needle in str(row.get("product_name", "")).casefold()]
    return rows


@router.post("", status_code=201)
def create_product(payload: ProductInput, db: Session = Depends(get_db)) -> dict:
    if payload.minimum_margin_percent is not None and payload.target_margin_percent is not None and payload.target_margin_percent < payload.minimum_margin_percent:
        raise HTTPException(status_code=422, detail="Target margin must be at least the minimum margin.")
    if get_settings().business_data_table_name:
        product = dynamodb_store.create_product({**payload.model_dump(), "stock_count_verified": False})
        if product is None:
            raise HTTPException(status_code=409, detail="SKU already exists.")
        return _product_dict(product)
    if db.scalar(select(Product).where(Product.sku == payload.sku)):
        raise HTTPException(status_code=409, detail="SKU already exists.")
    product = Product(**payload.model_dump(exclude={'opening_stock_date'}))
    db.add(product)
    db.flush()
    if product.quantity_on_hand:
        db.add(StockMovement(product_id=product.id, quantity_change=product.quantity_on_hand,
                             reason="Opening stock", effective_date=payload.opening_stock_date or date.today(),
                             movement_type="OPENING_STOCK"))
    db.commit()
    db.refresh(product)
    return _product_dict(product)


@router.patch("/{product_id}/pricing")
def update_pricing_rules(product_id: str, payload: PricingRulesInput,
                         db: Session = Depends(get_db)) -> dict:
    if payload.minimum_margin_percent is not None and payload.target_margin_percent is not None and payload.target_margin_percent < payload.minimum_margin_percent:
        raise HTTPException(status_code=422, detail="Target margin must be at least the minimum margin.")
    if get_settings().business_data_table_name:
        try:
            product = dynamodb_store.update_product_pricing(product_id, payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not product:
            raise HTTPException(status_code=404, detail="Product not found.")
        return _product_dict(product)
    try:
        numeric_id = int(product_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Product not found.") from exc
    product = db.get(Product, numeric_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    product.minimum_margin_percent = payload.minimum_margin_percent
    product.target_margin_percent = payload.target_margin_percent
    db.commit()
    db.refresh(product)
    return _product_dict(product)


@router.patch("/{product_id}/details")
def update_product_details(product_id: str, payload: ProductDetailsInput,
                           db: Session = Depends(get_db)) -> dict:
    """Update product classification details; HSN/SAC remains operator-verified."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one product detail to update.")
    if "hsn_sac" in updates and updates["hsn_sac"] is not None:
        updates["hsn_sac"] = updates["hsn_sac"].strip() or None
    if get_settings().business_data_table_name:
        try:
            product = dynamodb_store.update_product_details(product_id, updates)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not product:
            raise HTTPException(status_code=404, detail="Product not found.")
        return _product_dict(product)
    try:
        numeric_id = int(product_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Product not found.") from exc
    product = db.get(Product, numeric_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    for field, value in updates.items():
        setattr(product, field, value.strip() if isinstance(value, str) else value)
    db.commit()
    db.refresh(product)
    return _product_dict(product)


@router.post("/{product_id}/movements", status_code=201)
def add_movement(product_id: str, payload: MovementInput, db: Session = Depends(get_db)) -> dict:
    if payload.quantity_change == 0:
        raise HTTPException(422, "Enter a nonzero stock quantity.")
    if payload.movement_type == "OPENING_STOCK" and (payload.quantity_change <= 0 or not payload.confirmed_opening_balance):
        raise HTTPException(422, "Confirm the actual positive opening-stock quantity before saving it.")
    if get_settings().business_data_table_name:
        product = dynamodb_store.get_product(product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found.")
        if product["quantity_on_hand"] + payload.quantity_change < 0:
            raise HTTPException(status_code=422, detail="Movement would make stock negative.")
        try:
            updated = dynamodb_store.add_stock_movement(product_id, payload.quantity_change, payload.reason,
                effective_date=payload.effective_date.isoformat(), movement_type=payload.movement_type)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if updated is None:
            raise HTTPException(status_code=409, detail="Stock changed concurrently; refresh and retry.")
        return _product_dict(updated)
    product = db.get(Product, int(product_id))
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    new_quantity = product.quantity_on_hand + payload.quantity_change
    if new_quantity < 0:
        raise HTTPException(status_code=422, detail="Movement would make stock negative.")
    history = db.scalars(select(StockMovement).where(StockMovement.product_id == product.id)).all()
    try:
        validate_dated_stock(product.quantity_on_hand,
            [{'quantity_change': m.quantity_change, 'effective_date': m.effective_date} for m in history],
            payload.quantity_change, payload.effective_date, product.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    product.quantity_on_hand = new_quantity
    db.add(StockMovement(product_id=product_id, **payload.model_dump(exclude={'confirmed_opening_balance'})))
    db.commit()
    db.refresh(product)
    return _product_dict(product)


@router.get("/{product_id}/movements")
def list_movements(product_id: str, db: Session = Depends(get_db)) -> list[dict]:
    if get_settings().business_data_table_name:
        if not dynamodb_store.get_product(product_id):
            raise HTTPException(status_code=404, detail="Product not found.")
        return dynamodb_store.list_movements(product_id)
    product_id = int(product_id)
    if not db.get(Product, product_id):
        raise HTTPException(status_code=404, detail="Product not found.")
    rows = db.scalars(select(StockMovement).where(StockMovement.product_id == product_id)
                      .order_by(StockMovement.created_at.desc())).all()
    return [{"id": row.id, "product_id": row.product_id,
             "quantity_change": row.quantity_change, "reason": row.reason,
             "created_at": row.created_at, "effective_date": row.effective_date,
             "movement_type": row.movement_type} for row in rows]
