from pathlib import Path
import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.responses import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_db
from app.models import AppSetting, Invoice, InvoiceLine, Product, SerialAsset, StockMovement
from app.services.textract_results import sync_textract_invoice, sync_textract_results
from app.schemas.invoices import InvoiceCorrectionInput, InvoiceValidationInput, InvoiceValidationResult
from app.schemas.sales import PaymentInput, SalesInvoiceInput
from app.services.invoice_validation import validate_invoice
from app.services.sales_receipt_pdf import render_sales_receipt
from app.repositories import dynamodb_store
from app.services.serial_assets import add_months, serial_key
from app.services.pricing import effective_margins, price_points
from app.services.stock_dates import validate_dated_stock

router = APIRouter(prefix="/invoices", tags=["invoices"])
ALLOWED_TYPES = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}


def _check_local_stock_date(db, product, change, effective_date):
    history = db.scalars(select(StockMovement).where(StockMovement.product_id == product.id)).all()
    try:
        validate_dated_stock(product.quantity_on_hand,
            [{'quantity_change': m.quantity_change, 'effective_date': m.effective_date} for m in history],
            change, effective_date, product.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _s3_client():
    import boto3
    return boto3.client("s3", region_name=get_settings().aws_region)


def _validation_warnings(data: dict) -> list[str]:
    try:
        payload = InvoiceValidationInput.model_validate({
            key: data.get(key) for key in (
                "vendor_name", "vendor_gstin", "receiver_gstin", "invoice_number", "invoice_date",
                "currency", "subtotal", "tax_total", "grand_total", "transaction_type", "lines",
            )
        })
    except Exception:
        return ["Some invoice fields could not be validated; review the extracted details"]
    return validate_invoice(payload)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _invoice_dict(invoice: Invoice) -> dict:
    if isinstance(invoice, dict):
        serialized = {key: value for key, value in {**invoice, "lines": invoice.get("lines") or []}.items()
                      if key != "content_sha256"}
        serialized["validation_warnings"] = _validation_warnings(serialized)
        return serialized
    serialized = {
        "id": invoice.id, "filename": invoice.filename, "source_type": invoice.source_type,
        "vendor_name": invoice.vendor_name,
        "vendor_address": invoice.vendor_address,
        "receiver_name": invoice.receiver_name, "direction_reason": invoice.direction_reason,
        "vendor_gstin": invoice.vendor_gstin, "invoice_number": invoice.invoice_number,
        "receiver_gstin": invoice.receiver_gstin, "receiver_address": invoice.receiver_address,
        "amount_paid": invoice.amount_paid, "payment_status": invoice.payment_status,
        "payment_history": invoice.payment_history or [],
        "invoice_date": invoice.invoice_date, "currency": invoice.currency,
        "subtotal": invoice.subtotal, "tax_total": invoice.tax_total,
        "grand_total": invoice.grand_total, "status": invoice.status,
        "transaction_type": invoice.transaction_type,
        "extraction_status": invoice.extraction_status,
        "field_confidence": invoice.field_confidence or {},
        "review_notes": invoice.review_notes, "created_at": invoice.created_at,
        "inventory_posted": bool(invoice.review_notes and invoice.review_notes.startswith("Inventory posted:")),
        "lines": [{"id": line.id, "product_id": str(line.product_id) if line.product_id is not None else None,
                   "description": line.description, "hsn_sac": line.hsn_sac,
                   "is_stock_item": line.is_stock_item,
                   "quantity": line.quantity, "unit": line.unit, "unit_rate": line.unit_rate,
                   "line_amount": line.line_amount, "confidence": line.confidence,
                   "tax_rate": line.tax_rate, "tax_amount": line.tax_amount,
                   "serial_numbers": line.serial_numbers or [], "warranty_months": line.warranty_months,
                   "suggested_unit_rate": line.suggested_unit_rate,
                   "price_override_reason": line.price_override_reason}
                  for line in invoice.lines],
    }
    serialized["validation_warnings"] = _validation_warnings(serialized)
    return serialized


def _normalized_identity(value: str | None) -> str:
    return "".join(character for character in (value or "").casefold() if character.isalnum())


def _normalized_product_identity(value: str | None) -> str:
    normalized = (value or "").casefold().replace("×", "x")
    normalized = re.sub(r"\bhard[\s_-]*(?:disk|drive)\b|\bharddisk\b|\bharddrive\b|\bhdd\b", "harddisk", normalized)
    normalized = re.sub(r"\bsolid[\s_-]*state[\s_-]*drive\b|\bsolidstate\b|\bssd\b", "ssd", normalized)
    normalized = re.sub(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb|mp|inch|inches)\b", r"\1\2", normalized)
    tokens = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", normalized)
    return " ".join(sorted(tokens))


def _normalized_hsn(value: str | None) -> str:
    return "".join(character for character in (value or "") if character.isalnum()).casefold()


def _with_duplicate_matches(invoice: dict, candidates: list[dict]) -> dict:
    matches = []
    invoice_hash = invoice.get("content_sha256")
    vendor_gstin = _normalized_identity(invoice.get("vendor_gstin"))
    vendor_name = _normalized_identity(invoice.get("vendor_name"))
    invoice_number = _normalized_identity(invoice.get("invoice_number"))
    for candidate in candidates:
        if str(candidate.get("id")) == str(invoice.get("id")):
            continue
        same_file = bool(invoice_hash and invoice_hash == candidate.get("content_sha256"))
        candidate_number = _normalized_identity(candidate.get("invoice_number"))
        candidate_gstin = _normalized_identity(candidate.get("vendor_gstin"))
        candidate_vendor = _normalized_identity(candidate.get("vendor_name"))
        same_vendor = bool(
            (vendor_gstin and candidate_gstin and vendor_gstin == candidate_gstin)
            or (vendor_name and candidate_vendor and vendor_name == candidate_vendor)
        )
        same_invoice = bool(invoice_number and invoice_number == candidate_number and same_vendor)
        if same_file or same_invoice:
            matches.append({
                "id": candidate.get("id"),
                "filename": candidate.get("filename"),
                "match_type": "same_file" if same_file else "same_vendor_invoice",
            })
    return {key: value for key, value in {**invoice, "duplicate_matches": matches}.items()
            if key != "content_sha256"}


def _existing_invoice_records(db: Session) -> list[dict]:
    if get_settings().business_data_table_name:
        return dynamodb_store.list_invoices()
    return [_invoice_dict(row) | {"content_sha256": row.content_sha256}
            for row in db.scalars(select(Invoice)).all()]


@router.post("/validate", response_model=InvoiceValidationResult)
def validate_invoice_payload(invoice: InvoiceValidationInput) -> InvoiceValidationResult:
    warnings = validate_invoice(invoice)
    return InvoiceValidationResult(valid=not warnings, review_required=bool(warnings), warnings=warnings)


@router.post("/sales", status_code=201)
def create_sales_invoice(payload: SalesInvoiceInput, db: Session = Depends(get_db)) -> dict:
    """Create a sales receipt, post stock, and record any receivable."""
    settings = get_settings()
    if payload.receiver_gstin or any(line.tax_rate != 0 for line in payload.lines):
        raise HTTPException(status_code=422, detail="Sales receipts cannot charge tax or include a tax registration number.")

    if get_settings().business_data_table_name:
        products = dynamodb_store.list_products()
        existing_invoices = dynamodb_store.list_invoices()
    else:
        products = [{"id": row.id, "sku": row.sku, "name": row.name,
                     "hsn_sac": row.hsn_sac, "quantity_on_hand": row.quantity_on_hand,
                     "unit_cost": row.unit_cost,
                     "minimum_margin_percent": row.minimum_margin_percent,
                     "target_margin_percent": row.target_margin_percent}
                    for row in db.scalars(select(Product)).all()]
        existing_invoices = [_invoice_dict(row) for row in db.scalars(select(Invoice)).all()]
    policy_row = None if get_settings().business_data_table_name else db.get(AppSetting, "pricing_policy")
    pricing_policy = (dynamodb_store.get_pricing_policy() if get_settings().business_data_table_name
                      else (policy_row.value if policy_row else {}))
    if any(row.get("transaction_type") == "SALE" and row.get("invoice_number") == payload.invoice_number
           for row in existing_invoices):
        raise HTTPException(status_code=409, detail="That sales invoice number is already in use.")

    by_id = {str(product["id"]): product for product in products}
    prepared_lines = []
    stock_required: dict[str, int] = {}
    submitted_serial_keys: set[str] = set()
    subtotal = Decimal("0")
    tax_total = Decimal("0")
    for index, line in enumerate(payload.lines, start=1):
        product = by_id.get(str(line.product_id))
        if not product:
            raise HTTPException(status_code=422, detail=f"Select an existing inventory product for item {index}.")
        if line.quantity != line.quantity.to_integral_value():
            raise HTTPException(status_code=422, detail=f"Item {index} quantity must be a whole number for stock tracking.")
        quantity = int(line.quantity)
        unit_cost = Decimal(str(product.get("unit_cost") or 0))
        minimum_margin, target_margin = effective_margins(product, pricing_policy)
        floor_rate, suggested_rate = price_points(unit_cost, minimum_margin, target_margin)
        if line.unit_rate < floor_rate and not (line.price_override_reason or "").strip():
            raise HTTPException(status_code=422, detail=f"Selling rate for {product['name']} is below its floor (₹{_money(floor_rate)}). Enter an override reason to continue.")
        serials = [value.strip() for value in line.serial_numbers]
        normalized_serials = [serial_key(value) for value in serials]
        if serials and (len(serials) != quantity or any(not key for key in normalized_serials)):
            raise HTTPException(status_code=422, detail=f"Item {index} needs one valid serial number per unit, or no serial numbers.")
        if len(set(normalized_serials)) != len(normalized_serials):
            raise HTTPException(status_code=422, detail=f"Item {index} has duplicate serial numbers.")
        if submitted_serial_keys.intersection(normalized_serials):
            raise HTTPException(status_code=422, detail="A serial number cannot appear on more than one sale line.")
        submitted_serial_keys.update(normalized_serials)
        if not get_settings().business_data_table_name:
            if payload.lines[index - 1].serial_numbers:
                assets = [db.scalar(select(SerialAsset).where(SerialAsset.serial_key == key))
                          for key in normalized_serials]
                if any(asset is None for asset in assets):
                    raise HTTPException(status_code=422, detail=f"A serial number was not found for {product['name']}.")
                if any(asset.status != "IN_STOCK" or asset.product_id != str(product["id"]) for asset in assets):
                    raise HTTPException(status_code=422, detail=f"A serial number is not in stock for {product['name']}.")
                if len(assets) != len(set(normalized_serials)):
                    raise HTTPException(status_code=422, detail=f"Item {index} repeats a serial number.")
            elif db.scalar(select(SerialAsset.id).where(SerialAsset.product_id == str(product["id"]),
                    SerialAsset.status == "IN_STOCK").limit(1)):
                raise HTTPException(status_code=422, detail=f"Enter serial numbers for serialized stock: {product['name']}.")
        stock_required[str(product["id"])] = stock_required.get(str(product["id"]), 0) + quantity
        taxable = _money(line.quantity * line.unit_rate)
        tax = _money(taxable * line.tax_rate / Decimal("100"))
        subtotal += taxable
        tax_total += tax
        prepared_lines.append({
            "product_id": str(product["id"]), "description": product["name"],
            "hsn_sac": product.get("hsn_sac"), "quantity": line.quantity,
            "unit": "unit", "unit_rate": line.unit_rate,
            "line_amount": taxable, "tax_rate": line.tax_rate,
            "tax_amount": tax, "serial_numbers": serials, "warranty_months": None,
            "suggested_unit_rate": _money(suggested_rate),
            "price_override_reason": (line.price_override_reason or "").strip() or None,
        })
    subtotal = _money(subtotal)
    tax_total = _money(tax_total)
    grand_total = _money(subtotal + tax_total)
    if payload.amount_paid > grand_total:
        raise HTTPException(status_code=422, detail="Amount received cannot exceed the invoice total.")
    payment_status = "PAID" if payload.amount_paid == grand_total else "PARTIAL" if payload.amount_paid > 0 else "UNPAID"
    for product_id, quantity in stock_required.items():
        if int(by_id[product_id]["quantity_on_hand"]) < quantity:
            raise HTTPException(status_code=422, detail=f"Not enough stock for {by_id[product_id]['name']}; {by_id[product_id]['quantity_on_hand']} available.")

    record = {
        "filename": f"Sales-{payload.invoice_number}.pdf", "content_type": "application/pdf",
        "stored_path": f"generated-sales/{payload.invoice_number}.pdf", "source_type": "DEMO_RECEIPT" if payload.demo else "POS_RECEIPT",
        "vendor_name": (settings.business_name or "Demo Store").strip(), "vendor_address": (settings.business_address or "").strip(),
        "vendor_gstin": None,
        "receiver_name": payload.receiver_name.strip(), "receiver_gstin": payload.receiver_gstin.strip().upper() if payload.receiver_gstin else None,
        "receiver_address": payload.receiver_address, "invoice_number": payload.invoice_number.strip(),
        "invoice_date": payload.invoice_date, "currency": "INR", "subtotal": subtotal,
        "tax_total": tax_total, "grand_total": grand_total, "amount_paid": payload.amount_paid,
        "payment_status": payment_status, "transaction_type": "SALE",
        "payment_history": ([{"amount": str(payload.amount_paid), "method": "POS",
                               "reference": payload.invoice_number.strip(),
                               "received_at": payload.invoice_date.isoformat()}]
                             if payload.amount_paid > 0 else []),
        "direction_reason": "Manually entered sale; verified by operator.",
        "status": "NEEDS_REVIEW", "review_notes": None, "extraction_status": "MANUAL",
        "field_confidence": {}, "lines": prepared_lines,
    }
    if get_settings().business_data_table_name:
        created = dynamodb_store.create_invoice(record)
        try:
            approved = dynamodb_store.approve_invoice_and_post_stock(created, products)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=f"Sale was saved for review but stock was not posted: {exc}") from exc
        return _invoice_dict(approved)

    try:
        invoice = Invoice(**{key: value for key, value in record.items() if key != "lines"})
        db.add(invoice)
        db.flush()
        for line in prepared_lines:
            db.add(InvoiceLine(invoice_id=invoice.id, product_id=int(line["product_id"]),
                               description=line["description"], hsn_sac=line["hsn_sac"],
                               quantity=line["quantity"], unit=line["unit"], unit_rate=line["unit_rate"],
                               line_amount=line["line_amount"], tax_rate=line["tax_rate"],
                               tax_amount=line["tax_amount"], serial_numbers=line["serial_numbers"],
                               suggested_unit_rate=line["suggested_unit_rate"],
                               price_override_reason=line["price_override_reason"]))
            for serial in line["serial_numbers"]:
                key = serial_key(serial)
                asset = db.scalar(select(SerialAsset).where(SerialAsset.serial_key == key))
                if asset:
                    if asset.status != "IN_STOCK" or asset.product_id != line["product_id"]:
                        raise HTTPException(status_code=409, detail=f"Serial number {serial} is not available for this product.")
                    asset.status = "SOLD"
                    asset.sale_invoice_id = str(invoice.id)
        for product_id, quantity in stock_required.items():
            _check_local_stock_date(db, db.get(Product, int(product_id)), -quantity, payload.invoice_date)
            result = db.execute(update(Product).where(Product.id == int(product_id),
                                                       Product.quantity_on_hand >= quantity)
                                .values(quantity_on_hand=Product.quantity_on_hand - quantity))
            if result.rowcount != 1:
                raise HTTPException(status_code=409, detail="Stock changed while completing the sale. Refresh and retry.")
            db.add(StockMovement(product_id=int(product_id), quantity_change=-quantity,
                                 reason=f"POS sale {payload.invoice_number}", effective_date=payload.invoice_date,
                                 movement_type="SALE"))
        invoice.status = "APPROVED"
        invoice.review_notes = "Inventory posted: POS sale completed"
        db.commit()
        db.refresh(invoice)
        return _invoice_dict(invoice)
    except Exception:
        db.rollback()
        raise


@router.post("/upload", status_code=201)
async def upload_invoice(file: UploadFile = File(...), allow_duplicate: bool = Query(False),
                         transaction_type: Literal["UNKNOWN", "PURCHASE", "SALE"] = Query("UNKNOWN"),
                         demo: bool = Query(False),
                         db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    suffix = ALLOWED_TYPES.get(file.content_type or "")
    if suffix is None:
        raise HTTPException(status_code=415, detail="Upload a PDF, JPEG, or PNG invoice.")
    content = await file.read(settings.max_upload_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File exceeds the 15 MB local upload limit.")
    content_sha256 = hashlib.sha256(content).hexdigest()
    existing = _existing_invoice_records(db)
    same_file = [row for row in existing if row.get("content_sha256") == content_sha256]
    if same_file and not allow_duplicate:
        raise HTTPException(
            status_code=409,
            detail=f"This file matches {same_file[0].get('filename', 'an existing invoice')}. "
                   "Upload again only if it is an intentional second copy.",
        )
    object_key = f"incoming/{uuid4().hex}{suffix}"
    extraction_id = None
    extraction_status = "MANUAL"
    if settings.invoice_bucket_name:
        stored = _s3_client().put_object(Bucket=settings.invoice_bucket_name, Key=object_key,
                                         Body=content, ContentType=file.content_type,
                                         ServerSideEncryption="AES256")
        stored_path = object_key
        object_version = stored.get("VersionId") or stored.get("ETag", "").strip('"')
        if object_version:
            raw = f"{settings.invoice_bucket_name}\n{object_key}\n{object_version}".encode("utf-8")
            extraction_id = hashlib.sha256(raw).hexdigest()
            extraction_status = "QUEUED"
    else:
        upload_dir = Path(settings.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        local_path = upload_dir / f"{uuid4().hex}{suffix}"
        local_path.write_bytes(content)
        stored_path = str(local_path)
    record = {"filename": Path(file.filename or f"invoice{suffix}").name,
              "source_type": "DEMO" if demo else "UPLOAD",
              "content_type": file.content_type or "application/octet-stream",
              "stored_path": str(stored_path), "vendor_name": None, "receiver_name": None,
              "direction_reason": (f"Upload direction selected as {transaction_type}; verify before approval."
                                   if transaction_type != "UNKNOWN" else None), "vendor_gstin": None,
              "invoice_number": None, "invoice_date": None, "currency": "INR",
              "subtotal": None, "tax_total": None, "grand_total": None,
              "content_sha256": content_sha256, "field_confidence": {},
              "transaction_type": transaction_type, "status": "NEEDS_REVIEW",
              "extraction_id": extraction_id, "extraction_status": extraction_status,
              "review_notes": ("Uploaded to S3; waiting for Textract extraction."
                               if extraction_id else
                               "Uploaded locally. AWS Textract extraction is not connected yet."),
              "lines": []}
    if settings.business_data_table_name:
        created = _invoice_dict(dynamodb_store.create_invoice(record)) | {
            "content_sha256": content_sha256
        }
        return _with_duplicate_matches(created, existing)
    record = Invoice(**record)
    db.add(record)
    db.commit()
    db.refresh(record)
    created = _invoice_dict(record)
    created["content_sha256"] = content_sha256
    return _with_duplicate_matches(created, existing)


@router.get("")
def list_invoices(db: Session = Depends(get_db)) -> list[dict]:
    if get_settings().business_data_table_name:
        rows = [sync_textract_invoice(row) for row in dynamodb_store.list_invoices()]
        serialized = [_invoice_dict(row) | {"content_sha256": row.get("content_sha256")}
                      for row in rows]
        return [_with_duplicate_matches(row, rows) for row in serialized]
    rows = db.scalars(select(Invoice).order_by(Invoice.created_at.desc())).all()
    for row in rows:
        sync_textract_results(db, row)
    candidates = [_invoice_dict(row) | {"content_sha256": row.content_sha256} for row in rows]
    return [_with_duplicate_matches(row, candidates) for row in candidates]


@router.post("/{invoice_id}/payments")
def record_invoice_payment(invoice_id: str, payload: PaymentInput,
                           db: Session = Depends(get_db)) -> dict:
    """Record a received payment toward an approved sale or purchase invoice."""
    if get_settings().business_data_table_name:
        try:
            invoice = dynamodb_store.record_invoice_payment(
                invoice_id, payload.amount, method=payload.method, reference=payload.reference)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if invoice is None:
            raise HTTPException(status_code=404, detail="Invoice not found.")
        return _invoice_dict(invoice)
    try:
        numeric_id = int(invoice_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Invoice not found.") from exc
    invoice = db.get(Invoice, numeric_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != "APPROVED":
        raise HTTPException(status_code=409, detail="Payments can only be recorded on approved invoices.")
    if invoice.grand_total is None:
        raise HTTPException(status_code=409, detail="Invoice total is missing; correct it before recording a payment.")
    updated = Decimal(invoice.amount_paid or 0) + payload.amount
    if updated > invoice.grand_total:
        raise HTTPException(status_code=422, detail="Payment exceeds the outstanding invoice balance.")
    received_at = datetime.now(timezone.utc).isoformat()
    history = list(invoice.payment_history or [])
    history.append({"amount": str(payload.amount), "method": payload.method,
                    "reference": payload.reference, "received_at": received_at,
                    "recorded_at": received_at})
    invoice.amount_paid = updated
    invoice.payment_status = "PAID" if updated == invoice.grand_total else "PARTIAL"
    invoice.payment_history = history
    db.commit()
    db.refresh(invoice)
    return _invoice_dict(invoice)


@router.get("/{invoice_id}")
def get_invoice(invoice_id: str, db: Session = Depends(get_db)) -> dict:
    if get_settings().business_data_table_name:
        invoice = dynamodb_store.get_invoice(invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found.")
        serialized = _invoice_dict(sync_textract_invoice(invoice)) | {
            "content_sha256": invoice.get("content_sha256")
        }
        return _with_duplicate_matches(serialized, dynamodb_store.list_invoices())
    invoice = db.get(Invoice, int(invoice_id))
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    sync_textract_results(db, invoice)
    return _with_duplicate_matches(
        _invoice_dict(invoice) | {"content_sha256": invoice.content_sha256},
        [_invoice_dict(row) | {"content_sha256": row.content_sha256}
         for row in db.scalars(select(Invoice)).all()],
    )


@router.get("/{invoice_id}/receipt-pdf")
def download_sales_receipt(invoice_id: str, db: Session = Depends(get_db)):
    invoice = (dynamodb_store.get_invoice(invoice_id) if get_settings().business_data_table_name
               else db.get(Invoice, int(invoice_id)))
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    get = invoice.get if isinstance(invoice, dict) else lambda key, default=None: getattr(invoice, key, default)
    if get("source_type") not in {"POS_RECEIPT", "DEMO_RECEIPT"} or get("transaction_type") != "SALE":
        raise HTTPException(status_code=422, detail="Receipt PDFs are generated only for manually entered receipt sales.")
    if get("status") != "APPROVED":
        raise HTTPException(status_code=409, detail="Approve the sale before issuing its receipt.")
    record = _invoice_dict(invoice)
    record["lines"] = record.get("lines") or []
    return Response(content=render_sales_receipt(record), media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=Sales-{get('invoice_number')}.pdf"})


@router.get("/{invoice_id}/document")
def get_invoice_document(invoice_id: str, db: Session = Depends(get_db)):
    invoice = (dynamodb_store.get_invoice(invoice_id) if get_settings().business_data_table_name
               else db.get(Invoice, int(invoice_id)))
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice document not found.")
    get = invoice.get if isinstance(invoice, dict) else lambda key, default=None: getattr(invoice, key, default)
    if get("source_type") in {"POS", "POS_RECEIPT", "DEMO_RECEIPT"}:
        return download_sales_receipt(invoice_id, db)
    settings = get_settings()
    if settings.invoice_bucket_name:
        stored_path = invoice["stored_path"] if isinstance(invoice, dict) else invoice.stored_path
        filename = invoice["filename"] if isinstance(invoice, dict) else invoice.filename
        url = _s3_client().generate_presigned_url(
            "get_object", Params={"Bucket": settings.invoice_bucket_name, "Key": stored_path,
                                  "ResponseContentDisposition": f"inline; filename*=UTF-8''{quote(filename)}"},
            ExpiresIn=300,
        )
        return RedirectResponse(url)
    stored_path = invoice["stored_path"] if isinstance(invoice, dict) else invoice.stored_path
    content_type = invoice["content_type"] if isinstance(invoice, dict) else invoice.content_type
    filename = invoice["filename"] if isinstance(invoice, dict) else invoice.filename
    if not Path(stored_path).is_file():
        raise HTTPException(status_code=404, detail="Invoice document not found.")
    return FileResponse(stored_path, media_type=content_type,
                        filename=filename, content_disposition_type="inline")


@router.get("/{invoice_id}/document-url")
def get_invoice_document_url(invoice_id: str, db: Session = Depends(get_db)):
    """Return an authenticated, short-lived URL for the browser's inline preview."""
    invoice = (dynamodb_store.get_invoice(invoice_id) if get_settings().business_data_table_name
               else db.get(Invoice, int(invoice_id)))
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice document not found.")
    get = invoice.get if isinstance(invoice, dict) else lambda key, default=None: getattr(invoice, key, default)
    if get("source_type") in {"POS", "POS_RECEIPT", "DEMO_RECEIPT"}:
        raise HTTPException(status_code=501, detail="Generated invoices are served directly.")
    settings = get_settings()
    if not settings.invoice_bucket_name:
        raise HTTPException(status_code=501, detail="Signed previews are only available for cloud-stored invoices.")
    stored_path = invoice["stored_path"] if isinstance(invoice, dict) else invoice.stored_path
    filename = invoice["filename"] if isinstance(invoice, dict) else invoice.filename
    content_type = invoice.get("content_type", "application/octet-stream") if isinstance(invoice, dict) else invoice.content_type
    url = _s3_client().generate_presigned_url(
        "get_object", Params={"Bucket": settings.invoice_bucket_name, "Key": stored_path,
                              "ResponseContentDisposition": f"inline; filename*=UTF-8''{quote(filename)}"},
        ExpiresIn=300,
    )
    return {"url": url, "filename": filename, "content_type": content_type}


@router.patch("/{invoice_id}/correction")
def correct_invoice(invoice_id: str, payload: InvoiceCorrectionInput,
                    db: Session = Depends(get_db)) -> dict:
    if get_settings().business_data_table_name:
        invoice = dynamodb_store.get_invoice(invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found.")
        if invoice.get("inventory_posted") or invoice.get("status") == "APPROVED":
            raise HTTPException(status_code=409, detail="This invoice is approved; use a stock adjustment to correct it.")
        for field, value in payload.model_dump(exclude_unset=True).items():
            if field == "lines" and value is not None:
                invoice[field] = [line.model_dump() if hasattr(line, "model_dump") else line for line in value]
            else:
                invoice[field] = value
        if payload.transaction_type is not None:
            invoice["direction_reason"] = f"Direction manually confirmed as {payload.transaction_type}."
        invoice["review_notes"] = "Fields manually corrected by reviewer."
        return _invoice_dict(dynamodb_store.save_invoice(invoice))
    invoice = db.get(Invoice, int(invoice_id))
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status == "APPROVED":
        raise HTTPException(status_code=409, detail="This invoice is approved; use a stock adjustment to correct it.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "lines" and value is not None:
            corrected_lines = []
            for item in value:
                product_id = None
                if item.get("product_id"):
                    try:
                        product_id = int(item["product_id"])
                    except (TypeError, ValueError) as exc:
                        raise HTTPException(status_code=422, detail="Selected inventory product is invalid.") from exc
                    if not db.get(Product, product_id):
                        raise HTTPException(status_code=422, detail="Selected inventory product no longer exists.")
                corrected_lines.append(InvoiceLine(**{**item, "product_id": product_id}))
            invoice.lines = corrected_lines
            continue
        setattr(invoice, field, value)
    if payload.transaction_type is not None:
        invoice.direction_reason = f"Direction manually confirmed as {payload.transaction_type}."
    invoice.review_notes = "Fields manually corrected by reviewer."
    db.commit()
    db.refresh(invoice)
    return _invoice_dict(invoice)


@router.patch("/{invoice_id}/review")
def review_invoice(invoice_id: str, payload: dict, db: Session = Depends(get_db)) -> dict:
    if get_settings().business_data_table_name:
        invoice = dynamodb_store.get_invoice(invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found.")
        action = payload.get("action")
        if action not in {"approve", "reject"}:
            raise HTTPException(status_code=422, detail="action must be 'approve' or 'reject'.")
        if invoice.get("status") != "NEEDS_REVIEW":
            raise HTTPException(status_code=409, detail="Only an invoice waiting for review can be approved or rejected.")
        if action == "approve" and not all((invoice.get("vendor_name"), invoice.get("invoice_number"),
                                             invoice.get("invoice_date"), invoice.get("grand_total") is not None)):
            raise HTTPException(status_code=422, detail="Correct vendor, invoice number, invoice date, and grand total before approval.")
        if action == "approve" and invoice.get("transaction_type") not in {"PURCHASE", "SALE"}:
            raise HTTPException(status_code=422, detail="Set the invoice direction to PURCHASE or SALE before approval.")
        if action == "approve":
            try:
                approved = dynamodb_store.approve_invoice_and_post_stock(
                    invoice, dynamodb_store.list_products())
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return _invoice_dict(approved)
        invoice["status"] = "REJECTED"
        invoice["review_notes"] = str(payload.get("notes", ""))[:2000] or None
        return _invoice_dict(dynamodb_store.save_invoice(invoice))
    invoice = db.get(Invoice, int(invoice_id))
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    action = payload.get("action")
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="action must be 'approve' or 'reject'.")
    if invoice.status != "NEEDS_REVIEW":
        raise HTTPException(status_code=409, detail="Only an invoice waiting for review can be approved or rejected.")
    if action == "approve" and not all((invoice.vendor_name, invoice.invoice_number,
                                        invoice.invoice_date, invoice.grand_total is not None)):
        raise HTTPException(status_code=422, detail="Correct vendor, invoice number, invoice date, and grand total before approval.")
    if action == "approve" and invoice.transaction_type not in {"PURCHASE", "SALE"}:
        raise HTTPException(status_code=422, detail="Set the invoice direction to PURCHASE or SALE before approval.")
    if action == "approve":
        if not invoice.lines:
            raise HTTPException(status_code=422, detail="Add at least one invoice line before approval.")
        if invoice.status != "NEEDS_REVIEW":
            raise HTTPException(status_code=409, detail="Only an invoice waiting for review can be approved.")
        products = db.scalars(select(Product)).all()
        by_id = {str(product.id): product for product in products}
        by_name: dict[str, list[Product]] = {}
        by_identity: dict[str, list[Product]] = {}
        by_hsn: dict[str, list[Product]] = {}
        for product in products:
            key = _normalized_identity(product.name)
            by_name.setdefault(key, []).append(product)
            hsn = _normalized_hsn(product.hsn_sac)
            if hsn:
                by_hsn.setdefault(hsn, []).append(product)
            for label in [product.name, *(product.aliases or [])]:
                identity = _normalized_product_identity(label)
                if identity:
                    bucket = by_identity.setdefault(identity, [])
                    if product not in bucket:
                        bucket.append(product)
        changes: dict[int, int] = {}
        purchase_quantities: dict[int, Decimal] = {}
        purchase_values: dict[int, Decimal] = {}
        movement_rows = []
        for index, line in enumerate(invoice.lines):
            if line.is_stock_item is False:
                if line.product_id is not None or line.serial_numbers:
                    raise HTTPException(status_code=422, detail="Non-stock lines cannot have inventory products or serial numbers.")
                continue
            product = by_id.get(str(getattr(line, "product_id", "") or ""))
            if product is None:
                matches = by_name.get(_normalized_identity(line.description), [])
                if not matches:
                    matches = by_identity.get(_normalized_product_identity(line.description), [])
                if len(matches) > 1:
                    raise HTTPException(status_code=422, detail=f"Select the inventory product for item {index + 1}; its name matches multiple products.")
                product = matches[0] if len(matches) == 1 else None
            # Require a name/specification match or the reviewer's explicit mapping.
            # A unique HSN code alone does not identify a product.
            if product is None:
                if invoice.transaction_type != "PURCHASE":
                    raise HTTPException(status_code=422, detail=f"Match sale item {index + 1} to an inventory product before approval.")
                product = by_name.get(_normalized_identity(line.description), [None])[0]
                if product is None:
                    sku = "AUTO-" + hashlib.sha256(_normalized_identity(line.description).encode("utf-8")).hexdigest()[:12].upper()
                    product = Product(sku=sku, name=line.description, quantity_on_hand=0,
                                      hsn_sac=line.hsn_sac, reorder_point=0, unit_cost=line.unit_rate or 0,
                                      stock_count_verified=False)
                    db.add(product)
                    db.flush()
                    by_id[str(product.id)] = product
                    by_name.setdefault(_normalized_identity(line.description), []).append(product)
                    by_identity.setdefault(_normalized_product_identity(line.description), []).append(product)
            # Persist the reviewer/matcher decision on the invoice line. This is
            # needed for price history and prevents later views from losing the match.
            line.product_id = product.id
            if invoice.transaction_type == "SALE" and line.unit_rate is not None:
                unit_cost = Decimal(product.unit_cost or 0)
                floor_rate = (unit_cost / (Decimal("1") - Decimal(product.minimum_margin_percent) / Decimal("100"))
                              if product.minimum_margin_percent is not None else unit_cost)
                suggested_rate = (unit_cost / (Decimal("1") - Decimal(product.target_margin_percent) / Decimal("100"))
                                  if product.target_margin_percent is not None else floor_rate)
                line.suggested_unit_rate = _money(suggested_rate)
                if line.unit_rate < floor_rate and not (line.price_override_reason or "").strip():
                    raise HTTPException(status_code=422, detail=f"Selling rate for {product.name} is below its floor. Add an override reason before approval.")
            # A reviewer-selected match is a durable alias decision. Exact and
            # specification-order variants are handled by the canonical matcher.
            if _normalized_identity(line.description) != _normalized_identity(product.name):
                aliases = list(product.aliases or [])
                if _normalized_identity(line.description) not in {_normalized_identity(alias) for alias in aliases}:
                    aliases.append(line.description.strip())
                    product.aliases = aliases
                    identity = _normalized_product_identity(line.description)
                    bucket = by_identity.setdefault(identity, [])
                    if product not in bucket:
                        bucket.append(product)
            quantity = line.quantity
            if quantity <= 0 or quantity != quantity.to_integral_value():
                raise HTTPException(status_code=422, detail=f"Invoice item {index + 1} quantity must be a whole number for inventory posting.")
            change = int(quantity) * (1 if invoice.transaction_type == "PURCHASE" else -1)
            changes[product.id] = changes.get(product.id, 0) + change
            if invoice.transaction_type == "PURCHASE":
                purchase_quantities[product.id] = purchase_quantities.get(product.id, Decimal("0")) + quantity
                rate = line.unit_rate if line.unit_rate is not None else product.unit_cost
                purchase_values[product.id] = purchase_values.get(product.id, Decimal("0")) + quantity * rate
                if not product.hsn_sac and line.hsn_sac:
                    product.hsn_sac = line.hsn_sac
            movement_rows.append((product, change, index))
            serials = [str(value).strip() for value in (line.serial_numbers or [])]
            normalized_serials = [serial_key(value) for value in serials]
            if serials and (len(serials) != int(quantity) or any(not key for key in normalized_serials)):
                raise HTTPException(status_code=422, detail=f"Invoice item {index + 1} needs one valid serial number per unit, or no serial numbers.")
            if len(set(normalized_serials)) != len(normalized_serials):
                raise HTTPException(status_code=422, detail=f"Invoice item {index + 1} has duplicate serial numbers.")
            if invoice.transaction_type == "PURCHASE":
                for serial, key in zip(serials, normalized_serials):
                    if db.scalar(select(SerialAsset.id).where(SerialAsset.serial_key == key)):
                        raise HTTPException(status_code=409, detail=f"Serial number {serial} has already been recorded.")
                    start_date = invoice.invoice_date
                    db.add(SerialAsset(serial_key=key, serial_number=serial, product_id=str(product.id),
                        product_name=product.name, purchase_invoice_id=str(invoice.id),
                        warranty_months=line.warranty_months, warranty_start=start_date,
                        warranty_until=add_months(start_date, line.warranty_months)))
                if not serials and db.scalar(select(SerialAsset.id).where(
                        SerialAsset.product_id == str(product.id)).limit(1)):
                    raise HTTPException(status_code=422, detail=f"Enter a serial number for each unit of tracked product {product.name}.")
            elif serials:
                assets = [db.scalar(select(SerialAsset).where(SerialAsset.serial_key == key)) for key in normalized_serials]
                for serial, asset in zip(serials, assets):
                    if not asset or asset.status != "IN_STOCK" or asset.product_id != str(product.id):
                        raise HTTPException(status_code=422, detail=f"Serial number {serial} is not in stock for {product.name}.")
                    asset.status = "SOLD"
                    asset.sale_invoice_id = str(invoice.id)
            elif db.scalar(select(SerialAsset.id).where(SerialAsset.product_id == str(product.id),
                    SerialAsset.status == "IN_STOCK").limit(1)):
                raise HTTPException(status_code=422, detail=f"Enter serial numbers for serialized item {product.name} before approval.")
        for product_id, change in changes.items():
            if by_id[str(product_id)].quantity_on_hand + change < 0:
                raise HTTPException(status_code=422, detail=f"Sale quantity exceeds on-hand stock for {by_id[str(product_id)].name}.")
            _check_local_stock_date(db, by_id[str(product_id)], change, invoice.invoice_date)
        for product_id, change in changes.items():
            product = by_id[str(product_id)]
            previous_quantity = Decimal(product.quantity_on_hand)
            if invoice.transaction_type == "PURCHASE" and purchase_quantities.get(product_id):
                new_quantity = previous_quantity + purchase_quantities[product_id]
                if new_quantity > 0:
                    product.unit_cost = (previous_quantity * product.unit_cost + purchase_values[product_id]) / new_quantity
            product.quantity_on_hand += change
        for product, change, index in movement_rows:
            db.add(StockMovement(product_id=product.id, quantity_change=change,
                                 reason=f"Invoice {invoice.id} / {invoice.invoice_number or ''} line {index + 1}",
                                 effective_date=invoice.invoice_date, movement_type=invoice.transaction_type))
    invoice.status = "APPROVED" if action == "approve" else "REJECTED"
    reviewer_notes = str(payload.get("notes", ""))[:1800] or None
    invoice.review_notes = (f"Inventory posted: {reviewer_notes}" if reviewer_notes
                            else "Inventory posted: automatically from approved invoice") if action == "approve" else reviewer_notes
    db.commit()
    db.refresh(invoice)
    return _invoice_dict(invoice)


@router.post("/{invoice_id}/post-inventory")
def post_approved_invoice_inventory(invoice_id: str, db: Session = Depends(get_db)) -> dict:
    """Post a previously approved invoice once, for approvals made before stock automation."""
    if get_settings().business_data_table_name:
        invoice = dynamodb_store.get_invoice(invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found.")
        if invoice.get("status") != "APPROVED":
            raise HTTPException(status_code=409, detail="Approve and review the invoice before posting inventory.")
        try:
            posted = dynamodb_store.approve_invoice_and_post_stock(
                invoice, dynamodb_store.list_products(), already_approved=True)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _invoice_dict(posted)

    invoice = db.get(Invoice, int(invoice_id))
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != "APPROVED":
        raise HTTPException(status_code=409, detail="Approve and review the invoice before posting inventory.")
    if invoice.review_notes and invoice.review_notes.startswith("Inventory posted:"):
        raise HTTPException(status_code=409, detail="Inventory has already been posted for this invoice.")
    if invoice.transaction_type not in {"PURCHASE", "SALE"}:
        raise HTTPException(status_code=422, detail="Set the invoice direction to PURCHASE or SALE first.")
    products = db.scalars(select(Product)).all()
    by_name: dict[str, list[Product]] = {}
    for product in products:
        by_name.setdefault(_normalized_identity(product.name), []).append(product)
    changes: dict[int, int] = {}
    movement_rows = []
    for index, line in enumerate(invoice.lines):
        if line.is_stock_item is False:
            if line.product_id is not None or line.serial_numbers:
                raise HTTPException(status_code=422, detail="Non-stock lines cannot have inventory products or serial numbers.")
            continue
        matches = by_name.get(_normalized_identity(line.description), [])
        if len(matches) > 1:
            raise HTTPException(status_code=422, detail=f"Select the inventory product for item {index + 1}; its name matches multiple products.")
        product = matches[0] if len(matches) == 1 else None
        if product is None and invoice.transaction_type == "PURCHASE":
            sku = "AUTO-" + hashlib.sha256(_normalized_identity(line.description).encode("utf-8")).hexdigest()[:12].upper()
            product = Product(sku=sku, name=line.description, quantity_on_hand=0,
                              hsn_sac=line.hsn_sac, reorder_point=0, unit_cost=line.unit_rate or 0,
                              stock_count_verified=False)
            db.add(product)
            db.flush()
            products.append(product)
            by_name.setdefault(_normalized_identity(line.description), []).append(product)
        if product is None:
            raise HTTPException(status_code=422, detail=f"Match sale item {index + 1} to an inventory product first.")
        if line.quantity <= 0 or line.quantity != line.quantity.to_integral_value():
            raise HTTPException(status_code=422, detail=f"Invoice item {index + 1} quantity must be a positive whole number.")
        change = int(line.quantity) * (1 if invoice.transaction_type == "PURCHASE" else -1)
        changes[product.id] = changes.get(product.id, 0) + change
        movement_rows.append((product, change, index))
    for product_id, change in changes.items():
        if by_id_product := next((p for p in products if p.id == product_id), None):
            if by_id_product.quantity_on_hand + change < 0:
                raise HTTPException(status_code=422, detail=f"Sale quantity exceeds on-hand stock for {by_id_product.name}.")
            _check_local_stock_date(db, by_id_product, change, invoice.invoice_date)
    for product, change, index in movement_rows:
        reason = f"Invoice {invoice.id} / {invoice.invoice_number or ''} line {index + 1}"
        if db.scalar(select(StockMovement).where(StockMovement.product_id == product.id,
                                                 StockMovement.reason == reason)):
            raise HTTPException(status_code=409, detail="Inventory movements already exist for this invoice.")
    for product_id, change in changes.items():
        product = next((p for p in products if p.id == product_id), None)
        if product:
            product.quantity_on_hand += change
    for product, change, index in movement_rows:
        db.add(StockMovement(product_id=product.id, quantity_change=change,
                             reason=f"Invoice {invoice.id} / {invoice.invoice_number or ''} line {index + 1}",
                             effective_date=invoice.invoice_date, movement_type=invoice.transaction_type))
    invoice.review_notes = "Inventory posted: automatically from approved invoice"
    db.commit()
    return _invoice_dict(invoice)
