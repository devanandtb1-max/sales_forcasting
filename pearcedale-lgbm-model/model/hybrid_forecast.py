"""Evaluate baseline vs. hybrid (baseline + promotion-uplift) on the holdout.

Reads the walk-forward baseline OOS predictions, aggregates the daily promo
table (``promotions_daily.parquet``) to ISO weeks, predicts uplift using the
trained promotion model, and computes WMAPE for both baseline and hybrid on
the 13-week holdout.

Usage:
    python hybrid_forecast.py
"""
import json
from pathlib import Path
import polars as pl
import lightgbm as lgb
import numpy as np

OOS_BASELINE = Path("../processed_output/baseline_oos_predictions.parquet")
PROMO_DAILY = Path("../processed_output/promotions_daily.parquet")
PROMO_MODEL = Path("artifacts/lgbm_promotion.txt")
REPORT_DIR = Path("reports")
OUTPUT_PREDS = Path("../processed_output/hybrid_predictions_holdout.parquet")


def wmape(actual, pred):
    denom = np.abs(actual).sum()
    if denom == 0:
        return float("nan")
    return float(np.abs(actual - pred).sum() / denom)


def build_promo_weekly() -> pl.DataFrame:
    """Aggregate daily promo table to one row per (itemCode, iso_week)."""
    daily = pl.read_parquet(PROMO_DAILY)
    daily = daily.with_columns(
        pl.col("trading_date").dt.strftime("%G-%V").alias("iso_week"),
    )

    weekly = daily.group_by(["itemCode", "iso_week"]).agg(
        pl.col("promo_active").sum().alias("overlap_days"),
        pl.col("discount_pct").filter(pl.col("promo_active")).max().alias("discount_pct"),
        pl.col("promo_type_group").filter(pl.col("promo_active")).mode().first().alias("promo_type_group"),
    )

    weekly = weekly.with_columns(
        pl.col("discount_pct").fill_null(0.0),
        pl.col("promo_type_group").fill_null("NONE"),
    )

    return weekly


def evaluate_hybrid():
    print("Loading baseline OOS predictions...")
    oos = pl.read_parquet(OOS_BASELINE)

    max_week = oos.select(pl.col("week_start").max()).item()
    holdout_start = max_week - np.timedelta64(13 * 7, "D")

    holdout = oos.filter(pl.col("week_start") >= holdout_start)
    print(f"Holdout period: {holdout_start} to {max_week}. Rows: {holdout.height}")

    # Calculate Baseline WMAPE
    y_hold = holdout["qty_sum"].to_numpy()
    pred_base = holdout["baseline_forecast"].to_numpy()
    base_wmape = wmape(y_hold, pred_base)
    print(f"Baseline Holdout WMAPE: {base_wmape:.3%}")

    # Build weekly promo features and join
    holdout = holdout.with_columns(
        pl.col("week_start").dt.strftime("%G-%V").alias("iso_week"),
    )

    promo_weekly = build_promo_weekly()

    weekly_promo = holdout.join(
        promo_weekly, on=["itemCode", "iso_week"], how="left"
    )

    weekly_promo = weekly_promo.with_columns(
        pl.col("overlap_days").fill_null(0),
        pl.col("discount_pct").fill_null(0.0),
        pl.col("promo_type_group").fill_null("NONE"),
    )

    features = ["discount_pct", "overlap_days", "promo_type_group"]
    pdf = weekly_promo.to_pandas()
    pdf["promo_type_group"] = pdf["promo_type_group"].astype("category")
    X = pdf[features]

    if PROMO_MODEL.exists():
        model = lgb.Booster(model_file=str(PROMO_MODEL))
        predicted_uplift = model.predict(X)
    else:
        print("Warning: Promotion model not found. Assuming 0 uplift.")
        predicted_uplift = np.zeros(len(X))

    weekly_promo = weekly_promo.with_columns(
        pl.Series("predicted_uplift", predicted_uplift)
    )

    # Apply hybrid formula: ONLY if overlap_days > 0
    weekly_promo = weekly_promo.with_columns(
        pl.when(pl.col("overlap_days") > 0).then(
            pl.col("baseline_forecast") * (1.0 + pl.col("predicted_uplift"))
        ).otherwise(pl.col("baseline_forecast")).alias("hybrid_forecast")
    )

    # Clip just in case
    weekly_promo = weekly_promo.with_columns(
        pl.col("hybrid_forecast").clip(lower_bound=0.0)
    )

    # Calculate Hybrid WMAPE
    y_actual = weekly_promo["qty_sum"].to_numpy()
    pred_hybrid = weekly_promo["hybrid_forecast"].to_numpy()
    hybrid_wmape = wmape(y_actual, pred_hybrid)

    print(f"Hybrid Holdout WMAPE: {hybrid_wmape:.3%}")
    print(f"Improvement: {base_wmape - hybrid_wmape:.3%} percentage points")

    # Promoted-SKU WMAPE
    promo_mask = weekly_promo["overlap_days"].to_numpy() > 0
    if promo_mask.sum() > 0:
        promo_wmape_base = wmape(y_actual[promo_mask], pred_base[promo_mask])
        promo_wmape_hybrid = wmape(y_actual[promo_mask], pred_hybrid[promo_mask])
        print(f"Promoted-SKU Baseline WMAPE: {promo_wmape_base:.3%}")
        print(f"Promoted-SKU Hybrid WMAPE: {promo_wmape_hybrid:.3%}")
    else:
        promo_wmape_base = None
        promo_wmape_hybrid = None

    # Non-promoted WMAPE
    non_promo_mask = ~promo_mask
    if non_promo_mask.sum() > 0:
        non_promo_wmape = wmape(y_actual[non_promo_mask], pred_hybrid[non_promo_mask])
        print(f"Non-promoted WMAPE: {non_promo_wmape:.3%}")
    else:
        non_promo_wmape = None

    weekly_promo.write_parquet(OUTPUT_PREDS)

    report = {
        "holdout_start": str(holdout_start),
        "holdout_end": str(max_week),
        "n_rows": holdout.height,
        "baseline_wmape": base_wmape,
        "hybrid_wmape": hybrid_wmape,
        "improvement_abs": base_wmape - hybrid_wmape,
        "is_hybrid_better": hybrid_wmape < base_wmape,
        "promoted_sku_baseline_wmape": promo_wmape_base,
        "promoted_sku_hybrid_wmape": promo_wmape_hybrid,
        "non_promoted_wmape": non_promo_wmape,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "hybrid_evaluation.json").write_text(json.dumps(report, indent=2))
    print("Wrote hybrid_evaluation.json")


if __name__ == "__main__":
    evaluate_hybrid()

