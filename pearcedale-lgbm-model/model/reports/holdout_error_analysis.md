# Holdout error breakdown — hybrid model (baseline + uplift)

Analysis only — no code changed. All numbers are the hybrid model (confirmed
the better of the two options in the previous experiment) on the same
13-week holdout (2026-06-08 to 2026-09-07, 99 SKUs).

## Data-quality footnote that affects every breakdown below

The **last holdout week (2026-09-07 to 2026-09-13) is a partial week** —
`clean_daily_sales.parquet` ends 2026-09-10, so only 4 of that week's 7
calendar days exist as rows at all. `preprocess_features.py`'s
`is_complete_week` check (`observed_days / days_in_week >= 0.70`) computes
completeness against *available rows*, not the full calendar week, so this
week reads as "100% complete" while actually only capturing ~4/7 of a
week's demand. Comparing a full-week prediction against a 4/7-week actual
inflates error for every SKU that week — overall holdout WMAPE for that one
week alone is 52.9%, vs. 20-30% for every other week. **All SKU/volume/promo
breakdowns below exclude this week** (overall WMAPE 26.06% → 24.64% once
it's dropped). This is a real bug worth fixing in `preprocess_features.py`
(compare against calendar days, not row count) independent of anything else
in this report — flagged, not fixed, per this task's scope.

## Breakdown by sales-volume band

Bands are SKU volume tertiles from **pre-holdout** history only (so "fast/
medium/slow" isn't circularly defined using the evaluation period).

| Band | SKUs | Holdout rows | WMAPE |
|---|---|---|---|
| Slow (<17.0 units/wk) | 33 | 309 | **28.4%** |
| Medium (17.0-21.8) | 32 | 340 | 25.5% |
| Fast (≥21.8 units/wk) | 34 | 395 | 25.4% |

Slow movers are worst, as expected for count data — low weekly volumes mean
each unit of absolute error is a bigger percentage. This isn't unique to
this model; it's close to the Poisson noise floor the project's own
`model/README.md` already calls out for low-volume SKUs.

## Breakdown by promo status / promo type

| Segment | Rows | WMAPE |
|---|---|---|
| NOT_CONFIRMED (no promo, visibility available) | 309 | **21.5%** |
| UNKNOWN (pre-Oct 2024 or no promo history) | 132 | 25.1% |
| CONFIRMED (promo active) | 603 | **28.6%** |

Promo weeks are both the majority of the holdout (58%) and the worst
segment — 7 points worse than confirmed non-promo weeks. Within confirmed
promo weeks, by type:

| promo_type_group | Rows | WMAPE |
|---|---|---|
| multibuy | 6 | 40.3% (n too small to trust) |
| advert | 41 | 32.0% |
| ad_leader | 122 | 30.9% |
| weekly_special | 113 | 28.4% |
| other | 321 | 27.3% |

No promo type is "fine" — all of them sit above the non-promo baseline.
`ad_leader` and `advert` are the worst of the well-populated types.

## Breakdown by week

13 of 14 weeks sit in a tight 19.8%-30.3% band with no trend. Week
2026-09-07 (52.9%) is the partial-week artifact described above, not a
genuine forecasting failure — excluded from the rest of this report.

## The 15 worst SKUs (by holdout WMAPE, partial week excluded)

Together these 15 (of 99) account for **24.3% of total absolute holdout
error** — a real concentration, but fixing them alone wouldn't fix the
promo-week problem overall, since that shows up broadly across most SKUs.

| itemCode | commodity | band | weeks | WMAPE | bias | promo wks |
|---|---|---|---|---|---|---|
| STR000058215 | ENERGY DRINKS | slow | 6 | 75.0% | over +9.6 | 6/6 |
| STR000008243 | CHOCOLATES | medium | 5 | 66.9% | under −5.6 | 5/5 |
| STR000037788 | SOFT DRINKS, MIXERS | fast | 7 | 59.0% | over +3.7 | 7/7 |
| STR000029494 | SPORTS DRINKS | fast | 11 | 54.7% | under −3.1 | 11/11 |
| STR000019393 | CHOCOLATES | medium | 9 | 46.0% | under −1.0 | 9/9 |
| STR000033143 | SNACKS AND SAVOURIES | medium | 9 | 44.9% | over +4.6 | 7/9 |
| STR000019905 | CONFECTIONERY | fast | 10 | 44.4% | ~flat | 4/10 |
| STR000021775 | CHOCOLATES | fast | 10 | 44.3% | under −8.4 | 9/10 |
| STR000087937 | CHOCOLATES | slow | 5 | 43.3% | under −7.3 | 4/5 |
| STR000024567 | BUTTER | fast | 13 | 41.5% | over +2.3 | 3/13 |
| STR000020125 | CHOCOLATES | fast | 12 | 38.3% | over +4.8 | 11/12 |
| STR000055678 | FISH CANNED, TUNA | medium | 1 | 37.8% | under −12.5 | 1/1 |
| STR000009285 | CIGARETTE LIGHTERS | slow | 11 | 37.5% | over +1.6 | 0/11 |
| STR000086878 | PET FOOD, CHILLED | slow | 9 | 37.5% | under −1.3 | 0/9 |
| STR000020126 | CHOCOLATES | slow | 7 | 37.2% | over +4.1 | 7/7 |

### Why each is missed

**Promo-response heterogeneity — the pooled uplift model doesn't fit this SKU's real response (10 of 15: STR000058215, STR000008243, STR000037788, STR000029494, STR000019393, STR000033143, STR000021775, STR000087937, STR000020125, STR000020126).**
For all of these, nearly every holdout week is a confirmed promo week
(4/5 to 13/13), and per-week actuals swing far more than the prediction
does — e.g. STR000021775 hits actual=138 on a week predicted at 51, then
actual=82 vs predicted 40 two weeks earlier; STR000058215 is *always*
over-predicted during its `weekly_special`/`other`/`ad_leader` promo weeks
(every single one, 6/6). `train_promotion_model.py`'s uplift model has
**no SKU or commodity identity in its features at all** — only
`discount_pct`, `overlap_days`, `promo_type`. It learns one average uplift
curve per promo type, pooled across every SKU. These SKUs are the ones
whose real promo response is furthest from that pooled average — some lift
much less than the average discount would predict (over-predicted), some
lift much more (under-predicted). This is the dominant pattern in the list
by count.

**Volatile demand not explained by promotions (STR000024567, STR000009285, STR000086878, and part of STR000019905's error).**
STR000009285 (cigarette lighters) and STR000086878 (pet food, chilled) have
**zero** confirmed promo weeks in the entire holdout, yet actuals still
bounce (lighters: 6-23 units/week; pet food: 6-33) against a nearly flat
~14-15 prediction. STR000024567 (butter) is actually *worse* off-promo
(51.0% WMAPE, n=10) than on-promo (25.0%, n=3) — its volatility has nothing
to do with promotions. STR000019905 shows the same split (non-promo 66.6%
vs. promo 28.5%). These look like plain demand noise/intermittency the
lag/rolling features aren't capturing, not a promo-specific gap.

**Mid-holdout price change (STR000033143).**
This is the one SKU with more than one distinct `regular_price` observed
during the holdout window ([2.50, 2.75, 3.49]) — a real price change
partway through, not a data artifact. `discount_pct` and `price_ratio` are
computed against whichever regular price was known at the time via
forward-fill, so a genuine mid-window price change is exactly the case
those features are weakest at reacting to quickly.

**Too little holdout data to trust the number (STR000055678).**
Only 1 holdout week survived the completeness filter for this SKU. A WMAPE
computed on n=1 is not a reliable signal either way — it shouldn't be
acted on without more weeks of data, though it isn't wrong to flag.

**Not found: genuine stockouts.**
I looked for stockout signatures (runs of zero recorded sales) specifically
because it's a plausible-sounding explanation — none of the 15 have a
single zero-quantity day in the holdout window. What *is* common (13-57%
of days per SKU) is **null/unobserved days** — no quantity recorded at all,
distinct from a recorded zero. That's the DQ layer's existing "unobserved
day" signal, already handled by the completeness threshold, not evidence of
stock-outs — this dataset has no inventory/on-hand feed, so a true stockout
(zero sales because there was nothing to sell) and a POS logging gap are
indistinguishable from sales data alone. Flagging this distinction rather
than guessing at it.

## Recommended fixes, ranked by expected impact

1. **Give the promotion-uplift model SKU or commodity-group identity.**
   Highest expected impact: promo weeks are 58% of the holdout and the
   worst-performing segment (28.6% vs. 21.5% non-promo), and 10 of the 15
   worst SKUs show the exact signature a pooled, identity-blind uplift
   model would produce (systematic per-SKU over- or under-prediction that's
   consistent week to week). Add `itemCode` or `commodityName` as a
   categorical feature to `train_promotion_model.py`'s `features` list
   (currently just `discount_pct`, `overlap_days`, `promo_type`). Risk:
   with only ~1,450 training rows across 87 SKUs, adding `itemCode` could
   overfit per-SKU noise — `commodityName` (coarser grouping, e.g. all
   CHOCOLATES together) is the safer first move and worth trying before
   full per-SKU.
2. **Fix the partial-last-week completeness bug in `preprocess_features.py`.**
   Cheap, mechanical, and not just a reporting nicety — it makes any
   real-time evaluation run near the live edge of the data silently grade
   itself against an artificially low "actual." Compare `observed_days`
   against the full 7-calendar-day span for a week, not against
   `days_in_week` (row count), or drop the final week if it's not yet over.
3. **Treat slow-movers as a separate, lower-ceiling problem, not a bug to chase further.**
   The 28.4% vs. ~25.4% gap for slow SKUs is consistent with count-data
   noise at low volumes (this project's own README already documents a
   Poisson noise floor for low-volume SKUs). Further gains here are more
   likely from a zero-inflated/hurdle formulation than from more features —
   only worth the effort if slow-moving SKUs are commercially important
   enough to justify it.
4. **Get an inventory/stock-on-hand feed, if stockouts are suspected to matter.**
   Not a modeling fix — a data-acquisition ask. Right now there's no way to
   tell a stockout from a POS gap from genuinely low demand, and 13-57% of
   days per SKU in this list have no recorded quantity at all. This would
   matter more broadly than just these 15 SKUs if it turns out a meaningful
   share of "misses" are actually censored demand.
5. **React faster to a detected price change.**
   Narrow scope (found in 1 of 15 SKUs here), but a cheap targeted fix:
   when `regular_price` changes, consider resetting/down-weighting the
   `prev_asp_mean` and rolling-mean lag features rather than letting them
   average across the price change.
6. **Don't over-index on single-digit-week SKUs in future error reports.**
   Operational note, not a model fix: exclude or clearly flag SKUs with
   very few holdout weeks (e.g. STR000055678, n=1) from "worst SKU" lists
   going forward — their WMAPE is mostly sampling noise.
