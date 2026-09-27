"""
run_demo_agent2.py

Runs the exact same CREATE -> SIGN -> VERIFY -> CROSS-FRAMEWORK -> VERIFY
pipeline as run_demo.py, but against a structurally different agent:
Inventory Ops Agent (examples/inventory_ops.manifest.json).

Why this exists: run_demo.py alone only proves the passport schema and
verification pipeline work for ONE agent shape (2 read-only tools, simple
constraints). This script proves they generalize to a different shape:
  - 3 tools instead of 2
  - one read tool, one external-side-effect tool, one write-side-effect tool
  - a numeric business constraint (max_order_value_usd) baked into a tool's
    own input, in addition to the manifest-level declared constraints
  - a task set that includes both a normal restock and a deliberately
    over-budget restock, so the demo also shows a business-logic edge case
    flowing cleanly through unmodified verification code

No changes were made to core/, spec/, or verification/ to support this
second agent — same Passport class, same adapters, same harness.

Usage:
    python run_demo_agent2.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from core.passport import Passport, sign_manifest
from core.llm_router import build_task_set, llm_available
from adapters.raw_adapter import RawAdapter
from adapters.langchain_adapter import LangChainAdapter
from adapters.crewai_adapter import CrewAIAdapter
from adapters.llamaindex_adapter import LlamaIndexAdapter
from verification.harness import VerificationHarness, VerificationFailure

HERE = os.path.dirname(__file__)
UNSIGNED_PATH = os.path.join(HERE, "examples", "inventory_ops.manifest.json")
SIGNED_PATH = os.path.join(HERE, "examples", "inventory_ops.SIGNED.json")
VERIFIED_PATH = os.path.join(HERE, "examples", "inventory_ops.VERIFIED.json")

TASKS = {
    "Restock SKU-WIDGET-001, we're low": {
        "tool_calls": [
            {"tool": "check_stock_level", "args": {"item_sku": "SKU-WIDGET-001"}},
            {"tool": "notify_ops_channel", "args": {"message": "Restocking SKU-WIDGET-001: current stock 4, below threshold 20."}},
            {"tool": "place_restock_order", "args": {"item_sku": "SKU-WIDGET-001", "quantity": 100, "max_order_value_usd": 2000.0}},
        ],
        "final_answer": "Restocked SKU-WIDGET-001: 100 units for $1250.00.",
    },
    "Try to restock SKU-GIZMO-003 but keep it under budget": {
        "tool_calls": [
            {"tool": "check_stock_level", "args": {"item_sku": "SKU-GIZMO-003"}},
            {"tool": "notify_ops_channel", "args": {"message": "Restocking SKU-GIZMO-003: current stock 2, below threshold 10."}},
            # Deliberately over budget: 5 units * $899.00 = $4495.00 > $1000 cap.
            # The tool itself reports a business rejection (status: rejected_over_budget)
            # while still being a fully valid, schema-compliant, authorized call —
            # this is intentionally a business-logic edge case, not a contract violation.
            {"tool": "place_restock_order", "args": {"item_sku": "SKU-GIZMO-003", "quantity": 5, "max_order_value_usd": 1000.0}},
        ],
        "final_answer": "Attempted to restock SKU-GIZMO-003 but the order exceeded the budget cap and was not placed.",
    },
}


def step(n, title: str):
    print("\n" + "=" * 72)
    print(f"STEP {n} - {title}")
    print("=" * 72)


def status(ok: bool, label: str) -> bool:
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {label}")
    return ok


def main():
    overall_ok = True

    step(1, "CREATE PASSPORT (Inventory Ops Agent)")
    with open(UNSIGNED_PATH) as f:
        unsigned = json.load(f)
    print(f"  Loaded unsigned manifest for agent '{unsigned['identity']['name']}'")
    print(f"  Declared tools: {[t['name'] for t in unsigned['tools']]} "
          f"(side effects: {[t['side_effects'] for t in unsigned['tools']]})")
    print(f"  Declared constraints: {len(unsigned['behavior_contract']['constraints'])}")

    step(2, "SIGN PASSPORT")
    signed = sign_manifest(unsigned)
    with open(SIGNED_PATH, "w") as f:
        json.dump(signed, f, indent=2)
    print(f"  Written to {SIGNED_PATH}")

    step(3, "LOAD AND VERIFY")
    passport = Passport.load(signed, strict=True)
    static_result = passport.verify_full()
    overall_ok &= status(static_result.overall, "static passport integrity (signature/identity/contract)")
    print(f"  Loaded passport for agent_id={passport.agent_id!r}, {len(passport.tools)} tool(s), "
          f"{len(passport.constraints)} constraint(s)")

    step(4, "RUN ACROSS FRAMEWORKS")
    adapters = [RawAdapter(passport), LangChainAdapter(passport), CrewAIAdapter(passport), LlamaIndexAdapter(passport)]
    for a in adapters:
        a.build_agent()
        print(f"  built agent in runtime: {a.runtime_name}")

    tasks, routing_mode = build_task_set(passport, list(TASKS.keys()), deterministic_fallback=TASKS)
    print(f"  Task routing mode: {'LIVE LLM' if routing_mode == 'live-llm' else 'DETERMINISTIC REPLAY'}"
          + ("" if llm_available() else " (no ANTHROPIC_API_KEY set)"))

    harness = VerificationHarness(passport, adapters)
    try:
        certificate = harness.run(tasks)
    except VerificationFailure as e:
        print("\n  [FAIL] Cross-runtime verification failed:")
        print(e)
        sys.exit(1)

    step(5, "COMPARE TOOL CALLS ACROSS RUNTIMES")
    eq = certificate["checks"]["tool_call_equivalence"]
    overall_ok &= status(eq["passed"], f"tool-call traces identical across {certificate['runtimes_tested']}")

    step(6, "VERIFY CONSTRAINTS AND TOOL CONTRACTS")
    for check_name in ["constraint_compliance", "tool_contract_compliance", "tool_argument_validity"]:
        overall_ok &= status(certificate["checks"][check_name]["passed"], check_name.replace("_", " "))

    print(f"\n  Structured verification result:\n{json.dumps(certificate['structured_result'], indent=2)}")

    print("\n  Note the over-budget restock task above was still schema-valid and authorized —")
    print("  it demonstrates a business-logic rejection (tool-level), which is distinct from")
    print("  a contract violation (declared/authorized/argument-schema level). Both are")
    print("  handled correctly, and distinguishing them is itself part of honest tool design.")

    step("FINAL", "STAMP VERIFIED MANIFEST")
    verified_manifest = passport.stamp_verification(certificate["runtimes_tested"])
    verified_manifest["verification"]["certificate"] = certificate
    with open(VERIFIED_PATH, "w") as f:
        json.dump(verified_manifest, f, indent=2)
    print(f"  Verified, portable manifest written to {VERIFIED_PATH}")

    print("\n" + "#" * 72)
    if overall_ok:
        print(f"# RESULT: ALL CHECKS PASSED - Inventory Ops Agent verified across: {', '.join(certificate['runtimes_tested'])}")
        print("# This is a SECOND, structurally different agent proving the passport schema")
        print("# and verification pipeline are not overfit to the Travel Concierge example.")
    else:
        print("# RESULT: ONE OR MORE CHECKS FAILED - see [FAIL] lines above")
    print("#" * 72)

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
