import sys
import os
import json
import csv
from pathlib import Path
from datetime import date
import uuid

# add model directory to sys.path so we can import from it
sys.path.append(os.path.abspath('pearcedale-lgbm-model/model'))
sys.path.append(os.path.abspath('model_testing'))

from api import local_dataset_snapshot
from modal_deploy import _build_features_from_model_input
import run_forecasting as model

# Change working directory so run_forecasting can find features/ and data/
ROOT_DIR = os.path.abspath('.')
os.chdir(os.path.abspath('pearcedale-lgbm-model/model'))

def run_local_model(as_of, data_source, local_data=None):
    run_id = uuid.uuid4().hex
    output_dir = Path(ROOT_DIR) / f"model_testing/runs/{run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    model.OUTPUT_DIR = output_dir
    
    features_df = None
    promo_daily_df = None
    if data_source == "LOCAL_DATASET":
        model_input = {
            "sales": local_data['sales'],
            "promotions": local_data['promotions']
        }
        features_df, promo_daily_df = _build_features_from_model_input(model_input)
        
    model.main(date.fromisoformat(as_of), features_df=features_df, promo_daily_df=promo_daily_df)
    
    with open(output_dir / "evaluation_report.json") as f:
        report = json.load(f)
        
    forecasts = []
    with open(output_dir / "hybrid_predictions_15day.csv", newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            forecasts.append(row)
            
    return report, forecasts

def main():
    as_of = "2026-09-16"
    print(f"Running equivalence check for as_of_date={as_of}...")
    
    print("Running SAMPLE (bundled parquet)...")
    s_report, s_forecasts = run_local_model(as_of, "SAMPLE")
    
    print("Fetching local dataset from Supabase...")
    local_data = local_dataset_snapshot(date.fromisoformat(as_of))
    
    print("Running LOCAL_DATASET (Supabase data)...")
    l_report, l_forecasts = run_local_model(as_of, "LOCAL_DATASET", local_data)
    
    s_active = s_report.get('active_promotions') if 'active_promotions' in s_report else s_report.get('total_active_promotions', 'Unknown')
    l_active = l_report.get('active_promotions') if 'active_promotions' in l_report else l_report.get('total_active_promotions', 'Unknown')
    
    print(f"SAMPLE active promotions detected: {s_active}")
    print(f"LOCAL_DATASET active promotions detected: {l_active}")
    
    s_dict = {f['itemCode']: float(f['hybrid_forecast_15d']) for f in s_forecasts}
    l_dict = {f['itemCode']: float(f['hybrid_forecast_15d']) for f in l_forecasts}
    
    max_diff = 0
    diff_count = 0
    for code in s_dict:
        if code not in l_dict:
            print(f"Missing SKU in LOCAL_DATASET: {code}")
            continue
        diff = abs(s_dict[code] - l_dict[code])
        if diff > 0.1:
            diff_count += 1
        max_diff = max(max_diff, diff)
        
    print(f"Max difference in forecast: {max_diff:.4f}")
    print(f"Number of SKUs with >0.1 diff: {diff_count} out of {len(s_dict)}")
    print("Equivalence test completed.")

if __name__ == "__main__":
    main()
