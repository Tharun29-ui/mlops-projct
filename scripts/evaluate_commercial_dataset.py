"""Evaluate commercial models against extracted source invoices, without synthetic rows."""
import hashlib
import csv
import json
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.services.commercial_models import build_insights


def canonical(value):
    text=value.casefold()
    text=re.sub(r'\bhard\s*(disk|drive)\b|\bhdd\b','harddisk',text)
    text=re.sub(r'(\d+)\s*(tb|gb|mp)\b',r'\1\2',text)
    return ' '.join(sorted(re.findall(r'[a-z0-9]+',text)))


def main():
    path=ROOT/'ml/datasets/real-invoice-history/real_dataset.json'
    dataset=json.loads(path.read_text());rows=[];excluded=[];seen=set()
    for doc in dataset['documents']:
        invoice=doc['invoice']
        if doc['sha256'] in seen:continue
        seen.add(doc['sha256'])
        if doc.get('document_kind')=='QUOTATION' or Path(doc['source']).name=='9 Camera.pdf':
            excluded.append({'source':doc['source'],'reason':'quotation'});continue
        if not invoice.get('invoice_date'):
            excluded.append({'source':doc['source'],'reason':'missing date'});continue
        for line in invoice.get('lines',[]):
            try:
                price=float(line['unit_rate']);quantity=float(line['quantity'])
                if quantity<=0 or price<0:continue
                if line.get('line_amount') is not None and float(line['line_amount'])>0:
                    price=round(float(line['line_amount'])/quantity,2)
            except (TypeError,ValueError,KeyError):continue
            unit=(line.get('unit') or 'UNRECORDED').strip().casefold()
            if unit in {'nos','no','pcs','pieces','pc','unit','units'}:unit='each'
            rows.append({'product_id':canonical(line['description']),
                         'description':line['description'],'date':invoice['invoice_date'],
                         'price':price,'quantity':quantity,'unit':unit,
                         'party':(invoice.get('vendor_name') if doc['direction']=='PURCHASE' else invoice.get('receiver_name')) or 'UNRECORDED',
                         'currency':invoice.get('currency') or 'INR','direction':doc['direction'],
                         'source':doc['source'],'source_sha256':doc['sha256']})
    report=build_insights(rows)
    report.update({'data_source':'supplied_invoice_dataset','review_status':'EXTRACTED_UNVERIFIED',
                   'synthetic_rows':0,'source_documents':len(dataset['documents']),
                   'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                   'usable_price_rows':len(rows),'excluded_documents':excluded,
                   'extraction_failures':dataset.get('failures',[]),
                   'test_note':'Price accuracy is measured against OCR-derived rates, not independently verified labels; fixed held-out PDFs were excluded',
                   'profit_note':'No daily completeness assumption; no fabricated zero-sale days or demand quantities'})
    report['margin_scenario_assumptions']={'minimum_margin_percent':10,'target_margin_percent':20,
                                         'note':'Dataset-only illustrative margin assumptions, not business settings or learned margins'}
    target=ROOT/'backend/app/data/commercial-dataset-report.json';target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report,indent=2))
    (path.parent/'commercial_rows.json').write_text(json.dumps(rows,indent=2))
    (path.parent/'commercial_report.json').write_text(json.dumps(report,indent=2))
    artifacts=ROOT/'ml/artifacts/commercial';artifacts.mkdir(parents=True,exist_ok=True)
    bundle={key:report[key] for key in ['model_version','training_data_sha256','data_source','review_status',
        'shared_purchase_model','shared_selling_price_model','margin_scenario_assumptions','profit_model_status']}
    (artifacts/'model.json').write_text(json.dumps(bundle,indent=2))
    with (artifacts/'price_outlook.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=['direction','product_id','unit','forecast_date','predicted_price','last_price',
            'history_dates','basis','quality_gate'])
        writer.writeheader()
        for direction,key in [('PURCHASE','shared_purchase_model'),('SALE','shared_selling_price_model')]:
            model=report[key]
            for row in model.get('estimates',[]):
                writer.writerow({'direction':direction,'quality_gate':model['quality_gate'],
                    **{field:row[field] for field in writer.fieldnames if field in row}})
    trained=[(p['product_id'],kind,p[kind]['model']['algorithm']) for p in report['products']
             for kind in ['purchase_cost_model','selling_price_model'] if p[kind]['status']=='trained']
    print(json.dumps({'documents':len(dataset['documents']),'price_rows':len(rows),'products':len(report['products']),
                      'trained_models':trained,'excluded_documents':len(excluded),'synthetic_rows':0},indent=2))


if __name__=='__main__':main()
