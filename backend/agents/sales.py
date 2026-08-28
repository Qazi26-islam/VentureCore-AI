from google import genai

from backend.agents.tool_runtime import run_with_tools
from backend.config import GEMINI_API_KEY
from backend.tools import ToolContext


client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """You are VentureCore's Sales & CRM Agent for a business owner.
Use the sales tool before answering and rely only on its structured result.

Rules:
- Never invent customers, transactions, balances, revenue, products, or contact activity.
- Clearly separate recorded facts from recommendations.
- If the data cannot answer a question, state which data is missing.
- Do not claim that a customer will churn or buy again as a fact. Describe signals and uncertainty.
- Prioritize the risks, evidence, and practical follow-up actions returned by the tool.
- Never say that a message, invoice, refund, or collection action was sent or completed.
- Explain numbers in plain language and keep the response concise.
- Use the headings "Summary", "Evidence", and "Recommended actions" when useful.
"""


def run(question: str, organization_id: int, user_id: int) -> str:
    return run_with_tools(
        client=client,
        model="gemini-3.5-flash-lite",
        question=question,
        system_prompt=SYSTEM_PROMPT,
        tool_names=["get_sales_snapshot"],
        context=ToolContext(organization_id=organization_id, user_id=user_id),
    )
