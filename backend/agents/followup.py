from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are a helpful business research assistant. You previously "
    "produced a research report answering the user's business question. "
    "Now the user has follow-up questions. Answer them using the context "
    "of the original question and report. Be concise and direct. If you "
    "reference a section from the report, format it in bold followed by a "
    "colon (e.g. '**Market Research:**'). If the follow-up asks about "
    "something not covered in the report, use your general business "
    "knowledge to help, but be clear when you're going beyond the "
    "original report. Never calculate a figure or introduce a new numeric "
    "claim. You may repeat a quantitative fact only when it already appears "
    "in the supplied report with its source."
)


def run(question: str, report: str, history: list, new_message: str) -> str:
    context = (
        f"Original business question: {question}\n\n"
        f"Research report:\n{report}\n\n"
        f"---\nNow continue the conversation naturally based on the above."
    )

    contents = [
        {"role": "user", "parts": [{"text": context}]},
        {"role": "model", "parts": [{"text": "Understood. I'm ready to answer follow-up questions about this report."}]},
    ]

    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    contents.append({"role": "user", "parts": [{"text": new_message}]})

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    return response.text
