import logging
import json
import io
import threading
import re
import csv
import math
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import Response
import markdown as markdown_lib
from xhtml2pdf import pisa
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

from backend.models.schemas import (
    ResearchRequest, StartResponse, StatusResponse,
    HistoryItem, JobDetailResponse, MessageItem,
    FollowUpRequest, FollowUpResponse,
    RenameRequest, FavoriteRequest,
    CompanyProfileRequest, CompanyProfileResponse,
    DataQualityResponse,
    SupplierRequest, SupplierItem, ProductRequest, StockMovementRequest, InventoryItem,
    InventoryQuestionRequest, InventoryQuestionResponse,
    CustomerRequest, CustomerItem, SaleRequest, SaleRecord, SalesDashboardResponse,
    SalesQuestionRequest, SalesQuestionResponse,
)
from backend.agents.coordinator import run_research
from backend.agents import followup, inventory as inventory_agent, sales as sales_agent
from backend import jobs
from backend.db import get_connection

router = APIRouter()
logger = logging.getLogger("research_api")


def get_user_id(request: Request):
    return request.session.get("user_id")


def require_login(request: Request) -> int:
    user_id = get_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="You must be logged in.")
    return user_id


def _profile_to_context(profile) -> str:
    if profile is None:
        return ""
    fields = [
        ("Company", profile["company_name"]),
        ("Industry", profile["industry"]),
        ("Country / market", profile["country"]),
        ("Currency", profile["currency"]),
        ("Products / services", profile["products_services"]),
        ("Target customers", profile["target_customers"]),
        ("Main competitors", profile["main_competitors"]),
        ("Monthly budget", profile["monthly_budget"]),
        ("Business goals", profile["business_goals"]),
        ("Business stage", profile["business_stage"]),
    ]
    lines = [f"{label}: {value}" for label, value in fields if value]
    return "Saved company profile:\n" + "\n".join(lines)


@router.get("/company/profile", response_model=Optional[CompanyProfileResponse])
def get_company_profile(request: Request):
    user_id = require_login(request)
    conn = get_connection()
    row = conn.execute("SELECT * FROM company_profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@router.put("/company/profile", response_model=CompanyProfileResponse)
def save_company_profile(body: CompanyProfileRequest, request: Request):
    user_id = require_login(request)
    values = body.model_dump()
    conn = get_connection()
    conn.execute(
        """INSERT INTO company_profiles
           (user_id, company_name, industry, country, currency, products_services, target_customers, main_competitors, monthly_budget, business_goals, business_stage)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             company_name=excluded.company_name, industry=excluded.industry, country=excluded.country,
             currency=excluded.currency, products_services=excluded.products_services,
             target_customers=excluded.target_customers, main_competitors=excluded.main_competitors,
             monthly_budget=excluded.monthly_budget, business_goals=excluded.business_goals,
             business_stage=excluded.business_stage, updated_at=CURRENT_TIMESTAMP""",
        (user_id, *values.values()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM company_profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row)


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _looks_numeric_column(name: str) -> bool:
    return any(word in name.lower() for word in ("sales", "revenue", "cost", "price", "stock", "quantity", "qty", "expense", "amount", "margin"))


@router.post("/company/data/quality", response_model=DataQualityResponse)
async def check_data_quality(request: Request, file: UploadFile = File(...), data_type: str = Form("Business data")):
    user_id = require_login(request)
    filename = file.filename or "upload"
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in {"csv", "xlsx"}:
        raise HTTPException(status_code=400, detail="Upload a CSV or XLSX file.")
    raw = await file.read()
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File must be between 1 byte and 10 MB.")
    try:
        if extension == "csv":
            decoded = raw.decode("utf-8-sig")
            rows = list(csv.reader(decoded.splitlines()))
        else:
            workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            sheet = workbook.active
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            workbook.close()
    except Exception:
        raise HTTPException(status_code=400, detail="This file could not be read. Check that it is a valid CSV or XLSX file.")
    if len(rows) < 2:
        raise HTTPException(status_code=400, detail="Your file needs a header row and at least one data row.")
    columns = [str(cell).strip() if cell is not None else f"Column {index + 1}" for index, cell in enumerate(rows[0])]
    data_rows = rows[1:]
    missing_values = sum(1 for row in data_rows for cell in row if _is_blank(cell))
    normalized_rows = [tuple("" if _is_blank(cell) else str(cell).strip() for cell in row) for row in data_rows]
    duplicate_rows = len(normalized_rows) - len(set(normalized_rows))
    invalid_numeric_values = 0
    for row in data_rows:
        for index, cell in enumerate(row[:len(columns)]):
            if _looks_numeric_column(columns[index]) and not _is_blank(cell):
                try:
                    float(str(cell).replace(",", "").replace("RM", "").replace("BDT", "").replace("$", "").strip())
                except ValueError:
                    invalid_numeric_values += 1
    warnings = []
    if missing_values: warnings.append(f"{missing_values} missing value(s) found.")
    if duplicate_rows: warnings.append(f"{duplicate_rows} duplicate row(s) found.")
    if invalid_numeric_values: warnings.append(f"{invalid_numeric_values} value(s) in numeric-looking columns could not be read as numbers.")
    if not warnings: warnings.append("No common data-quality issues were found in this file.")
    result = DataQualityResponse(filename=filename, data_type=data_type, row_count=len(data_rows), column_count=len(columns), columns=columns, missing_values=missing_values, duplicate_rows=duplicate_rows, invalid_numeric_values=invalid_numeric_values, warnings=warnings)
    conn = get_connection()
    conn.execute("INSERT INTO data_uploads (user_id, filename, data_type, row_count, column_count, quality_summary) VALUES (?, ?, ?, ?, ?, ?)", (user_id, filename, data_type, result.row_count, result.column_count, json.dumps(result.model_dump())))
    conn.commit()
    conn.close()
    return result


@router.post("/inventory/suppliers")
def create_supplier(body: SupplierRequest, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO suppliers (user_id, name, contact_name, email, phone, lead_time_days, payment_terms) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, body.name, body.contact_name, body.email, body.phone, body.lead_time_days, body.payment_terms),
    )
    conn.commit()
    supplier_id = cursor.lastrowid
    conn.close()
    return {"id": supplier_id, "status": "created"}


@router.get("/inventory/suppliers", response_model=list[SupplierItem])
def list_suppliers(request: Request):
    user_id = require_login(request)
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, name, contact_name, email, phone, lead_time_days, payment_terms
           FROM suppliers WHERE user_id = ? ORDER BY name COLLATE NOCASE""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [SupplierItem(**dict(row)) for row in rows]


@router.post("/inventory/products")
def create_product(body: ProductRequest, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    if body.supplier_id is not None:
        supplier = conn.execute("SELECT id FROM suppliers WHERE id = ? AND user_id = ?", (body.supplier_id, user_id)).fetchone()
        if supplier is None:
            conn.close()
            raise HTTPException(status_code=404, detail="Supplier not found")
    try:
        cursor = conn.execute(
            """INSERT INTO products (user_id, supplier_id, sku, name, category, unit_cost, selling_price, reorder_point, lead_time_days)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, body.supplier_id, body.sku, body.name, body.category, body.unit_cost, body.selling_price, body.reorder_point, body.lead_time_days),
        )
        conn.commit()
        product_id = cursor.lastrowid
    except Exception as exc:
        conn.close()
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=400, detail="This SKU already exists in your company workspace.")
        raise
    conn.close()
    return {"id": product_id, "status": "created"}


@router.post("/inventory/products/{product_id}/movement")
def record_stock_movement(product_id: int, body: StockMovementRequest, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    product = conn.execute("SELECT id FROM products WHERE id = ? AND user_id = ?", (product_id, user_id)).fetchone()
    if product is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Product not found")
    direction = 1 if body.transaction_type in {"received", "adjustment"} else -1
    quantity_change = body.quantity * direction
    current_stock = conn.execute("SELECT COALESCE(SUM(quantity_change), 0) FROM inventory_transactions WHERE product_id = ?", (product_id,)).fetchone()[0]
    if current_stock + quantity_change < 0:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Not enough stock. Current stock is {current_stock}.")
    cursor = conn.execute(
        "INSERT INTO inventory_transactions (product_id, user_id, transaction_type, quantity_change, unit_cost, reference_note) VALUES (?, ?, ?, ?, ?, ?)",
        (product_id, user_id, body.transaction_type, quantity_change, body.unit_cost, body.reference_note),
    )
    conn.commit()
    conn.close()
    return {"id": cursor.lastrowid, "current_stock": current_stock + quantity_change, "status": "recorded"}


@router.get("/inventory/dashboard", response_model=list[InventoryItem])
def inventory_dashboard(request: Request):
    user_id = require_login(request)
    conn = get_connection()
    rows = conn.execute(
        """SELECT p.*, s.name AS supplier_name,
                  COALESCE(SUM(t.quantity_change), 0) AS current_stock,
                  COALESCE(SUM(CASE
                      WHEN t.transaction_type = 'sold'
                       AND t.created_at >= datetime('now', '-30 days')
                      THEN ABS(t.quantity_change) ELSE 0 END), 0) AS units_sold_30d
           FROM products p
           LEFT JOIN suppliers s ON s.id = p.supplier_id
           LEFT JOIN inventory_transactions t ON t.product_id = p.id
           WHERE p.user_id = ? AND p.active = 1
           GROUP BY p.id
           ORDER BY current_stock <= p.reorder_point DESC, p.name ASC""",
        (user_id,),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        stock = float(row["current_stock"])
        units_sold_30d = float(row["units_sold_30d"])
        average_daily_sales = units_sold_30d / 30
        days_of_stock = round(stock / average_daily_sales, 1) if average_daily_sales > 0 else None
        lead_time_days = int(row["lead_time_days"] or 0)
        reorder_point = float(row["reorder_point"] or 0)

        demand_target = math.ceil(average_daily_sales * (lead_time_days + 14))
        minimum_target = math.ceil(reorder_point * 2)
        target_stock = max(demand_target, minimum_target)
        recommended_reorder_quantity = max(0, math.ceil(target_stock - stock))

        if stock <= 0:
            status = "Out of stock"
        elif stock <= reorder_point or (days_of_stock is not None and days_of_stock <= lead_time_days):
            status = "Reorder now"
        elif days_of_stock is not None and days_of_stock <= lead_time_days + 7:
            status = "Order soon"
        else:
            status = "Healthy"
        result.append(InventoryItem(
            id=row["id"], sku=row["sku"] or "", name=row["name"], category=row["category"] or "",
            current_stock=stock, unit_cost=row["unit_cost"], selling_price=row["selling_price"],
            inventory_value=round(stock * row["unit_cost"], 2), reorder_point=reorder_point,
            lead_time_days=lead_time_days, supplier_name=row["supplier_name"],
            units_sold_30d=round(units_sold_30d, 2), average_daily_sales=round(average_daily_sales, 2),
            days_of_stock=days_of_stock, recommended_reorder_quantity=recommended_reorder_quantity,
            estimated_reorder_cost=round(recommended_reorder_quantity * float(row["unit_cost"]), 2),
            status=status,
        ))
    return result


@router.post("/inventory/ask", response_model=InventoryQuestionResponse)
def ask_inventory_agent(body: InventoryQuestionRequest, request: Request):
    user_id = require_login(request)
    items = inventory_dashboard(request)
    conn = get_connection()
    profile = conn.execute(
        """SELECT company_name, industry, country, currency, products_services,
                  target_customers, business_goals, business_stage
           FROM company_profiles WHERE user_id = ?""",
        (user_id,),
    ).fetchone()
    conn.close()
    company_profile = dict(profile) if profile else {"currency": "MYR"}
    try:
        answer = inventory_agent.run(
            question=body.question,
            company_profile=company_profile,
            inventory_items=[item.model_dump() for item in items],
        )
    except Exception as exc:
        logger.exception("Inventory Agent failed: %s", exc)
        raise HTTPException(status_code=502, detail="The Inventory Agent is temporarily unavailable. Please try again.")
    return InventoryQuestionResponse(answer=answer)


@router.post("/sales/customers")
def create_customer(body: CustomerRequest, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO customers (user_id, name, email, phone, segment, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, body.name, body.email, body.phone, body.segment, body.notes),
    )
    conn.commit()
    customer_id = cursor.lastrowid
    conn.close()
    return {"id": customer_id, "status": "created"}


@router.get("/sales/customers", response_model=list[CustomerItem])
def list_customers(request: Request):
    user_id = require_login(request)
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, name, email, phone, segment, notes
           FROM customers WHERE user_id = ? ORDER BY name COLLATE NOCASE""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [CustomerItem(**dict(row)) for row in rows]


@router.post("/sales/orders", response_model=SaleRecord)
def record_sale(body: SaleRequest, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    product = conn.execute(
        "SELECT id, name, selling_price FROM products WHERE id = ? AND user_id = ? AND active = 1",
        (body.product_id, user_id),
    ).fetchone()
    if product is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Product not found.")
    customer_name = None
    if body.customer_id is not None:
        customer = conn.execute(
            "SELECT id, name FROM customers WHERE id = ? AND user_id = ?",
            (body.customer_id, user_id),
        ).fetchone()
        if customer is None:
            conn.close()
            raise HTTPException(status_code=404, detail="Customer not found.")
        customer_name = customer["name"]
    if body.due_date:
        try:
            datetime.strptime(body.due_date, "%Y-%m-%d")
        except ValueError:
            conn.close()
            raise HTTPException(status_code=400, detail="Due date must use YYYY-MM-DD format.")
    current_stock = float(conn.execute(
        "SELECT COALESCE(SUM(quantity_change), 0) FROM inventory_transactions WHERE product_id = ?",
        (body.product_id,),
    ).fetchone()[0])
    if body.quantity > current_stock:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Not enough stock. Current stock is {current_stock}.")
    unit_price = float(body.unit_price if body.unit_price is not None else product["selling_price"])
    total_amount = round(body.quantity * unit_price, 2)
    cursor = conn.execute(
        """INSERT INTO sales_orders
           (user_id, customer_id, product_id, quantity, unit_price, total_amount, payment_status, due_date, reference_note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, body.customer_id, body.product_id, body.quantity, unit_price, total_amount,
         body.payment_status, body.due_date or None, body.reference_note),
    )
    sale_id = cursor.lastrowid
    conn.execute(
        """INSERT INTO inventory_transactions
           (product_id, user_id, transaction_type, quantity_change, unit_cost, reference_note)
           VALUES (?, ?, 'sold', ?, NULL, ?)""",
        (body.product_id, user_id, -body.quantity, f"Sale #{sale_id}: {body.reference_note}".strip()),
    )
    conn.commit()
    row = conn.execute("SELECT created_at FROM sales_orders WHERE id = ?", (sale_id,)).fetchone()
    conn.close()
    effective_status = "overdue" if body.payment_status == "due" and body.due_date and body.due_date < datetime.now().strftime("%Y-%m-%d") else body.payment_status
    return SaleRecord(
        id=sale_id, customer_name=customer_name, product_name=product["name"], quantity=body.quantity,
        unit_price=unit_price, total_amount=total_amount, payment_status=effective_status,
        due_date=body.due_date, created_at=row["created_at"],
    )


@router.get("/sales/dashboard", response_model=SalesDashboardResponse)
def sales_dashboard(request: Request):
    user_id = require_login(request)
    conn = get_connection()
    totals = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN created_at >= datetime('now', '-30 days') THEN total_amount ELSE 0 END), 0) AS revenue_30d,
             COALESCE(SUM(CASE WHEN created_at >= datetime('now', '-30 days') AND payment_status = 'paid' THEN total_amount ELSE 0 END), 0) AS cash_collected_30d,
             COALESCE(SUM(CASE WHEN payment_status != 'paid' THEN total_amount ELSE 0 END), 0) AS outstanding_amount,
             SUM(CASE WHEN created_at >= datetime('now', '-30 days') THEN 1 ELSE 0 END) AS orders_30d
           FROM sales_orders WHERE user_id = ?""",
        (user_id,),
    ).fetchone()
    top_customer_row = conn.execute(
        """SELECT c.name, SUM(s.total_amount) AS total
           FROM sales_orders s JOIN customers c ON c.id = s.customer_id
           WHERE s.user_id = ? AND s.created_at >= datetime('now', '-30 days')
           GROUP BY c.id ORDER BY total DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    rows = conn.execute(
        """SELECT s.id, c.name AS customer_name, p.name AS product_name, s.quantity,
                  s.unit_price, s.total_amount,
                  CASE WHEN s.payment_status != 'paid' AND s.due_date IS NOT NULL
                         AND date(s.due_date) < date('now') THEN 'overdue'
                       ELSE s.payment_status END AS payment_status,
                  s.due_date, s.created_at
           FROM sales_orders s
           JOIN products p ON p.id = s.product_id
           LEFT JOIN customers c ON c.id = s.customer_id
           WHERE s.user_id = ? ORDER BY s.created_at DESC, s.id DESC LIMIT 20""",
        (user_id,),
    ).fetchall()
    conn.close()
    return SalesDashboardResponse(
        revenue_30d=round(float(totals["revenue_30d"]), 2),
        cash_collected_30d=round(float(totals["cash_collected_30d"]), 2),
        outstanding_amount=round(float(totals["outstanding_amount"]), 2),
        orders_30d=int(totals["orders_30d"] or 0),
        top_customer=top_customer_row["name"] if top_customer_row else None,
        recent_sales=[SaleRecord(**dict(row)) for row in rows],
    )


@router.post("/sales/ask", response_model=SalesQuestionResponse)
def ask_sales_agent(body: SalesQuestionRequest, request: Request):
    user_id = require_login(request)
    dashboard = sales_dashboard(request)
    conn = get_connection()
    profile = conn.execute(
        """SELECT company_name, industry, country, currency, products_services,
                  target_customers, business_goals, business_stage
           FROM company_profiles WHERE user_id = ?""",
        (user_id,),
    ).fetchone()
    customer_performance = conn.execute(
        """SELECT c.id, c.name, c.segment, COUNT(s.id) AS order_count,
                  COALESCE(SUM(s.total_amount), 0) AS total_revenue,
                  COALESCE(SUM(CASE WHEN s.payment_status != 'paid' THEN s.total_amount ELSE 0 END), 0) AS outstanding_amount,
                  MAX(s.created_at) AS last_purchase
           FROM customers c LEFT JOIN sales_orders s ON s.customer_id = c.id
           WHERE c.user_id = ? GROUP BY c.id
           ORDER BY total_revenue DESC, c.name COLLATE NOCASE LIMIT 25""",
        (user_id,),
    ).fetchall()
    product_performance = conn.execute(
        """SELECT p.id, p.name, SUM(s.quantity) AS units_sold,
                  SUM(s.total_amount) AS revenue
           FROM sales_orders s JOIN products p ON p.id = s.product_id
           WHERE s.user_id = ? AND s.created_at >= datetime('now', '-30 days')
           GROUP BY p.id ORDER BY revenue DESC LIMIT 25""",
        (user_id,),
    ).fetchall()
    outstanding_invoices = conn.execute(
        """SELECT s.id, c.name AS customer_name, p.name AS product_name,
                  s.total_amount, s.due_date,
                  CASE WHEN s.due_date IS NOT NULL AND date(s.due_date) < date('now')
                       THEN 'overdue' ELSE 'due' END AS status
           FROM sales_orders s
           JOIN products p ON p.id = s.product_id
           LEFT JOIN customers c ON c.id = s.customer_id
           WHERE s.user_id = ? AND s.payment_status != 'paid'
           ORDER BY COALESCE(s.due_date, '9999-12-31'), s.created_at LIMIT 50""",
        (user_id,),
    ).fetchall()
    conn.close()
    sales_context = {
        "company_profile": dict(profile) if profile else {"currency": "MYR"},
        "dashboard": dashboard.model_dump(),
        "customer_performance": [dict(row) for row in customer_performance],
        "product_performance_last_30_days": [dict(row) for row in product_performance],
        "outstanding_invoices": [dict(row) for row in outstanding_invoices],
    }
    try:
        answer = sales_agent.run(body.question, sales_context)
    except Exception as exc:
        logger.exception("Sales Agent failed: %s", exc)
        raise HTTPException(status_code=502, detail="The Sales Agent is temporarily unavailable. Please try again.")
    return SalesQuestionResponse(answer=answer)


@router.post("/research/start", response_model=StartResponse)
def start_research(request: Request, body: ResearchRequest) -> StartResponse:
    user_id = get_user_id(request)
    research_question = body.question
    if body.use_company_profile:
        if user_id is None:
            raise HTTPException(status_code=401, detail="Log in to use a saved company profile.")
        conn = get_connection()
        profile = conn.execute("SELECT * FROM company_profiles WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        if profile is None:
            raise HTTPException(status_code=400, detail="Create your Company Profile before using it in research.")
        research_question = _profile_to_context(profile) + "\n\nResearch request:\n" + body.question
    job_id = jobs.create_job(research_question)

    if user_id is not None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO research_jobs (id, user_id, question, report, sections) VALUES (?, ?, ?, ?, ?)",
            (job_id, user_id, research_question, None, "{}"),
        )
        conn.commit()
        conn.close()

    def _background_run():
        try:
            run_research(research_question, job_id, mode=body.mode, depth=body.depth)
            if user_id is not None:
                job = jobs.get_job(job_id)
                conn2 = get_connection()
                cursor2 = conn2.cursor()
                cursor2.execute(
                    "UPDATE research_jobs SET report = ?, sections = ? WHERE id = ?",
                    (job["report"], json.dumps(job["sections"]), job_id),
                )
                conn2.commit()
                conn2.close()
        except Exception as e:
            logger.exception("Research pipeline crashed")
            jobs.fail_job(job_id, str(e))

    thread = threading.Thread(target=_background_run, daemon=True)
    thread.start()

    return StartResponse(job_id=job_id)


@router.get("/research/status/{job_id}", response_model=StatusResponse)
def get_status(job_id: str) -> StatusResponse:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return StatusResponse(
        status=job["status"],
        stage=job["stage"],
        sections=job["sections"],
        report=job["report"],
        error=job["error"],
    )


@router.get("/research/history", response_model=list[HistoryItem])
def get_history(request: Request, q: str = "", favorites_only: bool = False):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT id, question, title, favorite, created_at FROM research_jobs WHERE user_id = ?"
    params = [user_id]

    if q:
        query += " AND (question LIKE ? OR title LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like])

    if favorites_only:
        query += " AND favorite = 1"

    query += " ORDER BY favorite DESC, created_at DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [
        HistoryItem(
            id=r["id"],
            question=r["question"],
            title=r["title"],
            favorite=bool(r["favorite"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.get("/research/job/{job_id}", response_model=JobDetailResponse)
def get_job_detail(job_id: str, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found")

    cursor.execute(
        "SELECT role, content FROM follow_up_messages WHERE job_id = ? ORDER BY created_at ASC",
        (job_id,),
    )
    messages = [MessageItem(role=m["role"], content=m["content"]) for m in cursor.fetchall()]
    conn.close()

    sections = json.loads(row["sections"]) if row["sections"] else {}

    return JobDetailResponse(
        id=row["id"],
        question=row["question"],
        title=row["title"],
        favorite=bool(row["favorite"]),
        report=row["report"],
        sections=sections,
        messages=messages,
    )


@router.put("/research/job/{job_id}/rename")
def rename_job(job_id: str, body: RenameRequest, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found")
    cursor.execute("UPDATE research_jobs SET title = ? WHERE id = ?", (body.title, job_id))
    conn.commit()
    conn.close()
    return {"status": "renamed"}


@router.put("/research/job/{job_id}/favorite")
def favorite_job(job_id: str, body: FavoriteRequest, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found")
    cursor.execute("UPDATE research_jobs SET favorite = ? WHERE id = ?", (1 if body.favorite else 0, job_id))
    conn.commit()
    conn.close()
    return {"status": "updated"}


@router.delete("/research/job/{job_id}")
def delete_job(job_id: str, request: Request):
    user_id = require_login(request)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found")
    cursor.execute("DELETE FROM follow_up_messages WHERE job_id = ?", (job_id,))
    cursor.execute("DELETE FROM research_jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}


from backend.agents import opportunity_finder
from backend.models.schemas import OpportunityRequest, OpportunityResponse, OpportunityItem


@router.post("/research/opportunities", response_model=OpportunityResponse)
def find_opportunities(body: OpportunityRequest):
    items = opportunity_finder.run(body.query)
    return OpportunityResponse(items=[OpportunityItem(**item) for item in items])


def _get_report_and_question(job_id: str, user_id):
    if user_id is not None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None, None
        return row["question"], row["report"]
    else:
        job = jobs.get_job(job_id)
        if job is None:
            return None, None
        return job["question"], job["report"]


def _get_research_data(job_id: str, user_id):
    if user_id is not None:
        conn = get_connection()
        row = conn.execute(
            "SELECT question, report, sections FROM research_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id),
        ).fetchone()
        conn.close()
        if row is None:
            return None, None, {}
        return row["question"], row["report"], json.loads(row["sections"] or "{}")
    job = jobs.get_job(job_id)
    if job is None:
        return None, None, {}
    return job["question"], job["report"], job.get("sections", {})


def _clean_export_text(text: str) -> str:
    text = re.sub(
        r"^\s*(MARKET|COMPETITOR|FINANCIAL)_CHART_DATA:[^\n]*(?:\n(?!\s*(?:\*\*Sources|Sources:|---|\*\*[A-Za-z]))[^\n]*)*",
        "",
        text or "",
        flags=re.MULTILINE | re.IGNORECASE,
    )
    text = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text.strip()


def _safe_report_name(question: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "" for c in question)[:50].strip() or "report"


@router.get("/research/job/{job_id}/pdf")
def export_pdf(job_id: str, request: Request):
    user_id = get_user_id(request)
    question, report = _get_report_and_question(job_id, user_id)

    if question is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not report:
        raise HTTPException(status_code=400, detail="This report isn't ready yet.")

    body_html = markdown_lib.markdown(report, extensions=["tables"])
    prepared_date = datetime.now().strftime("%d %B %Y")

    html_content = f"""
    <html>
    <head>
    <style>
        body {{ font-family: Helvetica, Arial, sans-serif; font-size: 11px; color: #1a1a1a; }}
        h1 {{ color: #5b7cfa; font-size: 20px; margin-bottom: 2px; }}
        h2 {{ color: #5b7cfa; font-size: 15px; margin-top: 18px; margin-bottom: 6px; }}
        h3 {{ color: #5b7cfa; font-size: 13px; margin-top: 14px; }}
        p {{ line-height: 1.5; margin: 6px 0; }}
        table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
        th, td {{ border: 1px solid #cccccc; padding: 6px 8px; text-align: left; font-size: 10px; }}
        th {{ background: #f0f0f0; font-weight: bold; }}
        hr {{ border: none; border-top: 1px solid #cccccc; margin: 16px 0; }}
        .meta {{ color: #666666; font-size: 10px; margin-bottom: 20px; }}
        a {{ color: #5b7cfa; }}
    </style>
    </head>
    <body>
        <h1>VentureCore AI</h1>
        <div class="meta">
            Business Intelligence Report<br/>
            {question}<br/>
            Prepared: {prepared_date}
        </div>
        {body_html}
    </body>
    </html>
    """

    buffer = io.BytesIO()
    pisa.CreatePDF(html_content, dest=buffer)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in question)[:50].strip() or "report"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
    )


@router.get("/research/job/{job_id}/docx")
def export_docx(job_id: str, request: Request):
    question, report, _ = _get_research_data(job_id, get_user_id(request))
    if question is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not report:
        raise HTTPException(status_code=400, detail="This report isn't ready yet.")

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    title = document.add_heading("VENTURECORE AI", 0)
    title.runs[0].font.color.rgb = RGBColor(91, 124, 250)
    document.add_paragraph("Business Intelligence Report", style="Subtitle")
    document.add_paragraph(question)
    document.add_paragraph(f"Prepared: {datetime.now().strftime('%d %B %Y')}")
    document.add_page_break()

    for raw_line in _clean_export_text(report).splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        if line.startswith("**") and line.endswith("**"):
            document.add_heading(line.strip("*").rstrip(":"), level=1)
        elif line.startswith("### "):
            document.add_heading(line[4:], level=2)
        elif line.startswith("## "):
            document.add_heading(line[3:], level=1)
        elif line.startswith(("- ", "* ")):
            document.add_paragraph(line[2:], style="List Bullet")
        elif line.startswith("|"):
            paragraph = document.add_paragraph(line.strip("|").replace("|", "  |  "))
            paragraph.style = document.styles["No Spacing"]
        else:
            document.add_paragraph(line.replace("**", ""))

    styles = document.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(10.5)
    buffer = io.BytesIO()
    document.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{_safe_report_name(question)}.docx"'},
    )


@router.get("/research/job/{job_id}/xlsx")
def export_xlsx(job_id: str, request: Request):
    question, report, sections = _get_research_data(job_id, get_user_id(request))
    if question is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not report:
        raise HTTPException(status_code=400, detail="This report isn't ready yet.")

    workbook = Workbook()
    workbook.remove(workbook.active)
    sheet_data = [
        ("Executive Report", report),
        ("Market Research", sections.get("market_research", "")),
        ("Competitors", sections.get("competitor_analysis", "")),
        ("Financial Analysis", sections.get("financial_analysis", "")),
    ]
    header_fill = PatternFill("solid", fgColor="5B7CFA")
    for sheet_name, content in sheet_data:
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(["VENTURECORE AI", sheet_name])
        sheet.append(["Business", question])
        sheet.append(["Prepared", datetime.now().strftime("%d %B %Y")])
        sheet.append([])
        sheet.append(["Report content"])
        for line in _clean_export_text(content).splitlines():
            if line.strip():
                sheet.append([line.replace("**", "")])
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = Font(color="FFFFFF", bold=True)
        sheet.column_dimensions["A"].width = 110
        sheet.column_dimensions["B"].width = 70
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    sources_sheet = workbook.create_sheet("Sources")
    sources_sheet.append(["Source", "URL", "Quality"])
    source_number = 1
    combined = "\n".join([report] + list(sections.values()))
    seen_urls = set()
    for label, url in re.findall(r"\[([^]]+)\]\((https?://[^)]+)\)", combined):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        domain = url.lower()
        quality = "Official / Government" if any(token in domain for token in [".gov", ".edu", "worldbank.org", "who.int", "oecd.org"]) else "Industry / Company source"
        sources_sheet.append([f"[{source_number}] {label}", url, quality])
        source_number += 1
    for cell in sources_sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
    sources_sheet.column_dimensions["A"].width = 50
    sources_sheet.column_dimensions["B"].width = 75
    sources_sheet.column_dimensions["C"].width = 25
    sources_sheet.freeze_panes = "A2"

    buffer = io.BytesIO()
    workbook.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_safe_report_name(question)}.xlsx"'},
    )


@router.post("/research/job/{job_id}/message", response_model=FollowUpResponse)
def send_follow_up(job_id: str, body: FollowUpRequest, request: Request):
    user_id = get_user_id(request)

    if user_id is not None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM research_jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
        row = cursor.fetchone()
        if row is None:
            conn.close()
            raise HTTPException(status_code=404, detail="Job not found")

        cursor.execute(
            "SELECT role, content FROM follow_up_messages WHERE job_id = ? ORDER BY created_at ASC",
            (job_id,),
        )
        history = [{"role": m["role"], "content": m["content"]} for m in cursor.fetchall()]
        question = row["question"]
        report = row["report"] or ""
    else:
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        history = job["messages"]
        question = job["question"]
        report = job["report"] or ""

    try:
        reply = followup.run(
            question=question,
            report=report,
            history=history,
            new_message=body.message,
        )
    except Exception as e:
        if user_id is not None:
            conn.close()
        logger.exception("Follow-up failed")
        raise HTTPException(status_code=502, detail="Follow-up failed. Please try again.") from e

    if user_id is not None:
        cursor.execute(
            "INSERT INTO follow_up_messages (job_id, role, content) VALUES (?, ?, ?)",
            (job_id, "user", body.message),
        )
        cursor.execute(
            "INSERT INTO follow_up_messages (job_id, role, content) VALUES (?, ?, ?)",
            (job_id, "assistant", reply),
        )
        conn.commit()
        conn.close()
    else:
        jobs.add_message(job_id, "user", body.message)
        jobs.add_message(job_id, "assistant", reply)

    return FollowUpResponse(reply=reply)
