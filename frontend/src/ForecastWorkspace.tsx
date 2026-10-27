import {useEffect, useState} from "react";
import type {ReactNode} from "react";
import MonthlyPlanner from "./MonthlyPlanner";

type Props = {
  forecast: any; invoices: any[]; inventory: any[]; horizon: number; busy: boolean; error: string;
  onHorizon: (days: number) => void; onSales: () => void;
  onUpload: () => void; onReview: () => void; pricing: ReactNode; plan: ReactNode;
  onFeedback: (run: any, product: string, day: string, event: any) => void;
};

function saveFile(name: string, text: string, type: string) {
  const url=URL.createObjectURL(new Blob([text],{type}));
  const link=document.createElement("a");link.href=url;link.download=name;link.click();URL.revokeObjectURL(url);
}

export default function ForecastWorkspace(p: Props) {
  const [tab,setTab]=useState("monthly");
  const [source,setSource]=useState("business");
  const [complete,setComplete]=useState(false);
  const [product,setProduct]=useState("all");
  const [page,setPage]=useState(0);
  useEffect(()=>{setProduct("all");setPage(0);if(p.forecast?.metrics?.data_source==="reviewed_demo_invoices")setSource("business");},[p.forecast?.id]);
  const approved=p.invoices.filter(i=>i.status==="APPROVED");
  const businessRecords=approved.filter(i=>!String(i.source_type||"").startsWith("DEMO"));
  const sales=businessRecords.filter(i=>i.transaction_type==="SALE"&&i.lines?.some((l:any)=>l.is_stock_item!==false));
  const pending=p.invoices.filter(i=>i.status==="NEEDS_REVIEW"&&i.transaction_type==="SALE").length;
  const rows=(p.forecast?.forecasts||[]).filter((r:any)=>product==="all"||String(r.product_id)===product);
  const productIds=[...new Set<string>((p.forecast?.forecasts||[]).map((r:any)=>String(r.product_id)))];
  const name=(id:string)=>p.inventory.find(i=>String(i.id)===id)?.name||id.replace(/^DEMO_/,"").replaceAll("_"," ").toLowerCase();
  const daily=new Map<string,number>();
  for(const row of rows)daily.set(row.forecast_date,(daily.get(row.forecast_date)||0)+Number(row.predicted_quantity));
  const days=[...daily.entries()].sort(([a],[b])=>a.localeCompare(b));
  const maximum=Math.max(1,...days.map(([,q])=>q));
  const points=days.map(([,q],i)=>`${52+i*716/Math.max(1,days.length-1)},${198-q/maximum*160}`).join(" ");
  const total=rows.reduce((sum:number,r:any)=>sum+Number(r.predicted_quantity),0);
  const pageCount=Math.max(1,Math.ceil(rows.length/14));
  const currentPage=Math.min(page,pageCount-1);
  const demo=!!p.forecast?.metrics?.demo;
  return <div className="forecast-workspace">
    <div className="forecast-tabs" role="tablist" aria-label="Planning tools">
      <button role="tab" aria-selected={tab==="monthly"} onClick={()=>setTab("monthly")}>Monthly product plan <span>Units, prices &amp; revenue</span></button>
      <button role="tab" aria-selected={tab==="demand"} onClick={()=>setTab("demand")}>Sales forecast <span>How much will sell?</span></button>
      <button role="tab" aria-selected={tab==="pricing"} onClick={()=>setTab("pricing")}>Costs & pricing <span>What should I charge?</span></button>
    </div>
    {tab==="monthly" ? <MonthlyPlanner/> : tab==="pricing" ? <div className="forecast-pricing-view"><div className="forecast-intro"><span className="forecast-eyebrow">COSTS & PRICING</span><h2>Turn purchase history into a price plan.</h2><p>Choose your business records or explore the supplied dataset. Then search for a product to see its cost, selling rate and profit scenario.</p></div>{p.pricing}</div> : <>
      <section className="forecast-intro"><div><span className="forecast-eyebrow">PLAN AHEAD</span><h2>See what could sell next.</h2><p>Choose your data, pick a period, and generate a forecast. Training and predictions run on demand in AWS Lambda. Use complete real sales history for daily demand forecasts.</p></div><span className="forecast-intro-symbol" aria-hidden="true">↗</span></section>
      <section className="forecast-builder" aria-label="Create a forecast">
        <div className="forecast-step"><span>1</span><div><h3>Choose your data</h3><p>Daily demand needs complete, approved product sales history.</p></div></div>
        <div className="forecast-source-grid">
          <button className={`forecast-source ${source==="business"?"chosen":""}`} aria-pressed={source==="business"} onClick={()=>setSource("business")}><span className="forecast-source-icon">▤</span><span><strong>My business sales</strong><span>Use approved real product sales with matched stock.</span><em>{sales.length} approved product sale{sales.length===1?"":"s"} · {pending} waiting for review</em></span><i aria-hidden="true">{source==="business"?"●":"○"}</i></button>
        </div>
        {source==="business" && (sales.length===0 ? <div className="forecast-guidance"><span>ⓘ</span><div><strong>Add sales history first</strong><p>Purchase bills fill inventory. To predict demand, approve sales invoices with matched products. Service-only invoices do not count as product sales.</p><button className="forecast-link" onClick={p.onReview}>Review sales invoices →</button></div></div> : <label className="forecast-confirm"><input type="checkbox" checked={complete} onChange={e=>setComplete(e.target.checked)}/><span>I have entered every sale for the covered period.<small>Dates without a recorded sale will count as zero. At least four daily observations are needed; 56+ days gives a stronger starting point.</small></span></label>)}
        <div className="forecast-builder-bottom"><div><div className="forecast-step"><span>2</span><div><h3>How far ahead?</h3><p>Start with a week or a month.</p></div></div><div className="forecast-periods" aria-label="Forecast horizon">{[7,30,90].map(n=><button key={n} aria-pressed={p.horizon===n} onClick={()=>p.onHorizon(n)}>{n===7?"1 week":n===30?"1 month":"3 months"}</button>)}<label className="forecast-custom-period"><span>More</span><select aria-label="Other forecast horizons" value={[7,30,90].includes(p.horizon)?"":p.horizon} onChange={e=>e.target.value&&p.onHorizon(Number(e.target.value))}><option value="">Choose…</option><option value="60">2 months</option><option value="180">6 months</option><option value="365">1 year</option></select></label></div></div>
          <div className="forecast-generate"><button className="primary-button" disabled={p.busy||(source==="business"&&(!sales.length||!complete))} onClick={()=>{setPage(0);p.onSales();}}>{p.busy?"Generating forecast…":"Forecast my sales"}<span aria-hidden="true">→</span></button><small>{p.horizon} days ahead · approved sales only</small></div>
        </div>
        {p.error&&<div className="forecast-error" role="alert">{p.error}</div>}
        <details className="forecast-advanced"><summary>Have your own daily sales file?</summary><p>Upload a CSV with <code>product_id,date,quantity_sold</code>. Include every calendar day; enter zero only when you know there were no sales.</p><button className="select-button" disabled={p.busy} onClick={p.onUpload}>Upload daily sales CSV</button><a href="/sales_daily_template.csv" download>Download template ↗</a></details>
      </section>
      {p.forecast ? <div className="forecast-results" aria-label="Saved forecast results">
        <div className="forecast-results-heading"><div><span className="forecast-eyebrow">LATEST SAVED RESULT</span><h2>Your forecast, at a glance</h2></div><span className={`forecast-data-badge ${demo?"demo":""}`}>{demo?"Demo · synthetic sales":"Business sales"}</span></div>
        {demo&&<p className="forecast-demo-note">This is a presentation example, not a prediction of your business sales. It leaves invoices and stock unchanged.</p>}
        <div className="forecast-stat-grid"><article><span>Expected units</span><strong>{Math.round(total).toLocaleString()}</strong><small>{product==="all"?"Across all forecast products":name(product)}</small></article><article><span>Forecast period</span><strong>{days.length}<em> days</em></strong><small>{days[0]?.[0]} to {days.at(-1)?.[0]}</small></article><article><span>Products covered</span><strong>{product==="all"?productIds.length:1}</strong><small>View each product in the chart below</small></article></div>
        <section className="forecast-chart-panel"><div className="forecast-chart-heading"><div><h3>Expected sales over time</h3><p>Estimated units per day</p></div><label><span className="sr-only">Chart product</span><select value={product} onChange={e=>{setProduct(e.target.value);setPage(0);}}><option value="all">All products</option>{productIds.map(id=><option key={id} value={id}>{name(id)}</option>)}</select></label></div>
          <svg className="forecast-line-chart" viewBox="0 0 800 240" role="img" aria-label={`Expected sales for ${days.length} days`}><defs><linearGradient id="forecastArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#2d7650" stopOpacity=".2"/><stop offset="100%" stopColor="#2d7650" stopOpacity="0"/></linearGradient></defs>{[0,.25,.5,.75,1].map(t=><g key={t}><line x1="52" x2="768" y1={198-t*160} y2={198-t*160} stroke="#e7ede8" strokeDasharray="4 5"/><text x="40" y={202-t*160} textAnchor="end">{Math.round(t*maximum)}</text></g>)}{days.length>0&&<><polygon points={`52,198 ${points} 768,198`} fill="url(#forecastArea)"/><polyline points={points} fill="none" stroke="#28734b" strokeWidth="2.8" strokeLinejoin="round" strokeLinecap="round"/></>}<text x="52" y="226">{days[0]?.[0]}</text><text x="768" y="226" textAnchor="end">{days.at(-1)?.[0]}</text></svg>
          <div className="forecast-chart-footer"><span><i/>Forecast units · {demo?"demo":"business"} data</span><button className="forecast-link" onClick={()=>saveFile("sales-forecast.csv",["product_id,date,predicted_units",...rows.map((r:any)=>`"${String(r.product_id).replaceAll('"','""')}",${r.forecast_date},${r.predicted_quantity}`)].join("\r\n"),"text/csv")}>Download forecast ↓</button></div>
        </section>
        {(p.forecast.metrics.forecast_warnings||[]).length>0&&<div className="forecast-guidance"><span>ⓘ</span><div><strong>Use longer forecasts as planning scenarios</strong><p>There is limited history for this forecast period. Long-term patterns and annual seasonality may not be established.</p><details><summary>Read forecast limits</summary><ul>{p.forecast.metrics.forecast_warnings.map((w:string)=><li key={w}>{w}</li>)}</ul></details></div></div>}
        <details className="forecast-detail-panel"><summary><span>Monthly profit & stock plan<small>See estimated revenue, gross profit and purchase funding when product costs are available.</small></span><b>＋</b></summary>{p.plan}</details>
        <details className="forecast-detail-panel"><summary><span>Daily forecast breakdown<small>View product predictions or add actual sales for comparison.</small></span><b>＋</b></summary><div className="table-scroll"><table><thead><tr><th>PRODUCT</th><th>DATE</th><th>EXPECTED UNITS</th><th>RECORD ACTUAL SALES</th></tr></thead><tbody>{rows.slice(currentPage*14,(currentPage+1)*14).map((r:any)=><tr key={`${r.product_id}-${r.forecast_date}`}><td>{name(String(r.product_id))}</td><td>{r.forecast_date}</td><td>{Number(r.predicted_quantity).toFixed(1)}</td><td><form className="forecast-feedback" onSubmit={e=>p.onFeedback(p.forecast.id,r.product_id,r.forecast_date,e)}><input aria-label={`Actual units for ${r.product_id} on ${r.forecast_date}`} name="actual_quantity" type="number" min="0" step=".001" required placeholder="Units"/><input name="comment" aria-label="Actual sales note" placeholder="Note (optional)"/><button className="select-button">Save</button></form></td></tr>)}</tbody></table></div><div className="forecast-pagination"><span>Page {currentPage+1} of {pageCount}</span><button disabled={currentPage===0} onClick={()=>setPage(currentPage-1)}>Previous</button><button disabled={currentPage+1>=pageCount} onClick={()=>setPage(currentPage+1)}>Next</button></div></details>
        <details className="forecast-detail-panel"><summary><span>How reliable is this forecast?<small>Model choices, accuracy measurements and downloadable model files.</small></span><b>＋</b></summary><p>Accuracy below is measured on a separate short test period. It does not establish accuracy for the full forecast horizon.{demo?" These results use synthetic demonstration data.":""}</p><div className="table-scroll"><table><thead><tr><th>PRODUCT</th><th>SELECTED METHOD</th><th>AVERAGE ERROR (UNITS)</th><th>RELATIVE ERROR (WAPE)</th></tr></thead><tbody>{Object.entries(p.forecast.metrics.products||{}).map(([id,raw])=>{const m=raw as any;return <tr key={id}><td>{name(id)}</td><td>{m.evaluation_model_name?.replaceAll("_"," ")}{m.limited_history?" · limited history":""}</td><td>{m.mae}</td><td>{m.wape==null?"Not measurable":`${(m.wape*100).toFixed(1)}%`}</td></tr>;})}</tbody></table></div><button className="select-button" onClick={()=>saveFile("demand-model.json",JSON.stringify({model_name:p.forecast.model_name,metrics:p.forecast.metrics},null,2),"application/json")}>Download trained model</button></details>
      </div> : <section className="forecast-empty"><span>⌁</span><h3>Your first forecast starts here</h3><p>Review real sales or upload complete daily sales history, then select <strong>Forecast my sales</strong>.</p></section>}
    </>}
  </div>;
}
