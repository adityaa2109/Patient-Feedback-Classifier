# Explainability

This document explains how Agent Passport actually decides, what it
actually reads, and what it actually cannot guarantee. Every claim below
points at the real file and function responsible for it.

# Decision

The agent's core decision is binary and rule-based, never a free-form
judgment call: for each verification run, it decides whether a target
agent's manifest is **valid** (signature intact, behavior-contract hash
matches, identity block complete) and whether every tested runtime is
**compliant** (every tool call was declared, authorized, schema-valid,
and — depending on the chosen equivalence mode — either byte-identical
to every other runtime's trace or following the same tool sequence with
independently valid arguments). This happens in
`verification/harness.py`'s `VerificationHarness.run()`, which calls
four independent checks (`_check_tool_call_equivalence` or
`_check_semantic_tool_call_equivalence`, `_check_constraint_compliance`,
`_check_declared_and_authorized_tools_only`,
`_check_tool_argument_validity`) and only emits a passing certificate if
all four agree; otherwise it raises `VerificationFailure` with the exact
check and reason that failed, rather than a generic rejection. When live
Claude-based tool selection is enabled (`core/llm_router.py`), the
model's decision is treated as just another candidate route subject to
the same four checks — the agent never grants it special trust, and a
defensive `_normalize_route` step only repairs superficial formatting
noise (tool-name casing, numeric-string arguments) without ever
expanding what is authorized.

# Inputs

The agent's inputs are entirely explicit and file- or environment-based,
with no hidden state: (1) a signed agent manifest in the format defined
by `spec/agent_passport.schema.json`, containing the target agent's
identity, system prompt, declared tools with their JSON-schema input
contracts, and constraints; (2) a set of task descriptions paired with
either a fixed deterministic route or, optionally, a live response from
the Anthropic API when `ANTHROPIC_API_KEY` is present in the
environment; and (3) the runtime adapters under `adapters/` that
translate those declared tools into each framework's native tool
format. The agent does not read arbitrary files, does not accept
free-text instructions from an end user during verification, and does
not use any input that is not one of these three explicitly-typed
sources.

# Limits

The agent's verification only proves that a manifest's bytes are
unchanged since signing and that tested runtimes behaved according to
its own four rule-based checks; it does not and cannot prove that the
verified agent's underlying behavior is safe, unbiased, or free of
prompt-injection risk, since signing covers content integrity, not
behavioral quality — this is stated explicitly in `README.md`'s security
model section and must not be overstated. The live-LLM tool-selection
path depends on an external service (the Anthropic API) and a real API
key; without one, it automatically falls back to deterministic replay,
which means any claim of "live" verification is only as strong as
whichever mode actually ran and is reported in each run's `equivalence_mode`
field. Finally, `validate_tool_arguments()` is a lightweight structural
and numeric-bounds checker, not a full JSON Schema implementation, so it
can miss domain-specific invalid values (for example, a syntactically
valid but nonexistent item SKU) that a complete validator would catch.
