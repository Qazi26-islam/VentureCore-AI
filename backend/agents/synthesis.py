from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

QUICK_PROMPT = (
    "You are a Synthesis Agent. You will be given quick-scan research from "
    "three specialist agents about the same business question. Combine "
    "them into a short report: for each of the three sections, a bold "
    "label on its own line (e.g. '**Market Research:**') followed by "
    "their finding. After the sections, add a '**Verdict:**' header with "
    "GO, CAUTION, or NO-GO in bold and a short "
    "sentence explaining why. Keep the whole thing short — this is a "
    "quick scan, not a deep report."
)

BASE_PROMPT = (
    "You are a Synthesis Agent acting like a business consultant. You will "
    "be given research from three specialist agents: Market Research, "
    "Competitor Analysis, and Financial Analysis, all about the same "
    "business question. Combine them into one clear, well-organized report "
    "following this EXACT structure:\n\n"
    "For each agent section, start with the agent's name "
    "in bold followed by a colon on its own line (e.g. '**Market Research:**'), "
    "a blank line, then that agent's findings in clear paragraphs. If the "
    "Competitor Analysis section includes a markdown competitor comparison "
    "table, or the Financial Analysis section includes a scenario table "
    "(Worst/Base/Best case), preserve those tables EXACTLY as given — do "
    "not remove, reformat, or summarize them away. If a section includes a "
    "'**Sources:**' list, preserve it exactly as given too. After each "
    "section, place a horizontal divider on its own line using exactly "
    "three dashes (---).\n\n"
    "After the agent sections and a divider, add a '**SWOT Analysis:**' "
    "header followed by a markdown table with exactly this structure:\n"
    "| Strengths | Weaknesses |\n|---|---|\n| ... | ... |\n\n"
    "| Opportunities | Threats |\n|---|---|\n| ... | ... |\n\n"
    "Keep each cell to short bullet-style phrases separated by <br>. "
    "Base this only on the research given, do not invent new facts.\n\n"
    "After another divider, add a '**Verdict:**' header. On the first "
    "line, state one of exactly: GO, CAUTION, or NO-GO in bold, followed by "
    "a concise reason. State overall risk qualitatively, then explain how the evidence ties together. "
    "Do not calculate or invent scores."
)

DEEP_ADDON = (
    " This is a DEEP RESEARCH request — make the explanation under the "
    "Verdict noticeably more thorough, explicitly "
    "referencing specific facts from the research above rather than "
    "generic statements."
)

VALIDATOR_ADDON = (
    "\n\nAfter another divider, add a '**Venture Assessment:**' header "
    "specifically for validating this as a standalone business idea. "
    "Discuss demand, competition, profitability, scalability, and risk qualitatively. "
    "Then add a bolded verdict phrase such as '**Promising opportunity "
    "— proceed with validation.**' or '**High risk — needs significant "
    "de-risking before proceeding.**' matching the qualitative evidence."
)

def run(question: str, market: str, competitor: str, financial: str, mode: str = "market", depth: str = "standard") -> str:
    if depth == "quick":
        system_prompt = QUICK_PROMPT
    else:
        system_prompt = BASE_PROMPT
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
