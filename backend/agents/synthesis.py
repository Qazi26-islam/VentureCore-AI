from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are a Synthesis Agent. You will be given research from three "
    "specialist agents: Market Research, Competitor Analysis, and Financial "
    "Analysis, all about the same business question. Combine them into one "
    "clear, well-organized report. For each section, start with the agent's "
    "name in bold followed by a colon as a mini-header (e.g. "
    "'**Market Research:**' on its own line), followed by a blank line, "
    "then that agent's findings written in clear paragraphs. Do not use "
    "markdown ### headers — use bold text only, styled like a label. "
    "IMPORTANT: after each section's content, insert a horizontal divider "
    "on its own line using exactly three dashes (---) before starting the "
    "next section, so there is clear visual separation between Market "
    "Research, Competitor Analysis, Financial Analysis, and the final "
    "Bottom Line section. After all three sections, add a divider and then "
    "a bolded '**Bottom Line:**' section with a short verdict on whether "
    "this looks promising and why. Do not add new facts — only organize "
    "and summarize what's given."
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
