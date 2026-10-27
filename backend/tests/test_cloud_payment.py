import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from app.repositories import dynamodb_store as store


class ConditionalFailure(Exception):
    pass


class CloudPaymentTests(unittest.TestCase):
    def table(self, conflict=False):
        payload=json.dumps({'id':'sale-test','status':'APPROVED','grand_total':'6500.00',
                            'amount_paid':'1000.00','payment_history':[]})
        target=SimpleNamespace(meta=SimpleNamespace(client=SimpleNamespace(
            exceptions=SimpleNamespace(ConditionalCheckFailedException=ConditionalFailure))))
        target.get_item=lambda **kwargs:{'Item':{'payload':payload,'status':'APPROVED'}}
        target.calls=[]
        def update(**kwargs):
            target.calls.append(kwargs)
            if conflict:raise ConditionalFailure()
        target.update_item=update
        return target,payload

    def test_legacy_approved_record_with_missing_indexed_balance_can_be_paid(self):
        target,payload=self.table()
        with patch.object(store,'table',return_value=target):
            paid=store.record_invoice_payment('sale-test',Decimal('5500'),method='DEMO',reference='TEST')
        self.assertEqual(paid['amount_paid'],Decimal('6500'))
        self.assertEqual(paid['payment_status'],'PAID')
        self.assertEqual(target.calls[0]['ExpressionAttributeValues'][':expected_payload'],payload)
        self.assertIn('payload = :expected_payload',target.calls[0]['ConditionExpression'])

    def test_concurrent_payment_snapshot_is_rejected(self):
        target,_=self.table(conflict=True)
        with patch.object(store,'table',return_value=target):
            with self.assertRaisesRegex(ValueError,'changed concurrently'):
                store.record_invoice_payment('sale-test',Decimal('5500'),method='DEMO',reference='TEST')
