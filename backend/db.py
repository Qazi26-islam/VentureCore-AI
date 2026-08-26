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

    # Add new columns to existing tables if upgrading from an older version
    existing_cols = [row["name"] for row in cursor.execute("PRAGMA table_info(research_jobs)")]
    if "title" not in existing_cols:
        cursor.execute("ALTER TABLE research_jobs ADD COLUMN title TEXT")
    if "favorite" not in existing_cols:
        cursor.execute("ALTER TABLE research_jobs ADD COLUMN favorite INTEGER DEFAULT 0")

    conn.commit()
    conn.close()
    print("[DB] Database initialized.")
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

    # Add new columns to existing tables if upgrading from an older version
    existing_cols = [row["name"] for row in cursor.execute("PRAGMA table_info(research_jobs)")]
    if "title" not in existing_cols:
        cursor.execute("ALTER TABLE research_jobs ADD COLUMN title TEXT")
    if "favorite" not in existing_cols:
        cursor.execute("ALTER TABLE research_jobs ADD COLUMN favorite INTEGER DEFAULT 0")

    conn.commit()
    conn.close()
    print("[DB] Database initialized.")
