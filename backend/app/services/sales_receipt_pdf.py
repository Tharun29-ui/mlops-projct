"""Dependency-free printable sales receipts, without tax-invoice functionality."""
from decimal import Decimal


def _text(value, limit=100):
    value=str('' if value is None else value).encode('ascii','replace').decode('ascii')
    return value.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')[:limit]


def _page_stream(invoice, lines, page_number, page_count):
    chunks=['BT','/F1 15 Tf','40 800 Td',f"({_text(invoice['vendor_name'])}) Tj",
        '/F1 10 Tf','0 -18 Td','(Not a tax invoice) Tj','0 -14 Td',f"({_text(invoice.get('vendor_address'))}) Tj",
        '/F1 13 Tf','0 -24 Td','(SALES RECEIPT) Tj','/F1 10 Tf','0 -17 Td',
        f"(Receipt: {_text(invoice['invoice_number'])}    Date: {_text(invoice['invoice_date'])}) Tj",
        '0 -16 Td',f"(Customer: {_text(invoice.get('receiver_name'))}) Tj"]
    if invoice.get('receiver_address'):
        chunks.extend(['0 -14 Td',f"({_text(invoice['receiver_address'])}) Tj"])
    chunks.extend(['0 -24 Td','/F1 9 Tf','(PRODUCT                            HSN       QTY       RATE       AMOUNT) Tj',
        '0 -14 Td','(-----------------------------------------------------------------------) Tj'])
    for line in lines:
        row=f"{str(line.get('description') or '')[:32]:<32} {str(line.get('hsn_sac') or '-')[:8]:>8} {str(line.get('quantity')):>7} {str(line.get('unit_rate')):>10} {str(line.get('line_amount')):>10}"
        chunks.extend(['0 -15 Td',f'({_text(row,110)}) Tj'])
    balance=Decimal(str(invoice.get('grand_total') or 0))-Decimal(str(invoice.get('amount_paid') or 0))
    chunks.extend(['0 -23 Td',f"(Total: INR {_text(invoice.get('grand_total'))}) Tj",
        '0 -15 Td',f"(Amount received: INR {_text(invoice.get('amount_paid') or 0)}) Tj",
        '0 -15 Td',f'(Balance due: INR {_text(balance)}) Tj',
        '0 -25 Td','(Sales receipt only. No tax charged.) Tj',
        '0 -15 Td',f'(Page {page_number} of {page_count}) Tj','ET'])
    return '\n'.join(chunks).encode('ascii','replace')


def render_sales_receipt(invoice):
    lines=invoice.get('lines') or []
    pages=[lines[i:i+28] for i in range(0,len(lines),28)] or [[]]
    page_ids=[4+i*2 for i in range(len(pages))]
    kids=' '.join(f'{page_id} 0 R' for page_id in page_ids)
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',
        f'<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>'.encode(),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for i, page in enumerate(pages):
        stream=_page_stream(invoice,page,i+1,len(pages))
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {5+i*2} 0 R >>'.encode())
        objects.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
    output=bytearray(b'%PDF-1.4\n%ABOS\n');offsets=[0]
    for i,body in enumerate(objects,start=1):
        offsets.append(len(output));output.extend(f'{i} 0 obj\n'.encode()+body+b'\nendobj\n')
    xref=len(output);output.extend(f'xref\n0 {len(offsets)}\n'.encode()+b'0000000000 65535 f \n')
    for offset in offsets[1:]:output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(output)
