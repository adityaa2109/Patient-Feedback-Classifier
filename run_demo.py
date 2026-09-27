"""
run_demo.py

The single script that proves the whole Agent Passport claim:

    "One agent definition. Three runtimes. Byte-identical behavior. Verified."

Usage:
    python run_demo.py

What it does:
 1. Loads the unsigned example manifest and signs it (core/passport.py).
 2. Loads the signed Passport object (with signature + hash verification).
 3. Instantiates the same passport inside three adapters:
    raw-sdk, langchain(-shim), crewai(-shim).
 4. Runs an identical deterministic task set against all three.
 5. Runs the verification harness to prove tool-call equivalence,
    constraint compliance, and tool-contract compliance.
 6. Stamps the manifest with a verification certificate and writes
    the final, portable, verified manifest to disk.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from core.passport import Passport, sign_manifest
from adapters.raw_adapter import RawAdapter
from adapters.langchain_adapter import LangChainAdapter
from adapters.crewai_adapter import CrewAIAdapter
from verification.harness import VerificationHarness, VerificationFailure


HERE = os.path.dirname(__file__)
UNSIGNED_PATH = os.path.join(HERE, "examples", "travel_concierge.manifest.json")
SIGNED_PATH = os.path.join(HERE, "examples", "travel_concierge.SIGNED.json")
VERIFIED_PATH = os.path.join(HERE, "examples", "travel_concierge.VERIFIED.json")


# Deterministic task set: simulates what an LLM tool-call decision would
# produce, so the SAME decisions are fed to every adapter. This isolates
# the thing we're actually testing (does the runtime execute an identical
# agent identically?) from LLM non-determinism, which is a different
# problem than portability.
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


def main():
    print("=" * 70)
    print("STEP 1 — Sign the manifest")
    print("=" * 70)
    with open(UNSIGNED_PATH) as f:
        unsigned = json.load(f)
    signed = sign_manifest(unsigned)
    with open(SIGNED_PATH, "w") as f:
        json.dump(signed, f, indent=2)
    print(f"Signed manifest written to {SIGNED_PATH}")
    print(f"behavior_contract.system_prompt_hash = {signed['behavior_contract']['system_prompt_hash'][:16]}...")
    print(f"signature.value                      = {signed['signature']['value'][:16]}...\n")

    print("=" * 70)
    print("STEP 2 — Load Passport (verifies signature + contract hash)")
    print("=" * 70)
    passport = Passport.load(signed, strict=True)
    print(f"Loaded passport for agent '{passport.name}' v{passport.version}")
    print(f"Declared tools: {[t.name for t in passport.tools]}\n")

    print("=" * 70)
    print("STEP 3 — Instantiate the SAME passport across 3 runtimes")
    print("=" * 70)
    adapters = [RawAdapter(passport), LangChainAdapter(passport), CrewAIAdapter(passport)]
    for a in adapters:
        a.build_agent()
        print(f"  built: {a.runtime_name}")
    print()

    print("=" * 70)
    print("STEP 4 — Run identical tasks on every runtime + verify equivalence")
    print("=" * 70)
    harness = VerificationHarness(passport, adapters)
    try:
        certificate = harness.run(TASKS)
    except VerificationFailure as e:
        print("VERIFICATION FAILED:")
        print(e)
        sys.exit(1)

    print(json.dumps(certificate, indent=2))
    print()

    print("=" * 70)
    print("STEP 5 — Stamp the manifest with the verification certificate")
    print("=" * 70)
    verified_manifest = passport.stamp_verification(certificate["runtimes_tested"])
    verified_manifest["verification"]["certificate"] = certificate
    with open(VERIFIED_PATH, "w") as f:
        json.dump(verified_manifest, f, indent=2)
    print(f"Final verified, portable manifest written to {VERIFIED_PATH}")
    print("\n✅ Agent Passport verified across:", ", ".join(certificate["runtimes_tested"]))


if __name__ == "__main__":
    main()
