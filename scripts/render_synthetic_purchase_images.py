"""Provide purchase JPEGs + sales PDFs, matching the real dataset file types."""
import hashlib
import json
import shutil
from pathlib import Path
import pypdfium2 as pdfium

ROOT=Path(__file__).resolve().parents[1]
folder=ROOT/'ml/datasets/synthetic-test-20'
manifest=folder/'expected-invoices.json'
data=json.loads(manifest.read_text(encoding='utf-8'))
for d in data['documents']:
    original=ROOT/d.get('reference_pdf',d['path'])
    if d['transaction_type']=='PURCHASE':
        dest=folder/'test-inputs/purchase'/(d['invoice_number']+'.jpeg');dest.parent.mkdir(parents=True,exist_ok=True)
        doc=pdfium.PdfDocument(str(original));doc[0].render(scale=2.5).to_pil().convert('RGB').save(dest,quality=95)
    else:
        dest=folder/'test-inputs/sales'/original.name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,dest)
    d['reference_pdf']=original.relative_to(ROOT).as_posix();d['path']=dest.relative_to(ROOT).as_posix()
    d['filename']=dest.name;d['sha256']=hashlib.sha256(dest.read_bytes()).hexdigest()
manifest.write_text(json.dumps(data,indent=2),encoding='utf-8')
readme=folder/'README.md'
text=readme.read_text(encoding='utf-8')
if 'test-inputs/' not in text:
    readme.write_text(text+'\nUse test-inputs/purchase (10 JPEG images) and test-inputs/sales (10 PDFs), matching the original dataset file formats. The other purchase/sale PDFs are editable-generation reference copies of the same 20 bills; do not upload both forms.\n',encoding='utf-8')
print('Prepared 10 purchase JPEGs and 10 sales PDFs under',folder/'test-inputs')
