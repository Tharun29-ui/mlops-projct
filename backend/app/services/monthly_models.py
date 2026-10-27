"""Portable real-invoice model inference. Standard library only in Lambda."""
import calendar
import math
import re
import struct
from datetime import date


def identity(text):
    text=re.sub(r'image\s*star','image star',str(text),flags=re.I)
    text=re.sub(r'hi[-\s]*focus','hifocus',text,flags=re.I)
    text=re.sub(r'(\d+)\s*(?:inch|[\"”])',r'\1inch',text,flags=re.I)
    text=re.sub(r'\bhard\s*(disk|drive)\b|\bhdd\b','harddisk',str(text).casefold())
    text=re.sub(r'(\d+)\s*(tb|gb|mp)\b',r'\1\2',text)
    return ' '.join(sorted(re.findall(r'[a-z0-9]+',text)))


def month_number(day):
    d=date.fromisoformat(str(day)[:10]);return d.year*12+d.month


def category(name):
    s=str(name).casefold()
    for kind in ['toner','cable','camera','harddisk','monitor','rack','nvr','printer']:
        if kind in identity(s).replace(' ',''):return kind
    return 'other'


def demand_group(name):
    """Comparable recorded quantities only; never convert packaging by price."""
    kind=category(name);s=str(name).casefold()
    if kind=='cable':
        if re.search(r'coil|roll|box',s):return 'cable:pack'
        if re.search(r'connector|patch|hdmi|vga|usb',s):return 'cable:piece'
        return 'cable:length_unspecified'
    return kind+':piece'


def features(history,target,quantity=1,kind='demand',name=''):
    if not history:raise ValueError('Model requires prior same-product observations')
    last=history[-1];values=[float(x['value']) for x in history[-3:]]
    if any(not math.isfinite(v) or v<=0 for v in values):raise ValueError('Invalid historical value')
    d=date.fromisoformat(str(target)[:10]);previous=date.fromisoformat(last['date'])
    result=[math.log1p(values[-1]),math.log1p(sum(values)/len(values)),
        math.log(values[-1]/values[0]),min(len(history),24)/24,
        max(0,(d-previous).days)/30,math.sin(2*math.pi*d.month/12),math.cos(2*math.pi*d.month/12),
        math.log1p(float(quantity)) if kind!='demand' else 0]
    result += [float(category(name)==c) for c in ['toner','cable','camera','harddisk','monitor','rack','nvr','printer','other']]
    s=str(name).casefold()
    capacity=re.search(r'(\d+(?:\.\d+)?)\s*(tb|gb)\b',s)
    gb=float(capacity[1])*(1024 if capacity[2]=='tb' else 1) if capacity else 0
    mp=re.search(r'(\d+)\s*mp\b',s)
    result += [math.log1p(gb),float(mp[1]) if mp else 0,
        float('ip' in s),float('bullet' in s),float('dome' in s),
        float(demand_group(name)=='cable:pack'),float(demand_group(name)=='cable:piece'),
        float(demand_group(name)=='cable:length_unspecified')]
    return result


def tree_value(tree,x):
    node=0
    while tree['left'][node]!=-1:
        node=tree['left'][node] if x[tree['feature'][node]]<=tree['threshold'][node] else tree['right'][node]
    return tree['value'][node]


def predict(model,x,last):
    algorithm=model.get('algorithm','last_value')
    if algorithm=='last_value':return float(last)
    # sklearn's tree traversal casts feature inputs to IEEE float32.
    x=[struct.unpack('f',struct.pack('f',float(v)))[0] for v in x]
    if algorithm=='ridge_absolute':
        y=model['intercept']+sum(c*(v-m)/s for c,v,m,s in zip(model['coefficients'],x,model['mean'],model['scale']))
    elif algorithm in ('random_forest','forest_absolute'):y=sum(tree_value(t,x) for t in model['trees'])/len(model['trees'])
    elif algorithm=='gradient_boosting':y=model['initial']+model['learning_rate']*sum(tree_value(t,x) for t in model['trees'])
    else:raise ValueError('Unknown portable model')
    if algorithm.endswith('_absolute'):return max(0,math.expm1(max(0,min(12,y))))
    # Predict a bounded relative change, never another product's absolute price.
    return max(0,float(last)*math.exp(max(-2.3,min(2.3,y))))


def next_month(today):
    return date(today.year+int(today.month==12),today.month%12+1,1)


def inventory_plan(bundle,products,policy,today=None,month=None,assumptions=None):
    today=today or date.today();start=date.fromisoformat(month+'-01') if month else next_month(today)
    end=date(start.year,start.month,calendar.monthrange(start.year,start.month)[1])
    assumptions=assumptions or {};rows=[]
    if not 1<=month_number(start)-month_number(today)<=12:
        raise ValueError('Choose a future planning month within the next twelve months')
    known_ids={str(p['id']) for p in products}
    if set(assumptions)-known_ids:raise ValueError('Scenario contains a product outside inventory')
    for p in products:
        keys={identity(p.get('name','')),*[identity(a) for a in p.get('aliases',[]) or []]}
        matches=[k for k in keys if k in bundle['histories']]
        key=matches[0] if len(matches)==1 else None
        history=bundle['histories'].get(key,{}) if key else {}
        demand=history.get('demand',[]);costs=history.get('PURCHASE',[]);sales=history.get('SALE',[])
        cost=float(p.get('unit_cost') or (costs[-1]['value'] if costs else 0))
        if not math.isfinite(cost) or cost<0:raise ValueError('Invalid product cost')
        minimum=float(p.get('minimum_margin_percent') if p.get('minimum_margin_percent') is not None else policy.get('minimum_margin_percent') or 0)
        target=float(p.get('target_margin_percent') if p.get('target_margin_percent') is not None else policy.get('target_margin_percent') if policy.get('target_margin_percent') is not None else minimum)
        target=max(minimum,target)
        if not 0<=minimum<=target<100:raise ValueError('Margins must be between 0 and 100')
        floor=cost/(1-minimum/100) if cost else None
        margin_price=cost/(1-target/100) if cost else None
        price=None;price_status='MARGIN_POLICY_REFERENCE';outlook=None
        if sales:
            state=bundle['models']['SALE']
            price=predict(state['model'],features(sales,start,kind='price',name=p['name']),sales[-1]['value'])
            price_status='LEARNED_PRICE_ESTIMATE' if state['selected_algorithm']!='last_value' else 'LAST_SALE_REFERENCE'
        suggested=max(floor or 0,price or margin_price or 0) or None
        price_error=bundle['models']['SALE'].get('validation_relative_error',0)
        lower=max(floor or 0,(price or suggested or 0)*max(0,1-price_error)) if suggested else None
        upper=max(suggested or 0,(price or suggested or 0)*(1+price_error)) if suggested else None
        if price_status=='MARGIN_POLICY_REFERENCE':lower=floor;upper=suggested
        if costs:
            state=bundle['models']['PURCHASE']
            outlook=predict(state['model'],features(costs,start,kind='price',name=p['name']),costs[-1]['value'])
        units=None;status='NO_MATCHED_SALES_HISTORY';basis='No compatible real sales history'
        if demand:
            state=bundle['models']['demand']
            units=predict(state['model'],features(demand,start,name=p['name']),demand[-1]['value'])
            status='PARTIAL_HISTORY_SCENARIO';basis='Recorded monthly invoice quantities; missing business sales are unknown'
        elif category(p['name'])!='other' and demand_group(p['name']) in bundle.get('category_priors',{}):
            prior=bundle['category_priors'][demand_group(p['name'])]
            state=bundle['models']['demand']
            analogous=[{'date':prior['date'],'value':prior['value']}]
            units=predict(state['model'],features(analogous,start,name=p['name']),prior['value'])
            status='CATEGORY_ANALOGUE_SCENARIO'
            basis=f"Cold-start {demand_group(p['name'])} quantity-group prior from {prior['observations']} observed product-months; not this product's sales history"
        ident=str(p['id'])
        if ident in assumptions:
            value=float(assumptions[ident])
            if not math.isfinite(value) or value<0:raise ValueError('Scenario quantities must be finite and nonnegative')
            if value>100000:raise ValueError('Scenario quantities must not exceed 100000')
            units=value;status='USER_QUANTITY_SCENARIO';basis='Quantity explicitly supplied by user, not model output'
        elif bundle['models']['demand'].get('quality_gate',{}).get('passed') is False:
            units=None;status='DEMAND_MODEL_NOT_ACCEPTED'
            basis='Held-out demand quality gate failed; no automatic revenue or reorder estimate is released'
        stock=float(p.get('quantity_on_hand') or 0)
        gap=max(0,math.ceil(units-stock)) if units is not None else None
        fulfillable=min(stock,units) if units is not None else None
        error=0 if status=='USER_QUANTITY_SCENARIO' else bundle['models']['demand'].get('validation_relative_error',0)
        qlow=max(0,units*(1-error)) if units is not None else None
        qhigh=units*(1+error) if units is not None else None
        advice=[]
        if status=='DEMAND_MODEL_NOT_ACCEPTED':advice.append(basis+'. Add complete product sales history or use an explicitly entered quantity scenario.')
        if units is None:advice.append('Add matched product sales or enter an explicit quantity scenario; stock alone cannot predict demand.')
        elif gap:advice.append(f'Planning shortfall: {gap} units. Confirm demand and outstanding orders before purchasing.')
        else:advice.append('Recorded stock covers this quantity scenario; avoid extra purchasing without confirmed demand.')
        if status=='CATEGORY_ANALOGUE_SCENARIO':advice.append(basis+'. Confirm actual demand before buying; product identities and stock were not merged.')
        if price_status=='MARGIN_POLICY_REFERENCE':advice.append('Price range follows your margin policy; there is insufficient matched selling-price history.')
        if costs and outlook and outlook>costs[-1]['value']*1.03:
            advice.append('Estimated purchase cost is rising; compare a current supplier quote before buying the demand-backed shortfall.')
        if costs:
            latest=max(costs,key=lambda h:h['date']);age=(today-date.fromisoformat(latest['date'])).days
            if age>90:advice.append('Last purchase is over 90 days old. Check stock batches before considering a discount; keep the price floor.')
        rows.append({'product_id':ident,'sku':p.get('sku'),'product_name':p.get('name'),'hsn_sac':p.get('hsn_sac'),
            'on_hand':stock,'history_key':key,'history_months':len(demand),'status':status,'basis':basis,
            'forecast_units':round(units,2) if units is not None else None,'unit_range':[round(qlow,2),round(qhigh,2)] if units is not None else None,
            'price_range':[round(lower,2),round(upper,2)] if suggested else None,'suggested_price':round(suggested,2) if suggested else None,
            'price_floor':round(floor,2) if floor else None,'price_basis':price_status,'unit_cost':cost,'purchase_cost_outlook':round(outlook,2) if outlook else None,
            'revenue':round(units*suggested,2) if units is not None and suggested is not None else None,
            'revenue_range':[round(qlow*lower,2),round(qhigh*upper,2)] if units is not None and suggested is not None else None,
            'stock_backed_revenue':round(fulfillable*suggested,2) if fulfillable is not None and suggested else None,
            'gross_profit':round(units*(suggested-cost),2) if units is not None and suggested is not None else None,
            'reorder_quantity':gap,'recommendations':advice})
    available=[r for r in rows if r['revenue'] is not None]
    return {'month':start.strftime('%Y-%m'),'period_start':start.isoformat(),'period_end':end.isoformat(),
        'status':'PLANNING_SCENARIO_NOT_VALIDATED_BUSINESS_FORECAST','currency':'INR','synthetic_training_rows':0,
        'demand_model_accepted':bundle['models']['demand'].get('quality_gate',{}).get('passed'),
        'inventory_products':len(rows),'covered_products':len(available),'uncovered_products':len(rows)-len(available),
        'matched_history_products':sum(r['history_months']>0 for r in rows),
        'category_analogue_products':sum(r['status']=='CATEGORY_ANALOGUE_SCENARIO' for r in rows),
        'total_revenue':round(sum(r['revenue'] for r in available),2) if available else None,
        'revenue_range':[round(sum(r['revenue_range'][i] for r in available),2) for i in [0,1]] if available else None,
        'stock_backed_revenue':round(sum(r['stock_backed_revenue'] or 0 for r in available),2) if available else None,
        'gross_profit':round(sum(r['gross_profit'] or 0 for r in available),2) if available else None,
        'rows':rows,'model_version':bundle['version'],'source_audit':bundle['source_audit'],
        'models':{k:{a:b for a,b in v.items() if a not in ('model','evaluation_model','candidate_models')} for k,v in bundle['models'].items()},
        'limitations':['Source invoices are incomplete business history; estimates are conditional planning scenarios, not established total sales forecasts.',
            'Missing product-months are not filled with zero; demand training uses only observed product-month totals.',
            'Ranges are validation-error sensitivities, not calibrated confidence intervals. Revenue excludes tax, returns and operating expenses.',
            'Uncovered inventory products are excluded from totals, not assigned zero demand. Product matching requires exact normalized names or explicit aliases.']}
