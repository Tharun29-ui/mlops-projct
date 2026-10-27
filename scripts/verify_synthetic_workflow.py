"""Run the 20 synthetic expected invoices in a disposable in-memory database."""
import json
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.environ.update(DATABASE_URL='sqlite:///:memory:',BUSINESS_DATA_TABLE_NAME='',INVOICE_BUCKET_NAME='',AWS_EXTRACTION_ENABLED='false')
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.db import Base
from app.models import Invoice,InvoiceLine,Product,StockMovement
from app.api.routes.invoices import review_invoice


def main():
    fixture=ROOT/'ml/datasets/synthetic-test-20/expected-invoices.json'
    docs=json.loads(fixture.read_text())['documents']
    ocr_mode='--cached-ocr' in sys.argv
    if ocr_mode:
        checks=json.loads((ROOT/'demo-results/2026-10-24/model-strengthening/synthetic-ocr-check.json').read_text())['checks']
        assert all(c['passed'] for c in checks)
        selected=[]
        for c in checks:
            original=next(d for d in docs if d['filename']==c['filename'])
            # Direction is explicit upload metadata, not guessed from supplier text.
            selected.append({**original,**c['extracted'],'transaction_type':original['transaction_type']})
        docs=selected
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine);results=[]
    with Session(engine) as db:
        for d in sorted(docs,key=lambda d:d['invoice_date']):
            lines=[InvoiceLine(description=l['description'],hsn_sac=l['hsn_sac'],quantity=Decimal(l['quantity']),
                unit=l.get('unit') or 'each',unit_rate=Decimal(l['unit_rate']),line_amount=Decimal(l['line_amount']),is_stock_item=True) for l in d['lines']]
            inv=Invoice(filename=d['filename'],source_type='DEMO_INVOICE',content_type='image/jpeg' if d['filename'].endswith('.jpeg') else 'application/pdf',stored_path=str(ROOT/d['path']),
                vendor_name=d['vendor_name'],receiver_name=d['receiver_name'],invoice_number=d['invoice_number'],
                invoice_date=date.fromisoformat(d['invoice_date']),transaction_type=d['transaction_type'],status='NEEDS_REVIEW',
                subtotal=Decimal(d['subtotal']),tax_total=Decimal(d['tax_total']),grand_total=Decimal(d['grand_total']),lines=lines)
            db.add(inv);db.commit();db.refresh(inv)
            approved=review_invoice(inv.id,{'action':'approve'},db)
            assert approved['status']=='APPROVED'
            results.append({'filename':d['filename'],'direction':d['transaction_type'],'status':'APPROVED','id':inv.id})
        products=list(db.scalars(select(Product)).all())
        assert all(p.quantity_on_hand>=0 and p.hsn_sac for p in products)
        try:review_invoice(results[0]['id'],{'action':'approve'},db)
        except HTTPException as e:assert e.status_code==409
        else:raise AssertionError('Duplicate approval was not blocked')
        output={'environment':'DISPOSABLE_IN_MEMORY_TEST_ONLY','real_app_records_changed':False,
            'purchase_approvals':sum(r['direction']=='PURCHASE' for r in results),'sale_approvals':sum(r['direction']=='SALE' for r in results),
            'duplicate_approval_blocked':True,'products':[{'name':p.name,'remaining_stock':p.quantity_on_hand,'hsn_sac':p.hsn_sac} for p in products],
            'movements':len(list(db.scalars(select(StockMovement)).all())),'invoices':results,
            'scope':'Two actual cached AWS OCR outputs through approval and stock reduction; direction supplied as explicit upload metadata.' if ocr_mode else 'Workflow from structured expected values; PDF text and rendering checked separately. No claim of independent OCR/model accuracy.'}
        target=ROOT/'demo-results/2026-10-24/model-strengthening/ocr-to-stock-verification.json' if ocr_mode else fixture.parent/'workflow-verification.json'
        target.write_text(json.dumps(output,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in output.items() if k not in ['products','invoices']}))

if __name__=='__main__':main()
