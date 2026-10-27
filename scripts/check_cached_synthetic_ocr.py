"""Verify cached two-file AWS OCR smoke results without repeating paid calls."""
import ast,json,re,sys,os
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));os.environ.setdefault('DATABASE_URL','sqlite:///:memory:')
from app.services.textract_results import _normalized_payload
src=ast.parse((ROOT/'backend/lambda/complete_expense/complete_expense.py').read_text())
names={'_field_value','_normalize','_cell_text','_header_role','_table_line_items','_table_invoice_date','_merge_table_lines'}
ns={'re':re,'datetime':datetime}
exec(compile(ast.Module(body=[n for n in src.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'extractors','exec'),ns)
folder=ROOT/'demo-results/2026-10-24/model-strengthening'
raw=json.loads((folder/'synthetic-ocr-raw.json').read_text())
expected=json.loads((ROOT/'ml/datasets/synthetic-test-20/expected-invoices.json').read_text())['documents']
checks=[]
for r in raw['documents']:
    n=ns['_normalize']([r['expense']]);ed=n['expense_documents'][0]
    lines=ns['_table_line_items']([r['tables']])
    if lines:ed['line_items']=ns['_merge_table_lines'](ed['line_items'],lines)
    if not any(f.get('type')=='INVOICE_RECEIPT_DATE' and f.get('value') for f in ed['summary_fields']):
        field=ns['_table_invoice_date']([r['tables']])
        if field:ed['summary_fields'].append(field)
    actual=_normalized_payload(n);e=next(x for x in expected if x['filename']==r['filename'])
    row={'filename':r['filename'],'extracted':actual,
        'header_checks':{k:str(actual.get(k))==str(e.get(k)) for k in ['invoice_number','invoice_date']},
        'line_count_correct':len(actual['lines'])==len(e['lines']),
        'total_correct':abs(float(actual.get('grand_total') or 0)-float(e['grand_total']))<.02,
        'line_checks':[{'description':x['description'],'quantity_correct':float(x['quantity'])==float(y['quantity']),
            'hsn_correct':x.get('hsn_sac')==y.get('hsn_sac'),
            'amount_correct':abs(float(x['line_amount'])-float(y['line_amount']))<.02}
            for x,y in zip(actual['lines'],e['lines'])]}
    row['passed']=all(row['header_checks'].values()) and row['line_count_correct'] and row['total_correct'] and all(all(v for k,v in l.items() if k!='description') for l in row['line_checks'])
    checks.append(row)
result={'scope':'Two-file Textract and application parser smoke test; isolated from live inventory',
    'business_records_changed':False,'synthetic':True,'training_allowed':False,'checks':checks,
    'extra_api_calls':'Two initial response-normalization attempts failed in connector sandbox after OCR; third raw response cached and parsed locally. Calls are billable.'}
(folder/'synthetic-ocr-check.json').write_text(json.dumps(result,indent=2,default=str))
print(json.dumps(result,indent=2,default=str))
