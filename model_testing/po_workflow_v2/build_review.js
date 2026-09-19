const doc = $input.first().json;
const bySku = new Map(doc.forecasts.map(f => [f.sku, f]));
const riskOrder = {Critical: 0, High: 1, Medium: 2, Low: 3};
const rows = doc.recommendations.map(p => {
  const f = bySku.get(p.sku);
  const issues = [];
  const shortfall = Math.max(0, f.forecast_qty - p.current_stock);
  if (p.current_stock < 0) issues.push('Negative QOH requires investigation');
  if (!(Number.isSafeInteger(f.moq) && f.moq > 0)) issues.push('MOQ unavailable or invalid');
  if (!(Number.isSafeInteger(f.carton_qty) && f.carton_qty > 0)) issues.push('Carton quantity unavailable or invalid');
  if (p.recommended_order_qty > 0 && f.carton_qty > 0 && p.recommended_order_qty % f.carton_qty !== 0)
    issues.push('Order quantity is not a carton multiple');
  if (p.recommended_order_qty > 0 && f.moq > 0 && p.recommended_order_qty < f.moq)
    issues.push('Order quantity is below MOQ');
  if (p.decision === 'NO_PO_REQUIRED' && p.recommended_order_qty !== 0)
    issues.push('Decision and order quantity disagree');
  if (p.decision !== 'NO_PO_REQUIRED' && p.recommended_order_qty === 0)
    issues.push('Ordering decision has zero quantity');
  if (f.promotion_active && (!f.promotion_type || f.promotion_type === 'NONE' || !(f.discount_fraction > 0)))
    issues.push('Promotion label lacks a positive discount or named type; inspect source');
  const demand = Number(f.forecast_qty.toFixed(2));
  const days = demand > 0 && p.current_stock >= 0 ? Number((p.current_stock / (f.forecast_qty / 15)).toFixed(2)) : null;
  const explain = `Sample forecast is ${demand} ${f.uom || 'units'} over 15 days; simulated stock is ${p.current_stock}. ` +
    `The model's PO engine returned ${p.recommended_order_qty} units (${p.decision}). ` +
    'This is a test result, not an approved supplier order.';
  return {...p, category: f.category, department: f.department, uom: f.uom,
    baseline_qty: f.baseline_qty, forecast_qty: f.forecast_qty,
    moq: f.moq, carton_qty: f.carton_qty, supplier: null, lead_time_days: null,
    estimated_shortfall: Number(shortfall.toFixed(2)), estimated_days_of_supply: days,
    promotion_active: f.promotion_active, review_issues: issues,
    review_status: 'TEST_ONLY', explanation: explain};
});
rows.sort((a,b) => riskOrder[a.stockout_risk] - riskOrder[b.stockout_risk] ||
  b.review_issues.length - a.review_issues.length || b.estimated_shortfall - a.estimated_shortfall || a.sku.localeCompare(b.sku));
rows.forEach((r,i) => { r.review_rank = i + 1; });
const actionable = rows.filter(r => r.decision !== 'NO_PO_REQUIRED' || r.review_issues.length > 0 || ['Critical','High'].includes(r.stockout_risk));
return [{json: {...doc, recommendations: rows,
  summary: {...doc.summary, review_queue_count: actionable.length,
    rows_with_issues: rows.filter(r => r.review_issues.length).length},
  review_queue: actionable.map(r => r.sku)}}];
