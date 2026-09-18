import json
from pathlib import Path
import polars as pl
import lightgbm as lgb
import numpy as np

OOS_BASELINE = Path("../processed_output/baseline_oos_predictions.parquet")
PROMOTIONS = Path("../processed_output/promotions.parquet")
PROMO_MODEL = Path("artifacts/lgbm_promotion.txt")
REPORT_DIR = Path("reports")
OUTPUT_PREDS = Path("../processed_output/hybrid_predictions_holdout.parquet")

def wmape(actual, pred):
    denom = np.abs(actual).sum()
    if denom == 0:
        return float("nan")
    return float(np.abs(actual - pred).sum() / denom)

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
    
    # Load Promos and prepare for join
    promos = pl.read_parquet(PROMOTIONS)
    holdout = holdout.with_columns(
        (pl.col("week_start") + pl.duration(days=6)).alias("week_end")
    )
    
    merged = holdout.join(promos, on="itemCode", how="left")
    
    # Overlap logic
    merged = merged.with_columns(
        pl.when(
            (pl.col("promo_start") <= pl.col("week_end")) & (pl.col("promo_end") >= pl.col("week_start"))
        ).then(
            pl.max_horizontal(pl.col("week_start"), pl.col("promo_start"))
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
    
    # Aggregate back to weekly (pick max overlap)
    weekly_promo = merged.sort(["itemCode", "week_start", "overlap_days"], descending=[False, False, True])
    weekly_promo = weekly_promo.group_by(["itemCode", "week_start"]).first()
    
    weekly_promo = weekly_promo.with_columns(
        pl.col("discount_pct").fill_null(0.0),
        pl.col("promo_type").fill_null("NONE")
    )
    
    features = ["discount_pct", "overlap_days", "promo_type"]
    pdf = weekly_promo.to_pandas()
    pdf["promo_type"] = pdf["promo_type"].astype("category")
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
    
    weekly_promo.write_parquet(OUTPUT_PREDS)
    
    report = {
        "holdout_start": str(holdout_start),
        "holdout_end": str(max_week),
        "n_rows": holdout.height,
        "baseline_wmape": base_wmape,
        "hybrid_wmape": hybrid_wmape,
        "improvement_abs": base_wmape - hybrid_wmape,
        "is_hybrid_better": hybrid_wmape < base_wmape
    }
    
    (REPORT_DIR / "hybrid_evaluation.json").write_text(json.dumps(report, indent=2))
    print("Wrote hybrid_evaluation.json")

if __name__ == "__main__":
    evaluate_hybrid()
