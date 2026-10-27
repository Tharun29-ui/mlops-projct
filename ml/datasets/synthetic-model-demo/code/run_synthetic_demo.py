"""Isolated synthetic evaluation using the existing project's trainer, never its main()."""
from pathlib import Path
import csv,json,math,random,hashlib,sys,importlib.util,collections,platform,calendar
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP
import numpy as np
import sklearn
from reportlab.platypus import SimpleDocTemplate,Paragraph,Table,TableStyle,Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4

WORK=Path(__file__).resolve().parent
DEST=WORK.parent if WORK.name=='code' else WORK/'synthetic-ml-demo'
PROJECT=Path(__file__).resolve().parents[4]
SEED=20261025;rng=random.Random(SEED)
for s in ['purchase/sample-invoices','sales/sample-invoices','results','code']:(DEST/s).mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('project_monthly_trainer',PROJECT/'scripts/train_monthly_models.py')
trainer=importlib.util.module_from_spec(spec);spec.loader.exec_module(trainer)
from app.services.monthly_models import features,predict,demand_group
PRODUCTS=[('SYN-MON','DELL 22 inch LED Monitor','8528',5,6200),('SYN-CAM','4MP IP Bullet Camera','8525',12,2800),('SYN-HDD','1TB WD Hard Disk','8471',7,3600),('SYN-TON','Printer Toner Cartridge','8443',8,900),('SYN-NVR','16 Port NVR','8521',3,14000),('SYN-PRN','Laser Printer','8443',2,11500)]
HEADERS=['InvoiceNo','InvoiceDate','Direction','ProductSKU','SL.NO','Descriptions','HSN/SAC','Qty','Rate','Amount','Currency','DataSource']
def money(x):return float(Decimal(str(x)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def dump(path,data):path.write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')
def csvwrite(path,rows,headers):
 with path.open('w',newline='',encoding='utf-8-sig') as f:
  writer=csv.DictWriter(f,fieldnames=headers);writer.writeheader();writer.writerows(rows)
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [PROJECT/'backend/app/data/monthly-model-bundle.json',PROJECT/'ml/artifacts/monthly/model.json']}
START=date(2024,10,1);END=date(2026,9,30)
sales=[];purchases=[];stock={p[0]:0 for p in PRODUCTS};daily=[];ledger=[]
shocks={}
for month_i in range(24):
 for sku,*_ in PRODUCTS:shocks[(month_i,sku)]=rng.gauss(0,.045)
day=START
while day<=END:
 mi=(day.year-START.year)*12+day.month-START.month
 for j,(sku,name,hsn,base,cost0) in enumerate(PRODUCTS):
  season=1+.30*math.sin(2*math.pi*(day.month-1)/12+j*.3)
  weekday=1+.12*math.sin(2*math.pi*day.weekday()/7)
  level=base*season*(1+.006*mi)*weekday*(1+shocks[(mi,sku)])
  qty=max(1,int(round(level+rng.gauss(0,max(.6,base*.13)))))
  cost=money(cost0*(1+.004*mi+.02*math.sin(2*math.pi*day.month/12+j*.2)))
  rate=money(cost/(1-(.23+.02*math.sin(2*math.pi*day.month/12))))
  # Restock against current stock using a fixed coverage rule. No future actual sales are inspected.
  if stock[sku]<max(qty,int(base*8)):
   bought=max(qty,int(base*26)-stock[sku]);stock[sku]+=bought
   purchases.append(dict(zip(HEADERS,[f'SYN-P-{len(purchases)+1:05}',day.isoformat(),'PURCHASE',sku,1,name,hsn,bought,cost,money(bought*cost),'INR','SYNTHETIC_DEMO'])))
   ledger.append({'date':day.isoformat(),'sku':sku,'direction':'PURCHASE','change':bought,'on_hand':stock[sku]})
  assert stock[sku]>=qty
  stock[sku]-=qty
  sales.append(dict(zip(HEADERS,[f'SYN-S-{(day-START).days+1:04}',day.isoformat(),'SALE',sku,j+1,name,hsn,qty,rate,money(qty*rate),'INR','SYNTHETIC_DEMO'])))
  ledger.append({'date':day.isoformat(),'sku':sku,'direction':'SALE','change':-qty,'on_hand':stock[sku]})
  daily.append({'date':day.isoformat(),'product_id':sku,'quantity_sold':qty,'selling_price':rate,'synthetic':True})
 day+=timedelta(days=1)
csvwrite(DEST/'purchase/purchase-lines.csv',purchases,HEADERS);csvwrite(DEST/'sales/sales-lines.csv',sales,HEADERS)
csvwrite(DEST/'sales/complete-daily-sales.csv',daily,list(daily[0]))
csvwrite(DEST/'results/stock-ledger.csv',ledger,list(ledger[0]))

# Match the source invoice table headers. Export ten sample PDF bills per direction.
styles=getSampleStyleSheet()
for folder,rows in [('purchase',purchases),('sales',sales)]:
 groups=collections.defaultdict(list)
 for r in rows:groups[r['InvoiceNo']].append(r)
 invoices=list(groups.values());chosen=[invoices[round(i*(len(invoices)-1)/9)] for i in range(10)]
 for lines in chosen:
  r=lines[0];grid=[['SL.NO','Descriptions','HSN/SAC','Qty','Rate','Amount']]
  for i,l in enumerate(lines,1):grid.append([i,Paragraph(l['Descriptions'],styles['Normal']),l['HSN/SAC'],l['Qty'],f"{l['Rate']:.2f}",f"{l['Amount']:.2f}"])
  total=money(sum(l['Amount'] for l in lines));grid.append(['','','','','Total',f'{total:.2f}'])
  flow=[Paragraph('SYNTHETIC DEMO — NOT A REAL INVOICE',styles['Heading2']),Paragraph('AUTONOMOUS BUSINESS OS',styles['Title']),Paragraph('Fictional training business, Chennai, India',styles['Normal']),Spacer(1,12),Paragraph(f"Invoice: {r['InvoiceNo']}<br/>Date: {r['InvoiceDate']}<br/>Direction: {r['Direction']}<br/>Currency: INR. Amounts exclude tax.",styles['Normal']),Spacer(1,12)]
  table=Table(grid,colWidths=[35,190,60,40,75,75],repeatRows=1);table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E7E6E6')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
  flow +=[table,Spacer(1,14),Paragraph('Generated simulation only. Do not upload into real business inventory. The CSV datasets contain the complete simulated period; these ten PDFs are illustrations of the same records, not the full training corpus.',styles['Normal'])]
  SimpleDocTemplate(str(DEST/folder/'sample-invoices'/f"{r['InvoiceNo']}.pdf"),pagesize=A4).build(flow)

# Aggregate all simulated transactions to the same product-month grain for all three targets.
samples={k:[] for k in ['demand','SALE','PURCHASE']};histories={}
for sku,name,hsn,base,cost0 in PRODUCTS:
 histories[sku]={}
 for kind in samples:
  selected=sales if kind in ['demand','SALE'] else purchases
  groups=collections.defaultdict(list)
  for r in selected:
   if r['ProductSKU']==sku:groups[r['InvoiceDate'][:7]+'-01'].append(r)
  history=[]
  for day,rs in sorted(groups.items()):
   q=sum(r['Qty'] for r in rs);value=q if kind=='demand' else sum(r['Amount'] for r in rs)/q
   # Future monthly invoice quantity is unknown. Keep that price feature neutral
   # in both evaluation and forecasting rather than using actual held-out quantity.
   if history:samples[kind].append({'date':day,'key':sku,'x':features(history,day,1,kind,name),'last':history[-1]['value'],'actual':value,'quantity_group':demand_group(name)})
   history.append({'date':day,'value':value,'quantity':q})
  histories[sku][kind]=history
dump(DEST/'results/training-samples.json',samples)
models={};evaluation={};allpred=[]
for kind,rows in samples.items():
 result=trainer.evaluate_model(sorted(rows,key=lambda r:(r['date'],r['key'])),kind)
 result['status']='SYNTHETIC_SIMULATION_ONLY'
 result['quality_gate']['scope']='Synthetic recorded-label gate only, not evidence of real-business forecasting'
 metrics=result['test_metrics'];baseline=result['baseline_metrics']
 result['synthetic_demo_target']={'maximum_wape':.4,'passed':bool(metrics['wape']<=.4 and metrics['mae']<=baseline['mae'])}
 models[kind]=result
 evaluation[kind]={k:v for k,v in result.items() if k not in ['model','evaluation_model','candidate_models']}
 for r in result['test_predictions']:allpred.append({'target':kind,**r})
 print(kind,json.dumps({'method':result['selected_algorithm'],'test':metrics,'baseline':baseline,'synthetic_demo_target':result['synthetic_demo_target']}),flush=True)

forecasts=[]
for sku,name,hsn,base,cost0 in PRODUCTS:
 target='2026-10-01';preds={}
 for kind in samples:
  history=histories[sku][kind];preds[kind]=predict(models[kind]['model'],features(history,target,kind=kind,name=name),history[-1]['value'])
 units=round(preds['demand'],2);sell=money(preds['SALE']);cost=money(preds['PURCHASE'])
 margin_floor=money(cost/.9);sell=max(sell,margin_floor)
 demand_rel=models['demand']['validation_relative_error'];price_rel=models['SALE']['validation_relative_error']
 forecasts.append({'sku':sku,'product':name,'forecast_month':'2026-10','units':units,'unit_low_sensitivity':round(max(0,units*(1-demand_rel)),2),'unit_high_sensitivity':round(units*(1+demand_rel),2),'selling_price':sell,'price_low_sensitivity':money(max(margin_floor,sell*(1-price_rel))),'price_high_sensitivity':money(sell*(1+price_rel)),'estimated_purchase_cost':cost,'on_hand':stock[sku],'conditional_revenue':money(units*sell),'conditional_gross_profit':money(units*(sell-cost)),'suggested_shortfall':max(0,math.ceil(units-stock[sku])),'recommendation':'Compare supplier quotes before replenishing a confirmed shortfall. Simulation only; sensitivity ranges are not calibrated confidence intervals.'})
csvwrite(DEST/'results/test-predictions.csv',allpred,list(allpred[0]));csvwrite(DEST/'results/next-month-product-plan.csv',forecasts,list(forecasts[0]))
for k,v in models.items():dump(DEST/f'results/{k.lower()}-model.json',v)
hashes={str(p.relative_to(DEST)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [DEST/'purchase/purchase-lines.csv',DEST/'sales/sales-lines.csv']}
summary={'data_source':'SYNTHETIC_SIMULATION_ONLY','seed':SEED,'coverage':{'start':START.isoformat(),'end':END.isoformat(),'months':24,'products':len(PRODUCTS),'sales_lines':len(sales),'purchase_lines':len(purchases),'daily_rows':len(daily),'sample_pdfs_each_direction':10,'complete_simulated_sales':True},'generator':'Predeclared seasonal demand, gradual trend, weekday pattern and random noise. Purchase/selling price include gradual trend and seasonal variation. No tuning against final test results.','evaluation_protocol':'Project monthly trainer reused without executing its real-data main function. Last 20 percent of target months held out. Expanding earlier folds select the method. Metrics are rolling one-step-ahead with observed prior test months, not a single four-month-ahead forecast.','test_start':models['demand']['test_start'],'metrics':evaluation,'source_hashes':hashes,'trainer_sha256':hashlib.sha256((PROJECT/'scripts/train_monthly_models.py').read_bytes()).hexdigest(),'environment':{'python':platform.python_version(),'sklearn':sklearn.__version__,'numpy':np.__version__},'safety':{'live_business_records_changed':False,'real_model_artifacts_unchanged':all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items()),'synthetic_training_never_merged_with_real':True,'stock_nonnegative':all(x['on_hand']>=0 for x in ledger)},'conditional_next_month_totals':{'revenue':money(sum(x['conditional_revenue'] for x in forecasts)),'gross_profit':money(sum(x['conditional_gross_profit'] for x in forecasts))},'limitations':['Synthetic patterns are simpler than real operations.','These metrics do not establish real business accuracy.','Price tests use monthly weighted rates; the earlier real-data price evaluation used transaction dates, so the error values are not directly comparable.','No calibrated confidence intervals or causal optimal-pricing model.','No AWS deployment, OCR validation or live app mode added by this run.']}
dump(DEST/'results/evaluation.json',summary)
dump(DEST/'results/dataset-manifest.json',{'synthetic':True,'source_hashes':hashes,'coverage':summary['coverage']})
(DEST/'code/run_synthetic_demo.py').write_bytes(Path(__file__).read_bytes())
(DEST/'README.md').write_text('# Separate synthetic ML demonstration\n\nGenerated data only, not real business records.\n\n- purchase/purchase-lines.csv: complete simulated purchases\n- sales/sales-lines.csv: complete simulated invoice lines\n- sales/complete-daily-sales.csv: all generated product/day observations\n- Each direction has ten sample invoice PDFs, with source-style table headers.\n- results/evaluation.json: actual measured scores, baseline comparisons, provenance and limitations\n- results/test-predictions.csv: actual versus predicted held-out rows\n- results/next-month-product-plan.csv: synthetic October 2026 quantity, price, revenue and stock plan\n- results/*-model.json: portable fitted models and candidate comparisons\n- results/stock-ledger.csv: isolated simulation stock movements\n\nReproduce with the project backend virtual-environment Python and code/run_synthetic_demo.py. The script imports the existing project trainer from /path/to/autonomous-business-os but never calls its main() or updates real artifacts. Only the synthetic demonstration is trained. Synthetic metrics cannot be claimed as real-data accuracy. Metrics are errors, not generic accuracy percentages. This run does not add a live website mode.\n',encoding='utf-8')
print('FINISHED',DEST,flush=True)
