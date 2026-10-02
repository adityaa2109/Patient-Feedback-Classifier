# Explainability

This document is a comprehensive transparency report for Agent Passport.
It covers how the agent decides, what data it consumes and where that
data comes from, the exact algorithms and formulas behind its checks,
its data lineage from input manifest to output certificate, the
recognized safety frameworks its threat model maps to, and its concrete,
honestly-stated limitations. Every claim below points at the real file
and function responsible for it — nothing here is aspirational or
generic boilerplate.

# Decision

The agent's core decision is binary and rule-based, never a free-form
judgment call. For each verification run it answers two questions: is
the target agent's manifest **valid** (signature intact, behavior-contract
hash matches, identity block complete), and is every tested runtime
**compliant** (every tool call was declared, authorized, schema-valid,
and — depending on the chosen equivalence mode — either byte-identical
to every other runtime's trace or following the same tool sequence with
independently valid arguments).

This happens in `verification/harness.py`'s `VerificationHarness.run()`,
which executes four independent, deterministic checks per run:

1. **Tool-call equivalence** — `_check_tool_call_equivalence` (exact
   mode, the default) requires every runtime's `(tool_name, arguments,
   result)` tuple sequence to be identical to a baseline runtime's
   sequence. `_check_semantic_tool_call_equivalence` (opt-in mode)
   instead requires the ordered sequence of *tool names only* to match,
   while each individual call's arguments only need to independently
   pass schema and bounds validation — this accepts two live-model calls
   that reasonably chose different-but-valid arguments (e.g. a restock
   quantity of 90 vs. 100, both within a declared 1–500 bound) while
   still rejecting a call that skips a required step or uses the wrong
   tool entirely.
2. **Constraint compliance** — `_check_constraint_compliance` collects
   every `constraint_violations` entry an adapter recorded (e.g. "max
   tool calls exceeded", "tool not in declared passport tools") across
   all runtimes; any non-empty list fails this check.
3. **Tool contract compliance** —
   `_check_declared_and_authorized_tools_only` fails if any executed
   tool call used a tool name that is either not declared in the
   manifest at all, or declared but currently marked `authorized: false`
   (a de-authorized tool is treated identically to an undeclared one).
4. **Tool argument validity** — `_check_tool_argument_validity` runs
   `core/passport.py`'s `validate_tool_arguments()` against every
   observed call: required fields present, declared JSON Schema `type`
   respected, and declared numeric `minimum`/`maximum` bounds respected.

A certificate is only emitted if **all four** checks pass across **all**
tested runtimes; otherwise `VerificationHarness.run()` raises
`VerificationFailure` carrying the exact check name and the specific
reason it failed, rather than a generic rejection message. The decision
is additionally combined with the manifest's own static integrity
decision from `Passport.verify_full()` (signature, behavior-contract
hash, identity completeness, no duplicate tool names) into one
`VerificationResult` object with an `overall` boolean and an itemized
`violations` list, so a caller never has to guess *which* of several
possible problems actually occurred.

When live Claude-based tool selection is enabled (`core/llm_router.py`),
the model's decision is treated as just another candidate route subject
to the exact same four checks — it is never granted special trust. A
defensive `_normalize_route` step only repairs superficial formatting
noise (tool-name casing mismatches, numeric arguments returned as JSON
strings) and deliberately leaves a genuinely unrecognized tool name
uncorrected, so an undeclared or hallucinated tool call still fails
check 3 above rather than being silently patched into a false pass.

# Inputs

The agent's inputs are entirely explicit and file- or environment-based,
with no hidden state and no arbitrary file reads:

1. **A signed agent manifest**, in the format defined by
   `spec/agent_passport.schema.json`: the target agent's `identity`
   (agent_id, name, version, purpose, description), its
   `behavior_contract` (system prompt, a SHA-256 hash of that prompt,
   declared constraints, `max_tool_calls_per_task`), its `tools` array
   (name, description, `input_schema` as standard JSON Schema including
   optional `minimum`/`maximum` bounds, `output_schema`, `side_effects`
   classification of `none`/`read`/`write`/`external`, and an
   `authorized` boolean), its `capabilities` block, and a `signature`
   block (`algorithm: hmac-sha256`, the signature `value`, and which
   fields were signed).
2. **A set of task descriptions paired with routing instructions** —
   either a fixed, hand-written deterministic route
   (`{"tool_calls": [...], "final_answer": "..."}`), or, only when
   `ANTHROPIC_API_KEY` is present in the environment, a live response
   from the Anthropic Messages API describing which tool(s) to call and
   with what arguments. `core/llm_router.py: build_task_set()` tries the
   live route first per task and falls back to the deterministic route
   on any failure (missing key, network error, unparseable response),
   reporting which mode actually ran as `"live-llm"`, `"mixed"`, or
   `"deterministic"`.
3. **The runtime adapters under `adapters/`** (`raw_adapter.py`,
   `langchain_adapter.py`, `crewai_adapter.py`, `llamaindex_adapter.py`),
   which translate the manifest's declared tools into each framework's
   native tool-registration shape and execute the routed calls against
   the shared, deterministic backend functions in `core/tool_backends.py`.

The agent does not accept free-text instructions from an end user during
a verification run, does not browse the filesystem beyond the manifest
and task files it is explicitly given, and does not retain state between
runs beyond writing its own output (a `.VERIFIED.json` file and an
optional printed certificate) back to disk.

# Data Flow

Data moves through the system in one direction, with no implicit
feedback loop: **manifest → signature check → per-runtime execution →
verification checks → certificate → (optional) stamped-back manifest.**

Concretely: an unsigned manifest is hashed and signed by
`sign_manifest()` (`core/passport.py`), producing a `behavior_contract.system_prompt_hash`
and a `signature.value` computed as
`HMAC-SHA256(secret, canonical_json({passport_version, identity,
behavior_contract, tools, capabilities}))`, where `canonical_json` sorts
keys and uses compact separators so the signature is stable regardless
of dict ordering. `Passport.load(manifest, strict=True)` recomputes both
the hash and the signature and raises `ValueError` on any mismatch
before the manifest is ever treated as valid.

Once loaded, the same `Passport` object is handed to every configured
adapter; each adapter builds its framework-native tool set from
`passport.authorized_tool_names()` and `TOOL_REGISTRY`
(`core/tool_backends.py`), so every runtime executes the *same*
deterministic backend function for a given tool name — the only thing
that can differ between runtimes is which tool a model or route chose to
call and with what arguments, not what that tool actually does once
called. `VerificationHarness.run()` collects each adapter's
`RunResult` (a list of `ToolCallRecord(tool_name, arguments, result)`
plus any `constraint_violations`), runs the four checks described under
Decision, and serializes the outcome into a certificate dict that is
hashed again (`sha256_hex(json.dumps(certificate, sort_keys=True))`) to
produce a `certificate_hash` — a single value that changes if any part
of the certificate's content changes, giving a tamper-evident record of
the verification result itself, not just the original manifest.

# Safety and Security Considerations

This project's threat model centers on manifest integrity rather than
general application security, and maps most directly to **OWASP's LLM
Top 10 risk LLM01 (Prompt Injection) and the adjacent risk of insecure
output handling**, since a tampered system prompt or a smuggled tool
declaration are both forms of an agent's effective instructions being
altered without authorization. `verification/tamper_demo.py` exercises
seven concrete attack patterns against this threat model — an
unauthorized tool injected post-signing, an authorized tool silently
removed, an existing tool's schema or side-effects modified, the system
prompt rewritten, declared constraints stripped, identity metadata
swapped to impersonate a different agent, and a "stale signature" attack
where the prompt and its hash are both updated consistently with each
other but the signature itself is never recomputed — and confirms all
seven are rejected at `Passport.load(..., strict=True)`, before a
tampered agent ever has the chance to execute a single task.

Consistent with a least-privilege posture, every declared tool carries
an explicit `side_effects` classification (`none`/`read`/`write`/`external`)
and an `authorized` flag that can revoke a tool without deleting its
contract, and both the harness and every adapter treat a de-authorized
tool identically to an undeclared one — a defense-in-depth choice so
that disabling a capability can never be silently bypassed by any single
runtime. No secrets are read from or written into any committed file;
the only credential the agent ever consults is `ANTHROPIC_API_KEY`, read
exclusively from the process environment, used for exactly one narrowly
-scoped, optional call, and never logged or echoed back in any output.

# Limits

The agent's verification proves two specific, narrow things and nothing
beyond them: that a manifest's bytes are unchanged since signing, and
that tested runtimes behaved according to its own four rule-based
checks. It does **not** and cannot prove that the verified agent's
underlying behavior is safe, unbiased, or free of prompt-injection risk
from live user input at runtime — signing covers content integrity, not
behavioral quality, and this boundary is stated explicitly in this
repository's `README.md` security-model section and must never be
overstated as a safety guarantee.

The live-LLM tool-selection path depends on an external service (the
Anthropic API) and a real API key; without one, it automatically falls
back to deterministic replay, so any claim of "live" verification is
only as strong as whichever mode actually ran, which is reported
explicitly in each run's `equivalence_mode` field and `routing_mode`
output rather than assumed. `validate_tool_arguments()` is a lightweight
structural and numeric-bounds checker, not a full JSON Schema
implementation, so it can miss domain-specific invalid values — for
example, a syntactically valid but nonexistent item SKU — that a
complete validator would catch. Finally, the agent's cross-framework
export testing (see `README.md`'s HiDevs Agent Passport section) is
itself bounded by what could be genuinely executed in a given
environment: a schema-level export can be validated offline without an
API key, but a live, authenticated round-trip through a given
framework's own hosted service was not claimed as tested unless it
actually was.
