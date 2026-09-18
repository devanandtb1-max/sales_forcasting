"""Deploy the Pearcedale forecasting model as a Modal web endpoint.

Setup (one-time):
    pip install modal
    modal token new          # authenticates via browser

Deploy:
    cd product_sales_forcast
    modal deploy model_testing/modal_deploy.py

Test (ephemeral, auto-reloads on code changes):
    modal serve model_testing/modal_deploy.py

The endpoint URL is printed after deployment.  Pass it to the n8n
HTTP Request node along with the bearer token defined below.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import modal

# ── Auth ───────────────────────────────────────────────────────────────────────
# On first deploy, Modal stores this as part of the function definition.
# Override via:  modal deploy ... --env MODAL_FORECAST_TOKEN=<your-secret>
# Or set it in Modal's dashboard under Secrets.
DEFAULT_TOKEN = secrets.token_urlsafe(32)

# ── Container image ────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # product_sales_forcast/
MODEL_DIR = PROJECT_ROOT / "pearcedale-lgbm-model"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("lightgbm~=4.5", "polars~=1.8", "pyarrow", "pandas", "numpy~=2.1", "fastapi[standard]")
    # Bundle the full model directory into the container.
    # add_local_dir mounts at runtime by default (fast iteration).
    .add_local_dir(
        str(MODEL_DIR / "model" / "artifacts"),
        "/app/model/artifacts",
    )
    .add_local_dir(
        str(MODEL_DIR / "model" / "features"),
        "/app/model/features",
    )
    .add_local_dir(
        str(MODEL_DIR / "data"),
        "/app/data",
    )
    .add_local_dir(
        str(MODEL_DIR / "processed_output"),
        "/app/processed_output",
    )
    .add_local_file(
        str(MODEL_DIR / "model" / "run_forecasting.py"),
        "/app/model/run_forecasting.py",
    )
)

app = modal.App(name="pearcedale-forecast", image=image)


# ── Endpoint ───────────────────────────────────────────────────────────────────
@app.function(
    timeout=120,
    # Use Modal Secrets to store the token securely.  Create one named
    # "forecast-auth" in the Modal dashboard with key MODAL_FORECAST_TOKEN.
    # If it doesn't exist yet, the fallback DEFAULT_TOKEN printed at deploy
    # time is used — save it for n8n.
    secrets=[modal.Secret.from_name("forecast-auth")],
)
@modal.fastapi_endpoint(method="POST", docs=True)
def forecast(request_body: dict):
    """Run the LightGBM forecasting pipeline and return structured results.

    Expected JSON body:
        {"as_of_date": "YYYY-MM-DD"}

    Returns the same payload format as api.run_sample():
        forecasts[], recommendations[], summary{}, warnings[]
    """
    import sys
    from datetime import date, timedelta

    # ── Auth check ─────────────────────────────────────────────────────────
    # NOTE: FastAPI endpoint receives the parsed body directly.
    # For header-based auth, use the raw request via fastapi.Request.
    # Here we use a simpler body-level token for n8n compatibility.
    expected_token = os.environ.get("MODAL_FORECAST_TOKEN", DEFAULT_TOKEN)
    supplied_token = request_body.get("token", "")
    if not secrets.compare_digest(supplied_token, expected_token):
        return {"status": "error", "error": "Invalid or missing token"}

    # ── Parse input ────────────────────────────────────────────────────────
    raw_date = request_body.get("as_of_date")
    if raw_date is None:
        raw_date = date.today().isoformat()
    try:
        as_of = date.fromisoformat(raw_date)
        if as_of.isoformat() != raw_date:
            raise ValueError("Non-canonical date")
    except (ValueError, TypeError):
        return {"status": "error", "error": f"Invalid as_of_date: {raw_date!r}. Use YYYY-MM-DD."}

    # ── Run the model ──────────────────────────────────────────────────────
    # Change to the model directory so relative paths in run_forecasting.py
    # resolve correctly (features/, artifacts/, ../data/, ../processed_output/).
    os.chdir("/app/model")
    sys.path.insert(0, "/app/model")

    import csv
    import math
    import uuid

    # Import the model (now that sys.path and cwd are set).
    import run_forecasting as model

    run_id = uuid.uuid4().hex
    output_dir = Path(f"/tmp/runs/{run_id}")
    model.OUTPUT_DIR = output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        model.main(as_of)
    except Exception as exc:
        return {
            "status": "error",
            "error": f"Model execution failed: {exc}",
            "run_id": run_id,
        }

    # ── Build response (same structure as api.run_sample) ──────────────────
    try:
        report = json.loads((output_dir / "evaluation_report.json").read_text())

        def read_csv(name):
            with (output_dir / name).open(encoding="utf-8", newline="") as f:
                return list(csv.DictReader(f))

        forecasts = read_csv("hybrid_predictions_15day.csv")
        recommendations = read_csv("po_recommendations.csv")

        start = str(as_of + timedelta(days=1))
        end = str(as_of + timedelta(days=15))
        ids = [row["itemCode"] for row in forecasts]

        if not ids or len(ids) != len(set(ids)) or len(ids) != report["total_skus"]:
            return {"status": "error", "error": "Inconsistent forecast output", "run_id": run_id}

        for row in forecasts:
            for field in ("baseline_forecast_15d", "hybrid_forecast_15d"):
                row[field] = float(row[field])
                if not math.isfinite(row[field]) or row[field] < 0:
                    return {"status": "error", "error": f"Invalid {field}", "run_id": run_id}
            if row["horizon_start"] != start or row["horizon_end"] != end:
                return {"status": "error", "error": "Wrong forecast horizon", "run_id": run_id}

        po_ids = [row["itemCode"] for row in recommendations]
        if len(po_ids) != len(ids) or set(po_ids) != set(ids):
            return {"status": "error", "error": "Recommendation coverage mismatch", "run_id": run_id}

        for row in recommendations:
            row["recommended_order_qty"] = int(row["recommended_order_qty"])
            if row["recommended_order_qty"] < 0:
                return {"status": "error", "error": "Negative recommendation", "run_id": run_id}

        report.pop("outputs", None)
        return {
            "status": "passed",
            "run_id": run_id,
            "test_type": "pipeline_smoke_test",
            "inventory_is_simulated": True,
            "summary": report,
            "warnings": [
                "PO quantities use simulated inventory; do not place orders from this test.",
                "This run does not measure forecast accuracy or retrain models.",
                "Existing model approximates 15-day totals from two weekly forecasts.",
                "Existing stockout dates use the computer clock, not the supplied as-of date.",
            ],
            "forecasts": forecasts,
            "recommendations": recommendations,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": f"Output parsing failed: {exc}",
            "run_id": run_id,
        }


# Print the default token at import time so the deployer can save it.
if __name__ == "__main__":
    print(f"\nDefault bearer token (save this for n8n):\n  {DEFAULT_TOKEN}\n")
    print("Run:  modal deploy model_testing/modal_deploy.py")
    print("Test: modal serve model_testing/modal_deploy.py\n")
