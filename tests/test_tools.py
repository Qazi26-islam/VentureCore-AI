import inspect
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
from backend.agents import finance_operations, inventory, opportunity_finder, sales
from backend.main import app
from backend.seed_demo import seed_demo
from backend.tools import TOOL_REGISTRY, ToolContext, invoke_tool
from fastapi.testclient import TestClient


class FakeModels:
    def __init__(self, tool_name, final_text):
        self.tool_name = tool_name
        self.final_text = final_text
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            function_call = SimpleNamespace(name=self.tool_name, args={"lookback_days": 30})
            content = types.Content(
                role="model",
                parts=[types.Part.from_function_call(name=self.tool_name, args={"lookback_days": 30})],
            )
            return SimpleNamespace(
                function_calls=[function_call],
                candidates=[SimpleNamespace(content=content)],
                text=None,
            )
        return SimpleNamespace(function_calls=[], candidates=[], text=self.final_text)


class FakeClient:
    def __init__(self, tool_name, final_text):
        self.models = FakeModels(tool_name, final_text)


class BusinessToolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "tools.db"
        database.init_db()
        seed_demo()
        connection = database.get_connection()
        connection.execute(
            "INSERT INTO users (id, email, password_hash, salt) VALUES (100, 'tools@example.com', '!', 'salt')"
        )
        connection.execute(
            """INSERT INTO products
               (id, user_id, organization_id, sku, name, unit_cost_minor,
                selling_price_minor, currency, reorder_point, lead_time_days, source)
               VALUES (100, 100, 1, 'OWN-1', 'Own product', 250, 450, 'MYR', 2, 7, 'manual')"""
        )
        connection.execute(
            """INSERT INTO customers
               (id, user_id, organization_id, name, source)
               VALUES (100, 100, 1, 'Own customer', 'manual')"""
        )
        connection.execute(
            """INSERT INTO inventory_transactions
               (id, product_id, user_id, organization_id, transaction_type,
                quantity_change, unit_cost_minor, currency, source)
               VALUES (10000, 100, 100, 1, 'received', 20, 250, 'MYR', 'manual')"""
        )
        connection.execute(
            """INSERT INTO finance_transactions
               (id, user_id, organization_id, transaction_type, amount_minor,
                currency, category, source, transaction_date)
               VALUES (10000, 100, 1, 'expense', 500, 'MYR', 'Operations', 'manual', date('now'))"""
        )
        connection.commit()
        connection.close()
        self.context = ToolContext(organization_id=1, user_id=100)

    def tearDown(self):
        database.DB_PATH = self.original_database
        self.temp_dir.cleanup()

    def test_registry_declares_typed_schemas_in_one_place(self):
        self.assertEqual(
            set(TOOL_REGISTRY),
            {
                "get_inventory_snapshot",
                "get_sales_snapshot",
                "get_finance_snapshot",
                "record_sale",
                "format_opportunities",
            },
        )
        for definition in TOOL_REGISTRY.values():
            declaration = definition.declaration()
            self.assertTrue(declaration.name)
            self.assertTrue(declaration.description)
            self.assertIn("properties", declaration.parameters_json_schema)
            self.assertIsNotNone(definition.output_model)

    def test_read_tools_happy_path_include_source_rows_and_never_leak_demo_rows(self):
        inventory_result = invoke_tool("get_inventory_snapshot", self.context, {"lookback_days": 30})
        self.assertTrue(inventory_result.ok, inventory_result)
        self.assertEqual([item["id"] for item in inventory_result.data["items"]], [100])
        self.assertEqual(
            inventory_result.data["items"][0]["source_row_ids"]["inventory_transactions"],
            [10000],
        )

        sales_result = invoke_tool("get_sales_snapshot", self.context, {"lookback_days": 30})
        self.assertTrue(sales_result.ok, sales_result)
        self.assertEqual(sales_result.data["dashboard"]["orders"], 0)
        self.assertEqual(sales_result.data["dashboard"]["source_row_ids"]["revenue_minor"], [])
        self.assertEqual([item["customer_id"] for item in sales_result.data["customer_performance"]], [100])

        finance_result = invoke_tool("get_finance_snapshot", self.context, {"lookback_days": 30})
        self.assertTrue(finance_result.ok, finance_result)
        self.assertEqual(finance_result.data["dashboard"]["expenses_minor"], 500)
        self.assertEqual(
            finance_result.data["dashboard"]["source_row_ids"]["expenses_minor"],
            [10000],
        )

    def test_each_read_tool_rejects_invalid_arguments_before_database_access(self):
        for name in ("get_inventory_snapshot", "get_sales_snapshot", "get_finance_snapshot"):
            with patch("backend.tools.get_connection") as get_connection:
                result = invoke_tool(name, self.context, {"lookback_days": 0})
            self.assertFalse(result.ok)
            self.assertEqual(result.error.code, "invalid_arguments")
            get_connection.assert_not_called()

    def test_each_read_tool_is_isolated_by_context_organization(self):
        wrong_context = ToolContext(organization_id=1, user_id=database.get_demo_user_id())
        inventory_result = invoke_tool("get_inventory_snapshot", wrong_context, {})
        sales_result = invoke_tool("get_sales_snapshot", wrong_context, {})
        finance_result = invoke_tool("get_finance_snapshot", wrong_context, {})
        self.assertEqual(inventory_result.data["items"], [])
        self.assertEqual(sales_result.data["recent_sales"], [])
        self.assertEqual(finance_result.data["dashboard"]["expenses_minor"], 0)

    def test_model_cannot_override_organization_for_any_tool(self):
        valid_arguments = {
            "get_inventory_snapshot": {},
            "get_sales_snapshot": {},
            "get_finance_snapshot": {},
            "record_sale": {"product_id": 100, "quantity": 1},
            "format_opportunities": {
                "candidates": [
                    {
                        "candidate_id": "candidate-a",
                        "opportunity": "Example opportunity",
                        "market": "Example market",
                        "difficulty": "Low",
                        "rationale": "Evidence supports demand.",
                    }
                ]
            },
        }
        for name, arguments in valid_arguments.items():
            attempted_override = {**arguments, "organization_id": 2}
            with patch("backend.tools.get_connection") as get_connection:
                result = invoke_tool(name, self.context, attempted_override)
            self.assertFalse(result.ok, name)
            self.assertEqual(result.error.code, "invalid_arguments", name)
            get_connection.assert_not_called()

    def test_format_opportunities_happy_path_is_deterministic_and_traced(self):
        result = invoke_tool(
            "format_opportunities",
            self.context,
            {
                "candidates": [
                    {
                        "candidate_id": "candidate-a",
                        "opportunity": "Reusable packaging service",
                        "market": "Food service",
                        "difficulty": "Low",
                        "rationale": "Local demand evidence was found.",
                        "evidence_source_ids": ["https://example.com/evidence"],
                    }
                ]
            },
        )
        self.assertTrue(result.ok, result)
        self.assertEqual(result.data["items"][0]["potential"], 75)
        self.assertEqual(
            result.data["items"][0]["source_row_ids"],
            {
                "opportunity_candidates": ["candidate-a"],
                "external_evidence": ["https://example.com/evidence"],
            },
        )

    def test_format_opportunities_rejects_invalid_arguments(self):
        result = invoke_tool("format_opportunities", self.context, {"candidates": []})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "invalid_arguments")

    def test_record_sale_happy_path_is_traced_and_idempotent(self):
        arguments = {
            "product_id": 100,
            "customer_id": 100,
            "quantity": 2,
            "currency": "MYR",
            "payment_status": "paid",
            "external_id": "sale-retry-key",
        }
        first = invoke_tool("record_sale", self.context, arguments)
        second = invoke_tool("record_sale", self.context, arguments)
        self.assertTrue(first.ok, first)
        self.assertTrue(second.ok, second)
        self.assertFalse(first.data["idempotent_replay"])
        self.assertTrue(second.data["idempotent_replay"])
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(first.data["total_amount_minor"], 900)
        self.assertTrue(first.data["source_row_ids"]["inventory_transactions"])
        self.assertTrue(first.data["source_row_ids"]["finance_transactions"])
        connection = database.get_connection()
        count = connection.execute(
            """SELECT COUNT(*) FROM sales_orders
                WHERE organization_id = 1 AND source = 'agent_tool' AND external_id = 'sale-retry-key'"""
        ).fetchone()[0]
        connection.close()
        self.assertEqual(count, 1)

    def test_record_sale_invalid_arguments_do_not_touch_database(self):
        with patch("backend.tools.get_connection") as get_connection:
            result = invoke_tool("record_sale", self.context, {"product_id": -1, "quantity": 1})
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "invalid_arguments")
        get_connection.assert_not_called()

    def test_record_sale_cannot_use_another_organizations_ids(self):
        connection = database.get_connection()
        demo_product_id = connection.execute(
            "SELECT id FROM products WHERE organization_id = 2 ORDER BY id LIMIT 1"
        ).fetchone()[0]
        connection.close()
        result = invoke_tool(
            "record_sale",
            self.context,
            {"product_id": demo_product_id, "quantity": 1, "currency": "MYR"},
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "product_not_found")

    def test_record_sale_rejects_demo_organization(self):
        connection = database.get_connection()
        demo_product_id = connection.execute(
            "SELECT id FROM products WHERE organization_id = 2 ORDER BY id LIMIT 1"
        ).fetchone()[0]
        before = connection.execute(
            "SELECT COUNT(*) FROM sales_orders WHERE organization_id = 2"
        ).fetchone()[0]
        connection.close()
        result = invoke_tool(
            "record_sale",
            ToolContext(organization_id=2, user_id=database.get_demo_user_id()),
            {"product_id": demo_product_id, "quantity": 1, "currency": "MYR"},
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "read_only_organization")
        connection = database.get_connection()
        after = connection.execute(
            "SELECT COUNT(*) FROM sales_orders WHERE organization_id = 2"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(before, after)

    def test_record_sale_rolls_back_when_mid_operation_fails(self):
        connection = database.get_connection()
        connection.execute(
            """CREATE TRIGGER fail_sale_movement
               BEFORE INSERT ON inventory_transactions
               WHEN NEW.source = 'sale'
               BEGIN
                 SELECT RAISE(ABORT, 'forced movement failure');
               END"""
        )
        connection.commit()
        before_stock = connection.execute(
            "SELECT SUM(quantity_change) FROM inventory_transactions WHERE product_id = 100 AND organization_id = 1"
        ).fetchone()[0]
        connection.close()
        result = invoke_tool(
            "record_sale",
            self.context,
            {
                "product_id": 100,
                "quantity": 1,
                "currency": "MYR",
                "external_id": "rollback-sale",
            },
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "tool_execution_failed")
        connection = database.get_connection()
        sale_count = connection.execute(
            "SELECT COUNT(*) FROM sales_orders WHERE organization_id = 1 AND external_id = 'rollback-sale'"
        ).fetchone()[0]
        after_stock = connection.execute(
            "SELECT SUM(quantity_change) FROM inventory_transactions WHERE product_id = 100 AND organization_id = 1"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(sale_count, 0)
        self.assertEqual(before_stock, after_stock)


class AgentToolIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "agent-tools.db"
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.assertEqual(self.client.post("/demo/start").status_code, 200)

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        database.DB_PATH = self.original_database
        self.temp_dir.cleanup()

    def test_operational_agents_use_provider_function_calling_end_to_end(self):
        cases = (
            (inventory, "/inventory/ask", "get_inventory_snapshot", "Inventory answer"),
            (sales, "/sales/ask", "get_sales_snapshot", "Sales answer"),
            (finance_operations, "/finance/ask", "get_finance_snapshot", "Finance answer"),
        )
        for module, endpoint, tool_name, answer in cases:
            fake_client = FakeClient(tool_name, answer)
            with patch.object(module, "client", fake_client):
                response = self.client.post(endpoint, json={"question": "What needs attention?"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["answer"], answer)
            self.assertEqual(len(fake_client.models.calls), 2)
            second_contents = fake_client.models.calls[1]["contents"]
            self.assertEqual(second_contents[-1].role, "tool")

    def test_opportunity_agent_uses_structured_function_call_instead_of_text_parsing(self):
        class OpportunityModels:
            def __init__(self):
                self.calls = []

            def generate_content(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return SimpleNamespace(text="Researched candidate with supporting evidence.", candidates=[])
                function_call = SimpleNamespace(
                    name="format_opportunities",
                    args={
                        "candidates": [
                            {
                                "candidate_id": "candidate-a",
                                "opportunity": "Reusable packaging service",
                                "market": "Food service",
                                "difficulty": "Low",
                                "rationale": "Demand evidence was found.",
                                "evidence_source_ids": ["source-a"],
                            }
                        ]
                    },
                )
                return SimpleNamespace(function_calls=[function_call])

        fake_client = SimpleNamespace(models=OpportunityModels())
        with patch.object(opportunity_finder, "client", fake_client):
            items = opportunity_finder.run("Find packaging opportunities", 1, 1)
        self.assertEqual(items[0]["potential"], 75)
        self.assertEqual(len(fake_client.models.calls), 2)

    def test_prompt_definitions_contain_no_sql_arithmetic_or_numeric_thresholds(self):
        from backend.agents import competitor, financial, followup, market_research, opportunity_finder, synthesis

        modules = (
            inventory,
            sales,
            finance_operations,
            competitor,
            financial,
            market_research,
            opportunity_finder,
            synthesis,
            followup,
        )
        forbidden_words = ("select ", "insert ", "update ", "delete ", "sum(", "avg(")
        for module in modules:
            prompts = [
                value
                for name, value in inspect.getmembers(module)
                if isinstance(value, str) and ("PROMPT" in name or "ADDON" in name)
            ]
            for prompt in prompts:
                lowered = prompt.lower()
                self.assertFalse(any(word in lowered for word in forbidden_words), module.__name__)
                self.assertFalse(any(character.isdigit() for character in prompt), module.__name__)


if __name__ == "__main__":
    unittest.main()
