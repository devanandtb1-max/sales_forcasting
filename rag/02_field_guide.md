# Pearcedale promotion fields and interpretation

Document ID: pearcedale-promo-fields-v1
Prepared: 2026-09-17
Status: Observed schema with explicitly limited interpretations; not a supplier contract.
Sources: Pearcedale_Metcash_100_Promotions.json (promotion objects and scope); Pearcedale_Metcash_Promotion_Coverage.json (notes).

## Product and supplier identifiers
itemCode is the selected SKU identifier, for example STR000007715. supplierCode identifies the supplier scope, here "1". supplierItemId is an additional numeric identifier in promotion records; its external definition is not supplied. Do not derive supplierItemId from the digits of itemCode. Product names, product descriptions, and pack sizes are not provided.

## Promotion classification
origin contains Hosted or Instore. Their operational meanings are not formally defined by the export. promotionTypeCode and promotionTypeName contain source classification labels; some are null or generic. slot, sequence, and week are source attributes whose uniqueness and precise business semantics are undocumented. Do not assume week is an ISO week or that sequence is a globally unique promotion identifier.

## Cost and selling periods
costStart and costEnd describe the recorded cost interval. sellStart and sellEnd describe the recorded customer selling interval. These periods must be considered separately: a cost overlap alone is not evidence of a customer selling promotion on that date.

Raw timestamp strings use UTC Z notation. The coverage report summarizes dates in Australia/Melbourne. Convert to that named timezone for comparisons with local trading dates, accounting for daylight saving. The export does not document whether interval end points are inclusive or exclusive; establish that convention before implementing date matching.

## Amounts and flags
unitCost and sell are numeric amount fields associated with cost and selling respectively. Currency, tax treatment, unit basis, and multi-buy calculation semantics are not explicitly documented. Do not assume the amounts represent comparable per-unit prices or calculate a discount from them without confirming their definitions and a regular-price baseline.

costActive and sellActive are recorded Boolean flags. The coverage report explicitly cautions that active flags alone do not establish historical effectiveness. A true flag is also insufficient to establish activity today.

## Record timestamps and missing values
createdDate and lastModified record source timestamps; they are not substitutes for promotion start and end dates. Exact source-system timestamp semantics are not documented.

In the 3,601 supplied rows, 8 sellStart values are null, 211 costStart values are null, and 213 unitCost values equal zero. Null means unavailable in that field. Zero is an observed value whose business meaning is unconfirmed; it does not establish free stock. Do not silently replace missing values with a business assumption.
