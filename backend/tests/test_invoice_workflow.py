import os
import tempfile
import unittest
import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import patch
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

_TEST_DIRECTORY = tempfile.TemporaryDirectory(
    prefix="abos-test-", dir=Path(__file__).resolve().parents[1])
_TEST_ROOT = Path(_TEST_DIRECTORY.name)
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_ROOT / 'workflow.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_TEST_ROOT / "uploads")
os.environ["BUSINESS_DATA_TABLE_NAME"] = ""
os.environ["DATABASE_SECRET_ARN"] = ""
os.environ["INVOICE_BUCKET_NAME"] = ""
os.environ["SAGEMAKER_ENDPOINT_NAME"] = ""
os.environ["AWS_EXTRACTION_ENABLED"] = "false"
os.environ["BUSINESS_NAME"] = "Test Retailer"
os.environ["BUSINESS_GSTIN"] = "33ABCDE1234F1Z5"
os.environ["BUSINESS_ADDRESS"] = "1 Test Road, Chennai"

from fastapi import HTTPException
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.api.routes.forecasts import ApprovedSalesForecastInput, _linear_trend, _read_series, forecast_from_approved_sales, create_forecast
from app.api.routes.invoices import create_sales_invoice, post_approved_invoice_inventory, record_invoice_payment, review_invoice, validate_invoice_payload
from app.api.routes.inventory import ProductDetailsInput, PricingRulesInput, update_product_details, update_pricing_rules
from app.db import Base, SessionLocal, engine
from app.models import Invoice, InvoiceLine, Product, SerialAsset, StockMovement
from app.main import app
from app.schemas.invoices import InvoiceValidationInput
from app.schemas.sales import PaymentInput, SalesInvoiceInput
from app.services.textract_results import _normalized_payload, _line_from_fields


class InvoiceDirectionInferenceTests(unittest.TestCase):
    def extracted(self, vendor: str | None, invoice_number: str | None) -> dict:
        fields = []
        if vendor:
            fields.append({"type": "VENDOR_NAME", "value": vendor, "confidence": 99})
        if invoice_number:
            fields.append({"type": "INVOICE_RECEIPT_ID", "value": invoice_number, "confidence": 99})
        return _normalized_payload({"expense_documents": [{"summary_fields": fields, "line_items": []}]})

    def test_configured_example_p05_invoice_is_sale(self):
        result = self.extracted("Example Supplier", "P-05")
        self.assertEqual(result["transaction_type"], "SALE")
        self.assertIn("Suggested Sale", result["direction_reason"])

    def test_other_supplier_requires_explicit_direction(self):
        result = self.extracted("Other Supplier", "INV-22")
        self.assertEqual(result["transaction_type"], "UNKNOWN")
        self.assertIn("Choose Purchase or Sale", result["direction_reason"])

    def test_same_supplier_different_invoice_requires_explicit_direction(self):
        result = self.extracted("Example Supplier", "P-06")
        self.assertEqual(result["transaction_type"], "UNKNOWN")

    def test_exception_vendor_without_invoice_number_stays_for_manual_review(self):
        result = self.extracted("Example Supplier", None)
        self.assertEqual(result["transaction_type"], "UNKNOWN")
        self.assertIn("invoice number was not extracted", result["direction_reason"])

    def test_missing_vendor_stays_for_manual_review(self):
        result = self.extracted(None, "P-05")
        self.assertEqual(result["transaction_type"], "UNKNOWN")


class InvoiceWorkflowTests(unittest.TestCase):
    def test_backdated_sale_blocks_stock_bought_later(self):
        product=Product(sku='DATED-MONITOR',name='Monitor',quantity_on_hand=5,reorder_point=0,unit_cost=Decimal('10'))
        self.db.add(product);self.db.flush()
        self.db.add(StockMovement(product_id=product.id,quantity_change=5,reason='Purchase',
            effective_date=date(2026,1,5),movement_type='PURCHASE'))
        self.db.commit()
        invoice=self.make_invoice(direction='SALE',quantity='1',product_id=product.id)
        invoice.invoice_date=date(2026,1,4);self.db.commit()
        with self.assertRaises(HTTPException) as raised:
            review_invoice(invoice.id,{'action':'approve'},self.db)
        self.assertIn('later purchase',str(raised.exception.detail))
        self.db.refresh(product);self.assertEqual(product.quantity_on_hand,5)

    def test_confirmed_dated_opening_stock_does_not_create_a_purchase_invoice(self):
        from app.api.routes.inventory import MovementInput, add_movement
        product=Product(sku='OPENING-MONITOR',name='Monitor',quantity_on_hand=0,reorder_point=0,unit_cost=Decimal('10'))
        self.db.add(product);self.db.commit()
        with self.assertRaises(HTTPException):
            add_movement(str(product.id),MovementInput(quantity_change=2,reason='Counted stock',
                movement_type='OPENING_STOCK',effective_date=date(2026,1,3)),self.db)
        add_movement(str(product.id),MovementInput(quantity_change=2,reason='Counted stock',
            movement_type='OPENING_STOCK',effective_date=date(2026,1,3),confirmed_opening_balance=True),self.db)
        self.assertEqual(self.db.query(Invoice).count(),0)
        invoice=self.make_invoice(direction='SALE',quantity='1',product_id=product.id)
        invoice.invoice_date=date(2026,1,4);self.db.commit()
        result=review_invoice(invoice.id,{'action':'approve'},self.db)
        self.assertEqual(result['status'],'APPROVED')
        self.db.refresh(product);self.assertEqual(product.quantity_on_hand,1)

    def test_approved_sales_use_validated_model_and_requested_horizon(self):
        product=Product(sku='MODEL-VALIDATED',name='Monitor',quantity_on_hand=4,reorder_point=0,unit_cost=Decimal('40'))
        self.db.add(product);self.db.commit()
        for index in range(5):
            invoice=self.make_invoice(direction='SALE',quantity='1')
            invoice.invoice_number=f'MODEL-{index}'
            invoice.invoice_date=date(2026,10,1)+timedelta(days=index)
            invoice.status='APPROVED'
            invoice.lines[0].product_id=product.id
        self.db.commit()
        result=asyncio.run(forecast_from_approved_sales(ApprovedSalesForecastInput(
            confirm_complete_sales_history=True,horizon_days=30),self.db))
        self.assertEqual(result['model_name'],'validated_demand_model')
        self.assertEqual(result['metrics']['data_source'],'approved_complete_sales_history')
        self.assertEqual(result['metrics']['horizon_days'],30)
        self.assertEqual(len(result['forecasts']),30)
        self.assertTrue(result['metrics']['forecast_warnings'])
        self.db.refresh(product)
        self.assertEqual(product.quantity_on_hand,4)

    def test_receipt_without_tax_setup_posts_stock_and_renders_pdf(self):
        from app.core.config import get_settings
        from app.api.routes.invoices import download_sales_receipt
        product = Product(sku="RECEIPT-MON", name="Monitor", quantity_on_hand=3,
                          reorder_point=0, unit_cost=Decimal("40"))
        self.db.add(product)
        self.db.commit()
        payload = SalesInvoiceInput.model_validate({
            "document_type": "NON_GST_RECEIPT", "invoice_number": "RECEIPT-1",
            "invoice_date": "2026-10-22", "receiver_name": "Presentation customer",
            "amount_paid": "50", "lines": [{"product_id": str(product.id),
                "quantity": "1", "unit_rate": "50", "tax_rate": "0"}]})
        settings = get_settings().model_copy(update={"business_name": "", "business_gstin": "", "business_address": ""})
        with patch('app.api.routes.invoices.get_settings', return_value=settings):
            result = create_sales_invoice(payload, self.db)
        self.db.refresh(product)
        self.assertEqual(product.quantity_on_hand, 2)
        self.assertEqual(result['source_type'], 'POS_RECEIPT')
        self.assertEqual(result['tax_total'], Decimal('0'))
        self.assertEqual(result['status'], 'APPROVED')
        pdf = download_sales_receipt(str(result['id']), self.db).body
        self.assertIn(b'SALES RECEIPT', pdf)
        self.assertNotIn(b'GSTIN', pdf)

    def test_non_gst_receipt_rejects_tax_before_writing(self):
        payload = SalesInvoiceInput.model_validate({
            "document_type": "NON_GST_RECEIPT", "invoice_number": "INVALID-RECEIPT",
            "invoice_date": "2026-10-22", "receiver_name": "Customer",
            "lines": [{"product_id": "1", "quantity": "1", "unit_rate": "50", "tax_rate": "18"}]})
        with self.assertRaises(HTTPException) as raised:
            create_sales_invoice(payload, self.db)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(self.db.query(Invoice).count(), 0)

    def test_selected_upload_direction_survives_cloud_extraction_merge(self):
        from app.repositories.dynamodb_store import merge_textract
        invoice = {'transaction_type': 'SALE', 'direction_reason': 'Upload direction selected as SALE',
                   'lines': [], 'status': 'NEEDS_REVIEW'}
        with patch('app.repositories.dynamodb_store.save_invoice', side_effect=lambda row, **kwargs: row):
            result = merge_textract(invoice, {'transaction_type': 'PURCHASE',
                'direction_reason': 'Old inference', 'lines': []}, 'RESULTS_LOADED')
        self.assertEqual(result['transaction_type'], 'SALE')
        self.assertEqual(result['direction_reason'], 'Upload direction selected as SALE')

    def test_tax_inclusive_rate_reconciles_to_actual_dataset_line_amount(self):
        row=_line_from_fields([{'type':'ITEM','value':'OEM ELCO 6 SOCKET POWER MANAGER'},
                               {'type':'QUANTITY','value':'2 NOS'},
                               {'type':'UNIT_PRICE','value':'460.00'},
                               {'type':'PRICE','value':'779.66'}])
        self.assertEqual(row['unit_rate'],Decimal('389.83'))

    def test_forecast_runs_without_sagemaker_even_with_legacy_setting(self):
        data = 'product_id,date,quantity_sold\n' + ''.join(
            f'validation,{date(2026, 1, 1)+timedelta(days=day)},2\n' for day in range(10))
        settings = SimpleNamespace(max_upload_bytes=10000, sagemaker_endpoint_name='obsolete',
                                   aws_region='ap-southeast-2', business_data_table_name='')
        upload = UploadFile(file=io.BytesIO(data.encode()), filename='verified.csv',
                            headers=Headers({'content-type': 'text/csv'}))
        with patch('app.api.routes.forecasts.get_settings', return_value=settings):
            result = asyncio.run(create_forecast(upload, self.db))
        self.assertEqual(result['metrics']['mae'], 0)
        self.assertEqual(result['metrics']['training_backend'], 'aws_lambda')
        self.assertEqual(result['metrics']['inference_backend'], 'aws_lambda')
        self.assertEqual(result['model_name'], 'validated_demand_model')
        self.assertEqual(len(result['forecasts']), 7)

    @classmethod
    def tearDownClass(cls):
        engine.dispose()
        _TEST_DIRECTORY.cleanup()

    def setUp(self):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def make_invoice(self, *, direction="PURCHASE", status="NEEDS_REVIEW", quantity="2", product_id=None):
        invoice = Invoice(
            filename="invoice.pdf", content_type="application/pdf", stored_path="missing.pdf",
            vendor_name="Supplier", invoice_number="INV-1", invoice_date=date(2026, 10, 1),
            subtotal=Decimal("20.00"), tax_total=Decimal("0.00"), grand_total=Decimal("20.00"),
            transaction_type=direction, status=status,
            lines=[InvoiceLine(description="Widget", quantity=Decimal(quantity), unit="ea",
                               unit_rate=Decimal("10.00"), line_amount=Decimal("20.00"),
                               product_id=product_id)],
        )
        self.db.add(invoice)
        self.db.commit()
        self.db.refresh(invoice)
        return invoice

    def test_purchase_approval_creates_product_and_posts_stock_once(self):
        invoice = self.make_invoice()
        result = review_invoice(invoice.id, {"action": "approve"}, self.db)

        product = self.db.query(Product).one()
        self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(product.quantity_on_hand, 2)
        self.assertEqual(product.unit_cost, Decimal("10.00"))
        self.assertEqual(invoice.lines[0].product_id, product.id)
        self.assertFalse(product.stock_count_verified)
        self.assertEqual(self.db.query(StockMovement).count(), 1)
        with self.assertRaises(HTTPException) as raised:
            post_approved_invoice_inventory(invoice.id, self.db)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.db.query(StockMovement).count(), 1)

    def test_sale_approval_prevents_negative_stock(self):
        product = Product(sku="W-1", name="Widget", quantity_on_hand=1, reorder_point=0,
                          unit_cost=Decimal("10.00"), stock_count_verified=True)
        self.db.add(product)
        self.db.commit()
        invoice = self.make_invoice(direction="SALE", quantity="2", product_id=product.id)

        with self.assertRaises(HTTPException) as raised:
            review_invoice(invoice.id, {"action": "approve"}, self.db)
        self.assertEqual(raised.exception.status_code, 422)
        self.db.refresh(product)
        self.db.refresh(invoice)
        self.assertEqual(product.quantity_on_hand, 1)
        self.assertEqual(invoice.status, "NEEDS_REVIEW")
        self.assertEqual(self.db.query(StockMovement).count(), 0)

    def test_selected_product_mapping_overrides_invoice_description(self):
        product = Product(sku="TONER-88A", name="Image Star Toner Cartridge 88A",
                          quantity_on_hand=3, reorder_point=1,
                          unit_cost=Decimal("466.10"), stock_count_verified=False)
        self.db.add(product)
        self.db.commit()
        invoice = self.make_invoice(product_id=product.id)
        invoice.lines[0].description = "88A Toner"
        self.db.commit()

        review_invoice(invoice.id, {"action": "approve"}, self.db)

        self.db.refresh(product)
        self.assertEqual(self.db.query(Product).count(), 1)
        self.assertEqual(product.quantity_on_hand, 5)
        self.assertEqual(product.unit_cost, Decimal("283.66"))
        self.assertEqual(invoice.lines[0].product_id, product.id)
        self.assertIn("88A Toner", product.aliases)
        self.assertEqual(self.db.query(StockMovement).one().product_id, product.id)

    def test_product_match_is_insensitive_to_capacity_and_word_order(self):
        product = Product(sku="HDD-1TB", name="1 TB Hard Disk", quantity_on_hand=4,
                          reorder_point=1, unit_cost=Decimal("40.00"), stock_count_verified=True)
        self.db.add(product)
        self.db.commit()
        invoice = self.make_invoice(quantity="1")
        invoice.lines[0].description = "Harddisk 1TB"
        self.db.commit()

        review_invoice(invoice.id, {"action": "approve"}, self.db)

        self.assertEqual(self.db.query(Product).count(), 1)
        self.assertEqual(product.quantity_on_hand, 5)
        self.assertEqual(invoice.lines[0].product_id, product.id)

    def test_shared_hsn_does_not_merge_an_unverified_product(self):
        product = Product(sku="TONER-88A", name="Image Star Toner 88A",
                          product_type="Toner", hsn_sac="8443",
                          quantity_on_hand=3, reorder_point=1,
                          unit_cost=Decimal("40.00"), stock_count_verified=True)
        self.db.add(product)
        self.db.commit()
        invoice = self.make_invoice(quantity="1")
        invoice.lines[0].description = "Black laser cartridge"
        invoice.lines[0].hsn_sac = "84-43"
        self.db.commit()

        review_invoice(invoice.id, {"action": "approve"}, self.db)

        self.assertEqual(self.db.query(Product).count(), 2)
        self.assertEqual(product.quantity_on_hand, 3)
        self.assertNotEqual(invoice.lines[0].product_id, product.id)

    def test_quotation_cannot_be_approved_as_a_completed_sale(self):
        from app.repositories.dynamodb_store import approve_invoice_and_post_stock
        with self.assertRaisesRegex(ValueError, 'quotation'):
            approve_invoice_and_post_stock({'id': 'quote', 'status': 'NEEDS_REVIEW',
                'transaction_type': 'SALE', 'source_document_kind': 'QUOTATION'}, [])

    def test_service_sale_approves_without_creating_inventory(self):
        invoice = self.make_invoice(direction='SALE', quantity='1')
        invoice.lines[0].description = 'Installation service'
        invoice.lines[0].is_stock_item = False
        self.db.commit()
        result = review_invoice(invoice.id, {'action': 'approve'}, self.db)
        self.assertEqual(result['status'], 'APPROVED')
        self.assertFalse(result['lines'][0]['is_stock_item'])
        from sqlalchemy import select
        self.assertEqual(len(self.db.scalars(select(Product)).all()), 0)
        self.assertEqual(len(self.db.scalars(select(StockMovement)).all()), 0)

    def test_nonstock_purchase_does_not_create_courier_product(self):
        invoice = self.make_invoice(direction='PURCHASE', quantity='1')
        invoice.lines[0].description = 'Courier charges'
        invoice.lines[0].is_stock_item = False
        self.db.commit()
        review_invoice(invoice.id, {'action': 'approve'}, self.db)
        from sqlalchemy import select
        self.assertEqual(len(self.db.scalars(select(Product)).all()), 0)

    def test_cloud_service_sale_has_only_invoice_write(self):
        from unittest.mock import MagicMock
        from app.repositories import dynamodb_store
        mock_table = MagicMock()
        mock_table.name = 'business'
        mock_table.get_item.return_value = {'Item': {'status': 'NEEDS_REVIEW'}}
        record = {'id': 'service-sale', 'status': 'NEEDS_REVIEW', 'transaction_type': 'SALE',
            'lines': [{'description': 'Labour', 'quantity': '1.5', 'line_amount': '300', 'is_stock_item': False}]}
        with patch.object(dynamodb_store, 'table', return_value=mock_table), patch.object(dynamodb_store, 'list_serial_assets', return_value=[]):
            result = dynamodb_store.approve_invoice_and_post_stock(record, [])
        self.assertEqual(result['status'], 'APPROVED')
        writes = mock_table.meta.client.transact_write_items.call_args.kwargs['TransactItems']
        self.assertEqual(len(writes), 1)

    def test_nonstock_line_cannot_hide_mapped_inventory(self):
        from app.repositories import dynamodb_store
        record = {'id': 'service-bad', 'status': 'NEEDS_REVIEW', 'transaction_type': 'SALE',
            'lines': [{'description': 'Labour', 'quantity': '1', 'is_stock_item': False, 'product_id': 'disk'}]}
        with patch.object(dynamodb_store, 'list_serial_assets', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'must not have'):
                dynamodb_store.approve_invoice_and_post_stock(record, [])

    def test_cloud_alias_correction_preserves_stock_and_price(self):
        from unittest.mock import MagicMock
        from app.repositories import dynamodb_store
        product = {'id': 'nvr', 'name': '32 port NVR', 'aliases': ['16 port NVR'],
            'quantity_on_hand': 2, 'unit_cost': '100'}
        with patch.object(dynamodb_store, 'get_product', return_value=product), patch.object(dynamodb_store, 'table', return_value=MagicMock()):
            corrected = dynamodb_store.update_product_details('nvr', {'aliases': []})
        self.assertEqual(corrected['aliases'], [])
        self.assertEqual(corrected['quantity_on_hand'], 2)
        self.assertEqual(corrected['unit_cost'], '100')

    def test_sale_printer_cannot_match_toner_by_hsn(self):
        product = Product(sku='TONER-NOT-PRINTER', name='Toner cartridge', hsn_sac='8443',
                          quantity_on_hand=10, reorder_point=0, unit_cost=Decimal('10'))
        self.db.add(product)
        self.db.commit()
        invoice = self.make_invoice(direction='SALE', quantity='1')
        invoice.lines[0].description = 'HP 323 SDNW Printer'
        invoice.lines[0].hsn_sac = '8443'
        self.db.commit()
        with self.assertRaises(HTTPException):
            review_invoice(invoice.id, {'action': 'approve'}, self.db)
        self.db.refresh(product)
        self.assertEqual(product.quantity_on_hand, 10)

    def test_product_pricing_rules_are_optional_and_editable(self):
        product = Product(sku="MON-1", name="Monitor", quantity_on_hand=2,
                          reorder_point=1, unit_cost=Decimal("80.00"), stock_count_verified=True)
        self.db.add(product)
        self.db.commit()

        result = update_pricing_rules(str(product.id), PricingRulesInput(
            minimum_margin_percent=20, target_margin_percent=30), self.db)

        self.assertEqual(Decimal(str(result["minimum_margin_percent"])), Decimal("20.00"))
        self.assertEqual(Decimal(str(result["target_margin_percent"])), Decimal("30.00"))
        self.assertEqual(product.quantity_on_hand, 2)
        self.assertEqual(product.unit_cost, Decimal("80.00"))

    def test_product_hsn_can_be_corrected_without_changing_stock(self):
        product = Product(sku="HSN-1", name="Monitor", quantity_on_hand=2,
                          reorder_point=1, unit_cost=Decimal("80.00"))
        self.db.add(product)
        self.db.commit()

        result = update_product_details(str(product.id), ProductDetailsInput(hsn_sac="8528"), self.db)

        self.assertEqual(result["hsn_sac"], "8528")
        self.assertEqual(product.quantity_on_hand, 2)
        self.assertEqual(product.unit_cost, Decimal("80.00"))

    def test_product_target_margin_cannot_be_below_minimum(self):
        product = Product(sku="MON-2", name="Monitor 2", quantity_on_hand=1,
                          reorder_point=0, unit_cost=Decimal("80.00"), stock_count_verified=True)
        self.db.add(product)
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            update_pricing_rules(str(product.id), PricingRulesInput(
                minimum_margin_percent=25, target_margin_percent=20), self.db)
        self.assertEqual(raised.exception.status_code, 422)

    def test_pos_sale_creates_receipt_and_posts_stock_once(self):
        product = Product(sku="POS-MON", name="Monitor", hsn_sac="8528",
                          quantity_on_hand=3, reorder_point=1,
                          unit_cost=Decimal("40.00"), stock_count_verified=True)
        self.db.add(product)
        self.db.commit()

        invoice = create_sales_invoice(SalesInvoiceInput.model_validate({
            "invoice_number": "POS-1001", "invoice_date": "2026-10-20",
            "receiver_name": "Local Customer", "amount_paid": "50.00",
            "lines": [{"product_id": str(product.id), "quantity": "1",
                       "unit_rate": "50.00", "tax_rate": "0"}],
        }), self.db)

        self.db.refresh(product)
        self.assertEqual(invoice["status"], "APPROVED")
        self.assertEqual(invoice["transaction_type"], "SALE")
        self.assertEqual(invoice["subtotal"], Decimal("50.00"))
        self.assertEqual(invoice["tax_total"], Decimal("0.00"))
        self.assertEqual(invoice["grand_total"], Decimal("50.00"))
        self.assertEqual(invoice["payment_status"], "PAID")
        self.assertEqual(product.quantity_on_hand, 2)
        self.assertEqual(self.db.query(StockMovement).one().quantity_change, -1)

    def test_pos_sale_rejects_insufficient_stock_without_saving_invoice(self):
        product = Product(sku="POS-ONE", name="One item", quantity_on_hand=1,
                          reorder_point=0, unit_cost=Decimal("25.00"), stock_count_verified=True)
        self.db.add(product)
        self.db.commit()
        payload = SalesInvoiceInput.model_validate({
            "invoice_number": "POS-1002", "invoice_date": "2026-10-20",
            "receiver_name": "Local Customer",
            "lines": [{"product_id": str(product.id), "quantity": "2",
                       "unit_rate": "30.00", "tax_rate": "0"}],
        })

        with self.assertRaises(HTTPException) as raised:
            create_sales_invoice(payload, self.db)

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(self.db.query(Invoice).count(), 0)
        self.assertEqual(product.quantity_on_hand, 1)

    def test_sales_forecast_requires_explicit_complete_history_confirmation(self):
        coroutine = forecast_from_approved_sales(
            ApprovedSalesForecastInput(confirm_complete_sales_history=False), self.db)
        with self.assertRaises(HTTPException) as raised:
            coroutine.send(None)
        self.assertEqual(raised.exception.status_code, 422)

    def test_approved_sales_forecasts_share_one_calendar_cutoff(self):
        from app.api.routes.forecasts import forecast_from_approved_sales, ApprovedSalesForecastInput
        from types import SimpleNamespace
        rows=[{'id':'p1','status':'APPROVED','source_type':'DEMO','transaction_type':'SALE',
               'invoice_date':'2026-10-01','lines':[{'product_id':'A','quantity':2}]},
              {'id':'p2','status':'APPROVED','source_type':'DEMO','transaction_type':'SALE',
               'invoice_date':'2026-10-05','lines':[{'product_id':'B','quantity':1}]}]
        with patch('app.api.routes.forecasts.get_settings',return_value=SimpleNamespace(business_data_table_name='test')), \
             patch('app.api.routes.forecasts.dynamodb_store.list_invoices',return_value=rows), \
             patch('app.api.routes.forecasts.dynamodb_store.list_products',return_value=[]), \
             patch('app.api.routes.forecasts.dynamodb_store.create_forecast',side_effect=lambda record:dict(record,id='run',created_at='now')):
            run=asyncio.run(forecast_from_approved_sales(ApprovedSalesForecastInput(
                confirm_complete_sales_history=True,horizon_days=90),self.db))
        dates={row['forecast_date'] for row in run['forecasts']}
        self.assertEqual(len(dates),90)
        self.assertEqual(min(dates),'2026-10-06')
        self.assertEqual(len(run['forecasts']),180)

    def test_forecast_linear_trend_matches_known_series(self):
        rows = _read_series(
            b"product_id,date,quantity_sold\n"
            b"W-1,2026-10-01,1\n"
            b"W-1,2026-10-02,2\n"
            b"W-1,2026-10-03,3\n"
            b"W-1,2026-10-04,4\n")

        self.assertEqual(rows["W-1"][-1], (date(2026, 10, 4), 4.0))
        self.assertEqual(_linear_trend([1, 2, 3, 4]), 5)
        self.assertEqual(_linear_trend([1, 2, 3, 4], steps=2), 6)
        self.assertEqual(_linear_trend([3, 3, 3, 3]), 3)
        self.assertEqual(_linear_trend([0, 0, 0, 0]), 0)

    def test_invoice_validation_flags_bad_gstin_and_unbalanced_totals(self):
        result = validate_invoice_payload(InvoiceValidationInput.model_validate({
            "vendor_name": "Supplier", "vendor_gstin": "not-a-gstin", "invoice_number": "INV-1",
            "invoice_date": "2026-10-01", "subtotal": "100.00", "tax_total": "18.00",
            "grand_total": "120.00", "transaction_type": "PURCHASE",
            "lines": [{"description": "Widget", "quantity": "2", "unit_rate": "50.00",
                       "line_amount": "100.00"}],
        }))
        self.assertFalse(result.valid)
        self.assertTrue(any("GSTIN format is invalid" in warning for warning in result.warnings))
        self.assertTrue(any("does not reconcile" in warning for warning in result.warnings))

    def test_gst_routes_removed_and_receipt_route_present(self):
        paths=app.openapi()['paths']
        self.assertNotIn('/api/v1/reports/gst',paths)
        self.assertNotIn('/api/v1/invoices/{invoice_id}/gst-pdf',paths)
        self.assertIn('/api/v1/invoices/{invoice_id}/receipt-pdf',paths)
        with self.assertRaises(ValueError):
            SalesInvoiceInput.model_validate({'document_type':'GST_INVOICE','invoice_number':'REMOVED',
                'invoice_date':'2026-10-23','receiver_name':'Customer',
                'lines':[{'product_id':'1','quantity':1,'unit_rate':50,'tax_rate':0}]})

    def test_purchase_serials_and_warranty_follow_unit_through_sale(self):
        invoice = self.make_invoice(quantity="2")
        invoice.lines[0].serial_numbers = ["SN-A-001", "SN-A-002"]
        invoice.lines[0].warranty_months = 12
        review_invoice(invoice.id, {"action": "approve"}, self.db)
        product = self.db.query(Product).one()
        asset = self.db.query(SerialAsset).filter_by(serial_key="sna001").one()
        self.assertEqual(asset.status, "IN_STOCK")
        self.assertEqual(asset.warranty_until, date(2027, 10, 1))

        result = create_sales_invoice(SalesInvoiceInput.model_validate({
            "invoice_number": "POS-SERIAL-1", "invoice_date": "2026-10-20",
            "receiver_name": "Customer", "amount_paid": "0",
            "lines": [{"product_id": str(product.id), "quantity": "1",
                       "unit_rate": "15.00", "tax_rate": "0",
                       "serial_numbers": ["SN-A-001"]}],
        }), self.db)

        self.assertEqual(result["status"], "APPROVED")
        self.db.refresh(asset)
        self.assertEqual(asset.status, "SOLD")
        self.assertEqual(asset.sale_invoice_id, str(result["id"]))

    def test_sale_below_floor_requires_and_records_override_reason(self):
        product = Product(sku="FLOOR-1", name="Costly widget", quantity_on_hand=2,
                          unit_cost=Decimal("100.00"), stock_count_verified=True)
        self.db.add(product)
        self.db.commit()
        payload = SalesInvoiceInput.model_validate({
            "invoice_number": "POS-FLOOR-1", "invoice_date": "2026-10-20",
            "receiver_name": "Customer",
            "lines": [{"product_id": str(product.id), "quantity": "1",
                       "unit_rate": "80.00", "tax_rate": "0"}],
        })
        with self.assertRaises(HTTPException) as raised:
            create_sales_invoice(payload, self.db)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("below its floor", raised.exception.detail)

        override_payload = payload.model_copy(update={
            "lines": [payload.lines[0].model_copy(update={"price_override_reason": "Approved clearance sale"})],
        })
        result = create_sales_invoice(override_payload, self.db)
        self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(result["lines"][0]["suggested_unit_rate"], Decimal("100.00"))
        self.assertEqual(result["lines"][0]["price_override_reason"], "Approved clearance sale")

    def test_partial_invoice_payments_update_receivable_balance(self):
        product = Product(sku="PAY-1", name="Payment widget", quantity_on_hand=4,
                          unit_cost=Decimal("10.00"), stock_count_verified=True)
        self.db.add(product)
        self.db.commit()
        invoice = create_sales_invoice(SalesInvoiceInput.model_validate({
            "invoice_number": "POS-PAY-1", "invoice_date": "2026-10-20",
            "receiver_name": "Credit Customer",
            "lines": [{"product_id": str(product.id), "quantity": "1",
                       "unit_rate": "50.00", "tax_rate": "0"}],
        }), self.db)

        partial = record_invoice_payment(str(invoice["id"]), PaymentInput(amount=Decimal("20.00")), self.db)
        self.assertEqual(partial["payment_status"], "PARTIAL")
        self.assertEqual(Decimal(str(partial["amount_paid"])), Decimal("20.00"))
        paid = record_invoice_payment(str(invoice["id"]), PaymentInput(amount=Decimal("30.00")), self.db)
        self.assertEqual(paid["payment_status"], "PAID")
        self.assertEqual(len(paid["payment_history"]), 2)

if __name__ == "__main__":
    unittest.main()
