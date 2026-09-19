const doc = $input.first().json;
// Formula-like strings are neutralized for spreadsheet viewers, then CSV-quoted.
const cell = value => {
  let s = value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  if (typeof value === 'string' && /^[\s]*[=+\-@]/.test(s)) s = "'" + s;
  return '"' + s.replace(/"/g,'""') + '"';
};
const csv = (rows, columns) => [columns.map(cell).join(','), ...rows.map(row => columns.map(k => cell(row[k])).join(','))].join('\r\n');
const file = (name, content, mimeType) => ({data: Buffer.from(content,'utf8').toString('base64'), mimeType, fileName:name});
const suffix = `${doc.as_of_date}_${doc.run_id}`;
return [{json: doc, binary: {
  forecasts_csv: file(`forecasts_${suffix}.csv`, csv(doc.forecasts,
    ['sku','category','department','uom','horizon_start','horizon_end','baseline_qty','forecast_qty','promotion_active','promotion_type','promotion_days','discount_fraction']), 'text/csv'),
  po_review_csv: file(`po_review_TEST_ONLY_${suffix}.csv`, csv(doc.recommendations,
    ['review_rank','sku','category','department','current_stock','forecast_qty','recommended_order_qty','decision','stockout_risk','estimated_days_of_supply','moq','carton_qty','supplier','lead_time_days','review_status','simulated_inventory','submission_allowed','review_issues','explanation']), 'text/csv'),
  run_json: file(`po_run_${suffix}.json`, JSON.stringify(doc,null,2), 'application/json'),
}}];
