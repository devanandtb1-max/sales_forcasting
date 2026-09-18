# Sample model test from remote n8n

Workflow: Manual Trigger -> Test Settings -> Run Sample Forecast (HTTP) -> Validate and Summarize.

This uses the existing trained LightGBM baseline and promotion model, prepared feature files,
and promotion data. It does not ingest new SmartRetail data or retrain the models.
It is an execution smoke test, not an accuracy backtest. PO inventory is simulated.

## 1. Start the Python bridge on the model computer

In PowerShell, from the project root:

```powershell
cd 'C:\Users\devanand\Downloads\edt\product_sales_forcast'
$env:MODEL_TEST_API_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
python model_testing/api.py
```

Use the Python environment containing lightgbm, polars, numpy, pandas, and pyarrow.
The bridge itself uses only Python's standard library. It binds to localhost port 8000.
Keep this terminal running. Ctrl+C stops it. The key exists only in this terminal's environment.
To copy the key without printing it, stop the server temporarily, run
`Set-Clipboard -Value $env:MODEL_TEST_API_KEY`, then start the server again in the same terminal.
Save this key in n8n's credential manager, not in workflow code or chat.

## 2. Make the bridge reachable by your remote n8n

Route an HTTPS tunnel to `http://127.0.0.1:8000` on this computer.
Use your existing tunnel setup if available. With cloudflared already installed,
a temporary test tunnel can be started in a second terminal:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

The command prints a temporary HTTPS address. Append `/test-forecast` for the n8n URL.
This exposes the API through the tunnel; requests must still include your API key.
The address can change when the tunnel restarts. Keep both terminals and the computer running.
No tunnel is created automatically by these files. Use this lightweight bridge for supervised
sample testing only; it is not a production API server.

If n8n is on a server with a private network connection to your model host, a private
reverse-proxy route is another option. Remote n8n cannot reach your PC using `localhost`.

## 3. Import and configure n8n

1. Import `n8n_sample_test.json` into n8n using Import from File.
2. Open **Test Settings** and replace the placeholder `api_url` with your HTTPS URL.
3. Keep `as_of_date: '2026-09-16'` for the initial sample run, or supply a valid date.
   Changing this date does not refresh source data or make a historical run leakage-free.
4. In **Run Sample Forecast**, create/select a **Header Auth** credential:
   - Name: `X-API-Key`
   - Value: the secret from the Python terminal.
5. Execute the workflow manually. It remains inactive and has no schedule.

The API runs the model once per request, with a 90-second limit. HTTP errors stop the
workflow; retries are deliberately off. A second simultaneous request receives HTTP 409.
The HTTP node waits up to 110 seconds, though a tunnel/proxy can impose a shorter limit.
If a request times out, check the local logs before manually trying again.

## 4. Inspect the results

**Validate and Summarize** shows dates, SKU count, summed forecast quantity, sample rows,
and sample PO counts. **Run Sample Forecast** contains the full returned forecast and PO arrays.
Each run saves CSV, Parquet, summary JSON, and `run.log` under `model_testing/runs/<run_id>/`.
Existing model outputs remain untouched. Most CSV fields are returned as strings; forecast
quantities and recommended order quantities are explicitly converted to JSON numbers.

Checks cover nonempty/unique products, count consistency, finite nonnegative forecasts,
correct horizon dates, recommendation coverage, and nonnegative integer order quantities.
Passing these checks does not certify forecast accuracy or correctness of business rules.

Known model limitations remain: two weekly forecasts approximate the 15-day total;
inventory is simulated; stockout dates use the system clock rather than `as_of_date`.
The existing `evaluation_report.json` is a run summary, not an accuracy evaluation.
No real POs are submitted. Run artifacts accumulate locally and can be removed when no longer needed.

## Troubleshooting

- 401: check the Header Auth name and key; restarting in a new terminal requires the same key or a credential update.
- 400: send exactly `{"as_of_date":"2026-09-16"}` as JSON.
- 409: let the active test finish.
- 500: inspect the API terminal and the latest local run log for missing dependencies/files or model errors.
- 504: the model exceeded 90 seconds.
- Connection failure: confirm the Python server and tunnel are running and the URL is current.

References: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/
