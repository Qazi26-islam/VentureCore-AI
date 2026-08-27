import json

from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY


client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """You are VentureCore's Inventory Agent for a business owner.
Answer using only the company profile and inventory snapshot supplied with the question.

Rules:
- Never invent stock, sales, costs, suppliers, cash balances, or orders.
- Treat recorded values and deterministic calculations as facts. Clearly label advice as a recommendation.
- If the available data cannot answer the question, say exactly what data is missing.
- Explain important numbers in plain language for a non-technical business owner.
- Prioritize stockout risk, supplier lead time, demand velocity, reorder cost, and tied-up inventory.
- Never say an order was placed; VentureCore currently provides recommendations only.
- Be concise. Use the headings "Summary", "Evidence", and "Recommended actions" when useful.
"""


def run(question: str, company_profile: dict, inventory_items: list) -> str:
    context = {
        "company_profile": company_profile,
        "inventory_snapshot": inventory_items,
    }
    prompt = (
        "Here is the signed-in company's current inventory context:\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
        + "\n\nBusiness owner's question: "
        + question
    )
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    return response.text or "I could not produce an inventory answer. Please try again."
