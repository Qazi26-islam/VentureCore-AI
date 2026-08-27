import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")

import backend.db as database
from backend.main import app
from fastapi.testclient import TestClient


class BusinessWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "workflow.db"
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        response = self.client.post(
            "/auth/signup",
            json={"email": "owner@example.com", "password": "strong-password"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        database.DB_PATH = self.original_database
        self.temp_dir.cleanup()

    def test_inventory_sale_and_finance_keep_the_same_display_values(self):
        profile = self.client.put(
            "/company/profile",
            json={
                "company_name": "Example Shop",
                "currency": "MYR",
                "monthly_budget_minor": 2_000_000,
            },
        )
        self.assertEqual(profile.status_code, 200, profile.text)

        product = self.client.post(
            "/inventory/products",
            json={
                "name": "Bottled drink",
                "sku": "DRINK-1",
                "unit_cost_minor": 250,
                "selling_price_minor": 450,
                "currency": "MYR",
                "reorder_point": 2,
                "lead_time_days": 7,
            },
        )
        self.assertEqual(product.status_code, 200, product.text)
        product_id = product.json()["id"]

        movement = self.client.post(
            f"/inventory/products/{product_id}/movement",
            json={"transaction_type": "received", "quantity": 10, "reference_note": "Opening stock"},
        )
        self.assertEqual(movement.status_code, 200, movement.text)

        sale = self.client.post(
            "/sales/orders",
            json={
                "product_id": product_id,
                "quantity": 2,
                "unit_price_minor": None,
                "currency": "MYR",
                "payment_status": "paid",
            },
        )
        self.assertEqual(sale.status_code, 200, sale.text)
        self.assertEqual(sale.json()["total_amount_minor"], 900)

        inventory = self.client.get("/inventory/dashboard")
        self.assertEqual(inventory.status_code, 200, inventory.text)
        self.assertEqual(inventory.json()[0]["current_stock"], 8)
        self.assertEqual(inventory.json()[0]["inventory_value_minor"], 2_000)

        sales = self.client.get("/sales/dashboard")
        self.assertEqual(sales.status_code, 200, sales.text)
        self.assertEqual(sales.json()["revenue_30d_minor"], 900)
        self.assertEqual(sales.json()["cash_collected_30d_minor"], 900)

        finance = self.client.get("/finance/dashboard")
        self.assertEqual(finance.status_code, 200, finance.text)
        self.assertEqual(finance.json()["cash_balance_minor"], 900)
        self.assertEqual(finance.json()["currency"], "MYR")


if __name__ == "__main__":
    unittest.main()
