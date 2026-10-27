"""Create 20 isolated workflow fixtures, never training or business records."""
import copy
import csv
import hashlib
import json
import random
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'ml/datasets/synthetic-test-20'
ADDRESS='Example Street, Example City'
HEADERS=['SL.NO','Descriptions','HSN/SAC','Qty','Rate','Amount']


def money(value):return Decimal(str(value)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)


def main():
    data=json.loads((ROOT/'backend/app/data/reviewed-real-documents.json').read_text())['documents']
    sources=[d for d in data if d['direction']=='PURCHASE' and any(l.get('is_stock_item') for l in d['invoice']['lines'])]
    rng=random.Random(20261024);docs=[];styles=getSampleStyleSheet()
    for i in range(10):
        source=sources[i%len(sources)]
        purchase_lines=[];sale_lines=[]
        for original in source['invoice']['lines']:
            if not original.get('is_stock_item'):continue
            quantity=rng.randint(4,12);sold=rng.randint(1,quantity)
            for target,q in [(purchase_lines,quantity),(sale_lines,sold)]:
                line=copy.deepcopy(original);line.update(quantity=str(q),line_amount=str(money(Decimal(line['unit_rate'])*q)))
                target.append(line)
        for direction,lines,offset in [('PURCHASE',purchase_lines,i),('SALE',sale_lines,i+10)]:
            number=f'SYN-{direction[:1]}-{i+1:03}';day=date(2026,11,1)+timedelta(days=offset)
            subtotal=sum(Decimal(l['line_amount']) for l in lines);cgst=money(subtotal*Decimal('.09'));sgst=cgst
            invoice={'vendor_name':source['invoice']['vendor_name'] if direction=='PURCHASE' else 'Example Supplier',
                'receiver_name':'Example Supplier' if direction=='PURCHASE' else 'M/s Example Customer',
                'invoice_number':number,'invoice_date':day.isoformat(),'transaction_type':direction,'currency':'INR',
                'subtotal':str(subtotal),'tax_total':str(cgst+sgst),'grand_total':str(subtotal+cgst+sgst),
                'round_off':'0.00','lines':lines,'source_type':'SYNTHETIC_WORKFLOW_TEST','synthetic':True,
                'source_template_sha256':source['sha256'],'training_allowed':False}
            folder=DEST/direction.lower();folder.mkdir(parents=True,exist_ok=True)
            path=folder/(number+'.pdf')
            flow=[Paragraph('SYNTHETIC TEST — NOT A VALID TAX INVOICE',styles['Heading2']),
                Paragraph(invoice['vendor_name'],styles['Title']),Paragraph(ADDRESS,styles['Normal']),Spacer(1,18),
                Paragraph('INVOICE',styles['Heading2']),Paragraph(f"To: {invoice['receiver_name']}<br/>{ADDRESS}<br/>Invoice no: {number}<br/>Date: {day.strftime('%d-%m-%Y')}<br/>Test direction: {direction}",styles['Normal']),Spacer(1,20)]
            table_rows=[HEADERS]
            for j,l in enumerate(lines,1):table_rows.append([str(j),Paragraph(l['description'],styles['Normal']),l['hsn_sac'],l['quantity'],l['unit_rate'],l['line_amount']])
            table_rows += [['','','','','Subtotal',str(subtotal)],['','','','','CGST 9%',str(cgst)],['','','','','SGST 9%',str(sgst)],['','','','','Total',invoice['grand_total']]]
            table=Table(table_rows,colWidths=[32,210,60,35,70,70],repeatRows=1)
            table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf2ed')),('VALIGN',(0,0),(-1,-1),'TOP'),('FONTSIZE',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
            flow += [table,Spacer(1,25),Paragraph('Generated solely to test extraction, quantity changes, date handling and stock workflows. Not a real transaction; exclude from model training and business accuracy claims. Addresses are reused from supplied source examples for fixture consistency.',styles['Normal'])]
            SimpleDocTemplate(str(path),pagesize=A4,rightMargin=30,leftMargin=30,topMargin=35,bottomMargin=35).build(flow)
            invoice.update(filename=path.name,path=path.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            docs.append(invoice)
    (DEST/'expected-invoices.json').write_text(json.dumps({'synthetic':True,'training_allowed':False,'documents':docs},indent=2),encoding='utf-8')
    with (DEST/'expected-lines.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f);writer.writerow(['InvoiceNo','InvoiceDate','Direction',*HEADERS])
        for d in docs:
            for i,l in enumerate(d['lines'],1):writer.writerow([d['invoice_number'],d['invoice_date'],d['transaction_type'],i,l['description'],l['hsn_sac'],l['quantity'],l['unit_rate'],l['line_amount']])
    (DEST/'README.md').write_text('# Twenty synthetic invoice workflow tests\n\n10 purchases dated 1–10 November 2026 and 10 matching sales dated 11–20 November. Same source product names, HSN codes and rates; changed quantities/dates and recalculated totals. All purchases precede sales and sufficient fixture stock is supplied.\n\n'
        'Headers: SL.NO | Descriptions | HSN/SAC | Qty | Rate | Amount. Clearly marked synthetic, distinct SYN invoice numbers, matching JSON and CSV expected outputs. These are local test files only: never real business records, never part of real training or reported real-data test accuracy. Synthetic tests derived from source templates are not independent evidence of model generalisation.\n',encoding='utf-8')
    print(json.dumps({'documents':len(docs),'folder':str(DEST),'training_allowed':False}))

if __name__=='__main__':main()
