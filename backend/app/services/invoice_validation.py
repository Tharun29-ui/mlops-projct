from decimal import Decimal
import re

from app.schemas.invoices import InvoiceValidationInput


RUPEE_TOLERANCE = Decimal("1.00")
GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def validate_invoice(invoice: InvoiceValidationInput) -> list[str]:
    """Return review warnings; do not reject ambiguous GST invoice arithmetic."""
    warnings: list[str] = []

    if not invoice.vendor_name:
        warnings.append("Vendor name is missing")
    if not invoice.invoice_number:
        warnings.append("Invoice number is missing")
    if not invoice.invoice_date:
        warnings.append("Invoice date is missing")
    if invoice.vendor_gstin and not GSTIN_PATTERN.fullmatch(invoice.vendor_gstin.strip().upper()):
        warnings.append("Supplier GSTIN format is invalid; verify it against the source invoice")
    if invoice.receiver_gstin and not GSTIN_PATTERN.fullmatch(invoice.receiver_gstin.strip().upper()):
        warnings.append("Customer GSTIN format is invalid; verify it against the source invoice")
    if invoice.grand_total is None:
        warnings.append("Grand total is missing")
    if not invoice.lines:
        warnings.append("No line items were extracted")

    for index, line in enumerate(invoice.lines, start=1):
        if line.unit_rate is not None:
            expected_amount = line.quantity * line.unit_rate
            if abs(expected_amount - line.line_amount) > RUPEE_TOLERANCE:
                warnings.append(
                    f"Line {index}: quantity × unit rate differs from line amount; verify discounts and tax-inclusive pricing"
                )

    if invoice.subtotal is not None and invoice.lines:
        line_sum = sum((line.line_amount for line in invoice.lines), start=Decimal("0"))
        if abs(line_sum - invoice.subtotal) > RUPEE_TOLERANCE:
            warnings.append("Line amounts do not reconcile with subtotal; verify discounts, charges, and rounding")

    if invoice.subtotal is not None and invoice.tax_total is not None and invoice.grand_total is not None:
        if abs(invoice.subtotal + invoice.tax_total - invoice.grand_total) > RUPEE_TOLERANCE:
            warnings.append("Subtotal plus tax does not reconcile with grand total; verify round-off and other charges")

    return warnings
