import {useEffect,useState} from "react";
import {apiFetch} from "./auth";
const API=import.meta.env.VITE_API_BASE_URL||(import.meta.env.DEV?"http://127.0.0.1:8000/api/v1":"/api/v1");

export default function SageMakerTraining({onForecast,onShowForecast}:{onForecast:(r:any)=>void;onShowForecast:()=>void}){
  const [state,setState]=useState<any>(null);
  const [job,setJob]=useState<any>(null);
  const [accepted,setAccepted]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  async function request(path:string,body?:any){
    const response=await apiFetch(`${API}/training${path}`,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw new Error(data.detail||"Managed training request failed");return data;
  }
  async function refresh(){
    setError("");try{const result=await request("/status");setState(result);if(result.latest_job){const current=await request(`/jobs/${encodeURIComponent(result.latest_job.job_name)}`);setJob(current);}}
    catch(e){setError(e instanceof Error?e.message:"Status unavailable");}
  }
  useEffect(()=>{void refresh();},[]);
  useEffect(()=>{if(!job||!["SUBMITTING","InProgress","Stopping"].includes(job.status))return;const timer=setInterval(()=>void refresh(),30000);return()=>clearInterval(timer);},[job?.job_name,job?.status]);
  async function start(){
    setBusy(true);setError("");try{const result=await request("/jobs",{confirm_billable_training:accepted});setJob(result);setAccepted(false);setNotice("SageMaker training submitted. You can leave this page; AWS runs the job.");await refresh();}
    catch(e){setError(e instanceof Error?e.message:"Training submission failed");await refresh();}finally{setBusy(false);}
  }
  async function activate(){
    if(!job)return;setBusy(true);setError("");try{await request(`/jobs/${encodeURIComponent(job.job_name)}/activate`,{});const result=await request("/predict",{horizon_days:90});onForecast(result);setNotice("Evaluated SageMaker model activated. Lambda generated and saved the 90-day demo forecast.");await refresh();}
    catch(e){setError(e instanceof Error?e.message:"Model activation failed");}finally{setBusy(false);}
  }
  return <div className="real-sample-workspace">
    <section className="forecast-intro"><div><span className="forecast-eyebrow">MANAGED TRAINING · LAMBDA PREDICTIONS</span><h2>Train once. Use a saved model.</h2><p>SageMaker fits and evaluates the demand model. After review, the website uses its saved model in Lambda. No persistent prediction endpoint is needed.</p></div></section>
    <div className="forecast-stat-grid"><article><span>Training data</span><strong>192<em> rows</em></strong><small>3 products · 64 complete days · synthetic demo</small></article><article><span>Compute limit</span><strong>1<em> CPU instance</em></strong><small>ml.m5.large · 300-second runtime limit</small></article><article><span>Submission limit</span><strong>1<em> per 24h</em></strong><small>Prevents duplicate paid training requests</small></article></div>
    <section className="panel"><div className="panel-heading"><div><h2>1. Start a bounded training job</h2><p>Only the complete labelled demo is eligible. Your selected real bills do not establish complete daily sales.</p></div><button className="select-button" disabled={busy} onClick={()=>void refresh()}>Refresh status</button></div>
      {state&&<div className="forecast-guidance"><span>ⓘ</span><div><strong>{!state.configured?"AWS workspace required":state.quota<1?"Awaiting AWS training-quota approval":state.ready?"Ready to train":"Training safeguard active"}</strong><p>{state.reason}</p>{state.quota!==undefined&&<p>Available instance quota: {state.quota}. Region: ap-southeast-2. {state.cooldown_seconds>0?`Next submission in about ${Math.ceil(state.cooldown_seconds/3600)} hours.`:""}</p>}</div></div>}
      <p>Training can incur AWS charges. The runtime limit does not cap all provisioning, storage or log costs. The project’s USD 20 budget alert is not a hard spending cap.</p>
      <label className="managed-training-consent"><input type="checkbox" checked={accepted} onChange={e=>setAccepted(e.target.checked)}/>I understand this starts one billable SageMaker training job on synthetic demo data.</label>
      <div className="real-sample-actions"><button className="primary-button" disabled={busy||!state?.ready||!accepted} onClick={()=>void start()}>{busy?"Working…":"Start SageMaker demo training"}</button><a className="select-button" href="https://aws.amazon.com/sagemaker/ai/pricing/" target="_blank" rel="noreferrer">AWS pricing ↗</a></div>
    </section>
    {error&&<p className="forecast-error" role="alert">{error}</p>}{notice&&<p className="forecast-guidance" role="status">{notice}</p>}
    {job&&<section className="panel"><div className="panel-heading"><div><h2>2. Review training and evaluation</h2><p className="managed-job-name">{job.job_name}</p></div><span className="forecast-data-badge">{job.status}</span></div><p>Stage: {job.secondary_status||job.status}. Training time: {job.training_seconds??"Not reported yet"} seconds. Billable time: {job.billable_seconds??"Not reported yet"} seconds.</p>{job.failure_reason&&<p className="forecast-error">{job.failure_reason}</p>}<p>The artifact must match the source data and code checksums, reproduce its saved predictions and pass the demo evaluation gate before activation.</p><button className="primary-button" disabled={busy||job.status!=="Completed"} onClick={()=>void activate()}>Validate & use this model</button><p className="muted-cell">Activation preserves invoices and inventory. It produces a clearly labelled demo demand forecast.</p></section>}
    <section className="panel"><h2>3. View the active model</h2>{state?.active_model?<><p><strong>SageMaker training → Lambda predictions</strong></p><p className="managed-job-name">Version: {state.active_model.job_name}</p><p>Demo test WAPE: {(Number(state.active_model.metrics?.wape)*100).toFixed(2)}% · {state.active_model.metrics?.holdout_observations} test product-days. This is not real-business accuracy.</p><button className="select-button" onClick={onShowForecast}>View forecast results →</button></>:<p>No SageMaker model is active yet. Existing Lambda-trained forecasts remain available under Sales forecast.</p>}</section>
  </div>;
}
