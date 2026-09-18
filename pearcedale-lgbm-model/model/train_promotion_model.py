import json
from pathlib import Path
import polars as pl
import lightgbm as lgb
import numpy as np

OOS_BASELINE = Path("../processed_output/baseline_oos_predictions.parquet")
PROMOTIONS = Path("../processed_output/promotions.parquet")
OUTPUT_MODEL = Path("artifacts/lgbm_promotion.txt")
REPORT_DIR = Path("reports")

def overlap_days(w_start, w_end, p_start, p_end):
    latest_start = max(w_start, p_start)
    earliest_end = min(w_end, p_end)
    delta = (earliest_end - latest_start).days + 1
    return max(0, delta)

def build_training_data():
    oos = pl.read_parquet(OOS_BASELINE)
    
    # We must calculate week_end for overlap
    oos = oos.with_columns(
        (pl.col("week_start") + pl.duration(days=6)).alias("week_end")
    )
    
    promos = pl.read_parquet(PROMOTIONS)
    
    # Cross join is too big, let's do an exact or range join, or just iterate since it's small.
    # Actually, polars doesn't have a non-equi join yet in standard API, but we can join on itemCode 
    # and then filter.
    
    merged = oos.join(promos, on="itemCode", how="left")
    
    # Calculate overlap
    # We have week_start, week_end, promo_start, promo_end
    # Overlap condition: promo_start <= week_end AND promo_end >= week_start
    
    # Create a column for overlap_days
    merged = merged.with_columns(
        pl.when(
            (pl.col("promo_start") <= pl.col("week_end")) & (pl.col("promo_end") >= pl.col("week_start"))
        ).then(
            # Calculate overlap days (this is a bit tricky with pure expressions, we can do it via map_elements or just simple arithmetic)
            # latest_start = max(week_start, promo_start)
            pl.max_horizontal(pl.col("week_start"), pl.col("promo_start")).alias("latest_start")
        ).otherwise(None).alias("latest_start_tmp")
    )
    
    merged = merged.with_columns(
        pl.when(pl.col("latest_start_tmp").is_not_null()).then(
            pl.min_horizontal(pl.col("week_end"), pl.col("promo_end"))
        ).otherwise(None).alias("earliest_end_tmp")
    )
    
    merged = merged.with_columns(
        pl.when(pl.col("latest_start_tmp").is_not_null()).then(
            (pl.col("earliest_end_tmp") - pl.col("latest_start_tmp")).dt.total_days() + 1
        ).otherwise(0).alias("overlap_days")
    )
    
    # Aggregate back to weekly (since one week might have multiple overlapping promos, pick the one with max overlap)
    # Sort by itemCode, week_start, overlap_days descending
    weekly_promo = merged.sort(["itemCode", "week_start", "overlap_days"], descending=[False, False, True])
    weekly_promo = weekly_promo.group_by(["itemCode", "week_start"]).first()
    
    # Determine Status
    # Earliest promo in dataset is approx 2026-04-24
    promo_visibility_start = promos.select(pl.col("promo_start").min()).item()
    
    weekly_promo = weekly_promo.with_columns(
        pl.when(pl.col("overlap_days") > 0).then(pl.lit("PROMOTION CONFIRMED"))
        .when((pl.col("overlap_days") == 0) & (pl.col("week_start") >= promo_visibility_start)).then(pl.lit("PROMOTION NOT CONFIRMED"))
        .otherwise(pl.lit("UNKNOWN")).alias("promo_status")
    )
    
    # Filter only valid rows for training (we don't train on UNKNOWN)
    train_data = weekly_promo.filter(pl.col("promo_status").is_in(["PROMOTION CONFIRMED", "PROMOTION NOT CONFIRMED"]))
    
    # Calculate target: uplift = (actual / baseline) - 1. 
    # If baseline is 0, handle safely (e.g. uplift = 0 or drop)
    train_data = train_data.filter(pl.col("baseline_forecast") > 0)
    train_data = train_data.with_columns(
        ((pl.col("qty_sum") / pl.col("baseline_forecast")) - 1.0).alias("actual_uplift")
    )
    
    # Clip extreme uplifts to prevent outliers from skewing the model
    train_data = train_data.with_columns(
        pl.col("actual_uplift").clip(-1.0, 5.0)
    )
    
    # Fill nulls for non-promos
    train_data = train_data.with_columns(
        pl.col("discount_pct").fill_null(0.0),
        pl.col("promo_type").fill_null("NONE")
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
        
    features = ["discount_pct", "overlap_days", "promo_type"]
    categorical = ["promo_type"]
    
    pdf = df.to_pandas()
    for c in categorical:
        pdf[c] = pdf[c].astype("category")
        
    X = pdf[features]
    y = pdf["actual_uplift"].values
    
    # For a robust evaluation, we would do a walk-forward split here too, 
    # but since this is just a coefficient estimator on a tiny dataset (~April-Sept),
    # we'll use a simple CV or just train on all valid points to get the best estimator, 
    # because the *baseline* was already generated OOS.
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

if __name__ == "__main__":
    train_model()
