from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

from backend.money import (
    display_amount_decimal,
    has_excess_precision,
    major_to_minor,
    minor_to_major,
    normalize_currency,
    parse_display_amount,
)


logger = logging.getLogger("database_migrations")
MIGRATION_VERSION = 1
OBSERVABILITY_MIGRATION_VERSION = 2
SHOPIFY_MIGRATION_VERSION = 3
SCHEDULED_WORKERS_MIGRATION_VERSION = 4
DOMAIN_TABLES = (
    "research_jobs",
    "follow_up_messages",
    "company_profiles",
    "data_uploads",
    "suppliers",
    "products",
    "inventory_transactions",
    "customers",
    "sales_orders",
    "finance_transactions",
)

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user', 'admin')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

CURRENT_SCHEMAS = {
    "research_jobs": """CREATE TABLE {name} (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, organization_id INTEGER NOT NULL,
        question TEXT NOT NULL, title TEXT, report TEXT, sections TEXT, favorite INTEGER DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'manual', external_id TEXT, last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""",
    "follow_up_messages": """CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, organization_id INTEGER NOT NULL,
        role TEXT NOT NULL, content TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'manual', external_id TEXT, last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (job_id) REFERENCES research_jobs(id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""",
    "company_profiles": """CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        organization_id INTEGER NOT NULL, company_name TEXT NOT NULL, industry TEXT, country TEXT,
        monthly_budget_minor INTEGER, currency TEXT NOT NULL DEFAULT 'MYR'
            CHECK(length(currency) = 3 AND currency = UPPER(currency)),
        products_services TEXT, target_customers TEXT, main_competitors TEXT, business_goals TEXT,
        business_stage TEXT DEFAULT 'Existing business', source TEXT NOT NULL DEFAULT 'manual',
        external_id TEXT, last_synced_at TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(organization_id, user_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""",
    "data_uploads": """CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, organization_id INTEGER NOT NULL,
        filename TEXT NOT NULL, data_type TEXT NOT NULL, row_count INTEGER DEFAULT 0,
        column_count INTEGER DEFAULT 0, quality_summary TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'csv_import', external_id TEXT, last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""",
    "suppliers": """CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, organization_id INTEGER NOT NULL,
        name TEXT NOT NULL, contact_name TEXT, email TEXT, phone TEXT, lead_time_days INTEGER DEFAULT 7,
        payment_terms TEXT, source TEXT NOT NULL DEFAULT 'manual', external_id TEXT, last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""",
    "products": """CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, organization_id INTEGER NOT NULL,
        supplier_id INTEGER, sku TEXT, name TEXT NOT NULL, category TEXT,
        unit_cost_minor INTEGER NOT NULL DEFAULT 0, selling_price_minor INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'MYR' CHECK(length(currency) = 3 AND currency = UPPER(currency)),
        reorder_point REAL DEFAULT 0, lead_time_days INTEGER DEFAULT 7, active INTEGER DEFAULT 1,
        source TEXT NOT NULL DEFAULT 'manual', external_id TEXT, last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(organization_id, sku),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id),
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id))""",
    "inventory_transactions": """CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        organization_id INTEGER NOT NULL, transaction_type TEXT NOT NULL, quantity_change REAL NOT NULL,
        unit_cost_minor INTEGER, currency TEXT NOT NULL DEFAULT 'MYR'
            CHECK(length(currency) = 3 AND currency = UPPER(currency)),
        reference_note TEXT, source TEXT NOT NULL DEFAULT 'manual', external_id TEXT, last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id), FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""",
    "customers": """CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, organization_id INTEGER NOT NULL,
        name TEXT NOT NULL, email TEXT, phone TEXT, segment TEXT, notes TEXT,
        source TEXT NOT NULL DEFAULT 'manual', external_id TEXT, last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""",
    "sales_orders": """CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, organization_id INTEGER NOT NULL,
        customer_id INTEGER, product_id INTEGER NOT NULL, quantity REAL NOT NULL,
        unit_price_minor INTEGER NOT NULL, total_amount_minor INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'MYR' CHECK(length(currency) = 3 AND currency = UPPER(currency)),
        payment_status TEXT DEFAULT 'paid', due_date TEXT, reference_note TEXT,
        source TEXT NOT NULL DEFAULT 'manual', external_id TEXT, last_synced_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id), FOREIGN KEY (organization_id) REFERENCES organizations(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id), FOREIGN KEY (product_id) REFERENCES products(id))""",
    "finance_transactions": """CREATE TABLE {name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, organization_id INTEGER NOT NULL,
        transaction_type TEXT NOT NULL, amount_minor INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'MYR' CHECK(length(currency) = 3 AND currency = UPPER(currency)),
        category TEXT, description TEXT, source TEXT NOT NULL DEFAULT 'manual', external_id TEXT,
        last_synced_at TEXT, related_sale_id INTEGER UNIQUE, transaction_date TEXT DEFAULT CURRENT_DATE,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id), FOREIGN KEY (organization_id) REFERENCES organizations(id),
        FOREIGN KEY (related_sale_id) REFERENCES sales_orders(id))""",
}

LEGACY_SCHEMAS = {
    "research_jobs": """CREATE TABLE {name} (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
        question TEXT NOT NULL, title TEXT, report TEXT, sections TEXT, favorite INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id))""",
    "follow_up_messages": """CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (job_id) REFERENCES research_jobs(id))""",
    "company_profiles": """CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL, company_name TEXT NOT NULL, industry TEXT, country TEXT,
        currency TEXT DEFAULT 'MYR', products_services TEXT, target_customers TEXT, main_competitors TEXT,
        monthly_budget TEXT, business_goals TEXT, business_stage TEXT DEFAULT 'Existing business',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id))""",
    "data_uploads": """CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        filename TEXT NOT NULL, data_type TEXT NOT NULL, row_count INTEGER DEFAULT 0,
        column_count INTEGER DEFAULT 0, quality_summary TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id))""",
    "suppliers": """CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        name TEXT NOT NULL, contact_name TEXT, email TEXT, phone TEXT, lead_time_days INTEGER DEFAULT 7,
        payment_terms TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id))""",
    "products": """CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        supplier_id INTEGER, sku TEXT, name TEXT NOT NULL, category TEXT, unit_cost REAL DEFAULT 0,
        selling_price REAL DEFAULT 0, reorder_point REAL DEFAULT 0, lead_time_days INTEGER DEFAULT 7,
        active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, sku),
        FOREIGN KEY (user_id) REFERENCES users(id), FOREIGN KEY (supplier_id) REFERENCES suppliers(id))""",
    "inventory_transactions": """CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL, user_id INTEGER NOT NULL, transaction_type TEXT NOT NULL,
        quantity_change REAL NOT NULL, unit_cost REAL, reference_note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(id), FOREIGN KEY (user_id) REFERENCES users(id))""",
    "customers": """CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        name TEXT NOT NULL, email TEXT, phone TEXT, segment TEXT, notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id))""",
    "sales_orders": """CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        customer_id INTEGER, product_id INTEGER NOT NULL, quantity REAL NOT NULL, unit_price REAL NOT NULL,
        total_amount REAL NOT NULL, payment_status TEXT DEFAULT 'paid', due_date TEXT, reference_note TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (customer_id) REFERENCES customers(id), FOREIGN KEY (product_id) REFERENCES products(id))""",
    "finance_transactions": """CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL, transaction_type TEXT NOT NULL, amount REAL NOT NULL, category TEXT,
        description TEXT, source TEXT DEFAULT 'manual', related_sale_id INTEGER UNIQUE,
        transaction_date TEXT DEFAULT CURRENT_DATE, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id), FOREIGN KEY (related_sale_id) REFERENCES sales_orders(id))""",
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    if not _table_exists(conn, table):
        return []
    return [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"').fetchall()]


def _source(row: dict, table: str) -> str:
    if row.get("source"):
        return str(row["source"])
    return "csv_import" if table == "data_uploads" else "manual"


def _currency(value: str | None) -> str:
    try:
        return normalize_currency(value)
    except ValueError:
        logger.warning("Invalid legacy currency %r; defaulting to MYR", value)
        return "MYR"


def _money(row: dict, old_name: str, new_name: str, currency: str, table: str) -> int | None:
    if new_name in row:
        value = row.get(new_name)
        return None if value is None else int(value)
    value = row.get(old_name)
    if value is None:
        return None
    if has_excess_precision(value, currency):
        logger.warning(
            "Rounded %s.%s row id=%s value=%r to the %s minor-unit boundary",
            table, old_name, row.get("id"), value, currency,
        )
    return major_to_minor(value, currency)


def _insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    columns = list(row)
    placeholders = ", ".join("?" for _ in columns)
    names = ", ".join(f'"{column}"' for column in columns)
    conn.execute(f'INSERT INTO "{table}__new" ({names}) VALUES ({placeholders})', tuple(row[column] for column in columns))


def _common(row: dict, table: str) -> dict:
    return {
        "organization_id": int(row.get("organization_id") or 1),
        "source": _source(row, table),
        "external_id": row.get("external_id"),
        "last_synced_at": row.get("last_synced_at"),
    }


def _upgrade_row(table: str, row: dict, user_currencies: dict[int, str], product_currencies: dict[int, str]) -> dict:
    common = _common(row, table)
    if table == "research_jobs":
        return {"id": row["id"], "user_id": row["user_id"], **common, "question": row["question"],
                "title": row.get("title"), "report": row.get("report"), "sections": row.get("sections"),
                "favorite": row.get("favorite", 0), "created_at": row.get("created_at")}
    if table == "follow_up_messages":
        return {"id": row["id"], "job_id": row["job_id"], **common, "role": row["role"],
                "content": row["content"], "created_at": row.get("created_at")}
    if table == "company_profiles":
        currency = _currency(row.get("currency"))
        budget = row.get("monthly_budget_minor")
        if budget is None and row.get("monthly_budget"):
            try:
                original_budget = display_amount_decimal(str(row["monthly_budget"]), currency)
                if original_budget is not None and has_excess_precision(original_budget, currency):
                    logger.warning(
                        "Rounded company_profiles.monthly_budget row id=%s value=%r to the %s minor-unit boundary",
                        row.get("id"), row.get("monthly_budget"), currency,
                    )
                budget = parse_display_amount(str(row["monthly_budget"]), currency)
            except ValueError:
                logger.warning("Could not migrate company_profiles.monthly_budget row id=%s value=%r", row.get("id"), row.get("monthly_budget"))
        return {"id": row["id"], "user_id": row["user_id"], "organization_id": common["organization_id"],
                "company_name": row["company_name"], "industry": row.get("industry"), "country": row.get("country"),
                "monthly_budget_minor": budget, "currency": currency, "products_services": row.get("products_services"),
                "target_customers": row.get("target_customers"), "main_competitors": row.get("main_competitors"),
                "business_goals": row.get("business_goals"), "business_stage": row.get("business_stage"),
                "source": common["source"], "external_id": common["external_id"],
                "last_synced_at": common["last_synced_at"], "updated_at": row.get("updated_at")}
    if table == "data_uploads":
        return {"id": row["id"], "user_id": row["user_id"], **common, "filename": row["filename"],
                "data_type": row["data_type"], "row_count": row.get("row_count", 0),
                "column_count": row.get("column_count", 0), "quality_summary": row["quality_summary"],
                "created_at": row.get("created_at")}
    if table == "suppliers":
        return {"id": row["id"], "user_id": row["user_id"], **common, "name": row["name"],
                "contact_name": row.get("contact_name"), "email": row.get("email"), "phone": row.get("phone"),
                "lead_time_days": row.get("lead_time_days", 7), "payment_terms": row.get("payment_terms"),
                "created_at": row.get("created_at")}
    if table == "products":
        currency = _currency(row.get("currency") or user_currencies.get(row["user_id"]))
        return {"id": row["id"], "user_id": row["user_id"], **common, "supplier_id": row.get("supplier_id"),
                "sku": row.get("sku"), "name": row["name"], "category": row.get("category"),
                "unit_cost_minor": _money(row, "unit_cost", "unit_cost_minor", currency, table) or 0,
                "selling_price_minor": _money(row, "selling_price", "selling_price_minor", currency, table) or 0,
                "currency": currency, "reorder_point": row.get("reorder_point", 0),
                "lead_time_days": row.get("lead_time_days", 7), "active": row.get("active", 1),
                "created_at": row.get("created_at")}
    if table == "inventory_transactions":
        currency = _currency(row.get("currency") or product_currencies.get(row["product_id"]) or user_currencies.get(row["user_id"]))
        return {"id": row["id"], "product_id": row["product_id"], "user_id": row["user_id"], **common,
                "transaction_type": row["transaction_type"], "quantity_change": row["quantity_change"],
                "unit_cost_minor": _money(row, "unit_cost", "unit_cost_minor", currency, table), "currency": currency,
                "reference_note": row.get("reference_note"), "created_at": row.get("created_at")}
    if table == "customers":
        return {"id": row["id"], "user_id": row["user_id"], **common, "name": row["name"],
                "email": row.get("email"), "phone": row.get("phone"), "segment": row.get("segment"),
                "notes": row.get("notes"), "created_at": row.get("created_at")}
    if table == "sales_orders":
        currency = _currency(row.get("currency") or product_currencies.get(row["product_id"]) or user_currencies.get(row["user_id"]))
        return {"id": row["id"], "user_id": row["user_id"], **common, "customer_id": row.get("customer_id"),
                "product_id": row["product_id"], "quantity": row["quantity"],
                "unit_price_minor": _money(row, "unit_price", "unit_price_minor", currency, table) or 0,
                "total_amount_minor": _money(row, "total_amount", "total_amount_minor", currency, table) or 0,
                "currency": currency, "payment_status": row.get("payment_status", "paid"),
                "due_date": row.get("due_date"), "reference_note": row.get("reference_note"),
                "created_at": row.get("created_at")}
    if table == "finance_transactions":
        currency = _currency(row.get("currency") or user_currencies.get(row["user_id"]))
        return {"id": row["id"], "user_id": row["user_id"], **common,
                "transaction_type": row["transaction_type"],
                "amount_minor": _money(row, "amount", "amount_minor", currency, table) or 0,
                "currency": currency, "category": row.get("category"), "description": row.get("description"),
                "related_sale_id": row.get("related_sale_id"), "transaction_date": row.get("transaction_date"),
                "created_at": row.get("created_at")}
    raise ValueError(f"Unknown domain table: {table}")


def _downgrade_row(table: str, row: dict) -> dict:
    if table == "research_jobs":
        return {key: row.get(key) for key in ("id", "user_id", "question", "title", "report", "sections", "favorite", "created_at")}
    if table == "follow_up_messages":
        return {key: row.get(key) for key in ("id", "job_id", "role", "content", "created_at")}
    if table == "company_profiles":
        budget = row.get("monthly_budget_minor")
        budget_text = None if budget is None else format(minor_to_major(budget, row["currency"]), "f")
        return {"id": row["id"], "user_id": row["user_id"], "company_name": row["company_name"],
                "industry": row.get("industry"), "country": row.get("country"), "currency": row["currency"],
                "products_services": row.get("products_services"), "target_customers": row.get("target_customers"),
                "main_competitors": row.get("main_competitors"), "monthly_budget": budget_text,
                "business_goals": row.get("business_goals"), "business_stage": row.get("business_stage"),
                "updated_at": row.get("updated_at")}
    passthrough = {
        "data_uploads": ("id", "user_id", "filename", "data_type", "row_count", "column_count", "quality_summary", "created_at"),
        "suppliers": ("id", "user_id", "name", "contact_name", "email", "phone", "lead_time_days", "payment_terms", "created_at"),
        "customers": ("id", "user_id", "name", "email", "phone", "segment", "notes", "created_at"),
    }
    if table in passthrough:
        return {key: row.get(key) for key in passthrough[table]}
    if table == "products":
        result = {key: row.get(key) for key in ("id", "user_id", "supplier_id", "sku", "name", "category", "reorder_point", "lead_time_days", "active", "created_at")}
        result["unit_cost"] = float(minor_to_major(row["unit_cost_minor"], row["currency"]))
        result["selling_price"] = float(minor_to_major(row["selling_price_minor"], row["currency"]))
        return result
    if table == "inventory_transactions":
        result = {key: row.get(key) for key in ("id", "product_id", "user_id", "transaction_type", "quantity_change", "reference_note", "created_at")}
        result["unit_cost"] = None if row.get("unit_cost_minor") is None else float(minor_to_major(row["unit_cost_minor"], row["currency"]))
        return result
    if table == "sales_orders":
        result = {key: row.get(key) for key in ("id", "user_id", "customer_id", "product_id", "quantity", "payment_status", "due_date", "reference_note", "created_at")}
        result["unit_price"] = float(minor_to_major(row["unit_price_minor"], row["currency"]))
        result["total_amount"] = float(minor_to_major(row["total_amount_minor"], row["currency"]))
        return result
    if table == "finance_transactions":
        result = {key: row.get(key) for key in ("id", "user_id", "transaction_type", "category", "description", "source", "related_sale_id", "transaction_date", "created_at")}
        result["amount"] = float(minor_to_major(row["amount_minor"], row["currency"]))
        return result
    raise ValueError(f"Unknown domain table: {table}")


def _replace_tables(conn: sqlite3.Connection, schemas: dict[str, str], rows_by_table: dict[str, list[dict]], mapper) -> None:
    for table in reversed(DOMAIN_TABLES):
        conn.execute(f'DROP TABLE IF EXISTS "{table}__new"')
    for table in DOMAIN_TABLES:
        conn.execute(schemas[table].format(name=f'"{table}__new"'))
        for row in rows_by_table.get(table, []):
            _insert(conn, table, mapper(table, row))
    for table in reversed(DOMAIN_TABLES):
        if _table_exists(conn, table):
            conn.execute(f'DROP TABLE "{table}"')
    for table in DOMAIN_TABLES:
        conn.execute(f'ALTER TABLE "{table}__new" RENAME TO "{table}"')


def _create_sync_indexes(conn: sqlite3.Connection) -> None:
    for table in DOMAIN_TABLES:
        conn.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS "ux_{table}_org_source_external" '
            f'ON "{table}" (organization_id, source, external_id) WHERE external_id IS NOT NULL'
        )


def _upgrade_observability(conn: sqlite3.Connection) -> None:
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "role" not in user_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user' "
            "CHECK(role IN ('user', 'admin'))"
        )
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_runs (
        id TEXT PRIMARY KEY,
        organization_id INTEGER NOT NULL,
        agent_name TEXT NOT NULL,
        trigger_text TEXT,
        job_id TEXT,
        status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'success', 'error')),
        failure_mode TEXT,
        final_result TEXT,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        cost_minor INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'USD' CHECK(length(currency) = 3 AND currency = UPPER(currency)),
        latency_ms INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT,
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_run_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        organization_id INTEGER NOT NULL,
        step_index INTEGER NOT NULL,
        step_type TEXT NOT NULL CHECK(step_type IN ('model', 'tool')),
        model_name TEXT,
        tool_name TEXT,
        arguments_json TEXT,
        outcome_json TEXT,
        status TEXT NOT NULL CHECK(status IN ('success', 'error')),
        failure_mode TEXT,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        cost_minor INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'USD' CHECK(length(currency) = 3 AND currency = UPPER(currency)),
        latency_ms INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(run_id, step_index),
        FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_runs_created_at ON agent_runs(created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_run_steps_tool ON agent_run_steps(tool_name)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
        (OBSERVABILITY_MIGRATION_VERSION,),
    )


def _downgrade_observability(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS agent_run_steps")
    conn.execute("DROP TABLE IF EXISTS agent_runs")
    conn.execute(
        "DELETE FROM schema_migrations WHERE version = ?",
        (OBSERVABILITY_MIGRATION_VERSION,),
    )
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "role" in user_columns:
        conn.execute("ALTER TABLE users DROP COLUMN role")


def _upgrade_shopify(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS shopify_connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        shop_domain TEXT NOT NULL,
        access_token_encrypted TEXT NOT NULL,
        refresh_token_encrypted TEXT,
        token_expires_at TEXT,
        refresh_token_expires_at TEXT,
        scopes TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'connected'
            CHECK(status IN ('connected', 'syncing', 'stale', 'error', 'disconnected')),
        sync_resource TEXT,
        sync_cursor TEXT,
        sync_mode TEXT,
        last_attempt_at TEXT,
        last_successful_sync_at TEXT,
        records_synced INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(organization_id),
        UNIQUE(shop_domain),
        FOREIGN KEY (organization_id) REFERENCES organizations(id),
        FOREIGN KEY (user_id) REFERENCES users(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS shopify_webhook_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        connection_id INTEGER NOT NULL,
        webhook_id TEXT NOT NULL,
        event_id TEXT,
        topic TEXT NOT NULL,
        payload_encrypted TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'received'
            CHECK(status IN ('received', 'processed', 'dead_letter')),
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        processed_at TEXT,
        UNIQUE(connection_id, webhook_id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id),
        FOREIGN KEY (connection_id) REFERENCES shopify_connections(id) ON DELETE CASCADE)""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_shopify_connections_reconcile "
        "ON shopify_connections(status, last_successful_sync_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_shopify_webhooks_status "
        "ON shopify_webhook_events(organization_id, status)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
        (SHOPIFY_MIGRATION_VERSION,),
    )


def _downgrade_shopify(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS shopify_webhook_events")
    conn.execute("DROP TABLE IF EXISTS shopify_connections")
    conn.execute(
        "DELETE FROM schema_migrations WHERE version = ?",
        (SHOPIFY_MIGRATION_VERSION,),
    )


def _upgrade_scheduled_workers(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS delivery_preferences (
        organization_id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        email TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
        quiet_start TEXT NOT NULL DEFAULT '22:00',
        quiet_end TEXT NOT NULL DEFAULT '07:00',
        timezone TEXT NOT NULL DEFAULT 'Asia/Kuala_Lumpur',
        briefing_hour INTEGER NOT NULL DEFAULT 8 CHECK(briefing_hour BETWEEN 0 AND 23),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (organization_id) REFERENCES organizations(id),
        FOREIGN KEY (user_id) REFERENCES users(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS briefing_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        period TEXT NOT NULL,
        subject TEXT NOT NULL,
        content_json TEXT NOT NULL,
        html_body TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'computed',
        external_id TEXT NOT NULL,
        last_synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(organization_id, period),
        UNIQUE(organization_id, source, external_id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS scheduled_job_runs (
        id TEXT PRIMARY KEY,
        organization_id INTEGER NOT NULL,
        job_type TEXT NOT NULL,
        period TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running'
            CHECK(status IN ('running', 'retry', 'success', 'failed', 'timed_out')),
        attempts INTEGER NOT NULL DEFAULT 0,
        timeout_seconds INTEGER NOT NULL,
        next_retry_at TEXT,
        trace_run_id TEXT,
        outcome_json TEXT,
        failure_mode TEXT,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT,
        UNIQUE(organization_id, job_type, period),
        FOREIGN KEY (organization_id) REFERENCES organizations(id),
        FOREIGN KEY (trace_run_id) REFERENCES agent_runs(id) ON DELETE SET NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS notification_deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('briefing', 'alert')),
        idempotency_key TEXT NOT NULL,
        recipient TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('sending', 'sent', 'failed')),
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        sent_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(organization_id, kind, idempotency_key),
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS alert_states (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        organization_id INTEGER NOT NULL,
        alert_key TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0, 1)),
        value_json TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'computed',
        external_id TEXT NOT NULL,
        last_synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_alerted_at TEXT,
        UNIQUE(organization_id, alert_key),
        UNIQUE(organization_id, source, external_id),
        FOREIGN KEY (organization_id) REFERENCES organizations(id))""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_scheduled_jobs_retry "
        "ON scheduled_job_runs(status, next_retry_at)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
        (SCHEDULED_WORKERS_MIGRATION_VERSION,),
    )


def _downgrade_scheduled_workers(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS alert_states")
    conn.execute("DROP TABLE IF EXISTS notification_deliveries")
    conn.execute("DROP TABLE IF EXISTS scheduled_job_runs")
    conn.execute("DROP TABLE IF EXISTS briefing_cache")
    conn.execute("DROP TABLE IF EXISTS delivery_preferences")
    conn.execute(
        "DELETE FROM schema_migrations WHERE version = ?",
        (SCHEDULED_WORKERS_MIGRATION_VERSION,),
    )


def upgrade(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute(USERS_SCHEMA)
    conn.execute("""CREATE TABLE IF NOT EXISTS organizations (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT OR IGNORE INTO organizations (id, name) VALUES (1, 'Default Organization')")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (MIGRATION_VERSION,)).fetchone():
        _create_sync_indexes(conn)
        _upgrade_observability(conn)
        _upgrade_shopify(conn)
        _upgrade_scheduled_workers(conn)
        conn.commit()
        return

    rows_by_table = {table: _rows(conn, table) for table in DOMAIN_TABLES}
    profile_rows = rows_by_table["company_profiles"]
    user_currencies = {int(row["user_id"]): _currency(row.get("currency")) for row in profile_rows}
    product_rows = rows_by_table["products"]
    product_currencies = {
        int(row["id"]): _currency(row.get("currency") or user_currencies.get(int(row["user_id"])))
        for row in product_rows
    }

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        _replace_tables(
            conn,
            CURRENT_SCHEMAS,
            rows_by_table,
            lambda table, row: _upgrade_row(table, row, user_currencies, product_currencies),
        )
        _create_sync_indexes(conn)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (MIGRATION_VERSION,))
        _upgrade_observability(conn)
        _upgrade_shopify(conn)
        _upgrade_scheduled_workers(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def downgrade(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    if not _table_exists(conn, "schema_migrations") or not conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?", (MIGRATION_VERSION,)
    ).fetchone():
        return
    rows_by_table = {table: _rows(conn, table) for table in DOMAIN_TABLES}
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        _downgrade_scheduled_workers(conn)
        _downgrade_shopify(conn)
        _downgrade_observability(conn)
        _replace_tables(conn, LEGACY_SCHEMAS, rows_by_table, _downgrade_row)
        conn.execute("DELETE FROM schema_migrations WHERE version = ?", (MIGRATION_VERSION,))
        conn.execute("DROP TABLE organizations")
        conn.execute("DROP TABLE schema_migrations")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def main() -> None:
    parser = argparse.ArgumentParser(description="VentureCore SQLite schema migrations")
    parser.add_argument("direction", choices=("upgrade", "downgrade"))
    parser.add_argument("--database", default=str(Path(__file__).resolve().parent.parent / "app.db"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    conn = sqlite3.connect(args.database)
    try:
        (upgrade if args.direction == "upgrade" else downgrade)(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
