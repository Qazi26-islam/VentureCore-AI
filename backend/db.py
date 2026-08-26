import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "app.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS research_jobs (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            title TEXT,
            report TEXT,
            sections TEXT,
            favorite INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS follow_up_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES research_jobs(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            company_name TEXT NOT NULL,
            industry TEXT,
            country TEXT,
            currency TEXT DEFAULT 'MYR',
            products_services TEXT,
            target_customers TEXT,
            main_competitors TEXT,
            monthly_budget TEXT,
            business_goals TEXT,
            business_stage TEXT DEFAULT 'Existing business',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS data_uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            data_type TEXT NOT NULL,
            row_count INTEGER DEFAULT 0,
            column_count INTEGER DEFAULT 0,
            quality_summary TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT,
            phone TEXT,
            lead_time_days INTEGER DEFAULT 7,
            payment_terms TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            supplier_id INTEGER,
            sku TEXT,
            name TEXT NOT NULL,
            category TEXT,
            unit_cost REAL DEFAULT 0,
            selling_price REAL DEFAULT 0,
            reorder_point REAL DEFAULT 0,
            lead_time_days INTEGER DEFAULT 7,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, sku),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            transaction_type TEXT NOT NULL,
            quantity_change REAL NOT NULL,
            unit_cost REAL,
            reference_note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            segment TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            customer_id INTEGER,
            product_id INTEGER NOT NULL,
            quantity REAL NOT NULL,
            unit_price REAL NOT NULL,
            total_amount REAL NOT NULL,
            payment_status TEXT DEFAULT 'paid',
            due_date TEXT,
            reference_note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (customer_id) REFERENCES customers(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS finance_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            transaction_type TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT,
            description TEXT,
            source TEXT DEFAULT 'manual',
            related_sale_id INTEGER UNIQUE,
            transaction_date TEXT DEFAULT CURRENT_DATE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (related_sale_id) REFERENCES sales_orders(id)
        )
    """)

    cursor.execute("""
        INSERT OR IGNORE INTO finance_transactions
        (user_id, transaction_type, amount, category, description, source, related_sale_id, transaction_date)
        SELECT user_id, 'income', total_amount, 'Sales Revenue',
               'Payment received for sale #' || id, 'sale', id, date(created_at)
        FROM sales_orders WHERE payment_status = 'paid'
    """)

    # Add new columns to existing tables if upgrading from an older version
    existing_cols = [row["name"] for row in cursor.execute("PRAGMA table_info(research_jobs)")]
    if "title" not in existing_cols:
        cursor.execute("ALTER TABLE research_jobs ADD COLUMN title TEXT")
    if "favorite" not in existing_cols:
        cursor.execute("ALTER TABLE research_jobs ADD COLUMN favorite INTEGER DEFAULT 0")

    conn.commit()
    conn.close()
    print("[DB] Database initialized.")
