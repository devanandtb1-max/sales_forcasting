import sys
sys.path.append('model_testing')
from api import local_dataset_snapshot
from datetime import date

def test():
    try:
        as_of = date.fromisoformat("2026-09-16")
        res = local_dataset_snapshot(as_of)
        
        print("--- SALES KEYS ---")
        if res['sales']:
            print(list(res['sales'][0].keys()))
            # Spot check nulls
            null_quantity = [s for s in res['sales'] if s['quantity'] is None]
            if null_quantity:
                print("Found sales row with quantity: null", null_quantity[0])
            else:
                print("No sales row with quantity: null found.")
                
        print("--- PROMO KEYS ---")
        if res['promotions']:
            print(list(res['promotions'][0].keys()))
            null_price = [p for p in res['promotions'] if p['promo_price'] is None]
            if null_price:
                print("Found promo row with promo_price: null", null_price[0])
            else:
                print("No promo row with promo_price: null found.")

        print(f"sales_row_count: {res['sales_row_count']}")
        print(f"promotion_row_count: {res['promotion_row_count']}")

    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test()
