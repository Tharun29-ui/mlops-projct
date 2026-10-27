"""Auditable price regressions and decision support; never place orders or change stock."""
import math
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from app.services.demand_model import _solve, metrics


def effective_line_price(line):
    """Use the recorded net line amount for tax-consistent price comparisons."""
    try:
        quantity = Decimal(str(line.get('quantity') or 0))
        amount = Decimal(str(line['line_amount'])) if line.get('line_amount') is not None else None
        rate = Decimal(str(line['unit_rate'])) if line.get('unit_rate') is not None else None
        if not quantity.is_finite() or quantity <= 0:
            return None
        if amount is not None and amount.is_finite() and amount >= 0:
            return float((amount / quantity).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        return float(rate) if rate is not None and rate.is_finite() and rate >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _features(row, origin, parties):
    day=date.fromisoformat(str(row['date'])[:10])
    return [1.0,(day-origin).days/30,math.log1p(float(row.get('quantity',1)))] + [
        float(row.get('party','')==party) for party in parties]


def _fit(rows, algorithm):
    origin=date.fromisoformat(str(rows[0]['date'])[:10])
    parties=sorted({r.get('party','') for r in rows})
    model={'algorithm':algorithm,'origin':origin.isoformat(),'parties':parties,
           'last_price':float(rows[-1]['price'])}
    if algorithm=='ridge_price_regression':
        x=[_features(r,origin,parties) for r in rows];n=len(x[0])
        matrix=[[sum(r[i]*r[j] for r in x)+(1 if i==j and i else 0)
                 for j in range(n)] for i in range(n)]
        model['coefficients']=_solve(matrix,[sum(r[i]*float(y['price']) for r,y in zip(x,rows)) for i in range(n)])
    return model


def predict_price(model, day, quantity=1, party=''):
    if model['algorithm']=='last_observed_price':return model['last_price']
    row={'date':day,'quantity':quantity,'party':party}
    value=sum(a*b for a,b in zip(model['coefficients'],_features(row,date.fromisoformat(model['origin']),model['parties'])))
    if not math.isfinite(value):raise ValueError('Nonfinite predicted price')
    return round(max(0,value),2)


def _price_key(row):
    return str(row['product_id'])+'|'+str(row.get('unit') or 'UNRECORDED')


def _latest_prices(rows):
    """Aggregate same-day observations, rather than choosing by file order."""
    groups=defaultdict(list)
    for row in rows:groups[_price_key(row)].append(row)
    latest={}
    for key,observations in groups.items():
        day=max(str(r['date'])[:10] for r in observations)
        current=[r for r in observations if str(r['date'])[:10]==day]
        quantity=sum(float(r.get('quantity',1)) for r in current)
        latest[key]={'price':sum(float(r['price'])*float(r.get('quantity',1)) for r in current)/quantity,
                     'date':day,'quantity':quantity/len(current)}
    return latest


def _fit_shared_prices(rows, algorithm):
    origin=date.fromisoformat(min(str(r['date'])[:10] for r in rows))
    latest=_latest_prices(rows)
    model={'algorithm':algorithm,'origin':origin.isoformat(),'products':sorted(latest),
           'currency':rows[0].get('currency','INR'),'latest':latest}
    if algorithm=='supplier_last_price':
        parties=defaultdict(list)
        for row in rows:parties[str(row.get('party') or 'UNRECORDED')].append(row)
        model['supplier_prices']={party:_latest_prices(sample) for party,sample in sorted(parties.items())}
    if algorithm.startswith('within_product_log_ridge'):
        penalty=float(algorithm.rsplit('_',1)[-1])
        groups=defaultdict(list)
        for row in rows:groups[_price_key(row)].append(row)
        features=[];targets=[]
        for observations in groups.values():
            x=[[(date.fromisoformat(str(r['date'])[:10])-origin).days/30,
                math.log1p(float(r.get('quantity',1)))] for r in observations]
            y=[math.log(float(r['price'])) for r in observations]
            means=[sum(v[j] for v in x)/len(x) for j in range(2)]
            mean_y=sum(y)/len(y)
            features.extend([[v[j]-means[j] for j in range(2)] for v in x])
            targets.extend([v-mean_y for v in y])
        matrix=[[sum(v[i]*v[j] for v in features)+(penalty if i==j else 0)
                 for j in range(2)] for i in range(2)]
        model['coefficients']=_solve(matrix,[sum(v[i]*y for v,y in zip(features,targets)) for i in range(2)])
        model['maximum_price_factor']=2.0
    return model


def predict_shared_price(model,row):
    key=_price_key(row)
    if key not in model['products']:raise ValueError('No trained product/unit history')
    if row.get('currency',model['currency'])!=model['currency']:raise ValueError('Currency mismatch')
    quantity=float(row.get('quantity',1))
    if not math.isfinite(quantity) or quantity<=0:raise ValueError('Invalid quantity')
    # Retain compatibility with previously downloaded model artifacts.
    if model['algorithm']=='pooled_log_price_ridge':
        vector=[1,(date.fromisoformat(str(row['date'])[:10])-date.fromisoformat(model['origin'])).days/30,math.log1p(quantity)]+[float(key==p) for p in model['products']]
        return math.exp(max(-20,min(20,sum(a*b for a,b in zip(vector,model['coefficients'])))))
    reference=model['latest'][key]
    if model['algorithm']=='supplier_last_price':
        reference=model['supplier_prices'].get(str(row.get('party') or 'UNRECORDED'),{}).get(key,reference)
    if not model['algorithm'].startswith('within_product_log_ridge'):
        return reference['price']
    delta=[(date.fromisoformat(str(row['date'])[:10])-date.fromisoformat(reference['date'])).days/30,
           math.log1p(quantity)-math.log1p(reference['quantity'])]
    log_change=sum(a*b for a,b in zip(delta,model['coefficients']))
    bound=math.log(model['maximum_price_factor'])
    return reference['price']*math.exp(max(-bound,min(bound,log_change)))


def train_shared_prices(rows):
    """Select on earlier validation dates; reserve the final dates for testing."""
    for row in rows:
        if not math.isfinite(float(row['price'])) or float(row['price'])<0 or not math.isfinite(float(row.get('quantity',1))) or float(row.get('quantity',1))<=0:
            raise ValueError('Price observations require finite nonnegative rates and positive quantities')
        date.fromisoformat(str(row['date'])[:10])
    rows=sorted([r for r in rows if float(r['price'])>0],key=lambda r:(str(r['date']),_price_key(r),str(r.get('party',''))))
    dates=sorted({str(r['date'])[:10] for r in rows})
    if len(rows)<10 or len(dates)<5:return {'status':'insufficient_history','observations':len(rows)}
    currencies={r.get('currency','INR') for r in rows}
    if len(currencies)!=1:return {'status':'separate_currency_models_required','observations':len(rows)}
    holdout=max(1,len(dates)//5);cutoff=dates[-holdout]
    development=[r for r in rows if str(r['date'])[:10]<cutoff]
    test=[r for r in rows if str(r['date'])[:10]>=cutoff]
    development_dates=sorted({str(r['date'])[:10] for r in development})
    validation_start=development_dates[-max(1,len(development_dates)//4)]
    fitting=[r for r in development if str(r['date'])[:10]<validation_start]
    validation=[r for r in development if str(r['date'])[:10]>=validation_start]
    candidates=['last_observed_price','supplier_last_price','within_product_log_ridge_0.1',
                'within_product_log_ridge_1','within_product_log_ridge_10']
    scores={};validation_rows=0
    for algorithm in candidates:
        fitted=_fit_shared_prices(fitting,algorithm)
        known=[r for r in validation if _price_key(r) in fitted['products']]
        validation_rows=len(known)
        if known:scores[algorithm]=metrics([float(r['price']) for r in known],
            [predict_shared_price(fitted,r) for r in known])
    selected=min(scores,key=lambda k:scores[k]['mae']) if scores else 'last_observed_price'
    initial=_fit_shared_prices(development,selected)
    known=[r for r in test if _price_key(r) in initial['products']]
    evaluation=metrics([float(r['price']) for r in known],[predict_shared_price(initial,r) for r in known]) if known else None
    baseline_model=_fit_shared_prices(development,'last_observed_price')
    baseline=metrics([float(r['price']) for r in known],[predict_shared_price(baseline_model,r) for r in known]) if known else None
    model=_fit_shared_prices(rows,selected)
    latest={_price_key(r):r for r in rows}
    estimates=[]
    for product,row in sorted(latest.items()):
        reference=model['latest'][product]
        day=(date.fromisoformat(reference['date'])+timedelta(days=30)).isoformat()
        quantity=reference['quantity']
        estimate=predict_shared_price(model,{**row,'date':day,'quantity':quantity})
        history_dates=len({str(r['date'])[:10] for r in rows if _price_key(r)==product})
        estimates.append({'product_id':row['product_id'],'unit':row.get('unit') or 'UNRECORDED',
            'forecast_date':day,'predicted_price':round(estimate,2),'last_price':round(reference['price'],2),
            'assumed_quantity':quantity,'assumed_party':row.get('party') or 'UNRECORDED',
            'history_dates':history_dates,'basis':selected,'change_percent':round((estimate/reference['price']-1)*100,2)})
    gate='research_only' if len(known)<10 or not evaluation or evaluation['mae']>baseline['mae'] else 'passed_baseline_check'
    return {'status':'trained','observations':len(rows),'training_observations':len(development),
        'fitting_observations':len(fitting),'validation_observations':len(validation),
        'comparable_validation_rows':validation_rows,'validation_start':validation_start,
        'validation_scores':scores,'selected_algorithm':selected,
        'test_observations':len(test),'evaluated_known_product_rows':len(known),
        'unseen_product_test_rows':len(test)-len(known),'test_start':cutoff,
        'test_metrics':evaluation,'last_price_baseline_metrics':baseline,'quality_gate':gate,
        'recommendation_mode':'validated_price_model' if gate=='passed_baseline_check' else 'last_observed_price_reference',
        'evaluation_model':initial,'model':model,'estimates':estimates,
        'note':'Model selected using validation MAE before the untouched chronological test. Only repeated product/unit histories estimate price changes. Supplier baselines fall back to the same product. Sparse test coverage remains experimental.'}


def train_price(rows):
    rows=sorted(rows,key=lambda r:str(r['date']))
    if any(not math.isfinite(float(r['price'])) or float(r['price'])<0 or
           not math.isfinite(float(r.get('quantity',1))) or float(r.get('quantity',1))<=0 for r in rows):
        raise ValueError('Price observations require finite nonnegative rates and positive quantities')
    dates=sorted({str(r['date'])[:10] for r in rows})
    if len(dates)<5:
        return {'status':'insufficient_history','observations':len(rows),'distinct_dates':len(dates),
                'required_distinct_dates':5}
    training=[r for r in rows if str(r['date'])[:10]<dates[-2]]
    validation=[r for r in rows if str(r['date'])[:10]==dates[-2]]
    test=[r for r in rows if str(r['date'])[:10]==dates[-1]]
    scores={}
    for algorithm in ['last_observed_price','ridge_price_regression']:
        model=_fit(training,algorithm)
        scores[algorithm]=metrics([float(r['price']) for r in validation],
            [predict_price(model,r['date'],r.get('quantity',1),r.get('party','')) for r in validation])['mae']
    selected=min(scores,key=scores.get)
    before_test=[r for r in rows if str(r['date'])[:10]<dates[-1]]
    fitted=_fit(before_test,selected)
    evaluation=metrics([float(r['price']) for r in test],
                       [predict_price(fitted,r['date'],r.get('quantity',1),r.get('party','')) for r in test])
    fitted=_fit(rows,selected)
    next_day=(date.fromisoformat(dates[-1])+timedelta(days=30)).isoformat()
    latest_by_party={r.get('party',''):r for r in rows}
    suppliers=[{'party':p,'latest_price':float(r['price']),'last_observed_date':r['date'],
                'predicted_price':predict_price(fitted,next_day,1,p)} for p,r in latest_by_party.items()]
    return {'status':'trained','observations':len(rows),'distinct_dates':len(dates),
            'limited_history':len(dates)<20,'validation_mae':scores,'test_metrics':evaluation,
            'test_date':dates[-1],'forecast_date':next_day,'model':fitted,
            'predicted_price':predict_price(fitted,next_day,1,rows[-1].get('party','')),
            'last_price':float(rows[-1]['price']),'supplier_comparison':suppliers}


def build_insights(rows, products=None, demand=None, minimum_margin=10, target_margin=20):
    if not 0<=minimum_margin<=target_margin<100:raise ValueError('Invalid margin percentages')
    products=products or {};demand=demand or {}
    groups=defaultdict(lambda:{'PURCHASE':[],'SALE':[]})
    excluded=0
    for row in rows:
        if row.get('direction') not in {'PURCHASE','SALE'} or not row.get('date') or not row.get('product_id'):
            excluded+=1;continue
        key=(row['product_id'],row.get('unit') or 'UNRECORDED',row.get('currency','INR'))
        groups[key][row['direction']].append(row)
    output=[]
    for (product,unit,currency),history in sorted(groups.items()):
        purchases=history['PURCHASE'];sales=history['SALE']
        cost_model=train_price(purchases);sale_model=train_price(sales)
        product_state=products.get(product,{})
        latest=max(purchases,key=lambda r:str(r['date'])) if purchases else None
        cost=float(latest['price']) if latest else float(product_state.get('unit_cost') or 0)
        min_margin=float(product_state.get('minimum_margin_percent') if product_state.get('minimum_margin_percent') is not None else minimum_margin)
        target=float(product_state.get('target_margin_percent') if product_state.get('target_margin_percent') is not None else target_margin)
        floor=cost/(1-min_margin/100) if cost else None
        margin_price=cost/(1-max(min_margin,target)/100) if cost else None
        learned=sale_model.get('predicted_price') if not sale_model.get('limited_history',True) else None
        suggested=max(floor or 0,learned if learned is not None else margin_price or 0) or None
        stock=product_state.get('quantity_on_hand')
        forecast_quantity=demand.get(product) if sum(k[0]==product for k in groups)==1 else None
        needed=max(0,math.ceil(float(forecast_quantity)-float(stock))) if forecast_quantity is not None and stock is not None else None
        rising=(cost_model.get('predicted_price',cost)>cost*1.03) if cost and not cost_model.get('limited_history',True) else False
        repeated_sales=len({str(r['date'])[:10] for r in sales})>=3
        advice='Insufficient evidence for a buy-now recommendation'
        if rising and repeated_sales:advice='Price rise predicted; consider buying only the demand-backed stock shortfall'
        elif repeated_sales:advice='Repeated sales observed; monitor reorder quantity and supplier quotes'
        profit=None
        if forecast_quantity is not None and cost and suggested:
            q=float(forecast_quantity)
            profit={'predicted_units':q,'revenue':round(q*suggested,2),'cost_of_goods':round(q*cost,2),
                    'gross_profit':round(q*(suggested-cost),2),
                    'note':'Conditional gross-profit scenario; excludes tax, expenses, returns and financing'}
        example=None
        if latest and cost and suggested:
            q=float(latest['quantity'])
            example={'assumed_units':q,'revenue':round(q*suggested,2),'cost_of_goods':round(q*cost,2),
                     'gross_profit':round(q*(suggested-cost),2),
                     'basis':'Last purchase quantity used as an explicit planning assumption, not forecast demand',
                     'source_date':latest['date']}
        latest_quotes={r.get('party','UNRECORDED'):r for r in sorted(purchases,key=lambda r:str(r['date']))}
        quotes=[{'party':party,'latest_price':float(row['price']),'last_observed_date':row['date']} for party,row in latest_quotes.items()]
        output.append({'product_id':product,'unit':unit,'currency':currency,
            'product_name':next((r.get('description') for r in purchases+sales if r.get('description')),product_state.get('name',product)),
            'current_unit_cost':cost or None,
            'purchase_cost_model':cost_model,'selling_price_model':sale_model,
            'selling_price':{'floor':round(floor,2) if floor else None,
                             'suggested':round(suggested,2) if suggested else None,
                             'basis':'learned_sale_price_with_margin_floor' if learned is not None else 'margin_scenario_rule',
                             'minimum_margin_percent':min_margin,'target_margin_percent':target},
            'purchase_advisor':{'advice':advice,'reorder_quantity':needed,
                'best_observed_supplier':min(quotes,key=lambda r:r['latest_price'],default=None),
                'note':'Verify comparable units and fresh supplier quotes; no automatic orders'},
            'profit_projection':profit,'planning_scenario':example})
    fingerprint=hashlib.sha256(json.dumps(sorted(rows,key=lambda r:json.dumps(r,sort_keys=True)),sort_keys=True).encode()).hexdigest()
    return {'products':output,'excluded_rows':excluded,'model_version':'commercial-v2-validation-selected',
            'training_data_sha256':fingerprint,
            'profit_model_status':'conditional_cost_rate_quantity_calculation',
            'shared_purchase_model':train_shared_prices([r for r in rows if r.get('direction')=='PURCHASE' and r.get('date')]),
            'shared_selling_price_model':train_shared_prices([r for r in rows if r.get('direction')=='SALE' and r.get('date')]),
            'notes':['Only exact canonical products with identical currency and unit are compared',
                     'No sale outcomes means no learned willingness-to-pay or profit-maximizing claim',
                     'Profit projections require matched demand, cost and selling price']}
