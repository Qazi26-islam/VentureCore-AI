from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from backend.db import DEMO_ORGANIZATION_ID, DEMO_USER_EMAIL, DB_PATH, get_connection
from backend.migrations import upgrade


SOURCE = "demo_seed"
CURRENCY = "USD"


def _month_start(base: date, offset: int) -> date:
    month_index = base.year * 12 + base.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def _timestamp(day: date, hour: int = 10) -> str:
    return datetime(day.year, day.month, day.day, hour, 0, 0).isoformat(sep=" ")


def _insert(conn: sqlite3.Connection, table: str, values: dict) -> int:
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    names = ", ".join(columns)
    cursor = conn.execute(
        f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )
    return int(cursor.lastrowid)


def _clear_demo_data(conn: sqlite3.Connection) -> None:
    for table in (
        "follow_up_messages",
        "research_jobs",
        "finance_transactions",
        "sales_orders",
        "inventory_transactions",
        "products",
        "suppliers",
        "customers",
        "data_uploads",
        "company_profiles",
    ):
        conn.execute(f"DELETE FROM {table} WHERE organization_id = ?", (DEMO_ORGANIZATION_ID,))
    conn.execute(
        "DELETE FROM briefing_cache WHERE organization_id = ?",
        (DEMO_ORGANIZATION_ID,),
    )


def seed_demo(conn: sqlite3.Connection | None = None, today: date | None = None) -> dict:
    owns_connection = conn is None
    connection = conn or get_connection()
    connection.row_factory = sqlite3.Row
    today = today or date.today()
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    current_month = today.replace(day=1)
    months = [_month_start(current_month, offset) for offset in range(-11, 1)]

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO organizations (id, name) VALUES (?, ?)
               ON CONFLICT(id) DO UPDATE SET name = excluded.name""",
            (DEMO_ORGANIZATION_ID, "Harbour & Pine Demo Company"),
        )
        connection.execute(
            """INSERT INTO users (email, password_hash, salt)
               VALUES (?, '!', 'demo-login-disabled')
               ON CONFLICT(email) DO UPDATE SET password_hash = '!', salt = 'demo-login-disabled'""",
            (DEMO_USER_EMAIL,),
        )
        demo_user_id = int(
            connection.execute("SELECT id FROM users WHERE email = ?", (DEMO_USER_EMAIL,)).fetchone()["id"]
        )
        _clear_demo_data(connection)

        common = {
            "user_id": demo_user_id,
            "organization_id": DEMO_ORGANIZATION_ID,
            "source": SOURCE,
            "last_synced_at": now,
        }
        _insert(
            connection,
            "company_profiles",
            {
                **common,
                "external_id": "demo-company",
                "company_name": "Harbour & Pine Coffee Supply",
                "industry": "Specialty coffee retail and wholesale",
                "country": "United States",
                "monthly_budget_minor": 300_000,
                "currency": CURRENCY,
                "products_services": "Coffee beans, café supplies, bottled cold brew, and drinkware",
                "target_customers": "Independent cafés, offices, and home coffee enthusiasts",
                "main_competitors": "Local specialty roasters and café-supply distributors",
                "business_goals": "Protect stock availability, collect receivables, and control marketing spend",
                "business_stage": "Existing business",
                "updated_at": now,
            },
        )
        _insert(
            connection,
            "data_uploads",
            {
                **common,
                "external_id": "demo-upload-12-months",
                "filename": "harbour_pine_12_months.csv",
                "data_type": "Sales data",
                "row_count": 60,
                "column_count": 8,
                "quality_summary": json.dumps(
                    {
                        "missing_values": 0,
                        "duplicate_rows": 0,
                        "invalid_numeric_values": 0,
                        "warnings": ["No common data-quality issues were found in this file."],
                    }
                ),
                "created_at": now,
            },
        )

        supplier_specs = (
            ("Peninsula Roasters", "orders@peninsularoasters.example", 10, "Net 30"),
            ("Harbour Packaging", "sales@harbourpack.example", 14, "Net 14"),
            ("Northern Café Supply", "hello@northerncafe.example", 7, "Payment on delivery"),
        )
        supplier_ids = []
        for index, (name, email, lead_time, terms) in enumerate(supplier_specs, start=1):
            supplier_ids.append(
                _insert(
                    connection,
                    "suppliers",
                    {
                        **common,
                        "external_id": f"demo-supplier-{index}",
                        "name": name,
                        "contact_name": "Demo Account Manager",
                        "email": email,
                        "phone": "+1 555 010 0100",
                        "lead_time_days": lead_time,
                        "payment_terms": terms,
                        "created_at": _timestamp(months[0]),
                    },
                )
            )

        customer_specs = (
            ("Northbank Café", "Wholesale", "accounts@northbank.example"),
            ("Studio Eleven", "Office", "office@studioeleven.example"),
            ("Riverside Coffee Bar", "Wholesale", "hello@riversidecoffee.example"),
            ("Walk-in Retail", "Retail", "retail@example.invalid"),
            ("Lighthouse Coworking", "Office", "finance@lighthouse.example"),
            ("Juniper Bakery", "Wholesale", "orders@juniper.example"),
        )
        customer_ids = []
        for index, (name, segment, email) in enumerate(customer_specs, start=1):
            customer_ids.append(
                _insert(
                    connection,
                    "customers",
                    {
                        **common,
                        "external_id": f"demo-customer-{index}",
                        "name": name,
                        "email": email,
                        "phone": "+1 555 010 0200",
                        "segment": segment,
                        "notes": "Fictional customer used in the public demo.",
                        "created_at": _timestamp(months[0]),
                    },
                )
            )

        product_specs = (
            ("House Blend Beans 1kg", "BEAN-HOUSE-1K", 4_500, 7_000, 30, 10, 24, 0),
            ("Oat Milk 1L", "OAT-1L", 850, 1_400, 25, 7, 38, 2),
            ("Ceramic Pour-over Mug", "MUG-POUR", 1_200, 2_800, 12, 14, 9, 1),
            ("Cold Brew Bottle", "COLD-330", 600, 1_500, 40, 14, 30, 1),
            ("Filter Papers 100-pack", "FILTER-100", 500, 900, 20, 7, 20, 2),
        )
        seasonality = (80, 84, 88, 92, 96, 100, 94, 102, 108, 116, 132, 150)
        completed_month_indexes = [
            index for index, month in enumerate(months) if month + timedelta(days=14) <= today
        ]
        cold_brew_shortage_months = set(completed_month_indexes[-2:])
        product_ids = []
        total_sales_minor = 0
        paid_sales_minor = 0
        sold_quantities: dict[int, int] = {}
        received_quantities: dict[int, int] = {}

        for product_index, spec in enumerate(product_specs, start=1):
            name, sku, cost_minor, price_minor, reorder_point, lead_time, base_sales, supplier_index = spec
            product_id = _insert(
                connection,
                "products",
                {
                    **common,
                    "external_id": f"demo-product-{product_index}",
                    "supplier_id": supplier_ids[supplier_index],
                    "sku": sku,
                    "name": name,
                    "category": "Beverages" if product_index in {1, 2, 4} else "Accessories",
                    "unit_cost_minor": cost_minor,
                    "selling_price_minor": price_minor,
                    "currency": CURRENCY,
                    "reorder_point": reorder_point,
                    "lead_time_days": lead_time,
                    "active": 1,
                    "created_at": _timestamp(months[0]),
                },
            )
            product_ids.append(product_id)
            opening_stock = 60
            sold_quantities[product_id] = 0
            received_quantities[product_id] = opening_stock
            _insert(
                connection,
                "inventory_transactions",
                {
                    **common,
                    "external_id": f"demo-opening-{product_index}",
                    "product_id": product_id,
                    "transaction_type": "received",
                    "quantity_change": opening_stock,
                    "unit_cost_minor": cost_minor,
                    "currency": CURRENCY,
                    "reference_note": "Opening stock for demo period",
                    "created_at": _timestamp(months[0], 8),
                },
            )

            for month_index, month in enumerate(months):
                sale_date = month + timedelta(days=14)
                if sale_date > today:
                    continue
                quantity = max(1, (base_sales * seasonality[month_index] + 50) // 100)
                receipt_quantity = quantity
                if product_index == 4 and month_index in cold_brew_shortage_months:
                    receipt_quantity = max(0, quantity - 24)
                received_quantities[product_id] += receipt_quantity
                sold_quantities[product_id] += quantity
                if receipt_quantity:
                    _insert(
                        connection,
                        "inventory_transactions",
                        {
                            **common,
                            "external_id": f"demo-receipt-{product_index}-{month:%Y-%m}",
                            "product_id": product_id,
                            "transaction_type": "received",
                            "quantity_change": receipt_quantity,
                            "unit_cost_minor": cost_minor,
                            "currency": CURRENCY,
                            "reference_note": f"Scheduled supplier receipt {month:%B %Y}",
                            "created_at": _timestamp(month + timedelta(days=4), 8),
                        },
                    )

                is_overdue = product_index == 1 and month_index == 10
                payment_status = "due" if is_overdue else "paid"
                due_date = month + timedelta(days=20) if is_overdue else None
                total_amount_minor = quantity * price_minor
                total_sales_minor += total_amount_minor
                sale_id = _insert(
                    connection,
                    "sales_orders",
                    {
                        **common,
                        "external_id": f"demo-sale-{product_index}-{month:%Y-%m}",
                        "customer_id": customer_ids[(product_index + month_index) % len(customer_ids)],
                        "product_id": product_id,
                        "quantity": quantity,
                        "unit_price_minor": price_minor,
                        "total_amount_minor": total_amount_minor,
                        "currency": CURRENCY,
                        "payment_status": payment_status,
                        "due_date": due_date.isoformat() if due_date else None,
                        "reference_note": "Fictional monthly demo sale",
                        "created_at": _timestamp(sale_date, 12),
                    },
                )
                _insert(
                    connection,
                    "inventory_transactions",
                    {
                        **common,
                        "external_id": f"demo-sold-{product_index}-{month:%Y-%m}",
                        "product_id": product_id,
                        "transaction_type": "sold",
                        "quantity_change": -quantity,
                        "unit_cost_minor": None,
                        "currency": CURRENCY,
                        "reference_note": f"Sale #{sale_id}",
                        "created_at": _timestamp(sale_date, 12),
                    },
                )
                if payment_status == "paid":
                    paid_sales_minor += total_amount_minor
                    _insert(
                        connection,
                        "finance_transactions",
                        {
                            **common,
                            "external_id": f"demo-payment-{sale_id}",
                            "transaction_type": "income",
                            "amount_minor": total_amount_minor,
                            "currency": CURRENCY,
                            "category": "Sales Revenue",
                            "description": f"Payment received for sale #{sale_id}",
                            "related_sale_id": sale_id,
                            "transaction_date": sale_date.isoformat(),
                            "created_at": _timestamp(sale_date, 13),
                        },
                    )

        expense_totals_minor = 0
        marketing_values = (15_000, 15_000, 16_000, 16_000, 17_000, 18_000, 17_000, 18_000, 19_000, 20_000, 35_000, 60_000)
        for month_index, month in enumerate(months):
            expenses = {
                "Rent": 60_000,
                "Payroll": 120_000,
                "Utilities": 15_000 + (month_index % 3) * 2_000,
                "Logistics": 24_000 + (month_index % 4) * 2_000,
                "Marketing": marketing_values[month_index],
            }
            for category, amount_minor in expenses.items():
                transaction_day = month + timedelta(days=24)
                if transaction_day > today:
                    continue
                expense_totals_minor += amount_minor
                _insert(
                    connection,
                    "finance_transactions",
                    {
                        **common,
                        "external_id": f"demo-expense-{category.lower()}-{month:%Y-%m}",
                        "transaction_type": "expense",
                        "amount_minor": amount_minor,
                        "currency": CURRENCY,
                        "category": category,
                        "description": f"Fictional {category.lower()} expense for {month:%B %Y}",
                        "related_sale_id": None,
                        "transaction_date": transaction_day.isoformat(),
                        "created_at": _timestamp(transaction_day, 9),
                    },
                )

        report = (
            "**Executive Summary:**\n\n"
            "Harbour & Pine has seasonal revenue growth and positive recorded operating cash flow. "
            "Cold Brew Bottle stock has fallen below its reorder point, one wholesale invoice is overdue, "
            "and marketing expense increased sharply in the latest two months.\n\n---\n\n"
            "**Recommended Actions:**\n\n"
            "- Confirm the next Cold Brew Bottle supplier delivery.\n"
            "- Follow up on the overdue Northbank Café receivable.\n"
            "- Review the return from the recent marketing increase before extending it."
        )
        _insert(
            connection,
            "research_jobs",
            {
                **common,
                "external_id": "demo-management-report",
                "id": "demo-management-report",
                "question": "Prepare this month's management report for Harbour & Pine.",
                "title": "Demo management report",
                "report": report,
                "sections": json.dumps({"executive_summary": report}),
                "favorite": 1,
                "created_at": now,
            },
        )

        connection.commit()
        return {
            "organization_id": DEMO_ORGANIZATION_ID,
            "user_id": demo_user_id,
            "months": len(months),
            "products": len(product_ids),
            "sales_total_minor": total_sales_minor,
            "paid_sales_minor": paid_sales_minor,
            "expenses_total_minor": expense_totals_minor,
            "stock_balances": {
                product_id: received_quantities[product_id] - sold_quantities[product_id]
                for product_id in product_ids
            },
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed VentureCore's public demo organization")
    parser.add_argument("--database", default=str(DB_PATH))
    args = parser.parse_args()
    conn = sqlite3.connect(Path(args.database))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        upgrade(conn)
        seed_demo(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
