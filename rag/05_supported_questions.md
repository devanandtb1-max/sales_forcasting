# Pearcedale promotions: supported answers and application boundaries

Document ID: pearcedale-promo-answers-v1
Prepared: 2026-09-17
Status: Suggested assistant behaviour based on supplied evidence and the user's stated architecture; not an existing supermarket policy.
Sources: Pearcedale_Metcash_100_Promotions.json; Pearcedale_Metcash_Promotion_Coverage.json; user-specified separation of forecasting, PO calculation, structured data, and RAG.

## How many selected products have promotion records?
The supplied filtered export contains promotion records for 87 of the 100 selected SKUs, across 3,601 rows. This is snapshot coverage, not a claim that only 87 products were ever promoted.

## What is the supplier MOQ?
MOQ is not available in these files. No supported minimum ordering quantity or carton rule can be supplied. The MB promotion label is not a supplier MOQ.

## Did STR000025078 have no promotions?
STR000025078 has no promotion rows in this supplied export. Historical completeness is unverified, so the files cannot establish that it had no promotions.

## What does sellActive = true mean for a historical date?
It is a recorded active flag. The coverage report says such flags alone do not establish historical effectiveness. A dated answer also requires the relevant selling interval, applicable local-date conventions, and adequate source coverage.

## How much did a promotion increase sales?
The necessary sales observations and comparison baseline are absent. These files cannot quantify promotion uplift. A forecast is also not proof of a realized sales increase.

## How much should we order for the next 15 days?
The supplied files contain neither a 15-day demand prediction nor inventory or an official PO recommendation. The application must obtain the forecast from its forecasting model and the official quantity from its PO engine. Claude can explain supplied results but must not independently calculate or override the official order quantity.

## How should forecast information be added later?
Recommended integration approach: retrieve dated, SKU-specific predictions and official PO outputs from structured storage, alongside these explanatory documents. Include the forecast generation time, forecast horizon, model version when available, and the official recommendation's inputs or explanation. A promotion's presence alone does not establish that a model used it or that it caused an order recommendation.

## How should historical promotion records be queried?
Recommended integration approach: query structured promotion records by itemCode and the relevant cost or selling period. Cite the record identifiers and source snapshot in the answer. Use RAG for field meanings and coverage caveats. When overlapping records disagree, surface the ambiguity until a documented precedence rule is available.
