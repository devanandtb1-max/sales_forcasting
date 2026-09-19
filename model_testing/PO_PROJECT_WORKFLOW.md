# Purchase-order project: end-to-end workflow

This document maps the user's proposed architecture to executable boundaries.
It distinguishes the **current sample test** from integrations that still need
API contracts, database tables, and approved business rules. The importable
`n8n_po_project_workflow.json` exercises the current Modal step and output
validation; it deliberately does not create a real PO.

## A. Operational data workflow (n8n; separate sub-workflow)

1. **Trigger** on a schedule or on demand. Assign a run ID, Pearcedale store
   ID, and Australia/Melbourne as-of timestamp. Avoid two overlapping store
   refreshes.
2. **Fetch from SmartRetail** with saved n8n credentials. Collect products,
   sales, inventory snapshots, suppliers, pricing, promotions, and existing
   purchase orders only where their APIs actually provide those data. Paginate
   per endpoint; record retrieval time and source request ID. Do not invent
   missing sources.
3. **Save raw response** before transforming. Preserve API status and
   `no_row_returned` flags. A missing sales row is unknown demand, not zero.
4. **Data quality** validates required columns, types, dates, SKU identity,
   duplicate keys, impossible values, stale snapshots, and API anomalies.
   Negative `snapshotQOH` is flagged for review rather than silently set to
   zero. Keep rejected rows and reasons.
5. **Upsert validated rows into PostgreSQL/Supabase** with source and as-of
   timestamps. Use stable unique keys so retries are idempotent. Record a DQ
   run summary and stop downstream forecasts when required inputs fail.

The existing Pearcedale n8n collection and DQ workflows can fill these roles.
Their exact workflow IDs, API payloads, and table schemas are needed before
connecting them to a master orchestrator.

## B. Model development workflow (Python; versioned, not a daily n8n node)

6. Prepare the historical dataset: preserve unknown demand, analyze stockouts,
   engineer only past-known lags/rolling features, and join planned promotions
   only when they were available at prediction time.
7. Train and compare models with chronological train/validation/test windows.
   Evaluate overall and by SKU demand profile, including 15-day forecast error.
   Approve a model version, then deploy it to Modal with its data cutoff and
   feature contract recorded. Training is a separate controlled activity; the
   daily workflow calls an approved deployed version.

## C. Daily forecasting and ordering workflow (n8n + Modal + PO engine)

8. **Preflight** checks that the latest DQ run passed and that sales,
   inventory, product, supplier, and scheduled promotion data are fresh enough
   for the intended run. Missing required inventory blocks real PO decisions.
9. **Call the deployed model** using an n8n HTTP Request node. For current
   sample testing, keep the existing Modal URL, POST method, and response shape.
   Request at least `as_of_date`; a future model should receive a versioned
   dataset reference or validated inputs, not raw ad hoc values. Its response
   must identify `run_id`, model version, data cutoff, SKU, forecast horizon,
   quantities, and whether inventory is simulated.
10. **Validate the response**: HTTP success, correct run/date/horizon,
    nonnegative finite demand, unique SKUs, expected SKU coverage, and clear
    data provenance. Fail closed on malformed or stale outputs.
11. **Inventory position and PO calculation** use a separate deterministic
    engine with real current QOH, open POs, lead time, safety stock, MOQ,
    carton rounding, and supplier constraints. Record each input and reason.
    The current Modal sample endpoint also returns PO fields, but those use
    simulated stock and are only a preview. Do not turn that preview into a
    supplier order or an official recommendation.
12. **Persist** validated forecasts and draft recommendations in PostgreSQL
    using run ID + SKU + forecast date as idempotency keys. Keep forecast,
    inventory snapshot, rule version, and decision linked for audit.
13. **Review and approval**: buyers inspect draft POs in the application.
    Submission to SmartRetail/ERP/supplier is a separate approved action;
    never wire the current testing branch directly to it.
14. **Failures** record the stage, run ID, and reason. Notify an operator only
    through a deliberately configured channel. Do not reuse the last successful
    result as if it were current.

The importable JSON accompanying this document implements a **manual test
version of steps 8–10 and a preview of step 11**. The Modal HTTP Request is
named `Call Current Modal Model`; replace its URL/credential/body mapping when
the new model is deployed, while keeping the forecast response contract. The
template is inactive and has no supplier submission or database write nodes.

## D. Application and AI workflow (backend + website; separate request path)

15. A backend API reads approved forecasts, inventory, and draft PO records
    from PostgreSQL. It authenticates users and exposes dashboard, product,
    forecast, recommendation, and approval endpoints. The website does not
    query raw SmartRetail credentials or n8n internal endpoints directly.
16. For chat, retrieve current numeric facts from the API/database by SKU and
    date. Retrieve explanatory documents (field meanings, coverage, business
    rules) with RAG when useful. The LLM explains the saved forecast and PO
    decision with source/run references; it does not calculate or overwrite
    the official order quantity.

## To connect the remaining nodes without guessing

Supply the SmartRetail endpoint names and sample responses, the n8n IDs of the
existing ingestion/DQ workflows, PostgreSQL schema or migration files, live
inventory source and freshness rule, supplier/PO rules, and the expected
backend storage contract. Then the placeholders above can be implemented as
actual n8n sub-workflows and database operations.
