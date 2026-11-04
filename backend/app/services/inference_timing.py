"""Bounded, warm prediction benchmarking; no fitting or I/O inside the loop."""
from datetime import datetime, timezone
from math import ceil
from statistics import mean, median
from time import perf_counter_ns

from app.services.bill_profit import predict_month

SAMPLES = 1000
WARMUP_SAMPLES = 20


def benchmark_prediction(model, month, *, samples=SAMPLES, warmup=WARMUP_SAMPLES):
    if not 1 <= samples <= 5000 or not 0 <= warmup <= 100:
        raise ValueError('The prediction benchmark must use a bounded sample count.')
    for _ in range(warmup):
        predict_month(model, month)
    durations = []
    for _ in range(samples):
        start = perf_counter_ns()
        predict_month(model, month)
        durations.append((perf_counter_ns() - start) / 1_000_000)
    ordered = sorted(durations)
    return {
        'samples': samples,
        'warmup_samples': warmup,
        'median_ms': median(durations),
        'mean_ms': mean(durations),
        'p95_ms': ordered[ceil(samples * .95) - 1],
        'min_ms': ordered[0],
        'max_ms': ordered[-1],
        'prediction_month': month,
        'unit': 'ms',
        'scope': 'One monthly prediction from an already fitted model, including calendar features and money formatting. Excludes fitting, data loading and network.',
        'clock': 'perf_counter_ns',
        'measurement_overhead_included': True,
    }


def benchmark_report(prepare_report):
    started = perf_counter_ns()
    report = prepare_report()
    prepared = perf_counter_ns()
    timing = benchmark_prediction(report['model'], report['start_month']) if report['model'] else None
    finished = perf_counter_ns()
    return {
        'status': 'MEASURED' if timing else 'MODEL_NOT_READY',
        'source': report['source'],
        'synthetic': report['synthetic'],
        'model_version': report['model_version'],
        'model_status': report['status'],
        'algorithm': report['algorithm'],
        'usable_months': report['usable_months'],
        'start_month': report['start_month'],
        'horizon_months': report['horizon_months'],
        'preparation_ms': (prepared - started) / 1_000_000,
        'server_work_ms': (finished - started) / 1_000_000,
        'prediction': timing,
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'preparation_scope': 'Bill loading, profit calculation, monthly aggregation, fitting, evaluation and initial forecast generation.',
        'server_work_scope': 'Route work through completion of the timing loop. Excludes Lambda initialization, HTTP response serialization, API Gateway and network.',
        'message': 'Measured on the selected data source; repeated timing requests can vary.' if timing else 'At least two complete recorded profit months are needed before this model can predict or be timed.',
        'cold_start_measured_separately': False,
        'latency_guarantee': False,
    }
