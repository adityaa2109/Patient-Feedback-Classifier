"""
verification/live_llm_check.py

A single-purpose script: run this once you have a real ANTHROPIC_API_KEY
to confirm the live-LLM tool-selection path (core/llm_router.py) actually
works end to end against the real Anthropic API — not mocked, a genuine
network call.

This is intentionally separate from run_demo.py so a real API failure
here doesn't block your main demo, and so the result is unambiguous:
this script's ONLY job is to answer "does the live path actually work?"

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python verification/live_llm_check.py

Exit code 0 means the live call succeeded and produced a usable,
verification-passing route. Exit code 1 means something needs attention
— the script tells you exactly what.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.passport import Passport, sign_manifest
from core.llm_router import decide_route, llm_available, DEFAULT_MODEL
from adapters.raw_adapter import RawAdapter
from verification.harness import VerificationHarness, VerificationFailure

HERE = os.path.dirname(__file__)
MANIFEST_PATH = os.path.join(HERE, "..", "examples", "travel_concierge.manifest.json")

TASK = "Find me a flight from Mumbai to Delhi on 2026-10-05"


def main():
    print("=" * 72)
    print("LIVE LLM CHECK — real Anthropic API call, no mocks")
    print("=" * 72)

    if not llm_available():
        print("\n[BLOCKED] No ANTHROPIC_API_KEY found in the environment.")
        print("Set it first:  export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    print(f"\nModel: {os.environ.get('AGENT_PASSPORT_LLM_MODEL', DEFAULT_MODEL)}")
    print(f"Task:  \"{TASK}\"")
    print("\nCalling the real Anthropic API (this makes an actual network request)...")

    with open(MANIFEST_PATH) as f:
        unsigned = json.load(f)
    passport = Passport.load(sign_manifest(unsigned), strict=True)

    route = decide_route(passport, TASK)

    if route is None:
        print("\n[FAIL] decide_route() returned None.")
        print("This means either the network call failed, timed out, or the")
        print("model's response could not be parsed into a usable route even")
        print("after defensive extraction/normalization. Check your API key,")
        print("network connectivity, and rate limits, then retry.")
        sys.exit(1)

    print("\n[OK] Received and parsed a route from the live model:")
    print(json.dumps(route, indent=2))

    print("\nRunning the parsed route through the SAME verification pipeline")
    print("used everywhere else in this project (declared/authorized/")
    print("argument-schema checks) — the live model gets no special treatment.")

    adapter = RawAdapter(passport)
    harness = VerificationHarness(passport, [adapter])
    try:
        certificate = harness.run({TASK: route})
    except VerificationFailure as e:
        print("\n[FAIL] The live model's route did not pass verification:")
        print(e)
        print("\nThis does not necessarily mean llm_router.py is broken — it may")
        print("mean the model picked an invalid tool/argument for this task.")
        print("If this happens consistently, tighten the prompt in")
        print("core/llm_router.py's _build_prompt().")
        sys.exit(1)

    print("\n[PASS] Live-LLM route passed full verification.")
    print(f"Structured result: {json.dumps(certificate['structured_result'], indent=2)}")
    print("\n" + "#" * 72)
    print("# LIVE LLM MODE CONFIRMED WORKING against the real Anthropic API.")
    print("# You can now cite this in your README/demo with confidence.")
    print("#" * 72)
    sys.exit(0)


if __name__ == "__main__":
    main()
