// n8n Code node. Validates external results before producing a stable internal contract.
const config = $('Run Settings').first().json;
const fail = (stage, errors) => [{json: {status: 'failed', stage, errors,
  execution_id: String($execution.id), requested_as_of: config.as_of_date}}];
try {
  const incoming = $input.first()?.json;
  if (!incoming || incoming.error || incoming.statusCode >= 400) {
    return fail('modal_http', ['Modal request failed. Inspect the HTTP node for details; do not automatically retry a paid request.']);
  }
  let r = Object.prototype.hasOwnProperty.call(incoming, 'body') ? incoming.body : incoming;
  if (typeof r === 'string') {
    try { r = JSON.parse(r); } catch { return fail('response_format', ['Modal returned invalid JSON']); }
  }
  if (!r || typeof r !== 'object' || Array.isArray(r)) return fail('response_format', ['Expected a JSON object']);
  if (r.status === 'error') return fail('model', ['Modal reported a model or authentication error. Inspect the HTTP output.']);
  const errors = [];
  const warnings = [];
  const check = (ok, message) => { if (!ok && errors.length < 100) errors.push(message); };
  const number = (value, label, nonnegative = true, integer = false) => {
    if (value === null || value === undefined || typeof value === 'boolean' ||
        (typeof value !== 'string' && typeof value !== 'number') ||
        (typeof value === 'string' && value.trim() === '')) {
      check(false, `${label}: missing or invalid number`); return null;
    }
    const n = Number(value);
    if (!Number.isFinite(n) || (nonnegative && n < 0) || (integer && !Number.isSafeInteger(n))) {
      check(false, `${label}: invalid number`); return null;
    }
    return n;
  };
  const optionalNumber = value => {
    if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null;
    const n = Number(value); return Number.isFinite(n) ? n : null;
  };
  const boolean = (v, label) => {
    if (v === true || v === 'true') return true;
    if (v === false || v === 'false') return false;
    check(false, `${label}: invalid boolean`); return null;
  };
  const requested = config.as_of_date;
  const base = new Date(`${requested}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(requested) || !Number.isFinite(base.getTime()) ||
      base.toISOString().slice(0, 10) !== requested) return fail('configuration', ['Invalid requested as-of date']);
  const start = new Date(base.getTime() + 86400000).toISOString().slice(0, 10);
  const end = new Date(base.getTime() + 15 * 86400000).toISOString().slice(0, 10);
  check(r.status === 'passed' && r.test_type === 'pipeline_smoke_test', 'Unexpected model response type');
  check(typeof r.run_id === 'string' && /^[a-f0-9]{32}$/.test(r.run_id), 'Invalid model run ID');
  check(r.inventory_is_simulated === true, 'This version accepts simulated inventory only');
  const summary = r.summary || {};
  check(summary.run_date === requested, 'Returned as-of date differs from request');
  check(summary.horizon?.start === start && summary.horizon?.end === end, 'Wrong summary horizon');
  const rawF = Array.isArray(r.forecasts) ? r.forecasts : [];
  const rawP = Array.isArray(r.recommendations) ? r.recommendations : [];
  check(rawF.length > 0 && rawF.length === summary.total_skus, 'Missing forecasts or inconsistent summary count');
  if (config.expected_sku_count !== null) check(rawF.length === config.expected_sku_count, 'Unexpected SKU coverage');
  const forecasts = [];
  const bySku = new Map();
  for (const row of rawF) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) { check(false, 'Invalid forecast row'); continue; }
    const sku = row.itemCode;
    check(typeof sku === 'string' && sku.trim().length > 0, 'Missing forecast SKU');
    check(!bySku.has(sku), `Duplicate forecast SKU: ${sku}`);
    check(row.as_of_date === requested && row.horizon_start === start && row.horizon_end === end, `${sku}: incorrect forecast dates`);
    const f = {
      sku, category: row.commodityName || null, department: row.departmentName || null,
      uom: row.uom_class || null, horizon_start: start, horizon_end: end,
      baseline_qty: number(row.baseline_forecast_15d, `${sku} baseline`),
      forecast_qty: number(row.hybrid_forecast_15d, `${sku} forecast`),
      promotion_active: boolean(row.promotion_active, `${sku} promotion`),
      promotion_type: row.promo_type || null,
      promotion_days: number(row.promo_days_in_horizon, `${sku} promotion days`, true, true),
      discount_fraction: optionalNumber(row.discount_pct),
      predicted_uplift_fraction: optionalNumber(row.predicted_uplift),
      moq: optionalNumber(row.minOrderQty), carton_qty: optionalNumber(row.cartonQty),
    };
    check(f.promotion_days <= 15, `${sku}: promotion days exceed horizon`);
    forecasts.push(f); bySku.set(sku, f);
  }
  if (config.expected_sku_ids?.length) {
    const expected = new Set(config.expected_sku_ids);
    check(expected.size === bySku.size && [...bySku.keys()].every(id => expected.has(id)), 'SKU set differs from configured expected SKUs');
  }
  const decisions = {CREATE_PO: 0, REVIEW_REQUIRED: 0, NO_PO_REQUIRED: 0};
  const riskCounts = {Critical: 0, High: 0, Medium: 0, Low: 0};
  const decisionMap = {'CREATE PO': 'CREATE_PO', 'REVIEW REQUIRED': 'REVIEW_REQUIRED', 'NO PO REQUIRED': 'NO_PO_REQUIRED'};
  const recommendations = [];
  const poIds = new Set();
  for (const row of rawP) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) { check(false, 'Invalid PO row'); continue; }
    const sku = row.itemCode;
    check(bySku.has(sku) && !poIds.has(sku), `Unexpected or duplicate PO SKU: ${sku}`);
    poIds.add(sku);
    check(row.horizon_start === start && row.horizon_end === end, `${sku}: incorrect PO dates`);
    const demand = number(row.hybrid_forecast_15d, `${sku} PO demand`);
    const baseline = number(row.baseline_forecast_15d, `${sku} PO baseline`);
    const stock = number(row.current_stock, `${sku} stock`, false);
    const qty = number(row.recommended_order_qty, `${sku} recommended order`, true, true);
    const f = bySku.get(sku);
    check(f && Math.abs(demand - f.forecast_qty) <= 0.011 && Math.abs(baseline - f.baseline_qty) <= 0.011,
      `${sku}: PO demand differs from forecast`);
    const decision = decisionMap[row.po_decision];
    check(Boolean(decision), `${sku}: unknown PO decision`);
    check(Object.prototype.hasOwnProperty.call(riskCounts, row.stockout_risk), `${sku}: unknown stockout risk`);
    if (decision) decisions[decision]++;
    if (Object.prototype.hasOwnProperty.call(riskCounts, row.stockout_risk)) riskCounts[row.stockout_risk]++;
    recommendations.push({sku, forecast_qty: demand, current_stock: stock,
      recommended_order_qty: qty, decision, stockout_risk: row.stockout_risk,
      model_stockout_date: row.stockout_date || null, simulated_inventory: true,
      submission_allowed: false});
  }
  check(rawP.length === rawF.length && poIds.size === bySku.size, 'Incomplete recommendation coverage');
  for (const [decision, count] of Object.entries(decisions)) check(summary.po_decisions?.[decision] === count, `Summary mismatch: ${decision}`);
  check(summary.critical_stockout_risk_skus === riskCounts.Critical, 'Critical-risk summary mismatch');
  const promotionCount = forecasts.filter(f => f.promotion_active).length;
  check(summary.skus_with_active_promotion === promotionCount, 'Promotion summary mismatch');
  if (errors.length) return fail('validation', [...new Set(errors)]);
  warnings.push('Inventory is simulated. All PO recommendations require review and cannot be submitted.',
    'The current model returns a 15-day total approximated from weekly forecasts.',
    'Source-data freshness and model version are not reported by this endpoint.',
    'Product labels are commodity/category names, not verified product descriptions.',
    'Model stockout dates use its system clock; retain them only as raw diagnostic fields.');
  if (!config.expected_sku_ids?.length) warnings.push('SKU count is checked; an independent exact SKU list has not been configured.');
  return [{json: {
    status: 'passed', schema_version: 'po-test.v2', mode: 'SAMPLE_TEST',
    run_id: r.run_id, execution_id: String($execution.id), received_at: new Date().toISOString(),
    store: config.store, as_of_date: requested, horizon: {start, end},
    inventory_is_simulated: true, submission_allowed: false,
    provenance: {model_version: null, data_cutoff: null, source: 'Modal bundled sample snapshot',
      forecast_granularity: '15_day_total', freshness_verified: false},
    summary: {total_skus: forecasts.length, promoted_skus: promotionCount,
      total_forecast_qty: forecasts.reduce((s,f) => s + f.forecast_qty, 0),
      po_decisions: decisions, stockout_risks: riskCounts},
    forecasts, recommendations, warnings,
  }}];
} catch {
  return fail('response_format', ['Malformed model response could not be normalized; inspect the HTTP output.']);
}
