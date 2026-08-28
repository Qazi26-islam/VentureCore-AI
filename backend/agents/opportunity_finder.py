from google import genai
from google.genai import types

from backend.agents.tool_runtime import request_tool_result
from backend.config import GEMINI_API_KEY
from backend.tools import ToolContext

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are an Opportunity Finder Agent. Given a broad interest like "
    "'I want to start an AI business in Malaysia', use web search to "
    "identify concrete, realistic business opportunities matching "
    "that interest. Return concise researched findings with source links. "
    "Do not calculate or invent attractiveness scores."
)

FORMAT_PROMPT = (
    "Use the opportunity formatting tool to validate the researched candidates. "
    "Give every candidate a stable textual identifier and attach relevant evidence identifiers. "
    "Do not calculate potential; the tool applies that policy."
)


def _extract_source_ids(response) -> list[str]:
    identifiers = []
    try:
        grounding = response.candidates[0].grounding_metadata
        for chunk in grounding.grounding_chunks or []:
            if chunk.web and chunk.web.uri:
                identifiers.append(chunk.web.uri)
    except (AttributeError, IndexError, TypeError):
        return []
    return list(dict.fromkeys(identifiers))


def run(query: str, organization_id: int, user_id: int) -> list[dict]:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=query,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    source_ids = _extract_source_ids(response)
    formatting_input = (
        "Original request:\n"
        + query
        + "\n\nResearched findings:\n"
        + (response.text or "")
        + "\n\nEvidence identifiers:\n"
        + "\n".join(source_ids)
    )
    result = request_tool_result(
        client=client,
        model="gemini-3.5-flash-lite",
        prompt=formatting_input,
        system_prompt=FORMAT_PROMPT,
        tool_name="format_opportunities",
        context=ToolContext(organization_id=organization_id, user_id=user_id),
    )
    if not result.ok:
        return []
    return [
        {
            "opportunity": item["opportunity"],
            "market": item["market"],
            "difficulty": item["difficulty"],
            "potential": item["potential"],
        }
        for item in result.data["items"]
    ]
