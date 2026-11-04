type Bill = {id:string;invoice_number:string;date:string;net_sales:string;gross_profit:string|null;source_fingerprint:string;profit_note?:string};
const columns=['bill_id','invoice_number','date','net_sales','gross_profit','source_fingerprint','note'];
function cell(v:unknown){let text=String(v??'');if(/^[=+@]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';}
export function profitCsv(bills:Bill[]){return [columns.join(','),...bills.map(b=>[b.id,b.invoice_number,b.date,b.net_sales,b.gross_profit??'',b.source_fingerprint,b.profit_note||''].map(cell).join(','))].join('\r\n');}
export function readProfitCsv(text:string){
 const rows:string[][]=[];let row:string[]=[],field='',quoted=false;
 text=text.replace(/^\uFEFF/,'');
 for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'){if(quoted&&text[i+1]==='"'){field+='"';i++;}else quoted=!quoted;}else if(c===','&&!quoted){row.push(field);field='';}else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(field);if(row.some(x=>x.trim()))rows.push(row);row=[];field='';}else field+=c;}
 if(quoted)throw new Error('The CSV contains an unclosed quoted field.');row.push(field);if(row.some(x=>x.trim()))rows.push(row);
 const head=rows.shift()||[];const indices=columns.map(name=>head.indexOf(name));if(indices.some(i=>i<0))throw new Error('Use the downloaded bill-profit CSV template and keep its headers.');
 const seen=new Set<string>();
 return rows.flatMap((r,index)=>{const values=Object.fromEntries(columns.map((key,j)=>[key,(r[indices[j]]||'').trim()]));if(!values.gross_profit)return [];
  if(!/^-?\d+(?:\.\d{1,2})?$/.test(values.gross_profit)||!Number.isFinite(Number(values.gross_profit)))throw new Error(`Row ${index+2}: enter a money amount with at most two decimals.`);
  if(!values.bill_id||seen.has(values.bill_id))throw new Error(`Row ${index+2}: missing or duplicate bill ID.`);seen.add(values.bill_id);
  if(!/^[a-f0-9]{64}$/.test(values.source_fingerprint))throw new Error(`Row ${index+2}: the bill fingerprint is invalid.`);
  if(!values.note)throw new Error(`Row ${index+2}: add the source of this profit value in note.`);
  return [{bill_id:values.bill_id,source_fingerprint:values.source_fingerprint,gross_profit:values.gross_profit,note:values.note}];
 });
}
