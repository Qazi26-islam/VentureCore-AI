import re
from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are an Opportunity Finder Agent. Given a broad interest like "
    "'I want to start an AI business in Malaysia', use web search to "
    "identify 4-6 concrete, realistic business opportunities matching "
    "that interest. Output ONLY a markdown table, nothing else — no "
    "intro, no explanation. Use EXACTLY these columns:\n"
    "| Opportunity | Market | Difficulty | Potential |\n"
    "|---|---|---|---|\n"
    "Opportunity: a short, specific business idea name (a few words). "
    "Market: the relevant industry/sector. Difficulty: exactly Low, "
    "Medium, or High. Potential: a number from 0-100 reflecting overall "
    "attractiveness, written as just the number (no /100, no extra text)."
)


def _parse_table(text: str) -> list[dict]:
    rows = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        # Skip separator rows like |---|---|---|---|
        if re.match(r"^\|[\s\-:|]+\|$", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 4:
            continue
        if cells[0].lower() == "opportunity":
            continue
        potential_match = re.search(r"\d+", cells[3])
        potential = int(potential_match.group()) if potential_match else 0
        rows.append({
            "opportunity": cells[0],
            "market": cells[1],
            "difficulty": cells[2],
            "potential": potential,
        })
    return rows


def run(query: str) -> list[dict]:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=query,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    return _parse_table(response.text)
