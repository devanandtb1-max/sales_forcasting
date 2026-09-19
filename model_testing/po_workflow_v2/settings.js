// Edit these settings before running. Keep the known sample date until data is refreshed.
const settings = {store: 'Pearcedale', as_of_date: '2026-09-16', expected_sku_count: 99,
  expected_sku_ids: [], storage_enabled: false, storage_workflow_id: ''};
if (!/^\d{4}-\d{2}-\d{2}$/.test(settings.as_of_date) ||
    new Date(settings.as_of_date + 'T00:00:00Z').toISOString().slice(0,10) !== settings.as_of_date)
  throw new Error('Enter a valid YYYY-MM-DD as_of_date');
if (settings.storage_enabled && !settings.storage_workflow_id.trim())
  throw new Error('Configure the storage subworkflow ID before enabling storage');
return [{json: settings}];
