from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

QUICK_PROMPT = (
    "You are a Market Research Agent doing a QUICK SCAN. Given a business "
    "idea, write ONE short paragraph (3-4 sentences) covering the most "
    "important market demand signal and one key trend. Use your general "
    "knowledge, be direct and fast. No sources needed."
)

STANDARD_PROMPT = (
    "You are a Market Research Agent. Given a business idea or question, "
    "research current market demand, relevant trends, market size, and "
    "target customer profile using web search. Be concise and factual. "
    "Write 3-5 short paragraphs. Do not discuss competitors or finances — "
    "other agents handle those. Focus only on market demand, size, and trends."
)

DEEP_PROMPT = STANDARD_PROMPT + (
    " This is a DEEP RESEARCH request — be more thorough than usual. "
    "Cover market size (TAM/SAM/SOM if data allows), specific growth "
    "rates with numbers where you can find them, detailed customer "
    "segments, and 2-3 notable industry trends with brief explanations. "
    "Aim for 5-7 well-developed paragraphs."
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
