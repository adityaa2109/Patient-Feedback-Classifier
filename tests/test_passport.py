import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from core.passport import Passport, sign_manifest, verify_signature, sha256_hex
from adapters.raw_adapter import RawAdapter
from adapters.langchain_adapter import LangChainAdapter
from adapters.crewai_adapter import CrewAIAdapter
from verification.harness import VerificationHarness, VerificationFailure

HERE = os.path.dirname(__file__)
MANIFEST_PATH = os.path.join(HERE, "..", "examples", "travel_concierge.manifest.json")

TASKS = {
    "task-a": {
        "tool_calls": [{"tool": "search_flights", "args": {"origin": "Delhi", "destination": "Pune", "date": "2026-11-01"}}],
        "final_answer": "flights found",
    },
    "task-b": {
        "tool_calls": [{"tool": "search_hotels", "args": {"city": "Jaipur", "check_in": "2026-11-01", "check_out": "2026-11-03"}}],
        "final_answer": "hotels found",
    },
}


@pytest.fixture
def signed_manifest():
    with open(MANIFEST_PATH) as f:
        unsigned = json.load(f)
    return sign_manifest(unsigned)


def test_sign_and_verify_roundtrip(signed_manifest):
    assert verify_signature(signed_manifest) is True


def test_tampering_invalidates_signature(signed_manifest):
    tampered = json.loads(json.dumps(signed_manifest))
    tampered["tools"].append({
        "name": "evil_tool", "description": "x",
        "input_schema": {}, "output_schema": {},
    })
    assert verify_signature(tampered) is False


def test_passport_load_rejects_bad_signature(signed_manifest):
    tampered = json.loads(json.dumps(signed_manifest))
    tampered["identity"]["name"] = "Renamed Agent"
    with pytest.raises(ValueError):
        Passport.load(tampered, strict=True)


def test_passport_loads_with_correct_signature(signed_manifest):
    p = Passport.load(signed_manifest, strict=True)
    assert p.name == "Travel Concierge"
    assert {t.name for t in p.tools} == {"search_flights", "search_hotels"}


def test_behavior_contract_hash_matches_prompt(signed_manifest):
    contract = signed_manifest["behavior_contract"]
    assert sha256_hex(contract["system_prompt"]) == contract["system_prompt_hash"]


def test_cross_framework_tool_call_equivalence(signed_manifest):
    passport = Passport.load(signed_manifest, strict=True)
    adapters = [RawAdapter(passport), LangChainAdapter(passport), CrewAIAdapter(passport)]
    harness = VerificationHarness(passport, adapters)
    certificate = harness.run(TASKS)
    assert certificate["checks"]["tool_call_equivalence"]["passed"] is True
    assert certificate["checks"]["constraint_compliance"]["passed"] is True
    assert certificate["checks"]["tool_contract_compliance"]["passed"] is True
    assert len(certificate["runtimes_tested"]) == 3


def test_undeclared_tool_call_is_flagged(signed_manifest):
    passport = Passport.load(signed_manifest, strict=True)
    adapter = RawAdapter(passport)
    adapter.build_agent()
    result = adapter.run_task("bad-task", deterministic_router={
        "bad-task": {"tool_calls": [{"tool": "delete_everything", "args": {}}], "final_answer": "oops"}
    })
    assert "delete_everything" in " ".join(result.constraint_violations)


def test_max_tool_calls_enforced(signed_manifest):
    passport = Passport.load(signed_manifest, strict=True)
    passport.max_tool_calls_per_task = 1
    adapter = RawAdapter(passport)
    adapter.build_agent()
    many_calls_task = {
        "task": {
            "tool_calls": [
                {"tool": "search_flights", "args": {"origin": "A", "destination": "B", "date": "2026-01-01"}},
                {"tool": "search_flights", "args": {"origin": "C", "destination": "D", "date": "2026-01-02"}},
            ],
            "final_answer": "x",
        }
    }
    result = adapter.run_task("task", deterministic_router=many_calls_task)
    assert len(result.tool_calls) == 1
    assert any("max_tool_calls_per_task" in v for v in result.constraint_violations)
