from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are a Competitor Analysis Agent. Given a business idea or question, "
    "use web search to find 3-8 real, existing competitors relevant to it "
    "(same location or same market). \n\n"
    "First write 1-2 short paragraphs of overall competitive landscape context.\n\n"
    "Then output a markdown table with EXACTLY these columns:\n"
    "| Competitor | Pricing | Strengths | Weaknesses | Positioning |\n"
    "|---|---|---|---|---|\n"
    "One row per competitor. Keep each cell to a short phrase, not a full "
    "sentence. If pricing isn't available, write 'Not disclosed'. Only "
    "include real competitors you found via search — never invent one.\n\n"
    "Do not discuss market trends or finances — other agents handle those."
)


def _extract_sources(response) -> list[str]:
    sources = []
    try:
        candidate = response.candidates[0]
        grounding = getattr(candidate, "grounding_metadata", None)
        if grounding and grounding.grounding_chunks:
            for chunk in grounding.grounding_chunks:
                if chunk.web:
                    title = chunk.web.title or chunk.web.uri
                    uri = chunk.web.uri
                    if uri:
                        sources.append(f"- [{title}]({uri})")
    except Exception:
        pass
    seen = set()
    unique = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique[:6]


def run(question: str) -> str:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    text = response.text
    sources = _extract_sources(response)
    if sources:
        text += "\n\n**Sources:**\n" + "\n".join(sources)
    return text
