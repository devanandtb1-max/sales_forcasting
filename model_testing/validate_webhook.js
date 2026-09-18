// Used inside the n8n Code node; only the webhook body enters the result.
const r = $input.first().json.body;
const errors = [];
const check = (ok, message) => { if (!ok) errors.push(message); };
const validQty = x => typeof x === 'number' && Number.isFinite(x) && x >= 0;
check(r?.status === 'passed' && r?.test_type === 'pipeline_smoke_test', 'Unexpected payload type');
check(typeof r?.run_id === 'string' && /^[a-f0-9]{32}$/.test(r.run_id), 'Invalid run ID');
check(r?.inventory_is_simulated === true, 'This workflow expects simulated inventory');
const forecasts = Array.isArray(r?.forecasts) ? r.forecasts : [];
const recommendations = Array.isArray(r?.recommendations) ? r.recommendations : [];
const summary = r?.summary || {};
check(forecasts.length > 0 && forecasts.length === summary.total_skus, 'Missing or inconsistent product count');
const ids = new Set(forecasts.map(x => x?.itemCode));
check(ids.size === forecasts.length, 'Duplicate forecast products');
const rawDate = summary.run_date;
const origin = new Date(`${rawDate}T00:00:00Z`);
const validDate = typeof rawDate === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(rawDate)
  && Number.isFinite(origin.getTime()) && origin.toISOString().slice(0, 10) === rawDate;
check(validDate, 'Invalid as-of date');
let start, end;
if (validDate) {
  start = new Date(origin.getTime() + 86400000).toISOString().slice(0, 10);
  end = new Date(origin.getTime() + 15 * 86400000).toISOString().slice(0, 10);
  check(summary.horizon?.start === start && summary.horizon?.end === end, 'Wrong summary horizon');
}
for (const row of forecasts) {
  check(typeof row?.itemCode === 'string' && row.itemCode.length > 0, 'Missing itemCode');
  check(validQty(row?.baseline_forecast_15d) && validQty(row?.hybrid_forecast_15d), 'Invalid forecast quantity');
  check(validDate && row?.horizon_start === start && row?.horizon_end === end && row?.as_of_date === rawDate, 'Wrong forecast dates');
}
check(recommendations.length === forecasts.length
  && new Set(recommendations.map(x => x?.itemCode)).size === forecasts.length, 'Wrong recommendation coverage');
for (const row of recommendations) {
  check(ids.has(row?.itemCode), 'Unexpected recommendation product');
  check(Number.isInteger(row?.recommended_order_qty) && row.recommended_order_qty >= 0, 'Invalid order quantity');
}
if (errors.length) return [{json: {status: 'failed', errors: [...new Set(errors)]}}];
return [{json: {
  status: 'passed', run_id: r.run_id, test_type: r.test_type,
  inventory_is_simulated: true, run_date: rawDate, horizon: summary.horizon,
  total_skus: forecasts.length, skus_with_active_promotion: summary.skus_with_active_promotion,
  po_decisions: summary.po_decisions, critical_stockout_risk_skus: summary.critical_stockout_risk_skus,
  total_forecast_quantity: forecasts.reduce((sum, row) => sum + row.hybrid_forecast_15d, 0),
  warnings: r.warnings, sample_forecasts: forecasts.slice(0, 5),
  sample_recommendations: recommendations.slice(0, 5)
}}];
