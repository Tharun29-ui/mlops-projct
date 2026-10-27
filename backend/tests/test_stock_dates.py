import unittest
from app.services.stock_dates import validate_dated_stock


class StockDateTests(unittest.TestCase):
    def test_later_purchase_cannot_supply_an_earlier_sale(self):
        with self.assertRaisesRegex(ValueError, 'later purchase'):
            validate_dated_stock(5,[{'effective_date':'2026-01-05','quantity_change':5}],-1,'2026-01-04','Monitor')

    def test_earlier_confirmed_opening_balance_supplies_sale(self):
        validate_dated_stock(7,[{'effective_date':'2026-01-03','quantity_change':2},
            {'effective_date':'2026-01-05','quantity_change':5}],-2,'2026-01-04','Monitor')

    def test_same_day_purchase_and_sale_are_supported(self):
        validate_dated_stock(5,[{'effective_date':'2026-01-04','quantity_change':5}],-5,'2026-01-04','Monitor')

    def test_upload_order_does_not_change_effective_dates(self):
        history=[{'effective_date':'2026-01-05','quantity_change':5},
                 {'effective_date':'2026-01-03','quantity_change':2}]
        validate_dated_stock(7,history,-2,'2026-01-04','Monitor')
