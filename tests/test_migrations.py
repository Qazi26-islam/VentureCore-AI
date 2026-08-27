import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.migrations import DOMAIN_TABLES, LEGACY_SCHEMAS, downgrade, upgrade


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "legacy.db"
        self.conn = sqlite3.connect(self.database)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, salt TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        for table in DOMAIN_TABLES:
            self.conn.execute(LEGACY_SCHEMAS[table].format(name=f'"{table}"'))
        self._seed_legacy_rows()
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def _seed_legacy_rows(self):
        self.conn.execute("INSERT INTO users (id, email, password_hash, salt) VALUES (1, 'owner@example.com', 'hash', 'salt')")
        self.conn.execute("INSERT INTO research_jobs (id, user_id, question, sections) VALUES ('job-1', 1, 'A sufficiently long question', '{}')")
        self.conn.execute("INSERT INTO follow_up_messages (job_id, role, content) VALUES ('job-1', 'user', 'Hello')")
        self.conn.execute("""INSERT INTO company_profiles
            (id, user_id, company_name, currency, monthly_budget)
            VALUES (1, 1, 'Example Co', 'MYR', 'RM 10.005')""")
        self.conn.execute("""INSERT INTO data_uploads
            (id, user_id, filename, data_type, quality_summary)
            VALUES (1, 1, 'sales.csv', 'Sales data', '{}')""")
        self.conn.execute("INSERT INTO suppliers (id, user_id, name) VALUES (1, 1, 'Supplier')")
        self.conn.execute("""INSERT INTO products
            (id, user_id, supplier_id, sku, name, unit_cost, selling_price)
            VALUES (1, 1, 1, 'SKU-1', 'Product', 1.005, 2.345)""")
        self.conn.execute("""INSERT INTO inventory_transactions
            (id, product_id, user_id, transaction_type, quantity_change, unit_cost)
            VALUES (1, 1, 1, 'received', 10, 1.005)""")
        self.conn.execute("INSERT INTO customers (id, user_id, name) VALUES (1, 1, 'Customer')")
        self.conn.execute("""INSERT INTO sales_orders
            (id, user_id, customer_id, product_id, quantity, unit_price, total_amount)
            VALUES (1, 1, 1, 1, 2, 2.345, 4.69)""")
        self.conn.execute("""INSERT INTO finance_transactions
            (id, user_id, transaction_type, amount, source, related_sale_id)
            VALUES (1, 1, 'income', 4.69, 'sale', 1)""")

    def test_upgrade_and_downgrade_round_trip(self):
        with self.assertLogs("database_migrations", level="WARNING") as captured:
            upgrade(self.conn)
        self.assertTrue(any("products.unit_cost" in message for message in captured.output))
        self.assertTrue(any("company_profiles.monthly_budget" in message for message in captured.output))

        organization = self.conn.execute("SELECT id FROM organizations").fetchone()
        self.assertEqual(organization["id"], 1)
        for table in DOMAIN_TABLES:
            columns = {row["name"]: row for row in self.conn.execute(f"PRAGMA table_info({table})")}
            self.assertEqual(columns["organization_id"]["notnull"], 1)
            self.assertIn("source", columns)
            self.assertIn("external_id", columns)
            self.assertIn("last_synced_at", columns)
            indexes = [row["name"] for row in self.conn.execute(f"PRAGMA index_list({table})")]
            self.assertIn(f"ux_{table}_org_source_external", indexes)

        product = self.conn.execute("SELECT * FROM products WHERE organization_id = 1").fetchone()
        self.assertEqual(product["unit_cost_minor"], 101)
        self.assertEqual(product["selling_price_minor"], 235)
        self.assertEqual(product["currency"], "MYR")
        self.assertEqual(product["source"], "manual")
        upload = self.conn.execute("SELECT source FROM data_uploads WHERE organization_id = 1").fetchone()
        self.assertEqual(upload["source"], "csv_import")
        monetary_columns = {
            "company_profiles": ("monthly_budget_minor",),
            "products": ("unit_cost_minor", "selling_price_minor"),
            "inventory_transactions": ("unit_cost_minor",),
            "sales_orders": ("unit_price_minor", "total_amount_minor"),
            "finance_transactions": ("amount_minor",),
        }
        for table, names in monetary_columns.items():
            columns = {row["name"]: row["type"].upper() for row in self.conn.execute(f"PRAGMA table_info({table})")}
            for name in names:
                self.assertEqual(columns[name], "INTEGER")
            self.assertIn("currency", columns)

        downgrade(self.conn)
        tables = {row["name"] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("organizations", tables)
        self.assertNotIn("schema_migrations", tables)
        legacy_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(products)")}
        self.assertIn("unit_cost", legacy_columns)
        self.assertNotIn("unit_cost_minor", legacy_columns)
        legacy_product = self.conn.execute("SELECT unit_cost, selling_price FROM products WHERE id = 1").fetchone()
        self.assertAlmostEqual(legacy_product["unit_cost"], 1.01)
        self.assertAlmostEqual(legacy_product["selling_price"], 2.35)

    def test_external_identity_index_is_unique_only_when_present(self):
        upgrade(self.conn)
        self.conn.execute("""INSERT INTO customers
            (user_id, organization_id, name, source, external_id)
            VALUES (1, 1, 'Imported customer', 'csv_import', 'customer-1')""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO customers
                (user_id, organization_id, name, source, external_id)
                VALUES (1, 1, 'Duplicate', 'csv_import', 'customer-1')""")
        self.conn.execute("INSERT INTO customers (user_id, organization_id, name, source) VALUES (1, 1, 'Manual A', 'manual')")
        self.conn.execute("INSERT INTO customers (user_id, organization_id, name, source) VALUES (1, 1, 'Manual B', 'manual')")


if __name__ == "__main__":
    unittest.main()
