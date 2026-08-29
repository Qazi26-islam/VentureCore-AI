import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")

import backend.db as database
from backend.tools import ToolContext, invoke_tool
from backend.workers import get_or_create_briefing, run_daily_briefing, run_threshold_alerts


class ScheduledWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "workers.db"
        database.init_db()
        conn = database.get_connection()
        conn.execute(
            "INSERT INTO users (id, email, password_hash, salt) VALUES (50, 'owner@example.com', '!', 'salt')"
        )
        conn.execute(
            """INSERT INTO products
               (id, user_id, organization_id, sku, name, category, unit_cost_minor,
                selling_price_minor, currency, reorder_point, lead_time_days, source)
               VALUES (50, 50, 1, 'TEA-1', 'Iced Tea', 'Beverages', 500, 1000,
                       'MYR', 20, 7, 'manual')"""
        )
        conn.execute(
            "INSERT INTO customers (id, user_id, organization_id, name, source) VALUES (50, 50, 1, 'Acme Cafe', 'manual')"
        )
        conn.executemany(
            """INSERT INTO inventory_transactions
               (id, product_id, user_id, organization_id, transaction_type,
                quantity_change, unit_cost_minor, currency, source, created_at)
               VALUES (?, 50, 50, 1, ?, ?, 500, 'MYR', 'manual', ?)""",
            [
                (500, "received", 100, "2026-08-01 09:00:00"),
                (501, "sold", -90, "2026-08-20 09:00:00"),
            ],
        )
        conn.execute(
            """INSERT INTO sales_orders
               (id, user_id, organization_id, customer_id, product_id, quantity,
                unit_price_minor, total_amount_minor, currency, payment_status,
                due_date, source, created_at)
               VALUES (500, 50, 1, 50, 50, 50, 1000, 50000, 'MYR', 'due',
                       '2026-08-20', 'manual', '2026-08-10 10:00:00')"""
        )
        conn.executemany(
            """INSERT INTO finance_transactions
               (id, user_id, organization_id, transaction_type, amount_minor,
                currency, category, source, transaction_date)
               VALUES (?, 50, 1, ?, ?, 'MYR', ?, 'manual', ?)""",
            [
                (500, "income", 100000, "Sales Revenue", "2026-08-10"),
                (501, "expense", 60000, "Marketing", "2026-08-15"),
                (502, "income", 80000, "Sales Revenue", "2026-07-10"),
                (503, "expense", 10000, "Marketing", "2026-07-15"),
                (504, "expense", 10000, "Marketing", "2026-06-15"),
                (505, "expense", 10000, "Marketing", "2026-05-15"),
            ],
        )
        conn.commit()
        conn.close()
        self.as_of = date(2026, 8, 29)

    def tearDown(self):
        database.DB_PATH = self.original_database
        self.temp_dir.cleanup()

    def tool_arguments(self):
        return {
            "as_of": self.as_of.isoformat(),
            "velocity_days": 30,
            "stockout_days": 14,
            "expense_period_days": 30,
            "baseline_periods": 3,
            "receivable_min_minor": 50000,
            "expense_increase_percent": 50,
            "expense_increase_min_minor": 10000,
            "cash_drop_percent": 20,
            "cash_drop_min_minor": 50000,
        }

    def test_fixture_metrics_and_briefing_are_exact_and_deterministic(self):
        result = invoke_tool(
            "get_daily_briefing_metrics",
            ToolContext(organization_id=1, user_id=50),
            self.tool_arguments(),
        )
        self.assertTrue(result.ok, result)
        self.assertEqual(result.data["cash"]["recorded_cash_balance_minor"], 90000)
        self.assertEqual(result.data["cash"]["current_net_cash_flow_minor"], 40000)
        self.assertEqual(result.data["cash"]["previous_net_cash_flow_minor"], 70000)
        self.assertEqual(result.data["cash"]["change_percent"], -43)
        self.assertEqual(result.data["overdue_receivables"][0]["amount_minor"], 50000)
        self.assertEqual(result.data["stockout_products"][0]["current_stock"], 10)
        self.assertEqual(result.data["stockout_products"][0]["days_of_cover"], 3.3)
        self.assertEqual(result.data["expense_anomalies"][0]["baseline_average_minor"], 10000)
        self.assertEqual(result.data["expense_anomalies"][0]["current_amount_minor"], 60000)

        first = get_or_create_briefing(1, 50, "2026-08-29", self.as_of)
        second = get_or_create_briefing(1, 50, "2026-08-29", self.as_of)
        self.assertEqual(first["id"], second["id"])
        self.assertIn("MYR 900.00", first["html_body"])
        self.assertIn("Acme Cafe", first["html_body"])
        self.assertIn("3.3 days of cover", first["html_body"])
        self.assertIn("Marketing", first["html_body"])
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM briefing_cache").fetchone()[0], 1)
        conn.close()

    def test_daily_job_cannot_send_twice_for_same_organization_and_period(self):
        with patch("backend.workers.send_email") as send:
            first = run_daily_briefing(1, 50, "owner@example.com", "2026-08-29", self.as_of)
            second = run_daily_briefing(1, 50, "owner@example.com", "2026-08-29", self.as_of)
        self.assertEqual(first["status"], "sent")
        self.assertEqual(second["status"], "already_processed")
        send.assert_called_once()
        conn = database.get_connection()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM notification_deliveries WHERE status = 'sent'").fetchone()[0],
            1,
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM agent_runs WHERE agent_name = 'Daily Briefing Worker'").fetchone()[0],
            1,
        )
        conn.close()

    def test_invalid_arguments_and_cross_organization_access_are_safe(self):
        invalid = self.tool_arguments()
        invalid["velocity_days"] = 0
        with patch("backend.tools.get_connection") as get_connection:
            result = invoke_tool(
                "get_daily_briefing_metrics", ToolContext(organization_id=1, user_id=50), invalid
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "invalid_arguments")
        get_connection.assert_not_called()

        isolated = invoke_tool(
            "get_daily_briefing_metrics",
            ToolContext(organization_id=2, user_id=50),
            self.tool_arguments(),
        )
        self.assertTrue(isolated.ok)
        self.assertEqual(isolated.data["cash"]["recorded_cash_balance_minor"], 0)
        self.assertEqual(isolated.data["overdue_receivables"], [])
        self.assertEqual(isolated.data["stockout_products"], [])

    def test_alert_sends_only_when_material_condition_crosses_to_active(self):
        with patch("backend.workers.send_email") as send:
            first = run_threshold_alerts(
                1, 50, "owner@example.com", "2026-08-29T09", self.as_of
            )
            second = run_threshold_alerts(
                1, 50, "owner@example.com", "2026-08-29T10", self.as_of
            )
        self.assertEqual(first["status"], "sent")
        self.assertGreater(first["crossings"], 0)
        self.assertEqual(second, {"status": "no_new_crossing", "crossings": 0})
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
