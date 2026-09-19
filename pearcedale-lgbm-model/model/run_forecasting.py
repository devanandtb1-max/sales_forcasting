"""run_forecasting.py — Single entry point for n8n.

Workflow:
  1. Load trained LightGBM baseline model
  2. Load trained promotion-effect model
  3. Build 15-day baseline forecast for all SKUs (two ISO weeks covering today+15)
  4. Merge known future promotions from processed_output/promotions_daily.parquet
  5. Apply hybrid uplift where a confirmed future promotion overlaps the horizon
  6. Feed hybrid demand into the existing PO logic (stockout, MOQ, carton rounding)
  7. Write all outputs to outputs/

Usage (n8n):
    python run_forecasting.py
    python run_forecasting.py --as-of 2026-09-10   # for backtesting
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl

# ── Paths ──────────────────────────────────────────────────────────────────────
FEATURES_PATH    = Path("features/weekly_features.parquet")
PROMO_DAILY_PATH = Path("../processed_output/promotions_daily.parquet")
HOLIDAYS_CSV     = Path("../data/vic_public_holidays.csv")
SCHOOL_TERMS_CSV = Path("../data/vic_school_terms.csv")
MODEL_UNITS      = Path("artifacts/lgbm_units.txt")
MODEL_PROMO      = Path("artifacts/lgbm_promotion.txt")
OUTPUT_DIR       = Path("../outputs")

CATEGORICAL = ["itemCode", "departmentName", "commodityName", "primarySupplier", "tier",
               "promo_status_week", "promo_type_group_week"]
FEATURE_COLS = (
    [f"lag_{w}" for w in (1, 2, 3, 4, 8, 13, 52)]
    + [f"roll_mean_{w}" for w in (4, 8, 13)]
    + [f"roll_std_{w}" for w in (4, 8, 13)]
    + ["woy_sin", "woy_cos", "cal_month", "cal_quarter",
       "prev_week_completeness", "prev_asp_mean",
       "cartonQty", "minOrderQty"]
    + ["promo_days_in_week", "promo_discount_pct_max", "promo_discount_pct_mean",
       "promo_stacked_max", "price_ratio", "promo_pre_week", "promo_post_week1",
       "promo_post_week2", "has_public_holiday", "is_school_term"]
    + CATEGORICAL
)
PROMO_FEATURE_COLS = [
    "promo_status_week", "promo_type_group_week", "promo_days_in_week",
    "promo_discount_pct_max", "promo_discount_pct_mean", "promo_stacked_max",
    "price_ratio", "promo_pre_week", "promo_post_week1", "promo_post_week2",
]
CALENDAR_FEATURE_COLS = ["woy_sin", "woy_cos", "cal_month", "cal_quarter",
                          "has_public_holiday", "is_school_term"]

# Safety cap: never multiply baseline by more than 3x due to promotion
MAX_UPLIFT = 2.0
# Minimum uplift to apply (ignore noise below this)
MIN_UPLIFT_THRESHOLD = 0.01


# ── Step 1: Baseline Forecast ──────────────────────────────────────────────────
def iso_week_monday(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def build_future_promo_weekly() -> pl.DataFrame:
    """Same weekly promo aggregation as preprocess_features.py's
    build_promo_weekly()/add_promo_features(), applied to
    promotions_daily.parquet - which now covers known future promotions too
    (ingest_promotions.py's scaffold extends to the latest scheduled promo
    end date, not just historical sales dates). This lets the two ISO weeks
    covering the forecast horizon get their *own* real promo features
    instead of reusing whatever was true last week.
    """
    daily = pl.read_parquet(PROMO_DAILY_PATH)
    daily = daily.with_columns(pl.col("trading_date").dt.strftime("%G-%V").alias("iso_week"))
    effective_price = pl.when(pl.col("promo_active")).then(pl.col("promo_price")).otherwise(pl.col("regular_price"))

    weekly = daily.group_by(["itemCode", "iso_week"]).agg(
        pl.col("trading_date").min().alias("week_start"),
        pl.len().alias("_n_days"),
        (pl.col("promo_status") == "UNKNOWN").sum().alias("_n_unknown"),
        (pl.col("promo_status") == "CONFIRMED").sum().alias("_n_confirmed"),
        pl.col("promo_active").sum().alias("promo_days_in_week"),
        pl.col("discount_pct").max().alias("promo_discount_pct_max"),
        pl.col("discount_pct").mean().alias("promo_discount_pct_mean"),
        pl.col("n_stacked_promos").max().alias("promo_stacked_max"),
        effective_price.mean().alias("_mean_effective_price"),
        pl.col("regular_price").mean().alias("_mean_regular_price"),
        pl.col("promo_type_group").filter(pl.col("promo_active")).mode().first().alias("_dominant_type"),
    )
    weekly = weekly.with_columns((pl.col("_n_unknown") > pl.col("_n_days") / 2).alias("_is_unknown_week"))
    weekly = weekly.with_columns(
        pl.when(pl.col("_is_unknown_week")).then(pl.lit("UNKNOWN"))
          .when(pl.col("_n_confirmed") > 0).then(pl.lit("CONFIRMED"))
          .otherwise(pl.lit("NOT_CONFIRMED")).alias("promo_status_week"),
        pl.when(pl.col("_is_unknown_week")).then(None).otherwise(pl.col("promo_days_in_week")).alias("promo_days_in_week"),
        pl.when(pl.col("_is_unknown_week")).then(None).otherwise(pl.col("promo_discount_pct_max")).alias("promo_discount_pct_max"),
        pl.when(pl.col("_is_unknown_week")).then(None).otherwise(pl.col("promo_discount_pct_mean")).alias("promo_discount_pct_mean"),
        pl.when(pl.col("_is_unknown_week")).then(None).otherwise(pl.col("promo_stacked_max")).alias("promo_stacked_max"),
        pl.when(pl.col("_is_unknown_week")).then(None)
          .when(pl.col("promo_days_in_week") > 0).then(pl.col("_dominant_type"))
          .otherwise(pl.lit("none")).alias("promo_type_group_week"),
        pl.when(pl.col("_mean_regular_price").is_not_null() & (pl.col("_mean_regular_price") > 0))
          .then(pl.col("_mean_effective_price") / pl.col("_mean_regular_price"))
          .otherwise(None).alias("price_ratio"),
    )
    weekly = weekly.select([
        "itemCode", "week_start", "promo_status_week", "promo_days_in_week",
        "promo_discount_pct_max", "promo_discount_pct_mean", "promo_stacked_max",
        "promo_type_group_week", "price_ratio",
    ]).sort("itemCode", "week_start")

    weekly = weekly.with_columns((pl.col("promo_days_in_week") > 0).alias("_promo_active_any"))
    weekly = weekly.with_columns(
        pl.col("_promo_active_any").shift(-1).over("itemCode").cast(pl.Float64).alias("promo_pre_week"),
        pl.col("_promo_active_any").shift(1).over("itemCode").cast(pl.Float64).alias("promo_post_week1"),
        pl.col("_promo_active_any").shift(2).over("itemCode").cast(pl.Float64).alias("promo_post_week2"),
    )
    return weekly.drop("_promo_active_any")


def calendar_features_for_week(week_start: date) -> dict:
    """Pure calendar lookup for a specific future ISO week - same formulas as
    preprocess_features.py's add_calendar_features()/add_vic_calendar_flags(),
    evaluated directly instead of read from a historical row."""
    iso_week_num = int(week_start.isocalendar()[1])
    week_end = week_start + timedelta(days=6)

    holidays = pl.read_csv(HOLIDAYS_CSV, try_parse_dates=True)
    has_holiday = holidays.filter(
        (pl.col("date") >= week_start) & (pl.col("date") <= week_end)
    ).height > 0

    terms = pl.read_csv(SCHOOL_TERMS_CSV, try_parse_dates=True)
    terms = terms.with_columns(
        (pl.min_horizontal(pl.lit(week_end), "end_date") - pl.max_horizontal(pl.lit(week_start), "start_date"))
        .dt.total_days().add(1).clip(lower_bound=0).alias("overlap_days")
    )
    term_days = terms["overlap_days"].sum()

    return {
        "woy_sin": np.sin(2 * 3.14159265 * iso_week_num / 52),
        "woy_cos": np.cos(2 * 3.14159265 * iso_week_num / 52),
        "cal_month": week_start.month,
        "cal_quarter": (week_start.month - 1) // 3 + 1,
        "has_public_holiday": 1 if has_holiday else 0,
        "is_school_term": 1 if term_days >= 4 else 0,
    }


def build_baseline_15day(as_of: date) -> pl.DataFrame:
    """Predict the two ISO weeks covering the 15-day horizon and sum them.

    Lag/rolling sales features (and SKU metadata) come from the most recent
    *complete* week - sales aren't known in advance, so this is the best
    available proxy for both target weeks (no autoregressive rollout
    between the two). Promo and calendar features, unlike sales, ARE known
    in advance, so each target week gets its own real values computed for
    its own dates from promotions_daily.parquet and the vic_*.csv reference
    data, rather than reusing last week's - see experiments.md.
    """
    df = pl.read_parquet(FEATURES_PATH).filter(pl.col("is_complete_week"))
    # Only use data available as of the as_of date
    df = df.filter(pl.col("week_start") <= as_of)
    # Take the most recent feature row per SKU
    latest = df.sort("week_start").group_by("itemCode").tail(1)

    horizon_start = as_of + timedelta(days=1)
    horizon_end   = as_of + timedelta(days=15)
    week1_start = iso_week_monday(horizon_start)
    week2_start = week1_start + timedelta(days=7)

    future_promo = build_future_promo_weekly() if PROMO_DAILY_PATH.exists() else None

    model = lgb.Booster(model_file=str(MODEL_UNITS))
    week_preds = []
    for week_start in (week1_start, week2_start):
        wk = latest.drop(PROMO_FEATURE_COLS + CALENDAR_FEATURE_COLS)
        cal = calendar_features_for_week(week_start)
        wk = wk.with_columns([pl.lit(v).alias(k) for k, v in cal.items()])

        if future_promo is not None:
            promo_row = future_promo.filter(pl.col("week_start") == week_start).drop("week_start")
            wk = wk.join(promo_row, on="itemCode", how="left")
        else:
            wk = wk.with_columns([pl.lit(None).alias(c) for c in PROMO_FEATURE_COLS])

        pdf = wk.select(FEATURE_COLS).to_pandas()
        for c in CATEGORICAL:
            pdf[c] = pdf[c].astype("category")
        week_preds.append(np.clip(model.predict(pdf), a_min=0, a_max=None))

    # Two ISO weeks = 14 days of the 15-day horizon; scale up for the 15th day.
    pred_15d = (week_preds[0] + week_preds[1]) * (15 / 14)

    result = latest.select(["itemCode", "commodityName", "departmentName",
                             "cartonQty", "minOrderQty", "uom_class"]).with_columns(
        pl.lit(str(as_of)).alias("as_of_date"),
        pl.lit(str(horizon_start)).alias("horizon_start"),
        pl.lit(str(horizon_end)).alias("horizon_end"),
        pl.Series("baseline_forecast_15d", pred_15d),
    )
    return result


# ── Step 2: Promotion Uplift ───────────────────────────────────────────────────
def apply_promotion_uplift(baseline: pl.DataFrame, as_of: date) -> pl.DataFrame:
    """Merge future promotions from the daily table and apply hybrid uplift.

    Reads ``promotions_daily.parquet`` (produced by ``ingest_promotions.py``),
    filters to promo-active days within the 15-day horizon, aggregates per
    SKU, and predicts uplift using the retrained promotion model.
    """
    if not PROMO_DAILY_PATH.exists():
        print("  [warn] promotions_daily.parquet not found — returning baseline unchanged.")
        return baseline.with_columns(
            pl.lit(False).alias("promotion_active"),
            pl.lit("NONE").alias("promo_type"),
            pl.lit(0.0).alias("discount_pct"),
            pl.lit(0).cast(pl.Int64).alias("promo_days_in_horizon"),
            pl.lit(0.0).alias("predicted_uplift"),
            pl.col("baseline_forecast_15d").alias("hybrid_forecast_15d"),
        )

    daily = pl.read_parquet(PROMO_DAILY_PATH)

    horizon_start = as_of + timedelta(days=1)
    horizon_end   = as_of + timedelta(days=15)

    # Filter to promo-active days within the 15-day horizon
    horizon_promos = daily.filter(
        pl.col("promo_active")
        & (pl.col("trading_date") >= horizon_start)
        & (pl.col("trading_date") <= horizon_end)
    )

    # Aggregate per SKU: count promo days, max discount, dominant type
    best_promo = horizon_promos.group_by("itemCode").agg(
        pl.len().alias("promo_days_in_horizon"),
        pl.col("discount_pct").max().alias("discount_pct"),
        pl.col("promo_type_group").mode().first().alias("promo_type_group"),
    ).with_columns(
        pl.col("promo_type_group").fill_null("NONE"),
    )

    # Join onto baseline
    merged = baseline.join(best_promo, on="itemCode", how="left").with_columns(
        pl.col("promo_type_group").fill_null("NONE"),
        pl.col("promo_days_in_horizon").fill_null(0).cast(pl.Int64),
    )
    
    # Non-promotions get 0.0, but active promotions keep their null discount_pct (as NaN so float() doesn't crash)
    merged = merged.with_columns(
        pl.when(pl.col("promo_days_in_horizon") == 0)
          .then(0.0)
          .otherwise(pl.col("discount_pct").fill_null(float('nan')))
          .alias("discount_pct")
    )

    # Rename promo_type_group → promo_type for output column compatibility
    merged = merged.rename({"promo_type_group": "promo_type"})

    # Predict uplift using promotion model
    promo_model_available = MODEL_PROMO.exists()
    if promo_model_available:
        promo_model = lgb.Booster(model_file=str(MODEL_PROMO))
        pdf = merged.select(["discount_pct", "promo_days_in_horizon", "promo_type"]).to_pandas()
        pdf["promo_type"] = pdf["promo_type"].astype("category")
        pdf = pdf.rename(columns={
            "promo_days_in_horizon": "overlap_days",
            "promo_type": "promo_type_group",
        })
        raw_uplift = promo_model.predict(pdf)
    else:
        raw_uplift = np.zeros(merged.height)

    merged = merged.with_columns(
        pl.Series("raw_uplift", raw_uplift)
    )

    # Apply uplift ONLY where promotion is active and uplift is meaningful
    merged = merged.with_columns(
        (pl.col("promo_days_in_horizon") > 0).alias("promotion_active"),
        # Clamp uplift to safe bounds
        pl.when(pl.col("promo_days_in_horizon") > 0)
            .then(pl.col("raw_uplift").clip(-0.5, MAX_UPLIFT))
            .otherwise(pl.lit(0.0)).alias("predicted_uplift"),
    )
    
    # DQ flag for active promotions with missing discount
    merged = merged.with_columns(
        (pl.col("promotion_active") & pl.col("discount_pct").is_nan()).alias("review_dq_flag")
    )

    # Promo-day fraction of the 15-day horizon (scale uplift proportionally)
    merged = merged.with_columns(
        (pl.col("promo_days_in_horizon") / 15.0).clip(0.0, 1.0).alias("promo_fraction")
    )

    # Hybrid formula: baseline * (1 + uplift * promo_fraction)
    merged = merged.with_columns(
        pl.when(pl.col("promotion_active") & (pl.col("predicted_uplift").abs() >= MIN_UPLIFT_THRESHOLD))
            .then(pl.col("baseline_forecast_15d") * (1.0 + pl.col("predicted_uplift") * pl.col("promo_fraction")))
            .otherwise(pl.col("baseline_forecast_15d"))
            .clip(lower_bound=0.0)
            .alias("hybrid_forecast_15d")
    )

    return merged.drop(["raw_uplift", "promo_fraction"])


# ── Step 3: PO Calculator ──────────────────────────────────────────────────────
def calculate_po(forecast: pl.DataFrame) -> pl.DataFrame:
    """Apply inventory/PO logic from generate_po_report.py (unchanged business rules)."""
    np.random.seed(42)

    rows = []
    for row in forecast.iter_rows(named=True):
        demand_15d  = float(row["hybrid_forecast_15d"])
        carton_qty  = max(1, int(row.get("cartonQty") or 1))
        moq         = max(1, int(row.get("minOrderQty") or 1))
        daily_rate  = demand_15d / 15.0 if demand_15d > 0 else 0.0

        # Mock stock (in production this would come from live inventory API)
        current_stock = int(np.random.randint(0, int(demand_15d * 1.5) + 10))

        shortfall = demand_15d - current_stock

        if shortfall > 0:
            # Round up to MOQ, then round up to nearest carton
            units_needed = int(np.ceil(shortfall / moq) * moq)
            units_needed = int(np.ceil(units_needed / carton_qty) * carton_qty)
            decision = "CREATE PO" if shortfall > demand_15d * 0.5 else "REVIEW REQUIRED"
        else:
            units_needed = 0
            decision = "NO PO REQUIRED"

        days_of_supply = (current_stock / daily_rate) if daily_rate > 0 else 999
        if days_of_supply < 7:    risk = "Critical"
        elif days_of_supply < 14: risk = "High"
        elif days_of_supply < 21: risk = "Medium"
        else:                     risk = "Low"

        from datetime import datetime
        today = datetime.now()
        stockout_date = (today + timedelta(days=int(days_of_supply))).strftime("%d %b %Y") if days_of_supply < 15 else "N/A"

        rows.append({
            "itemCode":               row["itemCode"],
            "item_name":              row.get("commodityName", ""),
            "horizon_start":          row["horizon_start"],
            "horizon_end":            row["horizon_end"],
            "current_stock":          current_stock,
            "baseline_forecast_15d":  round(row["baseline_forecast_15d"], 2),
            "promotion_active":       row["promotion_active"],
            "promotion_type":         row.get("promo_type", "NONE"),
            "discount_pct":           round(float(row.get("discount_pct", 0.0)) * 100, 1),
            "promo_days_in_horizon":  int(row.get("promo_days_in_horizon", 0)),
            "predicted_uplift_pct":   round(float(row.get("predicted_uplift", 0.0)) * 100, 1),
            "hybrid_forecast_15d":    round(row["hybrid_forecast_15d"], 2),
            "recommended_order_qty":  units_needed,
            "po_decision":            decision,
            "stockout_risk":          risk,
            "stockout_date":          stockout_date,
        })

    return pl.DataFrame(rows)


# ── Main Orchestrator ──────────────────────────────────────────────────────────
def main(as_of: date | None = None):
    if as_of is None:
        as_of = date.today()

    print(f"\n{'='*60}")
    print(f"  Pearcedale Hybrid Forecasting Pipeline")
    print(f"  As-of date: {as_of}  |  Horizon: +15 days")
    print(f"{'='*60}\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Baseline
    print("[1/4] Generating 15-day baseline forecast...")
    baseline = build_baseline_15day(as_of)
    baseline.write_parquet(OUTPUT_DIR / "baseline_predictions_15day.parquet")
    baseline.write_csv(OUTPUT_DIR / "baseline_predictions_15day.csv")
    print(f"      {baseline.height} SKUs | baseline written.")

    # Step 2: Promotion Uplift
    print("[2/4] Applying promotion uplift...")
    hybrid = apply_promotion_uplift(baseline, as_of)

    promo_count = hybrid.filter(pl.col("promotion_active")).height
    print(f"      {promo_count}/{hybrid.height} SKUs have active promotions in this 15-day window.")

    # Write promotion effects
    promo_effects = hybrid.filter(pl.col("promotion_active")).select([
        "itemCode", "promo_type", "discount_pct",
        "promo_days_in_horizon", "predicted_uplift",
        "baseline_forecast_15d", "hybrid_forecast_15d"
    ])
    promo_effects.write_csv(OUTPUT_DIR / "promotion_effects.csv")
    hybrid.write_csv(OUTPUT_DIR / "hybrid_predictions_15day.csv")
    print("      Promotion effects written.")

    # Step 3: PO Recommendations
    print("[3/4] Running PO calculator (MOQ + carton rounding + stockout)...")
    po = calculate_po(hybrid)
    po.write_csv(OUTPUT_DIR / "po_recommendations.csv")
    print("      PO recommendations written.")

    # Step 4: Summary Report
    print("[4/4] Generating summary report...")
    create_po_summary = po.filter(pl.col("po_decision") == "CREATE PO").height
    review_summary    = po.filter(pl.col("po_decision") == "REVIEW REQUIRED").height
    no_po_summary     = po.filter(pl.col("po_decision") == "NO PO REQUIRED").height
    critical_count    = po.filter(pl.col("stockout_risk") == "Critical").height

    report = {
        "run_date": str(as_of),
        "horizon": {"start": str(as_of + timedelta(days=1)), "end": str(as_of + timedelta(days=15))},
        "total_skus": baseline.height,
        "skus_with_active_promotion": promo_count,
        "po_decisions": {
            "CREATE_PO": create_po_summary,
            "REVIEW_REQUIRED": review_summary,
            "NO_PO_REQUIRED": no_po_summary,
        },
        "critical_stockout_risk_skus": critical_count,
        "outputs": {
            "baseline": str(OUTPUT_DIR / "baseline_predictions_15day.csv"),
            "promotion_effects": str(OUTPUT_DIR / "promotion_effects.csv"),
            "hybrid_forecast": str(OUTPUT_DIR / "hybrid_predictions_15day.csv"),
            "po_recommendations": str(OUTPUT_DIR / "po_recommendations.csv"),
        }
    }
    (OUTPUT_DIR / "evaluation_report.json").write_text(json.dumps(report, indent=2))

    print(f"\n{'='*60}")
    print(f"  DONE. Summary:")
    print(f"    Total SKUs forecast:         {baseline.height}")
    print(f"    SKUs with active promotions: {promo_count}")
    print(f"    CREATE PO:                   {create_po_summary}")
    print(f"    REVIEW REQUIRED:             {review_summary}")
    print(f"    NO PO REQUIRED:              {no_po_summary}")
    print(f"    Critical stockout risk:      {critical_count}")
    print(f"  Outputs written to: {OUTPUT_DIR.resolve()}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pearcedale Hybrid Forecasting Pipeline")
    parser.add_argument("--as-of", type=str, default=None,
                        help="Override as-of date (YYYY-MM-DD). Defaults to today.")
    args = parser.parse_args()

    as_of_date = date.fromisoformat(args.as_of) if args.as_of else date.today()
    main(as_of=as_of_date)
