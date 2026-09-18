# Pearcedale promotion coverage and missing history

Document ID: pearcedale-promo-coverage-v1
Prepared: 2026-09-17
Status: Snapshot findings; historical completeness unverified.
Sources: Pearcedale_Metcash_100_Promotions.json (scope, selectedProducts, promotions); Pearcedale_Metcash_Promotion_Coverage.json.

## Is this complete three-year promotion history?
No completeness claim is supported. The declared training scope is 2023-09-11 through 2026-09-10, and historicalCompletenessVerified is false. The earliest non-null selling start in the supplied promotion array is 2024-10-01T14:00:00Z, corresponding to 2024-10-02 in Australia/Melbourne. The latest non-null selling end is 2027-02-22T13:00:00Z, corresponding to 2027-02-23 locally.

These extrema do not prove continuous coverage between those dates. They also do not describe the complete cost interval history. Missing earlier selling starts must not be interpreted as evidence that no promotions occurred in 2023 or early 2024.

## Why do the files show different counts?
The coverage report describes several scopes. Its all scope reports 332,295 records and 25,344 products. Its supplier1 scope reports 265,518 records and 20,624 products. Its selected100 scope reports 3,651 records across 88 products. These broader totals are reported summaries; their complete underlying records were not provided for independent verification.

The selected100OverlappingTraining scope reports 3,601 records across 87 products. Those two totals match direct counts of the supplied promotion array. The difference from selected100 is 50 records and one represented product; do not treat the unfiltered selected100 totals as the contents of the supplied array.

The overlapping-training report lists selling-start year counts of 434 in 2024, 1,775 in 2025, and 1,384 in 2026. These total 3,593, eight fewer than 3,601, consistent with the eight null sellStart values independently counted in the array. They count record start years, not promotion days or sales volume.

## Which selected SKUs have no rows in this export?
Direct comparison of selected item codes with promotion item codes confirms these 13 missing SKUs, matching the coverage report:

- STR000025078
- STR000051128
- STR000001743
- STR000002946
- STR000025112
- STR000025056
- STR000025037
- STR000085330
- STR000025070
- STR000009285
- STR000007646
- STR000025338
- STR000051710

For each listed SKU, the supported statement is “No promotion record for this SKU is present in this supplied export.” Its promotion history remains unknown. Missing records do not demonstrate that the SKU was never promoted.

## What can this imply for forecasting?
Recommended analytical practice, not an observed company rule: preserve unknown coverage when creating promotion features; do not automatically label uncovered dates or SKUs as confirmed non-promotion periods. Measure promotion effects only after joining appropriate sales data and checking coverage. These files alone provide neither causal uplift estimates nor forecast performance.
