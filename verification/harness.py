"""
verification/harness.py

Runs the SAME set of tasks against every registered runtime adapter and
proves the agent behaves equivalently everywhere:
  1. Tool-call equivalence: same tool, same args, same output, across runtimes.
  2. Constraint compliance: none of the manifest's declared constraints
     were violated in any runtime.
  3. Tool contract compliance: every tool actually called was declared
     AND currently authorized in the passport (no undeclared or
     de-authorized capability use).
  4. Tool argument validity: every call's arguments structurally match
     the tool's declared input_schema.

Produces a JSON "verification certificate" that gets embedded back into
the manifest via Passport.stamp_verification(), which is exactly the
"verification evidence" the challenge submission needs to show, plus a
structured VerificationResult answering the two questions the challenge
brief poses directly: "is this still the same agent that was signed?"
and "is this runtime implementation compliant with the signed passport?"
"""

from __future__ import annotations
import json
import datetime

from core.passport import Passport, VerificationResult, sha256_hex, validate_tool_arguments


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
        cross-runtime disagreement or contract violation.
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
            "tool_contract_compliance": self._check_declared_and_authorized_tools_only(per_runtime_results),
            "tool_argument_validity": self._check_tool_argument_validity(per_runtime_results),
        }

        all_passed = all(c["passed"] for c in checks.values())

        structured_result = self._build_structured_result(checks)

        if not all_passed:
            raise VerificationFailure(json.dumps({
                "checks": checks,
                "structured_result": structured_result.to_dict(),
            }, indent=2))

        certificate = {
            "agent_id": self.passport.agent_id,
            "runtimes_tested": list(per_runtime_results.keys()),
            "checks": checks,
            "structured_result": structured_result.to_dict(),
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        certificate["certificate_hash"] = sha256_hex(json.dumps(certificate, sort_keys=True))
        return certificate

    # ---- structured result --------------------------------------------------

    def _build_structured_result(self, checks: dict) -> VerificationResult:
        """Combines the passport's own static integrity check
        (signature/behavior-contract/identity) with this run's dynamic
        runtime-compliance checks into one judge-readable verdict."""
        static_result = self.passport.verify_full()

        violations = list(static_result.violations)
        runtime_compliance = True
        for check_name, check_result in checks.items():
            if not check_result["passed"]:
                runtime_compliance = False
                violations.append(f"{check_name} failed: {json.dumps({k: v for k, v in check_result.items() if k != 'passed'})}")

        return VerificationResult(
            passport_valid=static_result.passport_valid,
            identity_valid=static_result.identity_valid,
            behavior_contract_valid=static_result.behavior_contract_valid,
            tool_contract_valid=checks["tool_contract_compliance"]["passed"] and static_result.tool_contract_valid,
            runtime_compliance=runtime_compliance,
            violations=violations,
        )

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

    def _check_declared_and_authorized_tools_only(self, per_runtime_results: dict) -> dict:
        """A tool call is only compliant if it was BOTH declared in the
        passport AND currently authorized=true. A de-authorized tool is
        treated the same as an undeclared one — this is what lets an
        operator revoke a capability without rewriting the manifest."""
        authorized = self.passport.authorized_tool_names()
        declared = {t.name for t in self.passport.tools}

        problem_calls = {}
        for runtime, runs in per_runtime_results.items():
            bad = []
            for run in runs:
                for c in run.tool_calls:
                    if c.tool_name not in declared:
                        bad.append({"tool": c.tool_name, "reason": "undeclared"})
                    elif c.tool_name not in authorized:
                        bad.append({"tool": c.tool_name, "reason": "declared but not authorized"})
            if bad:
                problem_calls[runtime] = bad

        return {"passed": len(problem_calls) == 0, "non_compliant_calls": problem_calls}

    def _check_tool_argument_validity(self, per_runtime_results: dict) -> dict:
        """Structurally validates every observed tool call's arguments
        against that tool's declared input_schema (required fields present,
        basic type compatibility)."""
        problems = {}
        for runtime, runs in per_runtime_results.items():
            runtime_problems = []
            for run in runs:
                for c in run.tool_calls:
                    tool_contract = self.passport.get_tool(c.tool_name)
                    if tool_contract is None:
                        continue  # already flagged by tool_contract_compliance
                    issues = validate_tool_arguments(tool_contract, c.arguments)
                    if issues:
                        runtime_problems.append({"tool": c.tool_name, "issues": issues})
            if runtime_problems:
                problems[runtime] = runtime_problems

        return {"passed": len(problems) == 0, "argument_problems": problems}

