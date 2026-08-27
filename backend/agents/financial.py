from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

QUICK_PROMPT = (
    "You are a Financial Analysis Agent doing a QUICK SCAN. Given a "
    "business idea, write ONE short paragraph with a rough ballpark "
    "startup cost range and typical margin for this type of business. No "
    "table, be fast and direct. Make clear this is a very rough estimate."
)

STANDARD_PROMPT = (
    "You are a Financial Analysis Agent. Given a business idea or question, "
    "give a rough, honest estimate of the financial picture. Be clear this "
    "is a rough estimate, not a formal projection.\n\n"
    "First write 1-2 short paragraphs covering startup costs, ongoing "
    "monthly costs, and typical margins for this type of business.\n\n"
    "Then output a markdown table with EXACTLY these columns comparing "
    "three scenarios:\n"
    "| Metric | Worst Case | Base Case | Best Case |\n"
    "|---|---|---|---|\n"
    "Include rows for at least: Monthly Revenue, Monthly Costs, Net Margin, "
    "Time to Break-even. Use realistic ranges appropriate to the business "
    "type and market — worst case should reflect slow adoption or high "
    "costs, base case a reasonable expected outcome, best case strong "
    "execution and demand. Keep each cell short (a number, range, or short "
    "phrase, not a sentence).\n\n"
    "Do not discuss market trends or competitors — other agents handle those. "
    "At the very end, output one machine-readable line using exactly this format: "
    "FINANCIAL_CHART_DATA: {\"months\":[1,2,3,4,5,6,7,8,9,10,11,12],\"revenue\":[1000,1500,2000,2500,3000,3500,4000,4500,5000,5500,6000,6500],\"expenses\":[4000,3800,3600,3500,3400,3300,3200,3200,3200,3200,3200,3200],\"currency\":\"MYR\"}. "
    "Replace the example values with a realistic base-case 12-month projection and output valid JSON on one line."
)

DEEP_PROMPT = STANDARD_PROMPT + (
    " This is a DEEP RESEARCH request — add one more row to the table for "
    "Year-1 Cumulative Profit/Loss, and after the table add a short "
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
