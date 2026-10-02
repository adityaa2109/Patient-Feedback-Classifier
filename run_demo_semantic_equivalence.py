"""
run_demo_semantic_equivalence.py

Demonstrates WHY semantic equivalence checking exists and proves it does
the right thing in both directions — accepting valid variation, still
rejecting genuine problems. No API key or network call required: the two
"runtimes" below are hand-built RunResult objects standing in for what
two independent live-LLM tool-call decisions might plausibly look like,
in the same deterministic, reproducible spirit as every other demo in
this project.

Scenario: Inventory Ops Agent, restocking SKU-WIDGET-001.

  SCENARIO 1 (the problem exact-match can't handle):
    "runtime-A" decides to check stock, notify ops, then restock 100 units.
    "runtime-B" decides to check stock, notify ops, then restock 90 units.
    Both are legitimate: same plan, same tools in the same order, same
    budget respected, just a different (still-valid) quantity choice.
      -> exact-match equivalence: FAILS (the arguments differ)
      -> semantic equivalence:    PASSES (same tools/order, each call valid)

  SCENARIO 2 (semantic must still catch a real problem):
    "runtime-C" skips check_stock_level entirely and goes straight to
    placing the order — violating the agent's declared "must check stock
    before restocking" constraint.
      -> semantic equivalence: FAILS (tool-call sequence doesn't match)

  SCENARIO 3 (semantic must still catch an invalid argument):
    "runtime-D" follows the right sequence but requests 10,000 units —
    above the tool's declared maximum of 500.
      -> semantic equivalence: FAILS (argument validation, independent
         of any other runtime's choices)

Usage:
    python run_demo_semantic_equivalence.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from core.passport import Passport, sign_manifest
from adapters.base import RunResult, ToolCallRecord
from verification.harness import VerificationHarness

HERE = os.path.dirname(__file__)
MANIFEST_PATH = os.path.join(HERE, "examples", "inventory_ops.manifest.json")


def step(title: str):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def status(ok: bool, label: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def build_run_result(runtime_tag: str, calls: list[tuple[str, dict, dict]]) -> RunResult:
    """calls: list of (tool_name, arguments, result) tuples."""
    result = RunResult(runtime_name=runtime_tag)
    for tool_name, args, tool_result in calls:
        result.tool_calls.append(ToolCallRecord(tool_name=tool_name, arguments=args, result=tool_result))
    result.final_answer = f"({runtime_tag} simulated decision)"
    return result


def main():
    with open(MANIFEST_PATH) as f:
        unsigned = json.load(f)
    passport = Passport.load(sign_manifest(unsigned), strict=True)

    # harness is used only as a namespace holder here — no real adapters
    # are attached, since this demo hand-builds the per-runtime results
    # directly to make the contrast explicit and fully deterministic.
    harness = VerificationHarness(passport, adapters=[])

    stock_check = {"item_sku": "SKU-WIDGET-001", "current_stock": 4, "reorder_threshold": 20}
    notify_result = {"posted": True, "message_length": 42}

    # ---------------------------------------------------------------- SCENARIO 1
    step("SCENARIO 1 — two valid, differently-sized restock decisions")

    runtime_a = build_run_result("runtime-A (quantity=100)", [
        ("check_stock_level", {"item_sku": "SKU-WIDGET-001"}, stock_check),
        ("notify_ops_channel", {"message": "Restocking SKU-WIDGET-001"}, notify_result),
        ("place_restock_order", {"item_sku": "SKU-WIDGET-001", "quantity": 100, "max_order_value_usd": 2000.0},
         {"order_id": "PO-WIDGET-100", "total_cost_usd": 1250.0, "status": "placed"}),
    ])
    runtime_b = build_run_result("runtime-B (quantity=90)", [
        ("check_stock_level", {"item_sku": "SKU-WIDGET-001"}, stock_check),
        ("notify_ops_channel", {"message": "Restocking SKU-WIDGET-001, stock is low"}, notify_result),
        ("place_restock_order", {"item_sku": "SKU-WIDGET-001", "quantity": 90, "max_order_value_usd": 2000.0},
         {"order_id": "PO-WIDGET-90", "total_cost_usd": 1125.0, "status": "placed"}),
    ])

    per_runtime_1 = {runtime_a.runtime_name: [runtime_a], runtime_b.runtime_name: [runtime_b]}

    exact_result = harness._check_tool_call_equivalence(per_runtime_1)
    semantic_result = harness._check_semantic_tool_call_equivalence(per_runtime_1)

    print("\n  Exact-match check (what the default mode would say):")
    status(exact_result["passed"], "exact-match equivalence")
    if not exact_result["passed"]:
        print(f"         -> correctly reports a mismatch, because arguments differ: "
              f"{exact_result['mismatched_runtimes']}")
        print("         -> this is a FALSE ALARM: both runtimes made a legitimate, valid decision")

    print("\n  Semantic equivalence check (the new mode):")
    status(semantic_result["passed"], "semantic equivalence")
    print(f"         -> baseline tool sequence: {semantic_result['baseline_tool_sequence']}")
    print("         -> both runtimes followed the same plan and each call independently "
          "validated — the quantity difference is correctly treated as acceptable variation")

    scenario1_ok = (exact_result["passed"] is False) and (semantic_result["passed"] is True)
    status(scenario1_ok, "Scenario 1 demonstrates the intended contrast (exact fails, semantic passes)")

    # ---------------------------------------------------------------- SCENARIO 2
    step("SCENARIO 2 — a runtime skips the required stock check")

    runtime_c = build_run_result("runtime-C (skipped check_stock_level)", [
        ("notify_ops_channel", {"message": "Restocking SKU-WIDGET-001"}, notify_result),
        ("place_restock_order", {"item_sku": "SKU-WIDGET-001", "quantity": 100, "max_order_value_usd": 2000.0},
         {"order_id": "PO-WIDGET-100", "total_cost_usd": 1250.0, "status": "placed"}),
    ])

    per_runtime_2 = {runtime_a.runtime_name: [runtime_a], runtime_c.runtime_name: [runtime_c]}
    semantic_result_2 = harness._check_semantic_tool_call_equivalence(per_runtime_2)

    status(not semantic_result_2["passed"], "semantic equivalence correctly REJECTS the skipped-step runtime")
    print(f"  Reported problem: {semantic_result_2['problems_by_runtime'].get(runtime_c.runtime_name)}")

    # ---------------------------------------------------------------- SCENARIO 3
    step("SCENARIO 3 — a runtime requests a quantity above the declared maximum")

    runtime_d = build_run_result("runtime-D (quantity=10000, over max)", [
        ("check_stock_level", {"item_sku": "SKU-WIDGET-001"}, stock_check),
        ("notify_ops_channel", {"message": "Restocking SKU-WIDGET-001"}, notify_result),
        ("place_restock_order", {"item_sku": "SKU-WIDGET-001", "quantity": 10000, "max_order_value_usd": 2000.0},
         {"order_id": "", "total_cost_usd": 125000.0, "status": "rejected_over_budget"}),
    ])

    per_runtime_3 = {runtime_a.runtime_name: [runtime_a], runtime_d.runtime_name: [runtime_d]}
    semantic_result_3 = harness._check_semantic_tool_call_equivalence(per_runtime_3)

    status(not semantic_result_3["passed"], "semantic equivalence correctly REJECTS the out-of-bounds quantity")
    print(f"  Reported problem: {semantic_result_3['problems_by_runtime'].get(runtime_d.runtime_name)}")

    # ---------------------------------------------------------------- SUMMARY
    step("SUMMARY")
    all_ok = scenario1_ok and (not semantic_result_2["passed"]) and (not semantic_result_3["passed"])
    if all_ok:
        print("  ALL SCENARIOS BEHAVED AS INTENDED.")
        print("  Semantic equivalence accepts legitimate variation and still rejects real problems.")
    else:
        print("  UNEXPECTED RESULT — see FAIL lines above.")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
