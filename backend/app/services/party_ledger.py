"""Counterparty identity and currency-separated, invoice-backed payment history."""
from decimal import Decimal
from hashlib import sha256


def name_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def party_id(party_type: str, name: str) -> str:
    return sha256(f"{party_type}:{name_key(name)}".encode()).hexdigest()[:24]


def counterparty(invoice: dict):
    direction = invoice.get("transaction_type")
    field = "vendor_name" if direction == "PURCHASE" else "receiver_name" if direction == "SALE" else None
    name = str(invoice.get(field) or "").strip() if field else ""
    # A sale's supplier is our own business, not an unidentified customer.
    return ("VENDOR" if direction == "PURCHASE" else "CUSTOMER", name) if name else None


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def ledger(contact: dict, invoices: list[dict]) -> dict:
    identities = {name_key(n) for n in [contact["name"], *contact.get("aliases", [])]}
    rows, history, totals = [], [], {}
    for invoice in invoices:
        party = counterparty(invoice)
        if invoice.get("status") != "APPROVED" or not party:
            continue
        if party[0] != contact["party_type"] or name_key(party[1]) not in identities:
            continue
        currency = str(invoice.get("currency") or "INR").upper()
        total = money(invoice["grand_total"]) if invoice.get("grand_total") is not None else None
        paid = money(invoice.get("amount_paid"))
        outstanding = max(Decimal("0.00"), total-paid) if total is not None else None
        bucket = totals.setdefault(currency, {"invoiced": Decimal(0), "paid": Decimal(0), "outstanding": Decimal(0)})
        if total is not None:
            bucket["invoiced"] += total
            bucket["paid"] += paid
            bucket["outstanding"] += outstanding
        row = {k: invoice.get(k) for k in ["id", "filename", "invoice_number", "invoice_date", "transaction_type", "payment_status"]}
        row.update(currency=currency, grand_total=str(total) if total is not None else None,
                   amount_paid=str(paid), outstanding=str(outstanding) if outstanding is not None else None)
        rows.append(row)
        recorded = Decimal(0)
        for index, payment in enumerate(invoice.get("payment_history") or []):
            amount = money(payment.get("amount"))
            recorded += amount
            stamp = payment.get("received_at") or payment.get("recorded_at")
            history.append({"id": f"{invoice['id']}:{index}", "invoice_id": invoice["id"],
                "invoice_number": invoice.get("invoice_number") or invoice.get("filename"),
                "currency": currency, "amount": str(amount), "method": payment.get("method"),
                "reference": payment.get("reference"), "recorded_at": stamp,
                "payment_date": payment.get("payment_date") or (str(stamp)[:10] if stamp else None),
                "direction": "PAID_TO_VENDOR" if contact["party_type"] == "VENDOR" else "RECEIVED_FROM_CUSTOMER",
                "legacy_balance": False})
        if paid > recorded:
            history.append({"id": f"{invoice['id']}:earlier", "invoice_id": invoice["id"],
                "invoice_number": invoice.get("invoice_number") or invoice.get("filename"),
                "currency": currency, "amount": str(paid-recorded), "method": None, "reference": None,
                "payment_date": None, "recorded_at": None,
                "direction": "PAID_TO_VENDOR" if contact["party_type"] == "VENDOR" else "RECEIVED_FROM_CUSTOMER",
                "legacy_balance": True})
    rows.sort(key=lambda row: (str(row.get("invoice_date") or ""), str(row["id"])), reverse=True)
    history.sort(key=lambda row: (str(row.get("payment_date") or ""), str(row.get("recorded_at") or "")), reverse=True)
    return {"party": contact, "invoices": rows, "payments": history,
            "balances": [{"currency": currency, **{k: f"{v:.2f}" for k, v in bucket.items()}}
                         for currency, bucket in sorted(totals.items())]}


def directory(contacts: list[dict], invoices: list[dict]) -> list[dict]:
    owners = {(c["party_type"], name_key(n)): c["id"] for c in contacts
              for n in [c["name"], *c.get("aliases", [])]}
    result = {c["id"]: {**c, "saved": True} for c in contacts}
    for invoice in invoices:
        party = counterparty(invoice)
        if invoice.get("status") != "APPROVED" or not party:
            continue
        identity = (party[0], name_key(party[1]))
        if identity not in owners:
            identifier = party_id(*party)
            owners[identity] = identifier
            result.setdefault(identifier, {"id": identifier, "party_type": party[0], "name": party[1],
                "aliases": [], "phone": None, "email": None, "address": None, "notes": None, "saved": False})
    summaries = []
    for contact in sorted(result.values(), key=lambda c: (c["party_type"], name_key(c["name"]))):
        detail = ledger(contact, invoices)
        summaries.append({**contact, "balances": detail["balances"], "invoice_count": len(detail["invoices"])})
    return summaries
