import {useEffect,useRef,useState} from 'react';
import {apiFetch} from './auth';

const API=import.meta.env.VITE_API_BASE_URL||(import.meta.env.DEV?'http://127.0.0.1:8000/api/v1':'/api/v1');
type Props={source:string;horizon:number;startMonth:string;modelVersion:string;modelReady:boolean;disabled:boolean};
const ms=(value:number)=>`${value.toLocaleString('en-IN',{maximumSignificantDigits:4})} ms`;

export default function InferenceTiming({source,horizon,startMonth,modelVersion,modelReady,disabled}:Props){
 const [result,setResult]=useState<any>(null),[measuring,setMeasuring]=useState(false),[error,setError]=useState('');
 const active=useRef(true),controller=useRef<AbortController|null>(null);
 useEffect(()=>{active.current=true;return()=>{active.current=false;controller.current?.abort();};},[]);
 async function measure(){
  if(disabled||measuring||!modelReady)return;
  setMeasuring(true);setError('');setResult(null);controller.current=new AbortController();
  const started=performance.now();
  try{
   const query=new URLSearchParams({source,horizon:String(horizon),start_month:startMonth});
   const response=await apiFetch(`${API}/forecasts/profit/benchmark?${query}`,{signal:controller.current.signal,cache:'no-store'});
   const value=await response.json();const clientMs=performance.now()-started;
   if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:'Could not measure model speed.');
   if(value.source!==source||value.model_version!==modelVersion)throw new Error('The bill data changed. Refresh the forecast before measuring again.');
   if(active.current)setResult({...value,client_roundtrip_ms:clientMs});
  }catch(e){if(active.current&&!(e instanceof DOMException&&e.name==='AbortError'))setError(e instanceof Error?e.message:'Could not measure model speed.');}
  finally{if(active.current)setMeasuring(false);}
 }
 function download(){
  const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download=`inference-timing-${source}.json`;link.click();URL.revokeObjectURL(url);
 }
 return <section className="panel inference-panel" aria-labelledby="inference-heading">
  <div className="panel-heading"><div><h2 id="inference-heading">Model speed</h2><p>Measure this model on the selected invoice data.</p></div><button className="select-button" disabled={disabled||measuring||!modelReady} onClick={()=>void measure()}>{measuring?'Measuring…':'Measure inference time'}</button></div>
  {!modelReady&&<p className="inference-note">This model needs at least two complete recorded profit months before prediction time can be measured. The synthetic demonstration can be measured separately.</p>}
  {error&&<p className="profit-error" role="alert">{error}</p>}
  <div aria-live="polite" aria-busy={measuring}>
   {measuring&&<p className="inference-note">Preparing the selected forecast, then timing 1,000 warm one-month predictions…</p>}
   {result&&<>
    <div className="inference-source">{result.synthetic?'Synthetic demonstration':'Real invoice records'} · {result.algorithm}</div>
    <div className="inference-grid">
     <article><span>Model prediction</span><strong>{result.prediction?ms(result.prediction.median_ms):'Model not ready'}</strong><small>Median for one monthly prediction, after fitting.</small></article>
     <article><span>Forecast preparation</span><strong>{ms(result.preparation_ms)}</strong><small>Bills, monthly totals, fitting and evaluation.</small></article>
     <article><span>Browser round trip</span><strong>{ms(result.client_roundtrip_ms)}</strong><small>The complete measurement request and transfer.</small></article>
    </div>
    {result.prediction&&<p className="inference-note">{result.prediction.samples.toLocaleString()} timed predictions · {result.prediction.warmup_samples} warm-up predictions · Prediction p95 {ms(result.prediction.p95_ms)}. This is the prediction-loop p95, not the API p95.</p>}
    <p className="inference-note">Prediction time excludes data loading, fitting and network. Browser round trip includes the timing test and may include sign-in refresh or a cold start; initialization is not measured separately. These are observed timings, not a speed guarantee.</p>
    <div className="inference-footer"><small>Measured {new Date(result.observed_at_utc).toLocaleString()} · {result.model_version}</small><button className="text-button" onClick={download}>Download timing results</button></div>
   </>}
  </div>
 </section>;
}
