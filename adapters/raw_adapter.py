"""
adapters/raw_adapter.py

The simplest possible runtime: no framework at all, just a plain
tool-calling loop against the Anthropic API. This is the "ground truth"
adapter — the other adapters must reproduce this one's behavior.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.base import RuntimeAdapter, RunResult, ToolCallRecord
from core.tool_backends import TOOL_REGISTRY
from core.passport import validate_tool_arguments


class RawAdapter(RuntimeAdapter):
    runtime_name = "raw-sdk"

    def build_agent(self):
        # A raw agent is just: system_prompt + tool registry + a loop.
        # Nothing to "construct" beyond binding tools to the passport's
        # declared AND currently-authorized tool contracts.
        authorized_names = self.passport.authorized_tool_names()
        self._tools = {name: fn for name, fn in TOOL_REGISTRY.items() if name in authorized_names}
        return self._tools

    def run_task(self, task_description: str, deterministic_router: dict | None = None) -> RunResult:
        if not hasattr(self, "_tools"):
            self.build_agent()

        result = RunResult(runtime_name=self.runtime_name)

        route = (deterministic_router or {}).get(task_description)
        if route is None:
            result.final_answer = "No route configured for this task in demo mode."
            return result

        for call_count, step in enumerate(route.get("tool_calls", [])):
            if call_count >= self.passport.max_tool_calls_per_task:
                result.constraint_violations.append("max_tool_calls_per_task exceeded")
                break
            tool_name, args = step["tool"], step["args"]
            if tool_name not in self._tools:
                result.constraint_violations.append(f"tool '{tool_name}' not in declared passport tools")
                continue

            tool_contract = self.passport.get_tool(tool_name)
            if tool_contract is not None:
                arg_problems = validate_tool_arguments(tool_contract, args)
                if arg_problems:
                    result.constraint_violations.append(
                        f"invalid arguments for '{tool_name}': {'; '.join(arg_problems)}"
                    )
                    continue

            output = self._tools[tool_name](**args)
            result.tool_calls.append(ToolCallRecord(tool_name=tool_name, arguments=args, result=output))

        result.final_answer = route.get("final_answer_template", "").format(
            calls=result.tool_calls
        ) if route.get("final_answer_template") else route.get("final_answer", "")

        return result
