import polars as pl
import lightgbm as lgb
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

def generate_report():
    # 1. Load data and get the latest week for each SKU
    df = pl.read_parquet('features/weekly_features.parquet').filter(pl.col('is_complete_week') == True)
    
    # Get the most recent week of data for each SKU to predict the *next* 30 days
    latest_data = df.sort('week_start').group_by('itemCode').tail(1)
    
    # 2. Load Models
    model_units = lgb.Booster(model_file='artifacts/lgbm_units.txt')
    # We will just predict on units for this demo
    latest_units = latest_data.filter(pl.col('uom_class') == 'units')
    
    CATEGORICAL = ["itemCode", "departmentName", "commodityName", "primarySupplier", "tier"]
    FEATURE_COLS = (
        [f"lag_{w}" for w in (1, 2, 3, 4, 8, 13, 52)]
        + [f"roll_mean_{w}" for w in (4, 8, 13)]
        + [f"roll_std_{w}" for w in (4, 8, 13)]
        + ["woy_sin", "woy_cos", "cal_month", "cal_quarter",
           "prev_week_completeness", "prev_asp_mean",
           "cartonQty", "minOrderQty"]
        + CATEGORICAL
    )

    pdf = latest_units.select(FEATURE_COLS).to_pandas()
    for c in CATEGORICAL:
        pdf[c] = pdf[c].astype("category")

    # Predict next week, then multiply by 4.3 for a ~30 day forecast
    # (A more advanced version would autoregressively forecast 4 weeks)
    weekly_pred = model_units.predict(pdf)
    monthly_pred = weekly_pred * 4.3 
    
    # 3. Build the output table
    results = []
    
    np.random.seed(42) # For reproducible mock data
    
    for i, row in enumerate(latest_units.iter_rows(named=True)):
        item_id = row['itemCode']
        item_name = row['commodityName']
        
        # MOCK DATA: We don't have real inventory in the parquet, so we simulate it
        current_stock = int(np.random.randint(0, int(monthly_pred[i] * 1.5) + 20))
        
        pred_demand_30d = int(monthly_pred[i])
        
        # Simple Reorder Logic
        shortfall = pred_demand_30d - current_stock
        if shortfall > 0:
            recommended_order = int(np.ceil(shortfall / max(1, row['minOrderQty']))) * max(1, row['minOrderQty'])
            if shortfall > (pred_demand_30d * 0.5):
                decision = "CREATE PO"
            else:
                decision = "REVIEW REQUIRED"
        else:
            recommended_order = 0
            decision = "NO PO REQUIRED"
            
        # Stockout Risk & Date
        daily_run_rate = pred_demand_30d / 30.0
        days_of_supply = current_stock / daily_run_rate if daily_run_rate > 0 else 999
        
        if days_of_supply < 7:
            risk = "Critical"
        elif days_of_supply < 14:
            risk = "High"
        elif days_of_supply < 21:
            risk = "Medium"
        else:
            risk = "Low"
            
        if days_of_supply < 30:
            stockout_date = (datetime.now() + timedelta(days=days_of_supply)).strftime('%d %b %Y')
        else:
            stockout_date = "N/A"
            
        # Mock confidence based on Tier (A is higher confidence)
        base_conf = 85 if row['tier'] == 'A' else 75
        confidence = f"{base_conf + np.random.randint(-5, 10)}%"
        
        results.append({
            "Item ID": item_id,
            "Item Name": item_name,
            "Current Stock": current_stock,
            "Predicted Demand (30 Days)": pred_demand_30d,
            "Recommended Order Qty": recommended_order,
            "Reorder Decision": decision,
            "Stockout Risk": risk,
            "Expected Stockout Date": stockout_date,
            "Model Confidence": confidence
        })
        
        if len(results) >= 5: # Just show top 5 for the demo like the screenshot
            break
            
    # Output as Markdown Table
    df_out = pd.DataFrame(results)
    print(df_out.to_markdown(index=False))

if __name__ == '__main__':
    generate_report()
