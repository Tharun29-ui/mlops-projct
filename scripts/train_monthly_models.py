"""Train on real invoices only; export portable forests/boosting for Lambda."""
import collections
import hashlib
import json
import math
import os
import platform
import sys
from datetime import date
from pathlib import Path
import numpy as np
import sklearn
from sklearn.ensemble import RandomForestRegressor,GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.services.monthly_models import features,predict,inventory_plan,category,demand_group

SEED=20261024
CANDIDATES=['last_value','random_forest','gradient_boosting']
DEMAND_CANDIDATES=CANDIDATES+['forest_absolute','ridge_absolute']


def score(rows,predictions):
    errors=[abs(r['actual']-p) for r,p in zip(rows,predictions)]
    total=sum(r['actual'] for r in rows)
    return {'rows':len(rows),'mae':round(sum(errors)/len(errors),5) if errors else None,
        'wape':round(sum(errors)/total,5) if total else None}


def export_tree(tree):
    t=tree.tree_
    return {'left':t.children_left.tolist(),'right':t.children_right.tolist(),
        'feature':t.feature.tolist(),'threshold':t.threshold.tolist(),'value':t.value[:,0,0].tolist()}


def fit(rows,algorithm):
    if algorithm=='last_value':return {'algorithm':algorithm}
    x=np.array([r['x'] for r in rows],dtype=np.float32)
    y=np.array([math.log1p(r['actual']) if algorithm.endswith('_absolute') else math.log(r['actual']/r['last']) for r in rows])
    if algorithm=='ridge_absolute':
        scaler=StandardScaler().fit(x);estimator=Ridge(alpha=10).fit(scaler.transform(x),y)
        model={'algorithm':algorithm,'coefficients':estimator.coef_.tolist(),'intercept':float(estimator.intercept_),
            'mean':scaler.mean_.tolist(),'scale':scaler.scale_.tolist()}
        for r,p in zip(rows,estimator.predict(scaler.transform(x))):
            expected=math.expm1(max(0,min(12,float(p))))
            assert abs(predict(model,r['x'],r['last'])-expected)<max(1e-5,expected*1e-5)
        return model
    if algorithm in ('random_forest','forest_absolute'):
        estimator=RandomForestRegressor(n_estimators=80,max_depth=4,min_samples_leaf=2,random_state=SEED,n_jobs=1)
    else:estimator=GradientBoostingRegressor(n_estimators=60,max_depth=2,min_samples_leaf=2,learning_rate=.04,loss='huber',random_state=SEED)
    estimator.fit(x,y)
    if algorithm in ('random_forest','forest_absolute'):model={'algorithm':algorithm,'trees':[export_tree(t) for t in estimator.estimators_]}
    else:model={'algorithm':algorithm,'trees':[export_tree(t[0]) for t in estimator.estimators_],
        'initial':float(estimator.init_.constant_[0,0]),'learning_rate':float(estimator.learning_rate)}
    for r,p in zip(rows,estimator.predict(x)):
        expected=math.expm1(max(0,min(12,float(p)))) if algorithm.endswith('_absolute') else r['last']*math.exp(max(-2.3,min(2.3,float(p))))
        assert abs(predict(model,r['x'],r['last'])-expected)<max(1e-6,expected*1e-7),'Portable model does not reproduce sklearn'
    return model


def evaluate_model(rows,kind):
    candidates=DEMAND_CANDIDATES if kind=='demand' else CANDIDATES
    dates=sorted({r['date'] for r in rows})
    if len(dates)<3 or len(rows)<8:
        return {'selected_algorithm':'last_value','status':'INSUFFICIENT_REPEATED_HISTORY','model':{'algorithm':'last_value'},
            'quality_gate':{'passed':False,'reason':'Insufficient chronological repeated history'},
            'observations':len(rows),'distinct_target_dates':len(dates),'validation_relative_error':0,
            'test_metrics':{'rows':0,'mae':None,'wape':None},'validation_scores':{},'test_predictions':[]}
    test_dates=max(1,len(dates)//5);cutoff=dates[-test_dates]
    development=[r for r in rows if r['date']<cutoff];test=[r for r in rows if r['date']>=cutoff]
    dev_dates=sorted({r['date'] for r in development})
    validation_dates=dev_dates[max(1,len(dev_dates)//2):]
    folds={name:[] for name in candidates};validation_rows=[]
    for day in validation_dates:
        train=[r for r in development if r['date']<day];validation=[r for r in development if r['date']==day]
        if len(train)<4:continue
        validation_rows.extend(validation)
        for algorithm in candidates:
            model=fit(train,algorithm)
            folds[algorithm].extend(predict(model,r['x'],r['last']) for r in validation)
    scores={a:score(validation_rows,p) for a,p in folds.items()}
    selected=min(candidates,key=lambda a:scores[a]['mae'] if scores[a]['mae'] is not None else float('inf'))
    evaluation=fit(development,selected)
    predictions=[predict(evaluation,r['x'],r['last']) for r in test]
    baseline=[r['last'] for r in test]
    metrics=score(test,predictions);base=score(test,baseline)
    # Promotion is a separate held-out acceptance check, never a method selector.
    # Threshold is explicit; passing this small sample still is not business validation.
    gate={'passed':bool(metrics['rows']>=3 and metrics['wape'] is not None and metrics['wape']<=.5 and metrics['mae']<=base['mae']),
        'maximum_wape':.5,'minimum_test_rows':3,'must_not_underperform_baseline':True,
        'scope':'Recorded-label sample gate, not proof of complete business forecasting'}
    # Use rolling-validation errors for planning sensitivities; test targets never choose ranges or method.
    absolute_relative=[abs(r['actual']-p)/max(r['last'],1e-9) for r,p in zip(validation_rows,folds[selected])]
    radius=sorted(absolute_relative)[min(len(absolute_relative)-1,math.ceil(.8*len(absolute_relative))-1)] if absolute_relative else 0
    fitted={name:fit(rows,name) for name in candidates}
    grouped_metrics={g:score([r for r in test if r.get('quantity_group')==g],[p for r,p in zip(test,predictions) if r.get('quantity_group')==g]) for g in sorted({r.get('quantity_group','price') for r in test})} if kind=='demand' else {}
    return {'selected_algorithm':selected,'model':fitted[selected],'candidate_models':fitted,'evaluation_model':evaluation,
        'status':'EXPERIMENTAL_REAL_SAMPLE','observations':len(rows),'distinct_target_dates':len(dates),
        'selection':'expanding_date_folds_before_final_test','test_start':cutoff,'validation_scores':scores,
        'test_metrics':metrics,'baseline_metrics':base,'baseline_beaten':metrics['mae']<base['mae'],
        'quality_gate':gate,
        'test_metrics_by_quantity_group':grouped_metrics,
        'validation_relative_error':round(min(radius,3),5),'range_method':'80th percentile past validation relative error sensitivity; not calibrated coverage',
        'test_predictions':[{'date':r['date'],'product_key':r['key'],'actual':r['actual'],'predicted':round(p,5),'baseline':r['last']} for r,p in zip(test,predictions)],
        'feature_names':['log_last','log_recent_mean','relative_trend','history_count','gap_months','month_sin','month_cos','log_quantity',
            'toner','cable','camera','harddisk','monitor','rack','nvr','printer','other',
            'log_capacity_gb','megapixels','ip','bullet','dome','cable_pack','cable_piece','cable_length_unspecified']}


def main():
    source=ROOT/'ml/datasets/all-real/corpus.json';data=json.loads(source.read_text(encoding='utf-8'))
    assert not data['synthetic_rows'] and not data['missing_documents']
    histories=collections.defaultdict(dict);samples={k:[] for k in ['demand','SALE','PURCHASE']}
    grouped=collections.defaultdict(list)
    for r in data['rows']:
        if str(r.get('source','')).find('synthetic')>=0:raise ValueError('Synthetic source in real training')
        if r['currency']!='INR':continue
        grouped[r['product_key']].append(r)
    for key,rows in sorted(grouped.items()):
        name=rows[0]['description'];histories[key]['name']=name;histories[key]['hsn_sac']=rows[0].get('hsn_sac')
        for kind in ['SALE','PURCHASE','demand']:
            actual=[r for r in rows if r['direction']==('SALE' if kind=='demand' else kind)]
            by_date=collections.defaultdict(list)
            for r in actual:by_date[r['date'][:7]+'-01' if kind=='demand' else r['date']].append(r)
            history=[]
            for day,rs in sorted(by_date.items()):
                q=sum(r['quantity'] for r in rs)
                value=q if kind=='demand' else sum(r['quantity']*r['price'] for r in rs)/q
                if history and kind!='demand':
                    samples[kind].append({'date':day,'key':key,'x':features(history,day,q,kind,name),
                        'last':history[-1]['value'],'actual':value})
                history.append({'date':day,'value':value,'quantity':q,'sources':sorted({r['source'] for r in rs})})
            histories[key][kind]=history
    # Pooled demand learning also supports cold-start products without merging
    # their identities or stock. Category priors use strictly earlier months.
    months=sorted({h['date'] for state in histories.values() for h in state['demand']})
    for month in months:
        prior_by_category=collections.defaultdict(list)
        for state in histories.values():
            prior_by_category[demand_group(state['name'])].extend(h for h in state['demand'] if h['date']<month)
        for key,state in histories.items():
            current=next((h for h in state['demand'] if h['date']==month),None)
            if not current:continue
            history=[h for h in state['demand'] if h['date']<month]
            basis='same_product'
            if not history:
                if category(state['name'])=='other':continue
                prior=prior_by_category[demand_group(state['name'])]
                if not prior:continue
                value=float(np.median([h['value'] for h in prior]))
                history=[{'date':max(h['date'] for h in prior),'value':value}];basis='category_analogue'
            samples['demand'].append({'date':month,'key':key,'x':features(history,month,name=state['name']),
                'last':history[-1]['value'],'actual':current['value'],'basis':basis,'quantity_group':demand_group(state['name'])})
    models={kind:evaluate_model(sorted(rows,key=lambda r:(r['date'],r['key'])),kind) for kind,rows in samples.items()}
    code_hash=hashlib.sha256(Path(__file__).read_bytes()+(ROOT/'backend/app/services/monthly_models.py').read_bytes()).hexdigest()
    version_hash=hashlib.sha256(source.read_bytes()+code_hash.encode()).hexdigest()
    bundle={'version':'real-monthly-v2-'+version_hash[:12],'code_sha256':code_hash,'feature_schema_version':2,
        'source_audit':{'documents':len(data['documents']),'counts':data['source_counts'],'usable_lines':len(data['rows']),
            'excluded':data['excluded'],'synthetic_rows':0,'complete_sales_coverage':False,
            'targeted_source_corrections':len(data.get('correction_records',[])),
            'source_hashes':sorted(d['sha256'] for d in data['documents']),
            'reviewed_documents':sum(d.get('review_status')=='SOURCE_CHECKED_FOR_ANALYSIS' for d in data['documents']),
            'label_quality':'Mixed source-checked and unverified OCR; metrics evaluate recorded labels, not independent ground truth'},
        'training_data_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'models':models,'histories':dict(histories),
        'category_priors':{c:{'value':float(np.median([h['value'] for state in histories.values() if demand_group(state['name'])==c for h in state['demand']])),
            'date':max(h['date'] for state in histories.values() if demand_group(state['name'])==c for h in state['demand']),
            'observations':sum(len(state['demand']) for state in histories.values() if demand_group(state['name'])==c)}
            for c in sorted({demand_group(s['name']) for s in histories.values() if s['demand']})},
        'environment':{'python':platform.python_version(),'sklearn':sklearn.__version__,'numpy':np.__version__,'seed':SEED},
        'demand_target':'Quantity in observed product-month invoice groups; partial-history planning only. Missing months are not zero-filled.'}
    target=ROOT/'backend/app/data/monthly-model-bundle.json';target.write_text(json.dumps(bundle,separators=(',',':')),encoding='utf-8')
    artifacts=ROOT/'ml/artifacts/monthly';artifacts.mkdir(parents=True,exist_ok=True)
    (artifacts/'model.json').write_bytes(target.read_bytes())
    versions=artifacts/'versions';versions.mkdir(exist_ok=True)
    (versions/(bundle['version']+'.json')).write_bytes(target.read_bytes())
    evaluations=artifacts/'evaluations';evaluations.mkdir(exist_ok=True)
    (evaluations/(bundle['version']+'.json')).write_text(json.dumps({'version':bundle['version'],
        'data_sha256':bundle['training_data_sha256'],'code_sha256':bundle['code_sha256'],'synthetic_training_rows':0,
        'models':{k:{a:v for a,v in m.items() if a not in ['model','evaluation_model','candidate_models']} for k,m in models.items()}},indent=2),encoding='utf-8')
    (artifacts/'MODEL-CARD.md').write_text('# Real invoice monthly models\n\n'
        'Three components: recorded product demand, price estimation (purchase and selling models), and inventory-backed revenue/recommendation calculations. '
        'Random Forest and gradient boosting compete against the same-product last-value baseline in expanding date folds; final test dates do not select the model. '
        'Portable JSON trees reproduce scikit-learn predictions in Lambda without scikit-learn dependencies. '
        'All real sources are audited; only valid dated stock lines train. Synthetic workflow bills are excluded. '
        'Incomplete business coverage prevents validated total-demand claims. Monthly quantities are observed sample totals, and missing months are never invented as zero. '
        'No price elasticity, causal optimal pricing, or guaranteed accuracy is claimed. Profit is a conditional calculation.\n',encoding='utf-8')
    print(json.dumps({'version':bundle['version'],'documents':len(data['documents']),'products':len(histories),
        'models':{k:{a:v for a,v in m.items() if a in ['selected_algorithm','observations','test_metrics','baseline_metrics','status']} for k,m in models.items()},'bundle_bytes':target.stat().st_size},indent=2))

if __name__=='__main__':main()
