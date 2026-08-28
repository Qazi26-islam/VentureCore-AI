from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

QUICK_PROMPT = (
    "You are a Financial Analysis Agent doing a QUICK SCAN. Given a "
    "business idea, write one short paragraph describing its main startup "
    "and margin drivers. Do not calculate or invent figures. Be fast and direct."
)

STANDARD_PROMPT = (
    "You are a Financial Analysis Agent. Given a business idea or question, "
    "describe the financial drivers without inventing figures or performing calculations.\n\n"
    "Cover startup cost categories, ongoing costs, revenue drivers, and margin drivers.\n\n"
    "Then output a markdown table with these columns comparing scenarios qualitatively:\n"
    "| Metric | Worst Case | Base Case | Best Case |\n"
    "|---|---|---|---|\n"
    "Include rows for Revenue drivers, Cost drivers, Margin pressure, and Break-even conditions. "
    "Use evidence appropriate to the business "
    "type and market — worst case should reflect slow adoption or high "
    "costs, base case a reasonable expected outcome, best case strong "
    "execution and demand. Keep each cell short.\n\n"
    "Do not discuss market trends or competitors because other agents handle those. "
    "State which company inputs would be required for a deterministic projection."
)

DEEP_PROMPT = STANDARD_PROMPT + (
    " This is a DEEP RESEARCH request. Add a short "
    "paragraph on the biggest financial risk for this specific business."
)


def run(question: str, depth: str = "standard") -> str:
    if depth == "quick":
        system_prompt = QUICK_PROMPT
    elif depth == "deep":
        system_prompt = DEEP_PROMPT
    else:
        system_prompt = STANDARD_PROMPT

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
        ),
    )
    return response.text
