"""Train and export demand models using verified CSV data or a labeled synthetic demo."""
import argparse
import csv
import hashlib
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from app.services.demand_model import train, metrics


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input',type=Path)
    source.add_argument('--demo',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('ml/artifacts/demand'))
    args=parser.parse_args()
    series={}
    if args.demo:
        rng=random.Random(20261022)
        for name,level in [('DEMO_MONITOR',8),('DEMO_CAMERA',15),('DEMO_HARD_DISK',5)]:
            series[name]=[(date(2026,6,24)+timedelta(days=i),
                           float(max(0,round(level+.035*i+3*math.sin(2*math.pi*i/7)+rng.gauss(0,1.3)))))
                          for i in range(120)]
    else:
        with args.input.open(encoding='utf-8-sig',newline='') as handle:
            for row in csv.DictReader(handle):
                day=date.fromisoformat(row['date']); qty=float(row['quantity_sold'])
                if not math.isfinite(qty) or qty<0: raise ValueError('Invalid quantity')
                series.setdefault(row['product_id'],[]).append((day,qty))
    args.output.mkdir(parents=True,exist_ok=True)
    artifact={'data_source':'synthetic_presentation_demo' if args.demo else 'provided_sales_csv',
              'models':{},'product_metrics':{}}
    forecasts=[]; actuals=[]; predictions=[]
    for product,rows in sorted(series.items()):
        rows.sort()
        if len(rows)!=(rows[-1][0]-rows[0][0]).days+1 or len({d for d,_ in rows})!=len(rows):
            raise ValueError('Require exactly one row per product and consecutive calendar day')
        result=train(rows)
        artifact['models'][product]=result['model']
        artifact['product_metrics'][product]=result['metrics']
        actuals.extend(result['test_actuals']); predictions.extend(result['test_predictions'])
        forecasts.extend({'product_id':product,'forecast_date':(rows[-1][0]+timedelta(days=i)).isoformat(),
                          'predicted_quantity':q} for i,q in enumerate(result['predictions'],1))
    artifact['metrics']=metrics(actuals,predictions)
    artifact['source_sha256']=hashlib.sha256(json.dumps({p:[(d.isoformat(),q) for d,q in r]
                                                       for p,r in series.items()},sort_keys=True).encode()).hexdigest()
    (args.output/'model.json').write_text(json.dumps(artifact,indent=2))
    with (args.output/'forecasts.csv').open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['product_id','forecast_date','predicted_quantity'])
        writer.writeheader();writer.writerows(forecasts)
    print(json.dumps({'data_source':artifact['data_source'],'metrics':artifact['metrics'],
                      'selected_models':{p:m['algorithm'] for p,m in artifact['models'].items()},
                      'output':str(args.output)},indent=2))


if __name__=='__main__': main()
