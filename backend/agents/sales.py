import json

from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY


client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """You are VentureCore's Sales & CRM Agent for a business owner.
Answer using only the company and sales context provided with the question.

Rules:
- Never invent customers, transactions, balances, revenue, products, or contact activity.
- Clearly separate recorded facts from recommendations.
- If the data cannot answer a question, state which data is missing.
- Do not claim that a customer will churn or buy again as a fact. Describe signals and uncertainty.
- Prioritize unpaid invoices, revenue concentration, customer value, product performance, and practical follow-up actions.
- Never say that a message, invoice, refund, or collection action was sent or completed.
- Explain numbers in plain language and keep the response concise.
- Use the headings "Summary", "Evidence", and "Recommended actions" when useful.
"""


def run(question: str, sales_context: dict) -> str:
    prompt = (
        "Here is the signed-in company's current Sales & CRM context:\n"
        + json.dumps(sales_context, ensure_ascii=False, indent=2)
        + "\n\nBusiness owner's question: "
        + question
    )
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    return response.text or "I could not produce a sales answer. Please try again."
