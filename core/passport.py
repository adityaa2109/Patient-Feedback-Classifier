"""
core/passport.py

The heart of the Agent Passport system:
- Load/validate a manifest against the schema
- Compute the behavior-contract hash (drift detection)
- Sign and verify manifests with HMAC-SHA256
- Provide a single canonical `Passport` object every adapter consumes

This module has zero framework dependencies on purpose: LangChain, CrewAI,
or a raw SDK agent should all be able to import this file without pulling
in anything heavy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import copy
import datetime
from dataclasses import dataclass, field
from typing import Any


DEFAULT_SECRET = b"agent-passport-challenge-demo-key"  # override via env in real deployments
SIGNED_FIELDS = ["passport_version", "identity", "behavior_contract", "tools", "capabilities"]


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_payload(manifest: dict) -> str:
    """Deterministic JSON serialization of the fields we sign, so signing
    is stable regardless of dict key order."""
    payload = {k: manifest.get(k) for k in SIGNED_FIELDS}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sign_manifest(manifest: dict, secret: bytes = DEFAULT_SECRET) -> dict:
    """Return a copy of the manifest with system_prompt_hash filled in and
    a valid signature block attached."""
    manifest = copy.deepcopy(manifest)

    # Freeze the behavior contract hash before signing.
    system_prompt = manifest["behavior_contract"]["system_prompt"]
    manifest["behavior_contract"]["system_prompt_hash"] = sha256_hex(system_prompt)

    payload = _canonical_payload(manifest)
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    manifest["signature"] = {
        "algorithm": "hmac-sha256",
        "value": digest,
        "signed_fields": SIGNED_FIELDS,
    }
    return manifest


def verify_signature(manifest: dict, secret: bytes = DEFAULT_SECRET) -> bool:
    """Recompute the signature and compare. True == manifest is untampered."""
    if "signature" not in manifest:
        return False
    expected = manifest["signature"]["value"]
    payload = _canonical_payload(manifest)
    actual = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, actual)


def verify_behavior_contract_integrity(manifest: dict) -> bool:
    """Confirms the stored system_prompt_hash actually matches the
    system_prompt text still present in the manifest (detects hand-edits
    that forgot to re-sign)."""
    contract = manifest["behavior_contract"]
    return sha256_hex(contract["system_prompt"]) == contract["system_prompt_hash"]


@dataclass
class ToolContract:
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    side_effects: str = "none"


@dataclass
class Passport:
    """Runtime-friendly view of a manifest. Every adapter builds its
    framework-specific agent FROM this object, never from the raw dict,
    so there's exactly one source of truth."""

    agent_id: str
    name: str
    version: str
    purpose: str
    system_prompt: str
    constraints: list[str]
    max_tool_calls_per_task: int
    tools: list[ToolContract]
    capabilities: dict
    raw_manifest: dict = field(repr=False, default_factory=dict)

    @classmethod
    def load(cls, manifest: dict, secret: bytes = DEFAULT_SECRET, strict: bool = True) -> "Passport":
        if strict:
            if not verify_signature(manifest, secret):
                raise ValueError("Passport signature invalid — manifest may be tampered.")
            if not verify_behavior_contract_integrity(manifest):
                raise ValueError("behavior_contract hash mismatch — prompt drift detected.")

        identity = manifest["identity"]
        contract = manifest["behavior_contract"]
        tools = [ToolContract(**t) for t in manifest.get("tools", [])]

        return cls(
            agent_id=identity["agent_id"],
            name=identity["name"],
            version=identity["version"],
            purpose=identity["purpose"],
            system_prompt=contract["system_prompt"],
            constraints=contract.get("constraints", []),
            max_tool_calls_per_task=contract.get("max_tool_calls_per_task", 10),
            tools=tools,
            capabilities=manifest.get("capabilities", {}),
            raw_manifest=manifest,
        )

    @classmethod
    def load_file(cls, path: str, secret: bytes = DEFAULT_SECRET, strict: bool = True) -> "Passport":
        with open(path, "r") as f:
            manifest = json.load(f)
        return cls.load(manifest, secret=secret, strict=strict)

    def stamp_verification(self, verified_runtimes: list[str]) -> dict:
        """Produce an updated manifest dict with a verification block
        filled in. Does NOT re-sign — verification is evidence appended
        after the fact, layered on top of the original signed identity."""
        manifest = copy.deepcopy(self.raw_manifest)
        vhash_input = json.dumps(sorted(verified_runtimes)) + self.raw_manifest["signature"]["value"]
        manifest["verification"] = {
            "verified_runtimes": verified_runtimes,
            "last_verified_at": datetime.datetime.utcnow().isoformat() + "Z",
            "verification_hash": sha256_hex(vhash_input),
        }
        return manifest
