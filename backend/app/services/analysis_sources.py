"""Keep explicitly synthetic invoice history out of actual-business training."""
import re

def _value(invoice,key):
    return invoice.get(key) if isinstance(invoice,dict) else getattr(invoice,key,None)

def is_synthetic_invoice(invoice):
    source=str(_value(invoice,'source_type') or '').upper()
    if source.startswith(('DEMO','SYNTHETIC')) or _value(invoice,'synthetic') is True:return True
    for key in ['filename','invoice_number']:
        name=str(_value(invoice,key) or '').replace('\\','/').rsplit('/',1)[-1]
        if re.match(r'^SYN-[PS]-\d+(?:\.[A-Za-z]+)?$',name,re.I):return True
    return False

def approved_analysis_invoices(invoices):
    approved=[i for i in invoices if _value(i,'status')=='APPROVED']
    business=[i for i in approved if not is_synthetic_invoice(i)]
    demo=[i for i in approved if is_synthetic_invoice(i)]
    return (business,False,len(demo)) if business else (demo,bool(demo),0)
