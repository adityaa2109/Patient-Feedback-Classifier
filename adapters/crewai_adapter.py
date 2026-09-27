"""
adapters/crewai_adapter.py

Wraps the Agent Passport as a CrewAI-shaped Agent (role/goal/backstory +
tools). When the real `crewai` package is installed, tools are built with
CrewAI's own `crewai.tools.tool()` factory (its `Agent` is a Pydantic
model that requires genuine `BaseTool` instances, not just objects with a
matching `.name`/.description shape). If `crewai` isn't installed at all,
`_CrewAgentShim`/`_CrewToolWrapper` reproduce its public construction
shape so the adapter logic is honest about what a real integration looks
like and the demo still runs end to end.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.base import RuntimeAdapter, RunResult, ToolCallRecord
from core.tool_backends import TOOL_REGISTRY
from core.passport import validate_tool_arguments

try:
    from crewai import Agent as CrewAgent  # type: ignore
    from crewai.tools import tool as crewai_tool_factory  # type: ignore
    CREWAI_AVAILABLE = True

    def _make_crew_tool(name: str, description: str, func):
        """Build a genuine crewai.tools.Tool (a real BaseTool subclass
        instance) from a plain function, so it validates against
        Agent's pydantic tools field. crewai's factory reads the
        function's docstring as the tool description and requires one
        to be present, so we set it dynamically from the passport's
        declared tool description rather than hand-writing one per tool."""
        def wrapper(**kwargs):
            return func(**kwargs)
        wrapper.__name__ = name
        wrapper.__doc__ = description or "No description provided."
        return crewai_tool_factory(name)(wrapper)

except Exception:
    CREWAI_AVAILABLE = False

    class CrewAgent:
        """Structural shim matching crewai.Agent's public construction shape."""
        def __init__(self, role: str, goal: str, backstory: str, tools: list, verbose: bool = False):
            self.role = role
            self.goal = goal
            self.backstory = backstory
            self.tools = tools

    class _CrewToolWrapper:
        """CrewAI tools are typically callables with a .name/.description;
        this mirrors that minimal shape when the real package is absent."""
        def __init__(self, name, description, func):
            self.name = name
            self.description = description
            self.func = func

        def run(self, **kwargs):
            return self.func(**kwargs)

    def _make_crew_tool(name: str, description: str, func):
        return _CrewToolWrapper(name, description, func)


class CrewAIAdapter(RuntimeAdapter):
    runtime_name = "crewai" if CREWAI_AVAILABLE else "crewai-shim"

    def build_agent(self):
        authorized_names = self.passport.authorized_tool_names()
        wrapped_tools = [
            _make_crew_tool(t.name, t.description, TOOL_REGISTRY[t.name])
            for t in self.passport.tools if t.name in authorized_names
        ]
        self._tools_by_name = {t.name: t for t in wrapped_tools}
        self._crew_agent = CrewAgent(
            role=self.passport.name,
            goal=self.passport.purpose,
            backstory=self.passport.system_prompt,
            tools=wrapped_tools,
            verbose=False,
        )
        return self._crew_agent

    def run_task(self, task_description: str, deterministic_router: dict | None = None) -> RunResult:
        if not hasattr(self, "_crew_agent"):
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
            tool = self._tools_by_name.get(tool_name)
            if tool is None:
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

            output = tool.run(**args)
            result.tool_calls.append(ToolCallRecord(tool_name=tool_name, arguments=args, result=output))

        result.final_answer = route.get("final_answer", "")
        return result
