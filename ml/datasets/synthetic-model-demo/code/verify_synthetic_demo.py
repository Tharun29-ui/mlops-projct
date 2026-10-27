from pathlib import Path
import json,importlib.util,hashlib,csv,math
from reportlab.graphics.shapes import Drawing,String,Line
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics import renderPDF
from reportlab.lib import colors
here=Path(__file__).resolve().parent
root=here.parent if here.name=='code' else here/'synthetic-ml-demo'
project=Path(__file__).resolve().parents[4]
spec=importlib.util.spec_from_file_location('monthly_trainer',project/'scripts/train_monthly_models.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
samples=json.loads((root/'results/training-samples.json').read_text());summary=json.loads((root/'results/evaluation.json').read_text());checks={}
for kind,rows in samples.items():
 original=json.loads((root/f'results/{kind.lower()}-model.json').read_text());cutoff=original['test_start']
 changed=[dict(r,actual=r['actual']*1.7) if r['date']>=cutoff else r for r in rows]
 repeat=m.evaluate_model(sorted(changed,key=lambda r:(r['date'],r['key'])),kind)
 assert repeat['selected_algorithm']==original['selected_algorithm']
 assert repeat['validation_scores']==original['validation_scores']
 assert repeat['evaluation_model']==original['evaluation_model']
 test=sorted((r for r in rows if r['date']>=cutoff),key=lambda r:(r['date'],r['key']))
 pred=[m.predict(original['evaluation_model'],r['x'],r['last']) for r in test]
 assert m.score(test,pred)==original['test_metrics']
 checks[kind]={'heldout_target_change_leaves_selection_and_validation_model_unchanged':True,'portable_saved_evaluation_model_reproduces_metrics':True,'test_rows':len(test),'test_wape':original['test_metrics']['wape']}
ledger=list(csv.DictReader((root/'results/stock-ledger.csv').open(encoding='utf-8-sig')))
assert all(float(r['on_hand'])>=0 for r in ledger)
checks['stock_nonnegative']=True
assert summary['safety']['live_business_records_changed'] is False
assert summary['safety']['real_model_artifacts_unchanged'] is True
assert summary['safety']['synthetic_training_never_merged_with_real'] is True
checks['real_artifacts_unchanged']=True;checks['live_data_not_modified']=True
(root/'results/verification.json').write_text(json.dumps(checks,indent=2))

# Standard ReportLab charting, saved as an exportable scientific figure.
dr=Drawing(1050,740)
dr.add(String(525,715,'Synthetic demand holdout — June to September 2026',fontName='Helvetica-Bold',fontSize=22,textAnchor='middle'))
dr.add(String(525,689,'Rolling one-month predictions with observed prior months; simulated data only',fontSize=14,textAnchor='middle'))
ev=summary['metrics']['demand']['test_predictions'];skus=sorted({r['product_key'] for r in ev})
for i,sku in enumerate(skus):
 x=65+(i%2)*510;y=470-(i//2)*205;rs=sorted((r for r in ev if r['product_key']==sku),key=lambda r:r['date'])
 chart=LinePlot();chart.x=x;chart.y=y;chart.width=400;chart.height=135
 chart.data=[[(j,r[k]) for j,r in enumerate(rs)] for k in ['actual','predicted','baseline']]
 chart.xValueAxis.valueMin=0;chart.xValueAxis.valueMax=3;chart.xValueAxis.valueSteps=[0,1,2,3];chart.xValueAxis.labelTextFormat=lambda v:['Jun','Jul','Aug','Sep'][int(v)]
 chart.yValueAxis.valueMin=0;chart.yValueAxis.valueMax=math.ceil(max(r[k] for r in rs for k in ['actual','predicted','baseline'])*1.15/10)*10
 chart.lines[0].strokeColor=colors.HexColor('#2467ad');chart.lines[1].strokeColor=colors.HexColor('#16834a');chart.lines[2].strokeColor=colors.HexColor('#999999')
 for j in range(3):chart.lines[j].strokeWidth=2
 dr.add(chart);dr.add(String(x+200,y+153,sku,fontName='Helvetica-Bold',fontSize=15,textAnchor='middle'))
for x,label,color in [(180,'Actual simulated units','#2467ad'),(460,'Selected Ridge model','#16834a'),(760,'Last-value baseline','#999999')]:
 dr.add(Line(x,34,x+30,34,strokeColor=colors.HexColor(color),strokeWidth=3));dr.add(String(x+38,29,label,fontSize=13))
renderPDF.drawToFile(dr,str(root/'results/synthetic-demand-holdout.pdf'))
print(json.dumps(checks,indent=2))
