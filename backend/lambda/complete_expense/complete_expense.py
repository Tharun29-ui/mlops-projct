"""Collect Textract completion messages, save raw output, and normalize fields."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from hashlib import sha256

import boto3


textract = boto3.client("textract")
s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["JOB_TABLE_NAME"])
bucket_name = os.environ["INVOICE_BUCKET_NAME"]
notification_topic_arn = os.environ["TEXTRACT_TOPIC_ARN"]
textract_role_arn = os.environ["TEXTRACT_ROLE_ARN"]


def _unpack_notification(body: str) -> dict:
    envelope = json.loads(body)
    if envelope.get("Type") == "Notification":
        return json.loads(envelope["Message"])
    return envelope


def _get_all_pages(job_id: str) -> list[dict]:
    pages = []
    next_token = None
    while True:
        request = {"JobId": job_id}
        if next_token:
            request["NextToken"] = next_token
        response = textract.get_expense_analysis(**request)
        response.pop("ResponseMetadata", None)
        next_token = response.pop("NextToken", None)
        pages.append(response)
        if not next_token:
            return pages


def _get_all_table_pages(job_id: str) -> list[dict]:
    pages = []
    next_token = None
    while True:
        request = {"JobId": job_id}
        if next_token:
            request["NextToken"] = next_token
        response = textract.get_document_analysis(**request)
        response.pop("ResponseMetadata", None)
        next_token = response.pop("NextToken", None)
        pages.append(response)
        if not next_token:
            return pages


def _field_value(field: dict) -> dict:
    value = field.get("ValueDetection", {})
    kind = field.get("Type", {})
    label = field.get("LabelDetection", {})
    return {
        "type": kind.get("Text"),
        "value": value.get("Text"),
        "confidence": value.get("Confidence"),
        "page": value.get("PageNumber"),
        "label": label.get("Text"),
        "label_confidence": label.get("Confidence"),
    }


def _normalize(pages: list[dict]) -> dict:
    documents = []
    for page in pages:
        for expense in page.get("ExpenseDocuments", []):
            line_items = []
            for group in expense.get("LineItemGroups", []):
                for item in group.get("LineItems", []):
                    line_items.append(
                        [_field_value(field) for field in item.get("LineItemExpenseFields", [])]
                    )
            documents.append(
                {
                    "summary_fields": [
                        _field_value(field) for field in expense.get("SummaryFields", [])
                    ],
                    "line_items": line_items,
                }
            )
    return {
        "source": "amazon_textract_analyze_expense",
        "review_status": "NEEDS_REVIEW",
        "expense_documents": documents,
    }


def _needs_table_fallback(normalized: dict) -> bool:
    """Use table OCR when expense OCR misses a date, item row, or stock-critical field."""
    documents = normalized.get("expense_documents") or []
    if not any(
        field.get("type") in {"INVOICE_RECEIPT_DATE", "INVOICE_DATE"} and field.get("value")
        for document in documents
        for field in document.get("summary_fields") or []
    ):
        return True
    saw_item = False
    for document in documents:
        for fields in document.get("line_items") or []:
            by_type = {
                field.get("type") for field in fields if field.get("value")
            }
            if not by_type.intersection({"ITEM", "ITEM_DESCRIPTION", "DESCRIPTION"}):
                continue
            saw_item = True
            if not by_type.intersection({"PRODUCT_CODE", "HSN", "HSN_SAC"}):
                return True
            if "QUANTITY" not in by_type:
                return True
            if not by_type.intersection({"PRICE", "AMOUNT", "LINE_TOTAL", "UNIT_PRICE"}):
                return True
    return not saw_item


def _cell_text(cell: dict, blocks_by_id: dict[str, dict]) -> str:
    parts = []
    for relationship in cell.get("Relationships", []):
        if relationship.get("Type") != "CHILD":
            continue
        for child_id in relationship.get("Ids", []):
            child = blocks_by_id.get(child_id, {})
            if child.get("BlockType") in {"WORD", "SELECTION_ELEMENT"} and child.get("Text"):
                parts.append(child["Text"])
    return " ".join(parts).strip()


def _header_role(text: str) -> str | None:
    label = re.sub(r"[^a-z0-9]+", "", text.casefold())
    if any(token in label for token in ("hsn", "sac", "productcode")):
        return "hsn"
    if any(token in label for token in ("description", "particular", "item", "goods", "product", "service")):
        return "description"
    if "quantity" in label or label == "qty":
        return "quantity"
    if "amount" in label or "linevalue" in label or "linetotal" in label:
        return "amount"
    if "rate" in label or "unitprice" in label or "price" in label:
        return "rate"
    if label == "unit" or label == "per":
        return "unit"
    return None


def _table_line_items(pages: list[dict]) -> list[list[dict]]:
    """Convert recognized invoice table rows into the same fields used by AnalyzeExpense."""
    blocks = [block for page in pages for block in page.get("Blocks", [])]
    blocks_by_id = {block.get("Id"): block for block in blocks if block.get("Id")}
    normalized_tables = []
    for table in (block for block in blocks if block.get("BlockType") == "TABLE"):
        cell_ids = [child_id for relation in table.get("Relationships", [])
                    if relation.get("Type") == "CHILD" for child_id in relation.get("Ids", [])]
        rows: dict[int, dict[int, dict]] = {}
        for cell_id in cell_ids:
            cell = blocks_by_id.get(cell_id)
            if not cell or cell.get("BlockType") != "CELL":
                continue
            row_index = int(cell.get("RowIndex", 0))
            column_index = int(cell.get("ColumnIndex", 0))
            if row_index and column_index:
                rows.setdefault(row_index, {})[column_index] = cell
        best_header = None
        for row_index, cells in rows.items():
            columns: dict[str, int] = {}
            for column_index, cell in cells.items():
                role = _header_role(_cell_text(cell, blocks_by_id))
                if role and role not in columns:
                    columns[role] = column_index
            if "description" in columns and "quantity" in columns and ("amount" in columns or "rate" in columns):
                score = len(columns) + (2 if "amount" in columns else 0) + (1 if "hsn" in columns else 0)
                if best_header is None or score > best_header[0]:
                    best_header = (score, row_index, columns)
        if best_header:
            normalized_tables.append((best_header[1], best_header[2], rows))

    line_items: list[list[dict]] = []
    for header_row, columns, rows in normalized_tables:
        for row_index in sorted(index for index in rows if index > header_row):
            cells = rows[row_index]
            values = {role: _cell_text(cells.get(column, {}), blocks_by_id)
                      for role, column in columns.items()}
            description = re.sub(r"\s+", " ", values.get("description", "")).strip()
            if not description or not re.search(r"[A-Za-z]", description):
                continue
            if re.search(r"\b(total|subtotal|cgst|sgst|igst|tax|amount in words)\b", description, re.I):
                continue
            quantity_text = values.get("quantity", "").strip()
            quantity_match = re.search(r"[-+]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)", quantity_text)
            quantity = quantity_match.group(0).replace(",", "") if quantity_match else "0"
            unit = values.get("unit", "").strip()
            if not unit:
                unit_match = re.search(r"\b(?:\d[\d,.]*\s*)([A-Za-z]{1,12})\s*$", quantity_text)
                if unit_match:
                    unit = unit_match.group(1)
            confidence_values = [
                float(cells[column].get("Confidence"))
                for column in columns.values()
                if cells.get(column, {}).get("Confidence") is not None
            ]
            confidence = (sum(confidence_values) / len(confidence_values)
                          if confidence_values else None)

            def field(kind: str, value: str | None) -> dict:
                return {"type": kind, "value": value or None, "confidence": confidence}

            line_items.append([
                field("ITEM", description),
                field("PRODUCT_CODE", values.get("hsn", "").strip()),
                field("QUANTITY", quantity),
                field("UNIT", unit),
                field("UNIT_PRICE", values.get("rate", "").strip()),
                field("PRICE", values.get("amount", "").strip()),
            ])
    return line_items


def _table_invoice_date(pages: list[dict]) -> dict | None:
    """Recover an explicitly labeled invoice date when expense analysis missed it."""
    date_pattern = re.compile(
        r"(?<!\d)(\d{1,2}[\-/ ](?:[A-Za-z]{3,9}|\d{1,2})[\-/ ]\d{2,4})(?!\d)"
    )
    date_formats = (
        "%d-%b-%y", "%d-%b-%Y", "%d %b %y", "%d %b %Y",
        "%d-%B-%y", "%d-%B-%Y", "%d %B %y", "%d %B %Y",
        "%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y",
    )
    for page in pages:
        for block in page.get("Blocks", []):
            text = block.get("Text", "")
            if block.get("BlockType") != "LINE" or not re.search(
                r"\b(invoice\s+date|date|dated)\b", text, re.I
            ):
                continue
            for candidate in date_pattern.findall(text):
                for date_format in date_formats:
                    try:
                        value = datetime.strptime(candidate, date_format).date().isoformat()
                    except ValueError:
                        continue
                    return {
                        "type": "INVOICE_RECEIPT_DATE",
                        "value": value,
                        "confidence": block.get("Confidence"),
                        "page": block.get("Page"),
                    }
    return None


def _merge_table_lines(expense_lines: list[list[dict]],
                       table_lines: list[list[dict]]) -> list[list[dict]]:
    """Fill missing expense fields from corresponding table rows without overwriting OCR values."""
    if not expense_lines or not any(
        any(field.get("type") in {"ITEM", "ITEM_DESCRIPTION", "DESCRIPTION"}
            and field.get("value") for field in row)
        for row in expense_lines
    ):
        return table_lines
    def description_key(row: list[dict]) -> str:
        value = next((field.get("value") for field in row
                      if field.get("type") in {"ITEM", "ITEM_DESCRIPTION", "DESCRIPTION"}
                      and field.get("value")), "")
        return re.sub(r"[^a-z0-9]", "", str(value).casefold())

    merged = []
    for expense_row in expense_lines:
        key = description_key(expense_row)
        matches = [row for row in table_lines if key and description_key(row) == key]
        if len(matches) != 1 or sum(description_key(row) == key for row in expense_lines) != 1:
            merged.append(expense_row)
            continue
        present = {
            field.get("type") for field in expense_row if field.get("value")
        }
        merged.append(expense_row + [
            field for field in matches[0]
            if field.get("value") and field.get("type") not in present
        ])
    return merged


def _save_json(key: str, payload: dict) -> None:
    s3.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )


def _start_table_fallback(token: str, bucket: str, key: str,
                          normalized_key: str, raw_key: str,
                          pages: list[dict]) -> bool:
    """Run table recognition when expense OCR missed rows or key line-item fields."""
    existing = table.get_item(Key={"idempotency_key": token}, ConsistentRead=True).get("Item") or {}
    if existing.get("table_job_id"):
        return True
    table_token = sha256(f"{token}:tables".encode("utf-8")).hexdigest()
    try:
        result = textract.start_document_analysis(
            DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
            FeatureTypes=["TABLES"],
            ClientRequestToken=table_token,
            JobTag=token,
            NotificationChannel={"SNSTopicArn": notification_topic_arn,
                                 "RoleArn": textract_role_arn},
        )
        table.update_item(
            Key={"idempotency_key": token},
            UpdateExpression=("SET #status = :status, normalized_result_key = :normalized, "
                              "raw_result_key = :raw, table_job_id = :table_job, "
                              "table_started_at = :started"),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "TABLES_IN_PROGRESS", ":normalized": normalized_key,
                ":raw": raw_key, ":table_job": result["JobId"],
                ":started": datetime.now(timezone.utc).isoformat(),
            },
        )
        return True
    except Exception as error:  # Preserve the first-pass extraction if fallback cannot start.
        error_code = getattr(error, "response", {}).get("Error", {}).get("Code", type(error).__name__)
        table.update_item(
            Key={"idempotency_key": token},
            UpdateExpression="SET #status = :status, normalized_result_key = :normalized, raw_result_key = :raw, table_error = :error",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "PARTIAL_SUCCESS", ":normalized": normalized_key,
                ":raw": raw_key, ":error": str(error_code)[:120],
            },
        )
        return False


def handler(event: dict, _context: object) -> dict[str, int]:
    processed = 0
    for record in event.get("Records", []):
        notification = _unpack_notification(record["body"])
        api_name = notification.get("API")
        job_id = notification["JobId"]
        token = notification.get("JobTag")
        status = notification.get("Status", "UNKNOWN")
        document_location = notification.get("DocumentLocation", {})
        source_bucket = document_location.get("S3Bucket", "")
        source_key = document_location.get("S3ObjectName", "")
        if not token:
            raise ValueError("Textract completion notification has no JobTag")

        if api_name == "StartExpenseAnalysis" and status in {"SUCCEEDED", "PARTIAL_SUCCESS"}:
            pages = _get_all_pages(job_id)
            raw_key = f"processed/textract/{token}.json"
            normalized_key = f"processed/normalized/{token}.json"
            raw_result = {"job_id": job_id, "pages": pages}
            normalized = _normalize(pages)
            _save_json(raw_key, raw_result)
            _save_json(normalized_key, normalized)
            if _needs_table_fallback(normalized) and source_bucket and source_key:
                _start_table_fallback(token, source_bucket, source_key,
                                      normalized_key, raw_key, pages)
            else:
                table.update_item(
                    Key={"idempotency_key": token},
                    UpdateExpression=(
                        "SET job_id = :job_id, #bucket = :bucket, object_key = :object_key, "
                        "#status = :status, raw_result_key = :raw, "
                        "normalized_result_key = :normalized, completed_at = :completed"
                    ),
                    ExpressionAttributeNames={"#bucket": "bucket", "#status": "status"},
                    ExpressionAttributeValues={
                        ":job_id": job_id, ":bucket": source_bucket,
                        ":object_key": source_key, ":status": "NEEDS_REVIEW",
                        ":raw": raw_key, ":normalized": normalized_key,
                        ":completed": datetime.now(timezone.utc).isoformat(),
                    },
                )
        elif api_name == "StartDocumentAnalysis" and status in {"SUCCEEDED", "PARTIAL_SUCCESS"}:
            table_pages = _get_all_table_pages(job_id)
            table_lines = _table_line_items(table_pages)
            job = table.get_item(Key={"idempotency_key": token}, ConsistentRead=True).get("Item") or {}
            normalized_key = job.get("normalized_result_key") or f"processed/normalized/{token}.json"
            raw_key = job.get("raw_result_key") or f"processed/textract/{token}.json"
            normalized_body = s3.get_object(Bucket=bucket_name, Key=normalized_key)["Body"].read()
            normalized = json.loads(normalized_body)
            documents = normalized.setdefault("expense_documents", [])
            if not documents:
                documents.append({"summary_fields": [], "line_items": []})
            if table_lines:
                documents[0]["line_items"] = _merge_table_lines(
                    documents[0].get("line_items") or [], table_lines
                )
                normalized["source"] = "amazon_textract_analyze_expense+tables"
            if documents:
                summary_fields = documents[0].setdefault("summary_fields", [])
                has_date = any(
                    field.get("type") in {"INVOICE_RECEIPT_DATE", "INVOICE_DATE"}
                    and field.get("value")
                    for field in summary_fields
                )
                if not has_date:
                    date_field = _table_invoice_date(table_pages)
                    if date_field:
                        summary_fields.append(date_field)
            raw_body = s3.get_object(Bucket=bucket_name, Key=raw_key)["Body"].read()
            raw_result = json.loads(raw_body)
            raw_result["table_job_id"] = job_id
            raw_result["table_pages"] = table_pages
            _save_json(raw_key, raw_result)
            _save_json(normalized_key, normalized)
            table.update_item(
                Key={"idempotency_key": token},
                UpdateExpression=(
                    "SET #status = :status, normalized_result_key = :normalized, "
                    "raw_result_key = :raw, table_completed_at = :completed, "
                    "table_line_count = :line_count"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":status": "NEEDS_REVIEW" if table_lines else "PARTIAL_SUCCESS",
                    ":raw": raw_key, ":normalized": normalized_key,
                    ":completed": datetime.now(timezone.utc).isoformat(),
                    ":line_count": len(table_lines),
                },
            )
        elif api_name == "StartExpenseAnalysis":
            table.update_item(
                Key={"idempotency_key": token},
                UpdateExpression=(
                    "SET job_id = :job_id, #bucket = :bucket, object_key = :object_key, "
                    "#status = :status, completed_at = :completed"
                ),
                ExpressionAttributeNames={"#bucket": "bucket", "#status": "status"},
                ExpressionAttributeValues={
                    ":job_id": job_id, ":bucket": source_bucket,
                    ":object_key": source_key, ":status": status,
                    ":completed": datetime.now(timezone.utc).isoformat(),
                },
            )
        elif api_name == "StartDocumentAnalysis":
            job = table.get_item(Key={"idempotency_key": token}, ConsistentRead=True).get("Item") or {}
            table.update_item(
                Key={"idempotency_key": token},
                UpdateExpression="SET #status = :status, table_error = :error, table_completed_at = :completed",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":status": "PARTIAL_SUCCESS" if job.get("normalized_result_key") else status,
                    ":error": f"Table analysis {status}"[:120],
                    ":completed": datetime.now(timezone.utc).isoformat(),
                },
            )
        else:
            continue

        # Log only opaque job identifiers and state; invoice fields stay out of logs.
        print(json.dumps({"job_tag": token, "status": status}))
        processed += 1

    return {"processed_notifications": processed}
