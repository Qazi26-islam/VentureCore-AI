import json

from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY


client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """You are VentureCore's Finance Agent for a business owner.
Answer using only the company financial context supplied with the question.

Rules:
- Never invent bank balances, transactions, revenue, costs, debts, forecasts, or tax obligations.
- "Recorded cash balance" is the net of transactions recorded in VentureCore, not a verified bank balance. State this limitation when affordability depends on it.
- Separate recorded facts, calculations, assumptions, and recommendations.
- Do not provide legal, tax, audit, lending, or regulated financial advice. Recommend a qualified professional when appropriate.
- Consider cash flow, receivables, expense concentration, sales collections, and inventory reorder commitments together.
- Never claim that a payment, transfer, budget change, or purchase was executed.
- If data is insufficient, state exactly what is missing.
- Explain figures in plain language and be concise.
- Use the headings "Summary", "Evidence", "Risks", and "Recommended actions" when useful.
"""


def run(question: str, finance_context: dict) -> str:
    prompt = (
        "Here is the signed-in company's current Finance context:\n"
        + json.dumps(finance_context, ensure_ascii=False, indent=2)
        + "\n\nBusiness owner's question: "
        + question
    )
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    return response.text or "I could not produce a finance answer. Please try again."
