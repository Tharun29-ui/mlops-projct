import { useEffect, useState } from "react";
import { apiFetch } from "./auth";
import "./payments.css";

type Balance = { currency: string; invoiced: string; paid: string; outstanding: string };
type Party = { id: string; party_type: "VENDOR" | "CUSTOMER"; name: string; saved: boolean;
  phone?: string; email?: string; address?: string; notes?: string; aliases: string[];
  balances: Balance[]; invoice_count: number };
type Ledger = { party: Party; balances: Balance[]; invoices: any[]; payments: any[] };
const API = import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? "http://127.0.0.1:8000/api/v1" : "/api/v1");
const today = () => new Date().toLocaleDateString("en-CA");
const money = (value: string | number, currency = "INR") => Number(value).toLocaleString("en-IN", { style: "currency", currency });

async function request(path: string, options?: RequestInit) {
  const response = await apiFetch(`${API}${path}`, options);
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Check the entered details and try again.");
  return result;
}

export default function PaymentsWorkspace({ invoices, onRefresh, onNotice }: {
  invoices: any[]; onRefresh: () => Promise<void>; onNotice: (message: string) => void;
}) {
  const [parties, setParties] = useState<Party[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("ALL");
  const [view, setView] = useState("ALL");
  const [editing, setEditing] = useState<Partial<Party> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [unassigned, setUnassigned] = useState(0);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let current = true;
    setLoading(true);
    request("/parties").then(result => {
      if (!current) return;
      setParties(result.parties); setUnassigned(result.unassigned_invoice_count);
      setSelectedId(previous => result.parties.some((p: Party) => p.id === previous) ? previous : result.parties[0]?.id || "");
      setError("");
    }).catch(e => { if (current) setError(e.message); }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [invoices, revision]);

  useEffect(() => {
    let current = true;
    setLedger(null);
    if (selectedId) request(`/parties/${selectedId}/ledger`).then(result => {
      if (current) setLedger(result);
    }).catch(e => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [selectedId, invoices, revision]);

  const filtered = parties.filter(p => (kind === "ALL" || p.party_type === kind) &&
    [p.name, p.phone, p.email, ...p.aliases].filter(Boolean).join(" ").toLowerCase().includes(query.trim().toLowerCase()));
  const selected = parties.find(p => p.id === selectedId);
  const balances = new Map<string, { receivable: number; payable: number }>();
  parties.forEach(p => p.balances.forEach(b => {
    const total = balances.get(b.currency) || { receivable: 0, payable: 0 };
    total[p.party_type === "VENDOR" ? "payable" : "receivable"] += Number(b.outstanding);
    balances.set(b.currency, total);
  }));

  const saveContact = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const data = { party_type: form.get("party_type"), name: String(form.get("name") || "").trim(),
      phone: String(form.get("phone") || "").trim() || null, email: String(form.get("email") || "").trim() || null,
      address: String(form.get("address") || "").trim() || null, notes: String(form.get("notes") || "").trim() || null,
      aliases: String(form.get("aliases") || "").split("\n").map(s => s.trim()).filter(Boolean) };
    setBusy(true); setError("");
    try {
      const result = await request(editing?.saved ? `/parties/${editing.id}` : "/parties", {
        method: editing?.saved ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
      setSelectedId(result.id); setEditing(null); setRevision(n => n + 1);
      onNotice("Contact saved. Matching approved invoices and payment history appear in its ledger.");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save contact."); }
    finally { setBusy(false); }
  };

  const savePayment = async (invoiceId: string, event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true); setError("");
    try {
      await request(`/invoices/${invoiceId}/payments`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount: String(form.get("amount")), payment_date: form.get("payment_date"),
          method: form.get("method") || null, reference: String(form.get("reference") || "").trim() || null }) });
      formElement.reset();
      onNotice("Payment recorded. The invoice balance and party history have been updated.");
      await onRefresh(); setRevision(n => n + 1);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not record payment."); }
    finally { setBusy(false); }
  };

  return <div className="payments-workspace">
    <div className="panel-heading"><div><h2>Vendors, customers & payments</h2><p>Keep contact details, invoice balances and every recorded payment together.</p></div>
      <button className="primary-button" onClick={() => setEditing({ party_type: "VENDOR" })}>+ Add vendor / customer</button></div>
    <div className="party-summary-grid">
      <article className="party-summary"><span>Vendors</span><strong>{parties.filter(p => p.party_type === "VENDOR").length}</strong><small>You pay these businesses</small></article>
      <article className="party-summary"><span>Customers</span><strong>{parties.filter(p => p.party_type === "CUSTOMER").length}</strong><small>You receive payments from them</small></article>
      {[...balances].map(([currency, total]) => <article className="party-summary" key={currency}><span>Outstanding · {currency}</span><strong>{money(total.receivable, currency)} receivable</strong><small>{money(total.payable, currency)} payable to vendors</small></article>)}
    </div>
    {error && <div className="review-error" role="alert">{error}</div>}
    {unassigned > 0 && <p className="party-help">{unassigned} approved invoice(s) have no identified vendor/customer. Correct the party name on the invoice before relying on a party ledger.</p>}
    {editing && <section className="panel party-editor"><div className="panel-heading"><h3>{editing.saved ? "Edit contact" : "Add contact details"}</h3><button className="select-button" onClick={() => setEditing(null)}>Cancel</button></div>
      <form key={editing.id || "new"} onSubmit={saveContact} className="party-form">
        <label>Contact type<select name="party_type" defaultValue={editing.party_type || "VENDOR"} aria-readonly={editing.saved} onChange={event => { if (editing.saved) event.target.value = editing.party_type!; }}><option value="VENDOR">Vendor / supplier</option><option value="CUSTOMER">Customer</option></select></label>
        <label>Business / person name<input name="name" required maxLength={300} defaultValue={editing.name} placeholder="Use the invoice party name"/></label>
        <label>Phone<input name="phone" type="tel" maxLength={60} defaultValue={editing.phone || ""}/></label>
        <label>Email<input name="email" type="email" maxLength={254} defaultValue={editing.email || ""}/></label>
        <label>Address<textarea name="address" rows={2} maxLength={2000} defaultValue={editing.address || ""}/></label>
        <label>Other invoice names (one per line)<textarea name="aliases" rows={2} defaultValue={(editing.aliases || []).join("\n")} placeholder="Only confirmed names for the same party"/></label>
        <label>Notes<textarea name="notes" rows={2} maxLength={1000} defaultValue={editing.notes || ""}/></label>
        <p className="party-help">Exact names and confirmed aliases connect approved invoices. Vendor and customer balances are kept separate, including when the same business has both roles.</p>
        <button className="primary-button" disabled={busy}>{busy ? "Saving…" : "Save contact"}</button>
      </form></section>}
    <section className="panel"><div className="panel-heading"><div><h3>Your contacts</h3><p>Parties from approved invoices appear automatically. Add their contact details here.</p></div></div>
      <div className="party-search"><input aria-label="Search vendors and customers" placeholder="Search name, phone or email" value={query} onChange={e => setQuery(e.target.value)}/>
        <select aria-label="Contact type filter" value={kind} onChange={e => setKind(e.target.value)}><option value="ALL">Vendors & customers</option><option value="VENDOR">Vendors</option><option value="CUSTOMER">Customers</option></select></div>
      {loading ? <p role="status">Loading contacts…</p> : <div className="party-contact-grid">{filtered.map(p => <button className={`party-contact ${selectedId === p.id ? "selected" : ""}`} key={p.id} onClick={() => { setSelectedId(p.id); setView("ALL"); }}>
        <span className="party-kind">{p.party_type === "VENDOR" ? "Vendor" : "Customer"}</span><strong>{p.name}</strong><small>{p.phone || p.email || "Add phone / email"} · {p.invoice_count} invoice(s)</small>
        {p.balances.map(b => <span key={b.currency}>{money(b.outstanding, b.currency)} {p.party_type === "VENDOR" ? "to pay" : "to collect"}</span>)}
      </button>)}{!filtered.length && <p>No matching contacts. Add a vendor/customer to start.</p>}</div>}
    </section>
    {selected && <section className="panel"><div className="panel-heading"><div><span className="party-kind">{selected.party_type === "VENDOR" ? "Vendor ledger" : "Customer ledger"}</span><h2>{selected.name}</h2><p>{[selected.phone, selected.email, selected.address].filter(Boolean).join(" · ") || "Contact details have not been saved yet."}</p></div><button className="select-button" onClick={() => setEditing(selected)}>{selected.saved ? "Edit contact" : "Add contact details"}</button></div>
      {selected.notes && <p>{selected.notes}</p>}
      {!ledger ? <p role="status">Loading history…</p> : <>
        <div className="party-summary-grid">{ledger.balances.map(b => <article className="party-summary" key={b.currency}><span>{b.currency} · {ledger.invoices.filter(i => i.currency === b.currency).length} invoice(s)</span><strong>{money(b.outstanding, b.currency)} outstanding</strong><small>Invoiced {money(b.invoiced, b.currency)} · Payments {money(b.paid, b.currency)}</small></article>)}</div>
        <div className="panel-heading"><h3>Invoice history</h3><select aria-label="Invoice balance filter" value={view} onChange={e => setView(e.target.value)}><option value="ALL">All invoices</option><option value="OUTSTANDING">Outstanding</option><option value="SETTLED">Settled</option></select></div>
        <div className="table-scroll"><table className="party-invoices"><thead><tr><th>Invoice / date</th><th>Total</th><th>Paid</th><th>Outstanding</th><th>Record payment</th></tr></thead><tbody>
          {ledger.invoices.filter(i => view === "ALL" || (view === "OUTSTANDING" ? Number(i.outstanding) > 0 : i.outstanding !== null && Number(i.outstanding) === 0)).map(i => <tr key={i.id}>
            <td><strong>{i.invoice_number || i.filename}</strong><small>{i.invoice_date || "Date not recorded"}</small></td><td>{i.grand_total === null ? "Total not recorded" : money(i.grand_total, i.currency)}</td><td>{money(i.amount_paid, i.currency)}</td><td>{i.outstanding === null ? "Check invoice total" : money(i.outstanding, i.currency)}</td>
            <td>{Number(i.outstanding) > 0 ? <form className="party-payment-form" onSubmit={event => void savePayment(String(i.id), event)}>
              <label>Amount ({i.currency})<input name="amount" type="number" required min="0.01" max={i.outstanding} step="0.01" placeholder="0.00"/></label>
              <label>Payment date<input name="payment_date" type="date" required defaultValue={today()} max={today()}/></label>
              <label>Method<select name="method" required><option value="">Choose…</option><option>Cash</option><option>UPI</option><option>Bank transfer</option><option>Card</option><option>Other</option></select></label>
              <label>Reference<input name="reference" maxLength={160} placeholder="Receipt / transaction ID"/></label><button className="select-button" disabled={busy}>{busy ? "Saving…" : "Save payment"}</button>
            </form> : <span className="status-badge green">{i.outstanding === null ? "Review total" : "Settled"}</span>}</td>
          </tr>)}
          {!ledger.invoices.length && <tr><td colSpan={5}>No approved invoices match this contact yet.</td></tr>}
        </tbody></table></div>
        <h3 className="party-history-title">Payment history</h3><p className="party-help">Recorded payments stay here after an invoice is fully settled. This page records payments; it does not transfer money.</p>
        <div className="table-scroll"><table><thead><tr><th>Payment date</th><th>Invoice</th><th>Direction</th><th>Amount</th><th>Method / reference</th><th>Recorded at</th></tr></thead><tbody>
          {ledger.payments.map(p => <tr key={p.id}><td>{p.payment_date || "Earlier balance"}</td><td>{p.invoice_number}</td><td>{p.direction === "PAID_TO_VENDOR" ? "Paid to vendor" : "Received from customer"}</td><td>{money(p.amount, p.currency)}</td><td>{p.legacy_balance ? "Individual payment details were not recorded" : [p.method, p.reference].filter(Boolean).join(" · ") || "Not recorded"}</td><td>{p.recorded_at ? new Date(p.recorded_at).toLocaleString() : "Not recorded"}</td></tr>)}
          {!ledger.payments.length && <tr><td colSpan={6}>No payments recorded for this contact.</td></tr>}
        </tbody></table></div>
      </>}
    </section>}
  </div>;
}

export function PhysicalCount({ product, onRefresh, onNotice }: { product: any; onRefresh: () => Promise<void>; onNotice: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      await request(`/inventory/${encodeURIComponent(product.id)}/physical-count`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ counted_quantity: Number(form.get("counted_quantity")), expected_quantity: product.quantity_on_hand, confirmed: form.get("confirmed") === "on" }) });
      await onRefresh(); onNotice("Physical count recorded. The quantity was not changed.");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not confirm count."); }
    finally { setBusy(false); }
  };
  return <details className="physical-count"><summary>Confirm physical count</summary><p>Count this product on the shelf. Recorded quantity: <strong>{product.quantity_on_hand}</strong>. If different, record a stock correction with a reason first.</p>
    <form onSubmit={submit}><label>Quantity counted<input name="counted_quantity" type="number" required min="0" step="1"/></label><label className="stock-confirm"><input name="confirmed" type="checkbox" required/>I physically checked this quantity.</label><button className="select-button" disabled={busy}>{busy ? "Saving…" : "Confirm count"}</button></form>{error && <p role="alert" className="review-error">{error}</p>}
  </details>;
}
