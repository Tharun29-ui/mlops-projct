export function isSyntheticInvoice(invoice:{source_type?:string;synthetic?:boolean;filename?:string;invoice_number?:string}):boolean{
 const source=String(invoice.source_type||'').toUpperCase();
 return source.startsWith('DEMO')||source.startsWith('SYNTHETIC')||invoice.synthetic===true||[invoice.filename,invoice.invoice_number].some(value=>/^SYN-[PS]-\d+(?:\.[A-Za-z]+)?$/i.test(String(value||'').replaceAll('\\','/').split('/').at(-1)||''));
}
