import json
import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TRACE_RETENTION_DAYS = max(1, int(os.getenv("TRACE_RETENTION_DAYS", "30")))
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID", "")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET", "")
SHOPIFY_TOKEN_ENCRYPTION_KEY = os.getenv("SHOPIFY_TOKEN_ENCRYPTION_KEY", "")
SHOPIFY_APP_URL = os.getenv("SHOPIFY_APP_URL", "").rstrip("/")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2026-07")
SHOPIFY_SCOPES = os.getenv(
    "SHOPIFY_SCOPES", "read_products,read_inventory,read_orders,read_customers"
)
SHOPIFY_RECONCILE_HOURS = max(1, int(os.getenv("SHOPIFY_RECONCILE_HOURS", "6")))
APP_URL = os.getenv("APP_URL", SHOPIFY_APP_URL).rstrip("/")
JOB_RUNNER_SECRET = os.getenv("JOB_RUNNER_SECRET", "")
JOB_TIMEOUT_SECONDS = max(10, int(os.getenv("JOB_TIMEOUT_SECONDS", "120")))
JOB_MAX_ATTEMPTS = max(1, int(os.getenv("JOB_MAX_ATTEMPTS", "3")))
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME)
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
ALERT_STOCKOUT_DAYS = max(1, int(os.getenv("ALERT_STOCKOUT_DAYS", "14")))
ALERT_RECEIVABLE_MIN_MINOR = max(0, int(os.getenv("ALERT_RECEIVABLE_MIN_MINOR", "50000")))
ALERT_EXPENSE_INCREASE_PERCENT = max(0, int(os.getenv("ALERT_EXPENSE_INCREASE_PERCENT", "50")))
ALERT_EXPENSE_INCREASE_MIN_MINOR = max(0, int(os.getenv("ALERT_EXPENSE_INCREASE_MIN_MINOR", "10000")))
ALERT_CASH_DROP_PERCENT = max(0, int(os.getenv("ALERT_CASH_DROP_PERCENT", "20")))
ALERT_CASH_DROP_MIN_MINOR = max(0, int(os.getenv("ALERT_CASH_DROP_MIN_MINOR", "50000")))
SESSION_SECRET = os.getenv("SESSION_SECRET", "development-session-secret")

try:
    GEMINI_COST_RATES = json.loads(os.getenv("GEMINI_COST_RATES_JSON", "{}"))
except json.JSONDecodeError as exc:
    raise RuntimeError("GEMINI_COST_RATES_JSON must be valid JSON.") from exc

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
    )
