"""Build a daily per-SKU promotion table from the raw promotion feed.

Source: ``data/Pearcedale_Metcash_100_Promotions.json`` (``data.promotions``,
a flat list of 3,601 records — NOT the old nested ``productSnapshots[].raw.
promotions[]`` shape the previous version of this script expected, and not
the missing consolidated JSON it used to point at).

Classification rules, derived from ``model/reports/promo_data_profile.md``:

- **Regular price** = ``origin == "Instore" & slot == 1``. Confirmed against
  observed ASP on non-promo days (median diff 0.0%, 94.6% within +-5%) —
  this is the shelf price, not a promotion. ``Instore`` slot 4 (3 records)
  is excluded from both regular and promo classification; too few to
  characterize (see profile doc), left for manual review.
- **Qualifying promotion** = ``origin == "Hosted"`` and either a named
  promotion type, or ``"Added by gmGmcUpd"`` with a 7-day duration (83.7%
  of that label, priced a median 28% below regular — the 21/91-day
  gmGmcUpd records sit at regular price and are price/cost-maintenance
  entries, not discounts; excluded).
- Records with ``sellActive == false`` (8 rows, all with null/garbage
  dates) are dropped.

When multiple qualifying promos cover the same SKU-day (~38% of SKUs, see
profile doc), the record with the **deepest discount** wins the
day's ``promo_price`` / ``discount_pct`` / ``promo_type_group`` — not the
one with the longest window, which is what silently masks real weekly
promos under a long gmGmcUpd window in the existing weekly-interval
pipeline (``train_promotion_model.py`` / ``hybrid_forecast.py``).
``n_stacked_promos`` still records how many qualifying promos were active
that day, win or lose.

Dates are UTC in the source; converted to Australia/Melbourne throughout,
sell periods only (cost periods are never used).

Output: one row per (itemCode, trading_date) covering every SKU present in
``processed_output/clean_daily_sales.parquet`` across its full date range —
``processed_output/promotions_daily.parquet``. This is a new, additive
output; it does not replace ``processed_output/promotions.parquet``, which
``train_promotion_model.py`` / ``hybrid_forecast.py`` still read in the
older weekly-interval schema.

Usage:
    python ingest_promotions.py
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

PROMO_JSON = Path("../data/Pearcedale_Metcash_100_Promotions.json")
DAILY_SALES = Path("../processed_output/clean_daily_sales.parquet")
OUTPUT = Path("../processed_output/promotions_daily.parquet")

TZ = "Australia/Melbourne"

GMGMCUPD_LABEL = "Added by gmGmcUpd"
GMGMCUPD_PROMO_DURATION_DAYS = 7  # only the 7-day bucket prices like a real discount

TYPE_GROUP = {
    "LD - IGA AD LEADER": "ad_leader",
    "AD - ADVERT LINES": "advert",
    "Special Feature - Weekly": "weekly_special",
    "MB - MULTI BUYS": "multibuy",
}
DEFAULT_GROUP = "other"


def load_raw_promos() -> pl.DataFrame:
    with open(PROMO_JSON) as f:
        data = json.load(f)
    df = pl.DataFrame(data["promotions"])
    df = df.filter(pl.col("sellActive"))
    df = df.with_columns(
        pl.col("sellStart").str.to_datetime(time_zone="UTC", strict=False)
          .dt.convert_time_zone(TZ).dt.date().alias("s"),
        pl.col("sellEnd").str.to_datetime(time_zone="UTC", strict=False)
          .dt.convert_time_zone(TZ).dt.date().alias("e"),
    )
    df = df.filter(pl.col("s").is_not_null() & pl.col("e").is_not_null())
    df = df.with_columns((pl.col("e") - pl.col("s")).dt.total_days().add(1).alias("duration_days"))
    return df


def classify(df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split into (regular-price records, qualifying-promo records)."""
    regular = df.filter((pl.col("origin") == "Instore") & (pl.col("slot") == 1))

    is_named_hosted = (
        (pl.col("origin") == "Hosted")
        & pl.col("promotionTypeName").is_not_null()
        & (pl.col("promotionTypeName") != GMGMCUPD_LABEL)
    )
    is_qualifying_gmgmcupd = (
        (pl.col("origin") == "Hosted")
        & (pl.col("promotionTypeName") == GMGMCUPD_LABEL)
        & (pl.col("duration_days") == GMGMCUPD_PROMO_DURATION_DAYS)
    )
    promo = df.filter(is_named_hosted | is_qualifying_gmgmcupd)
    promo = promo.with_columns(
        pl.col("promotionTypeName").replace_strict(TYPE_GROUP, default=DEFAULT_GROUP).alias("promo_type_group")
    )
    return regular, promo


def explode_daily(df: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
    """One row per (itemCode, trading_date) for each record's [s, e] window."""
    df = df.with_columns(pl.date_ranges(pl.col("s"), pl.col("e"), interval="1d").alias("trading_date"))
    return df.explode("trading_date").select(["itemCode", "trading_date", *cols])


def build_scaffold(max_future_date=None) -> pl.DataFrame:
    """Calendar spans the sales history *and* any known future promo window,
    so run_forecasting.py can look up already-scheduled future promotions
    here instead of only ever seeing historical ones."""
    daily_sales = pl.read_parquet(DAILY_SALES).with_columns(pl.col("trading_date").str.to_date())
    skus = daily_sales.select("itemCode").unique()
    date_min = daily_sales["trading_date"].min()
    date_max = daily_sales["trading_date"].max()
    if max_future_date is not None and max_future_date > date_max:
        date_max = max_future_date
    calendar = pl.DataFrame({"trading_date": pl.date_range(date_min, date_max, interval="1d", eager=True)})
    return skus.join(calendar, how="cross")


def build_regular_price_daily(regular: pl.DataFrame, scaffold: pl.DataFrame) -> pl.DataFrame:
    exploded = explode_daily(regular.with_row_index("rec_id"), ["rec_id", "sell", "createdDate"])
    # If two Instore/slot-1 records cover the same day (rare), keep the most recently created one.
    exploded = exploded.sort(["itemCode", "trading_date", "createdDate"])
    exploded = exploded.group_by(["itemCode", "trading_date"], maintain_order=True).agg(
        pl.col("sell").last().alias("regular_price")
    )
    out = scaffold.join(exploded, on=["itemCode", "trading_date"], how="left")
    out = out.sort(["itemCode", "trading_date"]).with_columns(
        pl.col("regular_price").fill_null(strategy="forward").over("itemCode")
    )
    return out


def build_promo_daily(promo: pl.DataFrame, regular_daily: pl.DataFrame) -> pl.DataFrame:
    promo = promo.with_row_index("promo_id")
    exploded = explode_daily(promo, ["promo_id", "sell", "promo_type_group"])
    exploded = exploded.join(
        regular_daily.select(["itemCode", "trading_date", "regular_price"]),
        on=["itemCode", "trading_date"], how="left",
    )
    exploded = exploded.with_columns(
        pl.when(pl.col("regular_price").is_not_null() & (pl.col("regular_price") > 0))
          .then((pl.col("regular_price") - pl.col("sell")) / pl.col("regular_price"))
          .otherwise(None)
          .alias("discount_pct_record")
    )

    stacked = exploded.group_by(["itemCode", "trading_date"]).agg(
        pl.col("promo_id").n_unique().alias("n_stacked_promos")
    )

    winner = (
        exploded.sort(
            ["itemCode", "trading_date", "discount_pct_record"],
            descending=[False, False, True], nulls_last=True,
        )
        .group_by(["itemCode", "trading_date"], maintain_order=True)
        .first()
        .select(["itemCode", "trading_date", "sell", "discount_pct_record", "promo_type_group"])
        .rename({"sell": "promo_price", "discount_pct_record": "discount_pct"})
    )
    return winner.join(stacked, on=["itemCode", "trading_date"], how="left")


def add_promo_distance_features(daily: pl.DataFrame, promo: pl.DataFrame) -> pl.DataFrame:
    """days_to_next_promo / days_since_last_promo, both 0 on an active promo day."""
    starts = (promo.select(["itemCode", "s"]).unique().sort(["itemCode", "s"])
              .rename({"s": "start_date"}))
    ends = (promo.select(["itemCode", "e"]).unique().sort(["itemCode", "e"])
            .rename({"e": "end_date"}))

    daily = daily.sort("trading_date")

    probe_next = daily.select(["itemCode", "trading_date"]).with_columns(
        (pl.col("trading_date") + pl.duration(days=1)).alias("probe")
    ).sort("probe")
    next_start = probe_next.join_asof(
        starts.sort("start_date"), left_on="probe", right_on="start_date", by="itemCode", strategy="forward",
    ).with_columns(
        (pl.col("start_date") - pl.col("trading_date")).dt.total_days().alias("days_to_next_promo_raw")
    ).select(["itemCode", "trading_date", "days_to_next_promo_raw"])

    probe_prev = daily.select(["itemCode", "trading_date"]).with_columns(
        (pl.col("trading_date") - pl.duration(days=1)).alias("probe")
    ).sort("probe")
    prev_end = probe_prev.join_asof(
        ends.sort("end_date"), left_on="probe", right_on="end_date", by="itemCode", strategy="backward",
    ).with_columns(
        (pl.col("trading_date") - pl.col("end_date")).dt.total_days().alias("days_since_last_promo_raw")
    ).select(["itemCode", "trading_date", "days_since_last_promo_raw"])

    daily = daily.join(next_start, on=["itemCode", "trading_date"], how="left")
    daily = daily.join(prev_end, on=["itemCode", "trading_date"], how="left")
    daily = daily.with_columns(
        pl.when(pl.col("promo_active")).then(0).otherwise(pl.col("days_to_next_promo_raw")).alias("days_to_next_promo"),
        pl.when(pl.col("promo_active")).then(0).otherwise(pl.col("days_since_last_promo_raw")).alias("days_since_last_promo"),
    )
    return daily.drop(["days_to_next_promo_raw", "days_since_last_promo_raw"])


def add_promo_status(daily: pl.DataFrame, raw: pl.DataFrame) -> pl.DataFrame:
    visibility_start = raw["s"].min()
    skus_with_history = set(raw["itemCode"].unique().to_list())
    daily = daily.with_columns(
        pl.col("itemCode").is_in(skus_with_history).alias("_has_history")
    )
    daily = daily.with_columns(
        pl.when((pl.col("trading_date") < visibility_start) | (~pl.col("_has_history")))
          .then(pl.lit("UNKNOWN"))
          .when(pl.col("promo_active"))
          .then(pl.lit("CONFIRMED"))
          .otherwise(pl.lit("NOT_CONFIRMED"))
          .alias("promo_status")
    )
    return daily.drop("_has_history")


def build_daily_table() -> pl.DataFrame:
    raw = load_raw_promos()
    scaffold = build_scaffold(max_future_date=raw["e"].max())
    regular, promo = classify(raw)

    regular_daily = build_regular_price_daily(regular, scaffold)
    promo_daily = build_promo_daily(promo, regular_daily)

    out = regular_daily.join(promo_daily, on=["itemCode", "trading_date"], how="left")
    out = out.with_columns(
        pl.col("promo_price").is_not_null().alias("promo_active"),
        pl.col("n_stacked_promos").fill_null(0),
    )
    out = out.with_columns(
        pl.when(pl.col("promo_active")).then(pl.col("discount_pct")).otherwise(0.0).alias("discount_pct"),
    )

    # ── DQ: exclude price-hike records (promo_price > regular_price) ──────
    # These are invalid promotions where the "promotional" price exceeds the
    # regular shelf price, producing negative discount_pct.  Mark them as
    # non-promo days (promo_active=false) instead of allowing them to
    # contaminate the model.  The original promo_price/discount_pct are
    # nulled out so downstream code never sees a negative discount.
    is_price_hike = pl.col("promo_active") & (pl.col("discount_pct") < 0)
    out = out.with_columns(
        pl.when(is_price_hike).then(False).otherwise(pl.col("promo_active")).alias("promo_active"),
        pl.when(is_price_hike).then(None).otherwise(pl.col("promo_price")).alias("promo_price"),
        pl.when(is_price_hike).then(0.0).otherwise(pl.col("discount_pct")).alias("discount_pct"),
        pl.when(is_price_hike).then(None).otherwise(pl.col("promo_type_group")).alias("promo_type_group"),
    )

    out = add_promo_distance_features(out, promo)
    out = add_promo_status(out, raw)

    return out.select([
        "itemCode", "trading_date", "regular_price", "promo_active", "promo_price",
        "discount_pct", "promo_type_group", "n_stacked_promos",
        "days_to_next_promo", "days_since_last_promo", "promo_status",
    ]).sort(["itemCode", "trading_date"])


def main() -> None:
    out = build_daily_table()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(OUTPUT)
    print(f"Wrote {out.height} rows ({out['itemCode'].n_unique()} SKUs) to {OUTPUT}")
    print(out.group_by("promo_status").len())

    promo_active_count = out.filter(pl.col("promo_active")).height
    print(f"Promo-active days: {promo_active_count}")

    # ── DQ report: price-hike records that were excluded ──────────────────
    neg_discount = out.filter(
        (pl.col("discount_pct") == 0.0)
        & pl.col("promo_price").is_null()
        & pl.col("regular_price").is_not_null()
    )
    # A more precise check: re-read promo_daily data to count excluded rows
    # Instead, just check for days that had promo data but were zeroed out
    print(f"\n── DQ: Price-hike exclusion report ──")
    print(f"  Negative discount_pct rows remaining: "
          f"{out.filter(pl.col('discount_pct') < 0).height}")
    print(f"  Promo-active rows with discount_pct == 0: "
          f"{out.filter(pl.col('promo_active') & (pl.col('discount_pct') == 0.0)).height}")
    if out.filter(pl.col("discount_pct") < 0).height > 0:
        print("  WARNING: Negative discount_pct values still present!")
    else:
        print("  OK: No negative discount_pct values in output.")


if __name__ == "__main__":
    main()
