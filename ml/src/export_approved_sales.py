"""Export reviewed SALE invoices as a daily model-input CSV."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.db import SessionLocal  # noqa: E402
from app.models import Invoice, InvoiceLine  # noqa: E402


def normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def load_mapping(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"description", "product_id"}.issubset(reader.fieldnames):
            raise ValueError("Mapping CSV requires description,product_id columns")
        mapping: dict[str, str] = {}
        for row in reader:
            description = normalized(row.get("description") or "")
            product_id = (row.get("product_id") or "").strip()
            if not description or not product_id:
                continue
            if description in mapping and mapping[description] != product_id:
                raise ValueError(f"Description maps to multiple products: {row['description']}")
            mapping[description] = product_id
    if not mapping:
        raise ValueError("Mapping CSV has no reviewed product mappings")
    return mapping


def export_sales(mapping_path: Path, output_path: Path, coverage_start: date | None, coverage_end: date | None) -> int:
    mapping = load_mapping(mapping_path)
    daily: dict[tuple[str, date], Decimal] = defaultdict(Decimal)
    unmapped: set[str] = set()
    eligible_invoices = 0
    with SessionLocal() as db:
        invoices = db.query(Invoice).filter(
            Invoice.status == "APPROVED",
            Invoice.transaction_type == "SALE",
            Invoice.invoice_date.is_not(None),
        ).order_by(Invoice.invoice_date, Invoice.id).all()
        for invoice in invoices:
            eligible_invoices += 1
            for line in invoice.lines:
                key = normalized(line.description)
                product_id = mapping.get(key)
                if not product_id:
                    unmapped.add(line.description)
                    continue
                daily[(product_id, invoice.invoice_date)] += Decimal(line.quantity)
    if unmapped:
        names = ", ".join(sorted(unmapped))
        raise ValueError(f"Add human-reviewed mappings for these approved sales line descriptions: {names}")
    if not daily:
        raise ValueError("No approved, dated SALE invoice line items were found in the database")

    by_product: dict[str, dict[date, Decimal]] = defaultdict(dict)
    for (product_id, day), quantity in daily.items():
        by_product[product_id][day] = quantity

    if (coverage_start is None) != (coverage_end is None):
        raise ValueError("Provide both coverage start and end, or neither")
    rows: list[dict] = []
    for product_id, observed in sorted(by_product.items()):
        start = coverage_start or min(observed)
        end = coverage_end or max(observed)
        if start > end or min(observed) < start or max(observed) > end:
            raise ValueError(f"Coverage range does not contain all observed sales for {product_id}")
        day = start
        while day <= end:
            if day in observed:
                quantity = observed[day]
            elif coverage_start is not None:
                # Only an explicitly verified complete-coverage range may assert zero sales.
                quantity = Decimal("0")
            else:
                day += timedelta(days=1)
                continue
            rows.append({"product_id": product_id, "date": day.isoformat(), "quantity_sold": str(quantity)})
            day += timedelta(days=1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["product_id", "date", "quantity_sold"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Exported {len(rows)} daily rows from {eligible_invoices} approved SALE invoices to {output_path}")
    if coverage_start is None:
        print("No zero-sale days were inferred; the trainer will reject date gaps until a complete source-coverage range is confirmed.")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("../data/processed/sales_daily.csv"))
    parser.add_argument("--coverage-start", type=date.fromisoformat)
    parser.add_argument("--coverage-end", type=date.fromisoformat)
    args = parser.parse_args()
    try:
        export_sales(args.mapping_csv, args.output, args.coverage_start, args.coverage_end)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
