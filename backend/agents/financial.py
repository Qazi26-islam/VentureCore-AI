from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are a Financial Analysis Agent. Given a business idea or question, "
    "give a rough, honest estimate of startup costs, ongoing monthly costs, "
    "typical margins for this type of business, and a realistic view of "
    "how long profitability might take. Be clear this is a rough estimate, "
    "not a formal projection. Be concise: 3-5 short paragraphs. Do not "
    "discuss market trends or competitors — other agents handle those."
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