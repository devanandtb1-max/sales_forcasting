"""Train the promotion-uplift model from the cleaned daily promotion table.

Reads the walk-forward baseline OOS predictions and the daily promo table
(``promotions_daily.parquet``, produced by ``ingest_promotions.py``).  For
each (SKU, ISO-week) row in the baseline predictions, counts how many
promo-active days fell within that week (``overlap_days``), takes the max
``discount_pct`` and dominant ``promo_type_group``, and trains a LightGBM
regressor to predict the multiplicative uplift (``actual / baseline − 1``).

This replaces the legacy approach of joining event-level
``promotions.parquet`` (which had corrupt discount values) and resolving
overlaps by longest-window — the daily table has already resolved overlaps
by deepest discount (see ``ingest_promotions.py``).

Usage:
    python train_promotion_model.py
"""
import json
from pathlib import Path
import polars as pl
import lightgbm as lgb
import numpy as np

OOS_BASELINE = Path("../processed_output/baseline_oos_predictions.parquet")
PROMO_DAILY = Path("../processed_output/promotions_daily.parquet")
OUTPUT_MODEL = Path("artifacts/lgbm_promotion.txt")
REPORT_DIR = Path("reports")


def build_promo_weekly() -> pl.DataFrame:
    """Aggregate daily promo table to one row per (itemCode, iso_week).

    Returns columns: itemCode, iso_week, overlap_days, discount_pct,
    promo_type_group, promo_status.
    """
    daily = pl.read_parquet(PROMO_DAILY)
    daily = daily.with_columns(
        pl.col("trading_date").dt.strftime("%G-%V").alias("iso_week"),
    )

    weekly = daily.group_by(["itemCode", "iso_week"]).agg(
        pl.col("trading_date").min().alias("week_start"),
        # overlap_days = count of promo-active days in this week
        pl.col("promo_active").sum().alias("overlap_days"),
        # Best discount during the week (max, since higher = deeper)
        pl.col("discount_pct").filter(pl.col("promo_active")).max().alias("discount_pct"),
        # Dominant promo type on promo-active days
        pl.col("promo_type_group").filter(pl.col("promo_active")).mode().first().alias("promo_type_group"),
        # Promo status for the week
        (pl.col("promo_status") == "UNKNOWN").sum().alias("_n_unknown"),
        pl.len().alias("_n_days"),
    )

    # Determine week-level promo status
    weekly = weekly.with_columns(
        pl.when(pl.col("_n_unknown") > pl.col("_n_days") / 2)
          .then(pl.lit("UNKNOWN"))
          .when(pl.col("overlap_days") > 0)
          .then(pl.lit("CONFIRMED"))
          .otherwise(pl.lit("NOT_CONFIRMED"))
          .alias("promo_status"),
    )

    # Fill nulls for non-promo weeks
    weekly = weekly.with_columns(
        pl.col("discount_pct").fill_null(0.0),
        pl.col("promo_type_group").fill_null("NONE"),
    )

    return weekly.select([
        "itemCode", "iso_week", "week_start", "overlap_days",
        "discount_pct", "promo_type_group", "promo_status",
    ])


def build_training_data():
    oos = pl.read_parquet(OOS_BASELINE)

    # We need iso_week to join
    oos = oos.with_columns(
        pl.col("week_start").dt.strftime("%G-%V").alias("iso_week"),
    )

    promo_weekly = build_promo_weekly()

    # Join on (itemCode, iso_week) — one-to-one, no cross-join explosion
    merged = oos.join(
        promo_weekly.select(["itemCode", "iso_week", "overlap_days",
                             "discount_pct", "promo_type_group", "promo_status"]),
        on=["itemCode", "iso_week"],
        how="left",
    )

    # Fill nulls for SKUs with no promo data at all
    merged = merged.with_columns(
        pl.col("overlap_days").fill_null(0),
        pl.col("discount_pct").fill_null(0.0),
        pl.col("promo_type_group").fill_null("NONE"),
        pl.col("promo_status").fill_null("UNKNOWN"),
    )

    # Filter only valid rows for training (not UNKNOWN)
    train_data = merged.filter(
        pl.col("promo_status").is_in(["CONFIRMED", "NOT_CONFIRMED"])
    )

    # Calculate target: uplift = (actual / baseline) - 1.
    # Drop rows where baseline is 0 (can't compute ratio)
    train_data = train_data.filter(pl.col("baseline_forecast") > 0)
    train_data = train_data.with_columns(
        ((pl.col("qty_sum") / pl.col("baseline_forecast")) - 1.0).alias("actual_uplift")
    )

    # Clip extreme uplifts to prevent outliers from skewing the model
    train_data = train_data.with_columns(
        pl.col("actual_uplift").clip(-1.0, 5.0)
    )

    return train_data


def train_model():
    print("Building training data for promotion model...")
    df = build_training_data()
    print(f"Usable training rows: {df.height}")
    print(df.group_by("promo_status").len().to_pandas().to_string())

    if df.height == 0:
        print("No training data available. Exiting.")
        return

    features = ["discount_pct", "overlap_days", "promo_type_group"]
    categorical = ["promo_type_group"]

    pdf = df.to_pandas()
    for c in categorical:
        pdf[c] = pdf[c].astype("category")

    X = pdf[features]
    y = pdf["actual_uplift"].values

    # Same architecture + hyperparameters as before.
    # The true evaluation happens in hybrid_forecast.py on the 13-week holdout.
    model = lgb.LGBMRegressor(
        objective="regression",
        n_estimators=100,
        learning_rate=0.05,
        num_leaves=15,
        min_child_samples=5,
        random_state=42
    )

    model.fit(X, y, categorical_feature=categorical)

    OUTPUT_MODEL.parent.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(OUTPUT_MODEL))
    print(f"Saved promotion model to {OUTPUT_MODEL}")

    # Print some predictions to see if it learned anything
    print("\nSample predictions on training set:")
    pdf["predicted_uplift"] = model.predict(X)
    print(pdf[["promo_status", "discount_pct", "overlap_days", "actual_uplift", "predicted_uplift"]].head(10))

    # Print feature importance
    print("\nFeature importance:")
    for fname, imp in zip(features, model.feature_importances_):
        print(f"  {fname}: {imp}")


if __name__ == "__main__":
    train_model()

