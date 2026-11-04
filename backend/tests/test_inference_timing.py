import copy
import json
from unittest.mock import patch
import unittest
from app.services.bill_profit import fit_monthly, predict_month
from app.services.inference_timing import benchmark_prediction, benchmark_report
import test_bill_profit as profit_tests


class InferenceTimingTests(unittest.TestCase):
    def test_units_statistics_and_warmup_are_separate(self):
        # Five timed predictions: 1, 2, 3, 4 and 5 ms. One untimed warm-up.
        clock=[0,1_000_000,2_000_000,4_000_000,5_000_000,8_000_000,9_000_000,13_000_000,14_000_000,19_000_000]
        with patch('app.services.inference_timing.predict_month',return_value='12.00') as prediction,patch('app.services.inference_timing.perf_counter_ns',side_effect=clock):
            result=benchmark_prediction({},'2026-12',samples=5,warmup=1)
        self.assertEqual(prediction.call_count,6)
        self.assertEqual(result['median_ms'],3)
        self.assertEqual(result['mean_ms'],3)
        self.assertEqual(result['p95_ms'],5)
        self.assertEqual(result['min_ms'],1)
        self.assertEqual(result['unit'],'ms')

    def test_real_prediction_is_repeated_without_fitting_or_model_mutation(self):
        model=fit_monthly([{'month':'2026-01','gross_profit':'100'},{'month':'2026-02','gross_profit':'200'}])
        before=copy.deepcopy(model);expected=predict_month(model,'2026-12')
        with patch('app.services.inference_timing.predict_month',wraps=predict_month) as prediction:
            result=benchmark_prediction(model,'2026-12',samples=4,warmup=2)
        self.assertEqual(prediction.call_count,6)
        self.assertEqual(model,before)
        self.assertEqual(predict_month(model,'2026-12'),expected)
        self.assertGreaterEqual(result['min_ms'],0)

    def test_report_preparation_is_outside_prediction_timing(self):
        report={'model':{'algorithm':'test-model'},'source':'synthetic','synthetic':True,'model_version':'v1','status':'FORECAST_READY','algorithm':'Monthly Ridge Regression','usable_months':24,'start_month':'2026-12','horizon_months':3}
        with patch('app.services.inference_timing.perf_counter_ns',side_effect=[0,2_000_000,3_000_000]),patch('app.services.inference_timing.benchmark_prediction',return_value={'median_ms':.01}) as benchmark:
            result=benchmark_report(lambda:report)
        benchmark.assert_called_once_with(report['model'],'2026-12')
        self.assertEqual(result['status'],'MEASURED')
        self.assertEqual(result['prediction']['median_ms'],.01)
        self.assertEqual(result['preparation_ms'],2)
        self.assertEqual(result['server_work_ms'],3)

    def test_benchmark_bounds(self):
        for samples,warmup in [(0,0),(5001,0),(1,101)]:
            with self.assertRaises(ValueError):benchmark_prediction({},'2026-12',samples=samples,warmup=warmup)


class InferenceApiTests(unittest.TestCase):
    setUp=profit_tests.ProfitApiTests.setUp
    tearDown=profit_tests.ProfitApiTests.tearDown
    call=profit_tests.ProfitApiTests.call
    def test_selected_synthetic_model_is_measured_without_real_state(self):
        with patch('app.api.routes.forecasts.store.list_invoices',side_effect=AssertionError('No real reads')),patch('app.services.profit_state.load',side_effect=AssertionError('No real labels')):
            status,result=self.call('GET','/api/v1/forecasts/profit/benchmark?source=synthetic&horizon=3')
        self.assertEqual(status,200)
        self.assertEqual(result['status'],'MEASURED')
        self.assertTrue(result['synthetic'])
        self.assertEqual(result['prediction']['samples'],1000)
        self.assertEqual(result['horizon_months'],3)
        self.assertNotIn('bills',result)

    def test_real_insufficient_history_has_no_timing_or_synthetic_fallback(self):
        with patch('app.services.inference_timing.benchmark_prediction') as benchmark:
            status,result=self.call('GET','/api/v1/forecasts/profit/benchmark?source=real')
        self.assertEqual(status,200)
        benchmark.assert_not_called()
        self.assertEqual(result['status'],'MODEL_NOT_READY')
        self.assertFalse(result['synthetic'])
        self.assertIsNone(result['prediction'])
        self.assertEqual(self.call('GET','/api/v1/forecasts/profit/benchmark?source=mixed')[0],422)


if __name__=='__main__':unittest.main()
