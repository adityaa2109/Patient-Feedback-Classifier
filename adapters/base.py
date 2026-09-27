"""
adapters/base.py

Every framework adapter must implement RuntimeAdapter. The verification
harness only ever talks to this interface — it never knows or cares
whether it's driving LangChain, CrewAI, or a raw SDK loop underneath.

This is the actual "portability" contract of the whole project: a new
framework is supported by writing ONE adapter class, not by touching the
agent definition, the tools, or the harness.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCallRecord:
    tool_name: str
    arguments: dict
    result: dict


@dataclass
class RunResult:
    runtime_name: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    final_answer: str = ""
    constraint_violations: list[str] = field(default_factory=list)


class RuntimeAdapter(ABC):
    """Wraps one framework so it can load & execute an Agent Passport."""

    runtime_name: str = "base"

    def __init__(self, passport):
        self.passport = passport

    @abstractmethod
    def build_agent(self):
        """Construct the framework-native agent object from self.passport."""
        raise NotImplementedError

    @abstractmethod
    def run_task(self, task_description: str, deterministic_router: dict | None = None) -> RunResult:
        """
        Execute a task and return a normalized RunResult so the harness
        can diff behavior across runtimes.

        `deterministic_router` is an optional dict used ONLY by the demo
        to force the same tool-call decision across frameworks without
        needing a live LLM call in every adapter (keeps the challenge
        submission runnable offline/without API keys, while still proving
        the architecture). Real deployments would let each framework's
        own LLM reasoning decide the call.
        """
        raise NotImplementedError
