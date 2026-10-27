import { useEffect, useRef, useState } from "react";
import { buildMonthlyPlan } from "./forecastPlan";
import ForecastWorkspace from "./ForecastWorkspace";
import { apiFetch, authConfigured, beginLogin, completeLogin, signOut } from "./auth";

const navItems = ["Overview", "Invoices", "Sales", "Inventory", "Forecasts", "Payments"];
const API = import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? "http://127.0.0.1:8000/api/v1" : "/api/v1");

function CommercialModels() {
  const [report, setReport] = useState<any>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [units, setUnits] = useState<Record<string, string>>({});
  const load = async (dataset: boolean | "reviewed") => {
    setBusy(true); setError("");
    try {
      const response = await apiFetch(`${API}/intelligence${dataset === "reviewed" ? "/reviewed-sample" : dataset ? "/dataset" : ""}`);
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Model analysis failed");
      setReport(dataset === "reviewed" ? result.commercial : result);
    } catch (e) { setError(e instanceof Error ? e.message : "Model analysis failed"); }
    finally { setBusy(false); }
  };
  const money = (v: any, currency = "INR") => v == null ? "Unavailable" : `${currency} ${Number(v).toFixed(2)}`;
  const algorithm = (v: string) => (({last_observed_price: "Last observed price", supplier_last_price: "Supplier's last comparable price"} as Record<string, string>)[v] || v?.replaceAll("_", " ") || "More history needed");
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: "application/json"}));
    const link = document.createElement("a"); link.href = url; link.download = "commercial-model-results.json"; link.click(); URL.revokeObjectURL(url);
  };
  const products = (report?.products || []).filter((p: any) => `${p.product_name} ${p.product_id}`.toLowerCase().includes(search.toLowerCase()));
  return <section className="panel commercial-workspace">
    <div className="panel-heading"><div><h2>Choose which records to explore</h2><p>Compare purchase costs, choose a selling rate, and estimate gross profit.</p></div></div>
    <div style={{display: "flex", gap: 8, flexWrap: "wrap"}}>
      <button className="primary-button" disabled={busy} onClick={() => void load("reviewed")}>Reviewed real bills (10 + 10)</button>
      <button className="select-button" disabled={busy} onClick={() => void load(false)}>My business records</button>
      <button className="select-button" disabled={busy} onClick={() => void load(true)}>Explore supplied dataset</button>
    </div>
    {busy && <p role="status">Training and evaluating model candidates…</p>}{error && <p role="alert">{error}</p>}
    {report && <>
      <p><strong>{report.data_source === "reviewed_real_sample" ? "20 reviewed real bills — partial sample, not posted to stock" : report.data_source === "supplied_invoice_dataset" ? "Supplied invoice dataset — extracted, unverified labels" : report.data_source === "reviewed_demo_invoices" ? "Reviewed demo invoices — sample data only" : "Approved business records"}</strong></p>
      {report.usable_price_rows !== undefined && <p>{report.source_documents} documents · {report.usable_price_rows} price observations · {report.synthetic_rows} synthetic rows. {report.test_note}</p>}
      <div className="commercial-summary"><span><strong>{report.products.length}</strong> products to explore</span><p>Search below for purchase costs, suggested selling rates and profit examples. Limited history uses the last comparable price.</p></div>
      <details className="commercial-diagnostics"><summary>Model accuracy & technical details</summary>
      <div className="table-scroll"><table><thead><tr><th>MODEL</th><th>SELECTED METHOD</th><th>VALIDATION ROWS</th><th>TEST ROWS</th><th>TEST WAPE</th><th>STATUS</th></tr></thead><tbody>
        {[["Purchase cost", report.shared_purchase_model], ["Selling price", report.shared_selling_price_model]].map(([name, raw]) => {
          const m = raw as any;
          return <tr key={String(name)}><td><strong>{String(name)}</strong></td><td>{algorithm(m?.selected_algorithm || m?.model?.algorithm)}</td>
            <td>{m?.comparable_validation_rows ?? 0}</td><td>{m?.evaluated_known_product_rows ?? 0}</td>
            <td>{m?.test_metrics?.wape == null ? "Not measurable" : `${(m.test_metrics.wape * 100).toFixed(2)}%`}</td>
            <td>{m?.quality_gate === "passed_baseline_check" ? "Passed baseline comparison" : m?.status === "trained" ? "Limited evidence — reference only" : "More repeated history needed"}</td></tr>;
        })}
      </tbody></table></div>
      <p className="muted-cell">Methods compete on earlier validation dates; final test dates are kept separate. Price changes are learned within the same product and unit. When regression does not validate better, the app uses the last comparable price. A low test-row count does not establish dependable accuracy.</p>
      <details><summary>View model validation and baseline comparison</summary>
        {[["Purchase cost", report.shared_purchase_model], ["Selling price", report.shared_selling_price_model]].map(([name, raw]) => {
          const m = raw as any;
          return <div key={String(name)}><h3>{String(name)}</h3><p>Test starts {m?.test_start || "not available"}. Unseen product rows excluded from test accuracy: {m?.unseen_product_test_rows ?? 0}. Last-price baseline WAPE: {m?.last_price_baseline_metrics?.wape == null ? "unavailable" : `${(m.last_price_baseline_metrics.wape * 100).toFixed(2)}%`}.</p>
            <div className="table-scroll"><table><thead><tr><th>CANDIDATE</th><th>VALIDATION MAE</th><th>VALIDATION WAPE</th></tr></thead><tbody>{Object.entries(m?.validation_scores || {}).map(([method, metric]) => {const score = metric as any; return <tr key={method}><td>{algorithm(method)}</td><td>{score.mae}</td><td>{score.wape == null ? "unavailable" : `${(score.wape * 100).toFixed(2)}%`}</td></tr>;})}</tbody></table></div>
          </div>;
        })}
      </details>
      <button className="select-button" onClick={download}>Download trained artifacts and evaluation</button>
      <p>Price estimates cover 30 days after the latest observed rate. Profit is a conditional calculation using cost, selling rate and units; it is not a learned profit or customer-conversion model.</p>
      </details>
      <label className="commercial-search">Find a product<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search product or SKU" style={{display: "block", width: "100%", margin: "8px 0 14px", padding: 10}}/></label>
      <p className="muted-cell">{products.length} of {report.products.length} products</p>
      <div className="table-scroll"><table><thead><tr><th>PRODUCT / UNIT</th><th>COST OUTLOOK</th><th>SELLING PRICE / FLOOR</th><th>PURCHASE ADVICE</th><th>GROSS PROFIT SCENARIO</th></tr></thead><tbody>
        {products.map((p: any) => {
          const key = `${p.product_id}-${p.unit}-${p.currency}`;
          const q = Number(units[key] ?? p.planning_scenario?.assumed_units ?? "0");
          const costEstimate = report.shared_purchase_model?.estimates?.find((x: any) => x.product_id === p.product_id && x.unit === p.unit);
          const saleEstimate = report.shared_selling_price_model?.estimates?.find((x: any) => x.product_id === p.product_id && x.unit === p.unit);
          const reference = report.shared_purchase_model?.quality_gate !== "passed_baseline_check";
          const scenario = q >= 0 && p.current_unit_cost != null && p.selling_price.suggested != null ? q * (p.selling_price.suggested - p.current_unit_cost) : null;
          return <tr key={key}><td><strong>{p.product_name || p.product_id}</strong><small style={{display: "block"}}>{p.unit} · {p.currency}</small></td>
            <td>Last cost: {money(p.current_unit_cost, p.currency)}
              {p.purchase_cost_model.status === "trained" ? <small style={{display: "block"}}>{algorithm(p.purchase_cost_model.model.algorithm)}: {money(p.purchase_cost_model.predicted_price, p.currency)}</small>
              : costEstimate ? <small style={{display: "block"}}>{reference ? "Reference" : "Model outlook"}: {money(reference ? costEstimate.last_price : costEstimate.predicted_price, p.currency)} · {costEstimate.history_dates ?? 1} dated observations</small>
              : <small style={{display: "block"}}>No comparable purchase history</small>}
            </td>
            <td><strong>{money(p.selling_price.suggested, p.currency)}</strong><small style={{display: "block"}}>Floor: {money(p.selling_price.floor, p.currency)}<br/>{report.data_source === "reviewed_real_sample" ? "Sample price reference" : p.selling_price.basis === "margin_scenario_rule" ? "Configured margin rule" : "Validated sale model with margin floor"}</small>
              {report.data_source === "reviewed_real_sample" && <small style={{display:"block"}}>Sample analysis · no business margin settings applied</small>}
              {saleEstimate && <small style={{display: "block"}}>Observed sales reference: {money(saleEstimate.last_price, p.currency)}</small>}
            </td>
            <td>{p.purchase_advisor.advice}{p.purchase_advisor.best_observed_supplier && <small style={{display: "block"}}>Lowest recorded comparable quote: {p.purchase_advisor.best_observed_supplier.party} · {money(p.purchase_advisor.best_observed_supplier.latest_price, p.currency)} · {p.purchase_advisor.best_observed_supplier.last_observed_date}</small>}<small style={{display: "block"}}>Reorder shortfall: {p.purchase_advisor.reorder_quantity ?? "Complete demand and stock history needed"}</small></td>
            <td>{p.profit_projection && <p>Matched demand scenario: {money(p.profit_projection.gross_profit, p.currency)}</p>}<label>Scenario units<input type="number" min="0" step="1" value={units[key] ?? p.planning_scenario?.assumed_units ?? ""} placeholder="Enter planning quantity" onChange={(event) => setUnits((current) => ({...current, [key]: event.target.value}))}/></label><small style={{display: "block"}}>{scenario == null ? "Comparable cost and selling price needed" : `Gross profit: ${money(scenario, p.currency)}`}<br/>{p.planning_scenario && !units[key] && "Uses the last purchase quantity as a planning assumption. "}Before tax, expenses and returns.</small></td>
          </tr>;
        })}
        {products.length === 0 && <tr><td colSpan={5}>No matching comparable product history.</td></tr>}
      </tbody></table></div>
    </>}
  </section>;
}

function ForecastPlan({forecast, inventory, policy}: {forecast: any; inventory: any[]; policy: any}) {
  const rows = buildMonthlyPlan(forecast.forecasts || [], inventory,
    (product) => sellPrice(Number(product.unit_cost), effectiveMargins(product, policy).target));
  return <section className="panel" style={{marginTop: 14}}><div className="panel-heading"><div><h2>Monthly demand and profit plan</h2><p>Gross profit and purchase cash use current inventory cost and configured target margins. They are conditional scenarios, before overheads, returns and payment timing.</p></div></div>
    {forecast.metrics.demo && <p className="muted-cell">Demo figures are planning examples. Money amounts use mapped demo costs where available; they are not actual business profit.</p>}
    <div className="table-scroll"><table><thead><tr><th>MONTH</th><th>FORECAST UNITS</th><th>COST COVERAGE</th><th>PLANNED REVENUE</th><th>GROSS PROFIT</th><th>STOCK SHORTFALL CASH</th></tr></thead><tbody>{rows.map((row) => {
      const complete = row.priced === row.products;
      return <tr key={row.month}><td>{row.month}</td><td>{row.units.toFixed(1)}</td><td>{row.priced} / {row.products} products</td><td>{complete ? `₹${row.revenue.toFixed(2)}` : "Cost mapping needed"}</td><td>{complete ? `₹${(row.revenue-row.cost).toFixed(2)}` : "Cost mapping needed"}</td><td>{complete ? `₹${row.purchaseCash.toFixed(2)}` : "Cost mapping needed"}</td></tr>;
    })}</tbody></table></div>
    <p className="muted-cell">Stock shortfall cash assumes no scheduled deliveries and values missing units at current cost. It is not a cash-flow forecast or an automatic purchase order.</p>
  </section>;
}

function ReviewSignals({ invoice, inventory = [], onResolveStock, onMissingPurchase }: { invoice: any; inventory?: any[]; onResolveStock?: () => void; onMissingPurchase?: () => void }) {
  const matches = invoice.duplicate_matches || [];
  const textractUsed = ["RESULTS_LOADED", "SUCCEEDED", "PARTIAL_SUCCESS"].includes(invoice.extraction_status);
  const confidence = invoice.field_confidence || {};
  const validationWarnings: string[] = invoice.validation_warnings || [];
  const fields = [
    ["vendor_name", "Vendor", invoice.vendor_name],
    ["invoice_number", "Invoice number", invoice.invoice_number],
    ["invoice_date", "Date", invoice.invoice_date],
    ["grand_total", "Grand total", invoice.grand_total],
  ];
  const checks = textractUsed ? fields.flatMap(([key, label, value]: any) => {
    if (value === null || value === undefined || value === "") return [`${label}: not found`];
    const score = confidence[key];
    if (typeof score === "number" && score < 0.85) return [`${label}: ${Math.round(score * 100)}% confidence`];
    if (typeof score !== "number") return [`${label}: confidence unavailable`];
    return [];
  }) : [];
  const lowConfidenceLines = textractUsed ? (invoice.lines || []).flatMap((line: any, index: number) =>
    typeof line.confidence === "number" && line.confidence < 0.85
      ? [`Item ${index + 1}: ${Math.round(line.confidence * 100)}% confidence`]
      : typeof line.confidence !== "number" ? [`Item ${index + 1}: confidence unavailable`] : []) : [];
  const belowCostLines = invoice.transaction_type === "SALE" ? (invoice.lines || []).flatMap((line: any, index: number) => {
    if (line.is_stock_item === false) return [];
    const byId = inventory.find((product: any) => String(product.id) === String(line.product_id));
    const description = String(line.description || "").trim().toLowerCase();
    const nameMatches = description ? inventory.filter((product: any) => String(product.name || "").trim().toLowerCase() === description) : [];
    const product = byId || (nameMatches.length === 1 ? nameMatches[0] : null);
    const cost = Number(product?.unit_cost || 0);
    const rate = Number(line.unit_rate);
    if (!product || cost <= 0 || !Number.isFinite(rate)) return [];
    const minimumMargin = product.minimum_margin_percent == null ? null : Number(product.minimum_margin_percent);
    const minimumPrice = minimumMargin == null ? cost : cost / (1 - minimumMargin / 100);
    return rate < minimumPrice
      ? [`Item ${index + 1} (${line.description || product.name}): sale rate ₹${rate.toFixed(2)} is below ${minimumMargin == null ? `recorded unit cost ₹${cost.toFixed(2)}` : `minimum-margin price ₹${minimumPrice.toFixed(2)}`}.`]
      : [];
  }) : [];

  const stockWarnings: string[] = [];
  if (invoice.transaction_type === "SALE" && invoice.status === "NEEDS_REVIEW" && invoice.source_document_kind !== "QUOTATION") {
    const required = new Map<string, {product: any; quantity: number}>();
    for (const [index, line] of (invoice.lines || []).entries()) {
      if (line.is_stock_item === false) continue;
      if (/charges|labour|rent/i.test(line.description || "") && String(line.hsn_sac || "").startsWith("99")) {
        stockWarnings.push(`Item ${index + 1}: service charge needs handling without a stock deduction.`);
        continue;
      }
      const matches = inventory.filter((product: any) => String(product.name || "").trim().toLowerCase() === String(line.description || "").trim().toLowerCase());
      const product = inventory.find((p: any) => String(p.id) === String(line.product_id)) || (matches.length === 1 ? matches[0] : null);
      if (!product) { stockWarnings.push(`Item ${index + 1}: confirm the product and its purchased stock. HSN alone cannot identify it.`); continue; }
      const key = String(product.id);
      required.set(key, {product, quantity: (required.get(key)?.quantity || 0) + Number(line.quantity || 0)});
    }
    for (const {product, quantity} of required.values()) {
      if (quantity > Number(product.quantity_on_hand || 0)) stockWarnings.push(`${product.name}: ${quantity} required, ${product.quantity_on_hand || 0} recorded in stock.`);
    }
  }

  return <div style={{ display: "grid", gap: 5, marginTop: 6, whiteSpace: "normal", minWidth: 180 }}>
    {invoice.source_document_kind === "QUOTATION" && <div role="alert" className="review-error">Quotation only. Upload the completed sales invoice before approval.</div>}
    {stockWarnings.length > 0 && <div role="alert" className="review-error"><strong>Record the missing stock first</strong><p className="review-help">Keep the sale date unchanged. Add the earlier purchase bill, or record an opening balance only if you have verified the quantity and date.</p><ul style={{margin: "4px 0 0", paddingLeft:18}}>{stockWarnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul><div className="review-help-actions">{onMissingPurchase && <button onClick={onMissingPurchase}>Add earlier purchase</button>}{onResolveStock && <button onClick={onResolveStock}>Record opening stock</button>}</div></div>}
    {matches.length > 0 && <div role="alert" style={{ color: "#9b5c17", background: "#fff6e8", borderRadius: 6, padding: "6px 8px", fontSize: 12 }}>
      Possible duplicate: {matches.map((match: any) => `${match.filename} (${match.match_type === "same_file" ? "same file" : "same vendor and invoice number"})`).join("; ")}
    </div>}
    {validationWarnings.length > 0 && <div role="status" style={{ color: "#8a5a18", background: "#fff6e8", borderRadius: 6, padding: "6px 8px", fontSize: 12 }}>
      <strong>Invoice checks — verify before approval</strong><ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{validationWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
    </div>}
    {belowCostLines.length > 0 && <div role="alert" style={{ color: "#9b3f31", background: "#fff0ed", borderRadius: 6, padding: "6px 8px", fontSize: 12 }}>
      <strong>Sale price below cost or configured minimum margin — check before approval</strong><ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{belowCostLines.map((warning: string) => <li key={warning}>{warning}</li>)}</ul>
    </div>}
    {validationWarnings.length === 0 && invoice.lines?.length > 0 && <div role="status" style={{ color: "#315f3d", background: "#edf6ef", borderRadius: 6, padding: "6px 8px", fontSize: 12 }}>Invoice totals and supplier details pass the available checks.</div>}
    {invoice.direction_reason && <div role="note" style={{ color: "#315f3d", background: "#edf6ef", borderRadius: 6, padding: "6px 8px", fontSize: 12 }}>{invoice.direction_reason}</div>}
    {textractUsed && <details className="review-extraction"><summary>Extraction details{checks.length || lowConfidenceLines.length ? " · compare with the original" : " · key fields passed confidence checks"}</summary><p>Unavailable confidence is not the same as an incorrect field. Compare the extracted values with the original document.</p><ul>{[...checks, ...lowConfidenceLines].map((item,index) => <li key={index}>{item}</li>)}</ul></details>}

  </div>;
}

function purchasePriceHistory(productId: string | number, invoices: any[]) {
  return invoices.flatMap((invoice) => {
    if (invoice.status !== "APPROVED" || invoice.transaction_type !== "PURCHASE") return [];
    return (invoice.lines || []).filter((line: any) => String(line.product_id || "") === String(productId)
      && line.unit_rate !== null && line.unit_rate !== undefined).map((line: any) => ({
        date: invoice.invoice_date || invoice.created_at,
        supplier: invoice.vendor_name || "Supplier not recorded",
        invoice_number: invoice.invoice_number || "No invoice number",
        unit: line.unit || "unit not recorded",
        currency: invoice.currency || "INR",
        unit_rate: Number(line.unit_rate),
        quantity: Number(line.quantity || 0),
      }));
  }).sort((left, right) => String(left.date).localeCompare(String(right.date)));
}

function purchasePriceSignal(history: any[]) {
  const latest = history.at(-1);
  const comparable = latest ? history.filter((row) => row.unit === latest.unit && row.currency === latest.currency) : [];
  if (comparable.length < 3) return "Not enough comparable purchases for a price trend";
  const recent = comparable.slice(-3).map((row) => row.unit_rate);
  if (recent[0] < recent[1] && recent[1] < recent[2]) return "Rising across the last 3 purchases";
  if (recent[0] > recent[1] && recent[1] > recent[2]) return "Falling across the last 3 purchases";
  return "Mixed across the last 3 purchases";
}

function effectiveMargins(product: any, policy: any) {
  const minimum = product?.minimum_margin_percent ?? policy?.minimum_margin_percent ?? null;
  let target = product?.target_margin_percent ?? policy?.target_margin_percent ?? minimum;
  if (minimum != null && target != null) target = Math.max(Number(minimum), Number(target));
  return { minimum: minimum == null ? null : Number(minimum), target: target == null ? null : Number(target) };
}

function sellPrice(cost: number, margin: number | null) {
  return margin == null ? cost : cost / (1 - margin / 100);
}

function minimumSaleRate(product: any, policy: any = {}) {
  if (!product) return 0;
  const cost = Number(product.unit_cost || 0);
  return sellPrice(cost, effectiveMargins(product, policy).minimum);
}

function purchasePriceInsights(history: any[], product: any) {
  const latest = history.at(-1);
  if (!latest) return null;
  const comparable = history.filter((row) => row.unit === latest.unit && row.currency === latest.currency);
  if (comparable.length < 3) return { supplier: null, estimate: null, trend: "Not enough comparable purchases", advice: "Record at least 3 approved purchases to estimate a price trend." };
  const recent = comparable.slice(-6);
  const meanX = (recent.length - 1) / 2;
  const meanY = recent.reduce((sum, row) => sum + row.unit_rate, 0) / recent.length;
  const numerator = recent.reduce((sum, row, index) => sum + (index - meanX) * (row.unit_rate - meanY), 0);
  const denominator = recent.reduce((sum, _, index) => sum + (index - meanX) ** 2, 0);
  const slope = denominator ? numerator / denominator : 0;
  const estimate = Math.max(0, meanY + slope * (recent.length - 1 - meanX + 1));
  const supplierLatest = new Map<string, any>();
  for (const row of comparable) supplierLatest.set(row.supplier, row);
  const supplier = [...supplierLatest.values()].sort((a, b) => a.unit_rate - b.unit_rate)[0];
  const changePercent = latest.unit_rate ? ((estimate - latest.unit_rate) / latest.unit_rate) * 100 : 0;
  const trend = changePercent > 2 ? `Estimated rise ${changePercent.toFixed(1)}%` : changePercent < -2 ? `Estimated fall ${Math.abs(changePercent).toFixed(1)}%` : "Estimated roughly stable";
  const advice = changePercent > 2 && product.low_stock
    ? "Prices trend upward and stock is low; review replenishment soon."
    : changePercent > 2
      ? "Prices trend upward, but stock is above its reorder point; avoid an automatic bulk-buy decision."
      : "No strong upward price signal; use the reorder point to plan purchases.";
  return { supplier, estimate, trend, advice, currency: latest.currency, unit: latest.unit };
}

function App() {
  const [active, setActive] = useState("Overview");
  const [uploadDirection, setUploadDirection] = useState("UNKNOWN");
  const [notice, setNotice] = useState("");
  const [reviewError, setReviewError] = useState("");
  const [invoices, setInvoices] = useState<any[]>([]);
  const [inventory, setInventory] = useState<any[]>([]);
  const [pricingPolicy, setPricingPolicy] = useState<any>({ minimum_margin_percent: null, target_margin_percent: null });
  const [pricingDraft, setPricingDraft] = useState<any>({ minimum_margin_percent: "", target_margin_percent: "" });
  const [serialAssets, setSerialAssets] = useState<any[]>([]);
  const [salesLines, setSalesLines] = useState<any[]>([{ product_id: "", quantity: "1", unit_rate: "", tax_rate: "", serial_numbers: "" }]);
  const [inventoryQuery, setInventoryQuery] = useState("");
  const [inventoryTypeFilter, setInventoryTypeFilter] = useState("ALL");
  const [missingHsnOnly, setMissingHsnOnly] = useState(false);
  const [priceHistoryOpen, setPriceHistoryOpen] = useState<Record<string, boolean>>({});
  const [forecast, setForecast] = useState<any>(null);
  const [forecastHorizon, setForecastHorizon] = useState(30);
  const [forecastBusy, setForecastBusy] = useState(false);
  const [forecastError, setForecastError] = useState("");
  const [movements, setMovements] = useState<Record<number, any[]>>({});
  const [documentPreview, setDocumentPreview] = useState<{ url: string; filename: string; contentType: string; objectUrl: boolean; invoice: any } | null>(null);
  const [documentLoading, setDocumentLoading] = useState(false);
  const [authState, setAuthState] = useState(authConfigured ? "loading" : "ready");
  const fileInput = useRef<HTMLInputElement>(null);
  const forecastInput = useRef<HTMLInputElement>(null);
  const forecastByDay = new Map<string, number>();
  for (const row of forecast?.forecasts || []) {
    const value = Number(row.predicted_quantity);
    if (Number.isFinite(value)) forecastByDay.set(row.forecast_date, (forecastByDay.get(row.forecast_date) || 0) + value);
  }
  const overviewForecast = [...forecastByDay.entries()].sort(([a], [b]) => a.localeCompare(b)).slice(0, 7);
  const forecastMaximum = Math.max(1, ...overviewForecast.map(([, value]) => value));
  const approvedInvoiceCount = invoices.filter((invoice) => invoice.status === "APPROVED").length;
  const invoicesNeedingReview = invoices.filter((invoice) => invoice.status === "NEEDS_REVIEW");
  const lowStockProducts = inventory.filter((product) => product.low_stock);
  const inventoryValue = inventory.reduce((total, product) =>
    total + Number(product.quantity_on_hand || 0) * Number(product.unit_cost || 0), 0);
  const unverifiedStockCount = inventory.filter((product) => !product.stock_count_verified).length;
  const productTypes = [...new Set(inventory.map((product) => product.product_type).filter(Boolean))].sort();
  const missingHsnCount = inventory.filter((product) => !String(product.hsn_sac || "").trim()).length;
  const visibleInventory = inventory.filter((product) => {
    const query = inventoryQuery.trim().toLocaleLowerCase();
    const searchable = [product.sku, product.name, product.product_type, product.brand,
      product.model, product.hsn_sac].filter(Boolean).join(" ").toLocaleLowerCase();
    return (!query || searchable.includes(query))
      && (inventoryTypeFilter === "ALL" || product.product_type === inventoryTypeFilter)
      && (!missingHsnOnly || !String(product.hsn_sac || "").trim());
  });
  const salesTotals = salesLines.reduce((totals, line) => {
    const taxable = Number(line.quantity || 0) * Number(line.unit_rate || 0);
    return { subtotal: totals.subtotal + taxable, tax: 0 };
  }, { subtotal: 0, tax: 0 });

  useEffect(() => () => {
    if (documentPreview?.objectUrl) URL.revokeObjectURL(documentPreview.url);
  }, [documentPreview]);

  const openInvoiceDocument = async (invoice: any) => {
    setDocumentLoading(true);
    try {
      const response = await apiFetch(`${API}/invoices/${invoice.id}/document-url`);
      if (response.ok) {
        const result = await response.json();
        setDocumentPreview({ url: result.url, filename: result.filename || invoice.filename,
          contentType: result.content_type || "application/pdf", objectUrl: false, invoice });
        return;
      }
      if (response.status === 501) {
        const fileResponse = await apiFetch(`${API}/invoices/${invoice.id}/document`);
        if (!fileResponse.ok) throw new Error("Could not load this invoice document.");
        const url = URL.createObjectURL(await fileResponse.blob());
        setDocumentPreview({ url, filename: invoice.filename,
          contentType: fileResponse.headers.get("Content-Type") || "application/pdf", objectUrl: true, invoice });
        return;
      }
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "Could not load this invoice document.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not load this invoice document.");
    } finally {
      setDocumentLoading(false);
    }
  };

  const closeDocumentPreview = () => {
    if (documentPreview?.objectUrl) URL.revokeObjectURL(documentPreview.url);
    setDocumentPreview(null);
  };

  const refreshLocalData = async () => {
    try {
      const [invoiceResponse, inventoryResponse, forecastResponse, serialResponse, pricingResponse] = await Promise.all([
        apiFetch(`${API}/invoices`), apiFetch(`${API}/inventory`), apiFetch(`${API}/forecasts/latest`),
        apiFetch(`${API}/inventory/serial-assets`), apiFetch(`${API}/inventory/pricing-policy`),
      ]);
      if (!invoiceResponse.ok || !inventoryResponse.ok || !forecastResponse.ok || !serialResponse.ok || !pricingResponse.ok) throw new Error("API request failed");
      setInvoices(await invoiceResponse.json());
      setInventory(await inventoryResponse.json());
      setForecast(await forecastResponse.json());
      setSerialAssets(await serialResponse.json());
      const policy = await pricingResponse.json();
      setPricingPolicy(policy);
      setPricingDraft({
        minimum_margin_percent: policy.minimum_margin_percent ?? "",
        target_margin_percent: policy.target_margin_percent ?? "",
      });
    } catch {
      setNotice("Could not reach the app API. Check your connection or sign in again, then refresh.");
    }
  };

  useEffect(() => {
    let active = true;
    if (!authConfigured) {
      void refreshLocalData();
      return () => { active = false; };
    }
    void completeLogin().then((token) => {
      if (!active) return;
      setAuthState(token ? "ready" : "signed-out");
      if (token) void refreshLocalData();
    }).catch((error) => {
      if (active) {
        setAuthState("signed-out");
        setNotice(error instanceof Error ? error.message : "Sign-in failed.");
      }
    });
    return () => { active = false; };
  }, []);

  const uploadInvoices = async (selectedFiles?: File[]) => {
    const files = selectedFiles || [];
    if (!files.length) return;
    if (files.length > 20) {
      setNotice("Select up to 20 invoices per batch.");
      return;
    }

    let uploaded = 0;
    let queued = 0;
    let duplicates = 0;
    let failed = 0;
    let stoppedForAuth = false;

    for (const [index, file] of files.entries()) {
      setNotice(files.length === 1 ? "Uploading invoice…" : `Uploading invoice ${index + 1} of ${files.length}…`);
      const sendUpload = (allowDuplicate: boolean) => {
        const form = new FormData();
        form.append("file", file);
        return apiFetch(`${API}/invoices/upload?transaction_type=${uploadDirection}${allowDuplicate ? "&allow_duplicate=true" : ""}`, { method: "POST", body: form });
      };
      try {
        let response = await sendUpload(false);
        let payload = await response.json().catch(() => ({}));
        if (response.status === 409 && files.length === 1 && window.confirm(`${payload.detail}\n\nUpload it anyway?`)) {
          response = await sendUpload(true);
          payload = await response.json().catch(() => ({}));
        }
        if (response.status === 409) {
          duplicates += 1;
          continue;
        }
        if (response.status === 401 || response.status === 403) {
          failed += files.length - index;
          stoppedForAuth = true;
          break;
        }
        if (!response.ok) {
          failed += 1;
          continue;
        }
        uploaded += 1;
        if (payload.extraction_status === "QUEUED") queued += 1;
      } catch {
        failed += 1;
      }
    }

    await refreshLocalData();
    if (files.length === 1 && uploaded === 1) {
      setNotice(`${files[0].name} uploaded and added to the review queue.`);
    } else if (stoppedForAuth) {
      setNotice(`Batch paused: ${uploaded} uploaded, ${duplicates} duplicates skipped, ${failed} not uploaded. Sign in again, then retry the remaining files.`);
    } else {
      setNotice(`Batch complete: ${uploaded} uploaded, ${queued} extraction jobs queued, ${duplicates} duplicates skipped, ${failed} failed. Review each extracted invoice before approval.`);
    }
  };

  const reviewInvoice = async (invoiceId: number, action: "approve" | "reject") => {
    setReviewError("");
    try {
      const response = await apiFetch(`${API}/invoices/${invoiceId}/review`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Review action failed");
      setDocumentPreview((current) => current?.invoice.id === invoiceId ? { ...current, invoice: { ...current.invoice, status: payload.status } } : current);
      setNotice(`Invoice ${payload.filename}: ${payload.status.toLowerCase()}.`);
      await refreshLocalData();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Review action failed.";
      setReviewError(message);
      setNotice(message);
    }
  };

  const postInvoiceInventory = async (invoiceId: number) => {
    try {
      const response = await apiFetch(`${API}/invoices/${invoiceId}/post-inventory`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Inventory could not be posted");
      setNotice(`Inventory posted for ${payload.filename}.`);
      await refreshLocalData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Inventory could not be posted.");
    }
  };

  const saveInvoiceCorrection = async (invoiceId: number, event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const value = (key: string) => String(form.get(key) || "");
    const numberOrNull = (key: string) => value(key) === "" ? null : Number(value(key));
    const lineCount = Number(value("line_count"));
    const lines = Array.from({ length: lineCount }, (_, index) => {
      const description = value(`line_description_${index}`).trim();
      if (!description) return null;
      return { description, hsn_sac: value(`line_hsn_${index}`) || null,
        quantity: numberOrNull(`line_quantity_${index}`), unit: value(`line_unit_${index}`) || null,
        unit_rate: numberOrNull(`line_rate_${index}`), line_amount: numberOrNull(`line_amount_${index}`),
        serial_numbers: value(`line_serials_${index}`).split(/[\n,;]/).map((serial) => serial.trim()).filter(Boolean),
        warranty_months: numberOrNull(`line_warranty_${index}`),
        price_override_reason: value(`line_override_reason_${index}`).trim() || null,
        is_stock_item: value(`line_kind_${index}`) !== "service",
        product_id: value(`line_kind_${index}`) === "service" ? null : value(`line_product_${index}`) || null };
    }).filter((line) => line !== null);
    const payload = {
      vendor_name: value("vendor_name") || null,
      transaction_type: value("transaction_type"),
      invoice_number: value("invoice_number") || null,
      invoice_date: value("invoice_date") || null,
      subtotal: numberOrNull("subtotal"), tax_total: numberOrNull("tax_total"),
      grand_total: numberOrNull("grand_total"),
      lines,
    };
    try {
      const response = await apiFetch(`${API}/invoices/${invoiceId}/correction`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not save invoice fields");
      setDocumentPreview((current) => current?.invoice.id === invoiceId ? { ...current, invoice: { ...current.invoice, ...payload, direction_reason: payload.transaction_type ? `Direction manually confirmed as ${payload.transaction_type}.` : current.invoice.direction_reason } } : current);
      setNotice(`Invoice details saved for ${result.filename}. You can now finish the review.`);
      await refreshLocalData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not save invoice fields.");
    }
  };

  const createProduct = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = {
      sku: String(form.get("sku")), name: String(form.get("name")),
      product_type: String(form.get("product_type") || "").trim() || null,
      brand: String(form.get("brand") || "").trim() || null,
      model: String(form.get("model") || "").trim() || null,
      hsn_sac: String(form.get("hsn_sac") || "").trim() || null,
      quantity_on_hand: Number(form.get("quantity_on_hand")),
      reorder_point: Number(form.get("reorder_point")), unit_cost: Number(form.get("unit_cost")),
      minimum_margin_percent: form.get("minimum_margin_percent") === "" ? null : Number(form.get("minimum_margin_percent")),
      target_margin_percent: form.get("target_margin_percent") === "" ? null : Number(form.get("target_margin_percent")),
    };
    try {
      const response = await apiFetch(`${API}/inventory`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not add product");
      event.currentTarget.reset();
      setNotice(`${result.name} added to inventory.`);
      await refreshLocalData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not add product.");
    }
  };

  const saveProductHsn = async (productId: string | number, event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const hsn = String(form.get("hsn_sac") || "").trim();
    try {
      const response = await apiFetch(`${API}/inventory/${productId}/details`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hsn_sac: hsn || null }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not save HSN/SAC");
      setNotice(hsn ? `Verified HSN/SAC saved for ${result.name}.` : `HSN/SAC cleared for ${result.name}.`);
      await refreshLocalData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not save HSN/SAC.");
    }
  };

  const savePricingRules = async (productId: string | number, event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const min = String(form.get("minimum_margin_percent") ?? "");
    const target = String(form.get("target_margin_percent") ?? "");
    try {
      const response = await apiFetch(`${API}/inventory/${productId}/pricing`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ minimum_margin_percent: min === "" ? null : Number(min), target_margin_percent: target === "" ? null : Number(target) }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not save pricing rules");
      setNotice(`Pricing rules saved for ${result.name}.`);
      await refreshLocalData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not save pricing rules.");
    }
  };

  const saveCommonPricingRules = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const minimum = pricingDraft.minimum_margin_percent === "" ? null : Number(pricingDraft.minimum_margin_percent);
    const target = pricingDraft.target_margin_percent === "" ? null : Number(pricingDraft.target_margin_percent);
    if (minimum != null && target != null && target < minimum) {
      setNotice("Target margin must be at least the minimum margin.");
      return;
    }
    try {
      const response = await apiFetch(`${API}/inventory/pricing-policy`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ minimum_margin_percent: minimum, target_margin_percent: target }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not save shared pricing rules");
      setNotice("Common selling price rules saved. Products without overrides now use these margins.");
      await refreshLocalData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not save common pricing rules.");
    }
  };

  const createSalesInvoice = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const htmlForm = event.currentTarget;
    const form = new FormData(htmlForm);
    const lines = salesLines.map((line) => ({
      product_id: line.product_id, quantity: Number(line.quantity),
      unit_rate: Number(line.unit_rate), tax_rate: 0,
      serial_numbers: String(line.serial_numbers || "").split(/[\n,;]/).map((value) => value.trim()).filter(Boolean),
      price_override_reason: String(line.price_override_reason || "").trim() || null,
    }));
    if (lines.some((line) => !line.product_id || !Number.isFinite(line.quantity) || !Number.isFinite(line.unit_rate) || !Number.isFinite(line.tax_rate))) {
      setNotice("Select a product and enter quantity and selling rate for every line.");
      return;
    }
    try {
      const response = await apiFetch(`${API}/invoices/sales`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_type: "NON_GST_RECEIPT", invoice_number: String(form.get("invoice_number")).trim(),
          invoice_date: String(form.get("invoice_date")), receiver_name: String(form.get("receiver_name")).trim(),
          receiver_address: String(form.get("receiver_address") || "").trim() || null,
          amount_paid: Number(form.get("amount_paid") || 0), lines }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not complete the sale");
      htmlForm.reset();
      setSalesLines([{ product_id: "", quantity: "1", unit_rate: "", tax_rate: "", serial_numbers: "" }]);
      setNotice(`Sales receipt ${result.invoice_number} created. Stock reduced and ${result.payment_status.toLowerCase()} status recorded.`);
      await refreshLocalData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not complete the sale.");
    }
  };

  const recordInvoicePayment = async (invoiceId: string | number, event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const amount = Number(form.get("amount"));
    try {
      const response = await apiFetch(`${API}/invoices/${invoiceId}/payments`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount, method: String(form.get("method") || "").trim() || null,
          reference: String(form.get("reference") || "").trim() || null }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not record payment");
      setNotice(`Payment saved. Outstanding balance: ₹${(Number(result.grand_total) - Number(result.amount_paid)).toFixed(2)}.`);
      await refreshLocalData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not record payment.");
    }
  };

  const recordMovement = async (productId: number, event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const response = await apiFetch(`${API}/inventory/${productId}/movements`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quantity_change: Number(form.get("quantity_change")), reason: String(form.get("reason")), effective_date: String(form.get("effective_date")), movement_type: String(form.get("movement_type") || "ADJUSTMENT"), confirmed_opening_balance: form.get("confirmed_opening_balance") === "on" }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not update stock");
      setNotice(`Stock updated for ${result.name}.`);
      await refreshLocalData();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not update stock.");
    }
  };

  const loadMovements = async (productId: number) => {
    try {
      const response = await apiFetch(`${API}/inventory/${productId}/movements`);
      if (!response.ok) throw new Error("Could not load movement history");
      const rows = await response.json();
      setMovements((current) => ({ ...current, [productId]: rows }));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not load movement history.");
    }
  };

  const runForecast = async (file?: File) => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    if (forecastBusy) return;
    setForecastBusy(true); setForecastError("");
    try {
      const response = await apiFetch(`${API}/forecasts/import?horizon_days=${forecastHorizon}`, { method: "POST", body: form });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Forecast could not be created");
      setForecast(result);
      setNotice("Forecast generated and evaluated on a chronological holdout.");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Forecast could not be created.";
      setNotice(message); setForecastError(message);
    } finally { setForecastBusy(false); }
  };

  const runApprovedSalesForecast = async () => {
    if (forecastBusy) return;
    setForecastBusy(true); setForecastError("");
    try {
      const response = await apiFetch(`${API}/forecasts/from-approved-sales`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm_complete_sales_history: true, horizon_days: forecastHorizon }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Forecast could not be created");
      setForecast(result);
      setNotice(`Forecast generated from approved sales using ${result.model_name}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Forecast could not be created.";
      setNotice(message); setForecastError(message);
    } finally { setForecastBusy(false); }
  };

  const sendForecastFeedback = async (runId: number, productId: string, forecastDate: string, event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const response = await apiFetch(`${API}/forecasts/${runId}/feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ product_id: productId, forecast_date: forecastDate,
          actual_quantity: Number(form.get("actual_quantity")), comment: String(form.get("comment") || "") || null }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not save feedback");
      event.currentTarget.reset();
      setNotice(`Forecast feedback saved for ${productId} on ${forecastDate}.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not save feedback.");
    }
  };

  if (authConfigured && authState === "loading") {
    return <main className="auth-screen"><section className="panel"><h1>Connecting to your workspace…</h1></section></main>;
  }
  if (authConfigured && authState === "signed-out") {
    return <main className="auth-screen"><section className="panel auth-panel"><div className="brand"><span className="brand-mark">A</span><span>autonomous<span className="brand-light">.os</span></span></div><h1>Sign in to your workspace</h1><p className="muted-cell">Use the account created for this application.</p>{notice && <p role="alert">{notice}</p>}<button className="primary-button" onClick={() => void beginLogin()}>Continue with AWS sign-in</button></section></main>;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">A</span><span>autonomous<span className="brand-light">.os</span></span></div>
        <div className="workspace-label">WORKSPACE</div>
        <button className="workspace-switch"><span className="workspace-avatar">AK</span><span><strong>Demo Store</strong><small>Free workspace</small></span><span className="chevron">⌄</span></button>
        <div className="workspace-label nav-label">OPERATIONS</div>
        <nav aria-label="Main navigation">
          {navItems.map((item, index) => <button key={item} className={`nav-item ${active === item ? "selected" : ""}`} onClick={() => setActive(item)}><span className="nav-icon">{["⌂", "▤", "₹", "▦", "⌁", "▧"][index]}</span>{item}{item === "Invoices" && invoices.filter((invoice) => invoice.status === "NEEDS_REVIEW").length > 0 && <span className="nav-count">{invoices.filter((invoice) => invoice.status === "NEEDS_REVIEW").length}</span>}</button>)}
        </nav>
        <div className="sidebar-bottom"><div className="help-card"><span className="help-icon">?</span><strong>Need a hand?</strong><p>Explore the quick-start guide to set up your workspace.</p><button onClick={() => window.location.assign("/guide.html")}>Open guide <span>↗</span></button></div><button className="profile"><span className="profile-avatar">T</span><span><strong>Demo User</strong><small>Administrator</small></span><span className="profile-menu">···</span></button></div>
      </aside>

      <main className="main-area">
        <header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>{active}</strong></div><div className="top-actions">{authConfigured ? <button className="text-button" onClick={signOut}>Sign out</button> : <span className="demo-pill"><i /> Demo workspace</span>}<button className="icon-button" aria-label="Notifications" onClick={() => setNotice("You’re all caught up.")}>♧<b /></button><span className="top-avatar">T</span></div></header>
        <div className={`content-wrap ${active === "Forecasts" ? "forecast-page" : ""}`}>
          <div className="page-heading"><div><div className="eyebrow">{invoices.length > 0 && invoices.every(i => String(i.source_type || "").startsWith("DEMO")) ? "DEMONSTRATION WORKSPACE · SAMPLE DATA" : "BUSINESS WORKSPACE"}</div><h1>{active === "Overview" ? "Good morning, Demo User" : active}<span className="wave"> ✳</span></h1><p>{active === "Overview" ? "Here’s what’s happening with your business today." : active === "Forecasts" ? "Sales, pricing and profit planning, one step at a time." : `Manage your ${active.toLowerCase()} in your workspace.`}</p></div><input ref={fileInput} type="file" accept="application/pdf,image/jpeg,image/png" multiple hidden onChange={(event) => { const files = Array.from(event.currentTarget.files || []); void uploadInvoices(files); event.currentTarget.value = ""; }}/>{active !== "Forecasts" && <><label className="upload-direction">Upload as <select value={uploadDirection} onChange={(event) => setUploadDirection(event.target.value)}><option value="UNKNOWN">Choose during review</option><option value="PURCHASE">Purchase bills</option><option value="SALE">Sales bills</option></select></label><button className="primary-button" onClick={() => fileInput.current?.click()}><span>＋</span> Upload invoices</button></>}</div>

          {notice && <div className="notice" role="status"><span>{notice}</span><button onClick={() => setNotice("")} aria-label="Dismiss">×</button></div>}
          {active !== "Forecasts" && <div className="demo-banner"><span className="demo-dot">i</span><span><strong>Business data</strong> — Inventory and invoice summaries use your saved records. Real bill analysis shows its source files and data limitations.</span><button onClick={() => void refreshLocalData()}>Refresh <span>↻</span></button></div>}

          {active === "Overview" ? <>

          <section className="metric-grid" aria-label="Business summary">
            <article className="metric-card"><div className="metric-top"><span>Approved invoices</span><span className="metric-symbol violet">▤</span></div><div className="metric-value">{approvedInvoiceCount}</div><div className="metric-caption">{invoices.length} tracked · {invoicesNeedingReview.length} awaiting review</div></article>
            <article className="metric-card"><div className="metric-top"><span>Estimated inventory value</span><span className="metric-symbol blue">▦</span></div><div className="metric-value">{inventoryValue.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 })}</div><div className="metric-caption">{inventory.length} products · {unverifiedStockCount} stock counts unverified</div></article>
            <article className="metric-card"><div className="metric-top"><span>Needs attention</span><span className="metric-symbol orange">!</span></div><div className="metric-value">{invoicesNeedingReview.length + lowStockProducts.length}</div><div className="metric-caption">{invoicesNeedingReview.length} invoices · {lowStockProducts.length} low-stock products</div></article>
          </section>

          <section className="middle-grid">
            <article className="panel forecast-panel"><div className="panel-heading"><div><h2>Demand forecast</h2><p>{forecast?.metrics?.demo ? "Synthetic presentation demo · not business sales" : overviewForecast.length ? "Latest saved model · combined product units" : "Train a model from complete sales history to see predictions"}</p></div><button className="select-button" onClick={() => setActive("Forecasts")}>View models →</button></div>
              {overviewForecast.length > 0 ? <><div className="chart"><div className="chart-main"><div className="bars">{overviewForecast.map(([day, quantity]) => <div className="bar-group" key={day}><strong>{quantity.toFixed(1)}</strong><div className="bar-pair"><i className="bar future" title={`${day}: ${quantity.toFixed(1)} predicted units`} style={{height: `${Math.max(1, quantity / forecastMaximum * 140)}px`}} /></div><span>{day.slice(5)}</span></div>)}</div></div></div><div className="forecast-foot"><span>{forecast.model_name || "Saved demand model"}</span><span>{forecast.metrics?.demo ? "Demo data" : "Model estimate; not guaranteed sales"}</span></div></> : <p className="muted-cell">Open Forecasts to analyze the 20 real bills or train demand forecasts from complete sales history.</p>}
            </article>

            <article className="panel alerts-panel"><div className="panel-heading"><div><h2>Needs attention</h2><p>Invoices and stock that need review</p></div></div><div className="alert-list">{lowStockProducts.slice(0, 3).map((product) => <div className="alert-row" key={`stock-${product.id}`}><span className="alert-icon red-bg">↓</span><span className="alert-copy"><strong>{product.name} is running low</strong><small>{product.quantity_on_hand} on hand · reorder at {product.reorder_point}</small></span><span className="priority high">Stock</span></div>)}{invoicesNeedingReview.slice(0, 3).map((invoice) => <div className="alert-row" key={`invoice-${invoice.id}`}><span className="alert-icon amber-bg">▤</span><span className="alert-copy"><strong>Invoice needs review</strong><small>{invoice.vendor_name || invoice.filename} · {invoice.invoice_number || "number missing"}</small></span><span className="priority medium">Review</span></div>)}{invoicesNeedingReview.length === 0 && lowStockProducts.length === 0 && <p className="muted-cell">No outstanding invoice reviews or low-stock alerts.</p>}</div><button className="all-alerts" onClick={() => setActive(lowStockProducts.length ? "Inventory" : "Invoices")}>Open {lowStockProducts.length ? "inventory" : "invoices"} <span>→</span></button></article>
          </section>

          <section className="bottom-grid">
            <article className="panel table-panel"><div className="panel-heading"><div><h2>Recent invoices</h2><p>Latest documents in your workspace</p></div><button className="text-button" onClick={() => setActive("Invoices")}>View all <span>→</span></button></div><div className="table-scroll"><table><thead><tr><th>DOCUMENT</th><th>INVOICE</th><th>DATE</th><th>TYPE</th><th>STATUS</th><th>REVIEW</th></tr></thead><tbody>{invoices.slice(0, 5).map((invoice) => <tr key={invoice.id}><td><span className="vendor-avatar">{(invoice.vendor_name || invoice.filename)[0]}</span><strong>{invoice.vendor_name || invoice.filename}</strong></td><td className="muted-cell">{invoice.invoice_number || "Awaiting extraction"}</td><td className="muted-cell">{invoice.invoice_date || new Date(invoice.created_at).toLocaleDateString()}</td><td><span className={`invoice-direction-badge ${invoice.transaction_type === "SALE" ? "sale" : invoice.transaction_type === "PURCHASE" ? "purchase" : "unknown"}`}>{invoice.transaction_type === "SALE" ? "Sale" : invoice.transaction_type === "PURCHASE" ? "Purchase" : "Direction not set"}</span></td><td><span className={`status-badge ${invoice.status === "APPROVED" ? "green" : invoice.status === "REJECTED" ? "red" : "amber"}`}><i/>{invoice.status.replaceAll("_", " ")}</span></td><td>{invoice.status === "NEEDS_REVIEW" && <button className="text-button" onClick={() => setActive("Invoices")}>Review invoice →</button>}</td></tr>)}{invoices.length === 0 && <tr><td colSpan={6} className="muted-cell">No invoices uploaded yet.</td></tr>}</tbody></table></div></article>
            <article className="panel stock-panel"><div className="panel-heading"><div><h2>Stock snapshot</h2><p>Current on-hand inventory</p></div><button className="text-button" onClick={() => setActive("Inventory")}>Inventory <span>→</span></button></div><div className="stock-list">{inventory.slice(0, 5).map((product) => <div className="stock-row" key={product.sku}><div className="stock-name"><strong>{product.name}</strong><small>{product.sku}</small></div><div className="stock-quantity"><strong>{product.quantity_on_hand}</strong><small>in stock</small></div><div className="stock-track"><div className={`stock-fill ${product.low_stock ? "red" : "green"}`} style={{ width: `${Math.min(product.quantity_on_hand / Math.max(product.reorder_point * 2, 1) * 100, 100)}%` }}/></div><span className={`trend ${product.low_stock ? "red" : "green"}`}>{product.low_stock ? "Low" : "OK"}</span></div>)}{inventory.length === 0 && <p className="muted-cell">No products added yet.</p>}</div></article>
          </section>
          </>  : active === "Invoices" ? <section className="panel invoice-queue"><div className="panel-heading"><div><h2>Invoice review</h2><p>Open an invoice to compare it with the original, correct details if needed, then choose an action.</p></div><button className="text-button" onClick={() => void refreshLocalData()}>Refresh <span>↻</span></button></div>
            <div className="invoice-queue-summary"><strong>{invoicesNeedingReview.length}</strong> {invoicesNeedingReview.length === 1 ? "invoice needs" : "invoices need"} your review. Purchases add items to stock; sales reduce stock.</div>
            {invoicesNeedingReview.length > 0 ? <div className="invoice-card-list">{invoicesNeedingReview.map((invoice) => <article className="invoice-review-card" key={invoice.id}><div className="invoice-card-main"><div className="invoice-card-file"><span className="invoice-file-icon">PDF</span><div><strong>{invoice.filename}</strong><small>{invoice.vendor_name || "Vendor not found"} · Invoice {invoice.invoice_number || "number not found"}</small></div></div><span className="status-badge amber"><i/>Needs review</span></div><div className="invoice-card-meta"><span><b>Date</b> {invoice.invoice_date || "Not found"}</span><span><b>Direction</b> {invoice.transaction_type === "UNKNOWN" ? "Choose purchase or sale" : invoice.transaction_type === "PURCHASE" ? "Purchase · adds stock" : "Sale · reduces stock"}</span><span><b>Total</b> {invoice.grand_total == null ? "Not found" : `₹${Number(invoice.grand_total).toFixed(2)}`}</span></div><ReviewSignals invoice={invoice} inventory={inventory}/><div className="invoice-card-actions"><button type="button" className="review-open" disabled={documentLoading} onClick={() => void openInvoiceDocument(invoice)}>{documentLoading ? "Opening invoice…" : "Review invoice"} <span>→</span></button></div></article>)}</div> : <div className="invoice-empty-state"><span>✓</span><strong>You’re all caught up</strong><p>New uploaded invoices will appear here when they’re ready to review.</p></div>}
            {invoices.some((invoice) => invoice.status !== "NEEDS_REVIEW") && <details className="reviewed-invoices"><summary>Previously reviewed invoices ({invoices.filter((invoice) => invoice.status !== "NEEDS_REVIEW").length})</summary><div className="reviewed-invoice-list">{invoices.filter((invoice) => invoice.status !== "NEEDS_REVIEW").map((invoice) => <div className="reviewed-invoice-row" key={invoice.id}><span>{invoice.filename}<small>{invoice.vendor_name || "Vendor not found"} · {invoice.invoice_number || "No invoice number"}</small></span><span className={`invoice-direction-badge ${invoice.transaction_type === "SALE" ? "sale" : invoice.transaction_type === "PURCHASE" ? "purchase" : "unknown"}`}>{invoice.transaction_type === "SALE" ? "Sale" : invoice.transaction_type === "PURCHASE" ? "Purchase" : "Direction not set"}</span><span className={`status-badge ${invoice.status === "APPROVED" ? "green" : "red"}`}><i/>{invoice.status.replaceAll("_", " ")}</span><button type="button" className="text-button" disabled={documentLoading} onClick={() => void openInvoiceDocument(invoice)}>View</button>{invoice.status === "APPROVED" && !invoice.inventory_posted && <button type="button" className="text-button" onClick={() => void postInvoiceInventory(invoice.id)}>Post inventory</button>}</div>)}</div></details>}
          </section>: active === "Sales" ? <>
            <section className="panel"><div className="panel-heading"><div><h2>Record a sale</h2><p>Choose products, set the selling rate, record payment, and complete the sale. Stock updates when you submit.</p></div></div><p className="muted-cell">Create a simple sales receipt. No tax setup is needed; stock and payment balances update automatically.</p><form onSubmit={(event) => void createSalesInvoice(event)} className="sales-invoice-form"><div className="sales-customer-grid"><label>Invoice number<input name="invoice_number" required maxLength={128} placeholder="e.g. POS-1001"/></label><label>Invoice date<input name="invoice_date" type="date" required defaultValue={new Date().toISOString().slice(0, 10)}/></label><label>Customer name<input name="receiver_name" required maxLength={300} placeholder="Customer or business name"/></label><label className="sales-address">Customer address<textarea name="receiver_address" maxLength={2000} rows={2} placeholder="Optional billing address"/></label><label>Payment received<input name="amount_paid" type="number" min="0" step="0.01" defaultValue="0"/></label></div><h3>Items</h3>{salesLines.map((line, index) => <fieldset className="sales-line-form" key={index}><legend>Item {index + 1}</legend><label>Product<select required value={line.product_id} onChange={(event) => { const product = inventory.find((candidate) => String(candidate.id) === event.target.value); setSalesLines((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, product_id: event.target.value, unit_rate: item.unit_rate || (product ? sellPrice(Number(product.unit_cost), effectiveMargins(product, pricingPolicy).target).toFixed(2) : "") } : item)); }}><option value="">Choose product</option>{inventory.map((product) => <option key={product.id} value={product.id}>{[product.name, product.brand, product.model, product.hsn_sac ? `HSN ${product.hsn_sac}` : null, `${product.quantity_on_hand} in stock`].filter(Boolean).join(" · ")}</option>)}</select></label><label>Quantity<input type="number" min="1" step="1" required value={line.quantity} onChange={(event) => setSalesLines((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, quantity: event.target.value } : item))}/></label><label>Selling rate<input type="number" min="0" step="0.01" required value={line.unit_rate} onChange={(event) => setSalesLines((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, unit_rate: event.target.value } : item))}/></label>{Number(line.unit_rate || 0) < minimumSaleRate(inventory.find((product) => String(product.id) === String(line.product_id)), pricingPolicy) && line.product_id && <p className="review-error">Below the floor rate of ₹{minimumSaleRate(inventory.find((product) => String(product.id) === String(line.product_id)), pricingPolicy).toFixed(2)}. Add a reason to confirm this exception.</p>}<label>Price override reason (required below floor)<input value={line.price_override_reason || ""} onChange={(event) => setSalesLines((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, price_override_reason: event.target.value } : item))} placeholder="e.g. clearance / customer discount"/></label><label>Serial numbers (if this product is serialized)<input placeholder="One per unit, comma separated" value={line.serial_numbers || ""} onChange={(event) => setSalesLines((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, serial_numbers: event.target.value } : item))}/></label>{salesLines.length > 1 && <button type="button" className="text-button" onClick={() => setSalesLines((current) => current.filter((_, itemIndex) => itemIndex !== index))}>Remove</button>}</fieldset>)}<button type="button" className="select-button" onClick={() => setSalesLines((current) => [...current, { product_id: "", quantity: "1", unit_rate: "", tax_rate: "", serial_numbers: "" }])}>Add item</button><div className="sales-totals"><strong>Total: ₹{(salesTotals.subtotal + salesTotals.tax).toFixed(2)}</strong></div><button type="submit" className="primary-button">Complete sale and update stock</button></form></section>
          </>: active === "Inventory" ? <>
            <section className="panel table-panel">
              <div className="panel-heading"><div><h2>Inventory</h2><p>Search by product, type, brand, model, SKU, or HSN/SAC.</p></div></div>
              <div className="inventory-stock-guidance"><span className="inventory-stock-guidance-icon">✓</span><div><strong>Stock updates automatically for sales entered here</strong><p>Complete a sale in the Sales page, or approve an uploaded invoice marked SALE, and available stock is reduced automatically. Approving a PURCHASE adds stock.</p><small>Use “Stock tools” only to correct a physical count, damaged item, or a sale made outside the app.</small></div></div>
              <div className="inventory-search-controls">
                <input aria-label="Search inventory" placeholder="Search products or HSN/SAC" value={inventoryQuery} onChange={(event) => setInventoryQuery(event.target.value)}/>
                <select aria-label="Filter by product type" value={inventoryTypeFilter} onChange={(event) => setInventoryTypeFilter(event.target.value)}>
                  <option value="ALL">All product types</option>{productTypes.map((type: string) => <option key={type} value={type}>{type}</option>)}
                </select>
                <button type="button" className={missingHsnOnly ? "select-button active-filter" : "select-button"} onClick={() => setMissingHsnOnly((current) => !current)} disabled={missingHsnCount === 0}>{missingHsnOnly ? "Show all products" : `Missing HSN/SAC (${missingHsnCount})`}</button>
                <span>{visibleInventory.length} of {inventory.length} products</span>
              </div>
              <div className="table-scroll"><table className="inventory-table"><thead><tr><th>SKU</th><th>PRODUCT</th><th>TYPE</th><th>BRAND / MODEL</th><th>HSN/SAC</th><th>ON HAND</th><th>REORDER AT</th><th>UNIT COST</th><th>STATUS / STOCK TOOLS</th></tr></thead><tbody>
                {visibleInventory.map((product) => {
                  const priceHistory = purchasePriceHistory(product.id, invoices);
                  const priceInsights = purchasePriceInsights(priceHistory, product);
                  return <tr key={product.id}><td>{product.sku}</td><td><strong>{product.name}</strong>{!product.stock_count_verified && <small style={{ display: "block" }}>Count unverified</small>}</td><td>{product.product_type || "Unclassified"}</td><td>{[product.brand, product.model].filter(Boolean).join(" · ") || "—"}</td><td><details className="inventory-hsn-tools"><summary>{product.hsn_sac || "Add HSN/SAC"}</summary><form onSubmit={(event) => void saveProductHsn(product.id, event)}><label>Verified HSN/SAC<input name="hsn_sac" maxLength={32} defaultValue={product.hsn_sac || ""} placeholder="Enter code from invoice"/></label><small>Use the code printed on the invoice or a verified tax classification.</small><button className="text-button" type="submit">Save code</button></form></details></td><td>{product.quantity_on_hand}</td><td>{product.reorder_point}</td><td>{Number(product.unit_cost).toFixed(2)}<button type="button" className="text-button" style={{ display: "block", marginTop: 5 }} onClick={() => setPriceHistoryOpen((current) => ({ ...current, [product.id]: !current[product.id] }))}>{priceHistoryOpen[product.id] ? "Hide price history" : `Price history (${priceHistory.length})`}</button>{priceHistoryOpen[product.id] && <div className="purchase-price-history"><strong>{purchasePriceSignal(priceHistory)}</strong>{priceHistory.length ? <><p>{priceInsights?.advice}</p>{priceInsights?.estimate !== null && priceInsights?.estimate !== undefined && <p>Simple trend estimate: {priceInsights.currency} {priceInsights.estimate.toFixed(2)} per {priceInsights.unit} (not an ML forecast).</p>}{priceInsights?.supplier && <p>Lowest latest comparable rate: {priceInsights.supplier.supplier} · {priceInsights.currency} {priceInsights.supplier.unit_rate.toFixed(2)} per {priceInsights.unit}.</p>}<ul>{priceHistory.slice().reverse().map((row, index) => <li key={`${row.date}-${row.invoice_number}-${index}`}>{row.date} · {row.supplier} · {row.invoice_number} · {row.currency} {row.unit_rate.toFixed(2)} / {row.unit} × {row.quantity}</li>)}</ul></> : <p>Approve and match purchase invoices to build supplier price history.</p>}</div>}</td><td><span className={"status-badge " + (product.low_stock ? "amber" : "green")}>{product.low_stock ? "Low stock" : "In stock"}</span><details className="inventory-stock-tools"><summary>Stock tools</summary><div className="inventory-stock-tools-body"><p>Manual correction only. Sales entered through the Sales page or approved SALE invoices already update stock.</p><form onSubmit={(event) => void recordMovement(product.id, event)} className="inventory-movement-form"><label>Entry type<select name="movement_type"><option value="ADJUSTMENT">Stock correction</option><option value="OPENING_STOCK">Confirmed opening stock</option></select></label><label>Effective date<input name="effective_date" type="date" required defaultValue={new Date().toISOString().slice(0,10)}/></label><input aria-label="Stock change in units" name="quantity_change" type="number" required placeholder="+ or − units"/><input aria-label="Reason for stock adjustment" name="reason" required placeholder="Reason for change"/><label className="stock-confirm"><input type="checkbox" name="confirmed_opening_balance"/>I verified this opening quantity and date (required for opening stock).</label><button className="primary-button" type="submit">Save stock entry</button></form><button className="text-button inventory-history-button" onClick={() => void loadMovements(product.id)}>View movement history</button>{movements[product.id]?.map((movement) => <small className="inventory-movement-row" key={movement.id}>{movement.quantity_change > 0 ? "+" : ""}{movement.quantity_change} · {movement.reason} · Effective {movement.effective_date || "date not recorded"} · Saved {new Date(movement.created_at).toLocaleString()}</small>)}</div></details></td></tr>;
                })}
                {visibleInventory.length === 0 && <tr><td colSpan={9} className="muted-cell">{inventory.length ? "No products match this search." : "No products yet. Add one below."}</td></tr>}
              </tbody></table></div>
            </section>
            <section className="panel table-panel" style={{ marginTop: 14 }}><div className="panel-heading"><div><h2>Serialized stock and warranties</h2><p>Record serials while reviewing purchase invoices to track stock units, sales, and warranty dates.</p></div></div><div className="table-scroll"><table><thead><tr><th>SERIAL NUMBER</th><th>PRODUCT</th><th>STATUS</th><th>PURCHASE INVOICE</th><th>SALE INVOICE</th><th>WARRANTY</th></tr></thead><tbody>{serialAssets.map((asset: any) => <tr key={asset.serial_number}><td>{asset.serial_number}</td><td>{asset.product_name}</td><td><span className={"status-badge " + (asset.status === "IN_STOCK" ? "green" : "blue")}><i/>{asset.status === "IN_STOCK" ? "In stock" : "Sold"}</span></td><td>{asset.purchase_invoice_id}</td><td>{asset.sale_invoice_id || "—"}</td><td>{asset.warranty_until ? "Until " + asset.warranty_until : asset.warranty_months ? asset.warranty_months + " months" : "Not recorded"}</td></tr>)}{serialAssets.length === 0 && <tr><td colSpan={6} className="muted-cell">No serialized items recorded yet. Add serials and optional warranty months when correcting a purchase invoice before approving it.</td></tr>}</tbody></table></div></section>
            <section className="panel shared-pricing-panel" style={{ marginTop: 14 }}><div className="panel-heading"><div><h2>Common selling price rules</h2><p>Set a default minimum and target margin once. Products use these unless you add a product-specific override below.</p></div></div><form className="shared-pricing-form" onSubmit={(event) => void saveCommonPricingRules(event)}><label>Default minimum margin %<input aria-label="Default minimum margin percent" type="number" min="0" max="99.99" step="0.01" value={pricingDraft.minimum_margin_percent} placeholder="No minimum" onChange={(event) => setPricingDraft((current: any) => ({ ...current, minimum_margin_percent: event.target.value }))}/></label><label>Default target margin %<input aria-label="Default target margin percent" type="number" min="0" max="99.99" step="0.01" value={pricingDraft.target_margin_percent} placeholder="Same as minimum" onChange={(event) => setPricingDraft((current: any) => ({ ...current, target_margin_percent: event.target.value }))}/></label><button className="primary-button" type="submit">Save common rules</button><span className="muted-cell">Blank minimum means unit cost is the floor. Blank target uses the minimum.</span></form></section>
            <section className="panel table-panel" style={{ marginTop: 14 }}><div className="panel-heading"><div><h2>Product-specific overrides</h2><p>Leave a field blank to use the common rule. Set a value only when this product needs a different margin.</p></div></div><div className="table-scroll"><table><thead><tr><th>PRODUCT</th><th>UNIT COST</th><th>MIN MARGIN OVERRIDE</th><th>TARGET MARGIN OVERRIDE</th><th>EFFECTIVE MIN SELL PRICE</th><th>EFFECTIVE TARGET PRICE</th><th>SAVE</th></tr></thead><tbody>{visibleInventory.map((product) => { const cost = Number(product.unit_cost || 0); const ownMin = product.minimum_margin_percent == null ? null : Number(product.minimum_margin_percent); const ownTarget = product.target_margin_percent == null ? null : Number(product.target_margin_percent); const margins = effectiveMargins(product, pricingPolicy); const minimumPrice = sellPrice(cost, margins.minimum); const targetPrice = sellPrice(cost, margins.target); const formId = `pricing-${product.id}`; return <tr key={product.id}><td><strong>{product.name}</strong><small style={{ display: "block" }}>{product.sku}</small></td><td>₹{cost.toFixed(2)}</td><td><input form={formId} aria-label={`${product.name} minimum margin override percent`} name="minimum_margin_percent" type="number" min="0" max="99.99" step="0.01" defaultValue={ownMin ?? ""} placeholder={pricingPolicy.minimum_margin_percent == null ? "None" : `Common ${pricingPolicy.minimum_margin_percent}%`}/><small className="pricing-inheritance">{ownMin == null ? `Using common: ${pricingPolicy.minimum_margin_percent == null ? "no minimum" : `${pricingPolicy.minimum_margin_percent}%`}` : `Product override: ${ownMin}%`}</small></td><td><input form={formId} aria-label={`${product.name} target margin override percent`} name="target_margin_percent" type="number" min="0" max="99.99" step="0.01" defaultValue={ownTarget ?? ""} placeholder={pricingPolicy.target_margin_percent == null ? "Same as minimum" : `Common ${pricingPolicy.target_margin_percent}%`}/><small className="pricing-inheritance">{ownTarget == null ? `Using common: ${pricingPolicy.target_margin_percent == null ? "same as minimum" : `${pricingPolicy.target_margin_percent}%`}` : `Product override: ${ownTarget}%`}</small></td><td>₹{minimumPrice.toFixed(2)}</td><td>₹{targetPrice.toFixed(2)}</td><td><form id={formId} onSubmit={(event) => void savePricingRules(product.id, event)}><button className="text-button" type="submit">Save override</button></form></td></tr>; })}{visibleInventory.length === 0 && <tr><td colSpan={7} className="muted-cell">Add products to set selling price rules.</td></tr>}</tbody></table></div></section>
            <section className="panel" style={{ marginTop: 14 }}><div className="panel-heading"><div><h2>Add product</h2><p>Enter an opening stock count and basic product identity.</p></div></div><form onSubmit={(event) => void createProduct(event)} className="inventory-product-form"><input name="sku" required placeholder="SKU" maxLength={80}/><input name="name" required placeholder="Product name" maxLength={255}/><input name="product_type" placeholder="Type (e.g. Monitor)" maxLength={120}/><input name="brand" placeholder="Brand (e.g. Dell)" maxLength={120}/><input name="model" placeholder="Model / key specs" maxLength={160}/><input name="hsn_sac" placeholder="HSN/SAC" maxLength={32}/><input name="quantity_on_hand" type="number" min="0" required placeholder="On hand"/><input name="reorder_point" type="number" min="0" defaultValue="0" placeholder="Reorder at"/><input name="unit_cost" type="number" min="0" step="0.01" defaultValue="0" placeholder="Unit cost"/><input name="minimum_margin_percent" type="number" min="0" max="99.99" step="0.01" placeholder="Min margin % (optional)"/><input name="target_margin_percent" type="number" min="0" max="99.99" step="0.01" placeholder="Target margin % (optional)"/><button className="primary-button" type="submit">Add product</button></form></section>
          </> : active === "Forecasts" ? <>
            <input ref={forecastInput} type="file" accept=".csv,text/csv" hidden onChange={(event) => { void runForecast(event.target.files?.[0]); event.currentTarget.value = ""; }}/>
            <ForecastWorkspace forecast={forecast} invoices={invoices} inventory={inventory} horizon={forecastHorizon}
              busy={forecastBusy} error={forecastError} onHorizon={setForecastHorizon} onSales={() => void runApprovedSalesForecast()}
              onUpload={() => forecastInput.current?.click()} onReview={() => setActive("Invoices")}
              onFeedback={(run, product, day, event) => void sendForecastFeedback(run, product, day, event)}
              pricing={<CommercialModels/>} plan={forecast ? <ForecastPlan forecast={forecast} inventory={inventory} policy={pricingPolicy}/> : null}/>
          </> : <>
            <div className="panel-heading"><div><h2>Payments</h2><p>Track outstanding customer balances and supplier payments.</p></div></div>
            <section className="panel table-panel" style={{ marginTop: 14 }}><div className="panel-heading"><div><h3>Outstanding invoices</h3><p>Approved sales are receivables; approved purchases are payables. Record each later payment here.</p></div></div><div className="table-scroll"><table><thead><tr><th>TYPE</th><th>PARTY / INVOICE</th><th>INVOICE TOTAL</th><th>PAID</th><th>OUTSTANDING</th><th>RECORD PAYMENT</th></tr></thead><tbody>{invoices.filter((invoice) => invoice.status === "APPROVED" && Number(invoice.grand_total || 0) > Number(invoice.amount_paid || 0)).map((invoice: any) => { const balance = Number(invoice.grand_total || 0) - Number(invoice.amount_paid || 0); return <tr key={invoice.id}><td><span className={"invoice-direction-badge " + (invoice.transaction_type === "SALE" ? "sale" : "purchase")}>{invoice.transaction_type === "SALE" ? "Receivable" : "Payable"}</span></td><td>{invoice.transaction_type === "SALE" ? invoice.receiver_name || invoice.vendor_name : invoice.vendor_name || invoice.receiver_name}<small style={{ display: "block" }}>{invoice.invoice_number || invoice.filename}</small></td><td>₹{Number(invoice.grand_total).toFixed(2)}</td><td>₹{Number(invoice.amount_paid || 0).toFixed(2)}</td><td>₹{balance.toFixed(2)}</td><td><form onSubmit={(event) => void recordInvoicePayment(invoice.id, event)} style={{ display: "flex", flexWrap: "wrap", gap: 5 }}><input name="amount" type="number" min="0.01" max={balance.toFixed(2)} step="0.01" required placeholder="Amount" style={{ width: 100 }}/><input name="method" maxLength={40} placeholder="Method" style={{ width: 90 }}/><input name="reference" maxLength={160} placeholder="Reference" style={{ width: 110 }}/><button className="text-button" type="submit">Save payment</button></form></td></tr>})}{!invoices.some((invoice) => invoice.status === "APPROVED" && Number(invoice.grand_total || 0) > Number(invoice.amount_paid || 0)) && <tr><td colSpan={6} className="muted-cell">No outstanding approved invoices.</td></tr>}</tbody></table></div></section>
          </>}
          <footer className="page-footer"><span>Autonomous Business OS <span className="footer-separator">·</span> Capstone preview</span><span>Built for clearer business decisions <span className="footer-spark">✳</span></span></footer>
        </div>
      </main>
      {documentPreview && <div className="document-preview-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeDocumentPreview(); }}><section className="document-preview" role="dialog" aria-modal="true" aria-label={`Review invoice: ${documentPreview.filename}`}><header><div><strong>{documentPreview.filename}</strong><small>Compare the original with the extracted details before deciding.</small><a className="forecast-link" href={documentPreview.url} download={documentPreview.filename} target="_blank" rel="noreferrer">Download or open document</a><small>If your browser cannot display the preview, use the document link.</small></div><button type="button" aria-label="Close invoice review" onClick={closeDocumentPreview}>×</button></header><div className="document-preview-body"><div className="document-preview-file">{documentPreview.contentType.startsWith("image/") ? <img src={documentPreview.url} alt={documentPreview.filename}/> : <iframe title={`Invoice document: ${documentPreview.filename}`} src={documentPreview.url}/>}</div><aside className="document-preview-fields"><h2>Extracted details</h2><dl><dt>Vendor</dt><dd>{documentPreview.invoice.vendor_name || "Not found"}</dd><dt>Invoice number</dt><dd>{documentPreview.invoice.invoice_number || "Not found"}</dd><dt>Date</dt><dd>{documentPreview.invoice.invoice_date || "Not found"}</dd><dt>Direction</dt><dd>{documentPreview.invoice.transaction_type === "UNKNOWN" ? "Not set" : documentPreview.invoice.transaction_type}</dd><dt>Subtotal</dt><dd>{documentPreview.invoice.subtotal ?? "Not found"}</dd><dt>Tax</dt><dd>{documentPreview.invoice.tax_total ?? "Not found"}</dd><dt>Grand total</dt><dd>{documentPreview.invoice.grand_total == null ? "Not found" : `₹${Number(documentPreview.invoice.grand_total).toFixed(2)}`}</dd></dl><h3>Line items</h3>{documentPreview.invoice.lines?.length ? documentPreview.invoice.lines.map((line: any, index: number) => <div className="document-preview-line" key={index}><strong>{line.description || `Item ${index + 1}`}</strong><span>{line.is_stock_item === false ? "Service / expense · " : ""}{line.hsn_sac ? `HSN/SAC ${line.hsn_sac} · ` : ""}Qty {line.quantity ?? "—"} · Rate {line.unit_rate ?? "—"} · Amount {line.line_amount ?? "—"}</span></div>) : <p>No line items extracted.</p>}<ReviewSignals invoice={documentPreview.invoice} inventory={inventory} onResolveStock={() => { closeDocumentPreview(); setActive("Inventory"); }} onMissingPurchase={() => { closeDocumentPreview(); setUploadDirection("PURCHASE"); setActive("Invoices"); fileInput.current?.click(); }}/>{reviewError && <div className="review-error" role="alert">{reviewError}</div>}{documentPreview.invoice.status === "NEEDS_REVIEW" && <div className="modal-review-actions"><p className="modal-impact">{documentPreview.invoice.transaction_type === "PURCHASE" ? "Approving adds physical products to inventory; service and expense lines do not change stock." : documentPreview.invoice.transaction_type === "SALE" ? "Approving reduces physical stock and records sales; service lines do not change stock." : "Set the invoice direction before approval."}</p><div className="modal-action-buttons">{documentPreview.invoice.transaction_type === "UNKNOWN" ? <button type="button" className="review-approve review-disabled" disabled>Choose direction first</button> : <button type="button" className="review-approve" onClick={() => void reviewInvoice(documentPreview.invoice.id, "approve")}>Approve {documentPreview.invoice.transaction_type === "PURCHASE" ? "purchase" : "sale"}</button>}<button type="button" className="review-reject" onClick={() => void reviewInvoice(documentPreview.invoice.id, "reject")}>Reject invoice</button></div></div>}{documentPreview.invoice.status === "NEEDS_REVIEW" && <div className="modal-edit-details"><details className="document-edit"><summary>Correct extracted details</summary><form onSubmit={(event) => void saveInvoiceCorrection(documentPreview.invoice.id, event)} style={{ display: "grid", gap: 6, minWidth: 240, padding: 8 }}><label>Invoice direction<select name="transaction_type" defaultValue={documentPreview.invoice.transaction_type || "UNKNOWN"}><option value="UNKNOWN">Choose purchase or sale</option><option value="PURCHASE">Purchase (adds stock)</option><option value="SALE">Sale (reduces stock)</option></select></label><input name="vendor_name" placeholder="Vendor" defaultValue={documentPreview.invoice.vendor_name || ""}/><input name="invoice_number" placeholder="Invoice number" defaultValue={documentPreview.invoice.invoice_number || ""}/><input name="invoice_date" type="date" defaultValue={documentPreview.invoice.invoice_date || ""}/><input name="subtotal" type="number" min="0" step="0.01" placeholder="Subtotal" defaultValue={documentPreview.invoice.subtotal ?? ""}/><input name="tax_total" type="number" min="0" step="0.01" placeholder="Tax total" defaultValue={documentPreview.invoice.tax_total ?? ""}/><input name="grand_total" type="number" min="0" step="0.01" placeholder="Grand total" defaultValue={documentPreview.invoice.grand_total ?? ""}/><strong>Line items</strong><input type="hidden" name="line_count" value={documentPreview.invoice.lines.length + 1}/>{Array.from({ length: documentPreview.invoice.lines.length + 1 }, (_, index) => { const line = documentPreview.invoice.lines[index]; return <fieldset key={index} style={{ display: "grid", gap: 5, border: "1px solid #e9ede8", padding: 6 }}><legend>Item {index + 1}</legend>{typeof line?.confidence === "number" && <small>Textract confidence: {Math.round(line.confidence * 100)}%</small>}<input name={`line_description_${index}`} placeholder="Description" defaultValue={line?.description || ""}/><input name={`line_hsn_${index}`} placeholder="HSN/SAC" defaultValue={line?.hsn_sac || ""}/><label>Line type<select name={`line_kind_${index}`} defaultValue={line?.is_stock_item === false ? "service" : "stock"}><option value="stock">Physical product — update stock</option><option value="service">Service / expense — no stock change</option></select></label><label>Inventory product<select name={`line_product_${index}`} defaultValue={line?.product_id || ""}><option value="">Auto-match name or create purchase item</option>{inventory.map((product) => <option key={product.id} value={product.id}>{[product.name, product.product_type, product.brand, product.model, product.hsn_sac ? `HSN ${product.hsn_sac}` : null, `${product.quantity_on_hand} in stock`, product.sku].filter(Boolean).join(" · ")}</option>)}</select></label><input name={`line_quantity_${index}`} type="number" min="0.001" step="0.001" placeholder="Quantity" defaultValue={line?.quantity ?? ""}/><input name={`line_unit_${index}`} placeholder="Unit" defaultValue={line?.unit || ""}/><input name={`line_rate_${index}`} type="number" min="0" step="0.01" placeholder="Unit rate" defaultValue={line?.unit_rate ?? ""}/><input name={`line_override_reason_${index}`} placeholder="Below-floor price override reason" defaultValue={line?.price_override_reason || ""}/><input name={`line_amount_${index}`} type="number" min="0" step="0.01" placeholder="Line amount" defaultValue={line?.line_amount ?? ""}/><label>Serial numbers (comma separated, optional)<input name={`line_serials_${index}`} defaultValue={(line?.serial_numbers || []).join(", ")}/></label><label>Warranty months<input name={`line_warranty_${index}`} type="number" min="0" max="1200" placeholder="Optional" defaultValue={line?.warranty_months ?? ""}/></label></fieldset>})}<button className="primary-button" type="submit">Save fields and items</button></form></details></div>}</aside></div></section></div>}
    </div>
  );
}

export default App;

