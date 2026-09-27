"""
adapters/crewai_adapter.py

Wraps the Agent Passport as a CrewAI-shaped Agent (role/goal/backstory +
tools). Same fallback philosophy as the LangChain adapter: if `crewai`
isn't installed, `_CrewAgentShim` reproduces its public construction
shape so the adapter logic is honest about what a real CrewAI integration
would look like.
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.base import RuntimeAdapter, RunResult, ToolCallRecord
from core.tool_backends import TOOL_REGISTRY

try:
    from crewai import Agent as CrewAgent  # type: ignore
    CREWAI_AVAILABLE = True
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
    this mirrors that minimal shape."""
    def __init__(self, name, description, func):
        self.name = name
        self.description = description
        self.func = func

    def run(self, **kwargs):
        return self.func(**kwargs)


class CrewAIAdapter(RuntimeAdapter):
    runtime_name = "crewai" if CREWAI_AVAILABLE else "crewai-shim"

    def build_agent(self):
        declared_names = {t.name for t in self.passport.tools}
        wrapped_tools = [
            _CrewToolWrapper(t.name, t.description, TOOL_REGISTRY[t.name])
            for t in self.passport.tools if t.name in declared_names
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
            output = tool.run(**args)
            result.tool_calls.append(ToolCallRecord(tool_name=tool_name, arguments=args, result=output))

        result.final_answer = route.get("final_answer", "")
        return result
