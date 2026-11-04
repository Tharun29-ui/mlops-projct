import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from urllib.parse import urlsplit
from mangum import Mangum
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.db import Base, get_db
from app.main import app
from app.models import AppSetting, Invoice, Product, StockMovement
from app.services.bill_profit import amount, normalise_invoice, bill_profits, aggregate_months, fit_monthly, predict_month, evaluate_monthly, build_profit_report, monthly_features, month_index
from app.services import profit_state


def bill(day='2026-01-05',number='B1',quantity='2',net='120.00',**kwargs):
    raw={'invoice_number':number,'invoice_date':day,'vendor_name':'Shop','currency':'INR','subtotal':net,
         'lines':[{'description':'Monitor model A','quantity':quantity,'unit':'each','line_amount':net}]}
    raw.update(kwargs)
    return normalise_invoice(raw)


class BillProfitTests(unittest.TestCase):
    def test_bill_to_month_reconciles_and_purchase_not_expensed_whole(self):
        sales=[bill(number='S1'),bill(number='S2',day='2026-01-25',net='160.00')]
        purchase=bill(number='P1',day='2026-01-01',quantity='10',net='400.00')
        rows=bill_profits(sales,[purchase]);month=aggregate_months(rows)[0]
        self.assertEqual([r['gross_profit'] for r in rows],['40.00','80.00'])
        self.assertEqual(month['gross_profit'],'120.00')
        self.assertEqual(month['cost_of_sales'],'160.00')
        self.assertEqual(purchase['net_sales'],'400.00')

    def test_later_purchase_cannot_supply_historical_cost(self):
        rows=bill_profits([bill()],[bill(day='2026-02-01',number='P')])
        self.assertIsNone(rows[0]['gross_profit'])
        self.assertFalse(aggregate_months(rows)[0]['usable_for_training'])

    def test_missing_service_cost_and_units_are_not_zero_or_guessed(self):
        service=bill(lines=[{'description':'Installation','quantity':1,'line_amount':120,'is_stock_item':False}])
        self.assertIsNone(bill_profits([service],[])[0]['gross_profit'])
        different_unit=bill(day='2026-01-01',lines=[{'description':'Monitor model A','quantity':1,'line_amount':50,'unit':'box'}])
        self.assertIsNone(bill_profits([bill()],[different_unit])[0]['gross_profit'])

    def test_hsn_does_not_merge_different_products(self):
        sale=bill(lines=[{'description':'Monitor model B','quantity':2,'line_amount':120,'unit':'each','hsn_sac':'8528'}])
        self.assertIsNone(bill_profits([sale],[bill(day='2026-01-01')])[0]['gross_profit'])

    def test_user_confirmed_profit_is_fingerprint_bound_and_loss_allowed(self):
        sale=bill();record={'gross_profit':'-12.34','source_fingerprint':sale['source_fingerprint'],'note':'Verified ledger'}
        result=bill_profits([sale],[],{sale['id']:record})[0]
        self.assertEqual(result['gross_profit'],'-12.34');self.assertEqual(result['cost_of_sales'],'132.34')
        record['source_fingerprint']='0'*64
        result=bill_profits([sale],[],{sale['id']:record})[0]
        self.assertIsNone(result['gross_profit']);self.assertTrue(result['confirmation_stale'])

    def test_explicit_recorded_cost_and_invoice_subtotal_reconcile(self):
        sale=bill(lines=[{'description':'Service','quantity':1,'line_amount':120,'is_stock_item':False,'cost_of_goods_sold':'75.25'}])
        self.assertEqual(bill_profits([sale],[])[0]['gross_profit'],'44.75')
        sale=bill(net='125.00')
        sale['lines'][0]['net_amount']='120.00'
        self.assertIsNone(bill_profits([sale],[bill(day='2026-01-01')])[0]['gross_profit'])

    def test_decimal_monthly_sum_does_not_accumulate_binary_error(self):
        rows=[{'date':'2026-01-01','gross_profit':'0.10','net_sales':'1.00','cost_of_sales':'0.90'} for _ in range(3)]
        self.assertEqual(aggregate_months(rows)[0]['gross_profit'],'0.30')
        for bad in ['NaN','Infinity','-Infinity','1e999']:
            with self.assertRaises(ValueError):amount(bad)

    def test_missing_months_are_not_filled_and_losses_are_preserved(self):
        rows=[{'month':'2026-01','gross_profit':'-100'},{'month':'2026-03','gross_profit':'-200'}]
        model=fit_monthly(rows)
        self.assertLess(float(predict_month(model,'2026-04')),0)
        self.assertEqual(model['training_months'],2)
        self.assertEqual(evaluate_monthly(rows)['test_months'],0)

    def test_portable_ridge_matches_sklearn(self):
        import numpy as np
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler
        rows=[{'month':f'{2024+i//12:04d}-{i%12+1:02d}','gross_profit':str(1000+i*12+(i%4)*30)} for i in range(24)]
        model=fit_monthly(rows);origin=month_index(rows[0]['month'])
        x=np.array([monthly_features(r['month'],origin,True) for r in rows]);y=np.array([float(r['gross_profit']) for r in rows])
        scaler=StandardScaler().fit(x);reference=Ridge(alpha=1).fit(scaler.transform(x),y)
        expected=reference.predict(scaler.transform([monthly_features('2026-01',origin,True)]))[0]
        self.assertAlmostEqual(float(predict_month(model,'2026-01')),expected,places=2)

    def test_test_values_do_not_enter_evaluation_fit(self):
        rows=[{'month':f'2026-{i:02d}','gross_profit':str(i*100)} for i in range(1,11)]
        expected=evaluate_monthly(rows)
        changed=copy.deepcopy(rows);changed[-1]['gross_profit']='99999999'
        actual=evaluate_monthly(changed)
        self.assertEqual([r['predicted'] for r in expected['predictions']],[r['predicted'] for r in actual['predictions']])
        self.assertLess(actual['evaluation_model']['trained_through'],actual['predictions'][0]['month'])

    def test_real_corpus_is_not_given_synthetic_profits(self):
        data=json.loads((Path(__file__).resolve().parents[1]/'app/data/bill-profit-ledger.json').read_text(encoding='utf-8'))
        if not data['real']['sales']:
            self.skipTest('Private real-invoice corpus is intentionally not distributed.')
        report=build_profit_report(**{k:data['real'][k] for k in ['sales','purchases']},source='real',today=date(2026,11,4))
        self.assertEqual(report['bill_count'],48);self.assertEqual(report['bills_with_profit'],0)
        self.assertEqual(report['forecast'],[]);self.assertFalse(report['synthetic'])

    def test_complete_synthetic_bill_pipeline_and_source_nonmutation(self):
        data=json.loads((Path(__file__).resolve().parents[1]/'app/data/bill-profit-ledger.json').read_text(encoding='utf-8'))
        before=copy.deepcopy(data)
        report=build_profit_report(**{k:data['synthetic'][k] for k in ['sales','purchases']},source='synthetic',today=date(2026,11,4),horizon=3)
        self.assertEqual(report['bill_count'],730);self.assertEqual(report['usable_months'],24)
        self.assertEqual(len(report['forecast']),3)
        self.assertEqual(sum(Decimal(r['gross_profit']) for r in report['bills']),sum(Decimal(r['gross_profit']) for r in report['monthly']))
        self.assertEqual(before,data)
        self.assertEqual(report['evaluation']['test_months'],4)


class ProfitApiTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine);self.db=Session(self.engine)
        app.dependency_overrides[get_db]=lambda:self.db
        from app.api.routes import forecasts
        original_catalog = forecasts._catalog
        def isolated_catalog(db, source):
            if source == 'real':
                return [bill(day='2026-04-05', number='UNIT-S1'), bill(day='2026-05-05', number='UNIT-S2')], []
            return original_catalog(db, source)
        self.catalog_patch = patch.object(forecasts, '_catalog', side_effect=isolated_catalog)
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)

    def tearDown(self):
        app.dependency_overrides.clear();self.db.close();self.engine.dispose()

    def call(self,method,path,payload=None):
        parts=urlsplit(path)
        response=Mangum(app,lifespan='off')({'version':'2.0','routeKey':'ANY /api/{proxy+}','rawPath':parts.path,'rawQueryString':parts.query,
            'headers':{'content-type':'application/json'},'requestContext':{'http':{'method':method,'path':parts.path,'sourceIp':'127.0.0.1','protocol':'HTTP/1.1'},'stage':'$default','requestId':'profit-test'},
            'body':json.dumps(payload) if payload else None,'isBase64Encoded':False},{})
        return response['statusCode'],json.loads(response['body'])

    def test_synthetic_route_never_reads_real_ledger_or_writes(self):
        with patch('app.api.routes.forecasts.store.list_invoices',side_effect=AssertionError('No real data reads')),patch('app.services.profit_state.load',side_effect=AssertionError('No real labels')):
            status,result=self.call('GET','/api/v1/forecasts/profit?source=synthetic')
        self.assertEqual(status,200);self.assertTrue(result['synthetic'])
        self.assertEqual(self.db.scalars(select(AppSetting)).all(),[])

    def test_confirmations_fill_months_and_train_only_in_isolated_db(self):
        _,before=self.call('GET','/api/v1/forecasts/profit?source=real')
        # Deliberately artificial test-only labels; never written to the actual project database.
        selected=[b for b in before['bills'] if b['date'][:7] in {'2026-04','2026-05'}]
        values=[{'bill_id':b['id'],'source_fingerprint':b['source_fingerprint'],'gross_profit':'100.00','note':'Isolated unit-test fixture'} for b in selected]
        status,result=self.call('POST','/api/v1/forecasts/profit/confirmations',{'expected_revision':None,'confirm_actual_values':True,'values':values})
        self.assertEqual(status,200);self.assertEqual(result['usable_months'],2);self.assertEqual(len(result['forecast']),1)
        self.assertTrue(result['label_revision']);self.assertEqual(len(self.db.scalars(select(AppSetting)).all()),1)
        for model in [Invoice,Product,StockMovement]:self.assertEqual(self.db.scalars(select(model)).all(),[])
        status,_=self.call('POST','/api/v1/forecasts/profit/confirmations',{'expected_revision':None,'confirm_actual_values':True,'values':values})
        self.assertEqual(status,409)

    def test_input_requires_confirmation_valid_fingerprint_and_money(self):
        _,before=self.call('GET','/api/v1/forecasts/profit?source=real');bill=before['bills'][0]
        entry={'bill_id':bill['id'],'source_fingerprint':bill['source_fingerprint'],'gross_profit':'10','note':'Verified record'}
        for change,confirmed,expected in [({},False,422),({'source_fingerprint':'0'*64},True,409),({'gross_profit':'NaN'},True,422),({'gross_profit':'999999999999'},True,422),({'note':' '},True,422)]:
            status,_=self.call('POST','/api/v1/forecasts/profit/confirmations',{'confirm_actual_values':confirmed,'values':[{**entry,**change}]})
            self.assertEqual(status,expected)
        self.assertEqual(self.db.scalars(select(AppSetting)).all(),[])

    def test_invalid_source_and_retired_endpoints(self):
        self.assertEqual(self.call('GET','/api/v1/forecasts/profit?source=mixed')[0],422)
        for path in ['/api/v1/intelligence/synthetic-plan','/api/v1/intelligence/monthly-plan']:
            self.assertEqual(self.call('GET',path)[0],404)
        self.assertEqual(self.call('POST','/api/v1/forecasts/from-approved-sales',{})[0],404)

    def test_browser_query_parameters_accept_numeric_horizons(self):
        status,result=self.call('GET','/api/v1/forecasts/profit?source=synthetic&horizon=3')
        self.assertEqual(status,200);self.assertEqual(len(result['forecast']),3)
        self.assertEqual(self.call('GET','/api/v1/forecasts/profit?source=synthetic&horizon=2')[0],422)


class ProfitCloudStateTests(unittest.TestCase):
    def test_cloud_confirmations_use_only_the_separate_guarded_setting(self):
        class ConditionalFailure(Exception):pass
        class Table:
            item=None
            def __init__(self):self.writes=[];self.meta=SimpleNamespace(client=SimpleNamespace(exceptions=SimpleNamespace(ConditionalCheckFailedException=ConditionalFailure)))
            def get_item(self,**kwargs):
                assert kwargs['ConsistentRead'] is True
                assert kwargs['Key']=={'pk':profit_state.KEY,'sk':'DATA'}
                return {'Item':copy.deepcopy(self.item)} if self.item else {}
            def put_item(self,**kwargs):
                self.writes.append(kwargs);self.item=kwargs['Item']
        table=Table();row={'bill_id':'a','source_fingerprint':'a'*64,'gross_profit':'-5.25','note':'Verified test record'}
        with patch.object(profit_state,'get_settings',return_value=SimpleNamespace(business_data_table_name='test')),patch.object(profit_state.store,'table',return_value=table):
            first=profit_state.save(None,[row],None)
            self.assertEqual(profit_state.load(None)['inputs']['a']['gross_profit'],'-5.25')
            second=profit_state.save(None,[{**row,'gross_profit':'0.00'}],first['revision'])
            self.assertEqual(second['history'][-1]['previous']['gross_profit'],'-5.25')
            with self.assertRaises(ValueError):profit_state.save(None,[row],first['revision'])
        self.assertEqual(len(table.writes),2)
        self.assertEqual(table.writes[0]['Item']['pk'],profit_state.KEY)
        self.assertEqual(table.writes[1]['ConditionExpression'],'#payload = :previous')
        self.assertNotIn('INVOICE#',json.dumps(table.writes))


if __name__=='__main__':unittest.main()
