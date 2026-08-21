from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are a Market Research Agent. Given a business idea or question, "
    "research current market demand, relevant trends, and target customer "
    "profile using web search. Be concise and factual. Write 3-5 short "
    "paragraphs. Do not discuss competitors or finances — other agents "
    "handle those. Focus only on market demand and trends."
)


def run(question: str) -> str:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    return response.text