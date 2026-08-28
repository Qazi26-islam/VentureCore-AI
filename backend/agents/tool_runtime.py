from __future__ import annotations

import time
from typing import Any

from google.genai import types

from backend.tools import ToolContext, ToolResult, invoke_tool, tool_declarations
from backend.observability import instrument_client, record_tool_call, run_traced_agent


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
    client = instrument_client(client)
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
    arguments = dict(calls[0].args or {})
    started = time.perf_counter()
    result = invoke_tool(tool_name, context, arguments)
    record_tool_call(tool_name, arguments, result, int((time.perf_counter() - started) * 1000))
    return result


def run_with_tools(
    *,
    client: Any,
    model: str,
    question: str,
    system_prompt: str,
    tool_names: list[str],
    context: ToolContext,
) -> str:
    def execute() -> str:
        traced_client = instrument_client(client)
        return _run_with_tools(
            client=traced_client,
            model=model,
            question=question,
            system_prompt=system_prompt,
            tool_names=tool_names,
            context=context,
        )

    agent_by_tool = {
        "get_inventory_snapshot": "Inventory Agent",
        "get_sales_snapshot": "Sales & CRM Agent",
        "get_finance_snapshot": "Finance & Cash Flow Agent",
    }
    agent_name = next((agent_by_tool[name] for name in tool_names if name in agent_by_tool), "Business Agent")
    return run_traced_agent(agent_name, context.organization_id, question, execute)


def _run_with_tools(
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
            arguments = dict(call.args or {})
            started = time.perf_counter()
            result = invoke_tool(call.name, context, arguments)
            record_tool_call(call.name, arguments, result, int((time.perf_counter() - started) * 1000))
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
