# Pearcedale weekly demand model

Reads the DQ layer's `../processed_output/clean_daily_sales.parquet`
(already tier A/B only), aggregates to ISO week per SKU, and trains a
LightGBM baseline model plus a separate promotion-uplift model. The two are
combined into a hybrid forecast that feeds the PO logic. `run_forecasting.py`
is the single command n8n calls; everything else here is the training side.

**This file describes the current architecture. See "What changed and
why" below for the history of how it got here — every change was tried,
measured on a fixed holdout, and either kept or reverted; the full log is
`reports/experiments.md`.**

## Data

- 100 SKUs, all `uom_class == units` (no `weight`-class/scale-item SKUs
  survive in the current data — `artifacts/lgbm_weight.txt` on disk is
  stale, from an earlier data snapshot, and nothing in the pipeline loads
  it). `tier` is `UNKNOWN` for every SKU — the source data doesn't carry a
  real tier, and nothing here invents one.
- 15,700 SKU-weeks aggregated from daily sales; 9,782 "complete" (≥70% of
  the week's days observed) — the trainable set.
- Promotion data (`data/Pearcedale_Metcash_100_Promotions.json`) covers 87
  of the 100 SKUs from 2024-10-02 onward; the other 13 SKUs and everything
  before that date have no promo visibility (`promo_status == UNKNOWN`,
  never treated as "no promo"). Full profiling: `reports/promo_data_profile.md`.

## Pipeline

```
ingest_promotions.py       Pearcedale_Metcash_100_Promotions.json + clean_daily_sales.parquet
                              -> processed_output/promotions_daily.parquet
                              (daily regular_price / promo_active / discount_pct / promo_type_group
                               per SKU, scaffolded through the latest *scheduled* promo end date -
                               not just historical sales dates, so future promotions are lookup-able)

preprocess_features.py     clean_daily_sales.parquet + promotions_daily.parquet
                              + data/vic_public_holidays.csv + data/vic_school_terms.csv
                              -> features/weekly_features.parquet
                              (lag/rolling sales features, promo weekly features, VIC calendar flags)

train_lgbm.py               weekly_features.parquet
                              -> artifacts/lgbm_units.txt                (single train/val/holdout split model)
                              -> processed_output/baseline_oos_predictions.parquet (rolling 4-week-retrain walk-forward)
                              -> reports/evaluation_report.json

train_promotion_model.py    baseline_oos_predictions.parquet + processed_output/promotions.parquet
                              -> artifacts/lgbm_promotion.txt            (predicts uplift = actual/baseline - 1)

hybrid_forecast.py          baseline_oos_predictions.parquet + lgbm_promotion.txt
                              -> reports/hybrid_evaluation.json          (baseline vs. hybrid WMAPE on the same holdout)

run_forecasting.py          lgbm_units.txt + lgbm_promotion.txt + promotions_daily.parquet + vic_*.csv
                              -> outputs/*.csv, outputs/evaluation_report.json
                              (single command, n8n entry point)
```

Run:

```bash
pip install polars pyarrow lightgbm scikit-learn
python ingest_promotions.py
python preprocess_features.py
python train_lgbm.py
python train_promotion_model.py
python hybrid_forecast.py
python run_forecasting.py               # today
python run_forecasting.py --as-of 2026-08-19   # backtest
```

## Design choices

- **Weekly, not daily**, for the same reason as before: daily WMAPE is
  already near the Poisson noise floor at this volume.
- **One global LightGBM model**, SKU as a categorical feature — not one
  model per SKU (too few weekly rows per SKU on their own) and not one
  model per volume band either (tried, made things worse — see
  experiments.md #1: splitting the training data three ways cost more in
  sample size than it gained in specialization).
- **The baseline model is promo- and calendar-aware.** `preprocess_features.py`
  builds 12 weekly features from the daily promo table and the VIC
  reference calendars: `promo_days_in_week`, `promo_discount_pct_max/mean`,
  `promo_stacked_max`, `promo_type_group_week`, `price_ratio`,
  `promo_pre_week` (next week — promotions are planned in advance, so this
  is not leakage), `promo_post_week1/2`, `promo_status_week`,
  `has_public_holiday`, `is_school_term`. All promo-derived columns are
  **null**, never a defaulted zero/false, for weeks where promo visibility
  is `UNKNOWN`.
- **A separate uplift model sits on top, multiplicatively.** Confirmed by
  direct A/B test (experiments.md, "single model vs. hybrid") that this
  two-stage design beats the promo-aware baseline on its own by 0.9-2.7
  points of WMAPE on every slice checked, with zero downside on
  non-promoted SKUs — it's correcting a real residual, not double-counting
  the promo signal the baseline already has.
- **Every other structural idea that was tried and didn't help was
  reverted, not left half-in.** Per-volume-band models, stockout-day
  exclusion from the target, hyperparameter tuning against the validation
  window, and a LightGBM+Ridge blended ensemble were all tried and
  rejected — see experiments.md #1-4 for what was tried and why each one
  made things worse.
- **`run_forecasting.py` predicts the two ISO weeks covering the 15-day
  horizon, not the last completed week scaled by 15/7.** Sales lag/rolling
  features and SKU metadata still come from the most recent complete week
  (sales aren't known in advance — this is unavoidable). But promo and
  calendar features **are** known in advance, so each target week gets its
  own real values, freshly computed from `promotions_daily.parquet` (now
  scaffolded into the future) and the VIC reference CSVs, instead of
  reusing whatever was true last week. Measured effect on the 15-day
  backtest: baseline WMAPE 25.35% → 23.58%, hybrid WMAPE 23.34% → 22.53%
  (same as-of date, before/after). This does not change the two-model
  hybrid architecture — it fixes what features that architecture is fed
  with at inference time.
- **Ambiguous nulls are never imputed to zero** — same principle as
  before, now applied consistently to promo features too.
- **Walk-forward split, no shuffling** — train / 8-week early-stopping
  validation / 13-week holdout, 1-week gap standing in for supplier lead
  time. Note from experiments.md #3: this validation window (~539 rows,
  one non-overlapping slice) is too small to reliably pick among close
  alternatives — fine for early stopping, not reliable for hyperparameter
  or architecture search.
- **Metric is WMAPE**, never MAPE.

## Results (current, real holdout)

13-week holdout, 2026-06-08 to 2026-09-07, 1,044 SKU-weeks / 99 SKUs:

| | Baseline (promo-aware LightGBM alone) | Hybrid (+ uplift model) |
|---|---|---|
| Overall WMAPE | 26.93% | **26.06%** |
| Promoted-SKU WMAPE | 29.48% | **27.96%** |
| Non-promoted WMAPE | 23.50% | 23.50% |

15-day backtest (as-of 2026-08-26, actuals vs. forecast, current
`run_forecasting.py`):

| | Baseline | Hybrid |
|---|---|---|
| Overall WMAPE | 23.58% | **22.53%** |
| Promoted-SKU WMAPE | — | **21.37%** |
| Non-promoted WMAPE | — | 26.34% |

Full numbers and every experiment's before/after: `reports/experiments.md`.
Per-SKU error breakdown and root causes for the worst-performing SKUs:
`reports/holdout_error_analysis.md`.

## What changed and why

Chronological summary — see `reports/experiments.md` for the full detail
and metrics behind each entry:

1. **v0 → v1**: added the promo/calendar weekly features described above
   and rewrote `ingest_promotions.py` to read the real promotion feed
   (it previously pointed at a consolidated JSON that doesn't exist in
   this checkout). Kept — holdout WMAPE improved 0.6-2.1 points across
   every slice.
2. **Single-model vs. hybrid A/B test**: confirmed the two-stage
   baseline+uplift design still beats a single promo-aware model with no
   separate uplift layer. No code change; validated the existing design.
3. **Four rejected experiments** (per-volume-band models, stockout-day
   exclusion, validation-only hyperparameter tuning, LightGBM+Ridge
   ensemble): all tried, all reverted — none beat v1's holdout WMAPE.
4. **`run_forecasting.py` horizon-aware promo/calendar features**: the
   model architecture was already "best" per (1)-(3), but the live
   inference path was still feeding it stale promo/calendar values (from
   last week, not the actual forecast horizon) alongside otherwise-correct
   lag/rolling sales features. Fixed by predicting the two ISO weeks that
   cover the horizon with their own real promo/calendar features. Output
   file names and columns are unchanged; only the forecasted values (and,
   because they're more accurate, the PO decisions downstream of them)
   moved.
