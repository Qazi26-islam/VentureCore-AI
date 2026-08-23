from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
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
    "Do not discuss market trends or competitors — other agents handle those."
)


def run(question: str) -> str:
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    return response.text
