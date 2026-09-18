# Pearcedale Demand Forecasting — Agent Rules

## Project
Hybrid LightGBM demand forecast (15-day horizon) for 100 SKUs at the Pearcedale IGA store
(Victoria, Australia), feeding a PO recommendation engine. Entry point for n8n:
`python model/run_forecasting.py` (supports `--as-of YYYY-MM-DD`).

## Hard rules
- NEVER modify `model/generate_po_report.py` or the PO business logic (MOQ, carton rounding,
  stockout thresholds, decision rules).
- No data leakage: features may only use data from t-1 and earlier. Chronological splits only,
  never random shuffles.
- Holdout = last 13 weeks, never used for training or tuning.
- Keep output file names and columns in `outputs/` unchanged.
- `run_forecasting.py` must remain a single command.
- Before large changes, write a plan and wait for approval.

## Evaluation (required for every change)
Report on the SAME holdout, compared with the previous version:
overall WMAPE, promoted-SKU WMAPE, non-promoted WMAPE, 15-day backtest accuracy.
Append results to `model/reports/experiments.md` (date, change, metrics, kept/rejected).
Keep a change only if holdout WMAPE improves. If accuracy jumps suspiciously (>5 points),
check for leakage before reporting.

## Promotion data facts
- Files: `data/Pearcedale_Metcash_100_Promotions.json`, `data/Pearcedale_Metcash_Promotion_Coverage.json`
- origin = "Hosted" → weekly promo events (median ~6 days; types like IGA Ad Leader, Advert Lines,
  Special Feature – Weekly, Multi Buys).
- origin = "Instore", slot 1, ~89 days, no type → likely the regular shelf price, NOT a promotion (verify).
- "Added by gmGmcUpd" (~1,437 records) is an unclear system label — verify before treating as a promo.
- unitCost = supplier cost, NOT regular retail price. Discount = (regular_price − promo_sell) / regular_price.
- Cost periods (costStart/costEnd) and sell periods (sellStart/sellEnd) are different; the sell period drives demand.
- Promo data starts Oct 2024; earlier weeks are UNKNOWN (not "no promo").
- 13 of the 100 SKUs have no promo records.
- Dates are UTC; convert to Australia/Melbourne.
