"""
run_demo.py

The single script that proves the whole Agent Passport claim end to end,
in the exact narrative a hackathon judge should be able to follow without
reading any source code:

    CREATE -> SIGN -> LOAD & VERIFY -> RUN ACROSS FRAMEWORKS ->
    COMPARE TOOL CALLS -> VERIFY CONSTRAINTS -> SIMULATE TAMPERING ->
    REJECT TAMPERED AGENT

Usage:
    python run_demo.py
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
UNSIGNED_PATH = os.path.join(HERE, "examples", "travel_concierge.manifest.json")
SIGNED_PATH = os.path.join(HERE, "examples", "travel_concierge.SIGNED.json")
VERIFIED_PATH = os.path.join(HERE, "examples", "travel_concierge.VERIFIED.json")

TASKS = {
    "Find me a flight from Mumbai to Delhi on 2026-10-05": {
        "tool_calls": [
            {"tool": "search_flights", "args": {"origin": "Mumbai", "destination": "Delhi", "date": "2026-10-05"}}
        ],
        "final_answer": "Here are flight options from Mumbai to Delhi on 2026-10-05.",
    },
    "Find hotels in Goa for 2026-12-20 to 2026-12-23": {
        "tool_calls": [
            {"tool": "search_hotels", "args": {"city": "Goa", "check_in": "2026-12-20", "check_out": "2026-12-23"}}
        ],
        "final_answer": "Here are hotel options in Goa for your dates.",
    },
}
# This dict also serves as the deterministic FALLBACK for the live-LLM
# router below: if no ANTHROPIC_API_KEY is set, or the live call fails
# for any reason, these exact routes are used instead — the demo always
# completes, with or without a key.


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

    step(1, "CREATE PASSPORT")
    with open(UNSIGNED_PATH) as f:
        unsigned = json.load(f)
    print(f"  Loaded unsigned manifest for agent '{unsigned['identity']['name']}'")
    print(f"  Declared tools: {[t['name'] for t in unsigned['tools']]}")
    print(f"  Declared constraints: {len(unsigned['behavior_contract']['constraints'])}")

    step(2, "SIGN PASSPORT")
    signed = sign_manifest(unsigned)
    with open(SIGNED_PATH, "w") as f:
        json.dump(signed, f, indent=2)
    print(f"  behavior_contract.system_prompt_hash = {signed['behavior_contract']['system_prompt_hash'][:20]}...")
    print(f"  signature.value                      = {signed['signature']['value'][:20]}...")
    print(f"  Written to {SIGNED_PATH}")

    step(3, "LOAD AND VERIFY")
    passport = Passport.load(signed, strict=True)
    static_result = passport.verify_full()
    overall_ok &= status(static_result.passport_valid, "signature valid")
    overall_ok &= status(static_result.identity_valid, "identity block complete")
    overall_ok &= status(static_result.behavior_contract_valid, "behavior_contract hash matches prompt")
    overall_ok &= status(static_result.tool_contract_valid, "no duplicate tool names declared")
    print(f"  Loaded passport for agent_id={passport.agent_id!r}, {len(passport.tools)} tool(s), "
          f"{len(passport.constraints)} constraint(s), schema v{passport.schema_version}")

    step(4, "RUN ACROSS FRAMEWORKS")
    adapters = [RawAdapter(passport), LangChainAdapter(passport), CrewAIAdapter(passport), LlamaIndexAdapter(passport)]
    for a in adapters:
        a.build_agent()
        print(f"  built agent in runtime: {a.runtime_name}")

    tasks, routing_mode = build_task_set(passport, list(TASKS.keys()), deterministic_fallback=TASKS)
    mode_label = {
        "live-llm": "LIVE LLM (Claude decided every tool call)",
        "mixed": "MIXED (some tasks used live Claude, others fell back)",
        "deterministic": "DETERMINISTIC REPLAY (no ANTHROPIC_API_KEY found - using fixed routes)",
    }[routing_mode]
    print(f"  Task routing mode: {mode_label}")
    if not llm_available():
        print("  (Set ANTHROPIC_API_KEY in your environment to see live LLM tool selection instead.)")

    harness = VerificationHarness(passport, adapters)
    try:
        certificate = harness.run(tasks)
    except VerificationFailure as e:
        print("\n  [FAIL] Cross-runtime verification failed:")
        print(e)
        sys.exit(1)

    step(5, "COMPARE TOOL CALLS")
    eq = certificate["checks"]["tool_call_equivalence"]
    overall_ok &= status(eq["passed"], f"tool-call traces identical across {certificate['runtimes_tested']}")

    step(6, "VERIFY CONSTRAINTS")
    cc = certificate["checks"]["constraint_compliance"]
    tc = certificate["checks"]["tool_contract_compliance"]
    av = certificate["checks"]["tool_argument_validity"]
    overall_ok &= status(cc["passed"], "no constraint violations in any runtime")
    overall_ok &= status(tc["passed"], "every tool call was declared AND authorized")
    overall_ok &= status(av["passed"], "every tool call's arguments matched its input_schema")

    structured = certificate["structured_result"]
    print(f"\n  Structured verification result:\n{json.dumps(structured, indent=2)}")

    step(7, "SIMULATE TAMPERING")
    tampered = json.loads(json.dumps(signed))
    tampered["identity"]["name"] = "Hijacked Concierge"
    tampered["tools"].append({
        "name": "wire_transfer", "description": "not in the original passport",
        "input_schema": {}, "output_schema": {}, "side_effects": "external", "authorized": True,
    })
    print("  Simulated attack: renamed agent identity + injected an undeclared 'wire_transfer' tool")
    print("  (mirrors verification/tamper_demo.py, which covers 7 distinct attack types in full)")

    step(8, "REJECT TAMPERED AGENT")
    try:
        Passport.load(tampered, strict=True)
        overall_ok &= status(False, "tampered manifest was loaded - this should never happen")
    except ValueError as e:
        overall_ok &= status(True, f"tampered manifest rejected ({e})")

    step("FINAL", "STAMP VERIFIED MANIFEST")
    verified_manifest = passport.stamp_verification(certificate["runtimes_tested"])
    verified_manifest["verification"]["certificate"] = certificate
    with open(VERIFIED_PATH, "w") as f:
        json.dump(verified_manifest, f, indent=2)
    print(f"  Verified, portable manifest written to {VERIFIED_PATH}")

    print("\n" + "#" * 72)
    if overall_ok:
        print(f"# RESULT: ALL CHECKS PASSED - Agent Passport verified across: {', '.join(certificate['runtimes_tested'])}")
    else:
        print("# RESULT: ONE OR MORE CHECKS FAILED - see [FAIL] lines above")
    print("#" * 72)

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
