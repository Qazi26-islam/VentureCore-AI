from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Generic, Optional, TypeVar

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.db import DEMO_ORGANIZATION_ID, get_connection
from backend.money import multiply_minor


logger = logging.getLogger("business_tools")


class ToolContext(BaseModel):
    organization_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    source: str = Field(default="agent_tool", pattern="^(agent_tool|manual)$")


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ToolResult(BaseModel):
    ok: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[ToolError] = None


class SnapshotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_days: int = Field(default=30, ge=1, le=365)
    as_of: Optional[date] = None


class FigureWorkings(BaseModel):
    tool: str
    inputs: dict[str, Any]
    source_row_ids: dict[str, list[int]]


class RecordSaleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0)
    customer_id: Optional[int] = Field(default=None, gt=0)
    quantity: float = Field(gt=0)
    unit_price_minor: Optional[int] = Field(default=None, ge=0)
    currency: str = Field(default="MYR", pattern="^[A-Z]{3}$")
    payment_status: str = Field(default="paid", pattern="^(paid|due)$")
    due_date: Optional[str] = Field(default=None, max_length=10)
    reference_note: str = Field(default="", max_length=300)
    external_id: Optional[str] = Field(default=None, min_length=1, max_length=200)


class InventoryItemOutput(BaseModel):
    id: int
    sku: str
    name: str
    category: str
    current_stock: float
    unit_cost_minor: int
    selling_price_minor: int
    inventory_value_minor: int
    currency: str
    reorder_point: float
    lead_time_days: int
    supplier_name: Optional[str]
    units_sold: float
    average_daily_sales: float
    days_of_stock: Optional[float]
    recommended_reorder_quantity: int
    estimated_reorder_cost_minor: int
    status: str
    source_row_ids: dict[str, list[int]]


class InventorySnapshotOutput(BaseModel):
    company_profile: Optional[dict[str, Any]]
    lookback_days: int
    dashboard: dict[str, Any]
    items: list[InventoryItemOutput]


class SalesSnapshotOutput(BaseModel):
    company_profile: Optional[dict[str, Any]]
    lookback_days: int
    dashboard: dict[str, Any]
    customer_performance: list[dict[str, Any]]
    product_performance: list[dict[str, Any]]
    outstanding_invoices: list[dict[str, Any]]
    recent_sales: list[dict[str, Any]]


class FinanceSnapshotOutput(BaseModel):
    company_profile: Optional[dict[str, Any]]
    lookback_days: int
    dashboard: dict[str, Any]
    monthly_cash_flow: list[dict[str, Any]]
    expense_categories: list[dict[str, Any]]
    outstanding_receivables: list[dict[str, Any]]
    inventory_commitments: dict[str, Any]
    recent_transactions: list[dict[str, Any]]


class DailyBriefingMetricsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    velocity_days: int = Field(ge=1, le=365)
    stockout_days: int = Field(ge=1, le=365)
    expense_period_days: int = Field(ge=1, le=365)
    baseline_periods: int = Field(ge=1, le=12)
    receivable_min_minor: int = Field(ge=0)
    expense_increase_percent: int = Field(ge=0)
    expense_increase_min_minor: int = Field(ge=0)
    cash_drop_percent: int = Field(ge=0)
    cash_drop_min_minor: int = Field(ge=0)


class DailyBriefingMetricsOutput(BaseModel):
    as_of: date
    currency: str
    cash: dict[str, Any]
    overdue_receivables: list[dict[str, Any]]
    stockout_products: list[dict[str, Any]]
    expense_anomalies: list[dict[str, Any]]


class RecordSaleOutput(BaseModel):
    id: int
    product_name: str
    customer_name: Optional[str]
    quantity: float
    unit_price_minor: int
    total_amount_minor: int
    currency: str
    payment_status: str
    due_date: Optional[str]
    created_at: str
    idempotent_replay: bool
    source_row_ids: dict[str, list[int]]


class OpportunityCandidate(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=80)
    opportunity: str = Field(min_length=2, max_length=160)
    market: str = Field(min_length=2, max_length=120)
    difficulty: str = Field(pattern="^(Low|Medium|High)$")
    rationale: str = Field(min_length=2, max_length=400)
    evidence_source_ids: list[str] = Field(default_factory=list)


class FormatOpportunitiesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[OpportunityCandidate] = Field(min_length=1, max_length=8)


class OpportunityItemOutput(BaseModel):
    opportunity: str
    market: str
    difficulty: str
    potential: int
    rationale: str
    source_row_ids: dict[str, list[str]]


class OpportunityListOutput(BaseModel):
    items: list[OpportunityItemOutput]


InputModel = TypeVar("InputModel", bound=BaseModel)
OutputModel = TypeVar("OutputModel", bound=BaseModel)


@dataclass(frozen=True)
class ToolDefinition(Generic[InputModel, OutputModel]):
    name: str
    description: str
    input_model: type[InputModel]
    output_model: type[OutputModel]
    handler: Callable[[ToolContext, InputModel], OutputModel]
    writes: bool = False

    def declaration(self) -> types.FunctionDeclaration:
        response_schema = {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "data": self.output_model.model_json_schema(),
                "error": ToolError.model_json_schema(),
            },
            "required": ["ok"],
        }
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema=self.input_model.model_json_schema(),
            response_json_schema=response_schema,
        )


def _profile(connection: sqlite3.Connection, context: ToolContext) -> Optional[dict[str, Any]]:
    row = connection.execute(
        """SELECT id, company_name, industry, country, currency, products_services,
                  target_customers, main_competitors, monthly_budget_minor,
                  business_goals, business_stage
             FROM company_profiles
            WHERE user_id = ? AND organization_id = ?""",
        (context.user_id, context.organization_id),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["source_row_ids"] = {"company_profiles": [int(row["id"])]}
    return result


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _snapshot_as_of(body: SnapshotInput) -> datetime:
    if body.as_of is not None:
        return datetime.combine(body.as_of, time.max)
    return datetime.now()


def _workings(
    tool: str,
    body: SnapshotInput,
    source_row_ids: dict[str, list[int]],
) -> dict[str, Any]:
    inputs: dict[str, Any] = {"lookback_days": body.lookback_days}
    if body.as_of is not None:
        inputs["as_of"] = body.as_of.isoformat()
    return FigureWorkings(
        tool=tool,
        inputs=inputs,
        source_row_ids=source_row_ids,
    ).model_dump(mode="json")


def _inventory_snapshot(context: ToolContext, body: SnapshotInput) -> InventorySnapshotOutput:
    connection = get_connection()
    cutoff = _snapshot_as_of(body) - timedelta(days=body.lookback_days)
    try:
        products = connection.execute(
            """SELECT p.id, p.sku, p.name, p.category, p.unit_cost_minor,
                      p.selling_price_minor, p.currency, p.reorder_point,
                      p.lead_time_days, p.supplier_id, s.name AS supplier_name
                 FROM products p
                 LEFT JOIN suppliers s
                   ON s.id = p.supplier_id AND s.organization_id = p.organization_id
                WHERE p.user_id = ? AND p.organization_id = ? AND p.active = 1
                ORDER BY p.name COLLATE NOCASE""",
            (context.user_id, context.organization_id),
        ).fetchall()
        transactions = connection.execute(
            """SELECT id, product_id, transaction_type, quantity_change, created_at
                 FROM inventory_transactions
                WHERE user_id = ? AND organization_id = ?
                ORDER BY created_at, id""",
            (context.user_id, context.organization_id),
        ).fetchall()
        by_product: dict[int, list[sqlite3.Row]] = {}
        for transaction in transactions:
            by_product.setdefault(int(transaction["product_id"]), []).append(transaction)

        items = []
        for product in products:
            product_id = int(product["id"])
            product_transactions = by_product.get(product_id, [])
            stock = sum(float(row["quantity_change"]) for row in product_transactions)
            sold_rows = [
                row
                for row in product_transactions
                if row["transaction_type"] == "sold" and _parse_datetime(row["created_at"]) >= cutoff
            ]
            units_sold = sum(abs(float(row["quantity_change"])) for row in sold_rows)
            average_daily_sales = units_sold / body.lookback_days
            days_of_stock = round(stock / average_daily_sales, 1) if average_daily_sales > 0 else None
            reorder_point = float(product["reorder_point"] or 0)
            lead_time_days = int(product["lead_time_days"] or 0)
            lead_time_demand = average_daily_sales * lead_time_days
            minimum_target = math.ceil(reorder_point * 2)
            target_stock = max(minimum_target, math.ceil(lead_time_demand + reorder_point))
            recommended_quantity = max(0, math.ceil(target_stock - stock))
            if stock <= 0:
                status = "Out of stock"
            elif stock <= reorder_point or (days_of_stock is not None and days_of_stock <= lead_time_days):
                status = "Reorder now"
            elif days_of_stock is not None and days_of_stock <= lead_time_days + 7:
                status = "Watch"
            else:
                status = "Healthy"
            source_ids = {
                "products": [product_id],
                "inventory_transactions": [int(row["id"]) for row in product_transactions],
            }
            if product["supplier_id"] is not None:
                source_ids["suppliers"] = [int(product["supplier_id"])]
            items.append(
                InventoryItemOutput(
                    id=product_id,
                    sku=product["sku"] or "",
                    name=product["name"],
                    category=product["category"] or "",
                    current_stock=stock,
                    unit_cost_minor=int(product["unit_cost_minor"]),
                    selling_price_minor=int(product["selling_price_minor"]),
                    inventory_value_minor=multiply_minor(int(product["unit_cost_minor"]), stock),
                    currency=product["currency"],
                    reorder_point=reorder_point,
                    lead_time_days=lead_time_days,
                    supplier_name=product["supplier_name"],
                    units_sold=units_sold,
                    average_daily_sales=round(average_daily_sales, 2),
                    days_of_stock=days_of_stock,
                    recommended_reorder_quantity=recommended_quantity,
                    estimated_reorder_cost_minor=int(product["unit_cost_minor"]) * recommended_quantity,
                    status=status,
                    source_row_ids=source_ids,
                )
            )
        product_ids = [item.id for item in items]
        transaction_ids = sorted(
            {
                row_id
                for item in items
                for row_id in item.source_row_ids.get("inventory_transactions", [])
            }
        )
        all_sources = {"products": product_ids, "inventory_transactions": transaction_ids}
        attention_items = [item for item in items if item.status != "Healthy"]
        attention_sources = {
            "products": [item.id for item in attention_items],
            "inventory_transactions": sorted(
                {
                    row_id
                    for item in attention_items
                    for row_id in item.source_row_ids.get("inventory_transactions", [])
                }
            ),
        }
        dashboard = {
            "products_count": len(items),
            "inventory_value_minor": sum(item.inventory_value_minor for item in items),
            "needs_attention": len(attention_items),
            "estimated_reorder_cost_minor": sum(item.estimated_reorder_cost_minor for item in items),
            "currency": next((item.currency for item in items), "MYR"),
            "workings": {
                "products_count": _workings("get_inventory_snapshot", body, {"products": product_ids}),
                "inventory_value_minor": _workings("get_inventory_snapshot", body, all_sources),
                "needs_attention": _workings("get_inventory_snapshot", body, attention_sources),
                "estimated_reorder_cost_minor": _workings(
                    "get_inventory_snapshot", body, attention_sources
                ),
            },
        }
        return InventorySnapshotOutput(
            company_profile=_profile(connection, context),
            lookback_days=body.lookback_days,
            dashboard=dashboard,
            items=items,
        )
    finally:
        connection.close()


def _sales_snapshot(context: ToolContext, body: SnapshotInput) -> SalesSnapshotOutput:
    connection = get_connection()
    as_of = _snapshot_as_of(body)
    cutoff = as_of - timedelta(days=body.lookback_days)
    try:
        orders = connection.execute(
            """SELECT s.id, s.customer_id, s.product_id, s.quantity,
                      s.unit_price_minor, s.total_amount_minor, s.currency,
                      s.payment_status, s.due_date, s.created_at,
                      c.name AS customer_name, c.segment AS customer_segment,
                      p.name AS product_name, p.unit_cost_minor
                 FROM sales_orders s
                 JOIN products p ON p.id = s.product_id AND p.organization_id = s.organization_id
                 LEFT JOIN customers c ON c.id = s.customer_id AND c.organization_id = s.organization_id
                WHERE s.user_id = ? AND s.organization_id = ?
                ORDER BY s.created_at DESC, s.id DESC""",
            (context.user_id, context.organization_id),
        ).fetchall()
        customers = connection.execute(
            """SELECT id, name, segment FROM customers
                WHERE user_id = ? AND organization_id = ? ORDER BY name COLLATE NOCASE""",
            (context.user_id, context.organization_id),
        ).fetchall()
        recent = [row for row in orders if _parse_datetime(row["created_at"]) >= cutoff]
        currency = next((row["currency"] for row in orders), "MYR")
        recent_ids = [int(row["id"]) for row in recent]
        unpaid = [row for row in orders if row["payment_status"] != "paid"]
        as_of_date = as_of.date().isoformat()
        overdue = [
            row for row in unpaid if row["due_date"] is not None and row["due_date"] < as_of_date
        ]
        recent_by_customer: dict[int, list[sqlite3.Row]] = {}
        for order in recent:
            if order["customer_id"] is not None:
                recent_by_customer.setdefault(int(order["customer_id"]), []).append(order)
        top_customer_orders = max(
            recent_by_customer.values(),
            key=lambda rows: sum(int(row["total_amount_minor"]) for row in rows),
            default=[],
        )
        dashboard = {
            "revenue_minor": sum(int(row["total_amount_minor"]) for row in recent),
            "cash_collected_minor": sum(
                int(row["total_amount_minor"]) for row in recent if row["payment_status"] == "paid"
            ),
            "outstanding_amount_minor": sum(int(row["total_amount_minor"]) for row in unpaid),
            "overdue_receivables_minor": sum(int(row["total_amount_minor"]) for row in overdue),
            "currency": currency,
            "orders": len(recent),
            "top_customer": top_customer_orders[0]["customer_name"] if top_customer_orders else None,
            "source_row_ids": {
                "revenue_minor": recent_ids,
                "cash_collected_minor": [
                    int(row["id"]) for row in recent if row["payment_status"] == "paid"
                ],
                "outstanding_amount_minor": [int(row["id"]) for row in unpaid],
                "overdue_receivables_minor": [int(row["id"]) for row in overdue],
            },
        }
        dashboard["workings"] = {
            key: _workings(
                "get_sales_snapshot",
                body,
                {"sales_orders": dashboard["source_row_ids"].get(key, [])},
            )
            for key in (
                "revenue_minor",
                "cash_collected_minor",
                "outstanding_amount_minor",
                "overdue_receivables_minor",
            )
        }
        dashboard["workings"]["orders"] = _workings(
            "get_sales_snapshot", body, {"sales_orders": recent_ids}
        )
        customer_performance = []
        for customer in customers:
            customer_orders = [row for row in orders if row["customer_id"] == customer["id"]]
            customer_performance.append(
                {
                    "customer_id": int(customer["id"]),
                    "name": customer["name"],
                    "segment": customer["segment"] or "",
                    "order_count": len(customer_orders),
                    "total_revenue_minor": sum(int(row["total_amount_minor"]) for row in customer_orders),
                    "outstanding_amount_minor": sum(
                        int(row["total_amount_minor"])
                        for row in customer_orders
                        if row["payment_status"] != "paid"
                    ),
                    "last_purchase": customer_orders[0]["created_at"] if customer_orders else None,
                    "source_row_ids": {
                        "customers": [int(customer["id"])],
                        "sales_orders": [int(row["id"]) for row in customer_orders],
                    },
                }
            )
        product_groups: dict[int, list[sqlite3.Row]] = {}
        for order in recent:
            product_groups.setdefault(int(order["product_id"]), []).append(order)
        product_performance = [
            {
                "product_id": product_id,
                "name": product_orders[0]["product_name"],
                "units_sold": sum(float(row["quantity"]) for row in product_orders),
                "revenue_minor": sum(int(row["total_amount_minor"]) for row in product_orders),
                "gross_margin_minor": sum(
                    int(row["total_amount_minor"])
                    - multiply_minor(int(row["unit_cost_minor"]), float(row["quantity"]))
                    for row in product_orders
                ),
                "currency": product_orders[0]["currency"],
                "source_row_ids": {
                    "products": [product_id],
                    "sales_orders": [int(row["id"]) for row in product_orders],
                },
            }
            for product_id, product_orders in product_groups.items()
        ]
        outstanding = [
            {
                "id": int(row["id"]),
                "customer_name": row["customer_name"],
                "product_name": row["product_name"],
                "total_amount_minor": int(row["total_amount_minor"]),
                "currency": row["currency"],
                "due_date": row["due_date"],
                "status": "overdue" if row["due_date"] and row["due_date"] < as_of_date else "due",
                "source_row_ids": {"sales_orders": [int(row["id"])]},
            }
            for row in unpaid
        ]
        recent_sales = [
            {
                **dict(row),
                "source_row_ids": {
                    "sales_orders": [int(row["id"])],
                    "products": [int(row["product_id"])],
                    "customers": [int(row["customer_id"])] if row["customer_id"] else [],
                },
            }
            for row in orders[:20]
        ]
        return SalesSnapshotOutput(
            company_profile=_profile(connection, context),
            lookback_days=body.lookback_days,
            dashboard=dashboard,
            customer_performance=customer_performance,
            product_performance=product_performance,
            outstanding_invoices=outstanding,
            recent_sales=recent_sales,
        )
    finally:
        connection.close()


def _finance_snapshot(context: ToolContext, body: SnapshotInput) -> FinanceSnapshotOutput:
    connection = get_connection()
    as_of = _snapshot_as_of(body)
    cutoff = (as_of - timedelta(days=body.lookback_days)).date().isoformat()
    try:
        transactions = connection.execute(
            """SELECT id, transaction_type, amount_minor, currency, category,
                      description, source, transaction_date
                 FROM finance_transactions
                WHERE user_id = ? AND organization_id = ?
                ORDER BY transaction_date DESC, id DESC""",
            (context.user_id, context.organization_id),
        ).fetchall()
        receivables = connection.execute(
            """SELECT s.id, s.customer_id, s.total_amount_minor, s.currency, s.due_date,
                      c.name AS customer_name
                 FROM sales_orders s
                 LEFT JOIN customers c ON c.id = s.customer_id AND c.organization_id = s.organization_id
                WHERE s.user_id = ? AND s.organization_id = ? AND s.payment_status != 'paid'
                ORDER BY COALESCE(s.due_date, '9999-12-31'), s.id""",
            (context.user_id, context.organization_id),
        ).fetchall()
        recent = [row for row in transactions if row["transaction_date"] >= cutoff]
        income_recent = [row for row in recent if row["transaction_type"] == "income"]
        expenses_recent = [row for row in recent if row["transaction_type"] == "expense"]
        income_all = [row for row in transactions if row["transaction_type"] == "income"]
        expense_all = [row for row in transactions if row["transaction_type"] == "expense"]
        currency = next((row["currency"] for row in transactions), "MYR")
        dashboard = {
            "income_minor": sum(int(row["amount_minor"]) for row in income_recent),
            "expenses_minor": sum(int(row["amount_minor"]) for row in expenses_recent),
            "net_cash_flow_minor": sum(int(row["amount_minor"]) for row in income_recent)
            - sum(int(row["amount_minor"]) for row in expenses_recent),
            "recorded_cash_balance_minor": sum(int(row["amount_minor"]) for row in income_all)
            - sum(int(row["amount_minor"]) for row in expense_all),
            "receivables_minor": sum(int(row["total_amount_minor"]) for row in receivables),
            "currency": currency,
            "source_row_ids": {
                "income_minor": [int(row["id"]) for row in income_recent],
                "expenses_minor": [int(row["id"]) for row in expenses_recent],
                "net_cash_flow_minor": [int(row["id"]) for row in recent],
                "recorded_cash_balance_minor": [int(row["id"]) for row in transactions],
                "receivables_minor": [int(row["id"]) for row in receivables],
            },
        }
        dashboard["workings"] = {
            key: _workings(
                "get_finance_snapshot",
                body,
                {
                    ("sales_orders" if key == "receivables_minor" else "finance_transactions"):
                        dashboard["source_row_ids"].get(key, [])
                },
            )
            for key in (
                "income_minor",
                "expenses_minor",
                "net_cash_flow_minor",
                "recorded_cash_balance_minor",
                "receivables_minor",
            )
        }
        monthly: dict[str, list[sqlite3.Row]] = {}
        categories: dict[str, list[sqlite3.Row]] = {}
        for transaction in transactions:
            monthly.setdefault(transaction["transaction_date"][:7], []).append(transaction)
        for transaction in expenses_recent:
            categories.setdefault(transaction["category"] or "Other", []).append(transaction)
        monthly_cash_flow = [
            {
                "month": month,
                "income_minor": sum(
                    int(row["amount_minor"]) for row in rows if row["transaction_type"] == "income"
                ),
                "expenses_minor": sum(
                    int(row["amount_minor"]) for row in rows if row["transaction_type"] == "expense"
                ),
                "source_row_ids": {"finance_transactions": [int(row["id"]) for row in rows]},
            }
            for month, rows in sorted(monthly.items())
        ]
        expense_categories = [
            {
                "category": category,
                "amount_minor": sum(int(row["amount_minor"]) for row in rows),
                "source_row_ids": {"finance_transactions": [int(row["id"]) for row in rows]},
            }
            for category, rows in categories.items()
        ]
        inventory = _inventory_snapshot(context, body)
        commitment_ids = {
            "products": [item.id for item in inventory.items if item.recommended_reorder_quantity > 0],
            "inventory_transactions": sorted(
                {
                    row_id
                    for item in inventory.items
                    if item.recommended_reorder_quantity > 0
                    for row_id in item.source_row_ids.get("inventory_transactions", [])
                }
            ),
        }
        return FinanceSnapshotOutput(
            company_profile=_profile(connection, context),
            lookback_days=body.lookback_days,
            dashboard=dashboard,
            monthly_cash_flow=monthly_cash_flow,
            expense_categories=expense_categories,
            outstanding_receivables=[
                {
                    **dict(row),
                    "source_row_ids": {
                        "sales_orders": [int(row["id"])],
                        "customers": [int(row["customer_id"])] if row["customer_id"] else [],
                    },
                }
                for row in receivables
            ],
            inventory_commitments={
                "estimated_reorder_cost_minor": sum(
                    item.estimated_reorder_cost_minor for item in inventory.items
                ),
                "currency": currency,
                "source_row_ids": commitment_ids,
            },
            recent_transactions=[
                {
                    **dict(row),
                    "category": row["category"] or "Other",
                    "description": row["description"] or "",
                    "source_row_ids": {"finance_transactions": [int(row["id"])]},
                }
                for row in transactions[:30]
            ],
        )
    finally:
        connection.close()


def _rounded_percent(numerator: int, denominator: int) -> Optional[int]:
    if denominator == 0:
        return None
    value = Decimal(numerator) * Decimal(100) / Decimal(abs(denominator))
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _daily_briefing_metrics(
    context: ToolContext,
    body: DailyBriefingMetricsInput,
) -> DailyBriefingMetricsOutput:
    connection = get_connection()
    as_of = body.as_of
    current_start = as_of - timedelta(days=body.expense_period_days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=body.expense_period_days - 1)
    baseline_start = current_start - timedelta(
        days=body.expense_period_days * body.baseline_periods
    )
    try:
        transactions = connection.execute(
            """SELECT id, transaction_type, amount_minor, currency, category,
                      transaction_date
                 FROM finance_transactions
                WHERE user_id = ? AND organization_id = ? AND transaction_date <= ?
                ORDER BY transaction_date, id""",
            (context.user_id, context.organization_id, as_of.isoformat()),
        ).fetchall()
        currency = next((row["currency"] for row in transactions), "MYR")

        def net_between(start: date, end: date) -> tuple[int, list[int]]:
            rows = [
                row
                for row in transactions
                if start.isoformat() <= row["transaction_date"] <= end.isoformat()
            ]
            net = sum(
                int(row["amount_minor"])
                if row["transaction_type"] == "income"
                else -int(row["amount_minor"])
                for row in rows
            )
            return net, [int(row["id"]) for row in rows]

        current_net, current_ids = net_between(current_start, as_of)
        previous_net, previous_ids = net_between(previous_start, previous_end)
        change = current_net - previous_net
        cash_balance = sum(
            int(row["amount_minor"])
            if row["transaction_type"] == "income"
            else -int(row["amount_minor"])
            for row in transactions
        )
        change_percent = _rounded_percent(change, previous_net)
        cash = {
            "recorded_cash_balance_minor": cash_balance,
            "current_net_cash_flow_minor": current_net,
            "previous_net_cash_flow_minor": previous_net,
            "change_minor": change,
            "change_percent": change_percent,
            "material_drop": (
                change < 0
                and abs(change) >= body.cash_drop_min_minor
                and change_percent is not None
                and change_percent <= -body.cash_drop_percent
            ),
            "currency": currency,
            "source_row_ids": {
                "finance_transactions": [int(row["id"]) for row in transactions]
            },
            "period_source_row_ids": {
                "current": current_ids,
                "previous": previous_ids,
            },
        }

        receivable_rows = connection.execute(
            """SELECT s.id, s.customer_id, s.total_amount_minor, s.currency,
                      s.due_date, c.name AS customer_name
                 FROM sales_orders s
                 LEFT JOIN customers c
                   ON c.id = s.customer_id AND c.organization_id = s.organization_id
                WHERE s.user_id = ? AND s.organization_id = ?
                  AND s.payment_status != 'paid' AND s.due_date < ?
                ORDER BY s.due_date, s.id""",
            (context.user_id, context.organization_id, as_of.isoformat()),
        ).fetchall()
        overdue_receivables = [
            {
                "id": int(row["id"]),
                "customer_id": int(row["customer_id"]) if row["customer_id"] else None,
                "customer_name": row["customer_name"] or "Unknown customer",
                "amount_minor": int(row["total_amount_minor"]),
                "currency": row["currency"],
                "due_date": row["due_date"],
                "material": int(row["total_amount_minor"]) >= body.receivable_min_minor,
                "source_row_ids": {
                    "sales_orders": [int(row["id"])],
                    "customers": [int(row["customer_id"])] if row["customer_id"] else [],
                },
            }
            for row in receivable_rows
        ]

        inventory = _inventory_snapshot(
            context,
            SnapshotInput(lookback_days=body.velocity_days, as_of=as_of),
        )
        stockout_products = [
            {
                "product_id": item.id,
                "name": item.name,
                "current_stock": item.current_stock,
                "days_of_cover": item.days_of_stock,
                "currency": item.currency,
                "source_row_ids": item.source_row_ids,
            }
            for item in inventory.items
            if item.current_stock <= 0
            or (item.days_of_stock is not None and item.days_of_stock <= body.stockout_days)
        ]

        expense_rows = [
            row for row in transactions if row["transaction_type"] == "expense"
        ]
        categories = sorted({row["category"] or "Other" for row in expense_rows})
        anomalies = []
        for category in categories:
            current_rows = [
                row
                for row in expense_rows
                if (row["category"] or "Other") == category
                and current_start.isoformat() <= row["transaction_date"] <= as_of.isoformat()
            ]
            baseline_rows = [
                row
                for row in expense_rows
                if (row["category"] or "Other") == category
                and baseline_start.isoformat() <= row["transaction_date"] < current_start.isoformat()
            ]
            current_amount = sum(int(row["amount_minor"]) for row in current_rows)
            baseline_total = sum(int(row["amount_minor"]) for row in baseline_rows)
            baseline_average = int(
                (Decimal(baseline_total) / Decimal(body.baseline_periods)).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            increase = current_amount - baseline_average
            increase_percent = _rounded_percent(increase, baseline_average)
            if (
                increase > 0
                and increase >= body.expense_increase_min_minor
                and increase_percent is not None
                and increase_percent >= body.expense_increase_percent
            ):
                anomalies.append(
                    {
                        "category": category,
                        "current_amount_minor": current_amount,
                        "baseline_average_minor": baseline_average,
                        "increase_minor": increase,
                        "increase_percent": increase_percent,
                        "currency": currency,
                        "source_row_ids": {
                            "finance_transactions": [int(row["id"]) for row in current_rows]
                        },
                        "baseline_source_row_ids": {
                            "finance_transactions": [int(row["id"]) for row in baseline_rows]
                        },
                    }
                )

        return DailyBriefingMetricsOutput(
            as_of=as_of,
            currency=currency,
            cash=cash,
            overdue_receivables=overdue_receivables,
            stockout_products=stockout_products,
            expense_anomalies=anomalies,
        )
    finally:
        connection.close()


def _record_sale(context: ToolContext, body: RecordSaleInput) -> RecordSaleOutput:
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if body.external_id:
            existing = connection.execute(
                """SELECT s.id, s.quantity, s.unit_price_minor, s.total_amount_minor, s.currency,
                          s.payment_status, s.due_date, s.created_at, p.name AS product_name,
                          c.name AS customer_name
                     FROM sales_orders s
                     JOIN products p ON p.id = s.product_id AND p.organization_id = s.organization_id
                     LEFT JOIN customers c ON c.id = s.customer_id AND c.organization_id = s.organization_id
                    WHERE s.organization_id = ? AND s.source = ? AND s.external_id = ?""",
                (context.organization_id, context.source, body.external_id),
            ).fetchone()
            if existing:
                connection.rollback()
                return RecordSaleOutput(
                    **dict(existing),
                    idempotent_replay=True,
                    source_row_ids={"sales_orders": [int(existing["id"])]},
                )
        product = connection.execute(
            """SELECT id, name, selling_price_minor, currency FROM products
                WHERE id = ? AND user_id = ? AND organization_id = ? AND active = 1""",
            (body.product_id, context.user_id, context.organization_id),
        ).fetchone()
        if product is None:
            connection.rollback()
            raise ToolAccessError("product_not_found", "Product was not found in this organization.")
        customer_name = None
        if body.customer_id is not None:
            customer = connection.execute(
                """SELECT id, name FROM customers
                    WHERE id = ? AND user_id = ? AND organization_id = ?""",
                (body.customer_id, context.user_id, context.organization_id),
            ).fetchone()
            if customer is None:
                connection.rollback()
                raise ToolAccessError("customer_not_found", "Customer was not found in this organization.")
            customer_name = customer["name"]
        if body.due_date:
            try:
                datetime.strptime(body.due_date, "%Y-%m-%d")
            except ValueError as exc:
                connection.rollback()
                raise ToolAccessError("invalid_due_date", "Due date must use YYYY-MM-DD format.") from exc
        stock_rows = connection.execute(
            """SELECT id, quantity_change FROM inventory_transactions
                WHERE product_id = ? AND user_id = ? AND organization_id = ?""",
            (body.product_id, context.user_id, context.organization_id),
        ).fetchall()
        current_stock = sum(float(row["quantity_change"]) for row in stock_rows)
        if body.quantity > current_stock:
            connection.rollback()
            raise ToolAccessError("insufficient_stock", "The requested quantity exceeds recorded stock.")
        if body.currency != product["currency"]:
            connection.rollback()
            raise ToolAccessError("currency_mismatch", "Sale currency must match the product currency.")
        unit_price_minor = (
            body.unit_price_minor if body.unit_price_minor is not None else int(product["selling_price_minor"])
        )
        total_amount_minor = multiply_minor(unit_price_minor, body.quantity)
        sale_cursor = connection.execute(
            """INSERT INTO sales_orders
               (user_id, organization_id, customer_id, product_id, quantity, unit_price_minor,
                total_amount_minor, currency, payment_status, due_date, reference_note,
                source, external_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                context.user_id,
                context.organization_id,
                body.customer_id,
                body.product_id,
                body.quantity,
                unit_price_minor,
                total_amount_minor,
                product["currency"],
                body.payment_status,
                body.due_date,
                body.reference_note,
                context.source,
                body.external_id,
            ),
        )
        sale_id = int(sale_cursor.lastrowid)
        movement_cursor = connection.execute(
            """INSERT INTO inventory_transactions
               (product_id, user_id, organization_id, transaction_type, quantity_change,
                unit_cost_minor, currency, reference_note, source, external_id)
               VALUES (?, ?, ?, 'sold', ?, NULL, ?, ?, 'sale', ?)""",
            (
                body.product_id,
                context.user_id,
                context.organization_id,
                -body.quantity,
                product["currency"],
                f"Sale #{sale_id}: {body.reference_note}".strip(),
                f"tool-sale-{body.external_id}" if body.external_id else f"tool-sale-{sale_id}",
            ),
        )
        finance_id = None
        if body.payment_status == "paid":
            finance_cursor = connection.execute(
                """INSERT INTO finance_transactions
                   (user_id, organization_id, transaction_type, amount_minor, currency, category,
                    description, source, external_id, related_sale_id, transaction_date)
                   VALUES (?, ?, 'income', ?, ?, 'Sales Revenue', ?, 'sale', ?, ?, date('now'))""",
                (
                    context.user_id,
                    context.organization_id,
                    total_amount_minor,
                    product["currency"],
                    f"Payment received for sale #{sale_id}",
                    f"tool-sale-{body.external_id}" if body.external_id else f"tool-sale-{sale_id}",
                    sale_id,
                ),
            )
            finance_id = int(finance_cursor.lastrowid)
        created_at = connection.execute(
            """SELECT created_at FROM sales_orders
                WHERE id = ? AND organization_id = ?""",
            (sale_id, context.organization_id),
        ).fetchone()["created_at"]
        connection.commit()
        sources = {
            "sales_orders": [sale_id],
            "products": [int(product["id"])],
            "inventory_transactions": [int(movement_cursor.lastrowid)],
        }
        if body.customer_id is not None:
            sources["customers"] = [body.customer_id]
        if finance_id is not None:
            sources["finance_transactions"] = [finance_id]
        return RecordSaleOutput(
            id=sale_id,
            product_name=product["name"],
            customer_name=customer_name,
            quantity=body.quantity,
            unit_price_minor=unit_price_minor,
            total_amount_minor=total_amount_minor,
            currency=product["currency"],
            payment_status=body.payment_status,
            due_date=body.due_date,
            created_at=created_at,
            idempotent_replay=False,
            source_row_ids=sources,
        )
    except ToolAccessError:
        raise
    except Exception:
        connection.rollback()
        logger.exception("Transactional sale tool rolled back")
        raise
    finally:
        connection.close()


class ToolAccessError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _format_opportunities(
    _context: ToolContext,
    body: FormatOpportunitiesInput,
) -> OpportunityListOutput:
    potential_by_difficulty = {"Low": 75, "Medium": 50, "High": 25}
    return OpportunityListOutput(
        items=[
            OpportunityItemOutput(
                opportunity=candidate.opportunity,
                market=candidate.market,
                difficulty=candidate.difficulty,
                potential=potential_by_difficulty[candidate.difficulty],
                rationale=candidate.rationale,
                source_row_ids={
                    "opportunity_candidates": [candidate.candidate_id],
                    "external_evidence": candidate.evidence_source_ids,
                },
            )
            for candidate in body.candidates
        ]
    )


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    definition.name: definition
    for definition in (
        ToolDefinition(
            name="get_inventory_snapshot",
            description="Read the caller's inventory position, demand evidence, reorder recommendations, and source row identifiers.",
            input_model=SnapshotInput,
            output_model=InventorySnapshotOutput,
            handler=_inventory_snapshot,
        ),
        ToolDefinition(
            name="get_sales_snapshot",
            description="Read the caller's sales, customers, receivables, product performance, and source row identifiers.",
            input_model=SnapshotInput,
            output_model=SalesSnapshotOutput,
            handler=_sales_snapshot,
        ),
        ToolDefinition(
            name="get_finance_snapshot",
            description="Read the caller's recorded cash flow, expenses, receivables, inventory commitments, and source row identifiers.",
            input_model=SnapshotInput,
            output_model=FinanceSnapshotOutput,
            handler=_finance_snapshot,
        ),
        ToolDefinition(
            name="get_daily_briefing_metrics",
            description="Compute the caller's deterministic cash trend, overdue receivables, stockout risks, and expense anomalies with source row identifiers.",
            input_model=DailyBriefingMetricsInput,
            output_model=DailyBriefingMetricsOutput,
            handler=_daily_briefing_metrics,
        ),
        ToolDefinition(
            name="record_sale",
            description="Record a sale for the caller's organization as one transactional and optionally idempotent operation.",
            input_model=RecordSaleInput,
            output_model=RecordSaleOutput,
            handler=_record_sale,
            writes=True,
        ),
        ToolDefinition(
            name="format_opportunities",
            description="Validate researched opportunity candidates and apply the deterministic difficulty policy with evidence identifiers.",
            input_model=FormatOpportunitiesInput,
            output_model=OpportunityListOutput,
            handler=_format_opportunities,
        ),
    )
}


def invoke_tool(name: str, context: ToolContext | dict[str, Any], arguments: dict[str, Any]) -> ToolResult:
    definition = TOOL_REGISTRY.get(name)
    if definition is None:
        return ToolResult(
            ok=False,
            error=ToolError(code="unknown_tool", message="The requested tool is not registered."),
        )
    try:
        validated_context = ToolContext.model_validate(context)
        validated_arguments = definition.input_model.model_validate(arguments)
    except ValidationError as exc:
        return ToolResult(
            ok=False,
            error=ToolError(code="invalid_arguments", message=exc.errors()[0]["msg"]),
        )
    if definition.writes and validated_context.organization_id == DEMO_ORGANIZATION_ID:
        return ToolResult(
            ok=False,
            error=ToolError(
                code="read_only_organization",
                message="The public demo organization is read-only.",
            ),
        )
    try:
        output = definition.handler(validated_context, validated_arguments)
        validated_output = definition.output_model.model_validate(output)
        return ToolResult(ok=True, data=validated_output.model_dump(mode="json"))
    except ToolAccessError as exc:
        return ToolResult(ok=False, error=ToolError(code=exc.code, message=exc.message))
    except Exception:
        return ToolResult(
            ok=False,
            error=ToolError(
                code="tool_execution_failed",
                message="The operation could not be completed and no changes were saved.",
                retryable=True,
            ),
        )


def tool_declarations(names: list[str]) -> types.Tool:
    return types.Tool(function_declarations=[TOOL_REGISTRY[name].declaration() for name in names])
