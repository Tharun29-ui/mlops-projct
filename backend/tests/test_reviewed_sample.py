import copy
import csv
import hashlib
import json
import unittest
from decimal import Decimal
from pathlib import Path
from app.services.reviewed_sample import run_reviewed_sample, train_invoice_values, predict_event


class ReviewedRealSampleTests(unittest.TestCase):
    def setUp(self):
        self.report=run_reviewed_sample()

    def test_twenty_real_source_checked_documents_no_heldout_or_demo(self):
        r=self.report
        self.assertEqual(r['summary']['purchase_invoices'],10)
        self.assertEqual(r['summary']['sales_invoices'],10)
        self.assertEqual(r['synthetic_rows'],0)
        self.assertFalse(r['complete_sales_coverage'])
        self.assertEqual(len({d['sha256'] for d in r['documents']}),20)
        root=Path(__file__).resolve().parents[2]
        with (root/'ml/datasets/source-manifests/invoice_split.csv').open(encoding='utf-8-sig') as f:
            heldout={Path(d['relative_path']).name for d in csv.DictReader(f) if d['split']=='test'}
        self.assertFalse({d['filename'] for d in r['documents']}&heldout)
        for d in r['documents']:
            source=root/'data/sales_test'/('sales' if d['direction']=='SALE' else 'purchase')/d['filename']
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),d['sha256'])

    def test_reviewed_totals_and_hsn_reconcile(self):
        for d in self.report['documents']:
            # AP-10 prints a subtotal one paise above the sum of its printed lines.
            # Preserve both source values; do not alter an amount to make it reconcile.
            self.assertLessEqual(abs(sum(Decimal(l['line_amount']) for l in d['lines'])-Decimal(d['subtotal'])),Decimal('.02'))
            self.assertEqual(Decimal(d['subtotal'])+Decimal(d['tax_total'])+Decimal(d['round_off']),Decimal(d['total']))
            self.assertTrue(all(l['hsn_sac'].isdigit() and Decimal(l['quantity'])>0 for l in d['lines']))
            self.assertEqual(d['ledger_status'],'NOT_POSTED')

    def test_no_fake_daily_rows_or_model_selection_leakage(self):
        root=Path(__file__).resolve().parents[1]
        docs=json.loads((root/'app/data/reviewed-real-documents.json').read_text())['documents']
        sales=[d for d in docs if d['direction']=='SALE']
        before=train_invoice_values(sales)
        changed=copy.deepcopy(sales)
        for d in changed:
            if d['invoice']['invoice_date']>='2026-06-17':d['invoice']['subtotal']='9999999'
        after=train_invoice_values(changed)
        self.assertEqual(before['selected_algorithm'],after['selected_algorithm'])
        self.assertEqual(before['evaluation_model'],after['evaluation_model'])
        self.assertEqual(before['test_predictions'][0]['predicted'],after['test_predictions'][0]['predicted'])
        self.assertEqual(before['training_rows']+before['validation_rows']+before['test_rows'],10)
        self.assertFalse(before['calendar_forecast_available'])
        restored=json.loads(json.dumps(before['model']))
        self.assertEqual(predict_event(restored,1)[0],before['next_recorded_invoice_estimate'])

    def test_poor_result_is_not_promoted_as_accurate(self):
        model=self.report['sales_value_model']
        if model['test_metrics']['mae']>model['last_invoice_baseline_metrics']['mae']:
            self.assertEqual(model['quality_gate'],'FAILED_BASELINE_CHECK')

    def test_rolling_validation_uses_only_past_dates_and_compares_six_candidates(self):
        model=self.report['sales_value_model']
        self.assertEqual(model['candidate_count'],6)
        self.assertEqual(len(model['rolling_validation_scores']),6)
        for row in model['rolling_validation_predictions']:
            self.assertLess(row['train_through'],row['forecast_date'])
        self.assertEqual(model['estimate_range']['status'],'EXPLORATORY_RANGE_NOT_GUARANTEED_COVERAGE')
        self.assertGreaterEqual(model['estimate_range']['lower'],0)
        self.assertGreaterEqual(model['estimate_range']['upper'],model['estimate_range']['lower'])


if __name__=='__main__':unittest.main()
