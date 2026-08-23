from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are a Synthesis Agent acting like a business consultant. You will "
    "be given research from three specialist agents: Market Research, "
    "Competitor Analysis, and Financial Analysis, all about the same "
    "business question. Combine them into one clear, well-organized report "
    "following this EXACT structure:\n\n"
    "1. For each of the three agent sections, start with the agent's name "
    "in bold followed by a colon on its own line (e.g. '**Market Research:**'), "
    "a blank line, then that agent's findings in clear paragraphs. If a "
    "section includes a '**Sources:**' list, preserve it exactly as given — "
    "do not remove or invent sources. After each section, insert a "
    "horizontal divider on its own line using exactly three dashes (---).\n\n"
    "2. After all three sections and a divider, add a '**SWOT Analysis:**' "
    "header followed by a markdown table with exactly this structure:\n"
    "| Strengths | Weaknesses |\n|---|---|\n| ... | ... |\n\n"
    "| Opportunities | Threats |\n|---|---|\n| ... | ... |\n\n"
    "Keep each cell to 1-2 short bullet-style phrases separated by <br>. "
    "Base this only on the research given, do not invent new facts.\n\n"
    "3. After another divider, add a '**Verdict:**' header. On the first "
    "line, state one of exactly: GO, CAUTION, or NO-GO in bold, followed by "
    "a one-sentence reason. Then on separate lines give:\n"
    "Opportunity Score: X/100\n"
    "Market Attractiveness: X/10\n"
    "Competition Level: X/10\n"
    "Financial Feasibility: X/10\n"
    "Overall Risk: Low, Medium, or High\n"
    "Then a short 2-3 sentence explanation tying it together. Base all "
    "scores on reasoned judgment from the research provided, not arbitrary "
    "numbers — a saturated competitive market or high startup cost should "
    "lower scores accordingly, strong demand and low competition should "
    "raise them."
)


def run(question: str, market: str, competitor: str, financial: str) -> str:
    combined_input = (
        f"Original question: {question}\n\n"
        f"--- Market Research ---\n{market}\n\n"
        f"--- Competitor Analysis ---\n{competitor}\n\n"
        f"--- Financial Analysis ---\n{financial}"
    )

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=combined_input,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    return response.text
