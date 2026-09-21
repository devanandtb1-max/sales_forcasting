import json
filepath = 'model_testing/n8n_po_project_workflow.json'
with open(filepath, 'r', encoding='utf-8') as f:
    data = json.load(f)
for n in data['nodes']:
    if n['name'] == 'Architecture 5 - Local Dataset Phase':
        n['parameters']['content'] = """## LOCAL_DATASET phase (current)
SmartRetail API details are not available yet. This branch reads the project's real historical dataset, which is now served directly from Supabase (po_system.sales_history and po_system.promotions) through a small local HTTP bridge (model_testing/api.py, POST /local-dataset). It is still exposed via the same cloudflared tunnel pattern already used for the Modal test bridge - n8n itself runs remotely (n8n.srv1691210.hstgr.cloud) and cannot read your local machine or Supabase directly.

Set Run Configuration.local_dataset_api_url to the tunnel URL, and create an n8n Header Auth credential named 'Local Dataset API Key' matching your MODEL_TEST_API_KEY. /local-dataset now returns ~400 days of DAILY history per SKU (sales up to as_of_date, promotions through the 15-day horizon since promos are known in advance), not a single snapshot - the model needs real lag/rolling history to forecast. Modal rebuilds the same weekly features preprocess_features.py builds for the bundled data, using this history instead, and never falls back to bundled data for LOCAL_DATASET. Fields the historical dataset cannot provide (qoh, open_po_quantity, lead_time, product name, promotion date ranges) are still preserved as null/UNKNOWN in the per-SKU DQ snapshot, never fabricated."""
with open(filepath, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2)
print("Updated sticky note.")
