"""Keep synthetic invoice history out of actual-business model training."""


def approved_analysis_invoices(invoices):
    def value(invoice, key):
        return invoice.get(key) if isinstance(invoice, dict) else getattr(invoice, key, None)

    approved = [invoice for invoice in invoices if value(invoice, 'status') == 'APPROVED']
    business = [invoice for invoice in approved if not str(value(invoice, 'source_type') or '').startswith('DEMO')]
    demo = [invoice for invoice in approved if str(value(invoice, 'source_type') or '').startswith('DEMO')]
    if business:
        return business, False, len(demo)
    return demo, bool(demo), 0
