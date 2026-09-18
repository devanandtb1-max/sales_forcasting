# Forecasting models: current evaluation baseline

Document ID: pearcedale-forecast-baseline-v1
Prepared: 2026-09-17
Status: User-supplied model report, not independently verified; update after retraining.
Source: User-supplied "Forecasting Models Comparative Report: LightGBM vs. Chronos-2".
Scope: Results described for the earlier evaluation, apparently involving 100 SKUs for Chronos-2. This is not an evaluation of a future 100,000-SKU model.

## What was evaluated?

The report describes two demand forecasting approaches:

- LightGBM: a trained regression model using sales-history features, price and promotion features, calendar information, and product categories. It produces weekly forecasts. The report describes a global model trained across SKUs rather than a separate model for every SKU.
- Chronos-2: a zero-shot model used on historical daily sales quantities. In the reported experiment it did not receive promotion, price, holiday, or product-category inputs. The reported point forecast used its median prediction.

These are demand forecasts. Neither model calculates an official purchase order quantity; the separate PO engine does that.

## What were the reported test results?

The LightGBM report states a 13-week holdout, an overall WMAPE of 28.4%, and a final eight-week validation span for early stopping. Its listed influential features are itemCode, lag_2, price_ratio, lag_1, and promo_days_in_week. The report does not provide the raw prediction and actual-value pairs, exact metric formula, per-SKU errors, or run identifier needed to reproduce the score here.

The Chronos-2 report states a final 15-day holdout for 100 SKUs, with 1,184 valid observation pairs. It reports WAPE of 47.74%, MAE of 1.75 units per day, and bias of -23.77%, described as underforecasting. It also reports 45.0% WAPE for consistent-selling SKUs and 66.3% WAPE for intermittent-selling SKUs. The report says missing POS days were left as unknown values and excluded from the reported holdout metric.

WAPE and WMAPE are error measures; lower values are preferable when computed consistently on the same test observations. The supplied report does not define the exact formulas. The two reported percentages come from different time resolutions and holdout periods, so they should not be treated as a controlled, directly comparable model ranking.

## What is supported about promotions?

LightGBM's reported feature list includes promotion and price inputs. Chronos-2 did not use these inputs in the described experiment. This establishes a difference in available information, but the report does not isolate how much of either model's error was caused by promotion handling. The separate promotion export has unverified historical completeness. Do not claim a specific promotion caused a particular forecast unless the model input and explanation for that run are available.

## Which proposals remain untested?

The report suggests using Chronos-2 predictions as LightGBM features, trying Chronos-2 for new products, and flagging large disagreement between models for review. These are proposals, not measured improvements or approved production rules. The claim that one model is better for new products needs a dedicated cold-start test.

## What changes after retraining?

After retraining on a larger SKU set, create a new document version with the model version, data cutoff, SKU coverage, forecast horizon, evaluation dates, metric definitions, overall and segment results, and whether planned promotions were actually available at prediction time. Keep this document as a dated historical baseline, or retire it from active retrieval so Claude does not quote an old score as current performance.

For a current SKU question, retrieve that SKU's current prediction, prediction date, and official PO result from structured data. This report cannot supply those changing values.
