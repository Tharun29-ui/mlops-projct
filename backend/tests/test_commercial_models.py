import json
import csv
import unittest
import math
from datetime import date, timedelta
from copy import deepcopy
from pathlib import Path
from app.services.commercial_models import build_insights, train_shared_prices, predict_shared_price, effective_line_price


class SuppliedDatasetModelTests(unittest.TestCase):
    def test_shared_trend_can_win_on_repeated_product_history(self):
        rows=[{'product_id':product, 'unit':'each', 'date':(date(2026,1,1)+timedelta(days=day)).isoformat(),
            'quantity':2, 'price':base*math.exp(.01*day), 'party':'Supplier', 'currency':'INR'}
            for product,base in [('camera',100),('cable',10),('monitor',1000)] for day in range(30)]
        result=train_shared_prices(rows)
        self.assertTrue(result['selected_algorithm'].startswith('within_product_log_ridge'))
        self.assertLess(result['test_metrics']['mae'],result['last_price_baseline_metrics']['mae'])
        self.assertEqual(result['quality_gate'],'passed_baseline_check')
        restored=json.loads(json.dumps(result['model']))
        query={'product_id':'camera','unit':'each','date':'2026-02-15','quantity':2}
        self.assertEqual(predict_shared_price(restored,query),predict_shared_price(result['model'],query))

    def test_single_observation_product_does_not_create_a_price_trend(self):
        rows=[{'product_id':str(i),'unit':'each','date':f'2026-01-{i+1:02d}',
            'quantity':1,'price':10**(i%5),'currency':'INR'} for i in range(20)]
        result=train_shared_prices(rows)
        self.assertEqual(result['selected_algorithm'],'last_observed_price')
        self.assertEqual(result['comparable_validation_rows'],0)
        self.assertIsNone(result['test_metrics'])
        self.assertEqual(result['quality_gate'],'research_only')

    def test_shared_price_rejects_invalid_and_cross_currency_inputs(self):
        with self.assertRaises(ValueError):
            train_shared_prices([{'date':'2026-01-01','quantity':1,'price':float('nan')}])
        rows=[{'product_id':'disk','unit':'each','date':f'2026-01-{i+1:02d}',
            'quantity':1,'price':100,'currency':'INR' if i%2 else 'USD'} for i in range(20)]
        self.assertEqual(train_shared_prices(rows)['status'],'separate_currency_models_required')

    def test_unknown_product_cannot_receive_another_products_price(self):
        rows=[{'product_id':'disk','unit':'each','date':f'2026-01-{i+1:02d}',
            'quantity':1,'price':100,'currency':'INR'} for i in range(20)]
        result=train_shared_prices(rows)
        with self.assertRaisesRegex(ValueError,'No trained product'):
            predict_shared_price(result['model'],{'product_id':'monitor','unit':'each','date':'2026-02-01','quantity':1})

    def test_historical_inclusive_rate_uses_recorded_net_line_amount(self):
        self.assertEqual(effective_line_price({'quantity': '2', 'unit_rate': '460', 'line_amount': '779.66'}), 389.83)
        self.assertEqual(effective_line_price({'quantity': '2', 'unit_rate': '20'}), 20)
        self.assertIsNone(effective_line_price({'quantity': '0', 'unit_rate': '20'}))
    @classmethod
    def setUpClass(cls):
        path=Path(__file__).resolve().parents[2]/'ml/datasets/real-invoice-history/commercial_rows.json'
        if not path.exists():raise unittest.SkipTest('Run real invoice dataset extraction first')
        cls.rows=json.loads(path.read_text())

    def test_real_dataset_has_both_directions_and_no_heldout_sources(self):
        self.assertEqual({r['direction'] for r in self.rows},{'PURCHASE','SALE'})
        self.assertTrue(all(r['source_sha256'] and r['price']>=0 for r in self.rows))
        manifest=Path(__file__).resolve().parents[2]/'ml/datasets/source-manifests/invoice_split.csv'
        with manifest.open(encoding='utf-8-sig') as handle:
            heldout={r['relative_path'] for r in csv.DictReader(handle) if r['split']=='test'}
        self.assertFalse({r['source'] for r in self.rows}&heldout)

    def test_saved_shared_model_reproduces_dataset_estimate(self):
        result=train_shared_prices([r for r in self.rows if r['direction']=='SALE'])
        model=json.loads(json.dumps(result['model']))
        row=result['estimates'][0]
        prediction=predict_shared_price(model,{'product_id':row['product_id'],'unit':row['unit'],
                                               'date':row['forecast_date'],'quantity':row['assumed_quantity']})
        self.assertEqual(round(prediction,2),row['predicted_price'])

    def test_price_test_targets_do_not_change_evaluation_fit(self):
        rows=[r for r in self.rows if r['direction']=='PURCHASE']
        result=train_shared_prices(rows)
        changed=deepcopy(rows)
        for row in changed:
            if row['date'][:10]>=result['test_start']:row['price']*=10
        altered=train_shared_prices(changed)
        self.assertEqual(result['evaluation_model'],altered['evaluation_model'])
        self.assertNotEqual(result['test_metrics'],altered['test_metrics'])

    def test_profit_scenario_from_actual_dataset_cost_and_quantity(self):
        row=next(r for r in self.rows if r['direction']=='PURCHASE' and r['price']>0)
        report=build_insights([row],demand={row['product_id']:row['quantity']})
        product=report['products'][0]
        self.assertGreaterEqual(product['selling_price']['suggested'],product['selling_price']['floor'])
        profit=product['profit_projection']
        self.assertAlmostEqual(profit['revenue']-profit['cost_of_goods'],profit['gross_profit'],places=2)
        self.assertIn('scenario',profit['note'])
        self.assertEqual(product['planning_scenario']['assumed_units'],row['quantity'])
        self.assertIn('not forecast demand',product['planning_scenario']['basis'])
        self.assertEqual(len(report['training_data_sha256']),64)

    def test_few_rows_abstain_from_individual_price_training(self):
        report=build_insights(self.rows[:1])
        self.assertEqual(report['products'][0]['purchase_cost_model']['status'],'insufficient_history')
