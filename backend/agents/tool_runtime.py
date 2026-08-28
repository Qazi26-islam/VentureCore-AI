from __future__ import annotations

from typing import Any

from google.genai import types

from backend.tools import ToolContext, ToolResult, invoke_tool, tool_declarations


MAX_TOOL_ROUNDS = 4


def request_tool_result(
    *,
    client: Any,
    model: str,
    prompt: str,
    system_prompt: str,
    tool_name: str,
    context: ToolContext,
) -> ToolResult:
    declared_tools = tool_declarations([tool_name])
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[declared_tools],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            ),
        ),
    )
    calls = response.function_calls or []
    if not calls or calls[0].name != tool_name:
        return ToolResult.model_validate(
            {
                "ok": False,
                "error": {
                    "code": "required_tool_not_called",
                    "message": "The model did not return the required structured tool call.",
                    "retryable": True,
                },
            }
        )
    return invoke_tool(tool_name, context, dict(calls[0].args or {}))


def run_with_tools(
    *,
    client: Any,
    model: str,
    question: str,
    system_prompt: str,
    tool_names: list[str],
    context: ToolContext,
) -> str:
    declared_tools = tool_declarations(tool_names)
    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part.from_text(text=question)])
    ]
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[declared_tools],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            ),
        ),
    )

    for _ in range(MAX_TOOL_ROUNDS):
        calls = response.function_calls or []
        if not calls:
            return response.text or "I could not produce an answer. Please try again."
        contents.append(response.candidates[0].content)
        response_parts = []
        for call in calls:
            result = invoke_tool(call.name, context, dict(call.args or {}))
            response_parts.append(
                types.Part.from_function_response(
                    name=call.name,
                    response=result.model_dump(mode="json"),
                )
            )
        contents.append(types.Content(role="tool", parts=response_parts))
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[declared_tools],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="AUTO")
                ),
            ),
        )
    return "I could not complete the analysis after using the available business tools."
