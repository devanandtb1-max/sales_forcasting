"""Chronos-2 zero-shot forecasting experiment with 15-day holdout evaluation.

Reads the existing clean_daily_sales.parquet produced by the LightGBM pipeline's
ingest_json.py, performs a strict chronological 15-day holdout, runs batched
zero-shot inference with amazon/chronos-2, and evaluates WAPE / MAE / Bias
both globally and per-SKU.

Data-quality note
-----------------
The upstream pipeline deliberately keeps quantity=NaN for days where sales were
not observed (e.g. the item was not yet listed, or the POS record is missing).
NaN != "zero demand".  This script preserves that distinction:

  - Context fed to Chronos: NaN values are kept as-is in the tensor (converted
    to float NaN).  Chronos-2 is trained to handle NaN / missing observations
    natively via its attention mask - it skips those positions.

  - Holdout evaluation: only days where the actual quantity is *not* NaN count
    towards the error metrics.  A NaN actual means "unknown demand" and
    including it as zero would artificially deflate error.

Usage:
    python forecast_and_evaluate.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

# ── Paths ──────────────────────────────────────────────────────────────────────
CLEAN_SALES = Path(__file__).resolve().parent.parent / "pearcedale-lgbm-model" / "processed_output" / "clean_daily_sales.parquet"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"

MODEL_ID = "amazon/chronos-2"
PREDICTION_LENGTH = 15
HOLDOUT_DAYS = 15
BATCH_SIZE = 25            # SKUs per batch (avoids OOM on CPU)


# ── 1. Load and prepare ───────────────────────────────────────────────────────
def load_data() -> pd.DataFrame:
    """Load the DQ-layer parquet.  Parse dates, keep NaN quantity as-is."""
    print(f"Loading data from {CLEAN_SALES} ...")
    df = pd.read_parquet(CLEAN_SALES)
    df["trading_date"] = pd.to_datetime(df["trading_date"])
    print(f"  {df.shape[0]:,} rows  |  {df['itemCode'].nunique()} SKUs  |  "
          f"{df['trading_date'].min().date()} -> {df['trading_date'].max().date()}")

    # Report null-quantity prevalence so the user can audit
    n_null = df["quantity"].isna().sum()
    print(f"  quantity NaN rows: {n_null:,} / {df.shape[0]:,} "
          f"({n_null / df.shape[0] * 100:.1f}%) - preserved as unknown demand")
    return df


# ── 2. Chronological split ────────────────────────────────────────────────────
def split_holdout(df: pd.DataFrame):
    """Strict chronological split.  Last HOLDOUT_DAYS days are the test set."""
    max_date = df["trading_date"].max()
    cutoff = max_date - pd.Timedelta(days=HOLDOUT_DAYS - 1)  # inclusive cutoff

    context_df = df[df["trading_date"] < cutoff].copy()
    holdout_df = df[df["trading_date"] >= cutoff].copy()

    print(f"\nChronological split:")
    print(f"  Context:  ... -> {(cutoff - pd.Timedelta(days=1)).date()}  "
          f"({context_df.shape[0]:,} rows)")
    print(f"  Holdout:  {cutoff.date()} -> {max_date.date()}  "
          f"({holdout_df.shape[0]:,} rows, {HOLDOUT_DAYS} days)")
    return context_df, holdout_df, cutoff, max_date


# ── 3. Build per-SKU tensors ─────────────────────────────────────────────────
def build_context_tensors(context_df: pd.DataFrame, skus: list[str]):
    """Return a list of 1-D float32 tensors, one per SKU.

    NaN values are kept - Chronos-2 masks them out via its attention mechanism.
    """
    tensors = []
    for sku in skus:
        series = (
            context_df[context_df["itemCode"] == sku]
            .sort_values("trading_date")["quantity"]
            .values
            .astype(np.float32)
        )
        tensors.append(torch.tensor(series))
    return tensors


# ── 4. Batched inference ─────────────────────────────────────────────────────
def run_chronos_inference(tensors: list[torch.Tensor]):
    """Load the model once and run batched predict.

    Returns an array of shape (n_skus, PREDICTION_LENGTH) with the median
    (50th percentile) point forecast.
    """
    from chronos import Chronos2Pipeline

    print(f"\nLoading Chronos-2 pipeline ({MODEL_ID}) ...")
    t0 = time.time()
    pipeline = Chronos2Pipeline.from_pretrained(MODEL_ID, device_map="cpu")
    print(f"  Model loaded in {time.time() - t0:.1f}s")

    n = len(tensors)
    all_medians = []

    print(f"Running batched inference  ({n} SKUs, batch_size={BATCH_SIZE}, "
          f"prediction_length={PREDICTION_LENGTH}) ...")
    t0 = time.time()

    for start in range(0, n, BATCH_SIZE):
        end = min(start + BATCH_SIZE, n)
        batch = tensors[start:end]
        
        # predict returns a list of tensors, one per series in the batch
        # Each tensor has shape (1, num_quantiles, prediction_length)
        # For Chronos-2, num_quantiles is typically 21, with index 10 being the median (0.5)
        forecasts = pipeline.predict(
            inputs=batch,
            prediction_length=PREDICTION_LENGTH,
        )
        
        for forecast in forecasts:
            # Extract median (index 10) from shape (1, 21, PREDICTION_LENGTH)
            median_idx = forecast.shape[1] // 2
            median = forecast[0, median_idx, :].numpy()
            all_medians.append(median)
            
        print(f"  Batch {start // BATCH_SIZE + 1}/"
              f"{(n + BATCH_SIZE - 1) // BATCH_SIZE}  "
              f"({end - start} SKUs)  done")

    elapsed = time.time() - t0
    print(f"  Inference complete in {elapsed:.1f}s  "
          f"({elapsed / n:.2f}s per SKU)")

    return np.vstack(all_medians)   # (n_skus, PREDICTION_LENGTH)


# ── 5. Evaluate ──────────────────────────────────────────────────────────────
def evaluate(
    skus: list[str],
    medians: np.ndarray,
    holdout_df: pd.DataFrame,
    cutoff: pd.Timestamp,
):
    """Compute WAPE, MAE, Bias overall and per-SKU.

    Only observed (non-NaN) actuals participate in the metrics.
    """
    # Build a date index for the holdout window
    dates = pd.date_range(cutoff, periods=PREDICTION_LENGTH, freq="D")

    per_sku_records = []
    all_actual = []
    all_pred = []

    for i, sku in enumerate(skus):
        sku_holdout = holdout_df[holdout_df["itemCode"] == sku].set_index("trading_date")

        actual_vals = []
        pred_vals = []

        for d, day_idx in zip(dates, range(PREDICTION_LENGTH)):
            if d in sku_holdout.index:
                qty = sku_holdout.loc[d, "quantity"]
                # qty may be a Series if duplicate dates exist (shouldn't, but be safe)
                if isinstance(qty, pd.Series):
                    qty = qty.iloc[0]
                if pd.notna(qty):
                    actual_vals.append(float(qty))
                    pred_vals.append(float(medians[i, day_idx]))

        actual_arr = np.array(actual_vals)
        pred_arr = np.array(pred_vals)
        # Clip predictions to >= 0 (demand can't be negative)
        pred_arr = np.clip(pred_arr, 0.0, None)

        n_obs = len(actual_arr)

        if n_obs == 0:
            per_sku_records.append({
                "itemCode": sku,
                "observed_days": 0,
                "total_actual": np.nan,
                "total_forecast": np.nan,
                "mae": np.nan,
                "wape": np.nan,
                "bias": np.nan,
                "note": "no observed actuals in holdout",
            })
            continue

        total_actual = actual_arr.sum()
        total_pred = pred_arr.sum()
        abs_errors = np.abs(actual_arr - pred_arr)
        mae = abs_errors.mean()

        if total_actual > 0:
            wape = abs_errors.sum() / total_actual
            bias = (pred_arr.sum() - total_actual) / total_actual
        else:
            wape = np.nan
            bias = np.nan

        per_sku_records.append({
            "itemCode": sku,
            "observed_days": n_obs,
            "total_actual": round(total_actual, 2),
            "total_forecast": round(total_pred, 2),
            "mae": round(mae, 4),
            "wape": round(wape, 4) if not np.isnan(wape) else None,
            "bias": round(bias, 4) if not np.isnan(bias) else None,
        })

        all_actual.extend(actual_vals)
        all_pred.extend(pred_arr.tolist())

    # ── Global metrics ─────────────────────────────────────────────────────
    all_actual = np.array(all_actual)
    all_pred = np.array(all_pred)
    global_abs_errors = np.abs(all_actual - all_pred)

    global_mae = global_abs_errors.mean()
    total_actual_global = all_actual.sum()
    if total_actual_global > 0:
        global_wape = global_abs_errors.sum() / total_actual_global
        global_bias = (all_pred.sum() - total_actual_global) / total_actual_global
    else:
        global_wape = np.nan
        global_bias = np.nan

    global_metrics = {
        "total_skus": len(skus),
        "total_observation_pairs": len(all_actual),
        "global_mae": round(float(global_mae), 4),
        "global_wape": round(float(global_wape), 4),
        "global_bias": round(float(global_bias), 4),
    }

    return per_sku_records, global_metrics


# ── 6. Save outputs ─────────────────────────────────────────────────────────
def save_outputs(
    skus: list[str],
    medians: np.ndarray,
    cutoff: pd.Timestamp,
    per_sku_records: list[dict],
    global_metrics: dict,
):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # (a) Raw predictions - one row per SKU x day
    dates = pd.date_range(cutoff, periods=PREDICTION_LENGTH, freq="D")
    rows = []
    for i, sku in enumerate(skus):
        for d_idx, d in enumerate(dates):
            rows.append({
                "itemCode": sku,
                "forecast_date": d.date().isoformat(),
                "day_offset": d_idx + 1,
                "chronos2_median_forecast": round(max(0.0, float(medians[i, d_idx])), 4),
            })
    pred_df = pd.DataFrame(rows)
    pred_path = OUTPUT_DIR / "chronos2_predictions_15day.csv"
    pred_df.to_csv(pred_path, index=False)
    print(f"\n  Predictions saved -> {pred_path}")

    # (b) Per-SKU metrics
    sku_df = pd.DataFrame(per_sku_records)
    sku_path = OUTPUT_DIR / "chronos2_per_sku_metrics.csv"
    sku_df.to_csv(sku_path, index=False)
    print(f"  Per-SKU metrics  -> {sku_path}")

    # (c) Summary report (JSON)
    report = {
        "model": MODEL_ID,
        "prediction_length_days": PREDICTION_LENGTH,
        "holdout_days": HOLDOUT_DAYS,
        "point_forecast": "median (50th percentile) derived from default 21 quantiles",
        "null_handling": (
            "NaN quantity = unknown demand; kept as NaN in Chronos context "
            "(model masks via attention). Holdout evaluation excludes NaN actuals."
        ),
        "global_metrics": global_metrics,
    }
    report_path = OUTPUT_DIR / "chronos2_evaluation_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"  Summary report   -> {report_path}")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("  Chronos-2 - Zero-Shot Forecasting Experiment")
    print("=" * 65)

    df = load_data()
    context_df, holdout_df, cutoff, max_date = split_holdout(df)

    skus = sorted(df["itemCode"].unique().tolist())
    print(f"\nUsing {len(skus)} SKUs for evaluation.")

    # Build context tensors (NaN preserved)
    tensors = build_context_tensors(context_df, skus)
    lens = [len(t) for t in tensors]
    print(f"  Context lengths: min={min(lens)}, max={max(lens)}, "
          f"median={int(np.median(lens))}")

    # Inference
    medians = run_chronos_inference(tensors)

    # Evaluate
    print("\nEvaluating against holdout ...")
    per_sku_records, global_metrics = evaluate(skus, medians, holdout_df, cutoff)

    # Report
    print("\n" + "=" * 65)
    print("  GLOBAL METRICS")
    print("=" * 65)
    print(f"  MAE  : {global_metrics['global_mae']:.4f}")
    print(f"  WAPE : {global_metrics['global_wape']:.4f}  "
          f"({global_metrics['global_wape'] * 100:.1f}%)")
    bias_val = global_metrics['global_bias']
    direction = "over-forecast" if bias_val > 0 else "under-forecast"
    print(f"  Bias : {bias_val:+.4f}  ({abs(bias_val) * 100:.1f}% {direction})")
    print(f"  SKUs evaluated: {global_metrics['total_skus']}")
    print(f"  Observation pairs: {global_metrics['total_observation_pairs']}")
    print("=" * 65)

    # Save
    save_outputs(skus, medians, cutoff, per_sku_records, global_metrics)

    print("\nDone")


if __name__ == "__main__":
    main()
