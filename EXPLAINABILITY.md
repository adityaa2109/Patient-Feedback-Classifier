# Explainability

This document is a transparency report for Agent Passport. It explains how
the agent decides, what data it consumes and where that data comes from, the
exact algorithms and formulas behind its checks, the lineage of data from
input manifest to output certificate, the recognized safety frameworks its
threat model maps to, and its honestly-stated limitations. Every claim
points at a real file and function in this repository.

# Decision

The agent's core decision is binary and rule-based, never a free-form
judgment call. For each verification run it answers two questions: is the
target agent's manifest **valid** (signature intact, behavior-contract hash
matches, identity block complete), and is every tested runtime **compliant**
(every tool call was declared, authorized, schema-valid, and either
identical across runtimes or following the same tool sequence with
independently valid arguments).

The decision is made in `verification/harness.py` by
`VerificationHarness.run()`, which executes four independent, deterministic
checks:

1. **Tool-call equivalence** — `_check_tool_call_equivalence` (exact mode,
   the default) requires every runtime's `(tool_name, arguments, result)`
   sequence to equal a baseline runtime's sequence.
   `_check_semantic_tool_call_equivalence` (opt-in) requires only the
   ordered tool names to match, while each call's arguments must
   independently pass schema and bounds validation.
2. **Constraint compliance** — `_check_constraint_compliance` fails if any
   adapter recorded a `constraint_violations` entry (e.g. max tool calls
   exceeded).
3. **Tool contract compliance** — `_check_declared_and_authorized_tools_only`
   fails if any executed tool is undeclared or marked `authorized: false`.
4. **Tool argument validity** — `_check_tool_argument_validity` runs
   `validate_tool_arguments()` from `core/passport.py` on every observed
   call.

A certificate is emitted only if all four checks pass across all tested
runtimes. Otherwise `VerificationFailure` is raised with the exact check
name and reason. The result is combined with the manifest's static integrity
decision from `Passport.verify_full()` into one `VerificationResult` with an
`overall` boolean and an itemized `violations` list.

When live Claude-based tool selection is enabled (`core/llm_router.py`), the
model's choice is treated as just another candidate route and is subject to
the same four checks. `_normalize_route` repairs only formatting noise
(tool-name casing, numeric strings) and never patches an unknown tool name,
so a hallucinated tool still fails check 3.

# Inputs

All inputs are explicit and file- or environment-based. There is no hidden
state and no arbitrary file reading.

1. **A signed agent manifest** defined by `spec/agent_passport.schema.json`:
   `identity`, `behavior_contract` (system prompt, SHA-256 hash, constraints,
   `max_tool_calls_per_task`), `tools` (name, description, JSON-Schema
   `input_schema` with optional `minimum`/`maximum`, `output_schema`,
   `side_effects`, `authorized`), `capabilities`, and a `signature` block.
2. **A set of tasks with routing instructions** — either a fixed
   deterministic route or, only if `ANTHROPIC_API_KEY` is set, a live route
   from the Anthropic Messages API. `build_task_set()` in
   `core/llm_router.py` tries live first and falls back to deterministic on
   any failure, reporting `"live-llm"`, `"mixed"`, or `"deterministic"`.
3. **Runtime adapters** in `adapters/` (raw SDK, LangChain, CrewAI,
   LlamaIndex) that translate declared tools into each framework's format
   and execute them against `core/tool_backends.py`.

The agent accepts no free-text instructions from an end user during a run
and retains no state between runs beyond writing its `.VERIFIED.json` output.

# Data Sources

Every data source is local to the repository or the process environment.

| Source | Origin | Used by |
|---|---|---|
| Agent manifests | `examples/*.manifest.json`, `*.SIGNED.json` | `Passport.load()` |
| Manifest schema | `spec/agent_passport.schema.json` | manifest structure |
| Tool backends | `core/tool_backends.py` (deterministic, in-memory) | all adapters |
| Task definitions | `core/llm_router.py: build_task_set()` | harness |
| Live model output (optional) | Anthropic Messages API, only if `ANTHROPIC_API_KEY` is set | router |
| Signing secret | `DEFAULT_SECRET` in `core/passport.py` (demo placeholder), overridable | sign/verify |

No training data, user personal data, or third-party datasets are used. Tool
backends return fixed, deterministic results, so differences between
runtimes can only come from routing, not from the tools.

# Algorithms and Formulas

**Canonical payload.** `_canonical_payload` serializes
`{passport_version, identity, behavior_contract, tools, capabilities}` as
JSON with sorted keys and compact separators, so ordering never changes the
signature.

**Prompt hash.** `system_prompt_hash = SHA-256(system_prompt)`
(`sha256_hex`). `verify_behavior_contract_integrity` passes only if
`SHA-256(system_prompt) == system_prompt_hash`.

**Signature.** `signature = HMAC-SHA256(secret, canonical_payload)`
(`sign_manifest`). `verify_signature` recomputes it and compares using
`hmac.compare_digest` (constant-time).

**Exact equivalence.** For runtimes `R1..Rn`, the check passes iff for every
`i`, `trace(Ri) == trace(R1)`, where a trace is the ordered list of
`(tool_name, arguments, result)` tuples.

**Semantic equivalence.** Passes iff for every `i`,
`names(trace(Ri)) == names(trace(R1))` and, for every call `c`,
`validate_tool_arguments(tool(c), args(c)) == []`.

**Argument validity.** `validate_tool_arguments` reports a problem when a
required field is missing, an argument is not declared in `input_schema`,
the Python value does not match the declared `type`, `value < minimum`, or
`value > maximum`.

**Certificate hash.**
`certificate_hash = SHA-256(json.dumps(certificate, sort_keys=True))`,
computed over the certificate before the hash field is added. Changing any
field changes the hash. The manifest-level `verification_hash` produced by
`Passport.stamp_verification` uses the same SHA-256 construction.

**Overall verdict.** `overall = AND(check_1, check_2, check_3, check_4)`
combined with the static manifest checks. There are no weights, scores, or
probabilistic thresholds; every verdict is a boolean.

# Data Lineage

Data moves in one direction with no feedback loop:

`manifest (examples/*.manifest.json)` → `sign_manifest()` adds
`system_prompt_hash` and `signature` → `*.SIGNED.json` →
`Passport.load(strict=True)` recomputes hash and signature and raises
`ValueError` on mismatch → the same `Passport` object is passed to every
adapter → each adapter builds its framework-native tools from
`authorized_tool_names()` and `TOOL_REGISTRY` → each adapter returns a
`RunResult` of `ToolCallRecord(tool_name, arguments, result)` plus
`constraint_violations` → `VerificationHarness.run()` applies the four
checks → certificate dict with `certificate_hash` →
`stamp_verification()` → `*.VERIFIED.json`.

Each stage writes a new artifact rather than mutating its input, so any
output can be traced back to the exact manifest bytes and the exact run that
produced it. The `routing_mode` and `equivalence_mode` fields record which
path (deterministic, mixed, or live) produced a result.

# Safety and Security Considerations

The threat model centers on manifest integrity: a tampered system prompt or
a smuggled tool declaration changes an agent's effective instructions
without authorization. `verification/tamper_demo.py` exercises seven attack
patterns and confirms all are rejected at `Passport.load(..., strict=True)`:
an unauthorized tool injected after signing, an authorized tool silently
removed, a tool's schema or side-effects modified, the system prompt
rewritten, constraints stripped, identity swapped to impersonate another
agent, and a stale-signature attack where the prompt and hash are updated
consistently but the signature is not recomputed.

Every tool carries a `side_effects` class (`none`/`read`/`write`/`external`)
and an `authorized` flag, and a de-authorized tool is treated exactly like
an undeclared one by both the harness and every adapter. No secrets are
committed; `ANTHROPIC_API_KEY` is read only from the environment and never
logged, and `test_no_committed_secrets_in_tracked_text_files` enforces this
in CI.

# Safety Frameworks

The design maps to the following recognized frameworks. "Maps to" means the
mechanism addresses part of the control; it does not mean certification or
full compliance.

| Framework | Relevant item | How this project addresses it |
|---|---|---|
| OWASP Top 10 for LLM Applications | LLM01 Prompt Injection (tampered instructions), LLM05 Supply Chain, LLM06 Excessive Agency | Signed system prompt and hash detect instruction tampering; signed manifest detects altered components; declared, authorized-only tools with `side_effects` limit agency |
| NIST AI RMF 1.0 | MEASURE (testing and evaluation), MANAGE (risk controls), GOVERN (documentation) | Repeatable cross-runtime verification, tamper demo, and this transparency document |
| NIST SP 800-107 / FIPS 198-1 | HMAC and SHA-256 usage | HMAC-SHA256 with constant-time comparison |
| EU AI Act (transparency and record-keeping principles) | Technical documentation, traceability | Hash-stamped certificates and this document provide traceable records; this project does not claim to be a regulated high-risk system |
| MITRE ATLAS | Tampering with AI agent configuration | The seven tamper-demo attacks correspond to configuration-tampering techniques |

# Limits

The agent's verification proves two narrow things and nothing beyond them:
that a manifest's bytes are unchanged since signing, and that the tested
runtimes behaved according to its own four rule-based checks. It does not
prove that the verified agent is safe, unbiased, or free of prompt-injection
risk from live user input at runtime, and this boundary must never be
overstated as a safety guarantee.

HMAC is a shared-secret scheme, so anyone holding the secret can re-sign a
modified manifest; the repository's secret is a demo placeholder, and real
use needs a secret manager or asymmetric signatures. The live-LLM path
depends on an external API and key, and falls back to deterministic replay
without one, so any "live" claim is only as strong as the mode that actually
ran. `validate_tool_arguments()` is a lightweight structural and bounds
checker, not a full JSON Schema implementation, so it can miss
domain-specific invalid values such as a well-formed but nonexistent SKU.
Cross-framework export testing is limited to what was genuinely executed:
CrewAI and the OpenAI schema export passed, while Claude Code and Lyzr were
not fully tested, as stated in `README.md`.
