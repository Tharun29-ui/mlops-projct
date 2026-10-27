"""Read normalized Textract results and merge them into an invoice under review."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import boto3
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Invoice, InvoiceLine
from app.repositories import dynamodb_store


def _as_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    cleaned = value.replace(",", "").replace("₹", "").replace("INR", "").strip()
    # Textract often returns quantities with their printed unit ("1 NOS", "2 pcs")
    # and prices with surrounding OCR text. Keep the numeric token instead of
    # discarding the entire invoice line.
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", cleaned)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    for pattern in (
        "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%m/%d/%Y",
        "%d-%b-%y", "%d-%b-%Y", "%d %b %y", "%d %b %Y",
        "%d-%B-%y", "%d-%B-%Y", "%d %B %y", "%d %B %Y",
        "%d/%b/%y", "%d/%b/%Y",
    ):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    return None


def _field_map(fields: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in fields:
        name = field.get("type")
        value = field.get("value")
        if name and value and name not in result:
            result[name] = str(value).strip()
    return result


def _first(fields: dict[str, str], *names: str) -> str | None:
    return next((fields[name] for name in names if fields.get(name)), None)


def _tax_summary(fields: list[dict]) -> tuple[Decimal | None, float | None]:
    """Use an explicit tax total or distinct GST components, never repeated table totals."""
    totals, generic, components = [], [], {}
    for field in fields:
        kind = str(field.get('type') or '').upper()
        label = str(field.get('label') or '').upper()
        component = re.search(r'\b(CGST|SGST|UTGST|IGST|CESS)\b', kind+' '+label)
        if not (kind in {'TAX', 'TOTAL_TAX', 'TAX_TOTAL', 'CGST', 'SGST', 'UTGST', 'IGST', 'CESS'}
                or (kind.endswith('_TAX') and 'ID' not in kind) or component):
            continue
        # A field may read "9% 41.95"; the percentage is not its money amount.
        tokens = [match.group() for match in re.finditer(r'[-+]?\d[\d,]*(?:\.\d+)?', str(field.get('value') or ''))
                  if not re.match(r'\s*%', str(field.get('value') or '')[match.end():])]
        amount = _as_decimal(tokens[-1]) if tokens else None
        if amount is None:
            continue
        try:
            confidence = max(0.0, min(float(field.get('confidence') or 0)/100, 1.0))
        except (TypeError, ValueError):
            confidence = 0.0
        candidate = (amount, confidence)
        if kind in {'TOTAL_TAX', 'TAX_TOTAL'} or re.search(r'\bTOTAL\b.*\bTAX\b|\bTAX\b.*\bTOTAL\b', label):
            totals.append(candidate)
        elif component:
            name = component.group(1)
            if name not in components or confidence > components[name][1]:
                components[name] = candidate
        else:
            generic.append(candidate)
    if totals:
        return max(totals, key=lambda candidate: candidate[1])
    if components:
        return sum((v[0] for v in components.values()), Decimal('0')), min(v[1] for v in components.values())
    if generic:
        return max(generic, key=lambda candidate: candidate[1])
    return None, None


def _confidence(fields: list[dict], *names: str) -> float | None:
    for field in fields:
        if field.get("type") in names and field.get("value") is not None:
            try:
                return max(0.0, min(float(field["confidence"]) / 100, 1.0))
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _field_by_label(fields: list[dict], types: tuple[str, ...],
                    label_terms: tuple[str, ...]) -> str | None:
    for field in fields:
        if field.get("type") in types and field.get("value"):
            return str(field["value"]).strip()
    for field in fields:
        label = re.sub(r"[^a-z0-9]+", "", str(field.get("label") or "").casefold())
        if field.get("value") and any(term in label for term in label_terms):
            return str(field["value"]).strip()
    return None


def _identity_key(value: str | None) -> str:
    """Normalize party names and ignore the common M/s prefix."""
    normalized = "".join(character for character in (value or "").casefold()
                         if character.isalnum())
    for prefix in ("messrs", "ms"):
        if normalized.startswith(prefix) and len(normalized) > len(prefix) + 3:
            normalized = normalized[len(prefix):]
            break
    return normalized


def _name_matches(identity: str, party: str | None) -> bool:
    expected = _identity_key(identity)
    actual = _identity_key(party)
    if len(expected) < 6 or len(actual) < 6:
        return False
    return expected == actual or expected in actual or actual in expected


def _infer_direction(summary: dict[str, str], sale_vendor: str,
                     sale_invoice_number: str) -> tuple[str, str]:
    """Retain the known invoice exception; otherwise require explicit direction."""
    vendor = _first(summary, "VENDOR_NAME", "SUPPLIER_NAME")
    invoice_number = _first(summary, "INVOICE_RECEIPT_ID", "INVOICE_NUMBER")
    if not vendor:
        return "UNKNOWN", "Vendor was not extracted; choose the invoice direction manually."

    if _identity_key(vendor) == _identity_key(sale_vendor):
        if not invoice_number:
            return "UNKNOWN", f"Vendor matches {sale_vendor}, but invoice number was not extracted; verify its direction."
        if _identity_key(invoice_number) == _identity_key(sale_invoice_number):
            return "SALE", f"Suggested Sale: seller and invoice number match {sale_vendor} {sale_invoice_number}. Verify before approval."

    return "UNKNOWN", "Choose Purchase or Sale during upload or review; the supplier name alone does not establish direction."


def _line_from_fields(fields: list[dict]) -> dict | None:
    mapped = _field_map(fields)
    description = _field_by_label(
        fields, ("ITEM", "ITEM_DESCRIPTION", "DESCRIPTION"),
        ("description", "particular", "item", "product", "goods", "service"),
    ) or _first(mapped, "ITEM", "ITEM_DESCRIPTION", "DESCRIPTION")
    if not description:
        return None
    quantity_text = _field_by_label(fields, ("QUANTITY",), ("quantity", "qty"))
    quantity = _as_decimal(quantity_text)
    amount = _as_decimal(_field_by_label(
        fields, ("PRICE", "AMOUNT", "LINE_TOTAL"), ("amount", "linevalue", "linetotal"),
    ) or _first(mapped, "PRICE", "AMOUNT", "LINE_TOTAL"))
    unit_rate = _as_decimal(_field_by_label(
        fields, ("UNIT_PRICE", "UNIT_COST"), ("unitprice", "unitrate", "rate"),
    ) or _first(mapped, "UNIT_PRICE", "UNIT_COST"))
    if amount is None and unit_rate is not None:
        amount = quantity * unit_rate if quantity is not None else None
    if amount is not None and amount > 0 and quantity is not None and quantity > 0:
        # Some invoices print both tax-inclusive and tax-exclusive rates. The
        # effective rate must reconcile with the extracted line amount.
        reconciled_rate = amount / quantity
        if unit_rate is None or abs(unit_rate * quantity - amount) > Decimal('0.05'):
            unit_rate = reconciled_rate.quantize(Decimal('0.01'))
    # Keep a recognized description/HSN as a reviewable row even when Textract
    # misses a numeric column. The zero quantity prevents stock approval until
    # the reviewer corrects it.
    quantity = quantity if quantity is not None else Decimal("0")
    amount = amount if amount is not None else Decimal("0")
    hsn_sac = _field_by_label(
        fields, ("PRODUCT_CODE", "HSN", "HSN_SAC"), ("hsn", "sac", "productcode"),
    ) or _first(mapped, "PRODUCT_CODE", "HSN", "HSN_SAC")
    unit = _field_by_label(fields, ("UNIT",), ("unit",)) or _first(mapped, "UNIT")
    if not unit and quantity_text:
        unit_match = re.search(r"\b(?:\d[\d,.]*\s*)([A-Za-z]{1,12})\s*$", quantity_text)
        if unit_match:
            unit = unit_match.group(1)
    field_confidences = [
        _confidence(fields, "ITEM", "ITEM_DESCRIPTION", "DESCRIPTION"),
        _confidence(fields, "QUANTITY"),
        _confidence(fields, "PRICE", "AMOUNT", "UNIT_PRICE", "LINE_TOTAL"),
    ]
    available_confidences = [value for value in field_confidences if value is not None]
    return {
        "description": description[:500],
        "hsn_sac": hsn_sac,
        "quantity": quantity,
        "unit": unit,
        "unit_rate": unit_rate,
        "line_amount": amount,
        "confidence": (sum(available_confidences) / len(available_confidences)
                       if available_confidences else None),
    }


def _normalized_payload(payload: dict, sale_vendor: str | None = None,
                        sale_invoice_number: str | None = None) -> dict:
    documents = payload.get("expense_documents") or []
    if not documents:
        return {}
    summary = _field_map(documents[0].get("summary_fields") or [])
    summary_fields = documents[0].get("summary_fields") or []
    settings = get_settings()
    direction, direction_reason = _infer_direction(
        summary,
        sale_vendor if sale_vendor is not None else settings.invoice_sale_vendor,
        sale_invoice_number if sale_invoice_number is not None else settings.invoice_sale_invoice_number,
    )
    lines = [
        line
        for fields in documents[0].get("line_items") or []
        if (line := _line_from_fields(fields)) is not None
    ]
    tax_total, tax_confidence = _tax_summary(summary_fields)
    field_confidence = {
        "vendor_name": _confidence(summary_fields, "VENDOR_NAME", "SUPPLIER_NAME", "RECEIVER_NAME"),
        "vendor_gstin": _confidence(summary_fields, "VENDOR_TAX_ID", "SUPPLIER_TAX_ID", "TAX_PAYER_ID"),
        "invoice_number": _confidence(summary_fields, "INVOICE_RECEIPT_ID", "INVOICE_NUMBER"),
        "invoice_date": _confidence(summary_fields, "INVOICE_RECEIPT_DATE", "INVOICE_DATE"),
        "subtotal": _confidence(summary_fields, "SUBTOTAL"),
        "grand_total": _confidence(summary_fields, "TOTAL", "AMOUNT_DUE", "TOTAL_AMOUNT", "AMOUNT_PAID"),
    }
    field_confidence['tax_total'] = tax_confidence
    return {
        "vendor_name": _first(summary, "VENDOR_NAME", "SUPPLIER_NAME"),
        "receiver_name": _first(summary, "RECEIVER_NAME", "CUSTOMER_NAME", "BILL_TO_NAME"),
        "vendor_gstin": _first(summary, "VENDOR_GST_NUMBER", "VENDOR_TAX_ID", "SUPPLIER_TAX_ID", "TAX_PAYER_ID"),
        "receiver_gstin": _first(summary, "RECEIVER_GST_NUMBER", "RECEIVER_TAX_ID", "CUSTOMER_TAX_ID"),
        "invoice_number": _first(summary, "INVOICE_RECEIPT_ID", "INVOICE_NUMBER"),
        "invoice_date": _as_date(_first(summary, "INVOICE_RECEIPT_DATE", "INVOICE_DATE")),
        "subtotal": _as_decimal(_first(summary, "SUBTOTAL")),
        "tax_total": tax_total,
        "grand_total": _as_decimal(_first(summary, "TOTAL", "AMOUNT_DUE", "TOTAL_AMOUNT", "AMOUNT_PAID")),
        "transaction_type": direction,
        "direction_reason": direction_reason,
        "field_confidence": field_confidence,
        "lines": lines,
    }


def sync_textract_results(db: Session, invoice: Invoice) -> bool:
    """Merge a completed result once; return True when the row changed."""
    if invoice.status != 'NEEDS_REVIEW':
        return False
    settings = get_settings()
    if (
        not settings.aws_extraction_enabled
        or not settings.invoice_bucket_name
        or not settings.extraction_jobs_table_name
        or not invoice.extraction_id
        or (
            invoice.extraction_status in {"RESULTS_LOADED", "SUCCEEDED", "PARTIAL_SUCCESS", "FAILED"}
            and not (invoice.extraction_status == "RESULTS_LOADED"
                     and invoice.transaction_type == "UNKNOWN"
                     and not invoice.direction_reason)
        )
    ):
        return False

    dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
    job = dynamodb.Table(settings.extraction_jobs_table_name).get_item(
        Key={"idempotency_key": invoice.extraction_id}, ConsistentRead=True
    ).get("Item")
    if not job:
        return False

    status = str(job.get("status", "IN_PROGRESS"))
    result_key = job.get("normalized_result_key")
    if status not in {"NEEDS_REVIEW", "SUCCEEDED", "PARTIAL_SUCCESS"} or not result_key:
        invoice.extraction_status = status
        db.commit()
        return True

    s3 = boto3.client("s3", region_name=settings.aws_region)
    body = s3.get_object(Bucket=settings.invoice_bucket_name, Key=result_key)["Body"].read()
    extracted = _normalized_payload(json.loads(body))
    for field in ("vendor_name", "receiver_name", "vendor_gstin", "receiver_gstin", "invoice_number", "invoice_date",
                  "subtotal", "tax_total", "grand_total"):
        if getattr(invoice, field) is None and extracted.get(field) is not None:
            setattr(invoice, field, extracted[field])
    if not invoice.lines and extracted.get("lines"):
        invoice.lines = [InvoiceLine(**line) for line in extracted["lines"]]
    if invoice.transaction_type == "UNKNOWN" and extracted.get("transaction_type") in {"PURCHASE", "SALE"}:
        invoice.transaction_type = extracted["transaction_type"]
    if not invoice.direction_reason:
        invoice.direction_reason = extracted.get("direction_reason")
    invoice.field_confidence = extracted.get("field_confidence") or {}
    invoice.status = "NEEDS_REVIEW"
    invoice.extraction_status = "RESULTS_LOADED"
    invoice.review_notes = "Textract extraction loaded; verify fields and line items before approval."
    db.commit()
    return True


def sync_textract_invoice(invoice: dict) -> dict:
    """Merge a completed Textract job into a DynamoDB-backed invoice."""
    if invoice.get('status') != 'NEEDS_REVIEW' or invoice.get('inventory_posted'):
        return invoice
    settings = get_settings()
    if (
        not settings.aws_extraction_enabled
        or not settings.invoice_bucket_name
        or not settings.extraction_jobs_table_name
        or not invoice.get("extraction_id")
        or (
            invoice.get("extraction_status") in {
                "RESULTS_LOADED", "SUCCEEDED", "PARTIAL_SUCCESS", "FAILED"
            }
            and not (invoice.get("extraction_status") == "RESULTS_LOADED"
                     and invoice.get("transaction_type", "UNKNOWN") == "UNKNOWN"
                     and not invoice.get("direction_reason"))
        )
    ):
        return invoice

    job = boto3.resource("dynamodb", region_name=settings.aws_region).Table(
        settings.extraction_jobs_table_name
    ).get_item(Key={"idempotency_key": invoice["extraction_id"]}, ConsistentRead=True).get("Item")
    if not job:
        return invoice

    status = str(job.get("status", "IN_PROGRESS"))
    result_key = job.get("normalized_result_key")
    if status not in {"NEEDS_REVIEW", "SUCCEEDED", "PARTIAL_SUCCESS"} or not result_key:
        invoice["extraction_status"] = status
        return dynamodb_store.save_invoice(invoice, expected_status='NEEDS_REVIEW') or invoice

    body = boto3.client("s3", region_name=settings.aws_region).get_object(
        Bucket=settings.invoice_bucket_name, Key=result_key
    )["Body"].read()
    extracted = _normalized_payload(json.loads(body))
    invoice["field_confidence"] = extracted.get("field_confidence") or {}
    return dynamodb_store.merge_textract(invoice, extracted, "RESULTS_LOADED") or invoice
