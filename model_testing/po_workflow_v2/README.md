# Pearcedale PO workflow V2

Import `n8n_po_workflow_v2.json` into n8n as a new workflow. It uses your current Modal endpoint and its existing request-body authentication, copied from the previous workflow. Treat this JSON as private because it contains the existing test authentication value.

## Run it now

1. Import the main JSON using n8n's **Import from File** action.
2. Open **Run Settings**. The default date is the known sample date `2026-09-16`, with 99 expected SKUs. Change these only to match the deployed data. You may fill `expected_sku_ids` with an independently verified SKU list for stricter coverage checking.
3. Keep `storage_enabled: false` for the first run. No database credentials are needed.
4. Inspect **Call Current Modal Model** and run **Execute workflow**. This invokes the deployed model and may consume Modal compute credits. Requests have a five-minute timeout and no automatic retries.
5. Open **Test Run Report**, then its Binary output, to download forecasts CSV, the full PO review CSV, and the complete normalized JSON. **Download Full Results** also retains the files if optional storage fails later.

Flow: manual trigger → settings → Modal HTTP POST → validation and normalization → prioritized PO review → full downloads → optional storage → report. Validation errors take the Stop Invalid Run branch and mark the execution failed. Storage errors also fail the execution. No emails or supplier orders are sent.

## Improvements

- All forecast and PO rows are retained, instead of only five examples.
- Validation checks response shape, HTTP/model errors, dates, quantities, SKU coverage, duplicates, and summary consistency.
- The review list prioritizes stockout risk, then issues and estimated shortage. It flags negative QOH, missing MOQ/cartons, carton or minimum-order inconsistencies, and questionable promotion labels.
- Original model order quantities remain unchanged. Days of supply assume uniform daily demand across the 15-day total; they are estimates, not daily model predictions. Raw model stockout dates are kept only for diagnostics because the current PO engine uses its system clock.
- Full JSON provides a stable `po-test.v2` contract for later website/backend integration. CSV text that could be interpreted as spreadsheet formulas is neutralized.
- Simulated inventory, unknown source-data freshness, and missing model version are recorded explicitly. Every recommendation remains `TEST_ONLY` with `submission_allowed: false`.

## Optional PostgreSQL or Supabase storage

1. Review and execute `schema.sql` on your database. It creates a separate `po_testing` schema with one JSONB run table and two row views. It does not alter your current business tables. Keep it private; do not expose these views directly to anonymous website clients.
2. Import `n8n_po_storage_v2.json`. Configure your PostgreSQL credential on **Store Run in PostgreSQL**, save the workflow, and allow your main workflow to call it.
3. Copy that workflow's ID from its URL. Set it as `storage_workflow_id` in the main workflow's Run Settings and set `storage_enabled: true`.
4. Execute a test and verify `storage_status: stored` and the saved database rows.

The database query uses a bound JSON parameter. Replaying the identical document with the same model run ID preserves the existing payload; different content with that ID returns `conflict` and fails the main workflow. Running Modal again creates a new run ID and a separate record. This is per-run deduplication, not an automatic same-date order lock. An existing run's payload is never overwritten by a conflicting result.

The storage branch is separate so the basic test can run without database setup. n8n documents this mechanism under [Execute Sub-workflow](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.executeworkflow/), and bound queries under [Postgres](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.postgres/).

## Replace the model later

Keep the **Call Current Modal Model** HTTP node. Update its URL, authentication, and request body for the new deployment. If the response schema changes, update **Normalize and Validate** to produce the same `po-test.v2` document. The review, exports, and storage consume that normalized document. Refresh the expected SKU list/date and run the tests against a saved response from the new model. This version deliberately rejects live-inventory responses until a live contract and ordering controls are implemented.

## Remaining architecture connections

This is the complete current-model testing and PO review flow. The following business integrations are not implemented by this import:

- SmartRetail ingestion, pagination and source DQ require actual API contracts, credentials, and mappings. The current Modal endpoint reads bundled data; sending new inventory in its body would not make it use that data.
- Live inventory position needs QOH, open purchase orders, commitments, lead times, and supplier mappings. Connect these before live PO decisions.
- Model training and backtesting are separate from daily inference.
- Approval and actual supplier/ERP submission require an authenticated approval process and idempotent order IDs. Test quantities must never be submitted directly.
- Website/backend integration can consume stored results through an authenticated API. No website endpoint is created here.
- RAG can explain policies and approved model documentation after retrieval is connected. Numeric answers should read the stored structured results, not invent values from document retrieval. No RAG or LLM node is included yet.

No schedule is enabled. Once end-to-end testing and data refresh are verified, add a Schedule Trigger with the required Melbourne time.

## Verification and maintenance

Run `node model_testing/po_workflow_v2/test_workflow.js` from the project directory. All 23 local checks passed against the saved 99-SKU sample, including malformed/error responses, quantity/date/coverage checks, preserved quantities, complete exports, CSV formula handling, and graph/JavaScript syntax checks.

The generated workflow has not yet been imported or executed in n8n, and the SQL has not been run against a database. Local checks do not establish those integrations work on your installed n8n version.

Edit the JavaScript sources and regenerate imports with `python model_testing/po_workflow_v2/build_workflows.py`. This copies the current HTTP node from the prior workflow without redeploying or changing the model.
