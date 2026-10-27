import unittest
from decimal import Decimal
from app.services.sales_receipt_pdf import render_sales_receipt


class ReceiptZeroBalanceTests(unittest.TestCase):
    def test_paid_receipt_keeps_zero_balance_visible(self):
        pdf=render_sales_receipt({'vendor_name':'Demo Store','invoice_number':'TEST',
             'invoice_date':'2026-10-24','receiver_name':'Demo Customer','grand_total':Decimal('6500.00'),
             'amount_paid':Decimal('6500.00'),'lines':[]})
        self.assertIn(b'(Balance due: INR 0.00) Tj',pdf)
