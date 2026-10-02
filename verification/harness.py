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

EQUIVALENCE MODES
-----------------
run()'s default equivalence_mode="exact" requires tool-call traces to be
byte-identical across runtimes (tool name + arguments + result, in
order). This is the right bar for deterministic replay, where every
runtime is fed the exact same pre-decided route and any divergence is a
real bug.

It is the WRONG bar once tool calls come from a live model
(core/llm_router.py): two independent calls to the same model, or two
different models, may reasonably produce different-but-equally-valid
arguments for the same task (a restock quantity of 90 vs. 100, both
within budget). Exact-match checking would flag that as a failure even
though both calls are individually correct.

equivalence_mode="semantic" replaces the byte-exact trace comparison
with a rule-based check (see _check_semantic_tool_call_equivalence)
that two runtimes are equivalent if they called the SAME TOOLS IN THE
SAME ORDER, and every individual call's arguments independently pass
schema validation (including numeric minimum/maximum bounds) and
authorization — without requiring the argument VALUES to match. This is
deliberately NOT free-text similarity scoring or an LLM-graded
comparison; it stays fully deterministic and rule-based, consistent
with the project's "no required API keys for core verification"
guarantee. Default behavior (equivalence_mode="exact") is completely
unchanged by this feature's existence.
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

    def run(self, tasks: dict[str, dict], equivalence_mode: str = "exact",
            per_runtime_tasks: dict[str, dict] | None = None) -> dict:
        """
        tasks: {task_description: deterministic_router_entry} — the canonical
            set of task descriptions every runtime is asked about, and the
            routing every runtime uses UNLESS overridden per-runtime below.
        equivalence_mode: "exact" (default, unchanged original behavior) or
            "semantic" (see module docstring). Only changes which check
            populates the "tool_call_equivalence" entry in the returned
            certificate's checks dict — every other check is identical in
            both modes.
        per_runtime_tasks: optional {runtime_name: tasks_dict} override,
            letting individual runtimes receive DIFFERENT routing for the
            same task descriptions — e.g. to simulate two live-LLM calls
            that reasonably chose different-but-valid arguments. Omitted
            (None, the default) means every runtime uses `tasks` exactly
            as before; this parameter changes nothing for any existing
            caller that doesn't pass it.
        Returns a certificate dict; raises VerificationFailure on
        cross-runtime disagreement or contract violation.
        """
        if equivalence_mode not in ("exact", "semantic"):
            raise ValueError(f"equivalence_mode must be 'exact' or 'semantic', got {equivalence_mode!r}")

        per_runtime_results = {}
        for adapter in self.adapters:
            adapter.build_agent()
            effective_tasks = (per_runtime_tasks or {}).get(adapter.runtime_name, tasks)
            runtime_results = []
            for task_description in tasks:
                run_result = adapter.run_task(task_description, deterministic_router=effective_tasks)
                runtime_results.append(run_result)
            per_runtime_results[adapter.runtime_name] = runtime_results

        equivalence_check = (
            self._check_tool_call_equivalence(per_runtime_results)
            if equivalence_mode == "exact"
            else self._check_semantic_tool_call_equivalence(per_runtime_results)
        )

        checks = {
            "tool_call_equivalence": equivalence_check,
            "constraint_compliance": self._check_constraint_compliance(per_runtime_results),
            "tool_contract_compliance": self._check_declared_and_authorized_tools_only(per_runtime_results),
            "tool_argument_validity": self._check_tool_argument_validity(per_runtime_results),
        }

        all_passed = all(c["passed"] for c in checks.values())

        structured_result = self._build_structured_result(checks)

        if not all_passed:
            raise VerificationFailure(json.dumps({
                "equivalence_mode": equivalence_mode,
                "checks": checks,
                "structured_result": structured_result.to_dict(),
            }, indent=2))

        certificate = {
            "agent_id": self.passport.agent_id,
            "runtimes_tested": list(per_runtime_results.keys()),
            "equivalence_mode": equivalence_mode,
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

    def _check_semantic_tool_call_equivalence(self, per_runtime_results: dict) -> dict:
        """
        Rule-based alternative to byte-exact trace comparison (see module
        docstring for the full rationale). Two runtimes are considered
        semantically equivalent for a set of tasks if:

          (a) They called the SAME TOOLS IN THE SAME ORDER. Order is
              compared as the flat sequence of tool NAMES only (arguments
              excluded), which is what actually matters for a declared
              ordering constraint such as Inventory Ops Agent's "must
              check_stock_level before place_restock_order" — the agent
              followed the right plan, regardless of what quantity it
              decided on.
          (b) Every individual call's arguments independently pass
              validate_tool_arguments() — the SAME schema/required-field/
              type/numeric-bounds check used by tool_argument_validity —
              so a call is never accepted just because another runtime's
              call "looks similar"; each one must stand on its own.
          (c) Every called tool is declared and currently authorized in
              the passport (mirrors tool_contract_compliance, checked
              here too so this method is self-contained and testable in
              isolation from the other checks).

        This is deliberately NOT free-text similarity scoring, NOT an
        LLM-graded comparison, and does NOT compare argument VALUES
        across runtimes at all — two different-but-individually-valid
        values are exactly the case this mode exists to accept. A tool
        call using a different tool, skipping a required step, or
        carrying invalid/unauthorized arguments still fails, by design.
        """
        runtimes = list(per_runtime_results.keys())
        if len(runtimes) < 2:
            return {"passed": True, "mode": "semantic", "detail": "single runtime, nothing to compare"}

        def tool_sequence(results_list):
            return [c.tool_name for run in results_list for c in run.tool_calls]

        def per_call_problems(results_list):
            problems = []
            for run in results_list:
                for c in run.tool_calls:
                    tool_contract = self.passport.get_tool(c.tool_name)
                    if tool_contract is None:
                        problems.append(f"call to undeclared tool '{c.tool_name}'")
                        continue
                    if not tool_contract.authorized:
                        problems.append(f"call to declared-but-unauthorized tool '{c.tool_name}'")
                    issues = validate_tool_arguments(tool_contract, c.arguments)
                    if issues:
                        problems.append(f"'{c.tool_name}' arguments invalid: {'; '.join(issues)}")
            return problems

        baseline_runtime = runtimes[0]
        baseline_sequence = tool_sequence(per_runtime_results[baseline_runtime])

        problems_by_runtime: dict[str, list[str]] = {}

        baseline_own_problems = per_call_problems(per_runtime_results[baseline_runtime])
        if baseline_own_problems:
            problems_by_runtime[baseline_runtime] = baseline_own_problems

        for runtime in runtimes[1:]:
            runtime_problems = []
            sequence = tool_sequence(per_runtime_results[runtime])
            if sequence != baseline_sequence:
                runtime_problems.append(
                    f"tool-call sequence differs from baseline: expected {baseline_sequence}, got {sequence}"
                )
            runtime_problems.extend(per_call_problems(per_runtime_results[runtime]))
            if runtime_problems:
                problems_by_runtime[runtime] = runtime_problems

        return {
            "passed": len(problems_by_runtime) == 0,
            "mode": "semantic",
            "baseline_tool_sequence": baseline_sequence,
            "problems_by_runtime": problems_by_runtime,
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

