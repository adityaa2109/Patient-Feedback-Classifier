"""
adapters/llamaindex_adapter.py

Fourth runtime adapter — added as live proof of the portability claim:
a new framework can be supported by writing exactly one adapter class,
with ZERO changes to core/, spec/, or verification/.

Wraps the Agent Passport's tools as LlamaIndex `FunctionTool` objects.
If the real `llama_index.core` package isn't installed, falls back to a
structurally identical shim (same pattern as the LangChain/CrewAI
adapters), so the demo still runs end to end without the dependency.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.base import RuntimeAdapter, RunResult, ToolCallRecord
from core.tool_backends import TOOL_REGISTRY
from core.passport import validate_tool_arguments

try:
    from llama_index.core.tools import FunctionTool  # type: ignore
    LLAMAINDEX_AVAILABLE = True

    def _make_li_tool(name: str, description: str, func):
        return FunctionTool.from_defaults(fn=func, name=name, description=description)

    def _run_li_tool(li_tool, **kwargs):
        return li_tool.call(**kwargs).raw_output

except Exception:
    LLAMAINDEX_AVAILABLE = False

    class _LlamaIndexToolShim:
        """Structural shim matching llama_index.core.tools.FunctionTool's
        public shape (name/description + a callable), used only when the
        real package isn't installed."""
        def __init__(self, name, description, func):
            self.name = name
            self.description = description
            self.func = func

        def call(self, **kwargs):
            return self

        def run(self, **kwargs):
            return self.func(**kwargs)

    def _make_li_tool(name, description, func):
        return _LlamaIndexToolShim(name, description, func)

    def _run_li_tool(li_tool, **kwargs):
        return li_tool.run(**kwargs)


class LlamaIndexAdapter(RuntimeAdapter):
    runtime_name = "llamaindex" if LLAMAINDEX_AVAILABLE else "llamaindex-shim"

    def build_agent(self):
        authorized_names = self.passport.authorized_tool_names()
        self._li_tools = {}
        for tool_contract in self.passport.tools:
            if tool_contract.name not in authorized_names:
                continue
            backend_fn = TOOL_REGISTRY[tool_contract.name]
            self._li_tools[tool_contract.name] = _make_li_tool(
                tool_contract.name, tool_contract.description, backend_fn
            )
        return self._li_tools

    def run_task(self, task_description: str, deterministic_router: dict | None = None) -> RunResult:
        if not hasattr(self, "_li_tools"):
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
            li_tool = self._li_tools.get(tool_name)
            if li_tool is None:
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

            output = _run_li_tool(li_tool, **args)
            result.tool_calls.append(ToolCallRecord(tool_name=tool_name, arguments=args, result=output))

        result.final_answer = route.get("final_answer", "")
        return result
