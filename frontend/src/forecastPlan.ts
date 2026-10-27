export function buildMonthlyPlan(
  forecasts: any[], inventory: any[], sellingRate: (product: any) => number,
) {
  const months = new Map<string, Map<string, number>>();
  for (const row of forecasts) {
    const quantity = Number(row.predicted_quantity);
    if (!Number.isFinite(quantity) || quantity < 0) throw new Error("Invalid demand forecast");
    const month = String(row.forecast_date).slice(0, 7);
    if (!months.has(month)) months.set(month, new Map());
    const group = months.get(month)!;
    group.set(String(row.product_id), (group.get(String(row.product_id)) || 0) + quantity);
  }
  const remaining = new Map(inventory.map((p: any) => [String(p.id), Math.max(0, Number(p.quantity_on_hand || 0))]));
  return [...months.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([month, groups]) => {
    let revenue = 0, cost = 0, purchaseCash = 0, priced = 0, units = 0;
    for (const [id, quantity] of groups) {
      units += quantity;
      const product = inventory.find((p: any) => String(p.id) === id);
      const unitCost = Number(product?.unit_cost);
      if (!product || !Number.isFinite(unitCost) || unitCost <= 0) continue;
      const rate = sellingRate(product);
      if (!Number.isFinite(rate) || rate < 0) continue;
      revenue += quantity * rate; cost += quantity * unitCost;
      const available = remaining.get(id) || 0;
      const purchaseUnits = Math.max(0, Math.ceil(quantity - available));
      purchaseCash += purchaseUnits * unitCost;
      // Whole units bought for a fractional demand estimate carry into next month.
      remaining.set(id, Math.max(0, available + purchaseUnits - quantity));
      priced += 1;
    }
    return {month, units, revenue, cost, purchaseCash, priced, products: groups.size};
  });
}
