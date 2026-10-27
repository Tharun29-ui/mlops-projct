from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class InvoiceLineInput(BaseModel):
    is_stock_item: bool = True
    description: str = Field(min_length=1, max_length=500)
    hsn_sac: str | None = Field(default=None, max_length=32)
    quantity: Decimal = Field(gt=0)
    unit: str | None = Field(default=None, max_length=32)
    # Callers should provide the comparable pre-tax unit rate when available.
    unit_rate: Decimal | None = Field(default=None, ge=0)
    line_amount: Decimal = Field(ge=0)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    product_id: str | None = Field(default=None, max_length=80)
    tax_rate: Decimal = Field(default=0, ge=0, le=100)
    tax_amount: Decimal = Field(default=0, ge=0)
    serial_numbers: list[str] = Field(default_factory=list, max_length=100)
    warranty_months: int | None = Field(default=None, ge=0, le=1200)
    suggested_unit_rate: Decimal | None = Field(default=None, ge=0)
    price_override_reason: str | None = Field(default=None, max_length=500)


class InvoiceValidationInput(BaseModel):
    vendor_name: str | None = Field(default=None, max_length=300)
    vendor_gstin: str | None = Field(default=None, max_length=32)
    receiver_gstin: str | None = Field(default=None, max_length=32)
    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None
    currency: str = Field(default="INR", min_length=3, max_length=3)
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_total: Decimal | None = Field(default=None, ge=0)
    grand_total: Decimal | None = Field(default=None, ge=0)
    transaction_type: Literal["UNKNOWN", "PURCHASE", "SALE"] = "UNKNOWN"
    lines: list[InvoiceLineInput] = Field(default_factory=list, max_length=500)


class InvoiceValidationResult(BaseModel):
    valid: bool
    review_required: bool
    warnings: list[str]


class InvoiceCorrectionInput(BaseModel):
    vendor_name: str | None = Field(default=None, max_length=300)
    vendor_gstin: str | None = Field(default=None, max_length=32)
    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_total: Decimal | None = Field(default=None, ge=0)
    grand_total: Decimal | None = Field(default=None, ge=0)
    receiver_name: str | None = Field(default=None, max_length=300)
    receiver_gstin: str | None = Field(default=None, max_length=32)
    receiver_address: str | None = Field(default=None, max_length=2000)
    transaction_type: Literal["UNKNOWN", "PURCHASE", "SALE"] | None = None
    lines: list["InvoiceLineInput"] | None = Field(default=None, max_length=500)
