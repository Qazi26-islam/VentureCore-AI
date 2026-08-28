import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")

from google.genai import types

import backend.db as database
from backend.agents import finance_operations, inventory, sales


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "business_metrics.json"


class RecordedToolClient:
    def __init__(self, tool_name: str, arguments: dict):
        self.models = self
        self.tool_name = tool_name
        self.arguments = arguments
        self.calls = []
        self.tool_result = None

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            call = SimpleNamespace(name=self.tool_name, args=self.arguments)
            content = types.Content(
                role="model",
                parts=[types.Part.from_function_call(name=self.tool_name, args=self.arguments)],
            )
            return SimpleNamespace(
                function_calls=[call],
                candidates=[SimpleNamespace(content=content)],
                text=None,
            )
        function_response = kwargs["contents"][-1].parts[0].function_response
        self.tool_result = function_response.response
        return SimpleNamespace(function_calls=[], candidates=[], text="Recorded offline answer")


class AgentNumberEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text())

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "agent-evals.db"
        database.init_db()
        self._load_fixture()

    def tearDown(self):
        database.DB_PATH = self.original_database
        self.temp_dir.cleanup()

    def _load_fixture(self):
        fixture = self.fixture
        user = fixture["user"]
        organization_id = fixture["organization_id"]
        connection = database.get_connection()
        connection.execute(
            "INSERT INTO users (id, email, password_hash, salt) VALUES (?, ?, '!', 'eval')",
            (user["id"], user["email"]),
        )
        for product in fixture["products"]:
            connection.execute(
                """INSERT INTO products
                   (id, user_id, organization_id, sku, name, unit_cost_minor,
                    selling_price_minor, currency, reorder_point, lead_time_days, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'MYR', ?, ?, 'manual')""",
                (
                    product["id"], user["id"], organization_id, product["sku"], product["name"],
                    product["unit_cost_minor"], product["selling_price_minor"],
                    product["reorder_point"], product["lead_time_days"],
                ),
            )
        for customer in fixture["customers"]:
            connection.execute(
                """INSERT INTO customers (id, user_id, organization_id, name, source)
                   VALUES (?, ?, ?, ?, 'manual')""",
                (customer["id"], user["id"], organization_id, customer["name"]),
            )
        for movement in fixture["inventory_transactions"]:
            connection.execute(
                """INSERT INTO inventory_transactions
                   (id, product_id, user_id, organization_id, transaction_type,
                    quantity_change, currency, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'MYR', 'manual', ?)""",
                (
                    movement["id"], movement["product_id"], user["id"], organization_id,
                    movement["transaction_type"], movement["quantity_change"], movement["created_at"],
                ),
            )
        for order in fixture["sales_orders"]:
            connection.execute(
                """INSERT INTO sales_orders
                   (id, user_id, organization_id, customer_id, product_id, quantity,
                    unit_price_minor, total_amount_minor, currency, payment_status,
                    due_date, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'MYR', ?, ?, 'manual', ?)""",
                (
                    order["id"], user["id"], organization_id, order["customer_id"],
                    order["product_id"], order["quantity"], order["unit_price_minor"],
                    order["total_amount_minor"], order["payment_status"], order["due_date"],
                    order["created_at"],
                ),
            )
        for transaction in fixture["finance_transactions"]:
            connection.execute(
                """INSERT INTO finance_transactions
                   (id, user_id, organization_id, transaction_type, amount_minor,
                    currency, category, source, transaction_date)
                   VALUES (?, ?, ?, ?, ?, 'MYR', ?, 'manual', ?)""",
                (
                    transaction["id"], user["id"], organization_id,
                    transaction["transaction_type"], transaction["amount_minor"],
                    transaction["category"], transaction["transaction_date"],
                ),
            )
        connection.commit()
        connection.close()

    def _run_agent(self, module, tool_name):
        arguments = {
            "lookback_days": self.fixture["lookback_days"],
            "as_of": self.fixture["as_of"],
        }
        client = RecordedToolClient(tool_name, arguments)
        with patch.object(module, "client", client):
            answer = module.run(
                "Return the relevant recorded figures.",
                organization_id=self.fixture["organization_id"],
                user_id=self.fixture["user"]["id"],
            )
        self.assertEqual(answer, "Recorded offline answer")
        self.assertIsNotNone(client.tool_result)
        self.assertTrue(client.tool_result["ok"])
        return client.tool_result["data"]

    def test_inventory_agent_figures_equal_hand_calculated_answers(self):
        data = self._run_agent(inventory, "get_inventory_snapshot")
        expected = self.fixture["known_answers"]
        by_name = {item["name"]: item for item in data["items"]}
        for name, answer in expected["stock_on_hand_by_product"].items():
            self.assertEqual(by_name[name]["current_stock"], answer["value"])
        for name, answer in expected["days_of_cover_by_product"].items():
            self.assertEqual(by_name[name]["days_of_stock"], answer["value"])
            self.assertTrue(by_name[name]["source_row_ids"]["inventory_transactions"])
        self.assertEqual(
            set(data["dashboard"]["workings"]),
            {"products_count", "inventory_value_minor", "needs_attention", "estimated_reorder_cost_minor"},
        )

    def test_sales_agent_figures_equal_hand_calculated_answers(self):
        data = self._run_agent(sales, "get_sales_snapshot")
        expected = self.fixture["known_answers"]
        self.assertEqual(data["dashboard"]["revenue_minor"], expected["total_revenue_minor"]["value"])
        self.assertEqual(
            data["dashboard"]["overdue_receivables_minor"],
            expected["overdue_receivables_minor"]["value"],
        )
        by_name = {item["name"]: item for item in data["product_performance"]}
        for name, answer in expected["gross_margin_minor_by_product"].items():
            self.assertEqual(by_name[name]["gross_margin_minor"], answer["value"])
            self.assertTrue(by_name[name]["source_row_ids"]["sales_orders"])
        self.assertTrue(data["dashboard"]["workings"]["revenue_minor"]["source_row_ids"]["sales_orders"])

    def test_finance_agent_figures_equal_hand_calculated_answers(self):
        data = self._run_agent(finance_operations, "get_finance_snapshot")
        expected = self.fixture["known_answers"]["expense_totals_minor_by_category"]
        by_category = {item["category"]: item for item in data["expense_categories"]}
        for category, answer in expected.items():
            self.assertEqual(by_category[category]["amount_minor"], answer["value"])
            self.assertTrue(by_category[category]["source_row_ids"]["finance_transactions"])
        self.assertTrue(data["dashboard"]["workings"]["expenses_minor"]["source_row_ids"])

    def test_recorded_tool_selection_pass_rate(self):
        cases = self.fixture["tool_selection_cases"]
        module_by_tool = {
            "get_inventory_snapshot": inventory,
            "get_sales_snapshot": sales,
            "get_finance_snapshot": finance_operations,
        }
        passed = 0
        for case in cases:
            client = RecordedToolClient(
                case["recorded_tool"],
                {"lookback_days": self.fixture["lookback_days"], "as_of": self.fixture["as_of"]},
            )
            module = module_by_tool[case["expected_tool"]]
            with patch.object(module, "client", client):
                module.run(
                    case["question"],
                    organization_id=self.fixture["organization_id"],
                    user_id=self.fixture["user"]["id"],
                )
            submitted_question = client.calls[0]["contents"][0].parts[0].text
            self.assertEqual(submitted_question, case["question"])
            passed += case["recorded_tool"] == case["expected_tool"]
        pass_rate = passed / len(cases)
        self.assertGreaterEqual(pass_rate, 0.8, f"tool selection pass rate: {pass_rate:.1%}")


if __name__ == "__main__":
    unittest.main()
