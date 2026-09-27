"""
verification/harness.py

Runs the SAME set of tasks against every registered runtime adapter and
proves the agent behaves equivalently everywhere:
  1. Tool-call equivalence: same tool, same args, same output, across runtimes.
  2. Constraint compliance: none of the manifest's declared constraints
     were violated in any runtime.
  3. Tool contract compliance: every tool actually called was declared
     in the passport (no undeclared capability use).

Produces a JSON "verification certificate" that gets embedded back into
the manifest via Passport.stamp_verification(), which is exactly the
"verification evidence" the challenge submission needs to show.
"""

from __future__ import annotations
import json
import datetime
from dataclasses import asdict

from core.passport import Passport, sha256_hex


class VerificationFailure(Exception):
    pass


class VerificationHarness:
    def __init__(self, passport: Passport, adapters: list):
        self.passport = passport
        self.adapters = adapters  # list of instantiated RuntimeAdapter subclasses

    def run(self, tasks: dict[str, dict]) -> dict:
        """
        tasks: {task_description: deterministic_router_entry}
        Returns a certificate dict; raises VerificationFailure on
        cross-runtime disagreement.
        """
        per_runtime_results = {}
        for adapter in self.adapters:
            adapter.build_agent()
            runtime_results = []
            for task_description in tasks:
                run_result = adapter.run_task(task_description, deterministic_router=tasks)
                runtime_results.append(run_result)
            per_runtime_results[adapter.runtime_name] = runtime_results

        checks = {
            "tool_call_equivalence": self._check_tool_call_equivalence(per_runtime_results),
            "constraint_compliance": self._check_constraint_compliance(per_runtime_results),
            "tool_contract_compliance": self._check_declared_tools_only(per_runtime_results),
        }

        all_passed = all(c["passed"] for c in checks.values())
        if not all_passed:
            raise VerificationFailure(json.dumps(checks, indent=2))

        certificate = {
            "agent_id": self.passport.agent_id,
            "runtimes_tested": list(per_runtime_results.keys()),
            "checks": checks,
            "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        certificate["certificate_hash"] = sha256_hex(json.dumps(certificate, sort_keys=True))
        return certificate

    # ---- individual checks -------------------------------------------------

    def _check_tool_call_equivalence(self, per_runtime_results: dict) -> dict:
        runtimes = list(per_runtime_results.keys())
        if len(runtimes) < 2:
            return {"passed": True, "detail": "single runtime, nothing to compare"}

        baseline_runtime = runtimes[0]
        baseline_traces = [
            [(c.tool_name, c.arguments, c.result) for c in run.tool_calls]
            for run in per_runtime_results[baseline_runtime]
        ]

        mismatches = []
        for runtime in runtimes[1:]:
            traces = [
                [(c.tool_name, c.arguments, c.result) for c in run.tool_calls]
                for run in per_runtime_results[runtime]
            ]
            if traces != baseline_traces:
                mismatches.append(runtime)

        return {
            "passed": len(mismatches) == 0,
            "baseline": baseline_runtime,
            "mismatched_runtimes": mismatches,
        }

    def _check_constraint_compliance(self, per_runtime_results: dict) -> dict:
        violations = {
            runtime: [v for run in runs for v in run.constraint_violations]
            for runtime, runs in per_runtime_results.items()
        }
        any_violation = any(v for v in violations.values())
        return {"passed": not any_violation, "violations": violations}

    def _check_declared_tools_only(self, per_runtime_results: dict) -> dict:
        declared = {t.name for t in self.passport.tools}
        undeclared_calls = {}
        for runtime, runs in per_runtime_results.items():
            bad = [c.tool_name for run in runs for c in run.tool_calls if c.tool_name not in declared]
            if bad:
                undeclared_calls[runtime] = bad
        return {"passed": len(undeclared_calls) == 0, "undeclared_calls": undeclared_calls}
