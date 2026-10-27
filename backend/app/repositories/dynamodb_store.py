"""DynamoDB persistence for the cloud API runtime."""

from __future__ import annotations

import json
import hashlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Attr, ConditionExpressionBuilder, Key

from app.core.config import get_settings
from app.services.serial_assets import add_months
from app.services.stock_dates import validate_dated_stock


def _json_default(value):
    if isinstance(value, (Decimal,)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__} as JSON")


def _json_load(value: str) -> dict:
    decimal_fields = {"subtotal", "tax_total", "grand_total", "amount_paid", "amount", "quantity", "unit_rate",
                      "line_amount", "tax_rate", "tax_amount", "confidence", "unit_cost",
                      "minimum_margin_percent", "target_margin_percent", "suggested_unit_rate",
                      "actual_quantity"}

    def restore_numbers(item):
        for key in decimal_fields:
            if isinstance(item.get(key), str):
                try:
                    item[key] = Decimal(item[key])
                except InvalidOperation:
                    pass
        return item

    return json.loads(value, parse_float=Decimal, parse_int=int, object_hook=restore_numbers)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_identity(value: str | None) -> str:
    return "".join(character for character in (value or "").casefold() if character.isalnum())


def _normalized_product_identity(value: str | None) -> str:
    normalized = (value or "").casefold().replace("×", "x")
    normalized = re.sub(r"\bhard[\s_-]*(?:disk|drive)\b|\bharddisk\b|\bharddrive\b|\bhdd\b", "harddisk", normalized)
    normalized = re.sub(r"\bsolid[\s_-]*state[\s_-]*drive\b|\bsolidstate\b|\bssd\b", "ssd", normalized)
    normalized = re.sub(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb|mp|inch|inches)\b", r"\1\2", normalized)
    return " ".join(sorted(re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", normalized)))


def _normalized_hsn(value: str | None) -> str:
    return "".join(character for character in (value or "") if character.isalnum()).casefold()


def _condition_kwargs(condition) -> dict:
    built = ConditionExpressionBuilder().build_expression(condition)
    params = {"ConditionExpression": built.condition_expression}
    if built.attribute_name_placeholders:
        params["ExpressionAttributeNames"] = built.attribute_name_placeholders
    if built.attribute_value_placeholders:
        params["ExpressionAttributeValues"] = built.attribute_value_placeholders
    return params


@lru_cache(maxsize=1)
def table():
    settings = get_settings()
    if not settings.business_data_table_name:
        raise RuntimeError("BUSINESS_DATA_TABLE_NAME is required for cloud persistence")
    return boto3.resource("dynamodb", region_name=settings.aws_region).Table(
        settings.business_data_table_name
    )


def _item(pk: str, sk: str, kind: str, data: dict, *, index_pk: str | None = None,
          index_sk: str | None = None, **attrs) -> dict:
    item = {"pk": pk, "sk": sk, "entity_type": kind,
            "payload": json.dumps(data, default=_json_default, separators=(",", ":")), **attrs}
    if index_pk:
        item["gsi1pk"] = index_pk
    if index_sk:
        item["gsi1sk"] = index_sk
    return item


def _query_all(**kwargs) -> list[dict]:
    result = []
    response = table().query(**kwargs)
    result.extend(response.get("Items", []))
    while response.get("LastEvaluatedKey"):
        response = table().query(ExclusiveStartKey=response["LastEvaluatedKey"], **kwargs)
        result.extend(response.get("Items", []))
    return result


def create_invoice(data: dict) -> dict:
    invoice_id = uuid4().hex
    now = _now()
    record = {**data, "id": invoice_id, "created_at": now}
    table().put_item(Item=_item(
        f"INVOICE#{invoice_id}", "DATA", "INVOICE", record,
        index_pk="INVOICE", index_sk=f"{now}#{invoice_id}",
        status=record.get("status", "UPLOADED"),
        amount_paid=Decimal(str(record.get("amount_paid", 0))),
        payment_status=record.get("payment_status", "UNKNOWN"),
    ), ConditionExpression=Attr("pk").not_exists())
    return record


def get_invoice(invoice_id: str) -> dict | None:
    item = table().get_item(Key={"pk": f"INVOICE#{invoice_id}", "sk": "DATA"}, ConsistentRead=True).get("Item")
    return _json_load(item["payload"]) if item else None


def list_invoices() -> list[dict]:
    items = _query_all(
        IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("INVOICE"),
        ScanIndexForward=False,
    )
    return [_json_load(item["payload"]) for item in items]


def get_serial_asset(serial_number: str) -> dict | None:
    key = re.sub(r"[^a-z0-9]", "", serial_number.casefold())
    if not key:
        return None
    item = table().get_item(Key={"pk": f"SERIAL#{key}", "sk": "DATA"}).get("Item")
    return _json_load(item["payload"]) if item else None


def list_serial_assets() -> list[dict]:
    items = _query_all(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("SERIAL"),
                       ScanIndexForward=True)
    return [_json_load(item["payload"]) for item in items]


def save_invoice(record: dict, *, expected_status: str | None = None) -> dict:
    invoice_id = str(record["id"])
    existing = table().get_item(Key={"pk": f"INVOICE#{invoice_id}", "sk": "DATA"}).get("Item")
    if not existing:
        return None
    now = str(record.get("created_at") or _now())
    condition=Attr("pk").exists()
    if expected_status is not None:
        condition=condition & Attr("status").eq(expected_status)
    try:
        table().put_item(Item=_item(
            f"INVOICE#{invoice_id}", "DATA", "INVOICE", record,
            index_pk="INVOICE", index_sk=f"{now}#{invoice_id}",
            status=record.get("status", "UPLOADED"),
            amount_paid=Decimal(str(record.get("amount_paid", 0))),
            payment_status=record.get("payment_status", "UNKNOWN"),
        ), ConditionExpression=condition)
    except table().meta.client.exceptions.ConditionalCheckFailedException:
        if expected_status is None:
            raise
        return get_invoice(invoice_id)
    return record


def record_invoice_payment(invoice_id: str, amount: Decimal, *, method: str | None,
                           reference: str | None) -> dict | None:
    """Record an additional payment with a conditional balance update."""
    original_item = table().get_item(
        Key={"pk": f"INVOICE#{invoice_id}", "sk": "DATA"}, ConsistentRead=True).get("Item")
    if not original_item:
        return None
    record = _json_load(original_item["payload"])
    if record.get("status") != "APPROVED":
        raise ValueError("Payments can only be recorded on approved invoices.")
    total = Decimal(str(record.get("grand_total") or 0))
    previous = Decimal(str(record.get("amount_paid") or 0))
    updated = previous + amount
    if updated > total:
        raise ValueError("Payment exceeds the outstanding invoice balance.")
    received_at = _now()
    history = list(record.get("payment_history") or [])
    history.append({"amount": amount, "method": method, "reference": reference,
                    "received_at": received_at, "recorded_at": received_at})
    status = "PAID" if updated == total else "PARTIAL"
    saved = {**record, "amount_paid": updated, "payment_status": status,
             "payment_history": history}
    # Compare the exact stored snapshot, including older approved records whose
    # top-level payment attributes were not copied by the stock transaction.
    condition = "attribute_exists(pk) AND #status = :approved AND payload = :expected_payload"
    values = {":payload": json.dumps(saved, default=_json_default, separators=(",", ":")),
              ":new_amount": updated, ":payment_status": status, ":approved": "APPROVED",
              ":expected_payload": original_item["payload"]}
    try:
        table().update_item(
            Key={"pk": f"INVOICE#{invoice_id}", "sk": "DATA"},
            UpdateExpression="SET payload = :payload, amount_paid = :new_amount, payment_status = :payment_status",
            ConditionExpression=condition,
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=values,
        )
    except table().meta.client.exceptions.ConditionalCheckFailedException as exc:
        raise ValueError("Invoice payment changed concurrently. Refresh the invoice and retry.") from exc
    return saved


def approve_invoice_and_post_stock(record: dict, products: list[dict], *, already_approved: bool = False) -> dict:
    """Atomically approve an invoice and post its mapped stock movements once."""
    invoice_id = str(record["id"])
    expected_status = "APPROVED" if already_approved else "NEEDS_REVIEW"
    if record.get("status") != expected_status:
        raise ValueError("Invoice status changed; refresh the page and review again.")
    if record.get("inventory_posted"):
        raise ValueError("Inventory has already been posted for this invoice.")
    if record.get("source_document_kind") == "QUOTATION":
        raise ValueError("This document is a quotation. Upload the completed sales invoice before approval.")
    direction = record.get("transaction_type")
    if direction not in {"PURCHASE", "SALE"}:
        raise ValueError("Invoice direction must be PURCHASE or SALE.")
    lines = record.get("lines") or []
    if not lines:
        raise ValueError("Add at least one invoice line before approval.")

    by_id = {str(product["id"]): product for product in products}
    by_name: dict[str, list[dict]] = {}
    by_identity: dict[str, list[dict]] = {}
    by_hsn: dict[str, list[dict]] = {}
    for product in products:
        product.setdefault("aliases", [])
        name = _normalized_identity(str(product["name"]))
        by_name.setdefault(name, []).append(product)
        hsn = _normalized_hsn(str(product.get("hsn_sac") or ""))
        if hsn:
            by_hsn.setdefault(hsn, []).append(product)
        for label in [product["name"], *product["aliases"]]:
            identity = _normalized_product_identity(str(label))
            bucket = by_identity.setdefault(identity, [])
            if identity and all(str(existing["id"]) != str(product["id"]) for existing in bucket):
                bucket.append(product)

    mapped = []
    serial_writes: list[dict] = []
    serial_keys_seen: set[str] = set()
    known_serial_assets = list_serial_assets()
    new_products: dict[str, dict] = {}
    purchase_quantities: dict[str, Decimal] = {}
    purchase_values: dict[str, Decimal] = {}
    for index, line in enumerate(lines):
        if line.get("is_stock_item") is False:
            if line.get("product_id") or line.get("serial_numbers"):
                raise ValueError(f"Non-stock item {index + 1} must not have an inventory product or serial numbers.")
            if Decimal(str(line.get("quantity", 0))) <= 0:
                raise ValueError(f"Non-stock item {index + 1} requires a positive quantity.")
            continue
        product = by_id.get(str(line.get("product_id") or ""))
        if product is None:
            name = _normalized_identity(str(line.get("description") or ""))
            matches = by_name.get(name, [])
            if not matches:
                matches = by_identity.get(_normalized_product_identity(str(line.get("description") or "")), [])
            if len(matches) > 1:
                raise ValueError(f"Select the inventory product for invoice item {index + 1}; its name matches multiple products.")
            product = matches[0] if len(matches) == 1 else None
        # HSN describes a tax category, not an individual product or specification.
        # A shared code must never merge different capacities, models or equipment.
        if product is None:
            if direction != "PURCHASE":
                raise ValueError(f"Match sale item {index + 1} to an inventory product before approval.")
            product_name = str(line.get("description") or "").strip()
            if not product_name:
                raise ValueError(f"Add a description for invoice item {index + 1} before approval.")
            normalized = "".join(c for c in product_name.casefold() if c.isalnum())
            sku = "AUTO-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12].upper()
            product = new_products.get(sku) or {
                "id": sku, "sku": sku, "name": product_name,
                "quantity_on_hand": 0, "reorder_point": 0,
                "unit_cost": line.get("unit_rate") or 0,
                "hsn_sac": line.get("hsn_sac"),
                "aliases": [],
                "stock_count_verified": False,
            }
            new_products[sku] = product
            by_id[sku] = product
        quantity = Decimal(str(line.get("quantity", 0)))
        if quantity <= 0 or quantity != quantity.to_integral_value():
            raise ValueError(f"Invoice item {index + 1} quantity must be a whole number for inventory posting.")
        line["product_id"] = str(product["id"])
        if direction == "SALE" and line.get("unit_rate") is not None:
            unit_cost = Decimal(str(product.get("unit_cost") or 0))
            minimum_margin = product.get("minimum_margin_percent")
            target_margin = product.get("target_margin_percent")
            floor_rate = (unit_cost / (Decimal("1") - Decimal(str(minimum_margin)) / Decimal("100"))
                          if minimum_margin is not None else unit_cost)
            suggested_rate = (unit_cost / (Decimal("1") - Decimal(str(target_margin)) / Decimal("100"))
                              if target_margin is not None else floor_rate)
            line["suggested_unit_rate"] = str(suggested_rate.quantize(Decimal("0.01")))
            if Decimal(str(line["unit_rate"])) < floor_rate and not str(line.get("price_override_reason") or "").strip():
                raise ValueError(f"Selling rate for {product['name']} is below its floor. Add an override reason before approval.")
        serials = [str(serial).strip() for serial in (line.get("serial_numbers") or [])]
        quantity_int = int(quantity)
        if serials and (len(serials) != quantity_int or any(not re.sub(r"[^a-z0-9]", "", value.casefold()) for value in serials)):
            raise ValueError(f"Invoice item {index + 1} must have one distinct serial number per unit, or no serial numbers.")
        normalized_serials = [re.sub(r"[^a-z0-9]", "", value.casefold()) for value in serials]
        if len(set(normalized_serials)) != len(normalized_serials):
            raise ValueError(f"Invoice item {index + 1} contains a repeated serial number.")
        if serial_keys_seen.intersection(normalized_serials):
            raise ValueError("The same serial number appears on more than one invoice line.")
        serial_keys_seen.update(normalized_serials)
        has_tracked_stock = any(str(asset.get("product_id")) == str(product["id"])
                                and asset.get("status") == "IN_STOCK" for asset in known_serial_assets)
        has_tracked_product = any(str(asset.get("product_id")) == str(product["id"])
                                  for asset in known_serial_assets)
        if not serials and ((direction == "SALE" and has_tracked_stock)
                            or (direction == "PURCHASE" and has_tracked_product)):
            raise ValueError(f"Enter serial numbers for every unit of tracked product {product['name']}.")
        for serial, normalized in zip(serials, normalized_serials):
            if direction == "PURCHASE":
                warranty_months = line.get("warranty_months")
                start = record.get("invoice_date")
                if isinstance(start, str):
                    start = date.fromisoformat(start[:10]) if start else None
                asset = {"serial_number": serial, "serial_key": normalized,
                         "product_id": str(product["id"]), "product_name": str(product["name"]),
                         "purchase_invoice_id": invoice_id, "sale_invoice_id": None,
                         "warranty_months": warranty_months,
                         "warranty_until": add_months(start, int(warranty_months)).isoformat()
                             if start and warranty_months is not None else None,
                         "warranty_start": start.isoformat() if start else None,
                         "status": "IN_STOCK"}
                serial_writes.append({"Put": {"TableName": table().name,
                    "Item": _item(f"SERIAL#{normalized}", "DATA", "SERIAL_ASSET", asset,
                                  index_pk="SERIAL", index_sk=f"IN_STOCK#{normalized}",
                                  status="IN_STOCK", product_id=str(product["id"])),
                    "ConditionExpression": "attribute_not_exists(pk)"}})
            else:
                asset = get_serial_asset(serial)
                if not asset or asset.get("status") != "IN_STOCK" or str(asset.get("product_id")) != str(product["id"]):
                    raise ValueError(f"Serial number {serial} is not in stock for {product['name']}.")
                sold = {**asset, "status": "SOLD", "sale_invoice_id": invoice_id}
                serial_writes.append({"Put": {"TableName": table().name,
                    "Item": _item(f"SERIAL#{normalized}", "DATA", "SERIAL_ASSET", sold,
                                  index_pk="SERIAL", index_sk=f"SOLD#{normalized}",
                                  status="SOLD", product_id=str(product["id"])),
                    "ConditionExpression": "#status = :in_stock AND product_id = :product_id",
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {":in_stock": "IN_STOCK", ":product_id": str(product["id"])} }})
        change = int(quantity) * (1 if direction == "PURCHASE" else -1)
        if direction == "PURCHASE":
            product_id = str(product["id"])
            purchase_quantities[product_id] = purchase_quantities.get(product_id, Decimal("0")) + quantity
            rate = Decimal(str(line.get("unit_rate", product.get("unit_cost", 0))))
            purchase_values[product_id] = purchase_values.get(product_id, Decimal("0")) + quantity * rate
            if not product.get("hsn_sac") and line.get("hsn_sac"):
                product["hsn_sac"] = line["hsn_sac"]
        if _normalized_identity(str(line.get("description") or "")) != _normalized_identity(str(product.get("name") or "")):
            aliases = list(product.get("aliases") or [])
            alias_key = _normalized_identity(str(line.get("description") or ""))
            if alias_key and alias_key not in {_normalized_identity(alias) for alias in aliases}:
                aliases.append(str(line.get("description") or "").strip())
                product["aliases"] = aliases
                identity = _normalized_product_identity(str(line.get("description") or ""))
                bucket = by_identity.setdefault(identity, [])
                if identity and all(str(existing["id"]) != str(product["id"]) for existing in bucket):
                    bucket.append(product)
        mapped.append((index, line, product, change))

    # Aggregate by product so each product row is updated only once in the transaction.
    changes: dict[str, int] = {}
    for _, _, product, change in mapped:
        product_id = str(product["id"])
        changes[product_id] = changes.get(product_id, 0) + change
    for product_id, change in changes.items():
        if int(by_id[product_id]["quantity_on_hand"]) + change < 0:
            raise ValueError(f"Sale quantity exceeds on-hand stock for {by_id[product_id]['name']}.")
        if change < 0:
            validate_dated_stock(by_id[product_id]['quantity_on_hand'], list_movements(product_id),
                change, record.get('invoice_date') or date.today().isoformat(), by_id[product_id]['name'])
        product = by_id[product_id]
        if direction == "PURCHASE" and purchase_quantities.get(product_id):
            old_quantity = Decimal(str(product.get("quantity_on_hand", 0)))
            new_quantity = old_quantity + purchase_quantities[product_id]
            old_value = old_quantity * Decimal(str(product.get("unit_cost", 0)))
            product["unit_cost"] = (old_value + purchase_values[product_id]) / new_quantity

    approved = {**record, "status": "APPROVED", "inventory_posted": True,
                "review_notes": record.get("review_notes") or "Approved and inventory posted."}
    now = _now()
    existing_item = table().get_item(Key={"pk": f"INVOICE#{invoice_id}", "sk": "DATA"}).get("Item")
    if not existing_item:
        raise ValueError("Invoice no longer exists.")
    # Older invoice rows predate the top-level status attribute. Copy their current
    # status once so the transaction can use a conditional approval gate.
    if "status" not in existing_item:
        table().update_item(
            Key={"pk": f"INVOICE#{invoice_id}", "sk": "DATA"},
            UpdateExpression="SET #status = :current",
            ConditionExpression="attribute_not_exists(#status)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":current": record.get("status", "NEEDS_REVIEW")},
        )
    elif existing_item["status"] != expected_status:
        raise ValueError("Invoice status changed; refresh the page and review again.")
    invoice_item = _item(f"INVOICE#{invoice_id}", "DATA", "INVOICE", approved,
                         index_pk="INVOICE", index_sk=f"{record.get('created_at', now)}#{invoice_id}",
                         status="APPROVED", inventory_posted=True,
                         amount_paid=Decimal(str(approved.get("amount_paid") or 0)),
                         payment_status=approved.get("payment_status", "UNKNOWN"))
    client = table().meta.client
    writes = [{"Put": {"TableName": table().name, "Item": invoice_item,
                        "ConditionExpression": "#status = :expected AND attribute_not_exists(inventory_posted)",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": {":expected": expected_status}}}]
    writes.extend(serial_writes)
    for product_id, change in changes.items():
        if product_id in new_products:
            product = {**new_products[product_id], "quantity_on_hand": change}
            product_item = _product_item(product)
            product_put = {"TableName": table().name, "Item": product_item}
            product_put.update(_condition_kwargs(Attr("pk").not_exists()))
            writes.append({"Put": product_put})
            continue
        product = {**by_id[product_id],
                   "quantity_on_hand": int(by_id[product_id]["quantity_on_hand"]) + change}
        writes.append({"Update": {
            "TableName": table().name,
            "Key": {"pk": f"PRODUCT#{product_id}", "sk": "DATA"},
            "UpdateExpression": "SET quantity_on_hand = :new_quantity, payload = :payload",
            "ConditionExpression": "attribute_exists(pk) AND quantity_on_hand = :current AND quantity_on_hand >= :minimum",
            "ExpressionAttributeValues": {":new_quantity": Decimal(product["quantity_on_hand"]),
                                          ":payload": json.dumps(product, default=_json_default, separators=(",", ":")),
                                          ":current": Decimal(by_id[product_id]["quantity_on_hand"]),
                                          ":minimum": Decimal(-change if change < 0 else 0)},
        }})
    for index, line, product, change in mapped:
        movement_id = f"INVOICE#{invoice_id}#LINE#{index}"
        movement = {"id": movement_id, "product_id": str(product["id"]),
                    "quantity_change": change,
                    "reason": f"{direction.title()} invoice {record.get('invoice_number') or invoice_id}",
                    "invoice_id": invoice_id, "created_at": now,
                    "effective_date": str(record.get('invoice_date') or now[:10]),
                    "movement_type": direction}
        writes.append({"Put": {"TableName": table().name,
                                "Item": _item(f"PRODUCT#{product['id']}", f"MOVEMENT#{movement_id}",
                                              "STOCK_MOVEMENT", movement),
                                "ConditionExpression": "attribute_not_exists(pk)"}})
    if len(writes) > 100:
        raise ValueError("This invoice has too many lines to post in one safe operation.")
    try:
        client.transact_write_items(TransactItems=writes)
    except client.exceptions.TransactionCanceledException as exc:
        raise ValueError("Invoice or stock changed while posting. Refresh the page and review again.") from exc
    return approved


def _product_item(product: dict) -> dict:
    sku = str(product["sku"])
    item = _item(f"PRODUCT#{sku}", "DATA", "PRODUCT", product,
                 index_pk="PRODUCT", index_sk=f"{product['name'].casefold()}#{sku.casefold()}",
                 sku_key=sku.casefold(), quantity_on_hand=int(product["quantity_on_hand"]))
    return item


def create_product(product: dict) -> dict:
    sku = str(product["sku"])
    product = {**product, "id": sku}
    item = _product_item(product)
    movement = None
    if product["quantity_on_hand"]:
        movement_data = {"id": uuid4().hex, "product_id": sku,
                         "quantity_change": int(product["quantity_on_hand"]),
                         "reason": "Opening stock", "created_at": _now(),
                         "effective_date": str(product.get('opening_stock_date') or date.today()),
                         "movement_type": "OPENING_STOCK"}
        movement = _item(f"PRODUCT#{sku}", f"MOVEMENT#{movement_data['created_at']}#{movement_data['id']}",
                         "STOCK_MOVEMENT", movement_data)
    product_put = {"TableName": table().name, "Item": item}
    product_put.update(_condition_kwargs(Attr("pk").not_exists()))
    writes = [{"Put": product_put}]
    if movement:
        writes.append({"Put": {"TableName": table().name, "Item": movement}})
    try:
        table().meta.client.transact_write_items(TransactItems=writes)
    except table().meta.client.exceptions.TransactionCanceledException:
        return None
    return product


def get_product(product_id: str) -> dict | None:
    item = table().get_item(Key={"pk": f"PRODUCT#{product_id}", "sk": "DATA"}).get("Item")
    if not item:
        return None
    record = _json_load(item["payload"])
    record["quantity_on_hand"] = int(item["quantity_on_hand"])
    return record


def update_product_pricing(product_id: str, pricing: dict) -> dict | None:
    product = get_product(product_id)
    if not product:
        return None
    updated = {**product, **pricing}
    try:
        table().update_item(
            Key={"pk": f"PRODUCT#{product_id}", "sk": "DATA"},
            UpdateExpression="SET payload = :payload",
            ConditionExpression=Attr("pk").exists() & Attr("quantity_on_hand").eq(int(product["quantity_on_hand"])),
            ExpressionAttributeValues={":payload": json.dumps(updated, default=_json_default, separators=(",", ":"))},
        )
    except table().meta.client.exceptions.ConditionalCheckFailedException as exc:
        raise ValueError("Product changed while saving pricing rules; refresh and retry.") from exc
    return updated


def update_product_details(product_id: str, details: dict) -> dict | None:
    product = get_product(product_id)
    if not product:
        return None
    allowed = {"product_type", "brand", "model", "hsn_sac", "aliases"}
    if not details or not set(details).issubset(allowed):
        raise ValueError("Only product type, brand, model, HSN/SAC, and learned aliases can be updated here.")
    normalized = {key: value.strip() if isinstance(value, str) else value
                  for key, value in details.items()}
    updated = {**product, **normalized}
    try:
        table().update_item(
            Key={"pk": f"PRODUCT#{product_id}", "sk": "DATA"},
            UpdateExpression="SET payload = :payload",
            ConditionExpression=Attr("pk").exists() & Attr("quantity_on_hand").eq(int(product["quantity_on_hand"])),
            ExpressionAttributeValues={":payload": json.dumps(updated, default=_json_default, separators=(",", ":"))},
        )
    except table().meta.client.exceptions.ConditionalCheckFailedException as exc:
        raise ValueError("Product changed while saving details; refresh and retry.") from exc
    return updated


def list_products() -> list[dict]:
    items = _query_all(
        IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("PRODUCT"),
        ScanIndexForward=True,
    )
    products = []
    for item in items:
        record = _json_load(item["payload"])
        record["quantity_on_hand"] = int(item["quantity_on_hand"])
        products.append(record)
    return products


def get_pricing_policy() -> dict:
    item = table().get_item(Key={"pk": "SETTINGS#PRICING", "sk": "DATA"}).get("Item")
    return _json_load(item["payload"]) if item else {
        "minimum_margin_percent": None, "target_margin_percent": None,
    }


def save_pricing_policy(policy: dict) -> dict:
    record = {**policy, "updated_at": _now()}
    table().put_item(Item=_item("SETTINGS#PRICING", "DATA", "APP_SETTING", record))
    return record


def add_stock_movement(product_id: str, change: int, reason: str, *, effective_date=None, movement_type="ADJUSTMENT") -> dict | None:
    product = get_product(product_id)
    if not product:
        return None
    effective_date = str(effective_date or date.today())
    validate_dated_stock(product['quantity_on_hand'], list_movements(product_id),
                         change, effective_date, product['name'])
    movement = {"id": uuid4().hex, "product_id": product_id,
                "quantity_change": int(change), "reason": reason, "created_at": _now(),
                "effective_date": effective_date, "movement_type": movement_type}
    movement_item = _item(f"PRODUCT#{product_id}",
                          f"MOVEMENT#{movement['created_at']}#{movement['id']}",
                          "STOCK_MOVEMENT", movement)
    delta = Decimal(int(change))
    condition = (Attr("quantity_on_hand").gte(Decimal(abs(int(change))))
                 if change < 0 else Attr("quantity_on_hand").exists())
    condition_params = _condition_kwargs(condition)
    names = condition_params.get("ExpressionAttributeNames", {})
    values = {**condition_params.get("ExpressionAttributeValues", {}), ":delta": delta}
    try:
        table().meta.client.transact_write_items(TransactItems=[
            {"Update": {
                "TableName": table().name,
                "Key": {"pk": f"PRODUCT#{product_id}", "sk": "DATA"},
                "UpdateExpression": "SET quantity_on_hand = quantity_on_hand + :delta",
                "ConditionExpression": condition_params["ConditionExpression"],
                "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": values,
            }},
            {"Put": {"TableName": table().name, "Item": movement_item}},
        ])
    except table().meta.client.exceptions.TransactionCanceledException:
        return None
    product["quantity_on_hand"] += int(change)
    return product


def list_movements(product_id: str) -> list[dict]:
    items = _query_all(
        ConsistentRead=True,
        KeyConditionExpression=Key("pk").eq(f"PRODUCT#{product_id}")
        & Key("sk").begins_with("MOVEMENT#"),
        ScanIndexForward=False,
    )
    return [_json_load(item["payload"]) for item in items]


def create_forecast(record: dict) -> dict:
    run_id = uuid4().hex
    now = _now()
    record = {**record, "id": run_id, "created_at": now}
    table().put_item(Item=_item(f"FORECAST#{run_id}", "DATA", "FORECAST", record,
                                index_pk="FORECAST", index_sk=f"{now}#{run_id}"),
                     ConditionExpression=Attr("pk").not_exists())
    return record


def get_forecast(run_id: str) -> dict | None:
    item = table().get_item(Key={"pk": f"FORECAST#{run_id}", "sk": "DATA"}).get("Item")
    return _json_load(item["payload"]) if item else None


def latest_forecast() -> dict | None:
    items = _query_all(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("FORECAST"),
                       ScanIndexForward=False, Limit=1)
    return _json_load(items[0]["payload"]) if items else None


def create_feedback(run_id: str, feedback: dict) -> dict | None:
    feedback = {**feedback, "id": uuid4().hex, "forecast_run_id": run_id,
                "created_at": _now()}
    table().put_item(Item=_item(f"FORECAST#{run_id}", f"FEEDBACK#{feedback['id']}",
                                "FORECAST_FEEDBACK", feedback))
    return feedback


def merge_textract(invoice: dict, extracted: dict, status: str) -> dict:
    if invoice.get('status') != 'NEEDS_REVIEW' or invoice.get('inventory_posted'):
        return invoice
    for field in ("vendor_name", "receiver_name", "vendor_gstin", "receiver_gstin", "invoice_number", "invoice_date",
                  "subtotal", "tax_total", "grand_total"):
        if invoice.get(field) is None and extracted.get(field) is not None:
            invoice[field] = extracted[field]
    if not invoice.get("lines") and extracted.get("lines"):
        invoice["lines"] = extracted["lines"]
    if invoice.get("transaction_type", "UNKNOWN") == "UNKNOWN" and extracted.get("transaction_type") in {"PURCHASE", "SALE"}:
        invoice["transaction_type"] = extracted["transaction_type"]
    if not invoice.get("direction_reason"):
        invoice["direction_reason"] = extracted.get("direction_reason")
    invoice["status"] = "NEEDS_REVIEW"
    invoice["extraction_status"] = status
    invoice["review_notes"] = "Textract extraction loaded; verify fields and line items before approval."
    return save_invoice(invoice, expected_status='NEEDS_REVIEW')
