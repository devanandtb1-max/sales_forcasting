# Replacement: local model to n8n webhook

Import `n8n_webhook_model_test.json` as a new workflow. Keep the previous HTTP-request
workflow inactive. No local API server, Cloudflare tunnel, or VPS access is needed.
This is outbound HTTPS to your existing n8n server, not an SSH/VPN private network.

Flow: Receive Model Results -> Validate and Summarize -> Return Test Result.

1. Open **Receive Model Results**. Authentication is **Header Auth**.
   Create a credential with header name `X-API-Key` and a random secret value.
   To generate a new secret and copy it without printing it, use PowerShell:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))" | Set-Clipboard
   ```

   Paste it into the credential value and save it. Save the same value in
   `model_testing/runs/webhook-key.txt` on your computer; do not paste it into chat.
   Your n8n login password
   is not the webhook key.

2. The sender is configured for this webhook's **Test URL**. Leave the workflow unpublished.

3. From PowerShell run:

   ```powershell
   cd 'C:\Users\devanand\Downloads\edt\product_sales_forcast'
   python model_testing/send_to_n8n.py --as-of 2026-09-16
   ```

4. After preparing the model results, the script pauses. It reads the Test URL and
   the saved local key automatically. In n8n click **Execute workflow** to start listening
   and run the entire chain, then press Enter in PowerShell to send.
   The webhook also offers **Listen for test event**; use a full workflow execution
   for checking the validation and response nodes together.

5. The terminal should report `n8n validation PASSED` with the product count and run ID.
   The validation node shows a summary and five sample rows. Full forecasts and
   recommendations are in the webhook's input body and the local `payload.json`.

The script accepts only HTTPS test webhook URLs on `n8n.srv1691210.hstgr.cloud`,
does not follow redirects, and sends the key in a header. It saves the payload and
n8n response under `model_testing/runs/<run_id>/`. No external request occurs until
you press Enter at the final prompt. If the Test URL changes, use `--url`.
Results will be visible/stored according to your n8n execution
retention settings. No orders are placed.

For a retry, restart the listener and reuse the saved payload without rerunning the model:

```powershell
python model_testing/send_to_n8n.py --payload 'model_testing/runs/YOUR_RUN_ID/payload.json'
```

401/403 means check authentication; 404 usually means the listener expired or the URL
is wrong. 422 means validation rejected the input; inspect the returned errors. After a
network timeout, inspect n8n executions before retrying because it may already have received
the request. The sender does not automatically retry.

This verifies model execution and data transfer, not predictive accuracy. Inventory remains
simulated and the existing 15-day forecast approximation is unchanged.

Reference: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/
