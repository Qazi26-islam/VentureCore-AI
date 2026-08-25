from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

QUICK_PROMPT = (
    "You are a Synthesis Agent. You will be given quick-scan research from "
    "three specialist agents about the same business question. Combine "
    "them into a short report: for each of the three sections, a bold "
    "label on its own line (e.g. '**Market Research:**') followed by "
    "their finding. After all three, add a '**Verdict:**' header with "
    "GO, CAUTION, or NO-GO in bold, an Opportunity Score: X/100, and one "
    "sentence explaining why. Keep the whole thing short — this is a "
    "quick scan, not a deep report."
)

BASE_PROMPT = (
    "You are a Synthesis Agent acting like a business consultant. You will "
    "be given research from three specialist agents: Market Research, "
    "Competitor Analysis, and Financial Analysis, all about the same "
    "business question. Combine them into one clear, well-organized report "
    "following this EXACT structure:\n\n"
    "1. For each of the three agent sections, start with the agent's name "
    "in bold followed by a colon on its own line (e.g. '**Market Research:**'), "
    "a blank line, then that agent's findings in clear paragraphs. If the "
    "Competitor Analysis section includes a markdown competitor comparison "
    "table, or the Financial Analysis section includes a scenario table "
    "(Worst/Base/Best case), preserve those tables EXACTLY as given — do "
    "not remove, reformat, or summarize them away. If a section includes a "
    "'**Sources:**' list, preserve it exactly as given too. After each "
    "section, insert a horizontal divider on its own line using exactly "
    "three dashes (---).\n\n"
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
    "Then a short 2-3 sentence explanation tying it together."
)

DEEP_ADDON = (
    " This is a DEEP RESEARCH request — make the explanation under the "
    "Verdict noticeably more thorough (4-6 sentences), explicitly "
    "referencing specific facts from the research above rather than "
    "generic statements."
)

VALIDATOR_ADDON = (
    "\n\n4. After another divider, add a '**Venture Score:**' header "
    "specifically for validating this as a standalone business idea "
    "(distinct from the general Verdict above). Give exactly these five "
    "ratings, each on its own line:\n"
    "Demand: X/10\n"
    "Competition: X/10\n"
    "Profitability: X/10\n"
    "Scalability: X/10\n"
    "Risk: X/10\n"
    "Then a one-line bolded verdict phrase such as '**Promising opportunity "
    "— proceed with validation.**' or '**High risk — needs significant "
    "de-risking before proceeding.**' matching the actual scores."
)

SCORING_NOTE = (
    " Base all scores on reasoned judgment from the research provided, not "
    "arbitrary numbers — a saturated competitive market or high startup "
    "cost should lower scores accordingly, strong demand and low "
    "competition should raise them."
)


def run(question: str, market: str, competitor: str, financial: str, mode: str = "market", depth: str = "standard") -> str:
    if depth == "quick":
        system_prompt = QUICK_PROMPT
    else:
        system_prompt = BASE_PROMPT + SCORING_NOTE
        if depth == "deep":
            system_prompt += DEEP_ADDON
        if mode == "validate":
            system_prompt += VALIDATOR_ADDON

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
            system_instruction=system_prompt,
        ),
    )
    return response.text
