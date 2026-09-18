# Experiment log

Metrics are computed on the fixed 13-week holdout (2026-06-08 to 2026-09-07,
1,044 SKU-weeks, 99 SKUs) unless noted otherwise. No code was changed for
this entry — it records the metrics of the pipeline as it stands today.

## 2026-09-16 — v0 baseline (kept, reference point)

**Change:** None. First recorded run of the pipeline as committed
(`preprocess_features.py` → `train_lgbm.py` → `train_promotion_model.py` →
`hybrid_forecast.py` → `run_forecasting.py`), used as the reference point for
all future changes.

**Data:** `features/weekly_features.parquet` rebuilt from
`processed_output/clean_daily_sales.parquet` — 100 SKUs, 15,700 SKU-weeks,
9,782 complete (trainable) weeks, all `uom_class == units` (no `weight`-class
SKUs survive DQ in the current data, so the weight/Tweedie model is skipped —
`reports/evaluation_report.json` shows `"weight": {"skipped": true, "reason":
"empty dataset"}`; `artifacts/lgbm_weight.txt` on disk is stale, from an
earlier data snapshot, and is not used by `run_forecasting.py`).

**Metrics (holdout, hybrid = baseline LightGBM + promotion-uplift model):**

| Metric | Baseline (units LightGBM, no promo) | Hybrid (+ promotion uplift) |
|---|---|---|
| Overall WMAPE | 28.588% | 27.966% |
| Promoted-SKU WMAPE (overlap_days > 0, 584 rows) | 31.133% | 30.048% |
| Non-promoted WMAPE (overlap_days = 0, 460 rows) | 25.173% | 25.173% (uplift not applied when there's no promo overlap, so identical to baseline) |
| 15-day backtest WMAPE (as-of 2026-08-26, horizon 2026-08-27→2026-09-10, actuals vs. forecast, 99 SKUs) | 25.05% | 24.204% |

Supporting numbers:
- Single walk-forward split model (`train_lgbm.py`, train/8-wk val/13-wk
  holdout, 1-wk gap): holdout WMAPE **28.725%** (`reports/evaluation_report.json`).
  This differs slightly from the 28.588% baseline above because the hybrid
  evaluation uses `train_lgbm.py`'s rolling 4-week-retrain walk-forward OOS
  predictions (`processed_output/baseline_oos_predictions.parquet`), not the
  single fixed-split model — both are legitimate views, reported here to
  avoid confusion if they're compared later.
- Promotion model trained on 1,454 usable weeks (764 `PROMOTION CONFIRMED`,
  690 `PROMOTION NOT CONFIRMED`); rows before promo data starts (Oct 2024)
  are `UNKNOWN` and excluded from training.

**Sanity check:** improvement is modest (~0.6 pts overall, ~1.1 pts on
promoted SKUs) — no leakage flag needed (nowhere near the >5-point jump that
would trigger a leakage check).

**Verdict:** kept (this is the reference baseline itself, not a candidate
change).

## 2026-09-16 — v1: promotion + Victorian calendar weekly features (kept)

**Change:**
1. Rewrote `ingest_promotions.py` to read the real promo feed
   (`data/Pearcedale_Metcash_100_Promotions.json`, previously the script
   pointed at a consolidated JSON that doesn't exist in this checkout) and
   build a daily per-SKU table (`processed_output/promotions_daily.parquet`)
   per the classification rules in `promo_data_profile.md`: Instore/slot-1 =
   regular price, Hosted named types + 7-day `gmGmcUpd` = qualifying promo,
   21/91-day `gmGmcUpd` and Instore/slot-4 excluded. Stacked promos on the
   same day are resolved by deepest discount, not longest window (fixes the
   overlap risk flagged in that doc's §5).
2. Added 12 weekly features to `preprocess_features.py`, joined from that
   daily table: `promo_days_in_week`, `promo_discount_pct_max/mean`,
   `promo_stacked_max`, `promo_type_group_week`, `price_ratio` (mean
   effective price / mean regular price), `promo_pre_week` (next week),
   `promo_post_week1`/`promo_post_week2` (last 1-2 weeks), plus
   `promo_status_week` (CONFIRMED/NOT_CONFIRMED/UNKNOWN, added for
   correctness — see below) and Victorian `has_public_holiday` /
   `is_school_term` flags (reference data: `data/vic_public_holidays.csv`,
   `data/vic_school_terms.csv`, verified against Business Victoria / VIC
   Dept. of Education sources for 2023-2026).
3. All promo-derived weekly columns are **null**, not zero/false, for weeks
   where `promo_status_week == UNKNOWN` (majority of days before
   2024-10-02, or any of the 13 SKUs with no promo record at all) — an
   unknown promo state must never look like a confirmed "no promo," per
   the project's existing null-handling convention.
4. `promo_pre_week` (next week's promo activity) is intentionally
   forward-looking — promotions are planned in advance, so this is not
   leakage, matching the user's explicit instruction. All lag/rolling
   *sales* features are unchanged and still strictly t-1 and earlier.
5. Updated `FEATURE_COLS`/`CATEGORICAL` in `train_lgbm.py` and
   `run_forecasting.py` to match (required for the retrained model to load;
   `generate_po_report.py` untouched per the hard rule — it selects a fixed
   subset of columns that's still present, so it's unaffected).

**Metrics (same holdout as v0, 2026-06-08 to 2026-09-07, 1,044 rows):**

| Metric | v0 baseline | v1 (+ promo/calendar features) | Δ |
|---|---|---|---|
| Overall WMAPE — baseline LightGBM | 28.588% | **26.929%** | −1.66 pts |
| Overall WMAPE — hybrid | 27.966% | **26.056%** | −1.91 pts |
| Promoted-SKU WMAPE — baseline | 31.133% | **29.484%** | −1.65 pts |
| Promoted-SKU WMAPE — hybrid | 30.048% | **27.961%** | −2.09 pts |
| Non-promoted WMAPE (baseline = hybrid) | 25.173% | **23.502%** | −1.67 pts |
| 15-day backtest WMAPE — baseline (as-of 2026-08-26) | 25.05% | 25.35% | +0.30 pts (regression, see note) |
| 15-day backtest WMAPE — hybrid (as-of 2026-08-26) | 24.20% | **23.32%** | −0.88 pts |
| Single-split holdout WMAPE (`train_lgbm.py` fixed split) | 28.725% | **28.398%** | −0.33 pts |

Supporting: `promo_days_in_week`, `promo_discount_pct_mean`, and especially
`price_ratio` all landed in the retrained model's top-10 feature importances
(displacing some of the rolling-std features from v0's top 10) — the model
is actually using the new signal, not just adding noise.

**Sanity check:** all improvements are in the 0.3-2.1 point range — nowhere
near the >5-point jump that would call for a leakage check.

**Known regression / limitation (not fixed in this change):** the 15-day
*baseline-only* backtest number got very slightly worse (+0.30 pts). Root
cause: `run_forecasting.py`'s `build_baseline_15day()` reuses the *last
complete week's* feature row to stand in for the forecast horizon (a
pre-existing approximation, harmless for slow-moving lag/rolling sales
features). It is not a good approximation for `promo_days_in_week`,
`price_ratio`, `has_public_holiday`, etc., which genuinely differ week to
week — last week's promo/calendar status is not this week's. This is
flagged with a code comment in `run_forecasting.py` at `FEATURE_COLS`.
The *hybrid* backtest number (what actually drives the PO decision) still
improved, because the separate promotion-uplift step already applies the
correct future-promotion info on top of the baseline. Fixing the baseline
horizon-row construction to recompute promo/calendar features for the
actual forecast dates is a good candidate for a follow-up change.

**Verdict:** kept — holdout WMAPE improved (the primary gate) across
overall, promoted, and non-promoted slices, and the hybrid 15-day backtest
also improved. The baseline-only 15-day backtest regression is small,
understood, and documented above rather than hidden.

## 2026-09-16 — experiment: single promo-aware model vs. baseline+uplift hybrid

**Change:** none to the pipeline — this is a head-to-head comparison of two
approaches already present in the codebase, using artifacts already
produced by v1. No code was modified; `run_forecasting.py` was not touched.

- **"Single model"** = the v1 `units` LightGBM model on its own
  (`promo_days_in_week`, `promo_discount_pct_*`, `price_ratio`, etc. are
  already features of this one model — see v1 above). This is the
  `baseline_forecast` / `baseline_forecast_15d` column already produced by
  `train_lgbm.py`'s walk-forward OOS generator and by `run_forecasting.py` —
  no new run was needed, just evaluated on its own instead of downstream of
  the uplift step.
- **"Hybrid"** = that same single model's output, then multiplied by
  `train_promotion_model.py`'s separately-trained uplift model
  (`hybrid_forecast` / `hybrid_forecast_15d`), i.e. the current production
  path.

This directly tests whether the separate uplift layer is adding real value
on top of a model that already has promo features baked in, or just
double-counting the same signal (the risk flagged at the end of the v1
entry above).

**Holdout (2026-06-08 to 2026-09-07, 1,044 rows, 584 promoted / 460 non-promoted):**

| | Single model | Hybrid | Δ |
|---|---|---|---|
| Overall WMAPE | 26.929% | **26.056%** | −0.87 pts |
| Promoted-SKU WMAPE | 29.484% | **27.961%** | −1.52 pts |
| Non-promoted WMAPE | 23.502% | 23.502% | 0 (identical by construction — uplift never fires without an active promo) |

**15-day backtest (as-of 2026-08-26, horizon 2026-08-27→2026-09-10, 99 SKUs, 73 promoted / 26 non-promoted):**

| | Single model | Hybrid | Δ |
|---|---|---|---|
| Overall WMAPE | 25.351% | **23.320%** | −2.03 pts |
| Promoted-SKU WMAPE | 25.154% | **22.504%** | −2.65 pts |
| Non-promoted WMAPE | 25.997% | 25.997% | 0 (same reason) |

**Reading:** the hybrid wins everywhere it can possibly differ from the
single model (promoted SKUs, both eval windows) and is never worse. The gap
is bigger on the 15-day backtest (−2.65 pts on promoted SKUs) than on the
weekly holdout (−1.52 pts), i.e. the uplift layer's edge holds up, it
doesn't evaporate, when evaluated the way the model is actually used in
production. Non-promoted WMAPE is identical in both windows, which is the
expected null result, not a data bug — it's a direct check that the uplift
step is not touching rows it shouldn't.

This updates my note at the end of the v1 entry: the two-stage design
(promo-aware baseline + a separate uplift model) is **not** double-counting
the promo signal in a harmful way. The uplift model is trained on the
*residual* between the promo-aware baseline's own prediction and actual
demand (`actual_uplift = actual/baseline_OOS − 1`, in
`train_promotion_model.py`), so it's correcting for promo effects the
tree-based baseline still misses — likely discount-depth and promo-type
interactions that `price_ratio`/`promo_discount_pct_mean` only partially
capture as linear-ish tree splits. That correction is real, not noise.

**Recommendation: keep the current two-stage baseline+uplift hybrid.**
Collapsing to a single model with no uplift layer would give up 0.9-2.7
points of WMAPE with no upside (it's simpler, but the codebase already pays
the complexity cost of maintaining both models, so there's no simplicity
to be gained by removing the weaker option). No pipeline change made;
`run_forecasting.py` continues to run the hybrid path it already runs today.

**Verdict:** informational — no change to keep or reject, current
production path (hybrid) confirmed as the better of the two options tested.

## 2026-09-16 — experiment 1/4: separate models per volume band (rejected)

**Change tried:** instead of one global `units` LightGBM model, split SKUs
into fast/medium/slow volume bands (tertiles of mean weekly qty, computed
from **pre-holdout** weeks only so the band assignment itself can't leak
holdout demand) and train 3 independent models, one per band, same
hyperparameters and features as the current global model. Applied to both
the walk-forward OOS generator (feeds the main holdout/hybrid metric) and
the "official" `train_group()` model + `run_forecasting.py` (feeds the
15-day backtest) — band assignment persisted to
`artifacts/volume_bands.json`, SKUs routed to their band's booster at
inference.

**Metrics vs. current best (v1 hybrid):**

| | Holdout overall | Holdout promoted | Holdout non-promo | Backtest overall | Backtest promoted |
|---|---|---|---|---|---|
| Current best (v1) | 26.06% | 27.96% | 23.50% | 23.32% | 22.50% |
| Per-band models | 26.36% | 28.36% | 23.68% | 24.22% | 23.75% |
| Δ | +0.31 pts (worse) | +0.40 pts (worse) | +0.18 pts (worse) | +0.90 pts (worse) | +1.25 pts (worse) |

Worse on every single metric, no exceptions.

**Why:** splitting the ~8,125 training rows / 99 SKUs three ways leaves
each band's model with meaningfully less data (and the `itemCode`
categorical has less signal to work with per band too) — the same failure
mode the project's own `model/README.md` already documented for the old
`weight` model ("not enough data to outlearn a simple average" at ~800
rows). The specialization a per-band model could in principle offer isn't
worth what it gives up in training-set size here. My error-analysis report
had already hinted at this: the global model's slow/medium/fast gap
(28.4%/25.5%/25.4%) was real but modest, and the actual worst-SKU driver
was promo-response heterogeneity, not volume-band-level mis-specification —
so this experiment was targeting a smaller problem than the one visible in
the error breakdown, and the data cost of splitting outweighed the
targeted gain.

**Verdict: rejected, fully reverted.** `train_lgbm.py` and
`run_forecasting.py` are back to the single global `units` model (confirmed
by re-running the eval harness and reproducing the v1 numbers exactly).
Orphaned per-band artifacts deleted.

## 2026-09-16 — experiment 2/4: exclude likely-stockout days from the training target (rejected)

**Change tried:** in `preprocess_features.py`, flag a daily row as a
"likely stockout" when `quantity == 0` while the SKU's trailing 28-day
average was >= 1.0 units/day (i.e. a zero landing in an otherwise
actively-selling period, not a naturally slow SKU's normal zero day), then
null it out before weekly aggregation — reusing the existing
unobserved-day machinery (excluded from both `qty_sum` and
`observed_days`, same treatment as a missing quantity).

**First finding, before even getting to metrics: there's almost nothing here to exclude.**
Genuine zero-quantity days are already extremely rare in this dataset —
44 rows out of 72,626 non-null daily observations (0.06%) across all 100
SKUs over 3 years, and every SKU's mean daily quantity sits at 2-12 units/
day (min 1.98). This is a fundamentally different situation from a typical
zero-inflated retail dataset. The threshold flagged 43 of those 44 as
"likely stockout."

**Metrics vs. current best (v1 hybrid):**

| | Holdout overall | Holdout promoted | Holdout non-promo | Backtest overall | Backtest promoted |
|---|---|---|---|---|---|
| Current best (v1) | 26.06% | 27.96% | 23.50% | 23.32% | 22.50% |
| Exclude likely stockouts | 25.96% | 27.70% | 23.63% | 23.35% | 22.49% |
| Δ | −0.10 pts (better) | −0.26 pts (better) | +0.13 pts (worse) | +0.03 pts (worse) | −0.01 pts (flat) |

**Also measured: the pipeline's own run-to-run noise floor.** Re-running
the *unchanged* v1 code back-to-back (no code difference at all) moved
holdout overall WMAPE by ~0.01-0.02 points on its own — LightGBM's
multi-threaded histogram building isn't bit-exact across runs even with a
fixed `random_state` unless `deterministic=True` is also set. This
experiment's effect (0.03-0.26 points, in inconsistent directions between
the two eval windows) is only a little larger than that noise floor and
touches 0.06% of the data — not distinguishable from noise with any
confidence.

**Verdict: rejected, fully reverted.** Mixed signal (better on the main
holdout, marginally worse on the 15-day backtest) at a magnitude close to
measurement noise is not "helps" by any reasonable bar, and there's
essentially no zero-inflation problem in this dataset for the idea to fix
in the first place. `preprocess_features.py` reverted to the v1 version;
confirmed by re-running the eval harness.

## 2026-09-16 — experiment 3/4: hyperparameter tuning on the validation window only (rejected)

**Change tried:** a small grid search (9 combos over `num_leaves` in
{15,31,63}, `learning_rate` in {0.02,0.03,0.05}, `min_child_samples` in
{10,20,40}, current defaults included as a baseline) scored strictly on
the existing 8-week/539-row validation window — holdout never touched by
the search, same walk-forward split the pipeline already uses for early
stopping. The winning combo was applied to *both* the model
`run_forecasting.py` loads (`train_group()`) and the rolling-retrain
generator that produces the main holdout/hybrid metric
(`generate_walk_forward_oos()`), so the tuning result would actually show
up in both eval windows, not just one.

**Winner on validation:** `{num_leaves: 63, min_child_samples: 40,
learning_rate: 0.02}`, val WMAPE 25.91% (better than the untuned default's
val score). Bigger, more conservative trees, slower learning rate.

**Metrics vs. current best (v1 hybrid):**

| | Holdout overall | Holdout promoted | Holdout non-promo | Backtest overall | Backtest promoted |
|---|---|---|---|---|---|
| Current best (v1) | 26.05% | 27.94% | 23.50% | 23.34% | 22.53% |
| Validation-tuned | 26.62% | 28.02% | 24.74% | 23.62% | 22.60% |
| Δ | +0.56 pts (worse) | +0.06 pts (worse) | +1.24 pts (worse) | +0.30 pts (worse) | +0.10 pts (worse) |

Worse everywhere, most sharply on non-promoted holdout rows (+1.24 pts).

**Why:** the config that won on validation didn't win anywhere else. The
validation window is a single, non-overlapping 8-week/539-row slice
(`evaluation_report.json`'s own numbers) — small enough that a config
which happens to fit its particular noise pattern can look best there
while generalizing worse than the simpler default everywhere else. This is
the textbook failure mode of tuning against one small, non-repeated
validation split rather than something like repeated/rolling
cross-validation — exactly what happened here: `num_leaves=63` gives the
tree more room to fit that one window's idiosyncrasies at the cost of
holdout and backtest generalization.

**Verdict: rejected, fully reverted.** Followed the instruction precisely
(tuned on validation only, holdout never touched by the search) and it
still made things worse everywhere it was checked — a useful negative
result: on a validation window this small, the existing hand-set defaults
already outperform a grid search against it. `train_lgbm.py` reverted to
the v1 version; confirmed by re-running the eval harness (numbers match v1
within the ~0.01 pt run-to-run noise floor documented in experiment 2).

## 2026-09-16 — experiment 4/4: blended ensemble, LightGBM + Ridge (rejected)

**Change tried:** a second model - `sklearn.linear_model.Ridge` (alpha=10)
on the numeric features (all of `FEATURE_COLS` minus the categoricals,
median-imputed from train) plus one-hot encoded `itemCode` - blended with
the existing LightGBM model: `pred = w * lgbm_pred + (1-w) * ridge_pred`.
`w` was grid-searched over {0.0, 0.1, ..., 1.0} scored on the validation
window only (same split as experiment 3), then applied as a **fixed**
weight everywhere the baseline model is used: refit inside
`generate_walk_forward_oos()`'s rolling windows (feeds the main holdout
metric), and via a persisted `artifacts/ridge_ensemble_units.joblib` loaded
by `run_forecasting.py` (feeds the 15-day backtest).

**Winner on validation:** `w = 0.6` (60% LightGBM / 40% Ridge) - beat pure
LightGBM on that one validation window.

**Metrics vs. current best (v1 hybrid):**

| | Holdout overall | Holdout promoted | Holdout non-promo | Backtest overall | Backtest promoted |
|---|---|---|---|---|---|
| Current best (v1) | 26.05% | 27.94% | 23.50% | 23.34% | 22.53% |
| LightGBM + Ridge blend | 26.56% | 28.70% | 23.70% | 24.27% | 24.00% |
| Δ | +0.51 pts (worse) | +0.76 pts (worse) | +0.20 pts (worse) | +0.93 pts (worse) | +1.47 pts (worse) |

Worse on 9 of the 10 metric/window combinations checked across this and
the earlier table (backtest non-promoted was the one exception, −0.78 pts
better) - not close to worth it for a strictly worse result everywhere
that matters more.

**Why:** the same failure mode as experiment 3, one level up - `w=0.6`
was the blend that happened to fit the single 539-row validation window
best, not the blend that generalizes. Ridge with one-hot `itemCode` and
median-imputed promo/price features is also a considerably weaker model
than LightGBM on its own here (linear, can't use the tree splits that make
`price_ratio`/`promo_discount_pct_mean` useful - see the v1 feature
importances), so blending in a meaningful weight of it was never likely to
help regardless of how the weight was chosen; the validation-window
selection made it look like it would.

**Verdict: rejected, fully reverted.** `train_lgbm.py` and
`run_forecasting.py` reverted to the v1 versions; orphaned
`ridge_ensemble_*.joblib` artifacts deleted; confirmed by re-running the
eval harness.

## Summary of experiments 1-4

All four were tried as instructed and none improved on v1 - each is
documented above with why, not just a pass/fail. The consistent thread
across 3 of the 4 (per-band models, validation-tuned hyperparameters, the
ensemble blend) is that **the validation window this pipeline uses (8
weeks / ~539 rows, one non-overlapping slice) is too small and too
specific to reliably select among close alternatives** - every experiment
that asked it to choose something (a hyperparameter combo, a blend weight)
picked an option that looked better on that one window and generalized
worse. The one exception (stockout exclusion) failed for an unrelated
reason: there's essentially no zero-inflation in this dataset for the idea
to fix. **Current best remains v1** (baseline LightGBM with promo/calendar
features, hybrid-adjusted by the separate uplift model) - unchanged by
this round.

## 2026-09-16 — run_forecasting.py: horizon-aware promo/calendar features at inference (kept)

**Change:** v1's model architecture was already confirmed best (above),
but `run_forecasting.py`'s live inference path had a bug independent of
which model architecture was used: `build_baseline_15day()` predicted the
15-day horizon by taking the *last complete week's* full feature row and
scaling the prediction by 15/7. For the lag/rolling sales features that's
a reasonable, unavoidable approximation (sales aren't known in advance).
For the promo/calendar features added in v1, it meant the model was told
about *last week's* promotions and calendar position while actually
forecasting the *next two weeks* - exactly the gap flagged as a follow-up
in the v1 entry above.

Fixed by predicting the two ISO weeks that cover the 15-day horizon
separately and summing them (scaled 14→15 days), each with its own
correctly-computed promo/calendar features:
- `ingest_promotions.py`'s calendar scaffold now extends through the
  latest *scheduled* promo end date (was: only through the last historical
  sales date), so `promotions_daily.parquet` has rows for future weeks a
  forecast run needs to look up.
- `run_forecasting.py` gained `build_future_promo_weekly()` (same
  aggregation as `preprocess_features.py`'s promo weekly features, applied
  to the now-future-extended daily table) and `calendar_features_for_week()`
  (pure date lookups against the VIC reference CSVs) to compute real values
  for each of the two target weeks, instead of reusing last week's.
  Lag/rolling sales features and SKU metadata are unchanged - still carried
  over from the latest complete week, since sales aren't knowable in
  advance the way promotions and the calendar are.

No change to output file names/columns, no change to the PO logic, no
change to the two-model hybrid architecture itself - this only fixes what
features that architecture is fed with at inference time.

**Metrics - 15-day backtest, as-of 2026-08-26, before/after this fix (same
holdout metrics above are unaffected - this only touches the live
inference path, not training):**

| | Before (stale last-week features) | After (horizon-aware) | Δ |
|---|---|---|---|
| Baseline overall WMAPE | 25.35% | **23.58%** | −1.77 pts |
| Hybrid overall WMAPE | 23.34% | **22.53%** | −0.81 pts |
| Hybrid promoted WMAPE | 22.53% | **21.37%** | −1.16 pts |
| Hybrid non-promoted WMAPE | 26.00% | 26.34% | +0.34 pts (small regression) |

Also verified: ran normally (as-of today) and with `--as-of` 4 weeks
before today - both completed without error, `outputs/` file names and
columns unchanged, no negative/null forecast values, predicted magnitudes
in line with actual demand for the backtest window.

**Verdict:** kept - meaningful improvement (0.8-1.8 points) on the metric
that actually reflects production usage, with only a small non-promoted
regression that doesn't offset it.
