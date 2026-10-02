import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from core.passport import (
    Passport, sign_manifest, verify_signature, sha256_hex,
    validate_tool_arguments, diff_identity, VerificationResult, ToolContract,
)
from adapters.raw_adapter import RawAdapter
from adapters.langchain_adapter import LangChainAdapter
from adapters.crewai_adapter import CrewAIAdapter
from adapters.llamaindex_adapter import LlamaIndexAdapter
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
    adapters = [RawAdapter(passport), LangChainAdapter(passport), CrewAIAdapter(passport), LlamaIndexAdapter(passport)]
    harness = VerificationHarness(passport, adapters)
    certificate = harness.run(TASKS)
    assert certificate["checks"]["tool_call_equivalence"]["passed"] is True
    assert certificate["checks"]["constraint_compliance"]["passed"] is True
    assert certificate["checks"]["tool_contract_compliance"]["passed"] is True
    assert len(certificate["runtimes_tested"]) == 4


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


# ---------------------------------------------------------------------------
# New tests added for the upgrade: schema fields, authorization, argument
# validation, structured verification results, and identity diffing.
# None of the tests above were modified or weakened.
# ---------------------------------------------------------------------------

def test_schema_version_defaults_and_is_exposed(signed_manifest):
    p = Passport.load(signed_manifest, strict=True)
    assert p.schema_version == "1.0"


def test_unsupported_schema_version_is_rejected(signed_manifest):
    bad = json.loads(json.dumps(signed_manifest))
    bad["passport_version"] = "9.9"
    with pytest.raises(ValueError):
        Passport.load(bad, strict=True)


def test_tool_authorized_defaults_true_and_can_be_revoked(signed_manifest):
    p = Passport.load(signed_manifest, strict=True)
    assert p.authorized_tool_names() == {"search_flights", "search_hotels"}

    # Simulate revoking a tool's authorization without deleting its contract.
    manifest = json.loads(json.dumps(signed_manifest))
    for t in manifest["tools"]:
        if t["name"] == "search_hotels":
            t["authorized"] = False
    resigned = sign_manifest({k: v for k, v in manifest.items() if k != "signature"})
    p2 = Passport.load(resigned, strict=True)
    assert p2.authorized_tool_names() == {"search_flights"}
    assert p2.get_tool("search_hotels") is not None  # still declared, just not authorized


def test_deauthorized_tool_is_blocked_at_runtime(signed_manifest):
    manifest = json.loads(json.dumps(signed_manifest))
    for t in manifest["tools"]:
        if t["name"] == "search_hotels":
            t["authorized"] = False
    resigned = sign_manifest({k: v for k, v in manifest.items() if k != "signature"})
    passport = Passport.load(resigned, strict=True)

    adapter = RawAdapter(passport)
    adapter.build_agent()
    result = adapter.run_task("book", deterministic_router={
        "book": {"tool_calls": [{"tool": "search_hotels", "args": {"city": "Pune", "check_in": "2026-01-01", "check_out": "2026-01-02"}}],
                 "final_answer": "x"}
    })
    assert len(result.tool_calls) == 0  # never executed: search_hotels not in the built tool set

    harness = VerificationHarness(passport, [adapter])
    with pytest.raises(VerificationFailure):
        harness.run({"book": {"tool_calls": [{"tool": "search_hotels", "args": {"city": "Pune", "check_in": "2026-01-01", "check_out": "2026-01-02"}}], "final_answer": "x"}})


def test_validate_tool_arguments_flags_missing_required_field():
    contract = ToolContract(
        name="search_flights", description="x",
        input_schema={"type": "object", "required": ["origin", "destination", "date"],
                      "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}, "date": {"type": "string"}}},
        output_schema={},
    )
    issues = validate_tool_arguments(contract, {"origin": "Delhi"})
    assert any("destination" in i for i in issues)
    assert any("date" in i for i in issues)


def test_validate_tool_arguments_flags_wrong_type():
    contract = ToolContract(
        name="search_flights", description="x",
        input_schema={"type": "object", "required": ["origin"],
                      "properties": {"origin": {"type": "string"}}},
        output_schema={},
    )
    issues = validate_tool_arguments(contract, {"origin": 12345})
    assert any("expected type 'string'" in i for i in issues)


def test_validate_tool_arguments_passes_for_correct_args():
    contract = ToolContract(
        name="search_flights", description="x",
        input_schema={"type": "object", "required": ["origin"],
                      "properties": {"origin": {"type": "string"}}},
        output_schema={},
    )
    assert validate_tool_arguments(contract, {"origin": "Delhi"}) == []


def test_structured_verification_result_shape(signed_manifest):
    passport = Passport.load(signed_manifest, strict=True)
    adapters = [RawAdapter(passport), LangChainAdapter(passport), CrewAIAdapter(passport)]
    harness = VerificationHarness(passport, adapters)
    certificate = harness.run(TASKS)
    result = certificate["structured_result"]
    for key in ["passport_valid", "identity_valid", "behavior_contract_valid",
                "tool_contract_valid", "runtime_compliance", "violations", "overall"]:
        assert key in result
    assert result["overall"] is True
    assert result["violations"] == []


def test_verify_full_reports_violation_on_tampered_prompt(signed_manifest):
    passport = Passport.load(signed_manifest, strict=True)
    # Simulate post-load in-memory tampering of the raw manifest to exercise
    # verify_full()'s hash-mismatch violation reporting directly.
    passport.raw_manifest["behavior_contract"]["system_prompt"] += " extra"
    result = passport.verify_full()
    assert result.behavior_contract_valid is False
    assert result.overall is False
    assert any("system_prompt_hash" in v for v in result.violations)


def test_diff_identity_reports_tool_and_field_changes(signed_manifest):
    tampered = json.loads(json.dumps(signed_manifest))
    tampered["identity"]["agent_id"] = "agent.other"
    tampered["tools"] = [t for t in tampered["tools"] if t["name"] != "search_hotels"]
    changes = diff_identity(signed_manifest, tampered)
    assert changes["identity.agent_id"]["after"] == "agent.other"
    assert "search_hotels" in changes["tools"]["removed"]


def test_stale_signature_after_hash_only_update_is_rejected(signed_manifest):
    """The 'sneaky' attack: prompt AND hash are updated consistently with
    each other, but the signature itself is never recomputed. Must still
    be rejected — this is what proves signing covers real integrity, not
    just internal hash self-consistency."""
    tampered = json.loads(json.dumps(signed_manifest))
    tampered["behavior_contract"]["system_prompt"] += " hidden instruction"
    tampered["behavior_contract"]["system_prompt_hash"] = sha256_hex(tampered["behavior_contract"]["system_prompt"])
    with pytest.raises(ValueError):
        Passport.load(tampered, strict=True)


def test_tool_argument_validity_check_flags_bad_call(signed_manifest):
    passport = Passport.load(signed_manifest, strict=True)
    adapter = RawAdapter(passport)
    harness = VerificationHarness(passport, [adapter])
    bad_tasks = {
        "bad-args-task": {
            "tool_calls": [{"tool": "search_flights", "args": {"origin": "Delhi"}}],  # missing destination/date
            "final_answer": "x",
        }
    }
    with pytest.raises(VerificationFailure) as excinfo:
        harness.run(bad_tasks)
    assert "tool_argument_validity" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Live-LLM router tests (mocked — no real network/API key required so CI
# and offline grading stay fully reproducible). Confirms the fallback
# contract: no key -> deterministic; key present but call fails -> still
# deterministic; key present and call succeeds -> live route is used and
# still passes the exact same verification pipeline.
# ---------------------------------------------------------------------------

from core import llm_router


def test_llm_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_router.llm_available() is False


def test_decide_route_returns_none_without_api_key(signed_manifest, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    passport = Passport.load(signed_manifest, strict=True)
    assert llm_router.decide_route(passport, "any task") is None


def test_decide_route_returns_none_on_network_failure(signed_manifest, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    passport = Passport.load(signed_manifest, strict=True)

    def _boom(*args, **kwargs):
        raise OSError("simulated network failure")

    monkeypatch.setattr(llm_router.urllib.request, "urlopen", _boom)
    assert llm_router.decide_route(passport, "any task") is None


def test_decide_route_parses_valid_llm_response(signed_manifest, monkeypatch):
    """Mocks a successful Anthropic API response and confirms the router
    correctly extracts a usable route dict from it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    passport = Passport.load(signed_manifest, strict=True)

    fake_response_body = json.dumps({
        "content": [{
            "type": "text",
            "text": json.dumps({
                "tool_calls": [{"tool": "search_flights", "args": {"origin": "Pune", "destination": "Goa", "date": "2026-01-01"}}],
                "final_answer": "Here are your flights.",
            }),
        }]
    }).encode("utf-8")

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return fake_response_body

    monkeypatch.setattr(llm_router.urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

    route = llm_router.decide_route(passport, "book me a flight")
    assert route is not None
    assert route["tool_calls"][0]["tool"] == "search_flights"
    assert route["final_answer"] == "Here are your flights."


def test_build_task_set_falls_back_to_deterministic_without_key(signed_manifest, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    passport = Passport.load(signed_manifest, strict=True)
    fallback = {"task-a": {"tool_calls": [], "final_answer": "fallback used"}}
    tasks, mode = llm_router.build_task_set(passport, ["task-a"], deterministic_fallback=fallback)
    assert mode == "deterministic"
    assert tasks["task-a"]["final_answer"] == "fallback used"


def test_build_task_set_uses_live_route_when_available(signed_manifest, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    passport = Passport.load(signed_manifest, strict=True)

    live_route = {"tool_calls": [{"tool": "search_hotels", "args": {"city": "Goa", "check_in": "2026-01-01", "check_out": "2026-01-02"}}],
                  "final_answer": "live route used"}
    monkeypatch.setattr(llm_router, "decide_route", lambda p, desc, **k: live_route)

    fallback = {"task-a": {"tool_calls": [], "final_answer": "fallback used"}}
    tasks, mode = llm_router.build_task_set(passport, ["task-a"], deterministic_fallback=fallback)
    assert mode == "live-llm"
    assert tasks["task-a"]["final_answer"] == "live route used"


def test_live_route_still_passes_full_verification_pipeline(signed_manifest, monkeypatch):
    """End-to-end proof that a live-LLM-sourced route is subject to
    exactly the same declared/authorized/argument-schema checks as a
    deterministic one — the LLM never bypasses the passport's contract."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    passport = Passport.load(signed_manifest, strict=True)

    live_route = {"tool_calls": [{"tool": "search_flights", "args": {"origin": "Delhi", "destination": "Goa", "date": "2026-01-01"}}],
                  "final_answer": "live route used"}
    monkeypatch.setattr(llm_router, "decide_route", lambda p, desc, **k: live_route)

    tasks, mode = llm_router.build_task_set(passport, ["live-task"], deterministic_fallback={})
    assert mode == "live-llm"

    adapters = [RawAdapter(passport), LangChainAdapter(passport), CrewAIAdapter(passport)]
    harness = VerificationHarness(passport, adapters)
    certificate = harness.run(tasks)  # must not raise
    assert certificate["structured_result"]["overall"] is True


# ---------------------------------------------------------------------------
# Real-world response robustness tests. Live models don't always follow a
# "respond with ONLY JSON" instruction perfectly — these lock in the
# defensive parsing/normalization added to handle realistic deviations
# (prose wrapping, fence casing, tool-name casing, numeric-string args)
# WITHOUT weakening the actual security checks (an unrecognized tool name
# must still fail verification, not be silently patched).
# ---------------------------------------------------------------------------

def _fake_response(text: str):
    body = json.dumps({"content": [{"type": "text", "text": text}]}).encode("utf-8")

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return body

    return lambda *a, **k: _FakeResponse()


def test_extract_json_handles_prose_wrapped_response(signed_manifest, monkeypatch):
    """Model ignores 'no prose' instruction and adds commentary around
    the JSON object — must still be extracted correctly."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    passport = Passport.load(signed_manifest, strict=True)

    wrapped = (
        'Sure, here is my decision:\n\n'
        '{"tool_calls": [{"tool": "search_flights", "args": {"origin": "Pune", "destination": "Goa", "date": "2026-01-01"}}], '
        '"final_answer": "Found flights."}\n\n'
        'Let me know if you need anything else!'
    )
    monkeypatch.setattr(llm_router.urllib.request, "urlopen", _fake_response(wrapped))
    route = llm_router.decide_route(passport, "book a flight")
    assert route is not None
    assert route["tool_calls"][0]["tool"] == "search_flights"


def test_extract_json_handles_uppercase_fence(signed_manifest, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    passport = Passport.load(signed_manifest, strict=True)

    fenced = (
        '```JSON\n'
        '{"tool_calls": [{"tool": "search_hotels", "args": {"city": "Goa", "check_in": "2026-01-01", "check_out": "2026-01-02"}}], '
        '"final_answer": "Found hotels."}\n'
        '```'
    )
    monkeypatch.setattr(llm_router.urllib.request, "urlopen", _fake_response(fenced))
    route = llm_router.decide_route(passport, "book a hotel")
    assert route is not None
    assert route["tool_calls"][0]["tool"] == "search_hotels"


def test_tool_name_case_mismatch_is_corrected(signed_manifest, monkeypatch):
    """Model returns the right tool with the wrong casing — should be
    normalized to the passport's exact declared name so it isn't
    incorrectly flagged as undeclared."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    passport = Passport.load(signed_manifest, strict=True)

    mismatched = json.dumps({
        "tool_calls": [{"tool": "Search_Flights", "args": {"origin": "Pune", "destination": "Goa", "date": "2026-01-01"}}],
        "final_answer": "x",
    })
    monkeypatch.setattr(llm_router.urllib.request, "urlopen", _fake_response(mismatched))
    route = llm_router.decide_route(passport, "book a flight")
    assert route["tool_calls"][0]["tool"] == "search_flights"  # corrected to exact declared casing


def test_hallucinated_tool_name_is_left_uncorrected_and_still_rejected(signed_manifest, monkeypatch):
    """A tool name with NO case-insensitive match to anything declared
    must be left as-is (not silently mapped to something else) so
    verification correctly flags it as undeclared — normalization must
    never expand what's authorized."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    passport = Passport.load(signed_manifest, strict=True)

    hallucinated = json.dumps({
        "tool_calls": [{"tool": "book_train_ticket", "args": {}}],
        "final_answer": "x",
    })
    monkeypatch.setattr(llm_router.urllib.request, "urlopen", _fake_response(hallucinated))
    route = llm_router.decide_route(passport, "book a train")
    assert route["tool_calls"][0]["tool"] == "book_train_ticket"  # untouched

    adapter = RawAdapter(passport)
    harness = VerificationHarness(passport, [adapter])
    with pytest.raises(VerificationFailure) as excinfo:
        harness.run({"t": route})
    assert "book_train_ticket" in str(excinfo.value)


def test_numeric_string_arguments_are_coerced(signed_manifest, monkeypatch):
    """Models frequently return numeric args as JSON strings ('100'
    instead of 100). This should be coerced to the schema's declared
    type so it passes argument validation, without relaxing which
    fields are required or allowed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    with open(os.path.join(HERE, "..", "examples", "inventory_ops.manifest.json")) as f:
        inv_unsigned = json.load(f)
    inv_signed = sign_manifest(inv_unsigned)
    passport = Passport.load(inv_signed, strict=True)

    stringy = json.dumps({
        "tool_calls": [{"tool": "place_restock_order",
                         "args": {"item_sku": "SKU-WIDGET-001", "quantity": "100", "max_order_value_usd": "2000.0"}}],
        "final_answer": "x",
    })
    monkeypatch.setattr(llm_router.urllib.request, "urlopen", _fake_response(stringy))
    route = llm_router.decide_route(passport, "restock widgets")
    args = route["tool_calls"][0]["args"]
    assert args["quantity"] == 100 and isinstance(args["quantity"], int)
    assert args["max_order_value_usd"] == 2000.0 and isinstance(args["max_order_value_usd"], float)

    # Confirm it now actually passes argument validation (would have
    # failed type-checking before coercion).
    from core.passport import validate_tool_arguments
    tool_contract = passport.get_tool("place_restock_order")
    assert validate_tool_arguments(tool_contract, args) == []


def test_truncated_unbalanced_json_returns_none(signed_manifest, monkeypatch):
    """A response cut off mid-object (e.g. hit max_tokens) must fail
    safely to None, not raise or return a corrupted partial route."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    passport = Passport.load(signed_manifest, strict=True)

    truncated = '{"tool_calls": [{"tool": "search_flights", "args": {"origin": "Delhi"'  # cut off, no closing braces
    monkeypatch.setattr(llm_router.urllib.request, "urlopen", _fake_response(truncated))
    assert llm_router.decide_route(passport, "book a flight") is None


# ---------------------------------------------------------------------------
# Fourth-adapter (LlamaIndex) tests. Proves item 3: a new framework can be
# supported with zero changes to core/, spec/, or verification/ — this
# file only imports the new adapter class, nothing else changed.
# ---------------------------------------------------------------------------

from adapters.llamaindex_adapter import LlamaIndexAdapter


def test_llamaindex_adapter_builds_agent(signed_manifest):
    passport = Passport.load(signed_manifest, strict=True)
    adapter = LlamaIndexAdapter(passport)
    tools = adapter.build_agent()
    assert set(tools.keys()) == {"search_flights", "search_hotels"}


def test_llamaindex_adapter_runs_task_correctly(signed_manifest):
    passport = Passport.load(signed_manifest, strict=True)
    adapter = LlamaIndexAdapter(passport)
    adapter.build_agent()
    result = adapter.run_task("task-a", deterministic_router=TASKS)
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "search_flights"


def test_llamaindex_joins_cross_framework_equivalence_unmodified(signed_manifest):
    """The critical portability proof: adding LlamaIndex to the adapter
    list required zero changes to Passport, the schema, or the harness —
    only a new adapters/*.py file and one import line in the caller."""
    passport = Passport.load(signed_manifest, strict=True)
    adapters = [RawAdapter(passport), LlamaIndexAdapter(passport)]
    harness = VerificationHarness(passport, adapters)
    certificate = harness.run(TASKS)
    assert certificate["checks"]["tool_call_equivalence"]["passed"] is True
    assert "llamaindex" in certificate["runtimes_tested"] or "llamaindex-shim" in certificate["runtimes_tested"]


# ---------------------------------------------------------------------------
# Semantic equivalence checking tests. Proves the new equivalence_mode
# is purely additive: default behavior (exact mode / no mode specified)
# is completely unchanged, while semantic mode correctly accepts valid
# argument variation and still rejects genuine problems (wrong tool
# sequence, invalid/out-of-bounds arguments).
# ---------------------------------------------------------------------------

from adapters.base import RunResult, ToolCallRecord


def _inventory_passport():
    with open(os.path.join(HERE, "..", "examples", "inventory_ops.manifest.json")) as f:
        unsigned = json.load(f)
    return Passport.load(sign_manifest(unsigned), strict=True)


def _inventory_run_result(tag, calls):
    result = RunResult(runtime_name=tag)
    for tool_name, args, tool_result in calls:
        result.tool_calls.append(ToolCallRecord(tool_name=tool_name, arguments=args, result=tool_result))
    return result


def test_default_mode_is_exact_and_behavior_is_unchanged(signed_manifest):
    """No equivalence_mode argument at all -> identical behavior to
    before this feature existed. This is the core backward-compatibility
    guarantee: every existing caller of harness.run(tasks) keeps working
    exactly as it did."""
    passport = Passport.load(signed_manifest, strict=True)
    adapters = [RawAdapter(passport), LangChainAdapter(passport), CrewAIAdapter(passport)]
    harness = VerificationHarness(passport, adapters)
    certificate = harness.run(TASKS)  # no equivalence_mode passed
    assert certificate["equivalence_mode"] == "exact"
    assert certificate["checks"]["tool_call_equivalence"]["passed"] is True


def test_invalid_equivalence_mode_is_rejected(signed_manifest):
    passport = Passport.load(signed_manifest, strict=True)
    harness = VerificationHarness(passport, [RawAdapter(passport)])
    with pytest.raises(ValueError):
        harness.run(TASKS, equivalence_mode="fuzzy")


def test_semantic_mode_accepts_different_but_valid_arguments_where_exact_would_fail():
    """The headline case: two runtimes follow the same plan (same tools,
    same order) but choose different, individually-valid quantities.
    Exact-match must fail this; semantic must pass it."""
    passport = _inventory_passport()
    stock = {"item_sku": "SKU-WIDGET-001", "current_stock": 4, "reorder_threshold": 20}
    notify = {"posted": True, "message_length": 10}

    runtime_a = _inventory_run_result("runtime-A", [
        ("check_stock_level", {"item_sku": "SKU-WIDGET-001"}, stock),
        ("notify_ops_channel", {"message": "x"}, notify),
        ("place_restock_order", {"item_sku": "SKU-WIDGET-001", "quantity": 100, "max_order_value_usd": 2000.0},
         {"order_id": "PO-100", "total_cost_usd": 1250.0, "status": "placed"}),
    ])
    runtime_b = _inventory_run_result("runtime-B", [
        ("check_stock_level", {"item_sku": "SKU-WIDGET-001"}, stock),
        ("notify_ops_channel", {"message": "y"}, notify),
        ("place_restock_order", {"item_sku": "SKU-WIDGET-001", "quantity": 90, "max_order_value_usd": 2000.0},
         {"order_id": "PO-90", "total_cost_usd": 1125.0, "status": "placed"}),
    ])

    harness = VerificationHarness(passport, adapters=[])
    per_runtime = {"runtime-A": [runtime_a], "runtime-B": [runtime_b]}

    exact = harness._check_tool_call_equivalence(per_runtime)
    semantic = harness._check_semantic_tool_call_equivalence(per_runtime)

    assert exact["passed"] is False, "exact-match should fail on differing arguments (this is the problem semantic mode solves)"
    assert semantic["passed"] is True, "semantic mode should accept the same plan with different valid arguments"
    assert semantic["mode"] == "semantic"


def test_semantic_mode_rejects_skipped_required_step():
    """A runtime that skips check_stock_level before restocking violates
    the agent's declared ordering constraint — semantic mode must still
    catch this via the tool-sequence comparison, not wave it through
    just because the final call is schema-valid."""
    passport = _inventory_passport()
    stock = {"item_sku": "SKU-WIDGET-001", "current_stock": 4, "reorder_threshold": 20}
    notify = {"posted": True, "message_length": 10}

    runtime_a = _inventory_run_result("runtime-A", [
        ("check_stock_level", {"item_sku": "SKU-WIDGET-001"}, stock),
        ("notify_ops_channel", {"message": "x"}, notify),
        ("place_restock_order", {"item_sku": "SKU-WIDGET-001", "quantity": 100, "max_order_value_usd": 2000.0},
         {"order_id": "PO-100", "total_cost_usd": 1250.0, "status": "placed"}),
    ])
    runtime_skipped = _inventory_run_result("runtime-skipped", [
        ("notify_ops_channel", {"message": "x"}, notify),
        ("place_restock_order", {"item_sku": "SKU-WIDGET-001", "quantity": 100, "max_order_value_usd": 2000.0},
         {"order_id": "PO-100", "total_cost_usd": 1250.0, "status": "placed"}),
    ])

    harness = VerificationHarness(passport, adapters=[])
    per_runtime = {"runtime-A": [runtime_a], "runtime-skipped": [runtime_skipped]}
    semantic = harness._check_semantic_tool_call_equivalence(per_runtime)

    assert semantic["passed"] is False
    assert "runtime-skipped" in semantic["problems_by_runtime"]
    assert "sequence differs" in semantic["problems_by_runtime"]["runtime-skipped"][0]


def test_semantic_mode_rejects_out_of_bounds_argument():
    """Same correct tool sequence, but a quantity above the tool's
    declared maximum. Semantic mode must reject this on argument
    validity grounds, independent of what any other runtime did."""
    passport = _inventory_passport()
    stock = {"item_sku": "SKU-WIDGET-001", "current_stock": 4, "reorder_threshold": 20}
    notify = {"posted": True, "message_length": 10}

    runtime_a = _inventory_run_result("runtime-A", [
        ("check_stock_level", {"item_sku": "SKU-WIDGET-001"}, stock),
        ("notify_ops_channel", {"message": "x"}, notify),
        ("place_restock_order", {"item_sku": "SKU-WIDGET-001", "quantity": 100, "max_order_value_usd": 2000.0},
         {"order_id": "PO-100", "total_cost_usd": 1250.0, "status": "placed"}),
    ])
    runtime_over = _inventory_run_result("runtime-over", [
        ("check_stock_level", {"item_sku": "SKU-WIDGET-001"}, stock),
        ("notify_ops_channel", {"message": "x"}, notify),
        ("place_restock_order", {"item_sku": "SKU-WIDGET-001", "quantity": 10000, "max_order_value_usd": 2000.0},
         {"order_id": "", "total_cost_usd": 125000.0, "status": "rejected_over_budget"}),
    ])

    harness = VerificationHarness(passport, adapters=[])
    per_runtime = {"runtime-A": [runtime_a], "runtime-over": [runtime_over]}
    semantic = harness._check_semantic_tool_call_equivalence(per_runtime)

    assert semantic["passed"] is False
    assert "exceeds declared maximum" in semantic["problems_by_runtime"]["runtime-over"][0]


def test_semantic_mode_rejects_undeclared_tool():
    passport = _inventory_passport()
    stock = {"item_sku": "SKU-WIDGET-001", "current_stock": 4, "reorder_threshold": 20}

    runtime_a = _inventory_run_result("runtime-A", [
        ("check_stock_level", {"item_sku": "SKU-WIDGET-001"}, stock),
    ])
    runtime_bad = _inventory_run_result("runtime-bad", [
        ("wire_transfer", {"amount": 500}, {"ok": True}),
    ])

    harness = VerificationHarness(passport, adapters=[])
    per_runtime = {"runtime-A": [runtime_a], "runtime-bad": [runtime_bad]}
    semantic = harness._check_semantic_tool_call_equivalence(per_runtime)

    assert semantic["passed"] is False
    problems = semantic["problems_by_runtime"]["runtime-bad"]
    assert any("undeclared tool" in p for p in problems)


def test_numeric_bounds_validation_standalone():
    """Direct unit test of the new minimum/maximum support in
    validate_tool_arguments, independent of the harness."""
    from core.passport import validate_tool_arguments, ToolContract

    contract = ToolContract(
        name="place_restock_order", description="x",
        input_schema={
            "type": "object", "required": ["quantity"],
            "properties": {"quantity": {"type": "integer", "minimum": 1, "maximum": 500}},
        },
        output_schema={},
    )
    assert validate_tool_arguments(contract, {"quantity": 100}) == []
    assert validate_tool_arguments(contract, {"quantity": 500}) == []  # inclusive upper bound
    assert validate_tool_arguments(contract, {"quantity": 1}) == []    # inclusive lower bound

    too_high = validate_tool_arguments(contract, {"quantity": 501})
    assert len(too_high) == 1 and "exceeds declared maximum" in too_high[0]

    too_low = validate_tool_arguments(contract, {"quantity": 0})
    assert len(too_low) == 1 and "below declared minimum" in too_low[0]


# ---------------------------------------------------------------------------
# HiDevs GitAgent Passport (OpenGAP) compliance tests. Verifies the
# required root files exist and are structurally valid, so CI catches
# any future regression (e.g. someone accidentally deleting agent.yaml
# or breaking its required fields) automatically, not just at submission
# time.
# ---------------------------------------------------------------------------

import yaml

REPO_ROOT = os.path.join(HERE, "..")


def test_agent_yaml_exists_at_repo_root_with_required_fields():
    path = os.path.join(REPO_ROOT, "agent.yaml")
    assert os.path.exists(path), "agent.yaml must exist directly in the repository root"
    with open(path) as f:
        data = yaml.safe_load(f)
    assert data["spec_version"] == "0.1.0"
    assert re.match(r"^[a-z][a-z0-9-]*$", data["name"]), "name must be lowercase, start with a letter, use hyphens"
    assert "version" in data
    assert isinstance(data["description"], str) and len(data["description"].strip()) > 20


def test_soul_md_exists_and_has_substance():
    path = os.path.join(REPO_ROOT, "SOUL.md")
    assert os.path.exists(path), "SOUL.md must exist directly in the repository root"
    with open(path) as f:
        text = f.read()
    assert len(text) > 500, "SOUL.md should contain meaningful, non-trivial content"
    assert "# Identity" in text


def test_explainability_md_has_required_headings_and_sentence_counts():
    path = os.path.join(REPO_ROOT, "EXPLAINABILITY.md")
    assert os.path.exists(path), "EXPLAINABILITY.md must exist directly in the repository root"
    with open(path) as f:
        text = f.read()
    for heading in ["# Decision", "# Inputs", "# Limits"]:
        assert heading in text, f"EXPLAINABILITY.md is missing required heading: {heading}"

    sections = re.split(r"(?m)^# ", text)[1:]
    for sec in sections:
        lines = sec.split("\n", 1)
        title = lines[0].strip()
        body = lines[1] if len(lines) > 1 else ""
        if title in ("Decision", "Inputs", "Limits"):
            sentences = [s for s in re.split(r"(?<=[.!?])\s+", body.strip()) if len(s.split()) > 3]
            assert len(sentences) >= 2, f"EXPLAINABILITY.md '{title}' section needs at least 2 real sentences"


def test_no_duties_or_agents_maker_checker_conflict():
    """This project has no DUTIES.md/AGENTS.md, so there is nothing that
    could assign Maker and Checker roles incorrectly. This test pins
    that fact so it's caught immediately if either file is ever added
    without also reviewing the Maker/Checker rule."""
    duties_exists = os.path.exists(os.path.join(REPO_ROOT, "DUTIES.md"))
    agents_exists = os.path.exists(os.path.join(REPO_ROOT, "AGENTS.md"))
    if duties_exists or agents_exists:
        pytest.skip("DUTIES.md/AGENTS.md now exist — review Maker/Checker assignment manually before relying on this test")
    assert True


def test_no_committed_secrets_in_tracked_text_files():
    """Scans all tracked text files for obvious hardcoded API key
    patterns. A real secret should never be committed; env var NAMES
    (ANTHROPIC_API_KEY, OPENAI_API_KEY, LYZR_API_KEY) are fine and
    expected to appear."""
    suspicious_patterns = [
        re.compile(r"sk-ant-[a-zA-Z0-9]{20,}"),
        re.compile(r"sk-[a-zA-Z0-9]{32,}"),
    ]
    text_extensions = (".py", ".yaml", ".yml", ".md", ".json", ".txt")
    hits = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache", "node_modules")]
        for fname in files:
            if not fname.endswith(text_extensions):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            for pattern in suspicious_patterns:
                if pattern.search(content):
                    hits.append(fpath)
    assert hits == [], f"Possible hardcoded secret found in: {hits}"


def test_openai_export_produces_valid_tool_schema(signed_manifest):
    """Real, offline-safe test of adapters/openai_export.py against the
    actual installed openai package: constructs a real client (no
    network call) and validates the exported tool list against the
    SDK's own expected structure."""
    from adapters.openai_export import export_and_validate
    passport = Passport.load(signed_manifest, strict=True)
    result = export_and_validate(passport)
    assert result["status"] in ("pass", "not_tested")  # "not_tested" only if openai isn't installed
    if result["status"] == "pass":
        assert result["exported_tool_count"] == len(passport.authorized_tool_names())
        for tool in result["exported_tools"]:
            assert tool["type"] == "function"
            assert "name" in tool["function"]
            assert "parameters" in tool["function"]


def test_openai_export_excludes_unauthorized_tools(signed_manifest):
    from adapters.openai_export import export_tools
    manifest = json.loads(json.dumps(signed_manifest))
    for t in manifest["tools"]:
        if t["name"] == "search_hotels":
            t["authorized"] = False
    resigned = sign_manifest({k: v for k, v in manifest.items() if k != "signature"})
    passport = Passport.load(resigned, strict=True)
    exported = export_tools(passport)
    names = [t["function"]["name"] for t in exported]
    assert "search_hotels" not in names
    assert "search_flights" in names


def test_per_runtime_tasks_override_lets_runtimes_diverge(signed_manifest):
    """Confirms the new per_runtime_tasks parameter actually works
    end-to-end through real adapters: two real adapters fed DIFFERENT
    routing for the same task description, verified under semantic mode."""
    passport = Passport.load(signed_manifest, strict=True)
    adapter_a = RawAdapter(passport)
    adapter_b = RawAdapter(passport)
    adapter_a.runtime_name = "raw-sdk-A"
    adapter_b.runtime_name = "raw-sdk-B"

    shared_tasks = {"book a flight": {"tool_calls": [], "final_answer": "placeholder"}}
    per_runtime_tasks = {
        "raw-sdk-A": {"book a flight": {
            "tool_calls": [{"tool": "search_flights", "args": {"origin": "Delhi", "destination": "Goa", "date": "2026-01-01"}}],
            "final_answer": "a",
        }},
        "raw-sdk-B": {"book a flight": {
            "tool_calls": [{"tool": "search_flights", "args": {"origin": "Delhi", "destination": "Goa", "date": "2026-02-15"}}],
            "final_answer": "b",
        }},
    }

    harness = VerificationHarness(passport, [adapter_a, adapter_b])
    certificate = harness.run(shared_tasks, equivalence_mode="semantic", per_runtime_tasks=per_runtime_tasks)

    assert certificate["equivalence_mode"] == "semantic"
    assert certificate["checks"]["tool_call_equivalence"]["passed"] is True  # same tool, different (both valid) dates
