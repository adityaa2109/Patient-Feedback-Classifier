"""
core/llm_router.py

Optional LIVE tool-selection layer. When an ANTHROPIC_API_KEY is present
in the environment, this module asks a real Claude model to decide which
of the passport's declared tools to call, and with what arguments, given
a plain-language task description. When no key is present (or the call
fails for any reason), it returns None and the caller falls back to the
existing deterministic task router — so the project stays fully
reproducible offline, with zero required API keys.

This keeps a clean separation: the LLM only ever produces a *decision*
(which tool, which arguments). It never touches tool execution, never
bypasses the passport's declared/authorized/argument-schema checks, and
its output is fed through the exact same verification pipeline as the
deterministic router's output. That's what lets cross-runtime
equivalence checking keep working unmodified: whichever router produced
the route, every adapter still receives the identical route dict.
"""

from __future__ import annotations
import json
import os
import re
import urllib.request
import urllib.error

from core.passport import Passport

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"


def llm_available() -> bool:
    """True only if an API key is actually configured. Callers should
    treat this as a hint, not a guarantee — network errors are still
    handled gracefully by decide_route()."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _build_prompt(passport: Passport, task_description: str) -> str:
    tool_specs = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in passport.tools if t.authorized
    ]
    return (
        f"You are the tool-selection component of the agent '{passport.name}'.\n"
        f"Agent purpose: {passport.purpose}\n"
        f"Agent constraints: {json.dumps(passport.constraints)}\n\n"
        f"Available tools (JSON): {json.dumps(tool_specs)}\n\n"
        f"User task: \"{task_description}\"\n\n"
        "Decide which tool(s) to call and with what arguments to accomplish "
        "this task, respecting the agent's constraints and never inventing "
        "a tool that isn't listed above. Use the tool names EXACTLY as given "
        "above (same case, same spelling). Respond with ONLY a JSON object, "
        "no prose before or after it, no markdown fences, in this exact shape:\n"
        '{"tool_calls": [{"tool": "<tool_name>", "args": {...}}], '
        '"final_answer": "<short natural-language reply to the user>"}\n'
        "If no tool is needed, use an empty tool_calls list."
    )


def _extract_json_object(text: str) -> str | None:
    """
    Real model responses don't always arrive as pure JSON, even when
    explicitly instructed to. This defensively extracts the first
    top-level {...} object from arbitrary surrounding text, handling:
      - markdown fences in any casing (```json, ```JSON, plain ```)
      - leading/trailing prose the model added despite instructions
        (e.g. "Here's my decision:\n\n{...}\n\nLet me know if...")
      - nested braces/brackets and braces that appear inside string
        values, via proper bracket-depth + string-state tracking rather
        than a naive first-'{'-to-last-'}' slice.
    Returns the extracted JSON substring, or None if no balanced object
    could be found at all.
    """
    text = text.strip()
    # Strip common markdown fence wrappers regardless of casing/language tag.
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None  # never balanced — truncated or malformed response


def _normalize_route(route: dict, passport: Passport) -> dict:
    """
    Defensive normalization of a parsed live-model route, applied BEFORE
    verification, never in place of it:
      - Tool-name casing: if the model returns a tool name that matches a
        declared tool case-insensitively but not exactly (e.g.
        "Search_Flights" vs "search_flights"), correct it to the
        passport's exact declared name. A tool name with NO case-
        insensitive match at all is left untouched — that's a genuine
        hallucinated/undeclared tool, and it must still be caught by
        tool_contract_compliance, not silently patched.
      - Numeric string coercion: if a tool's input_schema declares an
        argument as "number"/"integer" and the model returned it as a
        numeric string (e.g. "100" instead of 100 — a common quirk of
        JSON generated by LLMs), convert it to the correct Python type
        so it passes validate_tool_arguments()'s type check. This does
        NOT relax which arguments are allowed or required — only the
        surface string/number representation of an already-expected value.
    This function never adds authorization, never invents a tool, and
    never removes a required field. It only repairs superficial
    formatting noise that live models are known to introduce.
    """
    declared_by_lower = {t.name.lower(): t.name for t in passport.tools}
    tools_by_name = {t.name: t for t in passport.tools}

    for call in route.get("tool_calls", []):
        raw_name = call.get("tool", "")
        exact_match = raw_name in tools_by_name
        if not exact_match:
            corrected = declared_by_lower.get(raw_name.lower())
            if corrected is not None:
                call["tool"] = corrected

        tool_contract = tools_by_name.get(call.get("tool", ""))
        if tool_contract is None:
            continue  # unrecognized tool — leave as-is, verification will reject it

        properties = (tool_contract.input_schema or {}).get("properties", {})
        args = call.get("args", {})
        for key, value in list(args.items()):
            expected_type = properties.get(key, {}).get("type")
            if expected_type in ("number", "integer") and isinstance(value, str):
                try:
                    args[key] = int(value) if expected_type == "integer" else float(value)
                except ValueError:
                    pass  # not actually numeric — leave it, validation will correctly flag it

    return route


def decide_route(passport: Passport, task_description: str, timeout: float = 20.0) -> dict | None:
    """
    Ask a live Claude model to decide the route for one task.
    Returns a route dict shaped exactly like the deterministic TASKS
    entries used elsewhere ({"tool_calls": [...], "final_answer": "..."}),
    or None if the LLM is unavailable / the call fails / the response
    can't be parsed — in every such case the caller should fall back to
    the deterministic router without treating it as a hard error.

    The returned route is defensively normalized (see _normalize_route)
    to correct superficial formatting quirks a live model may introduce,
    but is NOT pre-validated against the passport's tool contracts here —
    that validation still happens later, identically, in
    VerificationHarness / the adapters, exactly as it does for
    deterministic routes.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    payload = {
        "model": os.environ.get("AGENT_PASSPORT_LLM_MODEL", DEFAULT_MODEL),
        "max_tokens": 500,
        "messages": [{"role": "user", "content": _build_prompt(passport, task_description)}],
    }

    request = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None

    try:
        text_blocks = [b["text"] for b in body.get("content", []) if b.get("type") == "text"]
        raw_text = "".join(text_blocks)
        json_text = _extract_json_object(raw_text)
        if json_text is None:
            return None
        route = json.loads(json_text)
    except (KeyError, json.JSONDecodeError, IndexError, TypeError):
        return None

    if not isinstance(route, dict) or "tool_calls" not in route:
        return None

    return _normalize_route(route, passport)


def build_task_set(passport: Passport, task_descriptions: list[str], deterministic_fallback: dict) -> tuple[dict, str]:
    """
    Builds the {task_description: route} dict used by every adapter and
    the verification harness, trying the live LLM first for each task
    and falling back per-task to the deterministic route on any failure.

    Returns (tasks, mode) where mode is "live-llm" if every task was
    successfully routed by the live model, "deterministic" if the model
    was never consulted or never available, or "mixed" if some tasks used
    the live model and others fell back.
    """
    tasks: dict = {}
    used_llm = False
    used_fallback = False

    for description in task_descriptions:
        live_route = decide_route(passport, description) if llm_available() else None
        if live_route is not None:
            tasks[description] = live_route
            used_llm = True
        else:
            tasks[description] = deterministic_fallback[description]
            used_fallback = True

    if used_llm and not used_fallback:
        mode = "live-llm"
    elif used_llm and used_fallback:
        mode = "mixed"
    else:
        mode = "deterministic"

    return tasks, mode
