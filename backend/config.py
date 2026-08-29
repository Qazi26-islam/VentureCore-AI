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

try:
    GEMINI_COST_RATES = json.loads(os.getenv("GEMINI_COST_RATES_JSON", "{}"))
except json.JSONDecodeError as exc:
    raise RuntimeError("GEMINI_COST_RATES_JSON must be valid JSON.") from exc

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
    )
