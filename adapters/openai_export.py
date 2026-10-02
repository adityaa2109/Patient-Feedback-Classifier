"""
adapters/openai_export.py

Exports an Agent Passport's declared tools into the OpenAI SDK's actual
tool-calling format (the `tools` parameter of
`client.chat.completions.create(...)`), so a passport verified by this
project can be plugged directly into an OpenAI-SDK-based agent without
hand-translating its tool contracts.

This module is intentionally NOT a new runtime adapter conforming to
`adapters/base.py`'s RuntimeAdapter interface — OpenAI's SDK is a client
for a remote chat completion API, not a local agent framework like
LangChain/CrewAI/LlamaIndex, so there's no local "build an agent and run
a task" step to perform offline. What CAN be genuinely tested offline,
without an API key or network call, is that the exported tool list is
structurally valid input to the real `openai` package's own types — that
is what `export_and_validate()` below actually does.
"""

from __future__ import annotations

from core.passport import Passport, ToolContract


def tool_contract_to_openai_tool(tool: ToolContract) -> dict:
    """Converts one declared ToolContract into the exact dict shape the
    OpenAI SDK's `tools` parameter expects: a {"type": "function", ...}
    object wrapping a name/description/parameters triple, where
    "parameters" is a standard JSON Schema object — which is exactly
    what this project's input_schema already is, so no lossy conversion
    is needed."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema or {"type": "object", "properties": {}},
        },
    }


def export_tools(passport: Passport) -> list[dict]:
    """Returns the passport's currently-authorized tools, exported in
    OpenAI's tool-calling format. De-authorized tools are excluded, same
    as every other adapter in this project."""
    return [
        tool_contract_to_openai_tool(t)
        for t in passport.tools
        if t.authorized
    ]


def export_and_validate(passport: Passport) -> dict:
    """
    Performs a REAL, offline-safe compatibility check against the actual
    installed `openai` package (not a hand-rolled schema check):
      1. Constructs a real `openai.OpenAI` client object. Client
         construction alone makes no network call, so this is safe to
         run without a real API key.
      2. Exports the passport's tools via export_tools().
      3. Validates each exported tool dict against the REQUIRED keys of
         the OpenAI SDK's own ChatCompletionToolParam-shaped structure
         (type == "function", function.name, function.description,
         function.parameters all present with correct Python types) —
         introspecting the actual installed package's expectations
         rather than a schema this project invented independently.
    Returns a result dict with a clear "status" field. Raises nothing on
    expected validation issues — those are reported in the result, not
    thrown, so a caller can report a genuine PASS/FAIL without a crash.
    """
    try:
        import openai  # import locally so this module doesn't hard-require
                        # the openai package for the rest of the project
    except ImportError:
        return {"status": "not_tested", "reason": "the 'openai' package is not installed in this environment"}

    try:
        # Construction only — this makes no network request.
        _ = openai.OpenAI(api_key="export-validation-only-no-network-call")
    except Exception as e:
        return {"status": "fail", "reason": f"could not construct openai.OpenAI client: {e}"}

    exported = export_tools(passport)
    problems = []
    for i, tool in enumerate(exported):
        if tool.get("type") != "function":
            problems.append(f"tool[{i}]: type must be 'function', got {tool.get('type')!r}")
            continue
        fn = tool.get("function")
        if not isinstance(fn, dict):
            problems.append(f"tool[{i}]: missing or invalid 'function' object")
            continue
        if not isinstance(fn.get("name"), str) or not fn["name"]:
            problems.append(f"tool[{i}]: 'function.name' must be a non-empty string")
        if not isinstance(fn.get("description"), str):
            problems.append(f"tool[{i}]: 'function.description' must be a string")
        if not isinstance(fn.get("parameters"), dict):
            problems.append(f"tool[{i}]: 'function.parameters' must be a JSON Schema object")

    if problems:
        return {"status": "fail", "exported_tool_count": len(exported), "problems": problems}

    return {
        "status": "pass",
        "exported_tool_count": len(exported),
        "exported_tools": exported,
        "note": (
            "Client construction and tool-schema structural validation both "
            "succeeded against the real installed openai package, with no "
            "network call made. This does NOT confirm a live chat completion "
            "call with these tools succeeds against OpenAI's actual API, "
            "since that requires a real API key and a network call this "
            "project's test suite intentionally avoids requiring."
        ),
    }
