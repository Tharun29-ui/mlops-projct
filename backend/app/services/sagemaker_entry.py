"""Network-isolated SageMaker entry point, also runnable locally for contract tests."""
import csv
import hashlib
import json
import math
import os
from datetime import date, timedelta
from pathlib import Path

try:
    from demand_model import train, metrics
except ModuleNotFoundError:
    from app.services.demand_model import train, metrics


def train_bundle(input_dir, output_dir):
    input_dir=Path(input_dir); output_dir=Path(output_dir)
    manifest=json.loads((input_dir/'manifest.json').read_text())
    content=(input_dir/'sales_daily.csv').read_bytes()
    if not manifest.get('demo') or manifest.get('data_source')!='reviewed_demo_invoices' or not manifest.get('coverage_complete'):
        raise ValueError('This training release accepts only the complete labelled demo cohort')
    if hashlib.sha256(content).hexdigest()!=manifest['source_sha256']:
        raise ValueError('Training data checksum mismatch')
    code_dir=Path(__file__).resolve().parent
    code_hash=hashlib.sha256(b''.join(name.encode()+b'\0'+(code_dir/name).read_bytes()
        for name in sorted(['sagemaker_entry.py','demand_model.py']))).hexdigest()
    if code_hash!=manifest['code_sha256']:raise ValueError('Training code checksum mismatch')
    series={}; seen=set()
    for row in csv.DictReader(content.decode('utf-8-sig').splitlines()):
        key=(row['product_id'],row['date']); quantity=float(row['quantity_sold'])
        if key in seen or not key[0].startswith('DEMO-') or not math.isfinite(quantity) or quantity<0:
            raise ValueError('Invalid, duplicate or non-demo training row')
        seen.add(key);series.setdefault(key[0],[]).append((date.fromisoformat(key[1]),quantity))
    if len(series)!=3 or len(seen)!=192:
        raise ValueError('Expected the verified three-product, 64-day demo')
    models={};product_metrics={};forecasts=[];actuals=[];predicted=[]
    for product,rows in sorted(series.items()):
        rows.sort();result=train(rows,horizon=90)
        models[product]=result['model'];product_metrics[product]=result['metrics']
        actuals.extend(result['test_actuals']);predicted.extend(result['test_predictions'])
        forecasts.extend(dict(product_id=product,forecast_date=(rows[-1][0]+timedelta(days=i)).isoformat(),
                              predicted_quantity=q) for i,q in enumerate(result['predictions'],1))
    evaluation=metrics(actuals,predicted)
    artifact=dict(schema_version=1,training_backend='amazon_sagemaker',demo=True,
        data_source='reviewed_demo_invoices',source_sha256=manifest['source_sha256'],
        code_sha256=manifest['code_sha256'],training_job_name=manifest['training_job_name'],
        horizon_days=90,models=models,product_metrics=product_metrics,metrics=evaluation,forecasts=forecasts,
        quality_gate='PASSED_DEMO_CHECK' if evaluation['holdout_observations']>=30 and evaluation['wape'] is not None and evaluation['wape']<=.5 else 'FAILED_EVALUATION',
        limitations=['Synthetic demonstration history; no real-business accuracy claim.',
            '90-day projection from 64 days is a planning scenario, not validated 90-day accuracy.'])
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/'model.json').write_text(json.dumps(artifact,indent=2),encoding='utf-8')
    (output_dir/'evaluation.json').write_text(json.dumps(evaluation,indent=2),encoding='utf-8')
    with (output_dir/'forecasts.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=['product_id','forecast_date','predicted_quantity'])
        writer.writeheader();writer.writerows(forecasts)
    print('evaluation_mae: '+str(evaluation['mae']))
    print('evaluation_wape: '+str(evaluation['wape']))
    return artifact


def main():
    train_bundle(os.environ.get('SM_CHANNEL_TRAIN','/opt/ml/input/data/train'),os.environ.get('SM_MODEL_DIR','/opt/ml/model'))


if __name__=='__main__':main()
