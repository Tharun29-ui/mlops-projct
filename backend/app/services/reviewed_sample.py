"""Forecast observed invoice values without inventing unobserved calendar-day sales."""
import hashlib
import json
import math
from pathlib import Path
from app.services.commercial_models import build_insights, effective_line_price
from app.services.demand_model import metrics


def fit_event(values, algorithm):
    if not values or any(not math.isfinite(v) or v<0 for v in values):
        raise ValueError('Invoice values must be finite and nonnegative')
    n=len(values)
    mean=sum(values)/n
    slope=sum((i-(n-1)/2)*(v-mean) for i,v in enumerate(values))/sum((i-(n-1)/2)**2 for i in range(n)) if n>1 else 0
    ordered=sorted(values)
    median=(ordered[n//2]+ordered[(n-1)//2])/2
    ewma=values[0]
    for value in values[1:]:ewma=.4*value+.6*ewma
    return dict(algorithm=algorithm,mean=mean,last=values[-1],slope=slope,observations=n,
                median=median,recent_mean=sum(values[-3:])/len(values[-3:]),ewma=ewma)


def predict_event(model, steps):
    if model['algorithm']=='last_invoice':values=[model['last']]*steps
    elif model['algorithm']=='mean_invoice':values=[model['mean']]*steps
    elif model['algorithm']=='median_invoice':values=[model['median']]*steps
    elif model['algorithm']=='recent_mean_invoice':values=[model['recent_mean']]*steps
    elif model['algorithm']=='ewma_invoice':values=[model['ewma']]*steps
    else:values=[model['mean']+model['slope']*((model['observations']-1)/2+i) for i in range(1,steps+1)]
    return [round(max(0,x),2) for x in values]


def train_invoice_values(sales):
    """Frozen chronological 60/20/20 split; selection never sees final test targets."""
    sales=sorted(sales,key=lambda d:(d['invoice']['invoice_date'],d['invoice']['invoice_number']))
    if len(sales)<10:raise ValueError('At least ten reviewed invoices are required')
    values=[float(d['invoice']['subtotal']) for d in sales]
    test_n=max(2,len(values)//5);val_n=max(2,(len(values)-test_n)//4)
    development=values[:-test_n];training=development[:-val_n]
    scores={a:metrics(development[-val_n:],predict_event(fit_event(training,a),val_n))
            for a in ['mean_invoice','last_invoice','linear_event_trend']}
    # Predeclared robust candidates, selected on expanding past-only folds.
    # Final holdout values never enter these folds or interval calibration.
    candidates=['mean_invoice','last_invoice','linear_event_trend',
                'median_invoice','recent_mean_invoice','ewma_invoice']
    folds={name:[] for name in candidates}
    start=max(4,len(development)//2)
    for origin in range(start,len(development)):
        if sales[origin-1]['invoice']['invoice_date']==sales[origin]['invoice']['invoice_date']:
            continue
        end=origin+1
        while end<len(development) and sales[end]['invoice']['invoice_date']==sales[origin]['invoice']['invoice_date']:
            end+=1
        for name in candidates:
            predicted=predict_event(fit_event(development[:origin],name),end-origin)
            folds[name].extend(dict(train_through=sales[origin-1]['invoice']['invoice_date'],
                 forecast_date=sales[k]['invoice']['invoice_date'],actual=development[k],predicted=predicted[k-origin])
                 for k in range(origin,end))
    cv_scores={name:metrics([r['actual'] for r in rows],[r['predicted'] for r in rows])
               for name,rows in folds.items() if rows}
    if not cv_scores:raise ValueError('More distinct invoice dates are required for past-only validation')
    selected=min(cv_scores,key=lambda name:cv_scores[name]['mae'])
    residuals=sorted(abs(r['actual']-r['predicted']) for r in folds[selected])
    radius=residuals[min(len(residuals)-1,math.ceil(.9*len(residuals))-1)]
    point=predict_event(fit_event(values,selected),1)[0]
    interval={'lower':round(max(0,point-radius),2),'upper':round(point+radius,2),
        'method':'past_only_expanding_window_absolute_errors', 'calibration_rows':len(residuals),
        'status':'EXPLORATORY_RANGE_NOT_GUARANTEED_COVERAGE',
        'note':'Few selected invoices; this range is not a validated probability guarantee.'}

    evaluation=fit_event(development,selected);predictions=predict_event(evaluation,test_n)
    final=fit_event(values,selected)
    return dict(target='Net value (INR) of next recorded invoice in this selected sample',
        frequency='OBSERVED_INVOICE_NOT_CALENDAR_DAY',selected_algorithm=selected,
        training_rows=len(training),validation_rows=val_n,test_rows=test_n,
        selection_strategy='expanding_window_cv_before_final_holdout',
        rolling_validation_scores=cv_scores,rolling_validation_predictions=folds[selected],
        estimate_range=interval,candidate_count=len(candidates),
        validation_scores=scores,test_metrics=metrics(values[-test_n:],predictions),
        last_invoice_baseline_metrics=metrics(values[-test_n:],predict_event(fit_event(development,'last_invoice'),test_n)),
        test_predictions=[dict(date=d['invoice']['invoice_date'],invoice_number=d['invoice']['invoice_number'],
            actual=float(d['invoice']['subtotal']),predicted=p) for d,p in zip(sales[-test_n:],predictions)],
        evaluation_model=evaluation,model=final,next_recorded_invoice_estimate=predict_event(final,1)[0],
        quality_gate='FAILED_BASELINE_CHECK' if metrics(values[-test_n:],predictions)['mae'] > metrics(values[-test_n:],predict_event(fit_event(development,'last_invoice'),test_n))['mae'] else 'LIMITED_SAMPLE_REFERENCE_ONLY',calendar_forecast_available=False)


def run_reviewed_sample():
    path=Path(__file__).resolve().parents[1]/'data/reviewed-real-documents.json'
    raw=path.read_bytes();data=json.loads(raw);docs=data['documents']
    if len(docs)!=20 or data['complete_sales_coverage'] or data['synthetic_rows']:
        raise ValueError('Expected the source-checked, incomplete real 20-document cohort')
    if any(d['review_status']!='SOURCE_CHECKED_FOR_ANALYSIS' for d in docs):
        raise ValueError('Unreviewed invoice in sample')
    purchases=[d for d in docs if d['direction']=='PURCHASE'];sales=[d for d in docs if d['direction']=='SALE']
    if len(purchases)!=10 or len(sales)!=10:raise ValueError('Expected ten invoices per direction')
    rows=[]
    for doc in docs:
        invoice=doc['invoice']
        for line in invoice['lines']:
            if line['is_stock_item'] is False:continue
            # Deliberately keep brand, model and capacity; no HSN-only or family merges.
            key=' '.join(line['description'].casefold().split())
            rows.append(dict(product_id=key,description=line['description'],date=invoice['invoice_date'],
                quantity=float(line['quantity']),price=effective_line_price(line),unit='each',currency='INR',
                party=invoice.get('vendor_name') if doc['direction']=='PURCHASE' else invoice.get('receiver_name'),direction=doc['direction']))
    commercial=build_insights(rows,minimum_margin=0,target_margin=0)
    commercial.update(data_source='reviewed_real_sample',source_documents=20,usable_price_rows=len(rows),synthetic_rows=0,
        review_status='SOURCE_CHECKED_FOR_ANALYSIS',test_note='Partial real sample; stock approvals and daily completeness are not implied.',
        margin_scenario_assumptions={'minimum_margin_percent':0,'target_margin_percent':0,'note':'No business margin settings applied to sample analysis'})
    return dict(data_source='reviewed_real_sample',review_status='SOURCE_CHECKED_FOR_ANALYSIS',
        training_data_sha256=hashlib.sha256(raw).hexdigest(),synthetic_rows=0,complete_sales_coverage=False,
        summary=dict(purchase_invoices=10,sales_invoices=10,reviewed_lines=sum(len(d['invoice']['lines']) for d in docs),
            purchase_total=round(sum(float(d['invoice']['grand_total']) for d in purchases),2),
            sales_total=round(sum(float(d['invoice']['grand_total']) for d in sales),2),
            sales_period=[min(d['invoice']['invoice_date'] for d in sales),max(d['invoice']['invoice_date'] for d in sales)]),
        limitations=['Only 10 sales were selected; missing dates are unknown, not zero sales.',
            'Purchases postdate these sales. Historical opening stock and costs are unconfirmed; stock is not posted.',
            'The forecast concerns the next recorded invoice value, not daily product demand or monthly profit.',
            'Two final test invoices cannot establish dependable accuracy. No accuracy target is guaranteed.',
            'Different brands and unspecified models remain separate; shared HSN codes do not establish identity.'],
        sales_value_model=train_invoice_values(sales),commercial=commercial,
        documents=[dict(filename=d['filename'],direction=d['direction'],sha256=d['sha256'],
            invoice_number=d['invoice']['invoice_number'],date=d['invoice']['invoice_date'],
            subtotal=d['invoice']['subtotal'],tax_total=d['invoice']['tax_total'],total=d['invoice']['grand_total'],
            round_off=d['invoice']['round_off'],lines=d['invoice']['lines'],
            printed_subtotal_difference=round(float(d['invoice']['subtotal'])-sum(float(l['line_amount']) for l in d['invoice']['lines']),2),
            corrected_fields=[c['field'] for c in d['corrections']],ledger_status=d['ledger_status']) for d in docs])
