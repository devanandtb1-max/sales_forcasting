"""Weekly feature engineering for the Pearcedale LightGBM demand model.

Reads the DQ layer's `clean_daily_sales.parquet` (tier A/B only — tier C and
every quarantined row are already excluded by pearcedale_dq). Aggregates to
ISO week per SKU and builds lag / rolling / calendar features.

Nothing here imputes an ambiguous null. A week's target is the sum of its
*observed* days only; a week is flagged `is_complete_week=False` when fewer
than MIN_WEEK_COMPLETENESS of its days were observed and is then excluded
from the training target (kept for history/lag features, since it's still
the best real observation available - just not one to be graded against).

Usage:
    python preprocess_features.py
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

INPUT = Path("../processed_output/clean_daily_sales.parquet")
PROMO_DAILY = Path("../processed_output/promotions_daily.parquet")
HOLIDAYS_CSV = Path("../data/vic_public_holidays.csv")
SCHOOL_TERMS_CSV = Path("../data/vic_school_terms.csv")
OUTPUT = Path("features/weekly_features.parquet")

MIN_WEEK_COMPLETENESS = 0.70
LAG_WEEKS = [1, 2, 3, 4, 8, 13, 52]
ROLL_WINDOWS = [4, 8, 13]


def load_clean(path: Path) -> pl.DataFrame:
    df = pl.read_parquet(path)
    return df.with_columns(pl.col("trading_date").str.to_date().alias("_date"))


def aggregate_weekly(df: pl.DataFrame) -> pl.DataFrame:
    df = df.with_columns(
        pl.col("_date").dt.strftime("%G-%V").alias("iso_week"),
    )

    weekly = df.group_by("itemCode", "iso_week").agg(
        pl.col("_date").min().alias("week_start"),
        pl.col("quantity").sum().alias("qty_sum"),
        pl.col("quantity").is_not_null().sum().alias("observed_days"),
        pl.len().alias("days_in_week"),
        pl.col("returns_qty").sum().alias("returns_sum"),
        pl.col("asp").mean().alias("asp_mean"),
        pl.col("unit_margin").mean().alias("unit_margin_mean"),
        pl.col("uom_class").first(),
        pl.col("tier").first(),
        pl.col("departmentName").first(),
        pl.col("commodityName").first(),
        pl.col("primarySupplier").first(),
        pl.col("cartonQty").first(),
        pl.col("minOrderQty").first(),
    )

    weekly = weekly.with_columns(
        (pl.col("observed_days") / pl.col("days_in_week")).alias("completeness"),
    )
    weekly = weekly.with_columns(
        (pl.col("completeness") >= MIN_WEEK_COMPLETENESS).alias("is_complete_week"),
    )
    return weekly.sort("itemCode", "week_start")


def add_calendar_features(weekly: pl.DataFrame) -> pl.DataFrame:
    return weekly.with_columns(
        pl.col("week_start").dt.year().alias("cal_year"),
        pl.col("week_start").dt.month().alias("cal_month"),
        pl.col("week_start").dt.quarter().alias("cal_quarter"),
        pl.col("iso_week").str.slice(5, 2).cast(pl.Int32).alias("iso_week_num"),
    ).with_columns(
        (2 * 3.14159265 * pl.col("iso_week_num") / 52).sin().alias("woy_sin"),
        (2 * 3.14159265 * pl.col("iso_week_num") / 52).cos().alias("woy_cos"),
    )


def add_lag_and_rolling_features(weekly: pl.DataFrame) -> pl.DataFrame:
    weekly = weekly.sort("itemCode", "week_start")

    lag_exprs = [
        pl.col("qty_sum").shift(lag).over("itemCode").alias(f"lag_{lag}")
        for lag in LAG_WEEKS
    ]
    weekly = weekly.with_columns(lag_exprs)

    prior = pl.col("qty_sum").shift(1).over("itemCode")
    roll_exprs = []
    for w in ROLL_WINDOWS:
        roll_exprs.append(
            prior.rolling_mean(window_size=w, min_samples=1).over("itemCode").alias(f"roll_mean_{w}")
        )
        roll_exprs.append(
            prior.rolling_std(window_size=w, min_samples=2).over("itemCode").alias(f"roll_std_{w}")
        )
    weekly = weekly.with_columns(roll_exprs)

    weekly = weekly.with_columns(
        pl.col("completeness").shift(1).over("itemCode").alias("prev_week_completeness"),
        pl.col("asp_mean").shift(1).over("itemCode").alias("prev_asp_mean"),
    )
    return weekly


def build_promo_weekly(path: Path = PROMO_DAILY) -> pl.DataFrame:
    """Aggregate the daily promo table (model/ingest_promotions.py) to one row
    per (itemCode, iso_week). Promo-derived columns are left null for weeks
    where promo visibility is UNKNOWN (majority of days before Oct 2024, or
    for the 13 SKUs with no promo record at all) rather than defaulting to
    "no promo" - an ambiguous null must never look like a confirmed zero.
    Future-week promo info is intentionally allowed here (promos are planned
    in advance); only the history-based lag/rolling sales features are
    restricted to t-1 and earlier.
    """
    daily = pl.read_parquet(path)
    daily = daily.with_columns(pl.col("trading_date").dt.strftime("%G-%V").alias("iso_week"))

    effective_price = pl.when(pl.col("promo_active")).then(pl.col("promo_price")).otherwise(pl.col("regular_price"))

    weekly = daily.group_by(["itemCode", "iso_week"]).agg(
        pl.len().alias("_n_days"),
        (pl.col("promo_status") == "UNKNOWN").sum().alias("_n_unknown"),
        (pl.col("promo_status") == "CONFIRMED").sum().alias("_n_confirmed"),
        pl.col("promo_active").sum().alias("promo_days_in_week"),
        pl.col("discount_pct").max().alias("promo_discount_pct_max"),
        pl.col("discount_pct").mean().alias("promo_discount_pct_mean"),
        pl.col("n_stacked_promos").max().alias("promo_stacked_max"),
        effective_price.mean().alias("_mean_effective_price"),
        pl.col("regular_price").mean().alias("_mean_regular_price"),
        pl.col("promo_type_group").filter(pl.col("promo_active")).mode().first().alias("_dominant_type"),
    )

    weekly = weekly.with_columns(
        (pl.col("_n_unknown") > pl.col("_n_days") / 2).alias("_is_unknown_week")
    )

    weekly = weekly.with_columns(
        pl.when(pl.col("_is_unknown_week")).then(pl.lit("UNKNOWN"))
          .when(pl.col("_n_confirmed") > 0).then(pl.lit("CONFIRMED"))
          .otherwise(pl.lit("NOT_CONFIRMED")).alias("promo_status_week"),
        pl.when(pl.col("_is_unknown_week")).then(None).otherwise(pl.col("promo_days_in_week")).alias("promo_days_in_week"),
        pl.when(pl.col("_is_unknown_week")).then(None).otherwise(pl.col("promo_discount_pct_max")).alias("promo_discount_pct_max"),
        pl.when(pl.col("_is_unknown_week")).then(None).otherwise(pl.col("promo_discount_pct_mean")).alias("promo_discount_pct_mean"),
        pl.when(pl.col("_is_unknown_week")).then(None).otherwise(pl.col("promo_stacked_max")).alias("promo_stacked_max"),
        pl.when(pl.col("_is_unknown_week")).then(None)
          .when(pl.col("promo_days_in_week") > 0).then(pl.col("_dominant_type"))
          .otherwise(pl.lit("none")).alias("promo_type_group_week"),
        pl.when(pl.col("_mean_regular_price").is_not_null() & (pl.col("_mean_regular_price") > 0))
          .then(pl.col("_mean_effective_price") / pl.col("_mean_regular_price"))
          .otherwise(None).alias("price_ratio"),
    )

    return weekly.select([
        "itemCode", "iso_week", "promo_status_week", "promo_days_in_week",
        "promo_discount_pct_max", "promo_discount_pct_mean", "promo_stacked_max",
        "promo_type_group_week", "price_ratio",
    ])


def add_promo_features(weekly: pl.DataFrame, promo_weekly: pl.DataFrame) -> pl.DataFrame:
    weekly = weekly.join(promo_weekly, on=["itemCode", "iso_week"], how="left")

    weekly = weekly.with_columns((pl.col("promo_days_in_week") > 0).alias("_promo_active_any"))
    weekly = weekly.sort("itemCode", "week_start")
    weekly = weekly.with_columns(
        # Float64, not Boolean: LightGBM/pandas can't take a nullable bool column,
        # and these are null wherever the referenced week is promo-UNKNOWN.
        pl.col("_promo_active_any").shift(-1).over("itemCode").cast(pl.Float64).alias("promo_pre_week"),
        pl.col("_promo_active_any").shift(1).over("itemCode").cast(pl.Float64).alias("promo_post_week1"),
        pl.col("_promo_active_any").shift(2).over("itemCode").cast(pl.Float64).alias("promo_post_week2"),
    )
    return weekly.drop("_promo_active_any")


def add_vic_calendar_flags(
    weekly: pl.DataFrame,
    holidays_path: Path = HOLIDAYS_CSV,
    terms_path: Path = SCHOOL_TERMS_CSV,
) -> pl.DataFrame:
    """Victorian public-holiday and school-term flags, keyed off week_start/
    week_end. Pure calendar lookups - no data-driven ambiguity, so these are
    never null.
    """
    holidays = pl.read_csv(holidays_path, try_parse_dates=True)
    terms = pl.read_csv(terms_path, try_parse_dates=True)

    weekly = weekly.with_columns((pl.col("week_start") + pl.duration(days=6)).alias("_week_end"))
    unique_weeks = weekly.select(["week_start", "_week_end"]).unique()

    cross_hol = unique_weeks.join(holidays.select(pl.col("date").alias("h_date")), how="cross")
    holiday_weeks = (
        cross_hol.filter((pl.col("h_date") >= pl.col("week_start")) & (pl.col("h_date") <= pl.col("_week_end")))
        .select("week_start").unique()
        .with_columns(pl.lit(1).cast(pl.Int8).alias("has_public_holiday"))
    )

    cross_term = unique_weeks.join(terms, how="cross")
    cross_term = cross_term.with_columns(
        (pl.min_horizontal("_week_end", "end_date") - pl.max_horizontal("week_start", "start_date"))
        .dt.total_days().add(1).clip(lower_bound=0).alias("overlap_days")
    )
    term_days = cross_term.group_by("week_start").agg(pl.col("overlap_days").sum().alias("_term_days"))

    calendar_flags = (
        unique_weeks.join(holiday_weeks, on="week_start", how="left")
        .join(term_days, on="week_start", how="left")
        .with_columns(
            pl.col("has_public_holiday").fill_null(0),
            pl.col("_term_days").fill_null(0),
        )
        .with_columns(
            (pl.col("_term_days") >= 4).cast(pl.Int8).alias("is_school_term")
        )
        .select(["week_start", "has_public_holiday", "is_school_term"])
    )

    weekly = weekly.join(calendar_flags, on="week_start", how="left")
    return weekly.drop("_week_end")


def main() -> None:
    df = load_clean(INPUT)
    weekly = aggregate_weekly(df)
    weekly = add_calendar_features(weekly)
    weekly = add_lag_and_rolling_features(weekly)

    if PROMO_DAILY.exists():
        promo_weekly = build_promo_weekly()
        weekly = add_promo_features(weekly, promo_weekly)
    else:
        print(f"[warn] {PROMO_DAILY} not found - skipping promo features. "
              f"Run ingest_promotions.py first.")

    weekly = add_vic_calendar_flags(weekly)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    weekly.write_parquet(OUTPUT, compression="zstd")

    n_skus = weekly.select("itemCode").unique().height
    n_complete = weekly.filter(pl.col("is_complete_week")).height
    print(f"SKUs: {n_skus}")
    print(f"Total SKU-weeks: {weekly.height}")
    print(f"Complete (trainable) SKU-weeks: {n_complete}")
    print(f"By uom_class (complete weeks): "
          f"{ {r[0]: r[1] for r in weekly.filter(pl.col('is_complete_week')).group_by('uom_class').len().rows()} }")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
