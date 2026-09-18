# Product Sales Forecasting 📈

This repository houses the forecasting models used to predict product sales demand. It utilizes a robust dual-architecture approach, allowing us to leverage both domain-specific features (pricing, promotions) and zero-shot univariate pattern recognition.

---

## 1. The Core Engine: LightGBM (`pearcedale-lgbm-model/`)

Our primary forecasting model is a highly optimized, gradient-boosting pipeline built on **LightGBM**. It predicts demand at the weekly level using a "global model" approach (training one model across all SKUs per unit/weight group).

### How it Works
* **Objective:** Uses Poisson regression for discrete unit counts.
* **Covariates (Features):** The model is fed a rich dataset of engineered features, including historical autoregressive lags (1, 2, 4, 8, 13, 52 weeks), rolling statistics, and crucial business context like:
  * `promo_days_in_week`, `promo_discount_pct_mean`, `price_ratio`
  * Calendar effects (`cal_month`, `woy_sin`, `has_public_holiday`)
* **Performance:** High accuracy on core SKUs (~28.4% WMAPE). It natively understands that a massive price drop or holiday will spike demand.
* **Weakness:** "Cold Starts." It struggles with brand-new SKUs because it requires months of historical lags to generate features.

---

## 2. The Foundation Backup: Chronos-2 (`chronos_experiment/`)

To supplement LightGBM, we run **Chronos-2** (`amazon/chronos-2`, 120M parameters), a zero-shot time-series foundation model developed by Amazon. Chronos treats time-series forecasting like language modeling, tokenizing the historical sales curve and predicting the next "tokens".

### How it Works
* **Objective:** Generates daily point forecasts intrinsically mapped from 21 probability quantiles (using the 50th percentile / median).
* **Univariate:** It only looks at the historical sales quantity (`qty`). It knows *nothing* about prices, promotions, or holidays.
* **Handling Unknown Demand:** Missing POS records (`NaN`s) are preserved. Chronos-2 elegantly handles these via its attention mask, avoiding the artificial deflation of error that comes from zero-filling.
* **Performance:** Reasonable zero-shot accuracy (~47.7% WAPE). It struggles heavily with intermittent items but is highly capable on consistent sellers. 
* **Weakness:** It cannot predict abnormal spikes driven by external factors (e.g., an unexpected 50% off sale) because it lacks covariate inputs.

---

## 3. Working Together: The Hybrid Strategy 🤝

In production, these two models should not compete; they should collaborate. Here is how they are orchestrated to work together:

### Strategy A: Ensembling (Chronos as a Feature)
Instead of treating Chronos-2 as a separate endpoint, its predictions (the median and upper 90th percentile bounds) can be injected as **new columns** into the LightGBM training dataset. 
* **The Result:** LightGBM automatically learns when to trust Chronos-2's curve extrapolation and when to override it based on promotional covariates. 

### Strategy B: The Two-Tower Fallback
A routing logic layer sits in front of the models. 
* **Condition 1 (Fast Movers & Promo Items):** For SKUs with robust history (> 1 year) and active promotional schedules, the request routes to **LightGBM**.
* **Condition 2 (Cold Starts):** For brand-new SKUs (e.g., launched 3 weeks ago), LightGBM will fail because it lacks 52-week lag features. The router detects this and sends the request to **Chronos-2**, which excels at few-shot forecasting from very short historical contexts.

### Strategy C: Anomaly Detection Guardrails
Both models run simultaneously. If LightGBM predicts a 300% spike but Chronos-2 predicts a flatline, the system triggers an alert. Often, this reveals corrupted data in the upstream pipeline (e.g., a "flagged promotion" that isn't actually happening), acting as an automated sanity check.
