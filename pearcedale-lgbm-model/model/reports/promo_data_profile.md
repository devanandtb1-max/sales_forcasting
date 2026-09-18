# Promotion data profile — `data/Pearcedale_Metcash_100_Promotions.json`

Analysis only — no pipeline code was changed. Ad-hoc profiling scripts used to
produce these numbers are not part of the pipeline (not committed under
`model/`). Dates converted from UTC to `Australia/Melbourne` per project
convention.

**Source:** `data.promotions`, a flat list of 3,601 records covering 87 of
the 100 SKUs (13 SKUs have no promo record at all, consistent with the known
facts in [AGENTS.md](../../AGENTS.md)). Earliest `sellStart` 2024-10-02,
latest `sellEnd` 2027-02-23 (some rows extend into the future as
still-scheduled promotions).

## 1. Record counts

**By origin:**

| origin | records |
|---|---|
| Hosted | 3,277 |
| Instore | 324 |

**By slot:**

| slot | records | mostly |
|---|---|---|
| 4 | 3,211 | Hosted — the main promo slot (all named types + gmGmcUpd) |
| 1 | 321 | Instore — regular shelf price |
| 2 | 69 | Hosted — MB - MULTI BUYS exclusively |

**origin × slot:** Hosted/slot4 = 3,208, Hosted/slot2 = 69, Instore/slot1 =
321, Instore/slot4 = 3 (negligible). So slot cleanly separates the "regular
price" record (Instore/slot1) from the promo record (Hosted, slot 2 or 4) —
slot is a near-perfect origin proxy here, not an independent signal.

**By `promotionTypeName`** (14 distinct values incl. null):

| promotionTypeName | records | origin |
|---|---|---|
| Added by gmGmcUpd | 1,437 | Hosted |
| LD - IGA AD LEADER | 568 | Hosted |
| AD - ADVERT LINES | 426 | Hosted |
| Special Feature - Weekly | 400 | Hosted |
| *(null)* | 324 | **Instore only** — every null-type record is Instore/slot1 |
| BB - IGA BAKERY | 207 | Hosted |
| MB - MULTI BUYS | 69 | Hosted (slot 2) |
| Support Line (now 1 Week) | 69 | Hosted |
| LW - IGA L/WK SPEC | 50 | Hosted |
| PM - PRICE MATCH | 27 | Hosted |
| MK - MARKET DAY | 10 | Hosted |
| FM - IGA FRSH MEAT | 9 | Hosted |
| AF - IGA 2WK | 4 | Hosted |
| CO - IGA CORP BRND | 1 | Hosted |

## 2. Duration distribution

Overall (`sellEnd − sellStart + 1`, n=3,593, 8 null-date records): median
**7 days**, mean 16.0, range 1–300.

| Group | n | median | mean | min | max |
|---|---|---|---|---|---|
| Hosted | 3,277 | 7 | 10.7 | 1 | 91 |
| Instore | 324 | 90 | 69.5 | 2 | 300 |
| slot 1 | 321 | 90 | 70.1 | 2 | 300 |
| slot 2 | 69 | 7 | 7.2 | 7 | 21 |
| slot 4 | 3,211 | 7 | 10.8 | 1 | 91 |

By `promotionTypeName`, every named Hosted type except gmGmcUpd is
essentially **exactly 7 days** (min=max=median=7 for AD LEADER, ADVERT
LINES, Special Feature - Weekly, BAKERY, PRICE MATCH, FRESH MEAT, CORP
BRAND) or a short, type-specific window (LW - L/WK SPEC: median 3 days;
MARKET DAY: 1 day; AF - 2WK: 14 days) — these read as genuine, tightly
scoped campaign records, matching AGENTS.md's "weekly promo events" note.

`Added by gmGmcUpd` is **bimodal** and does not fit that pattern:

| duration | n | % of gmGmcUpd |
|---|---|---|
| 7 days | 1,203 | 83.7% |
| 21 days | 107 | 7.4% |
| 91 days | 119 | 8.3% |
| 14 / 28 days / null | 8 | 0.6% |

## 3. Instore slot-1 price vs. observed ASP — confirms "regular price"

Compared each Instore/slot-1 `sell` price to the SKU's mean `asp` (average
selling price) from `processed_output/clean_daily_sales.parquet`.

- **Naive comparison** (ASP averaged over the *entire* slot-1 window,
  including any Hosted promo days that fall inside it): slot-1 `sell` is a
  median **+14.4%** above ASP (mean +14.1%, only 33.6% within ±5%, 38.3%
  within ±10%). Looks like a mismatch at first glance.
- **Corrected comparison** (ASP averaged only over days in the slot-1 window
  that do **not** overlap any Hosted promo for that SKU, n=298 of 321):
  median difference **0.0%**, mean −0.02%, **94.6% within ±5%, 99.0% within
  ±10%**.

**Conclusion: Instore/slot-1 is the regular (non-discounted) shelf price.**
The +14% gap in the naive comparison is fully explained by promo-day
discounts pulling the raw ASP down during the same window — once those days
are excluded, slot-1 price and ASP agree almost exactly. This confirms
AGENTS.md's existing note; **Instore/slot-1 records should not be treated as
promotions.**

## 4. "Added by gmGmcUpd" — what it actually looks like

Compared each gmGmcUpd `sell` price to the SKU's regular price (the
overlapping Instore/slot-1 record where one exists — 605 of 1,432
date-valid records — else the SKU's overall mean ASP as a fallback, 827
records).

Pooled across all 1,432: median **−24.1%** below regular price (mean
−21.7%), only 20.7% within ±5% of regular — i.e. the *majority* of gmGmcUpd
records are real discounts. But this splits cleanly by duration:

| duration | n | median price vs. regular | reading |
|---|---|---|---|
| 7 days | 1,203 (83.7%) | **−28.1%** | a real, weekly-length discount — behaves like every other Hosted promo type |
| 21 days | 107 (7.4%) | **+2.8%** | essentially at regular price |
| 91 days | 119 (8.3%) | **+0.6%** | essentially at regular price |

**Conclusion:** "Added by gmGmcUpd" is not one thing. The 7-day records
(84% of the label) are genuine discount events, likely a system/GM-tool
auto-generated promo entry rather than a hand-typed one, but functionally a
promotion. The 21- and 91-day records sit right at regular price — these
look like price/cost-maintenance re-entries (a list-price refresh logged
through the same promo mechanism) and should **not** be counted as
promotions. Section 5 also flags a practical risk from mixing these into one
bucket.

## 5. Overlap frequency (same SKU, overlapping sell windows)

| Scope | SKUs | SKUs with ≥1 overlapping pair | overlapping pairs |
|---|---|---|---|
| All records (any origin/type) | 87 | 67 (77.0%) | 1,628 |
| Hosted only, incl. gmGmcUpd | 85 | 32 (37.6%) | 199 |
| Hosted only, excl. gmGmcUpd | 78 | 6 (7.7%) | 26 |
| Instore only | 61 | 12 (19.7%) | 16 |

Most of the "All records" overlap is the **expected, harmless** kind: an
Instore/slot-1 regular-price window (which can span 90+ days) overlapping
one or more short Hosted promo windows underneath it — that's just "this SKU
had a regular price, and also ran some promos, during that period," not a
data problem.

The overlap that matters is **Hosted-vs-Hosted**: 37.6% of SKUs have two
Hosted promo records covering the same date, but that drops to 7.7% once
gmGmcUpd is excluded — gmGmcUpd (especially its 91-day and 21-day flavor) is
the dominant cause of Hosted-on-Hosted overlap, e.g. a 91-day gmGmcUpd
window blanketing several distinct 7-day AD LEADER / ADVERT LINES /
MULTI BUYS weeks underneath it (see `STR000020053`, `STR000019479` in the
raw analysis). This matters because the existing dedup logic in
`train_promotion_model.py` / `hybrid_forecast.py` picks the record with the
**largest `overlap_days`** for a given SKU-week — a 91-day gmGmcUpd
"regular price" record will always win that tie-break over a genuine 7-day
promo it happens to overlap, silently swallowing the real promo signal for
that week. This is a real risk worth addressing, not just a curiosity.

## 6. Do sales actually rise during Hosted promo weeks, by type?

For each SKU, "baseline" = mean daily quantity on days *not* covered by any
Hosted promo interval for that SKU; "promo" = mean daily quantity on days
covered by a given type's interval. `pooled_lift_ratio` sums quantity and
baseline-implied quantity across all SKU instances (more robust to
low-volume SKUs than averaging per-SKU ratios).

| promotionTypeName | SKU instances | median ratio | mean ratio | % instances lift>1 | pooled lift |
|---|---|---|---|---|---|
| LD - IGA AD LEADER | 60 | 1.64 | 1.64 | 93.3% | **1.65×** |
| Special Feature - Weekly | 58 | 1.44 | 1.50 | 93.1% | **1.57×** |
| LW - IGA L/WK SPEC | 21 | 1.61 | 2.34 | 85.7% | **2.11×** |
| PM - PRICE MATCH | 19 | 1.35 | 1.43 | 78.9% | **1.60×** |
| AD - ADVERT LINES | 57 | 1.22 | 1.36 | 82.5% | **1.45×** |
| Added by gmGmcUpd (all durations pooled) | 81 | 1.34 | 1.39 | 82.7% | **1.11×** |
| MK - MARKET DAY | 2 | 1.89 | 1.89 | 100% | 1.79× (n too small to trust) |
| FM - IGA FRSH MEAT | 1 | — | — | — | (n=1, not usable) |
| CO - IGA CORP BRND | 1 | — | — | — | (n=1, not usable) |
| Support Line (now 1 Week) | 21 | 1.13 | 1.06 | 61.9% | 1.02× — weak/flat |
| BB - IGA BAKERY | 8 | 0.94 | 1.02 | 37.5% | 1.27× (median < 1, noisy — small n) |
| MB - MULTI BUYS | 21 | 0.90 | 1.01 | 42.9% | 1.04× — weak/flat |
| AF - IGA 2WK | 4 | 0.90 | 0.87 | 50% | 0.95× (n too small to trust) |

**Reading:** the well-populated, exactly-7-day promo types (AD LEADER,
Special Feature - Weekly, ADVERT LINES, PRICE MATCH, L/WK SPEC) show clear,
consistent lift — 1.4×–2.1× pooled, with 80–93% of individual SKU instances
above baseline. `Added by gmGmcUpd`'s pooled 1.11× understates its real
effect because it mixes the discounting 7-day records with the flat
21-/91-day records (section 4) — isolating just the 7-day gmGmcUpd records
would be expected to land closer to the other weekly types. MULTI BUYS,
BAKERY, Support Line, and AF-2WK show weak or flat lift, but three of those
four also have the smallest sample sizes (8–21 instances) — treat as
provisional, not "these promos don't work."

## 7. Recommendation: what should count as a promotion

| Source | Count | Include as promotion? | Why |
|---|---|---|---|
| Hosted, named type (AD LEADER, ADVERT LINES, Special Feature, BAKERY, MULTI BUYS, Support Line, L/WK SPEC, PRICE MATCH, MARKET DAY, FRESH MEAT, 2WK, CORP BRAND) | 2,164 | **Yes** | Confirmed weekly (or type-appropriate short) campaigns; measurable lift for the well-populated types (§6) |
| Hosted, `Added by gmGmcUpd`, duration = 7 days | 1,203 | **Yes** | Priced at a median −28% discount (§4), lift ratio in line with named promo types |
| Hosted, `Added by gmGmcUpd`, duration = 21 or 91 days | 226 | **No** | Priced at regular price (±0–3%, §4) — this is a price/cost-maintenance record wearing a promo label, not a discount event |
| Instore, slot 1 (any duration) | 321 | **No** | Confirmed to be the regular shelf price (§3), not a promotion |
| Instore, slot 4 | 3 | No (default) — flag for manual check | Too few to characterize; pattern (Instore origin) suggests regular-price-like, not promo |

**Practical follow-ups for whoever implements this** (not done here — no
pipeline code was changed):
1. Split `Added by gmGmcUpd` by `duration_days` (≤7 vs. ≥21) before it enters
   `ingest_promotions.py` / the training join, rather than treating the
   whole label as one promo type or discarding it entirely.
2. When resolving overlapping Hosted promos for the same SKU-week, break
   ties by discount depth (or exclude the 21-/91-day gmGmcUpd rows first),
   not just `overlap_days` — otherwise a long gmGmcUpd "regular price"
   window can mask a real weekly promo running underneath it (§5).
