import {useEffect,useState} from "react";
import {apiFetch} from "./auth";
const API=import.meta.env.VITE_API_BASE_URL||(import.meta.env.DEV?"http://127.0.0.1:8000/api/v1":"/api/v1");

export default function RealInvoiceSample(){
  const [report,setReport]=useState<any>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [direction,setDirection]=useState("ALL");
  const [trained,setTrained]=useState(false);
  async function load(train=false){
    setBusy(true);setError("");
    try{
      const response=await apiFetch(`${API}/intelligence/reviewed-sample${train?"/train":""}`,{method:train?"POST":"GET"});
      const data=await response.json();if(!response.ok)throw new Error(data.detail||"Could not load reviewed bills");setReport(data);if(train)setTrained(true);
    }catch(e){setError(e instanceof Error?e.message:"Analysis failed");}finally{setBusy(false);}
  }
  useEffect(()=>{void load();},[]);
  const money=(v:number)=>new Intl.NumberFormat("en-IN",{style:"currency",currency:"INR",maximumFractionDigits:2}).format(v);
  const download=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download="reviewed-real-invoices-model-results.json";a.click();URL.revokeObjectURL(url);};
  const m=report?.sales_value_model;
  return <div className="real-sample-workspace">
    <section className="forecast-intro"><div><span className="forecast-eyebrow">YOUR SUPPLIED FILES · SOURCE CHECKED</span><h2>Real invoice analysis</h2><p>Ten purchase images and ten sales PDFs, selected from your data folders and checked against the originals. Source files and the inventory ledger are tracked separately.</p></div></section>
    {error&&<p className="forecast-error" role="alert">{error}</p>}
    {busy&&<p role="status">Training and checking the reviewed sample…</p>}
    {report&&<>
      <div className="forecast-stat-grid"><article><span>Purchase bills</span><strong>10</strong><small>{money(report.summary.purchase_total)} · original totals</small></article><article><span>Sales bills</span><strong>10</strong><small>{money(report.summary.sales_total)} · original totals</small></article><article><span>Source lines checked</span><strong>{report.summary.reviewed_lines}</strong><small>No synthetic rows · no held-out PDFs used</small></article></div>
      <section className="panel real-sample-result"><div className="panel-heading"><div><span className="forecast-eyebrow">SAMPLE MODEL RESULT</span><h2>What might the next recorded bill be worth?</h2><p>Net invoice value, before tax. This estimates order size in the selected sample; it does not predict when an order will happen.</p></div><span className="forecast-data-badge">Real bills · limited history</span></div>
        <strong className="real-sample-estimate">{money(m.next_recorded_invoice_estimate)}</strong><p>Experimental estimate · {m.quality_gate==="FAILED_BASELINE_CHECK"?"failed the test baseline comparison; do not use for business decisions":"limited sample; reference only"}.</p><p>Selected method: {m.selected_algorithm.replaceAll("_"," ")}. Fitted again on all 10 reviewed sales after evaluation.</p>
        <div className="forecast-guidance"><span>ⓘ</span><div><strong>Why there is no daily forecast here</strong><p>These ten selected bills do not cover every sale. Missing dates cannot be assumed to mean zero sales. The purchase bills also come after these sales, so earlier stock and profit cannot be confirmed.</p></div></div>
        <p>Exploratory estimate range: <strong>{money(m.estimate_range?.lower||0)} to {money(m.estimate_range?.upper||0)}</strong>. Calibrated from {m.estimate_range?.calibration_rows||0} earlier prediction errors; reliable probability coverage is not established.</p>
        <details className="forecast-detail-panel"><summary>Compare model candidates and rolling validation</summary><p>Each fold trains on earlier dates and predicts later recorded invoices. Same-day bills stay in one block. These comparisons exclude the final test invoices.</p><div className="table-scroll"><table><thead><tr><th>METHOD</th><th>VALIDATION MAE</th><th>VALIDATION WAPE</th><th>OBSERVATIONS</th></tr></thead><tbody>{Object.entries(m.rolling_validation_scores||{}).map(([name,value])=>{const score=value as any;return <tr key={name}><td>{name.replaceAll("_"," ")}{name===m.selected_algorithm?" · selected":""}</td><td>{money(score.mae)}</td><td>{score.wape==null?"Unavailable":`${(score.wape*100).toFixed(1)}%`}</td><td>{score.holdout_observations}</td></tr>;})}</tbody></table></div></details>
        <div className="real-sample-actions"><button className="primary-button" disabled={busy} onClick={()=>void load(true)}>Train & evaluate these 20 bills</button><button className="select-button" onClick={download}>Download results & model</button></div>
        {trained&&<p role="status">Evaluation completed on the reviewed real bills. Model quality: {m.quality_gate==="FAILED_BASELINE_CHECK"?"failed baseline comparison":"limited sample evidence"}. Stock was not changed.</p>}
      </section>
      <details className="forecast-detail-panel"><summary><span>Check the model's test results<small>{m.training_rows} training invoices · {m.validation_rows} validation invoices · {m.test_rows} separate test invoices</small></span><b>＋</b></summary>
        <p>Test error (WAPE): <strong>{(m.test_metrics.wape*100).toFixed(1)}%</strong>. Last-invoice baseline: {(m.last_invoice_baseline_metrics.wape*100).toFixed(1)}%. Lower is better. Two test invoices are too few to establish reliable business accuracy.</p>
        <div className="table-scroll"><table><thead><tr><th>TEST DATE</th><th>INVOICE</th><th>ACTUAL NET VALUE</th><th>PREDICTED NET VALUE</th></tr></thead><tbody>{m.test_predictions.map((r:any)=><tr key={r.invoice_number}><td>{r.date}</td><td>{r.invoice_number}</td><td>{money(r.actual)}</td><td>{money(r.predicted)}</td></tr>)}</tbody></table></div>
        <p>Six predefined methods compete in expanding-window validation using only earlier invoices. Final test values do not choose the method. The result is a sample reference, not a forecast of monthly revenue, product demand or profit.</p>
      </details>
      <section className="panel"><div className="panel-heading"><div><h2>Review the selected bills</h2><p>Original dates, HSN codes, quantities and amounts. Source corrections are listed for each bill. Check Invoices and Inventory for approval and stock status.</p></div><select aria-label="Filter reviewed bill direction" value={direction} onChange={e=>setDirection(e.target.value)}><option value="ALL">All 20 bills</option><option value="PURCHASE">10 purchases</option><option value="SALE">10 sales</option></select></div>
        {report.documents.filter((d:any)=>direction==="ALL"||d.direction===direction).map((d:any)=><details className="real-sample-bill" key={d.sha256}><summary><span><strong>{d.filename}</strong><small>{d.invoice_number} · {d.date}</small></span><span className="forecast-data-badge">{d.direction==="SALE"?"Sale":"Purchase"}</span><strong>{money(Number(d.total))}</strong></summary>
          <div className="table-scroll"><table><thead><tr><th>PRODUCT / SERVICE</th><th>HSN / SAC</th><th>QTY</th><th>PRINTED RATE</th><th>NET AMOUNT</th></tr></thead><tbody>{d.lines.map((r:any,i:number)=><tr key={i}><td>{r.description}{r.is_stock_item===false&&<small> · service / licence / expense</small>}</td><td>{r.hsn_sac}</td><td>{r.quantity}</td><td>{money(Number(r.unit_rate))}</td><td>{money(Number(r.line_amount))}</td></tr>)}</tbody></table></div>
          <p>Subtotal {money(Number(d.subtotal))} · source tax {money(Number(d.tax_total))} · rounding difference {money(Number(d.round_off))}. Corrected fields: {d.corrected_fields.join(", ")}.</p>{d.printed_subtotal_difference!==0&&<p>Original subtotal differs from the sum of printed line amounts by {money(d.printed_subtotal_difference)}. Source values are preserved.</p>}<small>Source SHA256: {d.sha256}. Source-checked analysis; invoice approvals are tracked separately.</small>
        </details>)}
      </section>
    </>}
  </div>;
}
