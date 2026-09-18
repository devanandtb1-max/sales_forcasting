import json
import polars as pl
from pathlib import Path

JSON_INPUT = Path("../pearcedale-metcash1-top100-three-years-20260910_CONSOLIDATED.json")
OUTPUT = Path("../processed_output/clean_daily_sales.parquet")

def ingest():
    print(f"Loading {JSON_INPUT}...")
    with open(JSON_INPUT, 'r') as f:
        data = json.load(f)

    manifest = data.get("manifest", {})
    daily_sales_raw = data.get("dailySales", [])
    product_snapshots = data.get("productSnapshots", [])

    print(f"Manifest dates: {manifest.get('startDate')} to {manifest.get('endDate')}")
    print(f"Loaded {len(daily_sales_raw)} daily sales records and {len(product_snapshots)} product snapshots.")

    # 1. Build product metadata lookup
    meta_lookup = {}
    for snap in product_snapshots:
        item_code = snap["itemCode"]
        raw = snap.get("raw", {})
        
        # Primary item data
        items = raw.get("items", [{}])
        item_data = items[0] if items else {}
        
        # Supplier items data
        supplier_items = raw.get("supplierItems", [])
        primary_supplier = next((si for si in supplier_items if si.get("isPrimary")), {})

        uom_class = "weight" if item_data.get("isWeigh") or item_data.get("isScaleItem") else "units"

        meta_lookup[item_code] = {
            "departmentName": item_data.get("departmentName"),
            "commodityName": item_data.get("commodityName"),
            "primarySupplier": item_data.get("primarySupplier"),
            "cartonQty": primary_supplier.get("cartonQty"),
            "minOrderQty": primary_supplier.get("minOrderQty"),
            "uom_class": uom_class,
            "tier": "UNKNOWN" # As per instructions: do not invent tier
        }

    # 2. Flatten daily sales
    rows = []
    for ds in daily_sales_raw:
        item_code = ds.get("itemCode")
        trading_date = ds.get("date")
        quantity = ds.get("quantity")
        
        if quantity is None:
            # Note: keeping null quantities as per preprocessing script behavior which drops them for target
            pass
            
        asp = None
        unit_margin = None
        
        # Calculate ASP and Unit Margin from rawSalesRows (assumed to be line totals)
        raw_rows = ds.get("rawSalesRows", [])
        
        total_sell = 0.0
        total_cost = 0.0
        total_qty = 0.0
        
        for r in raw_rows:
            # historyType == 1 appears to be sales based on inspection, but we just sum up everything for the day
            if r.get("quantity", 0) > 0 and r.get("sell") is not None and r.get("cost") is not None:
                total_sell += float(r["sell"])
                total_cost += float(r["cost"])
                total_qty += float(r["quantity"])
                
        if total_qty > 0:
            asp = total_sell / total_qty
            unit_margin = (total_sell - total_cost) / total_qty
        
        # Returns qty assumption
        returns_qty = 0.0
        
        # Get metadata
        meta = meta_lookup.get(item_code, {})

        rows.append({
            "trading_date": trading_date,
            "itemCode": item_code,
            "quantity": quantity,
            "returns_qty": returns_qty,
            "asp": asp,
            "unit_margin": unit_margin,
            "uom_class": meta.get("uom_class"),
            "tier": meta.get("tier"),
            "departmentName": meta.get("departmentName"),
            "commodityName": meta.get("commodityName"),
            "primarySupplier": meta.get("primarySupplier"),
            "cartonQty": meta.get("cartonQty"),
            "minOrderQty": meta.get("minOrderQty")
        })

    schema = {
        "trading_date": pl.Utf8,
        "itemCode": pl.Utf8,
        "quantity": pl.Int64,
        "returns_qty": pl.Float64,
        "asp": pl.Float64,
        "unit_margin": pl.Float64,
        "uom_class": pl.Utf8,
        "tier": pl.Utf8,
        "departmentName": pl.Utf8,
        "commodityName": pl.Utf8,
        "primarySupplier": pl.Utf8,
        "cartonQty": pl.Int64,
        "minOrderQty": pl.Int64
    }

    # 3. Create DataFrame and Validate
    df = pl.DataFrame(rows, schema=schema)

    # Validation
    assert "itemCode" in df.columns
    assert "trading_date" in df.columns
    assert df.select(pl.col("itemCode").is_null().sum()).item() == 0, "Null itemCode found"
    assert df.select(pl.col("trading_date").is_null().sum()).item() == 0, "Null trading_date found"
    
    # Check duplicates
    dups = df.group_by(["itemCode", "trading_date"]).len().filter(pl.col("len") > 1)
    if dups.height > 0:
        raise ValueError(f"Found {dups.height} duplicate SKU-date records.")

    print(f"Dataframe constructed: {df.height} rows, {df['itemCode'].n_unique()} unique SKUs.")

    # 4. Save
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUTPUT)
    print(f"Successfully saved to {OUTPUT}")

if __name__ == "__main__":
    ingest()
