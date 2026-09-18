"""Train global LightGBM demand models on the weekly feature matrix.

Two separate models, per the vendor's own modelling notes (CLAUDE.md):
  - units  -> Poisson objective (integer counts)
  - weight -> Tweedie objective (fractional kg, scale items)

One global model per group, SKU as a categorical feature - not one model
per SKU (each SKU has too few weekly rows on its own for that).

Validation is walk-forward, never random split:
  train  = everything before (holdout_start - GAP_WEEKS)
  early-stopping val = last VAL_WEEKS of the train span
  holdout = last HOLDOUT_WEEKS weeks (evaluation only, never trained on)

GAP_WEEKS stands in for supplier lead time, which isn't in this dataset -
it's a placeholder assumption, documented here rather than silently baked
into the split. Adjust it once the real lead time is known.

Usage:
    python train_lgbm.py
"""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl

FEATURES = Path("features/weekly_features.parquet")
ARTIFACT_DIR = Path("artifacts")
REPORT_DIR = Path("reports")

HOLDOUT_WEEKS = 13   # ~90 days, matches the vendor's own baseline evaluation window
VAL_WEEKS = 8        # carved out of the training tail, for early stopping only
GAP_WEEKS = 1         # placeholder for supplier lead time - not present in the data

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

OBJECTIVE_BY_GROUP = {"units": "poisson", "weight": "tweedie"}


def wmape(actual: np.ndarray, pred: np.ndarray) -> float:
    denom = np.abs(actual).sum()
    if denom == 0:
        return float("nan")
    return float(np.abs(actual - pred).sum() / denom)


def make_splits(df: pl.DataFrame):
    max_week = df.select(pl.col("week_start").max()).item()
    holdout_start = max_week - np.timedelta64(HOLDOUT_WEEKS * 7, "D")
    train_end = holdout_start - np.timedelta64(GAP_WEEKS * 7, "D")
    val_start = train_end - np.timedelta64(VAL_WEEKS * 7, "D")

    train = df.filter(pl.col("week_start") < val_start)
    val = df.filter((pl.col("week_start") >= val_start) & (pl.col("week_start") < train_end))
    holdout = df.filter(pl.col("week_start") >= holdout_start)
    return train, val, holdout, holdout_start, train_end, val_start


def to_lgb_frame(df: pl.DataFrame):
    pdf = df.select(FEATURE_COLS).to_pandas()
    for c in CATEGORICAL:
        pdf[c] = pdf[c].astype("category")
    y = df.select("qty_sum").to_pandas()["qty_sum"].to_numpy(dtype=float)
    return pdf, y


def train_group(name: str, df: pl.DataFrame) -> dict:
    if df.height == 0:
        return {"group": name, "skipped": True, "reason": "empty dataset", "rows": {"train": 0, "val": 0, "holdout": 0}}

    train, val, holdout, holdout_start, train_end, val_start = make_splits(df)

    if train.height == 0 or val.height == 0 or holdout.height == 0:
        return {"group": name, "skipped": True, "reason": "insufficient rows for a walk-forward split",
                "rows": {"train": train.height, "val": val.height, "holdout": holdout.height}}

    X_train, y_train = to_lgb_frame(train)
    X_val, y_val = to_lgb_frame(val)
    X_hold, y_hold = to_lgb_frame(holdout)

    model = lgb.LGBMRegressor(
        objective=OBJECTIVE_BY_GROUP[name],
        n_estimators=2000,
        learning_rate=0.03,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        eval_metric="mae",
        categorical_feature=CATEGORICAL,
        callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False), lgb.log_evaluation(period=0)],
    )

    pred_hold = np.clip(model.predict(X_hold), a_min=0, a_max=None)
    overall_wmape = wmape(y_hold, pred_hold)

    tiers = holdout.select("tier").to_pandas()["tier"].to_numpy()
    by_tier = {}
    for t in sorted(set(tiers)):
        mask = tiers == t
        by_tier[t] = wmape(y_hold[mask], pred_hold[mask])

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(ARTIFACT_DIR / f"lgbm_{name}.txt"))

    importances = sorted(
        zip(FEATURE_COLS, model.feature_importances_.tolist()),
        key=lambda x: -x[1],
    )

    return {
        "group": name,
        "skipped": False,
        "objective": OBJECTIVE_BY_GROUP[name],
        "n_skus": df.select("itemCode").unique().height,
        "rows": {"train": train.height, "val": val.height, "holdout": holdout.height},
        "splits": {
            "val_start": str(val_start), "train_end_gap_start": str(train_end),
            "holdout_start": str(holdout_start), "gap_weeks": GAP_WEEKS,
        },
        "best_iteration": model.best_iteration_,
        "wmape_holdout": overall_wmape,
        "wmape_holdout_by_tier": by_tier,
        "feature_importance_top10": importances[:10],
    }


def generate_walk_forward_oos(name: str, df: pl.DataFrame):
    if df.height == 0:
        return None

    print(f"Generating OOS baseline predictions for {name}...")
    min_week = df.select(pl.col("week_start").min()).item()
    max_week = df.select(pl.col("week_start").max()).item()

    # Start predicting from roughly 1.5 years in (to give enough training data)
    # Since our promotions start April 2026, we just need OOS from Jan 2026 onwards.
    start_predict = max_week - np.timedelta64(40 * 7, "D")

    current_start = start_predict
    all_preds = []

    while current_start <= max_week:
        current_end = current_start + np.timedelta64(4 * 7, "D")

        train = df.filter(pl.col("week_start") < current_start - np.timedelta64(GAP_WEEKS * 7, "D"))
        test = df.filter((pl.col("week_start") >= current_start) & (pl.col("week_start") < current_end))

        if train.height == 0 or test.height == 0:
            current_start = current_end
            continue

        X_train, y_train = to_lgb_frame(train)
        X_test, _ = to_lgb_frame(test)

        model = lgb.LGBMRegressor(
            objective=OBJECTIVE_BY_GROUP[name],
            n_estimators=200, # Faster for CV
            learning_rate=0.05,
            num_leaves=31,
            random_state=42,
            verbose=-1
        )
        model.fit(X_train, y_train, categorical_feature=CATEGORICAL)
        preds = np.clip(model.predict(X_test), a_min=0, a_max=None)

        res = test.select(["itemCode", "week_start", "qty_sum"]).with_columns(
            pl.Series("baseline_forecast", preds)
        )
        all_preds.append(res)
        current_start = current_end

    if all_preds:
        return pl.concat(all_preds)
    return None

def main() -> None:
    df = pl.read_parquet(FEATURES).filter(pl.col("is_complete_week"))

    results = []
    oos_dfs = []
    for name in ("units", "weight"):
        sub = df.filter(pl.col("uom_class") == name)
        print(f"--- {name}: {sub.height} complete SKU-weeks, "
              f"{sub.select('itemCode').unique().height} SKUs ---")
        res = train_group(name, sub)
        results.append(res)

        oos_df = generate_walk_forward_oos(name, sub)
        if oos_df is not None:
            oos_dfs.append(oos_df)

        if res.get("skipped"):
            print(f"  skipped: {res['reason']}")
        else:
            print(f"  holdout WMAPE: {res['wmape_holdout']:.3%}  "
                  f"(by tier: { {k: f'{v:.3%}' for k, v in res['wmape_holdout_by_tier'].items()} })")
            print(f"  best_iteration: {res['best_iteration']}")

    if oos_dfs:
        final_oos = pl.concat(oos_dfs)
        out_path = Path("../processed_output/baseline_oos_predictions.parquet")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        final_oos.write_parquet(out_path)
        print(f"Wrote OOS predictions to {out_path}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "evaluation_report.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote {REPORT_DIR / 'evaluation_report.json'}")


if __name__ == "__main__":
    main()
