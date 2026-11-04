"""Bill gross profit -> monthly totals -> one portable monthly Ridge trend."""
from __future__ import annotations

from collections import defaultdict
import calendar
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import re

CENT = Decimal('0.01')


def amount(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('Enter a valid money amount.') from exc
    if not result.is_finite() or abs(result) > Decimal('1000000000000'):
        raise ValueError('Money amounts must be finite and within the supported range.')
    return result.quantize(CENT, rounding=ROUND_HALF_UP)


def month_index(value):
    d = date.fromisoformat(str(value)[:7] + '-01')
    return d.year * 12 + d.month - 1


def month_name(index):
    return f'{index // 12:04d}-{index % 12 + 1:02d}'


def identity(value):
    text = str(value or '').casefold()
    text = re.sub(r'\bhard[\s_-]*(?:disk|drive)\b|\bhdd\b', 'harddisk', text)
    text = re.sub(r'(\d+(?:\.\d+)?)\s*(tb|gb|mb|mp|inch|inches)\b', r'\1\2', text)
    return ' '.join(sorted(re.findall(r'[a-z0-9]+(?:\.[0-9]+)?', text)))


def normalise_invoice(raw, *, filename='', source_reviewed=False):
    day = str(raw.get('invoice_date') or '')[:10]
    date.fromisoformat(day)
    currency = str(raw.get('currency') or 'INR').upper()
    lines = []
    for line in raw.get('lines') or []:
        quantity = Decimal(str(line.get('quantity') or 0))
        if not quantity.is_finite() or quantity < 0:
            raise ValueError('Invoice quantities must be finite and nonnegative.')
        net = line.get('line_amount')
        if net is None:
            net = quantity * Decimal(str(line.get('unit_rate') or 0))
        net = amount(net)
        if net < 0:
            raise ValueError('Credit/return lines need a separate verified profit adjustment.')
        cost = line.get('cost_of_goods_sold')
        lines.append({
            'description': str(line.get('description') or ''),
            'product_id': str(line['product_id']) if line.get('product_id') else None,
            'quantity': str(quantity), 'unit': str(line.get('unit') or '').strip().casefold(),
            'net_amount': str(net), 'is_stock_item': line.get('is_stock_item') is not False,
            'recorded_cost': str(amount(cost)) if cost is not None else None,
        })
    revenue = amount(raw['subtotal']) if raw.get('subtotal') is not None else sum((amount(l['net_amount']) for l in lines), Decimal(0))
    if revenue < 0:
        raise ValueError('Bill net revenue must be nonnegative.')
    number = str(raw.get('invoice_number') or raw.get('id') or filename)
    vendor = str(raw.get('vendor_name') or '')
    key = json.dumps([identity(vendor), re.sub(r'[^a-z0-9]', '', number.casefold()), day, currency])
    bill_id = hashlib.sha256(key.encode()).hexdigest()[:28]
    financial = {'id': bill_id, 'invoice_number': number, 'date': day, 'currency': currency,
                 'net_sales': str(revenue), 'vendor_name': vendor, 'lines': lines}
    fingerprint = hashlib.sha256(json.dumps(financial, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {**financial, 'filename': filename or str(raw.get('filename') or number),
            'source_fingerprint': fingerprint, 'source_reviewed': source_reviewed}


def _unit(value):
    return 'each' if value in {'nos', 'no', 'pcs', 'pieces', 'pc', 'unit', 'units', 'each'} else value


def _keys(line, currency):
    keys = [(identity(line['description']), _unit(line['unit']), currency)]
    if line.get('product_id'):
        keys.insert(0, ('ID:' + line['product_id'], _unit(line['unit']) or 'each', currency))
    return keys


def bill_profits(sales, purchases, confirmations=None):
    confirmations = confirmations or {}
    lookup = defaultdict(list)
    for bill in purchases:
        for line in bill['lines']:
            quantity = Decimal(line['quantity'])
            if not line['is_stock_item'] or quantity <= 0:
                continue
            for key in _keys(line, bill['currency']):
                lookup[key].append((bill['date'], Decimal(line['net_amount']) / quantity, quantity))
    rows = []
    for bill in sorted(sales, key=lambda b: (b['date'], b['id'])):
        revenue = amount(bill['net_sales'])
        record = confirmations.get(bill['id'])
        confirmed = bool(record and record.get('source_fingerprint') == bill['source_fingerprint'])
        missing = []
        total_cost = Decimal(0)
        has_estimated_cost = False
        if confirmed:
            profit = amount(record['gross_profit'])
            if profit > revenue:
                raise ValueError('Bill profit cannot exceed net sales when costs are nonnegative.')
            total_cost = revenue - profit
            basis = 'USER_CONFIRMED_BILL_PROFIT'
        else:
            for line in bill['lines']:
                quantity = Decimal(line['quantity'])
                if not quantity:
                    continue
                if line.get('recorded_cost') is not None:
                    recorded = amount(line['recorded_cost'])
                    if recorded < 0:
                        raise ValueError('Recorded costs must be nonnegative.')
                    total_cost += recorded
                    continue
                if not line['is_stock_item']:
                    missing.append(line['description'] or 'Service cost')
                    continue
                eligible = []
                for key in _keys(line, bill['currency']):
                    eligible = [entry for entry in lookup[key] if entry[0] <= bill['date']]
                    if eligible:
                        break
                if not eligible:
                    missing.append(line['description'] or 'Unmatched item')
                    continue
                latest = max(entry[0] for entry in eligible)
                same_day = [entry for entry in eligible if entry[0] == latest]
                price = sum(p * q for _, p, q in same_day) / sum(q for _, _, q in same_day)
                total_cost += quantity * price
                has_estimated_cost = True
            # Unallocated invoice-level charges must not silently become profit.
            line_revenue = sum((amount(l['net_amount']) for l in bill['lines']), Decimal(0))
            if abs(line_revenue - revenue) > CENT:
                missing.append('Invoice subtotal differs from its line total')
            if not bill['lines']:
                missing.append('No costed lines')
            profit = None if missing else revenue - amount(total_cost)
            basis = 'MISSING_COST' if missing else 'DATED_PURCHASE_COST_ESTIMATE' if has_estimated_cost else 'RECORDED_LINE_COST'
        rows.append({k: bill[k] for k in ['id', 'invoice_number', 'date', 'currency', 'filename', 'source_fingerprint']} | {
            'net_sales': str(revenue), 'cost_of_sales': str(amount(total_cost)) if profit is not None else None,
            'gross_profit': str(amount(profit)) if profit is not None else None,
            'profit_basis': basis, 'missing_cost_items': missing,
            'confirmation_stale': bool(record and not confirmed),
            'profit_note': record.get('note', '') if confirmed else '',
        })
    return rows


def aggregate_months(bills):
    grouped = defaultdict(list)
    for bill in bills:
        grouped[bill['date'][:7]].append(bill)
    result = []
    for month, rows in sorted(grouped.items()):
        known = [r for r in rows if r['gross_profit'] is not None]
        complete = len(known) == len(rows)
        result.append({
            'month': month, 'bill_count': len(rows), 'bills_with_profit': len(known),
            'missing_profit_bills': len(rows) - len(known), 'usable_for_training': complete,
            'net_sales': str(sum((amount(r['net_sales']) for r in rows), Decimal(0))),
            'cost_of_sales': str(sum((amount(r['cost_of_sales']) for r in known), Decimal(0))) if known else None,
            'gross_profit': str(sum((amount(r['gross_profit']) for r in known), Decimal(0))) if known else None,
        })
    return result


def monthly_features(month, origin, seasonal):
    index = month_index(month)
    values = [(index - origin) / 12]
    if seasonal:
        number = index % 12 + 1
        values += [math.sin(2 * math.pi * number / 12), math.cos(2 * math.pi * number / 12),
                   float(calendar.monthrange(index // 12, number)[1])]
    return values


def _solve(matrix, target):
    size = len(target)
    augmented = [list(row) + [target[i]] for i, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda i: abs(augmented[i][col]))
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        if abs(divisor) < 1e-12:
            raise ValueError('Monthly regression cannot be fitted from these values.')
        augmented[col] = [v / divisor for v in augmented[col]]
        for row in range(size):
            if row != col:
                factor = augmented[row][col]
                augmented[row] = [a - factor * b for a, b in zip(augmented[row], augmented[col])]
    return [row[-1] for row in augmented]


def fit_monthly(rows, alpha=1.0):
    if len(rows) < 2:
        raise ValueError('At least two months of bill profits are needed to fit a monthly trend.')
    origin = min(month_index(r['month']) for r in rows)
    seasonal = len(rows) >= 18
    x = [monthly_features(r['month'], origin, seasonal) for r in rows]
    y = [float(amount(r['gross_profit'])) for r in rows]
    width = len(x[0]); mean_y = sum(y) / len(y)
    means = [sum(row[j] for row in x) / len(x) for j in range(width)]
    scales = [math.sqrt(sum((row[j] - means[j]) ** 2 for row in x) / len(x)) or 1.0 for j in range(width)]
    z = [[(value - center) / scale for value, center, scale in zip(row, means, scales)] for row in x]
    matrix = [[sum(row[i] * row[j] for row in z) + (alpha if i == j else 0) for j in range(width)] for i in range(width)]
    target = [sum(row[i] * (value - mean_y) for row, value in zip(z, y)) for i in range(width)]
    return {'algorithm': 'monthly_ridge_regression', 'alpha': alpha, 'origin_month': month_name(origin),
            'intercept': mean_y, 'coefficients': _solve(matrix, target), 'means': means, 'scales': scales,
            'seasonal_features': seasonal,
            'feature_names': ['time_trend', 'annual_sine', 'annual_cosine', 'days_in_month'] if seasonal else ['time_trend'],
            'training_months': len(rows), 'trained_through': max(r['month'] for r in rows)}


def predict_month(model, month):
    features = monthly_features(month, month_index(model['origin_month']), model['seasonal_features'])
    value = model['intercept'] + sum(coef * (value - center) / scale for coef, value, center, scale in zip(model['coefficients'], features, model['means'], model['scales']))
    if not math.isfinite(value):
        raise ValueError('The monthly model produced a nonfinite value.')
    return str(amount(value))


def evaluate_monthly(rows):
    if len(rows) < 3:
        return {'test_months': 0, 'mae': None, 'wape': None, 'baseline_wape': None,
                'predictions': [], 'status': 'MORE_MONTHS_NEEDED_FOR_EVALUATION'}
    test_count = max(1, len(rows) // 5)
    train, test = rows[:-test_count], rows[-test_count:]
    model = fit_monthly(train)
    baseline = float(amount(train[-1]['gross_profit']))
    predictions = [{'month': r['month'], 'actual': r['gross_profit'], 'predicted': predict_month(model, r['month']),
                    'baseline': str(amount(baseline))} for r in test]
    absolute = sum(abs(float(r['actual']) - float(r['predicted'])) for r in predictions)
    baseline_error = sum(abs(float(r['actual']) - baseline) for r in predictions)
    total = sum(abs(float(r['actual'])) for r in predictions)
    return {'test_months': test_count, 'training_months': len(train), 'mae': round(absolute / test_count, 2),
            'wape': absolute / total if total else None, 'baseline_wape': baseline_error / total if total else None,
            'predictions': predictions, 'evaluation_model': model,
            'status': 'BETTER_THAN_LAST_MONTH_BASELINE' if absolute <= baseline_error else 'WORSE_THAN_LAST_MONTH_BASELINE',
            'protocol': 'Chronological development backtest with fixed Ridge alpha 1. Final months are excluded from the evaluation fit. Feature development inspected this backtest; fresh future validation remains required.'}


def build_profit_report(sales, purchases, *, source, confirmations=None, horizon=1, start_month=None, today=None):
    if source not in {'real', 'synthetic'} or horizon not in {1, 3, 6}:
        raise ValueError('Choose real or synthetic invoices and 1, 3 or 6 forecast months.')
    if any(b.get('currency') != 'INR' for b in [*sales, *purchases]):
        raise ValueError('This profit workspace supports INR bills only; currencies cannot be combined.')
    today = today or date.today()
    start = start_month or month_name(month_index(today.isoformat()) + 1)
    offset = month_index(start) - month_index(today.isoformat())
    if not 1 <= offset <= 12:
        raise ValueError('Choose a future start month within the next twelve months.')
    bills = bill_profits(sales, purchases, confirmations)
    monthly = aggregate_months(bills)
    eligible = [r for r in monthly if r['usable_for_training']]
    if eligible and month_index(start) <= month_index(eligible[-1]['month']):
        raise ValueError('The forecast must start after the final historical profit month.')
    evaluation = evaluate_monthly(eligible)
    model = fit_monthly(eligible) if len(eligible) >= 2 else None
    predictions = [{'month': month_name(month_index(start) + i),
                    'gross_profit': predict_month(model, month_name(month_index(start) + i))} for i in range(horizon)] if model else []
    warnings = []
    if source == 'real':
        warnings.append('Real source bills are selected records. This estimates profit for the included recorded bills, not proven total business profit.')
    else:
        warnings.append('Synthetic invoices are simulated test data; their results do not establish real-business accuracy.')
    if any(r['missing_profit_bills'] for r in monthly):
        warnings.append('Months with missing bill profits are excluded from model training. Missing costs are not treated as zero.')
    if len(eligible) < 6:
        warnings.append('There are fewer than six usable months. Any fitted forecast is exploratory.')
    if eligible and month_index(start) - month_index(eligible[-1]['month']) > 1:
        warnings.append('The forecast extrapolates across a gap after the last historical month; unknown months are not filled with zero.')
    warnings.append('Dated purchase rates estimate cost of sold goods; they are not a FIFO batch-cost ledger. Gross profit excludes operating expenses and tax.')
    evidence = {'source': source, 'model': model, 'bill_values': [(b['id'], b['source_fingerprint'], b['gross_profit']) for b in bills]}
    version = 'monthly-bill-profit-v1-' + hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()[:12]
    return {'source': source, 'synthetic': source == 'synthetic', 'currency': 'INR', 'model_version': version,
            'target': 'Monthly sum of gross profits from included bills', 'algorithm': 'Monthly Ridge Regression',
            'model': model, 'evaluation': evaluation, 'bills': bills, 'monthly': monthly, 'forecast': predictions,
            'bill_count': len(bills), 'bills_with_profit': sum(b['gross_profit'] is not None for b in bills),
            'usable_months': len(eligible), 'start_month': start, 'horizon_months': horizon,
            'status': 'FORECAST_READY' if model else 'ENTER_BILL_PROFITS', 'warnings': warnings,
            'total_forecast_profit': str(sum((amount(r['gross_profit']) for r in predictions), Decimal(0))) if predictions else None}
