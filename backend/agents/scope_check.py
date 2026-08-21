from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are a scope classifier for a business research assistant. "
    "Decide if the user's message is a genuine business, market, "
    "startup, or investment-related question (e.g. 'should I open X', "
    "'is Y business idea viable', 'what's the market for Z'). "
    "Reply with EXACTLY one word: YES if it is a business question, "
    "NO if it is not (e.g. general trivia, personal questions, unrelated "
    "topics, greetings)."
)


def is_business_question(question: str) -> bool:
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    answer = response.text.strip().upper()
    return answer.startswith("YES")