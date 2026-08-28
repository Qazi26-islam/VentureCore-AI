from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

QUICK_PROMPT = (
    "You are a Competitor Agent doing a QUICK SCAN. Given a business idea, "
    "name the most relevant competitors you know of from general knowledge and "
    "write ONE short paragraph about the competitive landscape. No table, "
    "no sources needed. Be fast and direct."
)

STANDARD_PROMPT = (
    "You are a Competitor Analysis Agent. Given a business idea or question, "
    "use web search to find real, existing competitors relevant to it "
    "(same location or same market). \n\n"
    "First write a short overview of the competitive landscape.\n\n"
    "Then output a markdown table with EXACTLY these columns:\n"
    "| Competitor | Pricing | Strengths | Weaknesses | Positioning |\n"
    "|---|---|---|---|---|\n"
    "One row per competitor. Keep each cell to a short phrase, not a full "
    "sentence. If pricing isn't available, write 'Not disclosed'. Only "
    "include real competitors you found via search — never invent one.\n\n"
    "Do not discuss market trends or finances because other agents handle those. "
    "Do not invent or calculate competitor scores."
)

DEEP_PROMPT = STANDARD_PROMPT + (
    " This is a DEEP RESEARCH request. Include a broader competitor set when "
    "possible and add a short closing paragraph after the table "
    "identifying the clearest competitive gap or whitespace opportunity."
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
    return unique[:8]


def run(question: str, depth: str = "standard") -> str:
    if depth == "quick":
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=question,
            config=types.GenerateContentConfig(system_instruction=QUICK_PROMPT),
        )
        return response.text

    system_prompt = DEEP_PROMPT if depth == "deep" else STANDARD_PROMPT

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=question,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    text = response.text
    sources = _extract_sources(response)
    if sources:
        text += "\n\n**Sources:**\n" + "\n".join(sources)
    return text
