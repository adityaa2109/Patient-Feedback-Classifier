# Explainability

Agent Passport is a verification agent. It does not chat with end users; it
checks whether a *different* AI agent, described by a signed manifest, is
still the agent that was signed and behaves the same in every runtime. This
report explains what it decides, what it consumes, and where it stops being
reliable. Every claim points at a real file and function in this repository.

# Decision and Reasoning: How It Decides

## What the agent decides

The decision is binary and rule-based, never a free-form judgment. For each
run it answers two questions: is the manifest **valid** (signature intact,
behavior-contract hash matches, identity block complete, no duplicate tool
names), and is every tested runtime **compliant** (every tool call declared,
authorized, schema-valid, and equivalent across runtimes). The output is a
pass/fail certificate, not a score, so there are no weights, thresholds, or
probabilities to interpret.

## How it decides (algorithms and formulas)

`VerificationHarness.run()` in `verification/harness.py` runs four
independent, deterministic checks:

1. **Tool-call equivalence.** In exact mode, for runtimes R1..Rn the check
   passes iff `trace(Ri) == trace(R1)` for every i, where a trace is the
   ordered list of `(tool_name, arguments, result)` tuples. In semantic mode
   it passes iff the ordered tool names match and every call independently
   satisfies `validate_tool_arguments(tool, args) == []`.
2. **Constraint compliance.** Fails if any adapter recorded a
   `constraint_violations` entry, for example exceeding
   `max_tool_calls_per_task`.
3. **Tool contract compliance.** Fails if any executed tool is undeclared or
   has `authorized: false`. A de-authorized tool is treated exactly like an
   undeclared one.
4. **Tool argument validity.** Fails if a required field is missing, an
   argument is not declared in `input_schema`, the value's type does not
   match the declared `type`, or a number is below `minimum` or above
   `maximum`.

The manifest itself is checked in `core/passport.py`:

- `system_prompt_hash = SHA-256(system_prompt)`
- `signature = HMAC-SHA256(secret, canonical_json({passport_version,
  identity, behavior_contract, tools, capabilities}))`, with sorted keys and
  compact separators so dict ordering never changes the result.
- Verification recomputes both and compares signatures with
  `hmac.compare_digest` (constant-time).

The overall verdict is `overall = check_1 AND check_2 AND check_3 AND
check_4 AND manifest_valid`. The certificate is stamped with
`certificate_hash = SHA-256(json.dumps(certificate, sort_keys=True))`, so any
change to the certificate changes the hash.

## Why the decision can be trusted or challenged

Every failure raises `VerificationFailure` naming the exact check and
reason, and `VerificationResult.violations` lists each problem, so a reviewer
can see which rule failed instead of a generic rejection. The decision logic
is covered by `tests/test_passport.py` (51 tests), including seven tamper
scenarios in `verification/tamper_demo.py`.

## Human oversight

The agent never acts on the verified agent; it only reports. A person
decides what to do with a failed certificate. A passing certificate should
be treated as evidence about manifest integrity and runtime consistency, not
as approval to deploy.

## Role of the live model

When `core/llm_router.py` uses Claude to choose tools, that choice is just a
candidate route. It is subject to the same four checks and gets no special
trust. `_normalize_route` repairs only formatting noise (tool-name casing,
numbers returned as strings) and never corrects an unknown tool name, so a
hallucinated tool still fails check 3.

# Inputs and Data Sources: Data Used

## What goes in

1. **A signed agent manifest** (`spec/agent_passport.schema.json`):
   `identity`, `behavior_contract` (system prompt, hash, constraints,
   `max_tool_calls_per_task`), `tools` (name, description, JSON-Schema
   `input_schema` with optional `minimum`/`maximum`, `output_schema`,
   `side_effects` of none/read/write/external, `authorized` flag),
   `capabilities`, and a `signature` block.
2. **Tasks with routing instructions**, either a fixed deterministic route
   or, only when `ANTHROPIC_API_KEY` is set, a live response from the
   Anthropic Messages API. `build_task_set()` tries live first and falls back
   to deterministic on any failure, and reports `"live-llm"`, `"mixed"` or
   `"deterministic"`.
3. **Runtime adapters** in `adapters/` (raw SDK, LangChain, CrewAI,
   LlamaIndex) that turn declared tools into each framework's format and run
   them against `core/tool_backends.py`.

## Data sources

All data is local to the repository or the process environment: manifests
in `examples/`, the schema in `spec/`, deterministic in-memory tool backends
in `core/tool_backends.py`, and the optional Anthropic API. No training
data, scraped data, or third-party datasets are used.

## Privacy and sensitive data

The agent processes no personal data and no end-user content. No secrets are
committed; `ANTHROPIC_API_KEY` is read only from the environment and never
logged or echoed, and `test_no_committed_secrets_in_tracked_text_files`
enforces this on every CI run. The signing secret in `core/passport.py` is a
demo placeholder, overridable for real use.

## Data lineage

Data flows one way: manifest → `sign_manifest()` (adds hash and signature)
→ `*.SIGNED.json` → `Passport.load(strict=True)` (recomputes and rejects on
mismatch) → the same `Passport` object handed to every adapter → each
adapter returns a `RunResult` of `ToolCallRecord(tool_name, arguments,
result)` plus violations → harness checks → certificate with
`certificate_hash` → `stamp_verification()` → `*.VERIFIED.json`. Each stage
writes a new artifact instead of mutating its input, and `equivalence_mode`
and `routing_mode` record which path produced the result. The agent accepts
no free-text instructions during a run and keeps no state between runs.

## Input handling and security

`verification/tamper_demo.py` shows seven attacks being rejected at load
time: an unauthorized tool injected after signing, an authorized tool
removed, a tool's schema or side-effects modified, the system prompt
rewritten, constraints stripped, identity swapped to impersonate another
agent, and a stale-signature attack where prompt and hash are updated
consistently but the signature is not recomputed.

## Frameworks this maps to

The mapping shows which mechanism addresses part of each control. It is not
a certification or a claim of full compliance.

- **OWASP Top 10 for LLM Applications:** LLM01 Prompt Injection (tampered
  instructions are detected by the signed prompt and hash), LLM05 Supply
  Chain (altered components change the signature), and LLM06 Excessive
  Agency (only declared, authorized tools with a `side_effects` class).
- **NIST AI RMF 1.0:** GOVERN (this documentation), MEASURE (repeatable
  cross-runtime testing), MANAGE (de-authorizing a tool without deleting its
  contract).
- **MITRE ATLAS:** the tamper-demo attacks correspond to tampering with an
  AI agent's configuration.
- **NIST FIPS 198-1 and FIPS 180-4:** HMAC and SHA-256 as the signing and
  hashing primitives.
- **EU AI Act (transparency and record-keeping principles):** hash-stamped
  certificates give a traceable record. This project does not claim to be a
  regulated high-risk system.

# Limitations, Constraints and Known Issues

## What the verification proves

It proves two narrow things: a manifest's bytes are unchanged since signing,
and the tested runtimes behaved according to this agent's own four rules. It
does **not** prove the verified agent is safe, unbiased, accurate, or free of
prompt-injection risk from live user input, and it must never be presented as
a safety guarantee.

## Known weaknesses

- **Shared secret.** HMAC means anyone holding the secret can re-sign a
  modified manifest. The repository secret is a demo placeholder; real use
  needs a secret manager or asymmetric signatures.
- **Lightweight validation.** `validate_tool_arguments()` checks required
  fields, types, and numeric bounds, not full JSON Schema. It can miss
  domain-specific invalid values, such as a well-formed but nonexistent SKU.
- **Live mode depends on an external service.** Without an API key it falls
  back to deterministic replay, so a "live" claim is only as strong as the
  mode that actually ran.
- **Deterministic tools.** Backends return fixed results, so the agent tests
  routing consistency, not real-world tool behavior.
- **Bias and fairness.** The checks are structural and do not evaluate
  content, so bias or harmful output in the verified agent is out of scope.

## Failure behavior

On any failed check the agent stops and raises `VerificationFailure` with the
check name and reason; it never emits a certificate for a partial pass. If
the live model route fails, it falls back to the deterministic route rather
than guessing.

## Intended and prohibited use

Intended use is integrity and portability verification of agent manifests
during development, review, and CI. It should not be used as the sole basis
for approving an agent for high-stakes domains (legal, medical, financial),
or as proof of regulatory compliance.

## Export test coverage

Cross-framework export was tested for CrewAI and the OpenAI SDK (schema
export only, no live call). Claude Code and Lyzr were not fully tested, as
stated in `README.md`.
