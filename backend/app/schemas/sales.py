from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class SaleLineInput(BaseModel):
    product_id: str = Field(min_length=1, max_length=80)
    quantity: Decimal = Field(gt=0, max_digits=10, decimal_places=3)
    unit_rate: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    tax_rate: Decimal = Field(default=Decimal('0'), ge=0, le=100, max_digits=5, decimal_places=2)
    serial_numbers: list[str] = Field(default_factory=list, max_length=100)
    price_override_reason: str | None = Field(default=None, max_length=500)


class SalesInvoiceInput(BaseModel):
    demo: bool = False
    document_type: Literal["NON_GST_RECEIPT"] = "NON_GST_RECEIPT"
    invoice_number: str = Field(min_length=1, max_length=128)
    invoice_date: date
    receiver_name: str = Field(min_length=1, max_length=300)
    receiver_gstin: str | None = Field(default=None, max_length=32)
    receiver_address: str | None = Field(default=None, max_length=2000)
    amount_paid: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    lines: list[SaleLineInput] = Field(min_length=1, max_length=100)


class PaymentInput(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    method: str | None = Field(default=None, max_length=40)
    reference: str | None = Field(default=None, max_length=160)
