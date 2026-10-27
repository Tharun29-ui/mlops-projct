"""Cache real invoice extraction for offline model evaluation; never approve stock."""
import ast
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import boto3
from botocore.config import Config

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.environ['BUSINESS_DATA_TABLE_NAME']='offline-dataset-only'
from app.services.textract_results import _normalized_payload


def main():
    credentials=json.loads(subprocess.check_output(['aws','configure','export-credentials','--profile','capstone-dev','--format','process'],text=True))
    session=boto3.Session(aws_access_key_id=credentials['AccessKeyId'],aws_secret_access_key=credentials['SecretAccessKey'],aws_session_token=credentials['SessionToken'],region_name='ap-southeast-2')
    client=session.client('textract',config=Config(retries={'mode':'adaptive','max_attempts':8}))
    namespace={'re':re,'datetime':datetime}
    source=ast.parse((ROOT/'backend/lambda/complete_expense/complete_expense.py').read_text())
    names={'_field_value','_normalize','_needs_table_fallback','_cell_text','_header_role','_table_line_items','_table_invoice_date','_merge_table_lines'}
    exec(compile(ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'extractors','exec'),namespace)
    splits={r['relative_path']:r['split'] for r in csv.DictReader((ROOT/'ml/datasets/source-manifests/invoice_split.csv').open(encoding='utf-8-sig'))}
    paths=[p for p in (ROOT/'data/sample_invoices').glob('*.pdf') if splits.get(p.relative_to(ROOT).as_posix())=='development']
    paths+=list((ROOT/'data/purchase dataset').glob('*.jpeg'))
    out=ROOT/'ml/datasets/real-invoice-history';cache=out/'extraction_cache';cache.mkdir(parents=True,exist_ok=True)
    def extract(path):
        digest=hashlib.sha256(path.read_bytes()).hexdigest(); target=cache/f'{digest}.json'
        if target.exists():return json.loads(target.read_text())
        raw=client.analyze_expense(Document={'Bytes':path.read_bytes()})
        normalized=namespace['_normalize']([raw])
        if namespace['_needs_table_fallback'](normalized):
            tables=client.analyze_document(Document={'Bytes':path.read_bytes()},FeatureTypes=['TABLES'])
            docs=normalized['expense_documents']
            if docs:
                lines=namespace['_table_line_items']([tables])
                if lines:docs[0]['line_items']=namespace['_merge_table_lines'](docs[0]['line_items'],lines)
                if not any(f.get('type')=='INVOICE_RECEIPT_DATE' and f.get('value') for f in docs[0]['summary_fields']):
                    field=namespace['_table_invoice_date']([tables])
                    if field:docs[0]['summary_fields'].append(field)
        data=_normalized_payload(normalized)
        text=' '.join(block.get('Text','') for doc in raw.get('ExpenseDocuments',[]) for block in doc.get('Blocks',[]))
        result={'source':path.relative_to(ROOT).as_posix(),'sha256':digest,'split':'development',
                'direction':'SALE' if path.suffix=='.pdf' else 'PURCHASE',
                'review_status':'EXTRACTED_UNVERIFIED',
                'document_kind':'QUOTATION' if re.search(r'\bquotation\b|\bproforma\b',text,re.I) else 'INVOICE',
                'invoice':data}
        target.write_text(json.dumps(result,default=str,indent=2));return result
    results=[];failures=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(extract,p):p for p in paths}
        for future in as_completed(futures):
            try:results.append(future.result())
            except Exception as error:failures.append({'source':futures[future].relative_to(ROOT).as_posix(),'error':str(error)[:250]})
            print(json.dumps({'completed':len(results),'failed':len(failures),'total':len(paths)}),flush=True)
    (out/'real_dataset.json').write_text(json.dumps({'documents':results,'failures':failures},default=str,indent=2))
    print(json.dumps({'documents':len(results),'line_items':sum(len(r['invoice'].get('lines',[])) for r in results),'failures':len(failures)}))


if __name__=='__main__':main()
