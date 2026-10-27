import json
import math
import unittest
from datetime import date, timedelta
from app.services.demand_model import train, predict


class DemandModelTests(unittest.TestCase):
    def test_fortnight_pattern_is_selected_without_test_target_selection(self):
        series=[(date(2026,1,1)+timedelta(days=i), (2 if (i//7)%2==0 else 7) if i%7==0 else 0)
                for i in range(98)]
        result=train(series,horizon=30)
        self.assertIn('weekday_mean',result['metrics']['validation_mae'])
        self.assertIn('seasonal_naive_14',result['metrics']['validation_mae'])
        self.assertEqual(result['metrics']['wape'],0)
        self.assertEqual(predict(json.loads(json.dumps(result['model'])),30),result['predictions'])

    def test_long_period_requires_two_complete_training_cycles(self):
        result=train(self.series()[:40])
        self.assertNotIn('seasonal_naive_28',result['metrics']['validation_mae'])
    def test_year_horizon_keeps_separate_test_and_reproducible_inference(self):
        result=train(self.series(),horizon=365)
        short=train(self.series(),horizon=7)
        self.assertEqual(len(result['predictions']),365)
        self.assertEqual(result['metrics'],short['metrics'])
        self.assertEqual(predict(json.loads(json.dumps(result['model'])),365),result['predictions'])
        self.assertTrue(all(math.isfinite(value) and value>=0 for value in result['predictions']))

    def series(self):
        return [(date(2026,1,1)+timedelta(days=i),10+.05*i+3*math.sin(2*math.pi*i/7))
                for i in range(120)]

    def test_saved_model_reproduces_predictions(self):
        result=train(self.series())
        restored=json.loads(json.dumps(result['model']))
        self.assertEqual(predict(restored,7),result['predictions'])
        self.assertEqual(result['metrics']['holdout_observations'],14)
        self.assertIn('ridge_lag_calendar',result['metrics']['validation_mae'])

    def test_test_targets_do_not_influence_model_selection(self):
        series=self.series()
        original=train(series)
        changed=train(series[:-14]+[(day,500) for day,_ in series[-14:]])
        self.assertEqual(original['metrics']['validation_mae'],changed['metrics']['validation_mae'])
        self.assertEqual(original['model']['algorithm'],changed['model']['algorithm'])
        self.assertGreater(changed['metrics']['mae'],original['metrics']['mae'])

    def test_rejects_gaps_and_nonfinite_sales(self):
        with self.assertRaises(ValueError): train(self.series()[::2])
        series=self.series(); series[-1]=(series[-1][0],float('nan'))
        with self.assertRaises(ValueError): train(series)

    def test_short_history_is_labeled_baseline(self):
        result=train(self.series()[:5])
        self.assertTrue(result['metrics']['limited_history'])
        self.assertEqual(result['model']['algorithm'],'recent_mean')
