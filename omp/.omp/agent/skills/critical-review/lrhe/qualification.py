#!/usr/bin/env python3
"""Fail-closed reader for lead-relative critical-review qualification.

Schema v9 selects one live profile for one of two qualified accountable lead
families: GPT/ChatGPT or Claude. Each profile has exactly three concepts:
strongCritic, always-on supplements, and record-selected
architectureSpecialists. The strong critic is reciprocal cross-family
independent evidence. Gemini Flash and Grok are cross-family supplemental
evidence. Eligible Fable architecture synthesis is always supplemental, with
lead-relative lineage recorded honestly.

Reviewer identity is not model lineage. The reviewers mapping key is the
reviewer_id, the only join key for manifests, dispatch, and findings.
model_family and correlation_group describe which model produced an opinion.
Reviewer entries carry no role or standing: this resolver derives role,
independence_class, and authority from lead family, reviewer family, and the
selected profile group.

Roster choice belongs to this resolver, not its caller. `initial` requires
`--lead-family`, a frozen record, its packet, and a durable manifest path. It
reuses the strict record validator and readiness gate, binds the lead family,
record, packet, qualification authority, and resolver bytes into one immutable
selection manifest, writes it atomically and read-only exactly once, and prints
the same bytes. A caller that filters, reorders, retypes, or supplements that
roster is dispatching a panel nobody resolved.

Authorization is two grants, never one. `provider_data_allowlist` carries the
vendor data-rights grant a reviewer's `data_allowlist_key` must appear in, and
`reviewer_access_profile_allowlist` carries the entitlement-lane grant its
`access_profile` must appear in. A lane without a not-selected state fails the
whole resolution when either grant is missing. A conditional critic is skipped
with the reason recorded.

`targeted-refuter` remains a separate global roster, but it still requires the
accountable lead family so the operation is bound to a configured profile and
can never silently use a same-family refuter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast

try:
    import yaml
except ModuleNotFoundError:
    venv_python = Path(__file__).resolve().parent / ".venv/bin/python"
    if (
        __name__ != "__main__"
        or not venv_python.is_file()
        or Path(sys.executable).resolve() == venv_python.resolve()
    ):
        raise
    os.execv(venv_python, [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])

from review_sequence import (
    CREDENTIALED_EXTERNAL_LIFECYCLE_DOMAIN,
    RISK_DOMAINS,
    _is_session_local,
    _sha256,
    proof_subject_digest,
    select_review_action,
)

SCHEMA_VERSION = 9
# Pinned beside the schema version so activation is atomic: a v9 resolver and
# v6 panel definition cannot half-agree about lead eligibility or reviewer tiers.
LIVE_PANEL_ID = "critical-review-primary-v6"
MANIFEST_SCHEMA_VERSION = 8
DEFAULT_QUALIFICATION = Path.home() / ".omp/agent/skills/critical-review/qualification.yml"
# The manifest records which resolver bytes produced its roster. This is
# provenance for later audit and re-resolution, not a caller-selectable authority:
# the path is a fixed skill-relative literal and the digest comes from this module.
QUALIFICATION_RELATIVE_PATH = "lrhe/qualification.py"
CANARY_AUTHORITIES = frozenset({"historical-non-scoring", "evaluation", "live-qualification"})
PROVIDER_CANARY_AUTHORITIES = frozenset({"evaluation", "live-qualification"})
READ_ONLY_REPOSITORY_TOOLS = ("read", "grep", "glob", "lsp", "ast_grep")

STRONG_ROLE = "strong_critic"
SUPPLEMENT_ROLE = "supplement"
ARCHITECTURE_ROLE = "architecture_specialist"
DISABLED_ROLE = "disabled"

REVIEWER_IDENTITY_FIELDS = (
    "model_family",
    "correlation_group",
    "provider_route",
    "access_profile",
    "data_allowlist_key",
)

EXECUTION_MODES = ("task_agent",)

CROSS_FAMILY = "cross_family"
SAME_LINEAGE_BLIND_SAMPLE = "same_lineage_blind_sample"
NO_LINEAGE_CLAIM = "not_applicable"
INDEPENDENT_EVIDENCE = "independent_evidence"
SUPPLEMENTAL_EVIDENCE = "supplemental_evidence"
NO_LIVE_AUTHORITY = "no_live_authority"
LIVE_ROLES: Mapping[str, tuple[str, str]] = {
    STRONG_ROLE: (CROSS_FAMILY, INDEPENDENT_EVIDENCE),
    SUPPLEMENT_ROLE: (CROSS_FAMILY, SUPPLEMENTAL_EVIDENCE),
    ARCHITECTURE_ROLE: (CROSS_FAMILY, SUPPLEMENTAL_EVIDENCE),
    "targeted_refuter": (CROSS_FAMILY, INDEPENDENT_EVIDENCE),
    "evaluation_only": (NO_LINEAGE_CLAIM, NO_LIVE_AUTHORITY),
    DISABLED_ROLE: (NO_LINEAGE_CLAIM, NO_LIVE_AUTHORITY),
}
LEAD_RELATIVE_SUPPLEMENTAL_ROLES = frozenset({ARCHITECTURE_ROLE})

LIVE_GROUPS: Mapping[str, tuple[str, bool]] = {
    "strongCritic": (STRONG_ROLE, True),
    "supplements": (SUPPLEMENT_ROLE, True),
    "architectureSpecialists": (ARCHITECTURE_ROLE, True),
    "targetedRefuters": ("targeted_refuter", True),
    "evaluationOnly": ("evaluation_only", False),
    "disabled": (DISABLED_ROLE, False),
}
PROFILE_GROUPS = ("strongCritic", "supplements", "architectureSpecialists")
GLOBAL_GROUPS = ("targetedRefuters", "evaluationOnly", "disabled")
CROSS_FAMILY_GROUPS = ("strongCritic", "supplements", "targetedRefuters")
ORACLE_SHADOW_FIELDS = frozenset(
    {
        "enabled",
        "shadow_id",
        "model_family",
        "correlation_group",
        "provider_route",
        "access_profile",
        "data_allowlist_key",
        "preset",
        "execution_mode",
        "evidence_delivery",
        "dataset_path",
    }
)
ORACLE_SHADOW_EXECUTION_MODE = "pi_oracle_async"
ORACLE_SHADOW_EVIDENCE_DELIVERY = "repository"
ORACLE_SHADOW_MODEL_FAMILY = "gpt"
ORACLE_SHADOW_PROVIDER_ROUTE = "openai-chatgpt-web"
ORACLE_SHADOW_DATA_ALLOWLIST_KEY = "openai"
ORACLE_SHADOW_PRESET = "pro_extended"
ORACLE_SHADOW_SELECTED = "selected"
ORACLE_SHADOW_SKIPPED = "skipped"
ORACLE_SHADOW_DISABLED = "disabled"
ORACLE_SHADOW_DISABLED_REASON_CODE = "oracle_shadow_disabled"
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

QUALIFICATION_FIELDS = frozenset({"common", "scopes"})
COMMON_QUALIFICATION_FIELDS = frozenset(
    {"schemaValid", "readOnlyBoundary", "exactServedModelRequired"}
)
SCOPE_STATUSES = frozenset({"passed", "ineligible", "failed"})
ELIGIBILITY_FIELDS = frozenset(
    {
        "policy",
        "allowedReviewModes",
        "activationRiskDomainsAny",
        "deniedRiskDomains",
        "requiredProofClassStatuses",
        "denyPathComponentRegex",
        "onUnknown",
    }
)

# The packet context fixed by SKILL.md. The set is closed in both directions: a
# missing key is an incomplete packet, and an extra key is a packet this resolver
# cannot claim to have understood.
PACKET_FIELDS = (
    "review_record_path",
    "review_record_sha256",
    "goal",
    "non_goals",
    "requirements",
    "invariants",
    "trust_boundaries",
    "data_or_state_transitions",
    "rollback_contract",
    "compatibility_contract",
    "design_or_diff",
    "known_open_questions",
    "rejected_alternatives_and_reasons",
    "provider_data_allowlist",
    # Vendor data rights and lane entitlement are different grants, so they are
    # named separately. `provider_data_allowlist` says which vendors may receive
    # the material; `reviewer_access_profile_allowlist` says which entitlement
    # lanes the request authorizes. One route is reachable under several profiles,
    # so a vendor grant never authorizes a lane and a lane grant never authorizes
    # a vendor.
    "reviewer_access_profile_allowlist",
)
# The two grant vocabularies. Matched exactly against reviewer metadata, never
# read as repository content -- see `packet_paths`.
AUTHORIZATION_PACKET_FIELDS = frozenset(
    {"provider_data_allowlist", "reviewer_access_profile_allowlist"}
)
_PACKET_FENCE = re.compile(r"^```ya?ml[ \t]*\n(?P<body>.*?)^```", re.DOTALL | re.MULTILINE)

# The conservative path-component rule, verbatim from the implementation packet.
# It is a skip filter and never a content-rewriting mechanism: a false positive
# withholds one additive opinion, alters no byte of the packet, and suppresses no
# other review lane.
SECURITY_PATH_PATTERN = (
    r"(?i)(^|[/_.-])(auth|authentication|authorization|oauth|oidc|sso|rbac|iam|security"
    r"|secrets?|credentials?|cryptography|crypto|certificates?|tls)([/_.-]|$)"
)
SECURITY_SENSITIVE_PATH = re.compile(SECURITY_PATH_PATTERN)

MANIFEST_MODES = ("initial", "targeted-refuter")
STRONG_REASON_CODE = "configured-strong-critic"
SUPPLEMENT_REASON_CODE = "configured-supplement"
ARCHITECTURE_DESIGN_REASON_CODE = "architecture-design-council"
ARCHITECTURE_INITIAL_REASON_CODE = "architecture-material"
ARCHITECTURE_REDESIGN_REASON_CODE = "architecture-material-redesign"
TARGETED_REFUTER_REASON_CODE = "configured-targeted-refuter"
SELECTION_REASON_CODES = (
    STRONG_REASON_CODE,
    SUPPLEMENT_REASON_CODE,
    ARCHITECTURE_DESIGN_REASON_CODE,
    ARCHITECTURE_INITIAL_REASON_CODE,
    ARCHITECTURE_REDESIGN_REASON_CODE,
    TARGETED_REFUTER_REASON_CODE,
)
SELECTION_CLASSES = ("strong", "supplement", "conditional", "targeted")
SELECTABLE_ROLES = (
    STRONG_ROLE,
    SUPPLEMENT_ROLE,
    ARCHITECTURE_ROLE,
    "targeted_refuter",
)
ACCESS_PROFILE_SKIP_REASON_CODE = "access-profile-not-authorized"
PROVIDER_DATA_RIGHTS_SKIP_REASON_CODE = "provider-data-rights-not-authorized"
SKIP_REASON_CODES = (
    ACCESS_PROFILE_SKIP_REASON_CODE,
    "architecture-scope-absent",
    "authorization-proof-applicable",
    PROVIDER_DATA_RIGHTS_SKIP_REASON_CODE,
    "review-mode-ineligible",
    "risk-domains-empty",
    "security-risk-domain",
    "security-sensitive-path",
)


@dataclass(frozen=True)
class ConditionalPolicy:
    """Pinned eligibility for one record-selected supplemental specialist."""

    policy: str
    required_scope: str
    allowed_review_modes: tuple[str, ...]
    activation_risk_domains: tuple[str, ...]
    denied_risk_domains: tuple[str, ...]
    qualified_risk_domains: tuple[str, ...]
    required_proof_class_statuses: tuple[tuple[str, str], ...]
    deny_path_pattern: str
    on_unknown: str
    fallback_allowed: bool
    thinking_level: str
    read_only_marker: str
    cohort_schema: str
    cohort_min_eligible_attempts: int
    cohort_max_security_misroutes: int
    cohort_min_completion_percent: int
    cohort_min_served_model_match_percent: int
    cohort_max_forbidden_tool_attempts: int
    cohort_max_negative_control_percent: int


FABLE_ARCHITECTURE_SYNTHESIS_V2 = ConditionalPolicy(
    policy="fable-non-security-architecture-v1",
    required_scope="non-security-architecture",
    allowed_review_modes=("design", "initial", "material-redesign"),
    activation_risk_domains=(
        "architecture",
        "cache-invalidation",
        "concurrency",
        "documentation-policy",
        "persistent-state",
    ),
    denied_risk_domains=(
        "authorization",
        CREDENTIALED_EXTERNAL_LIFECYCLE_DOMAIN,
        "money-or-assets",
        "privacy",
        "release-supply-chain",
        "secrets-cryptography",
    ),
    qualified_risk_domains=(
        "architecture",
        "cache-invalidation",
        "concurrency",
        "documentation-policy",
        "persistent-state",
    ),
    required_proof_class_statuses=(("authorization", "not-applicable"),),
    deny_path_pattern=SECURITY_PATH_PATTERN,
    on_unknown="skip",
    fallback_allowed=False,
    thinking_level="max",
    read_only_marker="FABLE_ARCHITECTURE_REVIEWER_READ_ONLY_V2",
    cohort_schema="lrhe-fable-architecture-cohort-v1",
    cohort_min_eligible_attempts=20,
    cohort_max_security_misroutes=0,
    cohort_min_completion_percent=90,
    cohort_min_served_model_match_percent=100,
    cohort_max_forbidden_tool_attempts=0,
    cohort_max_negative_control_percent=10,
)
# Kept as a source-level name for callers; the v1 policy id itself is removed.
FABLE_POLICY = FABLE_ARCHITECTURE_SYNTHESIS_V2
CONDITIONAL_POLICIES: Mapping[str, ConditionalPolicy] = {
    FABLE_POLICY.policy: FABLE_POLICY
}


class QualificationError(ValueError):
    """The qualification record cannot safely drive dispatch or evaluation."""


@dataclass(frozen=True)
class LiveReviewer:
    """One resolved reviewer: its identity, its lineage, and what it dispatches."""

    reviewer_id: str
    model_family: str
    correlation_group: str
    provider_route: str
    access_profile: str
    data_allowlist_key: str
    execution_mode: str
    role: str
    independence_class: str
    authority: str
    agent: str
    lens: str
    model: str
    evidence_delivery: str


@dataclass(frozen=True)
class OracleShadow:
    """One asynchronous no-standing consultation outside the resolver roster."""

    enabled: bool
    shadow_id: str
    model_family: str
    correlation_group: str
    provider_route: str
    access_profile: str
    data_allowlist_key: str
    preset: str
    execution_mode: str
    evidence_delivery: str
    dataset_path: str


@dataclass(frozen=True)
class ConditionalCritic:
    reviewer: LiveReviewer
    policy: ConditionalPolicy
    scope_receipt: str


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise QualificationError(f"{field} must be a mapping")
    return cast(Mapping[str, object], value)


def _names(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise QualificationError(f"{field} must be a list of names")
    sequence = cast(Sequence[object], value)
    names: list[str] = []
    for item in sequence:
        if not isinstance(item, str) or not item.strip():
            raise QualificationError(f"{field} contains an entry that is not a name")
        names.append(item.strip())
    if len(names) != len(set(names)):
        raise QualificationError(f"{field} contains duplicate names")
    return tuple(names)


def oracle_shadow(document: Mapping[str, object]) -> OracleShadow | None:
    """Validate the asynchronous ChatGPT Pro shadow configuration."""

    raw = document.get("oracleShadow")
    if raw is None:
        return None
    block = _mapping(raw, "oracleShadow")
    if set(block) != ORACLE_SHADOW_FIELDS:
        raise QualificationError(
            f"oracleShadow fields must be {sorted(ORACLE_SHADOW_FIELDS)}, got {sorted(block)}"
        )
    enabled = block.get("enabled")
    if not isinstance(enabled, bool):
        raise QualificationError("oracleShadow.enabled must be boolean")
    values: dict[str, str] = {}
    for field in ORACLE_SHADOW_FIELDS - {"enabled"}:
        value = block.get(field)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise QualificationError(f"oracleShadow.{field} must be a non-empty trimmed string")
        values[field] = value
    if values["execution_mode"] != ORACLE_SHADOW_EXECUTION_MODE:
        raise QualificationError(
            f"oracleShadow.execution_mode must be {ORACLE_SHADOW_EXECUTION_MODE!r}"
        )
    if values["evidence_delivery"] != ORACLE_SHADOW_EVIDENCE_DELIVERY:
        raise QualificationError(
            f"oracleShadow.evidence_delivery must be {ORACLE_SHADOW_EVIDENCE_DELIVERY!r}"
        )
    for field, expected in (
        ("model_family", ORACLE_SHADOW_MODEL_FAMILY),
        ("provider_route", ORACLE_SHADOW_PROVIDER_ROUTE),
        ("data_allowlist_key", ORACLE_SHADOW_DATA_ALLOWLIST_KEY),
        ("preset", ORACLE_SHADOW_PRESET),
    ):
        if values[field] != expected:
            raise QualificationError(f"oracleShadow.{field} must be {expected!r}")
    dataset = Path(values["dataset_path"])
    if dataset.is_absolute() or ".." in dataset.parts or not dataset.parts or dataset.parts[0] != "lrhe-data":
        raise QualificationError("oracleShadow.dataset_path must stay under lrhe-data")
    return OracleShadow(enabled=enabled, **values)


def selector_thinking_level(selector: str) -> str:
    """Return a selector's explicit effort suffix, or '' when it carries none."""

    base, separator, effort = selector.rpartition(":")
    return effort if separator and base else ""


def selector_model(selector: str) -> str:
    """Return a selector without its effort suffix."""

    base, separator, _ = selector.rpartition(":")
    return base if separator and base else selector


def _relative_data_path(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualificationError(f"{field} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise QualificationError(f"{field} must stay under the skill directory")
    if not path.parts or path.parts[0] != "lrhe-data" or path.suffix != ".json":
        raise QualificationError(f"{field} must be a JSON path under lrhe-data")
    return value


def _scope_receipt(reviewer_id: str, entry: Mapping[str, object], policy: ConditionalPolicy) -> str:
    """Validate one conditional critic's nested scoped qualification.

    The scoped shape is the point: a lane can be `passed` for the scope it was
    measured in and explicitly `ineligible` for a scope it refused, without the
    refusal evidence being rewritten as a passed canary and without the passed
    scope being read as blanket authority.
    """

    field = f"reviewers.{reviewer_id}.qualification"
    block = _mapping(entry.get("qualification"), field)
    if set(block) != QUALIFICATION_FIELDS:
        raise QualificationError(
            f"{field} fields must be {sorted(QUALIFICATION_FIELDS)}, got {sorted(block)}"
        )
    common = _mapping(block.get("common"), f"{field}.common")
    if set(common) != COMMON_QUALIFICATION_FIELDS:
        raise QualificationError(
            f"{field}.common fields must be {sorted(COMMON_QUALIFICATION_FIELDS)}, "
            f"got {sorted(common)}"
        )
    if common.get("schemaValid") is not True:
        raise QualificationError(f"{field}.common.schemaValid must be true")
    if common.get("readOnlyBoundary") != "passed":
        raise QualificationError(f"{field}.common.readOnlyBoundary must be 'passed'")
    selector = entry.get("model")
    if not isinstance(selector, str) or not selector.strip():
        raise QualificationError(f"reviewers.{reviewer_id}.model is missing")
    if common.get("exactServedModelRequired") != selector_model(selector):
        raise QualificationError(
            f"{field}.common.exactServedModelRequired must be "
            f"{selector_model(selector)!r} for selector {selector!r}, got "
            f"{common.get('exactServedModelRequired')!r}"
        )
    if selector_thinking_level(selector) != policy.thinking_level:
        raise QualificationError(
            f"reviewers.{reviewer_id}.model must resolve at thinking level "
            f"{policy.thinking_level!r} for {policy.policy}, got {selector!r}"
        )

    scopes = _mapping(block.get("scopes"), f"{field}.scopes")
    if policy.required_scope not in scopes:
        raise QualificationError(
            f"{field}.scopes must declare {policy.required_scope!r} for {policy.policy}"
        )
    receipt = ""
    for scope_name, raw_scope in scopes.items():
        scope_field = f"{field}.scopes.{scope_name}"
        scope = _mapping(raw_scope, scope_field)
        status = scope.get("status")
        if status not in SCOPE_STATUSES:
            raise QualificationError(
                f"{scope_field}.status must be one of {sorted(SCOPE_STATUSES)}, got {status!r}"
            )
        if status == "passed":
            if set(scope) != {"status", "canaryReceipt"}:
                raise QualificationError(
                    f"{scope_field} fields must be ['canaryReceipt', 'status'] when passed, "
                    f"got {sorted(scope)}"
                )
            scope_receipt = _relative_data_path(
                f"{scope_field}.canaryReceipt", scope.get("canaryReceipt")
            )
            if scope_name == policy.required_scope:
                receipt = scope_receipt
        else:
            if set(scope) != {"status", "boundaryEvidence"}:
                raise QualificationError(
                    f"{scope_field} fields must be ['boundaryEvidence', 'status'] when "
                    f"{status}, got {sorted(scope)}"
                )
            evidence = scope.get("boundaryEvidence")
            if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
                raise QualificationError(f"{scope_field}.boundaryEvidence must be a list")
            items = cast(Sequence[object], evidence)
            if not items:
                raise QualificationError(
                    f"{scope_field}.boundaryEvidence must retain the evidence for {status!r}"
                )
            for index, item in enumerate(items):
                _relative_data_path(f"{scope_field}.boundaryEvidence[{index}]", item)
    if not receipt:
        raise QualificationError(
            f"{field}.scopes.{policy.required_scope} must be 'passed' with a fresh "
            f"canary receipt before {policy.policy} can dispatch"
        )
    return receipt


def _eligibility(reviewer_id: str, entry: Mapping[str, object]) -> ConditionalPolicy:
    """Validate one conditional critic's eligibility block against its policy."""

    field = f"reviewers.{reviewer_id}.eligibility"
    block = _mapping(entry.get("eligibility"), field)
    if set(block) != ELIGIBILITY_FIELDS:
        raise QualificationError(
            f"{field} fields must be {sorted(ELIGIBILITY_FIELDS)}, got {sorted(block)}"
        )
    policy_id = block.get("policy")
    if not isinstance(policy_id, str) or policy_id not in CONDITIONAL_POLICIES:
        raise QualificationError(
            f"{field}.policy must be one of {sorted(CONDITIONAL_POLICIES)}, got {policy_id!r}"
        )
    policy = CONDITIONAL_POLICIES[policy_id]

    modes = _names(block.get("allowedReviewModes"), f"{field}.allowedReviewModes")
    if modes != policy.allowed_review_modes:
        raise QualificationError(
            f"{field}.allowedReviewModes must be {list(policy.allowed_review_modes)!r} "
            f"for {policy_id}, got {list(modes)!r}"
        )
    activation = _names(
        block.get("activationRiskDomainsAny"), f"{field}.activationRiskDomainsAny"
    )
    if activation != policy.activation_risk_domains:
        raise QualificationError(
            f"{field}.activationRiskDomainsAny must be "
            f"{list(policy.activation_risk_domains)!r} for {policy_id}, "
            f"got {list(activation)!r}"
        )
    denied = _names(block.get("deniedRiskDomains"), f"{field}.deniedRiskDomains")
    if denied != policy.denied_risk_domains:
        raise QualificationError(
            f"{field}.deniedRiskDomains must be {list(policy.denied_risk_domains)!r} "
            f"for {policy_id}, got {list(denied)!r}"
        )
    unknown = sorted((set(activation) | set(denied)) - RISK_DOMAINS)
    if unknown:
        raise QualificationError(f"{field} names unknown risk domains {unknown}")
    statuses = _mapping(
        block.get("requiredProofClassStatuses"), f"{field}.requiredProofClassStatuses"
    )
    expected_statuses = dict(policy.required_proof_class_statuses)
    if dict(statuses) != expected_statuses:
        raise QualificationError(
            f"{field}.requiredProofClassStatuses must be {expected_statuses!r} "
            f"for {policy_id}, got {dict(statuses)!r}"
        )
    if block.get("denyPathComponentRegex") != policy.deny_path_pattern:
        raise QualificationError(
            f"{field}.denyPathComponentRegex does not match the pinned {policy_id} rule"
        )
    if block.get("onUnknown") != policy.on_unknown:
        raise QualificationError(f"{field}.onUnknown must be {policy.on_unknown!r}")
    if entry.get("fallbackAllowed") is not policy.fallback_allowed:
        raise QualificationError(
            f"reviewers.{reviewer_id}.fallbackAllowed must be {policy.fallback_allowed} "
            f"for {policy_id}: a conditional critic is additive, never a fallback"
        )
    return policy


def _identity(
    reviewer_id: str,
    entry: Mapping[str, object],
) -> dict[str, object]:
    """Read one reviewer's standing-neutral identity and execution contract."""

    if "profileBinding" in entry:
        raise QualificationError(
            f"reviewers.{reviewer_id}.profileBinding is no longer supported; "
            "execution is resolved from the selected profile and execution_mode"
        )
    for field in ("dispatchRole", "independence_class", "authority"):
        if field in entry:
            raise QualificationError(
                f"reviewers.{reviewer_id}.{field} is no longer supported; "
                "standing is derived from the selected lead-family profile group"
            )

    identity: dict[str, object] = {"reviewer_id": reviewer_id}
    for field in REVIEWER_IDENTITY_FIELDS:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise QualificationError(f"reviewers.{reviewer_id}.{field} must be a non-empty string")
        identity[field] = value.strip()
    execution_mode = entry.get("execution_mode")
    if execution_mode not in EXECUTION_MODES:
        raise QualificationError(
            f"reviewers.{reviewer_id}.execution_mode must be one of {list(EXECUTION_MODES)}, "
            f"got {execution_mode!r}"
        )
    identity["execution_mode"] = execution_mode
    focused_eligible = entry.get("focusedEligible")
    if focused_eligible is not None and not isinstance(focused_eligible, bool):
        raise QualificationError(
            f"reviewers.{reviewer_id}.focusedEligible must be boolean when present"
        )
    if entry.get("fallbackAllowed") is True:
        raise QualificationError(
            f"reviewers.{reviewer_id}.fallbackAllowed must not be true; a reviewer "
            "is never a fallback and may never change models"
        )
    return identity


def _profiles(live: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
    raw_profiles = _mapping(live.get("byLeadFamily"), "liveDispatch.byLeadFamily")
    if not raw_profiles:
        raise QualificationError("liveDispatch.byLeadFamily must declare at least one profile")
    profiles: dict[str, Mapping[str, object]] = {}
    for raw_family, raw_profile in raw_profiles.items():
        if (
            not isinstance(raw_family, str)
            or not raw_family.strip()
            or raw_family != raw_family.strip()
        ):
            raise QualificationError("liveDispatch.byLeadFamily contains an invalid family name")
        profile = _mapping(raw_profile, f"liveDispatch.byLeadFamily.{raw_family}")
        if set(profile) != set(PROFILE_GROUPS):
            raise QualificationError(
                f"liveDispatch.byLeadFamily.{raw_family} fields must be "
                f"{list(PROFILE_GROUPS)!r}, got {sorted(profile)}"
            )
        profiles[raw_family] = profile
    return profiles


def _profile(document: Mapping[str, object], lead_family: str) -> Mapping[str, object]:
    if not isinstance(lead_family, str) or not lead_family.strip():
        raise QualificationError("lead family must be a non-empty string")
    live = _mapping(document.get("liveDispatch"), "liveDispatch")
    profiles = _profiles(live)
    profile = profiles.get(lead_family)
    if profile is None:
        raise QualificationError(
            f"unsupported lead family {lead_family!r}; configured families are {sorted(profiles)}"
        )
    return profile


def validate_qualification(document: object) -> Mapping[str, object]:
    root = _mapping(document, "qualification")
    if root.get("schemaVersion") != SCHEMA_VERSION:
        raise QualificationError(
            f"qualification schemaVersion must be {SCHEMA_VERSION}, got {root.get('schemaVersion')!r}"
        )

    ledger_map = _mapping(root.get("canaryLedgers"), "canaryLedgers")
    if not ledger_map:
        raise QualificationError("canaryLedgers must declare at least one protected ledger")
    ledger_paths: set[str] = set()
    for ledger_name, raw_entry in ledger_map.items():
        if not isinstance(ledger_name, str) or not ledger_name.strip():
            raise QualificationError("canaryLedgers contains a non-name entry")
        entry = _mapping(raw_entry, f"canaryLedgers.{ledger_name}")
        mode = entry.get("mode")
        expected_fields = (
            {"path", "mode", "sha256", "authority"}
            if mode == "sealed"
            else {"path", "mode", "prefixRows", "prefixSha256", "authority"}
            if mode == "append-only"
            else set()
        )
        if not expected_fields:
            raise QualificationError(
                f"canaryLedgers.{ledger_name}.mode must be 'sealed' or 'append-only'"
            )
        if set(entry) != expected_fields:
            raise QualificationError(
                f"canaryLedgers.{ledger_name} fields must be {sorted(expected_fields)}, "
                f"got {sorted(entry)}"
            )
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise QualificationError(f"canaryLedgers.{ledger_name}.path must be non-empty")
        ledger_path = Path(raw_path)
        if (
            ledger_path.is_absolute()
            or ".." in ledger_path.parts
            or not ledger_path.parts
            or ledger_path.parts[0] != "lrhe-data"
            or ledger_path.suffix != ".jsonl"
        ):
            raise QualificationError(
                f"canaryLedgers.{ledger_name}.path must be a JSONL path under lrhe-data"
            )
        if raw_path in ledger_paths:
            raise QualificationError(f"canaryLedgers contains duplicate path {raw_path!r}")
        ledger_paths.add(raw_path)
        authority = entry.get("authority")
        if authority not in CANARY_AUTHORITIES:
            raise QualificationError(
                f"canaryLedgers.{ledger_name}.authority must be one of "
                f"{sorted(CANARY_AUTHORITIES)}, got {authority!r}"
            )
        digest_field = "sha256" if mode == "sealed" else "prefixSha256"
        digest = entry.get(digest_field)
        if not isinstance(digest, str) or not _SHA256_HEX.fullmatch(digest):
            raise QualificationError(
                f"canaryLedgers.{ledger_name}.{digest_field} must be lowercase SHA-256"
            )
        if mode == "append-only":
            rows = entry.get("prefixRows")
            if not isinstance(rows, int) or isinstance(rows, bool) or rows < 1:
                raise QualificationError(
                    f"canaryLedgers.{ledger_name}.prefixRows must be a positive integer"
                )

    if oracle_shadow(root) is None:
        raise QualificationError("qualification must declare oracleShadow for full-council shadowing")

    live = _mapping(root.get("liveDispatch"), "liveDispatch")
    expected_live_fields = {"panelId", "byLeadFamily", *GLOBAL_GROUPS}
    if set(live) != expected_live_fields:
        raise QualificationError(
            f"liveDispatch fields must be {sorted(expected_live_fields)}, got {sorted(live)}"
        )
    panel_id = live.get("panelId")
    if panel_id != LIVE_PANEL_ID:
        raise QualificationError(
            f"liveDispatch.panelId must be {LIVE_PANEL_ID!r}, got {panel_id!r}"
        )

    profiles = _profiles(live)
    if set(profiles) != {"gpt", "claude"}:
        raise QualificationError(
            "liveDispatch.byLeadFamily must contain exactly the qualified lead families "
            f"['claude', 'gpt'], got {sorted(profiles)}"
        )
    profile_groups = {
        lead_family: {
            group: _names(
                profile.get(group),
                f"liveDispatch.byLeadFamily.{lead_family}.{group}",
            )
            for group in PROFILE_GROUPS
        }
        for lead_family, profile in profiles.items()
    }
    global_groups = {
        group: _names(live.get(group), f"liveDispatch.{group}") for group in GLOBAL_GROUPS
    }

    reviewer_map = _mapping(root.get("reviewers"), "reviewers")
    entries: dict[str, Mapping[str, object]] = {}
    identities: dict[str, Mapping[str, object]] = {}
    for reviewer_id, raw_entry in reviewer_map.items():
        if (
            not isinstance(reviewer_id, str)
            or not reviewer_id.strip()
            or reviewer_id != reviewer_id.strip()
        ):
            raise QualificationError("reviewers contains an invalid reviewer id")
        entry = _mapping(raw_entry, f"reviewers.{reviewer_id}")
        entries[reviewer_id] = entry
        identities[reviewer_id] = _identity(reviewer_id, entry)

    memberships: dict[str, set[str]] = {}
    for lead_family, groups in profile_groups.items():
        seen: dict[str, str] = {}
        active_profile = bool(groups["strongCritic"] or groups["supplements"])
        if active_profile and len(groups["strongCritic"]) != 1:
            raise QualificationError(
                f"liveDispatch.byLeadFamily.{lead_family}.strongCritic must select "
                "exactly one reciprocal strong critic"
            )
        if active_profile and not groups["supplements"]:
            raise QualificationError(
                f"liveDispatch.byLeadFamily.{lead_family}.supplements must not be empty"
            )
        for group in PROFILE_GROUPS:
            for reviewer_id in groups[group]:
                if reviewer_id in seen:
                    raise QualificationError(
                        f"reviewer {reviewer_id!r} appears in both "
                        f"liveDispatch.byLeadFamily.{lead_family}.{seen[reviewer_id]} and "
                        f"liveDispatch.byLeadFamily.{lead_family}.{group}"
                    )
                seen[reviewer_id] = group
                identity = identities.get(reviewer_id)
                if identity is None:
                    raise QualificationError(
                        f"liveDispatch.byLeadFamily.{lead_family}.{group} names unknown "
                        f"reviewer {reviewer_id!r}"
                    )
                memberships.setdefault(reviewer_id, set()).add(group)
                model_family = cast(str, identity["model_family"])
                if group in CROSS_FAMILY_GROUPS and model_family == lead_family:
                    raise QualificationError(
                        f"reviewers.{reviewer_id} has model_family {lead_family!r}, which is "
                        f"the accountable lead's own lineage; "
                        f"liveDispatch.byLeadFamily.{lead_family}.{group} must be cross-family"
                    )
                if identity["execution_mode"] != "task_agent":
                    raise QualificationError(
                        f"reviewers.{reviewer_id}.execution_mode must be 'task_agent' in "
                        f"liveDispatch.byLeadFamily.{lead_family}.{group}"
                    )
        core_reviewers = (*groups["strongCritic"], *groups["supplements"])
        for field in ("model_family", "correlation_group"):
            owners: dict[str, str] = {}
            for reviewer_id in core_reviewers:
                value = cast(str, identities[reviewer_id][field])
                previous = owners.get(value)
                if previous is not None:
                    raise QualificationError(
                        f"liveDispatch.byLeadFamily.{lead_family}.strongCritic and supplements "
                        f"must use distinct {field} values; reviewers {previous!r} and "
                        f"{reviewer_id!r} both use {value!r}"
                    )
                owners[value] = reviewer_id
    global_memberships: dict[str, str] = {}
    for group in GLOBAL_GROUPS:
        for reviewer_id in global_groups[group]:
            if reviewer_id in global_memberships:
                raise QualificationError(
                    f"reviewer {reviewer_id!r} appears in both "
                    f"liveDispatch.{global_memberships[reviewer_id]} and liveDispatch.{group}"
                )
            if reviewer_id in memberships:
                raise QualificationError(
                    f"reviewer {reviewer_id!r} appears in a lead-family profile and "
                    f"liveDispatch.{group}"
                )
            identity = identities.get(reviewer_id)
            if identity is None:
                raise QualificationError(
                    f"liveDispatch.{group} names unknown reviewer {reviewer_id!r}"
                )
            global_memberships[reviewer_id] = group
            memberships.setdefault(reviewer_id, set()).add(group)
            if group == "targetedRefuters" and identity["model_family"] in profiles:
                raise QualificationError(
                    f"reviewers.{reviewer_id} has model_family {identity['model_family']!r}, "
                    "which matches a configured accountable lead; "
                    "liveDispatch.targetedRefuters must be cross-family for every profile"
                )

    reviewer_names = set(entries)
    assigned_names = set(memberships)
    if reviewer_names != assigned_names:
        missing = sorted(reviewer_names - assigned_names)
        unknown = sorted(assigned_names - reviewer_names)
        raise QualificationError(
            f"liveDispatch membership mismatch: unassigned={missing}, unknown={unknown}"
        )

    for reviewer_id, entry in entries.items():
        groups = memberships[reviewer_id]
        roles = {LIVE_GROUPS[group][0] for group in groups}
        if ARCHITECTURE_ROLE in roles and roles != {ARCHITECTURE_ROLE}:
            raise QualificationError(
                f"reviewers.{reviewer_id} is an architecture specialist in one profile and "
                "carries another live role; scoped eligibility cannot change by lead family"
            )
        if entry.get("focusedEligible") is True and SUPPLEMENT_ROLE not in roles:
            raise QualificationError(
                f"reviewers.{reviewer_id}.focusedEligible requires the supplement role"
            )
        expected_dispatch = any(LIVE_GROUPS[group][1] for group in groups)
        if entry.get("dispatchEnabled") is not expected_dispatch:
            raise QualificationError(
                f"reviewers.{reviewer_id}.dispatchEnabled disagrees with liveDispatch membership"
            )
        if not isinstance(entry.get("evaluationEnabled"), bool):
            raise QualificationError(f"reviewers.{reviewer_id}.evaluationEnabled must be boolean")

        earned = expected_dispatch or entry.get("evaluationEnabled") is True
        charter_amendment = entry.get("charterAmendment")
        if charter_amendment is not None:
            if not earned:
                raise QualificationError(
                    f"reviewers.{reviewer_id}.charterAmendment cannot qualify a disabled lane"
                )
            _relative_data_path(f"reviewers.{reviewer_id}.charterAmendment", charter_amendment)

        if earned:
            required = (
                ()
                if roles == {ARCHITECTURE_ROLE}
                else (
                    ("providerCanary", "passed"),
                    ("schemaValid", True),
                    ("readOnlyBoundary", "passed"),
                )
            )
            missing = [name for name, expected in required if entry.get(name) != expected]
            if missing:
                raise QualificationError(
                    f"reviewers.{reviewer_id} enabled without proven {', '.join(missing)}"
                )
            for name in ("agent", "model"):
                value = entry.get(name)
                if not isinstance(value, str) or not value.strip():
                    raise QualificationError(f"reviewers.{reviewer_id}.{name} is missing")

        contract_fields = ("evidenceDelivery", "tools", "canaryReceipt")
        present = [name for name in contract_fields if name in entry]
        if present:
            missing_contract = [name for name in contract_fields if name not in entry]
            if missing_contract:
                raise QualificationError(
                    f"reviewers.{reviewer_id} incomplete evidence contract: "
                    f"missing {', '.join(missing_contract)}"
                )
            evidence_delivery = entry.get("evidenceDelivery")
            if evidence_delivery not in ("inline", "repository"):
                raise QualificationError(
                    f"reviewers.{reviewer_id}.evidenceDelivery must be 'inline' or "
                    f"'repository', got {evidence_delivery!r}"
                )
            tools = _names(entry.get("tools"), f"reviewers.{reviewer_id}.tools")
            expected_tools = () if evidence_delivery == "inline" else READ_ONLY_REPOSITORY_TOOLS
            if tools != expected_tools:
                raise QualificationError(
                    f"reviewers.{reviewer_id}.tools must be {list(expected_tools)!r} for "
                    f"{evidence_delivery} delivery, got {list(tools)!r}"
                )
            receipt = entry.get("canaryReceipt")
            if not isinstance(receipt, str) or not receipt.strip():
                raise QualificationError(
                    f"reviewers.{reviewer_id}.canaryReceipt must be a non-empty relative path"
                )
            if Path(receipt).is_absolute() or ".." in Path(receipt).parts:
                raise QualificationError(
                    f"reviewers.{reviewer_id}.canaryReceipt must stay under the skill directory"
                )

        if roles == {ARCHITECTURE_ROLE}:
            _scope_receipt(reviewer_id, entry, _eligibility(reviewer_id, entry))
        else:
            for stray in ("eligibility", "qualification"):
                if stray in entry:
                    raise QualificationError(
                        f"reviewers.{reviewer_id} declares {stray} without the "
                        f"{ARCHITECTURE_ROLE!r} role; scope and role activate together"
                    )
    return root


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bind_qualification(
    path: Path = DEFAULT_QUALIFICATION,
) -> tuple[Path, str, Mapping[str, object]]:
    """Read, hash, parse, and validate one retained authority byte snapshot."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise QualificationError(f"qualification file is not readable: {resolved}")
    try:
        raw = resolved.read_bytes()
        document = yaml.safe_load(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise QualificationError(f"qualification file cannot be parsed: {exc}") from exc
    return resolved, _digest_bytes(raw), validate_qualification(document)


def load_qualification(path: Path = DEFAULT_QUALIFICATION) -> Mapping[str, object]:
    """Read, parse, and validate the live qualification authority."""

    return bind_qualification(path)[2]


def reviewers(document: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(document.get("reviewers"), "reviewers")


def reviewer_roles(document: Mapping[str, object], reviewer_id: str) -> tuple[str, ...]:
    """Return every resolver-derived role this lane can hold across profiles."""

    live = _mapping(document.get("liveDispatch"), "liveDispatch")
    groups: set[str] = set()
    for profile in _profiles(live).values():
        for group in PROFILE_GROUPS:
            if reviewer_id in _names(profile.get(group), f"liveDispatch profile {group}"):
                groups.add(group)
    for group in GLOBAL_GROUPS:
        if reviewer_id in _names(live.get(group), f"liveDispatch.{group}"):
            groups.add(group)
    return tuple(sorted({LIVE_GROUPS[group][0] for group in groups}))


def _reviewer(
    document: Mapping[str, object],
    reviewer_id: str,
    group: str,
    lead_family: str,
) -> LiveReviewer:
    entry = _mapping(reviewers(document).get(reviewer_id), f"reviewers.{reviewer_id}")
    lens_value = entry.get("lens")
    role = LIVE_GROUPS[group][0]
    independence_class, authority = LIVE_ROLES[role]
    if role in LEAD_RELATIVE_SUPPLEMENTAL_ROLES and entry.get("model_family") == lead_family:
        independence_class = SAME_LINEAGE_BLIND_SAMPLE
    return LiveReviewer(
        **_identity(reviewer_id, entry),
        role=role,
        independence_class=independence_class,
        authority=authority,
        agent=cast(str, entry["agent"]),
        lens=lens_value if isinstance(lens_value, str) else "",
        model=cast(str, entry["model"]),
        evidence_delivery=(
            cast(str, entry["evidenceDelivery"])
            if isinstance(entry.get("evidenceDelivery"), str)
            else "repository"
        ),
    )


def profile_reviewers(
    document: Mapping[str, object], lead_family: str, group: str
) -> tuple[LiveReviewer, ...]:
    if group not in PROFILE_GROUPS:
        raise QualificationError(f"unsupported profile reviewer group: {group}")
    profile = _profile(document, lead_family)
    return tuple(
        _reviewer(document, reviewer_id, group, lead_family)
        for reviewer_id in _names(
            profile.get(group), f"liveDispatch.byLeadFamily.{lead_family}.{group}"
        )
    )


def strong_reviewers(
    document: Mapping[str, object], lead_family: str
) -> tuple[LiveReviewer, ...]:
    return profile_reviewers(document, lead_family, "strongCritic")


def focused_reviewers(
    document: Mapping[str, object], lead_family: str
) -> tuple[LiveReviewer, ...]:
    supplements = profile_reviewers(document, lead_family, "supplements")
    entries = reviewers(document)
    return (
        *strong_reviewers(document, lead_family),
        *(
            reviewer
            for reviewer in supplements
            if _mapping(
                entries.get(reviewer.reviewer_id), f"reviewers.{reviewer.reviewer_id}"
            ).get("focusedEligible")
            is True
        ),
    )


def global_reviewers(
    document: Mapping[str, object], group: str, lead_family: str
) -> tuple[LiveReviewer, ...]:
    if group not in GLOBAL_GROUPS:
        raise QualificationError(f"unsupported global reviewer group: {group}")
    _profile(document, lead_family)
    live = _mapping(document.get("liveDispatch"), "liveDispatch")
    return tuple(
        _reviewer(document, reviewer_id, group, lead_family)
        for reviewer_id in _names(live.get(group), f"liveDispatch.{group}")
    )


def architecture_specialists(
    document: Mapping[str, object], lead_family: str
) -> tuple[ConditionalCritic, ...]:
    group = "architectureSpecialists"
    entries = reviewers(document)
    result: list[ConditionalCritic] = []
    for reviewer in profile_reviewers(document, lead_family, group):
        entry = _mapping(entries.get(reviewer.reviewer_id), f"reviewers.{reviewer.reviewer_id}")
        policy = _eligibility(reviewer.reviewer_id, entry)
        result.append(
            ConditionalCritic(
                reviewer=reviewer,
                policy=policy,
                scope_receipt=_scope_receipt(reviewer.reviewer_id, entry, policy),
            )
        )
    return tuple(result)


def all_architecture_specialists(
    document: Mapping[str, object],
) -> tuple[ConditionalCritic, ...]:
    live = _mapping(document.get("liveDispatch"), "liveDispatch")
    seen: set[str] = set()
    result: list[ConditionalCritic] = []
    for lead_family in _profiles(live):
        for critic in architecture_specialists(document, lead_family):
            reviewer_id = critic.reviewer.reviewer_id
            if reviewer_id not in seen:
                seen.add(reviewer_id)
                result.append(critic)
    return tuple(result)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in cast(Sequence[object], value) if isinstance(item, str))


def packet_paths(packet: Mapping[str, object]) -> tuple[str, ...]:
    """Return every packet string the deny rule is applied to.

    Section 4.1 trusts the repository-relative paths explicitly present in the
    immutable packet. Every packet string is scanned rather than a guessed path
    subset: the rule is component-anchored, so prose survives it, and the only
    cost of over-scanning is withholding one additive opinion.

    The two authorization allowlists are the exception, and not because scanning
    them is expensive. They are closed grant vocabularies matched exactly against
    reviewer metadata, so no member of either can ever be a repository path -- but
    they read like one to a component-anchored rule. `xai-oauth-default` is an
    entitlement lane, and scanning it would skip every conditional critic on every
    council forever, on the strength of a route's name. Excluding them removes
    false positives only: a security-sensitive path cannot hide in a list whose
    entries must equal some reviewer's `access_profile` or `data_allowlist_key`.
    """

    collected: list[str] = []
    for field in PACKET_FIELDS:
        if field in AUTHORIZATION_PACKET_FIELDS:
            continue
        value = packet.get(field)
        if isinstance(value, str):
            collected.append(value)
        else:
            collected.extend(_strings(value))
    return tuple(collected)


def fable_skip_reason_codes(
    policy: ConditionalPolicy,
    record: Mapping[str, object],
    packet_source_paths: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return every eligibility failure for one conditional critic, sorted.

    All reasons are reported, not just the first: a packet ineligible on three
    independent grounds is a different fact from one ineligible on a single
    ground, and the skip record is the only place that distinction survives.

    `risk-domains-empty` stays in this vocabulary because the policy has to be
    complete on its own, but the live resolver never emits it -- an empty or
    unknown domain set fails the whole record in `review_sequence` before any
    critic is considered, and that strict whole-record failure is deliberately
    not weakened into a conditional-critic skip.
    """

    reasons: set[str] = set()
    if record.get("review_mode") not in policy.allowed_review_modes:
        reasons.add("review-mode-ineligible")
    domains = set(_strings(record.get("touched_risk_domains")))
    if not domains:
        reasons.add("risk-domains-empty")
    else:
        if not domains.intersection(policy.activation_risk_domains):
            reasons.add("architecture-scope-absent")
        if domains.intersection(policy.denied_risk_domains):
            reasons.add("security-risk-domain")
    proof_classes = record.get("proof_classes")
    for proof_class, expected in policy.required_proof_class_statuses:
        row = proof_classes.get(proof_class) if isinstance(proof_classes, Mapping) else None
        status = row.get("status") if isinstance(row, Mapping) else None
        if status != expected:
            reasons.add("authorization-proof-applicable")
    candidates = (
        str(record.get("artifact_path") or ""),
        *_strings(record.get("changed_files")),
        *packet_source_paths,
    )
    if any(SECURITY_SENSITIVE_PATH.search(path) for path in candidates if path):
        reasons.add("security-sensitive-path")
    unknown = reasons - set(SKIP_REASON_CODES)
    if unknown:  # pragma: no cover -- defends the closed manifest vocabulary
        raise QualificationError(f"unknown skip reason codes {sorted(unknown)}")
    return tuple(sorted(reasons))


def _parse_packet_text(text: str) -> Mapping[str, object]:
    match = _PACKET_FENCE.search(text)
    body = match.group("body") if match else text
    try:
        document = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise QualificationError(f"packet context cannot be parsed: {exc}") from exc
    packet = _mapping(document, "packet context")
    missing = [field for field in PACKET_FIELDS if field not in packet]
    unknown = sorted(set(packet) - set(PACKET_FIELDS))
    if missing or unknown:
        raise QualificationError(
            f"packet context fields must be exactly {list(PACKET_FIELDS)}: "
            f"missing={missing}, unknown={unknown}"
        )
    declared_path = packet.get("review_record_path")
    if not isinstance(declared_path, str) or not declared_path.strip():
        raise QualificationError("packet review_record_path must be a non-empty path")
    declared_digest = packet.get("review_record_sha256")
    if not isinstance(declared_digest, str) or not _SHA256_HEX.fullmatch(declared_digest):
        raise QualificationError("packet review_record_sha256 must be a lowercase SHA-256 digest")
    if not _names(packet.get("provider_data_allowlist"), "packet provider_data_allowlist"):
        raise QualificationError("packet provider_data_allowlist authorizes no provider")
    if not _names(
        packet.get("reviewer_access_profile_allowlist"),
        "packet reviewer_access_profile_allowlist",
    ):
        raise QualificationError(
            "packet reviewer_access_profile_allowlist authorizes no access profile; a "
            "vendor grant is not a lane grant, so an empty list authorizes nobody"
        )
    return packet


def parse_packet(path: Path) -> Mapping[str, object]:
    """Parse the fixed packet context out of one immutable packet file."""

    try:
        return _parse_packet_text(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise QualificationError(f"packet is not readable: {exc}") from exc


def bind_record(path: Path) -> tuple[Path, str, Mapping[str, object]]:
    """Resolve one frozen record and bind parsing to one retained byte snapshot."""

    resolved = path.expanduser().resolve()
    if _is_session_local(resolved):
        raise QualificationError(f"review record must not be session-local: {resolved}")
    if not resolved.is_file():
        raise QualificationError(f"review record is not readable: {resolved}")
    try:
        raw = resolved.read_bytes()
        value: object = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError(f"review record cannot be parsed: {exc}") from exc
    return resolved, _digest_bytes(raw), _mapping(value, "review record")


def bind_packet(
    path: Path, record_path: Path, record_sha256: str
) -> tuple[Path, str, Mapping[str, object]]:
    """Bind parsing and digest to one packet byte snapshot, then verify its record."""

    resolved = path.expanduser().resolve()
    if _is_session_local(resolved):
        raise QualificationError(f"packet must not be session-local: {resolved}")
    if not resolved.is_file():
        raise QualificationError(f"packet is not readable: {resolved}")
    try:
        raw = resolved.read_bytes()
        packet = _parse_packet_text(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise QualificationError(f"packet is not readable: {exc}") from exc
    declared_path = cast(str, packet["review_record_path"])
    if Path(declared_path).expanduser().resolve() != record_path:
        raise QualificationError(
            f"packet review_record_path {declared_path!r} does not bind the resolved "
            f"record {record_path}"
        )
    if packet["review_record_sha256"] != record_sha256:
        raise QualificationError(
            f"packet review_record_sha256 {packet['review_record_sha256']!r} does not "
            f"match the record digest {record_sha256}"
        )
    return resolved, _digest_bytes(raw), packet


def _selected(
    reviewer: LiveReviewer, selection_class: str, reason_codes: Sequence[str]
) -> dict[str, object]:
    """Emit one roster row, joined by reviewer_id and never by lineage.

    Lineage metadata rides along so a later audit can ask what a result was worth
    without re-reading private configuration. The strong critic and supplements
    are pairwise distinct; an architecture specialist may intentionally share
    model_family or correlation_group with another row. execution_mode is
    resolved here so every dispatcher uses the same native Task-agent path
    instead of branching on reviewer names.
    """

    return {
        "reviewer_id": reviewer.reviewer_id,
        "model_family": reviewer.model_family,
        "correlation_group": reviewer.correlation_group,
        "provider_route": reviewer.provider_route,
        "access_profile": reviewer.access_profile,
        "data_allowlist_key": reviewer.data_allowlist_key,
        "execution_mode": reviewer.execution_mode,
        "role": reviewer.role,
        "independence_class": reviewer.independence_class,
        "authority": reviewer.authority,
        "agent": reviewer.agent,
        "lens": reviewer.lens,
        "model": reviewer.model,
        "evidence_delivery": reviewer.evidence_delivery,
        "selectionClass": selection_class,
        "reasonCodes": list(reason_codes),
    }


def qualification_binding() -> tuple[str, str]:
    """Name this resolver's authoritative path and digest for the manifest.

    The digest is taken from the module actually imported, so it describes the
    code that produced the roster rather than some other copy of it. The path is
    the fixed literal, checked against where this file really sits: a layout that
    does not put the resolver at its canonical location cannot emit a manifest
    claiming it does.
    """

    module = Path(__file__).resolve()
    if "/".join(module.parts[-2:]) != QUALIFICATION_RELATIVE_PATH:
        raise QualificationError(
            f"resolver runs from {module}, which is not {QUALIFICATION_RELATIVE_PATH!r} "
            "under a skill root; a manifest cannot bind an authority that is not there"
        )
    return QUALIFICATION_RELATIVE_PATH, _sha256(module)


def _manifest(
    *,
    mode: str,
    panel_id: str,
    lead_family: str,
    record: Mapping[str, object],
    record_path: Path,
    record_sha256: str,
    packet_path: Path,
    packet_sha256: str,
    authority_path: Path,
    authority_sha256: str,
    selected: Sequence[Mapping[str, object]],
    skipped: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    try:
        subject_digest = proof_subject_digest(record)
    except ValueError as exc:
        raise QualificationError(f"record has no proof subject digest: {exc}") from exc
    qualification_path, qualification_sha256 = qualification_binding()
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "panelId": panel_id,
        "mode": mode,
        "leadFamily": lead_family,
        "reviewRecordPath": str(record_path),
        "reviewRecordSha256": record_sha256,
        "subjectDigest": subject_digest,
        # The authority that resolved this roster, named so the worker can fetch
        # it and rerun the selection instead of believing the answer. The path is
        # a fixed literal rather than wherever this copy sits, and the consumer
        # holds it against the canonical installed authority it already trusts: a
        # manifest that agrees only with itself proves nothing.
        "qualificationPath": qualification_path,
        "qualificationSha256": qualification_sha256,
        # Beyond the packet's recommended shape, and deliberately so: binding the
        # packet bytes as well as the record bytes closes the window in which a
        # resolved packet is edited between resolution and dispatch.
        "packetPath": str(packet_path),
        "packetSha256": packet_sha256,
        "authorityPath": str(authority_path),
        "authoritySha256": authority_sha256,
        "selected": [dict(entry) for entry in selected],
        "skipped": [dict(entry) for entry in skipped],
    }


def _packet_authorization_reason_codes(
    *, access_profile: str, data_allowlist_key: str, packet: Mapping[str, object]
) -> tuple[str, ...]:
    reasons: list[str] = []
    if data_allowlist_key not in _names(
        packet.get("provider_data_allowlist"), "packet provider_data_allowlist"
    ):
        reasons.append(PROVIDER_DATA_RIGHTS_SKIP_REASON_CODE)
    if access_profile not in _names(
        packet.get("reviewer_access_profile_allowlist"),
        "packet reviewer_access_profile_allowlist",
    ):
        reasons.append(ACCESS_PROFILE_SKIP_REASON_CODE)
    return tuple(sorted(reasons))


def packet_authorization_reason_codes(
    reviewer: LiveReviewer, packet: Mapping[str, object]
) -> tuple[str, ...]:
    """Return every grant this packet does not carry for one reviewer, sorted.

    Two independent grants, because they answer different questions. The vendor
    data-rights grant says the material may reach this vendor at all; the
    access-profile grant says this entitlement lane in particular is authorized.
    One route is reachable under several profiles -- `daybreak-blue` shares
    `openai-codex` with ordinary GPT lanes -- so a vendor grant can never stand in
    for a lane grant, and neither is implied by the route or chosen credential.
    """

    return _packet_authorization_reason_codes(
        access_profile=reviewer.access_profile,
        data_allowlist_key=reviewer.data_allowlist_key,
        packet=packet,
    )


def oracle_shadow_selection(
    document: Mapping[str, object], packet: Mapping[str, object]
) -> dict[str, object] | None:
    """Resolve the no-standing shadow and its packet-specific data grants."""

    shadow = oracle_shadow(document)
    if shadow is None:
        return None
    if not shadow.enabled:
        selection = ORACLE_SHADOW_DISABLED
        reasons = (ORACLE_SHADOW_DISABLED_REASON_CODE,)
    else:
        missing = _packet_authorization_reason_codes(
            access_profile=shadow.access_profile,
            data_allowlist_key=shadow.data_allowlist_key,
            packet=packet,
        )
        selection = ORACLE_SHADOW_SKIPPED if missing else ORACLE_SHADOW_SELECTED
        reasons = missing
    return {
        "schemaVersion": 1,
        "shadowId": shadow.shadow_id,
        "selection": selection,
        "reasonCodes": list(reasons),
        "modelFamily": shadow.model_family,
        "correlationGroup": shadow.correlation_group,
        "providerRoute": shadow.provider_route,
        "accessProfile": shadow.access_profile,
        "dataAllowlistKey": shadow.data_allowlist_key,
        "preset": shadow.preset,
        "executionMode": shadow.execution_mode,
        "evidenceDelivery": shadow.evidence_delivery,
        "datasetPath": shadow.dataset_path,
        "standing": "none",
        "blocksClosure": False,
        "receivesPeerOutput": False,
    }


def _require_packet_authorization(reviewer: LiveReviewer, packet: Mapping[str, object]) -> None:
    """Refuse a member the packet does not authorize, rather than dropping it.

    Only the lanes that cannot be skipped come through here: the unconditional
    critics and the always-on specialists. Neither of
    them has a not-selected state -- an absent unconditional critic is a resolution
    failure and a specialist is never record-selected -- so silently omitting one
    would shrink a council nobody agreed to shrink, and emitting it would dispatch
    a lane the packet never authorized. Failing the whole resolution is the only
    answer that is neither.
    """

    reasons = packet_authorization_reason_codes(reviewer, packet)
    if reasons:
        raise QualificationError(
            f"packet does not authorize reviewers.{reviewer.reviewer_id} "
            f"(access_profile {reviewer.access_profile!r}, data_allowlist_key "
            f"{reviewer.data_allowlist_key!r}): {list(reasons)}"
        )


def _architecture_selection_reason(record: Mapping[str, object]) -> str:
    mode = record.get("review_mode")
    if mode == "design":
        return ARCHITECTURE_DESIGN_REASON_CODE
    if mode == "material-redesign":
        return ARCHITECTURE_REDESIGN_REASON_CODE
    return ARCHITECTURE_INITIAL_REASON_CODE


def select_full_council(
    document: Mapping[str, object],
    record: Mapping[str, object],
    packet: Mapping[str, object],
    *,
    lead_family: str,
    record_path: Path,
    record_sha256: str,
    packet_path: Path,
    packet_sha256: str,
    authority_path: Path,
    authority_sha256: str,
) -> dict[str, object]:
    """Resolve one lead-relative tiered council in stable dispatch order."""

    decision = select_review_action(record)
    if decision.action != "full-council":
        raise QualificationError(
            f"record does not authorize a full council: status={decision.status!r}, "
            f"action={decision.action!r}, reasons={list(decision.reason_codes)}"
        )
    live = _mapping(document.get("liveDispatch"), "liveDispatch")
    strong = strong_reviewers(document, lead_family)
    if not strong:
        raise QualificationError(
            f"liveDispatch.byLeadFamily.{lead_family}.strongCritic selects no "
            "independent assurance anchor"
        )

    selected: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for reviewer in strong:
        _require_packet_authorization(reviewer, packet)
        selected.append(_selected(reviewer, "strong", (STRONG_REASON_CODE,)))
    for reviewer in profile_reviewers(document, lead_family, "supplements"):
        _require_packet_authorization(reviewer, packet)
        selected.append(_selected(reviewer, "supplement", (SUPPLEMENT_REASON_CODE,)))

    sources = packet_paths(packet)
    for candidate in architecture_specialists(document, lead_family):
        reasons = tuple(
            sorted(
                {
                    *fable_skip_reason_codes(candidate.policy, record, sources),
                    *packet_authorization_reason_codes(candidate.reviewer, packet),
                }
            )
        )
        if reasons:
            skipped.append(
                {
                    "reviewer_id": candidate.reviewer.reviewer_id,
                    "selectionClass": "conditional",
                    "reasonCodes": list(reasons),
                }
            )
        else:
            selected.append(
                _selected(
                    candidate.reviewer,
                    "conditional",
                    (_architecture_selection_reason(record),),
                )
            )
    return _manifest(
        mode="initial",
        panel_id=cast(str, live["panelId"]),
        lead_family=lead_family,
        record=record,
        record_path=record_path,
        record_sha256=record_sha256,
        packet_path=packet_path,
        packet_sha256=packet_sha256,
        authority_path=authority_path,
        authority_sha256=authority_sha256,
        selected=selected,
        skipped=skipped,
    )


def manifest_text(manifest: Mapping[str, object]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def write_manifest(path: Path, text: str) -> Path:
    """Persist one resolution exactly once, crash-durable, whole, and read-only."""

    resolved = path.expanduser().resolve()
    if _is_session_local(resolved):
        raise QualificationError(
            f"selection manifest must be durable, not session-local: {resolved}"
        )
    if resolved.exists():
        raise QualificationError(f"refusing to overwrite existing selection manifest: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    staged = resolved.parent / f".{resolved.name}.{os.getpid()}.staging"
    directory_descriptor: int | None = None
    try:
        directory_descriptor = os.open(
            resolved.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fchmod(handle.fileno(), 0o444)
            os.fsync(handle.fileno())
        os.link(staged, resolved)
        os.fsync(directory_descriptor)
        staged.unlink()
        os.fsync(directory_descriptor)
    except FileExistsError as exc:
        raise QualificationError(
            f"refusing to overwrite existing selection manifest: {resolved}"
        ) from exc
    except OSError as exc:
        raise QualificationError(f"selection manifest cannot be written: {exc}") from exc
    finally:
        staged.unlink(missing_ok=True)
        if directory_descriptor is not None:
            os.close(directory_descriptor)
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)

    initial = commands.add_parser(
        "initial",
        help=(
            "resolve one full council from a frozen record and its immutable packet, "
            "then persist the selected/skipped manifest that authorizes dispatch"
        ),
    )
    refuter = commands.add_parser(
        "targeted-refuter",
        help="resolve the fixed refutation pool; never a conditional critic",
    )

    initial.add_argument("--record", type=Path, required=True)
    initial.add_argument("--packet", type=Path, required=True)
    initial.add_argument(
        "--out",
        type=Path,
        required=True,
        help="durable manifest path; an existing file is never overwritten",
    )
    for command in (initial, refuter):
        command.add_argument(
            "--lead-family",
            required=True,
            help="accountable main-session model_family; must name a configured profile",
        )
        command.add_argument("--qualification", type=Path, default=DEFAULT_QUALIFICATION)

    args = parser.parse_args(argv)
    try:
        authority_path, authority_sha256, document = bind_qualification(args.qualification)
        if args.mode == "targeted-refuter":
            roster = [
                _selected(reviewer, "targeted", (TARGETED_REFUTER_REASON_CODE,))
                for reviewer in global_reviewers(document, "targetedRefuters", args.lead_family)
            ]
            print(json.dumps(roster, sort_keys=True))
            return 0
        record_path, record_sha256, record = bind_record(args.record)
        packet_path, packet_sha256, packet = bind_packet(args.packet, record_path, record_sha256)
        manifest = select_full_council(
            document,
            record,
            packet,
            lead_family=args.lead_family,
            record_path=record_path,
            record_sha256=record_sha256,
            packet_path=packet_path,
            packet_sha256=packet_sha256,
            authority_path=authority_path,
            authority_sha256=authority_sha256,
        )
        text = manifest_text(manifest)
        write_manifest(args.out, text)
    except QualificationError as exc:
        parser.error(str(exc))
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
