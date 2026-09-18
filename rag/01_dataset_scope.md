# Pearcedale Metcash promotion dataset: scope and purpose

Document ID: pearcedale-promo-scope-v1
Prepared: 2026-09-17
Status: Evidence-based dataset documentation; not an approved purchasing policy.
Sources: Pearcedale_Metcash_100_Promotions.json (scope and promotions); Pearcedale_Metcash_Promotion_Coverage.json (coverage summaries).

## What information is available?
The supplied promotion export selects 100 item codes for supplierCode "1". Its promotions array contains 3,601 records for 87 distinct item codes. The file names identify the dataset as Pearcedale Metcash; the records identify the supplier using code "1" rather than a supplier-name field.

The declared training scope runs from 2023-09-11 to 2026-09-10. The export describes its filter as: cost or selling interval overlaps training dates, with supplierCode exactly 1. An overlapping interval can begin before or finish after that scope. Inclusion does not mean that the customer selling promotion occurs entirely inside the training period.

There are 3,277 records with origin Hosted and 324 with origin Instore. These are row counts, not verified counts of distinct campaigns, sales, or purchases.

## What does selection ranking mean?
The selectedProducts entries contain itemCode, rankingNetUnits, and rank. There are 100 selected entries. The ranking measurement period and calculation are not documented in these files. rankingNetUnits must not be presented as three-year sales or a demand forecast.

## What is absent?
These files contain no sales transaction series, inventory balances, forecast predictions, model accuracy results, official PO quantities, supplier MOQ, carton requirements, or approved purchasing procedures. They cannot establish promotion uplift, stockout risk, or an order quantity.

## What belongs in RAG?
This dataset description, its field explanations, and its limitations are useful textual knowledge. Store the individual promotion records in structured storage for SKU and date queries. Retrieve current forecasts, inventory, and official PO recommendations separately from their authoritative application sources.

These documents describe the supplied snapshot. Recheck and revise them when the source files change. They do not establish which promotions are active today.
