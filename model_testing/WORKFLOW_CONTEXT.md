# Context: Pearcedale local model test in n8n

This is a **sample execution test** for the Pearcedale supermarket forecasting
prototype. It confirms that the existing Python pipeline runs locally, produces
forecasts and simulated purchase-order (PO) recommendations, sends them to n8n,
and passes basic output checks. It does **not** measure predictive accuracy or
create real purchase orders.

## Current flow

```text
Local Python model -> outbound HTTPS POST -> n8n test webhook
                                       -> Validate and Summarize
                                       -> Return Test Result
```

- The imported n8n workflow is **Pearcedale - Receive Local Model Test** at
  `https://n8n.srv1691210.hstgr.cloud/workflow/Vkl46u3JSTkadsgk`.
- Its webhook uses POST, path `pearcedale-model-results-test`, and Header Auth
  (`X-API-Key`). The dedicated n8n credential is named
  **Pearcedale Model Test Webhook**. Never include its value in shared context.
- The workflow is used through its **Test URL** while n8n is listening. It is
  not published as a production webhook.
- n8n returns HTTP 200 with `status: passed` after validation, or HTTP 422
  with validation errors. The response includes a summary and five sample
  forecast and recommendation rows. Full arrays enter the webhook node.

## Local files

- `model_testing/n8n_webhook_model_test.json`: importable n8n workflow.
- `model_testing/send_to_n8n.py`: runs the model and posts results. It reads
  the matching local key from `model_testing/runs/webhook-key.txt` and uses the
  configured n8n Test URL. The key file is ignored by version control.
- `model_testing/api.py`: provides the `run_sample()` function used by the
  sender. Its local HTTP server is not required for this webhook approach.
- `pearcedale-lgbm-model/model/run_forecasting.py`: existing model entry point.
  The test calls its `main()` with a chosen as-of date and redirects outputs to
  an isolated `model_testing/runs/<run_id>/` folder, preserving normal outputs.
- `model_testing/validate_webhook.js`: validation code embedded in the n8n
  Code node.
- `model_testing/WEBHOOK_SETUP.md`: setup and retry instructions.

## What the test validates

The webhook checks that the run has the expected type, run ID, as-of date,
15-day horizon, nonempty unique product IDs, finite nonnegative forecasts,
matching recommendation coverage, and nonnegative integer order quantities.
The local sender also checks model output before transmitting it.

The confirmed successful test used `--as-of 2026-09-16`, forecasting
2026-09-17 through 2026-10-01. n8n returned `status: passed` for **99 SKUs**.
The run reported 83 SKUs with an active-promotion label and about 4,399 total
forecast units. Its simulated PO decisions were 30 CREATE PO, 22 REVIEW
REQUIRED, and 47 NO PO REQUIRED. Run ID:
`2f8a0f1369b14f7ea4c0c183194246ee`.

The observed result is saved locally at
`model_testing/runs/2f8a0f1369b14f7ea4c0c183194246ee/n8n_response.json`.
The n8n editor also showed “Workflow executed successfully.”

## How to repeat the test

From the project root in PowerShell:

```powershell
python model_testing/send_to_n8n.py --as-of 2026-09-16
```

Wait until the model prepares its payload. Click **Execute workflow** in n8n
so the Test URL is listening, then press Enter in PowerShell. The script sends
the payload and prints n8n's validation result. To retry a prepared run, use
`--payload model_testing/runs/<run_id>/payload.json` after starting the listener.
The terminal environment used by Codex required network escalation for the
outbound POST; a normal user PowerShell may not.

## Limits and next work

- Inventory in this prototype is **simulated**, so PO quantities and stockout
  risks are demonstrations and must not be used to place orders.
- The model currently approximates a 15-day total from two weekly forecasts.
  It does not return 15 separate daily forecasts.
- Existing stockout dates use the computer clock rather than the test as-of date.
- The reported 83 promotion labels come from the current model inputs; they do
  not prove that 83 meaningful discounts are active. Promotion classification
  and coverage need review before operational use.
- This workflow does not ingest SmartRetail data, retrain models, evaluate
  accuracy against actual sales, store results in PostgreSQL, or run RAG/chat.
- Next meaningful validation is a chronological 15-day backtest against actual
  sales, followed by integration of real inventory and finalized PO rules.
