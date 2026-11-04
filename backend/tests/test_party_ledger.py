import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from mangum import Mangum
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Invoice, Product, StockMovement
from app.repositories import dynamodb_store as store
from app.services.party_ledger import directory, ledger


class LocalApiClient:
    """Exercise the same API Gateway event adapter used by the cloud runtime."""
    def request(self, method, path, json=None):
        import json as codec
        response = Mangum(app, lifespan="off")({"version": "2.0", "routeKey": "ANY /api/{proxy+}",
            "rawPath": path, "rawQueryString": "", "headers": {"content-type": "application/json"},
            "requestContext": {"http": {"method": method, "path": path, "sourceIp": "127.0.0.1", "protocol": "HTTP/1.1"},
                               "stage": "$default", "requestId": "local-test"},
            "body": codec.dumps(json) if json is not None else None, "isBase64Encoded": False}, {})
        return SimpleNamespace(status_code=response["statusCode"], text=response["body"],
                               json=lambda: codec.loads(response["body"]))

    def get(self, path): return self.request("GET", path)
    def post(self, path, **kwargs): return self.request("POST", path, **kwargs)
    def put(self, path, **kwargs): return self.request("PUT", path, **kwargs)


class LedgerServiceTests(unittest.TestCase):
    def invoice(self, **values):
        return {"id": "one", "vendor_name": "ACME", "receiver_name": "Buyer", "status": "APPROVED",
                "transaction_type": "PURCHASE", "currency": "INR", "grand_total": "100.00",
                "amount_paid": "30.00", "payment_history": [], **values}

    def test_role_separation_unpaid_and_legacy_history_without_fabricated_date(self):
        invoices = [self.invoice(), self.invoice(id="sale", transaction_type="SALE", receiver_name="ACME"),
                    self.invoice(id="pending", status="NEEDS_REVIEW"),
                    self.invoice(id="missing-buyer", transaction_type="SALE", receiver_name=None)]
        contacts = directory([], invoices)
        self.assertEqual(len(contacts), 2)
        vendor = next(p for p in contacts if p["party_type"] == "VENDOR")
        result = ledger(vendor, invoices)
        self.assertEqual(result["balances"][0]["outstanding"], "70.00")
        self.assertEqual(len(result["invoices"]), 1)
        self.assertTrue(result["payments"][0]["legacy_balance"])
        self.assertIsNone(result["payments"][0]["payment_date"])

    def test_currencies_are_not_added_together_and_settled_history_survives(self):
        payment = {"amount": "100", "payment_date": "2026-10-10", "recorded_at": "2026-10-11T00:00:00Z", "method": "UPI", "reference": "ABC"}
        invoices = [self.invoice(amount_paid="100", payment_history=[payment]), self.invoice(id="usd", currency="USD")]
        contact = directory([], invoices)[0]
        result = ledger(contact, invoices)
        self.assertEqual([b["currency"] for b in result["balances"]], ["INR", "USD"])
        self.assertEqual(result["balances"][0]["outstanding"], "0.00")
        self.assertEqual(result["payments"][0]["payment_date"], "2026-10-10")
        self.assertEqual(len(result["invoices"]), 2)


class ContactAndCountApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = LocalApiClient()
        self.invoice = Invoice(filename="purchase.pdf", stored_path="test.pdf", content_type="application/pdf",
            vendor_name="ACME", transaction_type="PURCHASE", status="APPROVED", grand_total=Decimal("100"), amount_paid=0)
        self.product = Product(sku="TEST-1", name="Monitor", quantity_on_hand=6, reorder_point=0, unit_cost=10)
        self.db.add_all([self.invoice, self.product]); self.db.commit()

    def tearDown(self):
        app.dependency_overrides.clear(); self.db.close(); self.engine.dispose()

    def test_create_edit_alias_conflict_and_separate_customer(self):
        response = self.client.post("/api/v1/parties", json={"party_type": "VENDOR", "name": "ACME", "phone": "123"})
        self.assertEqual(response.status_code, 201, response.text)
        identifier = response.json()["id"]
        self.assertEqual(self.client.post("/api/v1/parties", json={"party_type": "VENDOR", "name": " acme "}).status_code, 409)
        self.assertEqual(self.client.post("/api/v1/parties", json={"party_type": "CUSTOMER", "name": "ACME"}).status_code, 201)
        update = self.client.put(f"/api/v1/parties/{identifier}", json={"party_type": "VENDOR", "name": "ACME Computers"})
        self.assertEqual(update.status_code, 200, update.text)
        self.assertIn("ACME", update.json()["aliases"])
        self.assertEqual(self.client.get(f"/api/v1/parties/{identifier}/ledger").json()["invoices"][0]["grand_total"], "100.00")
        self.assertEqual(self.client.post("/api/v1/parties", json={"party_type": "VENDOR", "name": "Other", "aliases": ["ACME"]}).status_code, 409)

    def test_partial_then_full_payment_is_visible_with_transaction_date(self):
        contact = self.client.get("/api/v1/parties").json()["parties"][0]
        url = f"/api/v1/invoices/{self.invoice.id}/payments"
        self.assertEqual(self.client.post(url, json={"amount": "40.00", "payment_date": "2026-10-01", "method": "Cash"}).status_code, 200)
        self.assertEqual(self.client.post(url, json={"amount": "61.00"}).status_code, 422)
        self.assertEqual(self.client.post(url, json={"amount": "60.00", "method": "UPI", "reference": "TEST"}).status_code, 200)
        result = self.client.get(f"/api/v1/parties/{contact['id']}/ledger").json()
        self.assertEqual(result["balances"][0]["outstanding"], "0.00")
        self.assertEqual(len(result["payments"]), 2)
        self.assertIn("2026-10-01", [p["payment_date"] for p in result["payments"]])
        self.assertEqual(len(result["invoices"]), 1)
        self.assertEqual(self.product.quantity_on_hand, 6)

    def test_physical_count_requires_matching_confirmed_current_quantity(self):
        url = f"/api/v1/inventory/{self.product.id}/physical-count"
        self.assertEqual(self.client.post(url, json={"counted_quantity": 6, "expected_quantity": 6, "confirmed": False}).status_code, 422)
        self.assertEqual(self.client.post(url, json={"counted_quantity": 5, "expected_quantity": 6, "confirmed": True}).status_code, 409)
        self.assertEqual(self.client.post(url, json={"counted_quantity": 5, "expected_quantity": 5, "confirmed": True}).status_code, 409)
        result = self.client.post(url, json={"counted_quantity": 6, "expected_quantity": 6, "confirmed": True})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["stock_count_verified"])
        self.assertEqual(result.json()["quantity_on_hand"], 6)
        movements = self.db.scalars(select(StockMovement)).all()
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].quantity_change, 0)
        self.assertEqual(movements[0].movement_type, "COUNT_VERIFICATION")


class CloudCountTests(unittest.TestCase):
    def test_count_write_is_guarded_and_does_not_change_quantity(self):
        record = {"id": "SKU", "sku": "SKU", "name": "Monitor", "quantity_on_hand": 6}
        payload = json.dumps(record)
        calls = []
        fake = SimpleNamespace(name="table", get_item=lambda **kw: {"Item": {"quantity_on_hand": 6, "payload": payload}},
            meta=SimpleNamespace(client=SimpleNamespace(transact_write_items=lambda **kw: calls.append(kw),
                exceptions=SimpleNamespace(TransactionCanceledException=RuntimeError))))
        with patch.object(store, "table", return_value=fake):
            result = store.confirm_stock_count("SKU", 6, 6)
            with self.assertRaisesRegex(ValueError, "Count differs"):
                store.confirm_stock_count("SKU", 5, 5)
        self.assertTrue(result["stock_count_verified"])
        update = calls[0]["TransactItems"][0]["Update"]
        self.assertIn("quantity_on_hand = :expected_quantity", update["ConditionExpression"])
        self.assertEqual(update["UpdateExpression"], "SET payload = :payload")
        movement = json.loads(calls[0]["TransactItems"][1]["Put"]["Item"]["payload"])
        self.assertEqual(movement["quantity_change"], 0)
