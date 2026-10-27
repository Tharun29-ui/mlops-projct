from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now():
    return datetime.now(timezone.utc)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    stored_path: Mapped[str] = mapped_column(String(600))
    source_type: Mapped[str] = mapped_column(String(16), default="UPLOAD")
    vendor_name: Mapped[str | None] = mapped_column(String(300))
    vendor_address: Mapped[str | None] = mapped_column(Text)
    receiver_name: Mapped[str | None] = mapped_column(String(300))
    vendor_gstin: Mapped[str | None] = mapped_column(String(32))
    receiver_gstin: Mapped[str | None] = mapped_column(String(32))
    receiver_address: Mapped[str | None] = mapped_column(Text)
    invoice_number: Mapped[str | None] = mapped_column(String(128))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tax_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    grand_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    payment_status: Mapped[str] = mapped_column(String(16), default="UNKNOWN", index=True)
    payment_history: Mapped[list] = mapped_column(JSON, default=list)
    transaction_type: Mapped[str] = mapped_column(String(16), default="UNKNOWN", index=True)
    direction_reason: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED", index=True)
    review_notes: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    field_confidence: Mapped[dict | None] = mapped_column(JSON)
    extraction_id: Mapped[str | None] = mapped_column(String(64))
    extraction_status: Mapped[str] = mapped_column(String(32), default="MANUAL", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    lines: Mapped[list["InvoiceLine"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    __table_args__ = (Index("uq_invoices_extraction_id", "extraction_id", unique=True),)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(String(500))
    is_stock_item: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    hsn_sac: Mapped[str | None] = mapped_column(String(32))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit: Mapped[str | None] = mapped_column(String(32))
    unit_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    line_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    serial_numbers: Mapped[list] = mapped_column(JSON, default=list)
    warranty_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggested_unit_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    price_override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    product_type: Mapped[str | None] = mapped_column(String(120), index=True)
    brand: Mapped[str | None] = mapped_column(String(120), index=True)
    model: Mapped[str | None] = mapped_column(String(160))
    hsn_sac: Mapped[str | None] = mapped_column(String(32), index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    reorder_point: Mapped[int] = mapped_column(Integer, default=0)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    minimum_margin_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    target_margin_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    stock_count_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    movements: Mapped[list["StockMovement"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    quantity_change: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(255))
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    movement_type: Mapped[str] = mapped_column(String(32), default="ADJUSTMENT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    product: Mapped[Product] = relationship(back_populates="movements")


class SerialAsset(Base):
    __tablename__ = "serial_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    serial_number: Mapped[str] = mapped_column(String(160))
    product_id: Mapped[str] = mapped_column(String(80), index=True)
    product_name: Mapped[str] = mapped_column(String(255))
    purchase_invoice_id: Mapped[str] = mapped_column(String(80), index=True)
    sale_invoice_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    warranty_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warranty_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="IN_STOCK", index=True)


class ForecastRun(Base):
    __tablename__ = "forecast_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(100))
    window_days: Mapped[int] = mapped_column(Integer)
    horizon_days: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSON)
    forecasts: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    feedback: Mapped[list["ForecastFeedback"]] = relationship(back_populates="forecast_run", cascade="all, delete-orphan")


class ForecastFeedback(Base):
    __tablename__ = "forecast_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    forecast_run_id: Mapped[int] = mapped_column(ForeignKey("forecast_runs.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(String(80))
    forecast_date: Mapped[date] = mapped_column(Date)
    actual_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    forecast_run: Mapped[ForecastRun] = relationship(back_populates="feedback")
