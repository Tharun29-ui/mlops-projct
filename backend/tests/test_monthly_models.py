import copy
import hashlib
import json
import math
import unittest
from datetime import date
from pathlib import Path
from app.services.monthly_models import identity,inventory_plan,predict,next_month,demand_group,features


class MonthlyModelTests(unittest.TestCase):
    def bundle(self):
        state={'selected_algorithm':'last_value','model':{'algorithm':'last_value'},'validation_relative_error':.2}
        return {'version':'test','models':{k:copy.deepcopy(state) for k in ['demand','SALE','PURCHASE']},
            'source_audit':{'synthetic_rows':0},'histories':{identity('WD 1TB Hard Disk'):{'demand':[{'date':'2026-09-01','value':8}],
                'SALE':[{'date':'2026-09-11','value':150}],'PURCHASE':[{'date':'2026-09-01','value':90}]}}}
    def product(self):return {'id':'p1','name':'WD 1TB Hard Disk','unit_cost':90,'quantity_on_hand':3,'sku':'WD1','hsn_sac':'8471'}
    def plan(self,**kwargs):return inventory_plan(self.bundle(),[self.product()],{'minimum_margin_percent':10,'target_margin_percent':20},today=date(2026,10,24),**kwargs)
    def test_calendar_month_and_revenue_stock_shortfall(self):
        p=self.plan();r=p['rows'][0]
        self.assertEqual((p['period_start'],p['period_end']),('2026-11-01','2026-11-30'))
        self.assertEqual(r['forecast_units'],8);self.assertEqual(r['revenue'],1200)
        self.assertEqual(r['stock_backed_revenue'],450);self.assertEqual(r['reorder_quantity'],5)
        self.assertGreaterEqual(r['price_range'][0],r['price_floor'])
    def test_quantity_override_zero_is_not_missing(self):
        r=self.plan(assumptions={'p1':0})['rows'][0]
        self.assertEqual(r['revenue'],0);self.assertEqual(r['status'],'USER_QUANTITY_SCENARIO')
    def test_missing_history_is_not_zero_and_category_is_explicit(self):
        b=self.bundle();p=self.product();p['name']='WD 2TB Hard Disk'
        result=inventory_plan(b,[p],{},today=date(2026,10,24))
        self.assertIsNone(result['total_revenue']);self.assertIsNone(result['rows'][0]['history_key'])
        b['category_priors']={'harddisk:piece':{'date':'2026-09-01','value':2,'observations':6}}
        r=inventory_plan(b,[p],{},today=date(2026,10,24))['rows'][0]
        self.assertEqual(r['status'],'CATEGORY_ANALOGUE_SCENARIO');self.assertIsNone(r['history_key'])
    def test_no_inventory_or_artifact_mutation(self):
        b=self.bundle();p=[self.product()];before=copy.deepcopy((b,p))
        inventory_plan(b,p,{},today=date(2026,10,24));self.assertEqual((b,p),before)
    def test_future_month_and_invalid_assumptions_rejected(self):
        for args in [{'month':'2026-09'},{'month':'2030-01'},{'assumptions':{'p1':float('nan')}},{'assumptions':{'p1':-1}},{'assumptions':{'unknown':3}}]:
            with self.assertRaises(ValueError):self.plan(**args)
        self.assertEqual(next_month(date(2026,12,5)),date(2027,1,1))
    def test_product_specs_and_brand_are_preserved(self):
        self.assertEqual(identity('1 TB WD Hard disk'),identity('WD Hard Disk 1TB'))
        self.assertNotEqual(identity('Image King 88A'),identity('Image Star 88A'))
        self.assertNotEqual(identity('WD 1TB'),identity('WD 2TB'))
    def test_portable_tree_inference(self):
        tree={'left':[-1],'right':[-1],'feature':[-2],'threshold':[-2],'value':[math.log(1.2)]}
        self.assertAlmostEqual(predict({'algorithm':'random_forest','trees':[tree]},[0],100),120)
        self.assertAlmostEqual(predict({'algorithm':'gradient_boosting','trees':[tree],'initial':0,'learning_rate':1},[0],100),120)
        tree['value']=[math.log1p(12)]
        self.assertAlmostEqual(predict({'algorithm':'forest_absolute','trees':[tree]},[0],100),12)
        self.assertAlmostEqual(predict({'algorithm':'ridge_absolute','intercept':math.log1p(12),'coefficients':[0],'mean':[0],'scale':[1]},[0],100),12)
    def test_quantity_groups_and_structured_specs(self):
        self.assertNotEqual(demand_group('Cat6 cable 305 meter coil'),demand_group('Cat6 cable meter'))
        self.assertNotEqual(demand_group('HDMI cable'),demand_group('Cat6 cable meter'))
        h=[{'date':'2026-09-01','value':2}]
        self.assertEqual(len(features(h,'2026-11-01',name='WD 1TB hard disk')),25)
        self.assertNotEqual(features(h,'2026-11-01',name='WD 1TB hard disk'),features(h,'2026-11-01',name='WD 2TB hard disk'))
    def test_failed_quality_gate_blocks_automatic_but_not_explicit_scenario(self):
        b=self.bundle();b['models']['demand']['quality_gate']={'passed':False}
        result=inventory_plan(b,[self.product()],{},today=date(2026,10,24))
        self.assertIsNone(result['total_revenue']);self.assertIsNone(result['rows'][0]['reorder_quantity'])
        self.assertEqual(result['rows'][0]['status'],'DEMAND_MODEL_NOT_ACCEPTED')
        manual=inventory_plan(b,[self.product()],{},today=date(2026,10,24),assumptions={'p1':2})
        self.assertEqual(manual['rows'][0]['status'],'USER_QUANTITY_SCENARIO');self.assertEqual(manual['total_revenue'],300)
        self.assertEqual(manual['rows'][0]['unit_range'],[2,2])
    def test_source_corrections_and_quotation_exclusion(self):
        root=Path(__file__).resolve().parents[2]
        corpus=json.loads((root/'ml/datasets/all-real/corpus.json').read_text())
        self.assertEqual(len(corpus['correction_records']),7)
        self.assertFalse(any(r['source'].endswith('/4MP Camera.pdf') for r in corpus['rows']))
        line=next(r for r in corpus['rows'] if r['source'].endswith('/30.pdf') and r['description']=='POE Plastic Rack')
        self.assertAlmostEqual(line['price'],847.46)
    def test_all_real_sources_and_synthetic_test_separation(self):
        root=Path(__file__).resolve().parents[2]
        corpus=json.loads((root/'ml/datasets/all-real/corpus.json').read_text())
        tests=json.loads((root/'ml/datasets/synthetic-test-20/expected-invoices.json').read_text())
        self.assertEqual(corpus['source_counts'],{'sales':50,'purchases':53})
        self.assertFalse(corpus['missing_documents']);self.assertEqual(corpus['synthetic_rows'],0)
        self.assertEqual(len(tests['documents']),20);self.assertFalse(tests['training_allowed'])
        hashes={d['sha256'] for d in corpus['documents']}
        self.assertFalse(hashes & {d['sha256'] for d in tests['documents']})
        for d in tests['documents']:
            self.assertEqual(hashlib.sha256((root/d['path']).read_bytes()).hexdigest(),d['sha256'])
            from decimal import Decimal
            self.assertEqual(sum(Decimal(l['line_amount']) for l in d['lines']),Decimal(d['subtotal']))
            self.assertEqual(Decimal(d['subtotal'])+Decimal(d['tax_total']),Decimal(d['grand_total']))

if __name__=='__main__':unittest.main()
