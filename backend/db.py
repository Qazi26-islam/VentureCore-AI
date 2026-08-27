import sqlite3
from pathlib import Path

from backend.migrations import upgrade


DB_PATH = Path(__file__).resolve().parent.parent / "app.db"
DEFAULT_ORGANIZATION_ID = 1
DEMO_ORGANIZATION_ID = 2
DEMO_USER_EMAIL = "demo@venturecore.invalid"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_demo_user_id() -> int:
    conn = get_connection()
    row = conn.execute("SELECT id FROM users WHERE email = ?", (DEMO_USER_EMAIL,)).fetchone()
    conn.close()
    if row is None:
        raise RuntimeError("Demo data has not been seeded.")
    return int(row["id"])


def init_db():
    conn = get_connection()
    upgrade(conn)
    conn.execute(
        """INSERT OR IGNORE INTO finance_transactions
           (user_id, organization_id, transaction_type, amount_minor, currency, category,
            description, source, external_id, related_sale_id, transaction_date)
           SELECT s.user_id, s.organization_id, 'income', s.total_amount_minor, s.currency,
                  'Sales Revenue', 'Payment received for sale #' || s.id, 'sale',
                  CAST(s.id AS TEXT), s.id, date(s.created_at)
           FROM sales_orders AS s
           WHERE s.organization_id = ? AND s.payment_status = 'paid'""",
        (DEFAULT_ORGANIZATION_ID,),
    )
    conn.commit()
    conn.close()
