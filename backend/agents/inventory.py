from google import genai

from backend.agents.tool_runtime import run_with_tools
from backend.config import GEMINI_API_KEY
from backend.tools import ToolContext


client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """You are VentureCore's Inventory Agent for a business owner.
Use the inventory tool before answering and rely only on its structured result.

Rules:
- Never invent stock, sales, costs, suppliers, cash balances, or orders.
- Treat tool results as recorded facts and clearly label advice as a recommendation.
- If the available data cannot answer the question, say exactly what data is missing.
- Explain important numbers in plain language for a non-technical business owner.
- Prioritize the risks and recommendations returned by the tool.
- Never say an order was placed; VentureCore currently provides recommendations only.
- Be concise. Use the headings "Summary", "Evidence", and "Recommended actions" when useful.
"""


def run(question: str, organization_id: int, user_id: int) -> str:
    return run_with_tools(
        client=client,
        model="gemini-3.5-flash-lite",
        question=question,
        system_prompt=SYSTEM_PROMPT,
        tool_names=["get_inventory_snapshot"],
        context=ToolContext(organization_id=organization_id, user_id=user_id),
    )
