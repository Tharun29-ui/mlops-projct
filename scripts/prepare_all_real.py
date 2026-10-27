"""Build an auditable real-only modelling corpus; never post invoices or stock."""
import ast
import copy
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.environ.setdefault('DATABASE_URL','sqlite:///:memory:')
from app.services.textract_results import _normalized_payload


def identity(text):
    text=re.sub(r'image\s*star','image star',text,flags=re.I)
    text=re.sub(r'hi[-\s]*focus','hifocus',text,flags=re.I)
    text=re.sub(r'(\d+)\s*(?:inch|[\"”])',r'\1inch',text,flags=re.I)
    text=re.sub(r'\bhard\s*(disk|drive)\b|\bhdd\b','harddisk',text.casefold())
    text=re.sub(r'(\d+)\s*(tb|gb|mp)\b',r'\1\2',text)
    return ' '.join(sorted(re.findall(r'[a-z0-9]+',text)))


def main():
    folder=ROOT/'ml/datasets/real-invoice-history'
    cached=json.loads((folder/'real_dataset.json').read_text())['documents']
    by_hash={d['sha256']:copy.deepcopy(d) for d in cached}
    raw_path=folder/'missing-source-extraction.json'
    if raw_path.exists():
        ns={'re':re,'datetime':datetime}
        source=ast.parse((ROOT/'backend/lambda/complete_expense/complete_expense.py').read_text())
        names={'_field_value','_normalize','_cell_text','_header_role','_table_line_items','_table_invoice_date','_merge_table_lines'}
        exec(compile(ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'extractors','exec'),ns)
        for row in json.loads(raw_path.read_text())['documents']:
            meta=row['source'];normalized=row.get('normalized') or ns['_normalize']([row['expense']])
            tables=row.get('tables',{});ed=normalized['expense_documents']
            if ed and tables:
                lines=ns['_table_line_items']([tables])
                if lines:ed[0]['line_items']=ns['_merge_table_lines'](ed[0]['line_items'],lines)
                if not any(f.get('type')=='INVOICE_RECEIPT_DATE' and f.get('value') for f in ed[0]['summary_fields']):
                    field=ns['_table_invoice_date']([tables])
                    if field:ed[0]['summary_fields'].append(field)
            text=' '.join(b.get('Text','') for b in tables.get('Blocks',[]) if b.get('BlockType')=='LINE')
            by_hash[meta['sha256']]={'source':meta['path'].replace('\\','/'),'sha256':meta['sha256'],
                'direction':'SALE','review_status':'EXTRACTED_UNVERIFIED',
                'document_kind':row.get('document_kind') or ('QUOTATION' if re.search(r'quotation|proforma',text,re.I) else 'INVOICE'),
                'invoice':_normalized_payload(normalized)}
    reviewed=json.loads((ROOT/'backend/app/data/reviewed-real-documents.json').read_text())['documents']
    for d in reviewed:by_hash[d['sha256']]=copy.deepcopy(d)
    correction_path=ROOT/'ml/datasets/all-real/source-corrections.json'
    corrections=json.loads(correction_path.read_text()) if correction_path.exists() else []
    for patch in corrections:
        doc=by_hash[patch['sha256']]
        doc['document_kind']=patch.get('document_kind',doc.get('document_kind','INVOICE'))
        doc['invoice'].update(patch.get('invoice_updates',{}))
        for change in patch.get('line_updates',[]):
            matches=[l for l in doc['invoice']['lines'] if l['description']==change['description']]
            if len(matches)!=1:raise ValueError('Correction line no longer matches original: '+patch['filename'])
            matches[0].update(change['updates'])
        doc['source_correction_audit']=patch
        doc['review_status']='TARGETED_SOURCE_CORRECTION_CHECKED'
    purchase_root=ROOT/'backups/2026-10-24-real-data-reset/data-archive/purchase dataset'
    purchase_dest=ROOT/'data/sales_test/purchase';purchase_dest.mkdir(exist_ok=True)
    for p in purchase_root.glob('*.jpeg'):
        if not (purchase_dest/p.name).exists():shutil.copy2(p,purchase_dest/p.name)
    docs=[];missing=[];rows=[];excluded=[];seen=set()
    for direction,paths in [('SALE',sorted((ROOT/'data/sample_invoices').glob('*.pdf'))),('PURCHASE',sorted(purchase_dest.glob('*.jpeg')))]:
        for path in paths:
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            if sha not in by_hash:missing.append(str(path.relative_to(ROOT)));continue
            d=by_hash[sha];d['source']=path.relative_to(ROOT).as_posix();d['direction']=direction
            d['invoice']['transaction_type']=direction;docs.append(d)
            if sha in seen:
                excluded.append({'source':d['source'],'reason':'duplicate file hash'});continue
            seen.add(sha)
            inv=d['invoice'];day=inv.get('invoice_date')
            if d.get('document_kind')=='QUOTATION' or path.name=='9 Camera.pdf':
                excluded.append({'source':d['source'],'reason':'quotation, not a completed sale'});continue
            try:datetime.strptime(str(day),'%Y-%m-%d')
            except (ValueError,TypeError):excluded.append({'source':d['source'],'reason':'missing or invalid date'});continue
            for line in inv.get('lines',[]):
                name=' '.join(str(line.get('description') or '').split());hsn=str(line.get('hsn_sac') or '')
                if not name or line.get('is_stock_item') is False or hsn.startswith('99') or re.search(r'installation|labour|courier|rent|service charges',name,re.I):continue
                try:
                    q=float(line['quantity']);amount=float(line['line_amount']);price=amount/q
                    if not (0<q<100000 and 0<price<10000000):raise ValueError()
                except (ValueError,TypeError,KeyError,ZeroDivisionError):
                    excluded.append({'source':d['source'],'line':name,'reason':'invalid quantity / net amount'});continue
                rows.append({'product_key':identity(name),'description':name,'hsn_sac':hsn,'date':str(day),
                    'quantity':q,'price':round(price,4),'currency':inv.get('currency') or 'INR',
                    'unit':str(line.get('unit') or 'each').casefold(),'direction':direction,
                    'supplier':inv.get('vendor_name') or '', 'source':d['source'],'source_sha256':sha,
                    'review_status':d.get('review_status','EXTRACTED_UNVERIFIED')})
    result={'documents':docs,'rows':rows,'missing_documents':missing,'excluded':excluded,'synthetic_rows':0,
        'complete_sales_coverage':False,'correction_records':corrections,'source_counts':{'sales':sum(d['direction']=='SALE' for d in docs),'purchases':sum(d['direction']=='PURCHASE' for d in docs)},
        'note':'All supplied originals audited. Unverified OCR labels are not independently verified ground truth. Missing months are unknown business sales, not zero.'}
    target=ROOT/'ml/datasets/all-real';target.mkdir(parents=True,exist_ok=True)
    (target/'corpus.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'counts':result['source_counts'],'usable_lines':len(rows),'missing':missing,'excluded':len(excluded)}))

if __name__=='__main__':main()
