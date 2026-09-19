// Response contract for the current Modal sample endpoint.
// Replace this node only if a future model changes the response schema.
const incoming = $input.first().json;
const expectedDate = $('Prepare Run Context').first().json.as_of_date;
if (incoming.error || incoming.statusCode >= 400) {
  return [{json: {status: 'failed', stage: 'modal_http',
    errors: [String(incoming.error?.message || incoming.error || `HTTP ${incoming.statusCode}`)]}}];
}
const r = typeof incoming.body === 'string' ? JSON.parse(incoming.body) : (incoming.body || incoming);
if (r?.status === 'error') {
  return [{json: {status: 'failed', stage: 'modal_model', errors: [r.error || 'Model error'], run_id: r.run_id || null}}];
}
const errors = [];
const check = (condition, message) => { if (!condition) errors.push(message); };
const isQty = x => typeof x === 'number' && Number.isFinite(x) && x >= 0;
const f = Array.isArray(r?.forecasts) ? r.forecasts : [];
const p = Array.isArray(r?.recommendations) ? r.recommendations : [];
const summary = r?.summary || {};
check(r?.status === 'passed' && r?.test_type === 'pipeline_smoke_test', 'Unexpected response type');
check(typeof r?.run_id === 'string' && /^[a-f0-9]{32}$/.test(r.run_id), 'Invalid run ID');
check(r?.inventory_is_simulated === true, 'Expected simulated inventory in sample test');
check(summary.run_date === expectedDate, 'As-of date does not match request');
const base = new Date(`${expectedDate}T00:00:00Z`);
const start = new Date(base.getTime() + 86400000).toISOString().slice(0, 10);
const end = new Date(base.getTime() + 15 * 86400000).toISOString().slice(0, 10);
check(summary.horizon?.start === start && summary.horizon?.end === end, 'Incorrect 15-day horizon');
check(f.length > 0 && f.length === summary.total_skus, 'Missing or inconsistent forecasts');
const forecastIds = new Set(f.map(x => x?.itemCode));
check(forecastIds.size === f.length, 'Duplicate forecast SKU');
for (const row of f) {
  check(typeof row?.itemCode === 'string' && row.itemCode.length > 0, 'Missing SKU');
  check(isQty(row?.baseline_forecast_15d) && isQty(row?.hybrid_forecast_15d), 'Invalid forecast demand');
  check(row?.as_of_date === expectedDate && row?.horizon_start === start && row?.horizon_end === end,
    'Incorrect row dates');
}
check(p.length === f.length, 'PO preview coverage does not match forecasts');
const recommendationIds = new Set(p.map(x => x?.itemCode));
check(recommendationIds.size === p.length && [...recommendationIds].every(x => forecastIds.has(x)),
  'Duplicate or unexpected PO preview SKU');
for (const row of p) {
  check(Number.isInteger(row?.recommended_order_qty) && row.recommended_order_qty >= 0,
    'Invalid PO preview quantity');
}
if (errors.length) return [{json: {status: 'failed', stage: 'validation',
  errors: [...new Set(errors)], run_id: r?.run_id || null}}];
return [{json: {
  status: 'passed', mode: 'SAMPLE_TEST', run_id: r.run_id,
  as_of_date: expectedDate, horizon: summary.horizon, total_skus: f.length,
  total_forecast_qty: f.reduce((sum, row) => sum + row.hybrid_forecast_15d, 0),
  simulated_inventory: true, po_submission_allowed: false,
  po_preview_decisions: summary.po_decisions,
  sample_forecasts: f.slice(0, 5), sample_po_preview: p.slice(0, 5),
  warnings: r.warnings || []
}}];
