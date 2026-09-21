import os
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

def create_schema_and_tables(conn):
    with conn.cursor() as cur:
        # Create schema
        cur.execute("CREATE SCHEMA IF NOT EXISTS po_system;")
        
        # Create Populated Tables
        cur.execute("""
            DROP TABLE IF EXISTS po_system.sales_history CASCADE;
            CREATE TABLE po_system.sales_history (
                id               BIGSERIAL PRIMARY KEY,
                item_code        VARCHAR(50)  NOT NULL,
                trading_date     DATE         NOT NULL,
                quantity         INTEGER,
                returns_qty      NUMERIC      NOT NULL,
                asp              NUMERIC,
                unit_margin      NUMERIC,
                uom_class        VARCHAR(50)  NOT NULL,
                tier             VARCHAR(50)  NOT NULL,
                department_name  VARCHAR(100) NOT NULL,
                commodity_name   VARCHAR(200) NOT NULL,
                primary_supplier VARCHAR(50)  NOT NULL,
                carton_qty       INTEGER      NOT NULL,
                min_order_qty    INTEGER      NOT NULL
            );
            CREATE INDEX ON po_system.sales_history (item_code, trading_date);
        """)
        
        cur.execute("""
            DROP TABLE IF EXISTS po_system.promotions CASCADE;
            CREATE TABLE po_system.promotions (
                id                BIGSERIAL PRIMARY KEY,
                item_code         VARCHAR(50) NOT NULL,
                trading_date      DATE        NOT NULL,
                regular_price     NUMERIC,
                promo_active      BOOLEAN     NOT NULL,
                promo_price       NUMERIC,
                discount_pct      NUMERIC,
                promo_type_group  VARCHAR(50),
                n_stacked_promos  INTEGER     NOT NULL,
                promo_status      VARCHAR(20) NOT NULL
            );
            CREATE INDEX ON po_system.promotions (item_code, trading_date);
        """)
        
        # Create Deferred Empty Tables
        cur.execute("""
            CREATE TABLE IF NOT EXISTS po_system.suppliers (
                id BIGSERIAL PRIMARY KEY,
                supplier_name VARCHAR(255) NOT NULL,
                lead_time_days INT
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS po_system.products (
                id BIGSERIAL PRIMARY KEY,
                item_code VARCHAR(50) UNIQUE NOT NULL,
                description TEXT,
                supplier_id BIGINT REFERENCES po_system.suppliers(id)
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS po_system.inventory (
                id BIGSERIAL PRIMARY KEY,
                item_code VARCHAR(50) REFERENCES po_system.products(item_code),
                qoh NUMERIC,
                as_of_date DATE
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS po_system.purchase_orders (
                id BIGSERIAL PRIMARY KEY,
                supplier_id BIGINT REFERENCES po_system.suppliers(id),
                order_date DATE,
                status VARCHAR(50)
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS po_system.purchase_order_lines (
                id BIGSERIAL PRIMARY KEY,
                po_id BIGINT REFERENCES po_system.purchase_orders(id),
                item_code VARCHAR(50) REFERENCES po_system.products(item_code),
                ordered_qty NUMERIC
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS po_system.forecast_runs (
                id BIGSERIAL PRIMARY KEY,
                run_date TIMESTAMP,
                model_version VARCHAR(50)
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS po_system.forecast_outputs (
                id BIGSERIAL PRIMARY KEY,
                run_id BIGINT REFERENCES po_system.forecast_runs(id),
                item_code VARCHAR(50) REFERENCES po_system.products(item_code),
                forecast_date DATE,
                predicted_qty NUMERIC
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS po_system.po_recommendations (
                id BIGSERIAL PRIMARY KEY,
                run_id BIGINT REFERENCES po_system.forecast_runs(id),
                item_code VARCHAR(50) REFERENCES po_system.products(item_code),
                recommended_qty NUMERIC
            );
        """)
        conn.commit()
        print("Schema and tables created successfully.")

def migrate_data(conn):
    # Load data, replace NaN with None for explicit NULL handling in psycopg2
    print("Loading parquet files...")
    sales_df = pd.read_parquet("pearcedale-lgbm-model/processed_output/clean_daily_sales.parquet")
    sales_cols = ['itemCode', 'trading_date', 'quantity', 'returns_qty', 'asp', 'unit_margin', 'uom_class', 'tier', 'departmentName', 'commodityName', 'primarySupplier', 'cartonQty', 'minOrderQty']
    sales_df = sales_df[sales_cols]
    sales_df = sales_df.astype(object).where(pd.notnull(sales_df), None)
    
    promo_df = pd.read_parquet("pearcedale-lgbm-model/processed_output/promotions_daily.parquet")
    promo_cols = ['itemCode', 'trading_date', 'regular_price', 'promo_active', 'promo_price', 'discount_pct', 'promo_type_group', 'n_stacked_promos', 'promo_status']
    promo_df = promo_df[promo_cols]
    promo_df = promo_df.astype(object).where(pd.notnull(promo_df), None)

    sales_records = list(sales_df.itertuples(index=False, name=None))
    promo_records = list(promo_df.itertuples(index=False, name=None))
    
    with conn.cursor() as cur:
        # Truncate to ensure clean migration if re-run
        cur.execute("TRUNCATE TABLE po_system.sales_history RESTART IDENTITY CASCADE;")
        cur.execute("TRUNCATE TABLE po_system.promotions RESTART IDENTITY CASCADE;")
        
        print(f"Inserting {len(sales_records)} rows into po_system.sales_history...")
        execute_values(
            cur,
            """INSERT INTO po_system.sales_history 
               (item_code, trading_date, quantity, returns_qty, asp, unit_margin, uom_class, tier, department_name, commodity_name, primary_supplier, carton_qty, min_order_qty) 
               VALUES %s""",
            sales_records,
            page_size=10000
        )
        
        print(f"Inserting {len(promo_records)} rows into po_system.promotions...")
        execute_values(
            cur,
            """INSERT INTO po_system.promotions 
               (item_code, trading_date, regular_price, promo_active, promo_price, discount_pct, promo_type_group, n_stacked_promos, promo_status) 
               VALUES %s""",
            promo_records,
            page_size=10000
        )
        conn.commit()
        print("Data migration completed successfully.")
        
        # Verification
        cur.execute("SELECT COUNT(*) FROM po_system.sales_history;")
        sales_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM po_system.promotions;")
        promo_count = cur.fetchone()[0]
        print(f"Verification: po_system.sales_history has {sales_count} rows.")
        print(f"Verification: po_system.promotions has {promo_count} rows.")

if __name__ == "__main__":
    load_dotenv()
    db_url = os.getenv("DATABASE_URL_DIRECT")
    if not db_url:
        raise ValueError("DATABASE_URL_DIRECT is missing in .env")
    
    print("Connecting to PostgreSQL...")
    conn = psycopg2.connect(db_url)
    try:
        create_schema_and_tables(conn)
        migrate_data(conn)
    finally:
        conn.close()
