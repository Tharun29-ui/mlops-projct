import json
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.catalogue_merge import confirmed_product_merge
from app.repositories import dynamodb_store as store


class CatalogueMergeTests(unittest.TestCase):
    def products(self):
        return ({'id':'main','sku':'main','name':'DELL 22 inch LED MONITOR SE2225HM','quantity_on_hand':1,
                 'unit_cost':'5076.27','hsn_sac':'852852','aliases':[]},
                {'id':'short','sku':'short','name':'DELL 22 inch LED MONITOR','quantity_on_hand':6,
                 'unit_cost':'5076.27','hsn_sac':'852852','aliases':[]})

    def test_confirmed_same_product_conserves_stock_value_and_learns_alias(self):
        main, source = self.products()
        merged, archived = confirmed_product_merge(main,source,confirmed_same_product=True,timestamp='2026-11-01')
        self.assertEqual(merged['quantity_on_hand'],7)
        self.assertEqual(Decimal(merged['unit_cost'])*7,Decimal(main['unit_cost'])*1+Decimal(source['unit_cost'])*6)
        self.assertIn(source['name'],merged['aliases'])
        self.assertEqual(archived['quantity_on_hand'],0)
        self.assertEqual(archived['merged_into'],'main')
        self.assertEqual(archived['pre_merge_quantity_on_hand'],6)
        self.assertEqual(main['quantity_on_hand'],1)

    def test_requires_confirmation_and_rejects_a_different_model(self):
        main,source=self.products()
        with self.assertRaisesRegex(ValueError,'confirmation'):
            confirmed_product_merge(main,source,confirmed_same_product=False,timestamp='now')
        source['name']='DELL 22 inch LED MONITOR SE2222H'
        with self.assertRaisesRegex(ValueError,'model'):
            confirmed_product_merge(main,source,confirmed_same_product=True,timestamp='now')

    def test_archived_sku_is_not_returned_as_active_stock(self):
        main,source=self.products()
        source['merged_into']='main'
        items=[{'payload':json.dumps(p),'quantity_on_hand':p['quantity_on_hand']} for p in [main,source]]
        with patch.object(store,'_query_all',return_value=items):
            self.assertEqual([p['id'] for p in store.list_products()],['main'])
        fake=MagicMock();fake.get_item.return_value={'Item':items[1]}
        with patch.object(store,'table',return_value=fake):self.assertIsNone(store.get_product('short'))

    def test_following_purchase_uses_learned_short_name_without_creating_product(self):
        main,source=self.products()
        main['aliases']=[source['name']]
        record={'id':'later','status':'NEEDS_REVIEW','transaction_type':'PURCHASE','invoice_number':'LATER',
            'invoice_date':'2026-11-02','lines':[{'description':source['name'],'quantity':'2','unit_rate':'5076.27',
                                               'line_amount':'10152.54','hsn_sac':'852852'}]}
        fake=MagicMock();fake.name='data';fake.get_item.return_value={'Item':{'status':'NEEDS_REVIEW'}}
        with patch.object(store,'table',return_value=fake),patch.object(store,'list_serial_assets',return_value=[]),patch.object(store,'list_movements',return_value=[]):
            result=store.approve_invoice_and_post_stock(record,[main])
        self.assertEqual(result['lines'][0]['product_id'],'main')
        writes=fake.meta.client.transact_write_items.call_args.kwargs['TransactItems']
        self.assertFalse(any('Put' in w and w['Put']['Item'].get('entity_type')=='PRODUCT' for w in writes))
        updated=next(w['Update'] for w in writes if 'Update' in w)
        self.assertIn('attribute_not_exists(merged_into)',updated['ConditionExpression'])

    def test_movement_history_is_in_time_order(self):
        rows=[{'payload':json.dumps({'id':'older','created_at':'2026-01-01','quantity_change':1})},
              {'payload':json.dumps({'id':'newer','created_at':'2026-02-01','quantity_change':6})}]
        with patch.object(store,'_query_all',return_value=rows):
            self.assertEqual([p['id'] for p in store.list_movements('main')],['newer','older'])
