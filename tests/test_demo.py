import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch

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
        self.assertEqual(cold_brew["stock"], 12)
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
        self.assertEqual([row[0] for row in marketing[-3:]], [20_000, 35_000, 60_000])

        seeded_text = conn.execute(
            """SELECT description FROM finance_transactions WHERE organization_id = ?
               UNION ALL
               SELECT reference_note FROM sales_orders WHERE organization_id = ?""",
            (database.DEMO_ORGANIZATION_ID, database.DEMO_ORGANIZATION_ID),
        ).fetchall()
        self.assertFalse(any("fictional" in (row[0] or "").lower() for row in seeded_text))

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
        operating_surplus = result["paid_sales_minor"] - result["expenses_total_minor"]
        self.assertGreater(operating_surplus, 0)
        self.assertLess(operating_surplus * 100, result["paid_sales_minor"] * 25)

    def test_visitor_can_enter_populated_demo_and_real_accounts_cannot_see_it(self):
        root = self.client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertTrue(self.client.get("/auth/me").json()["demo_mode"])
        briefing = self.client.get("/briefings/demo")
        self.assertEqual(briefing.status_code, 200, briefing.text)
        metrics = briefing.json()["content"]["metrics"]
        self.assertTrue(metrics["stockout_products"])
        self.assertTrue(metrics["overdue_receivables"])
        self.assertTrue(metrics["expense_anomalies"])
        self.assertTrue(briefing.json()["content"]["actions"])

        inventory = self.client.get("/inventory/dashboard")
        sales = self.client.get("/sales/dashboard")
        finance = self.client.get("/finance/dashboard")
        history = self.client.get("/research/history")
        self.assertEqual(len(inventory.json()["items"]), 5)
        self.assertEqual(sales.status_code, 200, sales.text)
        self.assertGreater(sales.json()["orders_30d"], 0)
        self.assertEqual(finance.status_code, 200, finance.text)
        self.assertTrue(history.json())

        signup = self.client.post(
            "/auth/signup",
            json={"email": "real-owner@example.com", "password": "strong-password"},
        )
        self.assertEqual(signup.status_code, 200, signup.text)
        self.assertEqual(self.client.get("/").status_code, 200)
        identity = self.client.get("/auth/me").json()
        self.assertTrue(identity["logged_in"])
        self.assertFalse(identity.get("demo_mode", False))
        self.assertEqual(self.client.get("/inventory/dashboard").json()["items"], [])
        self.assertEqual(self.client.get("/sales/customers").json(), [])
        self.assertEqual(self.client.get("/research/history").json(), [])
        self.assertEqual(self._demo_counts()["products"], 5)

    def test_demo_page_load_never_invokes_a_model_client(self):
        client_paths = (
            "backend.agents.competitor.client.models.generate_content",
            "backend.agents.finance_operations.client.models.generate_content",
            "backend.agents.financial.client.models.generate_content",
            "backend.agents.followup.client.models.generate_content",
            "backend.agents.inventory.client.models.generate_content",
            "backend.agents.market_research.client.models.generate_content",
            "backend.agents.opportunity_finder.client.models.generate_content",
            "backend.agents.sales.client.models.generate_content",
            "backend.agents.scope_check.client.models.generate_content",
            "backend.agents.synthesis.client.models.generate_content",
        )
        with ExitStack() as stack:
            mocks = [
                stack.enter_context(patch(path, side_effect=AssertionError("model called during demo page load")))
                for path in client_paths
            ]
            self.assertEqual(self.client.get("/").status_code, 200)
            self.assertTrue(self.client.get("/auth/me").json()["demo_mode"])
            self.assertEqual(self.client.get("/briefings/demo").status_code, 200)
            self.assertTrue(all(mock.call_count == 0 for mock in mocks))

    def test_exit_demo_clears_demo_session_without_reloading_root(self):
        self.client.get("/")
        self.assertTrue(self.client.get("/auth/me").json()["demo_mode"])

        exited = self.client.post("/demo/exit")

        self.assertEqual(exited.status_code, 200, exited.text)
        identity = self.client.get("/auth/me").json()
        self.assertFalse(identity["logged_in"])
        self.assertFalse(identity.get("demo_mode", False))

    def test_demo_briefing_figures_reconcile_to_seeded_source_rows(self):
        self.client.get("/")
        payload = self.client.get("/briefings/demo").json()["content"]
        metrics = payload["metrics"]
        self.assertEqual(metrics["currency"], "USD")
        conn = database.get_connection()

        cash = metrics["cash"]
        self.assertEqual(cash["inflows_minor"] - cash["outflows_minor"], cash["recorded_cash_balance_minor"])
        self.assertGreater(cash["recorded_cash_balance_minor"], 0)
        self.assertEqual(cash["inflow_count"] + cash["outflow_count"], len(cash["workings_rows"]))
        self.assertEqual(cash["trend"][-1]["ending_balance_minor"], cash["recorded_cash_balance_minor"])
        subtotals = cash["category_subtotals"]
        self.assertEqual(
            sum(item["amount_minor"] for item in subtotals if item["transaction_type"] == "income"),
            cash["inflows_minor"],
        )
        self.assertEqual(
            sum(item["amount_minor"] for item in subtotals if item["transaction_type"] == "expense"),
            cash["outflows_minor"],
        )
        self.assertEqual(sum(item["row_count"] for item in subtotals), len(cash["workings_rows"]))
        self.assertEqual(
            sorted(row_id for item in subtotals for row_id in item["source_row_ids"]),
            sorted(cash["source_row_ids"]["finance_transactions"]),
        )

        for receivable in metrics["overdue_receivables"]:
            row = conn.execute(
                "SELECT total_amount_minor, organization_id FROM sales_orders WHERE id = ?",
                (receivable["id"],),
            ).fetchone()
            self.assertEqual(row["organization_id"], database.DEMO_ORGANIZATION_ID)
            self.assertEqual(row["total_amount_minor"], receivable["amount_minor"])
            self.assertGreater(receivable["days_overdue"], 0)
            self.assertEqual(receivable["workings_rows"][0]["id"], receivable["id"])

        for product in metrics["stockout_products"]:
            stock = conn.execute(
                """SELECT COALESCE(SUM(quantity_change), 0) FROM inventory_transactions
                    WHERE organization_id = ? AND product_id = ?""",
                (database.DEMO_ORGANIZATION_ID, product["product_id"]),
            ).fetchone()[0]
            sold = conn.execute(
                """SELECT COALESCE(SUM(quantity), 0) FROM sales_orders
                    WHERE organization_id = ? AND product_id = ?
                      AND date(created_at) >= date(?, '-29 days') AND date(created_at) <= date(?)""",
                (database.DEMO_ORGANIZATION_ID, product["product_id"], metrics["as_of"], metrics["as_of"]),
            ).fetchone()[0]
            self.assertEqual(stock, product["current_stock"])
            expected_cover = round(float(stock) / (float(sold) / 30), 1)
            self.assertEqual(expected_cover, product["days_of_cover"])

        for anomaly in metrics["expense_anomalies"]:
            current_ids = anomaly["source_row_ids"]["finance_transactions"]
            baseline_ids = anomaly["baseline_source_row_ids"]["finance_transactions"]
            current = sum(
                conn.execute(
                    "SELECT amount_minor FROM finance_transactions WHERE id = ? AND organization_id = ?",
                    (row_id, database.DEMO_ORGANIZATION_ID),
                ).fetchone()[0]
                for row_id in current_ids
            )
            baseline_total = sum(
                conn.execute(
                    "SELECT amount_minor FROM finance_transactions WHERE id = ? AND organization_id = ?",
                    (row_id, database.DEMO_ORGANIZATION_ID),
                ).fetchone()[0]
                for row_id in baseline_ids
            )
            expected_baseline = int(
                (Decimal(baseline_total) / Decimal(3)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            self.assertEqual(current, anomaly["current_amount_minor"])
            self.assertEqual(expected_baseline, anomaly["baseline_average_minor"])
        conn.close()

        self.assertTrue(payload["actions"])

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
