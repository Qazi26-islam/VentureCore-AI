import json
import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TRACE_RETENTION_DAYS = max(1, int(os.getenv("TRACE_RETENTION_DAYS", "30")))

try:
    GEMINI_COST_RATES = json.loads(os.getenv("GEMINI_COST_RATES_JSON", "{}"))
except json.JSONDecodeError as exc:
    raise RuntimeError("GEMINI_COST_RATES_JSON must be valid JSON.") from exc

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
    )
