import {useEffect,useId,useRef,useState} from 'react';

type Props={product:{name:string;hsn_sac?:string|null};onSave:(code:string)=>Promise<void>};

export default function InventoryHsnEditor({product,onSave}:Props){
 const [open,setOpen]=useState(false),[draft,setDraft]=useState(''),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const dialog=useRef<HTMLDialogElement>(null),input=useRef<HTMLInputElement>(null),titleId=useId(),helpId=useId(),errorId=useId();
 useEffect(()=>{if(open){dialog.current?.showModal();input.current?.focus();input.current?.select();}else if(dialog.current?.open){dialog.current.close();}},[open]);
 function begin(){setDraft(product.hsn_sac||'');setError('');setOpen(true);}
 async function submit(event:React.FormEvent<HTMLFormElement>){event.preventDefault();if(saving)return;setSaving(true);setError('');try{await onSave(draft.trim());setOpen(false);}catch(e){setError(e instanceof Error?e.message:'Could not save the code. Please try again.');}finally{setSaving(false);}}
 return <>
  <div className="inventory-hsn-cell"><span className={product.hsn_sac?'inventory-hsn-code':'inventory-hsn-code is-empty'}>{product.hsn_sac||'Not set'}</span><button type="button" className="hsn-edit-button" onClick={begin} aria-label={`Edit HSN/SAC for ${product.name}`} title="Edit HSN/SAC"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m16 3 5 5M4 20l4-1L21 6a2.1 2.1 0 0 0-3-3L5 16z"/></svg><span>Edit</span></button></div>
  <dialog ref={dialog} className="hsn-editor-dialog" aria-labelledby={titleId} onClose={()=>setOpen(false)} onCancel={e=>{if(saving)e.preventDefault();}}>
   <form onSubmit={event=>void submit(event)}>
    <header className="hsn-editor-header"><div><span className="hsn-editor-eyebrow">PRODUCT DETAILS</span><h2 id={titleId}>Edit HSN / SAC code</h2></div><button type="button" className="hsn-editor-close" aria-label="Close HSN editor" disabled={saving} onClick={()=>setOpen(false)}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></header>
    <div className="hsn-editor-body"><p className="hsn-editor-product">{product.name}</p><label htmlFor={titleId+'-input'}>HSN / SAC code</label><input ref={input} id={titleId+'-input'} name="hsn_sac" value={draft} maxLength={32} onChange={e=>setDraft(e.target.value)} disabled={saving} placeholder="Enter code from the invoice" aria-describedby={error?helpId+' '+errorId:helpId} aria-invalid={!!error}/><p id={helpId} className="hsn-editor-help">Use the code printed on the invoice or a verified classification. Leave it blank to clear the saved code.</p>{error&&<p id={errorId} className="hsn-editor-error" role="alert">{error}</p>}</div>
    <footer className="hsn-editor-footer"><button type="button" className="select-button" disabled={saving} onClick={()=>setOpen(false)}>Cancel</button><button type="submit" className="primary-button" disabled={saving}>{saving?'Saving…':'Save changes'}</button></footer>
   </form>
  </dialog>
 </>;
}
