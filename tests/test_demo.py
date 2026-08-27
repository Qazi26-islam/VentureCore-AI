import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")

import backend.db as database
from backend.main import app
from backend.seed_demo import SOURCE, seed_demo
from fastapi.testclient import TestClient


class PublicDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "demo.db"
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        database.DB_PATH = self.original_database
        self.temp_dir.cleanup()

    def _demo_counts(self):
        tables = (
            "company_profiles",
            "data_uploads",
            "suppliers",
            "products",
            "inventory_transactions",
            "customers",
            "sales_orders",
            "finance_transactions",
            "research_jobs",
            "follow_up_messages",
        )
        conn = database.get_connection()
        counts = {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE organization_id = ?",
                (database.DEMO_ORGANIZATION_ID,),
            ).fetchone()[0]
            for table in tables
        }
        conn.close()
        return counts

    def test_seed_is_idempotent_reconciled_and_contains_expected_anomalies(self):
        result = seed_demo(today=date(2026, 8, 28))
        first_counts = self._demo_counts()
        repeated_result = seed_demo(today=date(2026, 8, 28))
        self.assertEqual(first_counts, self._demo_counts())
        self.assertEqual(result["sales_total_minor"], repeated_result["sales_total_minor"])
        self.assertEqual(first_counts["sales_orders"], 60)

        conn = database.get_connection()
        products = conn.execute(
            """SELECT p.id, p.name, p.reorder_point,
                      COALESCE(SUM(t.quantity_change), 0) AS stock
                 FROM products p
                 LEFT JOIN inventory_transactions t
                   ON t.product_id = p.id AND t.organization_id = p.organization_id
                WHERE p.organization_id = ?
                GROUP BY p.id ORDER BY p.id""",
            (database.DEMO_ORGANIZATION_ID,),
        ).fetchall()
        for product in products:
            sold = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM sales_orders WHERE product_id = ? AND organization_id = ?",
                (product["id"], database.DEMO_ORGANIZATION_ID),
            ).fetchone()[0]
            movement_sold = conn.execute(
                """SELECT COALESCE(-SUM(quantity_change), 0) FROM inventory_transactions
                    WHERE product_id = ? AND organization_id = ? AND transaction_type = 'sold'""",
                (product["id"], database.DEMO_ORGANIZATION_ID),
            ).fetchone()[0]
            self.assertEqual(sold, movement_sold)
            self.assertEqual(product["stock"], repeated_result["stock_balances"][product["id"]])

        cold_brew = next(product for product in products if product["name"] == "Cold Brew Bottle")
        self.assertEqual(cold_brew["stock"], 24)
        self.assertLess(cold_brew["stock"], cold_brew["reorder_point"])

        overdue = conn.execute(
            """SELECT COUNT(*) FROM sales_orders WHERE organization_id = ?
                 AND payment_status = 'due' AND date(due_date) < date('2026-08-28')""",
            (database.DEMO_ORGANIZATION_ID,),
        ).fetchone()[0]
        self.assertEqual(overdue, 1)

        marketing = conn.execute(
            """SELECT amount_minor FROM finance_transactions
                WHERE organization_id = ? AND source = ? AND category = 'Marketing'
                ORDER BY transaction_date""",
            (database.DEMO_ORGANIZATION_ID, SOURCE),
        ).fetchall()
        self.assertEqual([row[0] for row in marketing[-3:]], [60_000, 105_000, 180_000])

        recorded_sales = conn.execute(
            "SELECT SUM(total_amount_minor) FROM sales_orders WHERE organization_id = ?",
            (database.DEMO_ORGANIZATION_ID,),
        ).fetchone()[0]
        recorded_paid = conn.execute(
            """SELECT SUM(total_amount_minor) FROM sales_orders
                WHERE organization_id = ? AND payment_status = 'paid'""",
            (database.DEMO_ORGANIZATION_ID,),
        ).fetchone()[0]
        linked_income = conn.execute(
            """SELECT SUM(amount_minor) FROM finance_transactions
                WHERE organization_id = ? AND category = 'Sales Revenue'""",
            (database.DEMO_ORGANIZATION_ID,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(recorded_sales, result["sales_total_minor"])
        self.assertEqual(recorded_paid, linked_income)

    def test_visitor_can_enter_populated_demo_and_real_accounts_cannot_see_it(self):
        root = self.client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertIn("View demo workspace", root.text)

        started = self.client.post("/demo/start")
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.json()["organization_id"], database.DEMO_ORGANIZATION_ID)
        self.assertTrue(self.client.get("/auth/me").json()["demo_mode"])

        inventory = self.client.get("/inventory/dashboard")
        sales = self.client.get("/sales/dashboard")
        finance = self.client.get("/finance/dashboard")
        history = self.client.get("/research/history")
        self.assertEqual(len(inventory.json()), 5)
        self.assertEqual(sales.status_code, 200, sales.text)
        self.assertGreater(sales.json()["orders_30d"], 0)
        self.assertEqual(finance.status_code, 200, finance.text)
        self.assertTrue(history.json())

        signup = self.client.post(
            "/auth/signup",
            json={"email": "real-owner@example.com", "password": "strong-password"},
        )
        self.assertEqual(signup.status_code, 200, signup.text)
        self.assertEqual(self.client.get("/inventory/dashboard").json(), [])
        self.assertEqual(self.client.get("/sales/customers").json(), [])
        self.assertEqual(self.client.get("/research/history").json(), [])
        self.assertEqual(self._demo_counts()["products"], 5)

    def test_every_business_write_path_rejects_demo_session(self):
        self.assertEqual(self.client.post("/demo/start").status_code, 200)
        conn = database.get_connection()
        product_id = conn.execute(
            "SELECT id FROM products WHERE organization_id = ? ORDER BY id LIMIT 1",
            (database.DEMO_ORGANIZATION_ID,),
        ).fetchone()[0]
        due_sale_id = conn.execute(
            """SELECT id FROM sales_orders WHERE organization_id = ?
                AND payment_status = 'due' LIMIT 1""",
            (database.DEMO_ORGANIZATION_ID,),
        ).fetchone()[0]
        conn.close()
        before = self._demo_counts()

        requests = (
            self.client.put("/company/profile", json={"company_name": "Changed Demo"}),
            self.client.post(
                "/company/data/quality",
                data={"data_type": "Sales data"},
                files={"file": ("sales.csv", b"amount\n10\n", "text/csv")},
            ),
            self.client.post("/inventory/suppliers", json={"name": "New Supplier"}),
            self.client.post("/inventory/products", json={"name": "New Product"}),
            self.client.post(
                f"/inventory/products/{product_id}/movement",
                json={"transaction_type": "received", "quantity": 1},
            ),
            self.client.post("/sales/customers", json={"name": "New Customer"}),
            self.client.post(
                "/sales/orders",
                json={"product_id": product_id, "quantity": 1},
            ),
            self.client.post(f"/sales/orders/{due_sale_id}/mark-paid"),
            self.client.post(
                "/finance/transactions",
                json={"transaction_type": "expense", "amount_minor": 100},
            ),
            self.client.post(
                "/research/start",
                json={"question": "Research this demo company now"},
            ),
            self.client.put(
                "/research/job/demo-management-report/rename",
                json={"title": "Changed title"},
            ),
            self.client.put(
                "/research/job/demo-management-report/favorite",
                json={"favorite": False},
            ),
            self.client.delete("/research/job/demo-management-report"),
            self.client.post(
                "/research/job/demo-management-report/message",
                json={"message": "Change this report"},
            ),
        )
        for response in requests:
            self.assertEqual(response.status_code, 403, response.text)
            self.assertIn("read-only", response.json()["detail"])
        self.assertEqual(before, self._demo_counts())


if __name__ == "__main__":
    unittest.main()
