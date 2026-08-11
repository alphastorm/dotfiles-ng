#!/usr/bin/env python3
"""Fail-closed reader for critical-review qualification and live panel roles.

Schema v6 adds exactly one dispatch role: a conditional critic. Unconditional
critics dispatch on every full council. A conditional critic is additive -- it
dispatches only when the frozen record and its immutable packet fall inside the
eligibility its policy pins here, and it never replaces, substitutes for, or
falls back to another reviewer. A conditional critic that is skipped is recorded
as skipped and the council proceeds without it.

Roster choice belongs to this resolver, not to its caller. `initial` refuses to
answer without a frozen record, its packet, and a durable manifest path. It
reuses the existing strict record validator and full readiness gate, binds the
absolute record path, record digest, proof-subject digest, packet path, and
packet digest into one schema-v1 selection manifest, writes that manifest
exactly once, and prints the same bytes it wrote. A caller that filters,
reorders, retypes, or supplements that roster is dispatching a panel nobody
resolved. `initial` is the full-council command, not a record mode: it resolves
any record the gate answers `full-council` for, which is `design`, `initial`, or
`material-redesign`.

`targeted-refuter` stays a separate fixed roster: one cold falsification of one
disputed claim, never a conditional critic.
"""

from __future__ import annotations

import argparse
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
    RISK_DOMAINS,
    _is_session_local,
    _sha256,
    proof_subject_digest,
    select_review_action,
)

SCHEMA_VERSION = 6
# Pinned beside the schema version so activation is atomic: a v6 resolver and a
# v2 panel definition cannot half-agree about who is on the council.
LIVE_PANEL_ID = "critical-review-primary-v3"
MANIFEST_SCHEMA_VERSION = 1
DEFAULT_QUALIFICATION = Path.home() / ".omp/agent/skills/critical-review/qualification.yml"
CANARY_AUTHORITIES = frozenset({"historical-non-scoring", "evaluation", "live-qualification"})
PROVIDER_CANARY_AUTHORITIES = frozenset({"evaluation", "live-qualification"})
READ_ONLY_REPOSITORY_TOOLS = ("read", "grep", "glob", "lsp", "ast_grep")
LIVE_GROUPS = {
    "initialCritics": ("primary_critic", True),
    "conditionalCritics": ("conditional_critic", True),
    "targetedRefuters": ("targeted_refuter", True),
    "evaluationOnly": ("evaluation_only", False),
    "disabled": ("disabled", False),
}
CONDITIONAL_ROLE = "conditional_critic"
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
        "allRiskDomainsIn",
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

MANIFEST_MODES = ("initial", "targeted-refuter", "qualification-canary")
UNCONDITIONAL_REASON_CODE = "configured-primary-critic"
TARGETED_REFUTER_REASON_CODE = "configured-targeted-refuter"
QUALIFICATION_CANARY_REASON_CODE = "qualification-canary-only"
# Closed and sorted on emission, so two resolutions of one record never disagree
# and a new skip reason cannot enter a manifest without a schema change.
SKIP_REASON_CODES = (
    "authorization-proof-applicable",
    "review-mode-ineligible",
    "risk-domain-outside-allowlist",
    "risk-domains-empty",
    "security-sensitive-path",
)


@dataclass(frozen=True)
class ConditionalPolicy:
    """What a conditional critic may review, pinned in public code.

    Membership stays private configuration; scope does not. Pinning the allowed
    modes, allowed risk domains, required proof statuses, deny regex, and cohort
    promotion gates here means a private edit can retire a conditional critic
    but cannot quietly widen one into security review.
    """

    policy: str
    required_scope: str
    allowed_review_modes: tuple[str, ...]
    allowed_risk_domains: tuple[str, ...]
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


FABLE_NON_SECURITY_ARCHITECTURE_V1 = ConditionalPolicy(
    policy="fable-non-security-architecture-v1",
    required_scope="non-security-architecture",
    allowed_review_modes=("design", "initial", "material-redesign"),
    allowed_risk_domains=(
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
    # The packet's six promotion gates, integers so a cohort is graded by exact
    # arithmetic instead of float comparison.
    cohort_schema="lrhe-fable-architecture-cohort-v1",
    cohort_min_eligible_attempts=20,
    cohort_max_security_misroutes=0,
    cohort_min_completion_percent=90,
    cohort_min_served_model_match_percent=100,
    cohort_max_forbidden_tool_attempts=0,
    cohort_max_negative_control_percent=10,
)
CONDITIONAL_POLICIES: Mapping[str, ConditionalPolicy] = {
    FABLE_NON_SECURITY_ARCHITECTURE_V1.policy: FABLE_NON_SECURITY_ARCHITECTURE_V1
}


class QualificationError(ValueError):
    """The qualification record cannot safely drive dispatch or evaluation."""


@dataclass(frozen=True)
class LiveReviewer:
    family: str
    agent: str
    lens: str
    model: str
    evidence_delivery: str


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
        raise QualificationError(f"{field} must be a list of family names")
    sequence = cast(Sequence[object], value)
    names: list[str] = []
    for item in sequence:
        if not isinstance(item, str) or not item.strip():
            raise QualificationError(f"{field} contains a non-name entry")
        names.append(item.strip())
    if len(names) != len(set(names)):
        raise QualificationError(f"{field} contains duplicate families")
    return tuple(names)


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


def _scope_receipt(family: str, entry: Mapping[str, object], policy: ConditionalPolicy) -> str:
    """Validate one conditional critic's nested scoped qualification.

    The scoped shape is the point: a lane can be `passed` for the scope it was
    measured in and explicitly `ineligible` for a scope it refused, without the
    refusal evidence being rewritten as a passed canary and without the passed
    scope being read as blanket authority.
    """

    field = f"reviewers.{family}.qualification"
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
        raise QualificationError(f"reviewers.{family}.model is missing")
    if common.get("exactServedModelRequired") != selector_model(selector):
        raise QualificationError(
            f"{field}.common.exactServedModelRequired must be "
            f"{selector_model(selector)!r} for selector {selector!r}, got "
            f"{common.get('exactServedModelRequired')!r}"
        )
    if selector_thinking_level(selector) != policy.thinking_level:
        raise QualificationError(
            f"reviewers.{family}.model must resolve at thinking level "
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


def _eligibility(family: str, entry: Mapping[str, object]) -> ConditionalPolicy:
    """Validate one conditional critic's eligibility block against its policy."""

    field = f"reviewers.{family}.eligibility"
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
    domains = _names(block.get("allRiskDomainsIn"), f"{field}.allRiskDomainsIn")
    if domains != policy.allowed_risk_domains:
        raise QualificationError(
            f"{field}.allRiskDomainsIn must be {list(policy.allowed_risk_domains)!r} "
            f"for {policy_id}, got {list(domains)!r}"
        )
    unknown = sorted(set(domains) - RISK_DOMAINS)
    if unknown:
        raise QualificationError(f"{field}.allRiskDomainsIn names unknown domains {unknown}")
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
            f"reviewers.{family}.fallbackAllowed must be {policy.fallback_allowed} "
            f"for {policy_id}: a conditional critic is additive, never a fallback"
        )
    return policy


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

    live = _mapping(root.get("liveDispatch"), "liveDispatch")
    panel_id = live.get("panelId")
    if panel_id != LIVE_PANEL_ID:
        raise QualificationError(
            f"liveDispatch.panelId must be {LIVE_PANEL_ID!r}, got {panel_id!r}"
        )

    lead_family = live.get("leadFamily")
    if not isinstance(lead_family, str) or not lead_family.strip():
        raise QualificationError("liveDispatch.leadFamily must be a non-empty string")
    lead_family = lead_family.strip()

    reviewers = _mapping(root.get("reviewers"), "reviewers")
    groups = {group: _names(live.get(group), f"liveDispatch.{group}") for group in LIVE_GROUPS}
    for group in ("initialCritics", "conditionalCritics", "targetedRefuters"):
        if lead_family in groups[group]:
            raise QualificationError(
                f"lead family {lead_family!r} cannot appear in liveDispatch.{group}"
            )

    memberships: dict[str, str] = {}
    for group, (role, dispatch_enabled) in LIVE_GROUPS.items():
        families = groups[group]
        for family in families:
            if family in memberships:
                raise QualificationError(
                    f"reviewer {family!r} appears in both {memberships[family]} and {group}"
                )
            memberships[family] = group
            entry = _mapping(reviewers.get(family), f"reviewers.{family}")
            if entry.get("dispatchRole") != role:
                raise QualificationError(
                    f"reviewers.{family}.dispatchRole must be {role!r} for {group}"
                )
            if entry.get("dispatchEnabled") is not dispatch_enabled:
                raise QualificationError(
                    f"reviewers.{family}.dispatchEnabled disagrees with {group}"
                )
            if not isinstance(entry.get("evaluationEnabled"), bool):
                raise QualificationError(f"reviewers.{family}.evaluationEnabled must be boolean")

            earned = dispatch_enabled or entry.get("evaluationEnabled") is True
            if earned:
                # A conditional critic proves the same three things per scope
                # instead of once globally, so the flat gate is replaced rather
                # than skipped -- see `_scope_receipt`.
                required = (
                    ()
                    if role == CONDITIONAL_ROLE
                    else (
                        ("providerCanary", "passed"),
                        ("schemaValid", True),
                        ("readOnlyBoundary", "passed"),
                    )
                )
                missing = [name for name, expected in required if entry.get(name) != expected]
                if missing:
                    raise QualificationError(
                        f"reviewers.{family} enabled without proven {', '.join(missing)}"
                    )
                for name in ("agent", "model"):
                    value = entry.get(name)
                    if not isinstance(value, str) or not value.strip():
                        raise QualificationError(f"reviewers.{family}.{name} is missing")

            contract_fields = ("evidenceDelivery", "tools", "canaryReceipt")
            present = [name for name in contract_fields if name in entry]
            if present:
                missing_contract = [name for name in contract_fields if name not in entry]
                if missing_contract:
                    raise QualificationError(
                        f"reviewers.{family} incomplete evidence contract: "
                        f"missing {', '.join(missing_contract)}"
                    )
                evidence_delivery = entry.get("evidenceDelivery")
                if evidence_delivery not in ("inline", "repository"):
                    raise QualificationError(
                        f"reviewers.{family}.evidenceDelivery must be 'inline' or "
                        f"'repository', got {evidence_delivery!r}"
                    )
                tools = _names(entry.get("tools"), f"reviewers.{family}.tools")
                expected_tools = () if evidence_delivery == "inline" else READ_ONLY_REPOSITORY_TOOLS
                if tools != expected_tools:
                    raise QualificationError(
                        f"reviewers.{family}.tools must be {list(expected_tools)!r} for "
                        f"{evidence_delivery} delivery, got {list(tools)!r}"
                    )
                receipt = entry.get("canaryReceipt")
                if not isinstance(receipt, str) or not receipt.strip():
                    raise QualificationError(
                        f"reviewers.{family}.canaryReceipt must be a non-empty relative path"
                    )
                if Path(receipt).is_absolute() or ".." in Path(receipt).parts:
                    raise QualificationError(
                        f"reviewers.{family}.canaryReceipt must stay under the skill directory"
                    )

            if role == CONDITIONAL_ROLE:
                _scope_receipt(family, entry, _eligibility(family, entry))
            else:
                for stray in ("eligibility", "qualification"):
                    if stray in entry:
                        raise QualificationError(
                            f"reviewers.{family} declares {stray} without the "
                            f"{CONDITIONAL_ROLE!r} role; scope and role activate together"
                        )

    reviewer_names = set(reviewers.keys())
    assigned_names = set(memberships)
    if reviewer_names != assigned_names:
        missing = sorted(reviewer_names - assigned_names)
        unknown = sorted(assigned_names - reviewer_names)
        raise QualificationError(
            f"liveDispatch membership mismatch: unassigned={missing}, unknown={unknown}"
        )
    return root


def load_qualification(path: Path = DEFAULT_QUALIFICATION) -> Mapping[str, object]:
    if not path.is_file():
        raise QualificationError(f"qualification file is not readable: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise QualificationError(f"qualification file cannot be parsed: {exc}") from exc
    return validate_qualification(document)


def reviewers(document: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(document.get("reviewers"), "reviewers")


def _reviewer(document: Mapping[str, object], family: str) -> LiveReviewer:
    entry = _mapping(reviewers(document).get(family), f"reviewers.{family}")
    lens_value = entry.get("lens")
    return LiveReviewer(
        family=family,
        agent=cast(str, entry["agent"]),
        lens=lens_value if isinstance(lens_value, str) else "",
        model=cast(str, entry["model"]),
        evidence_delivery=(
            cast(str, entry["evidenceDelivery"])
            if isinstance(entry.get("evidenceDelivery"), str)
            else "repository"
        ),
    )


def live_reviewers(document: Mapping[str, object], mode: str) -> tuple[LiveReviewer, ...]:
    """Return one fixed roster: the unconditional critics or the refutation pool.

    Neither roster ever contains a conditional critic. `initial` here is the
    unconditional floor that `select_full_council` starts from, which is why a
    skipped conditional critic can never shrink a council.
    """

    group = {"initial": "initialCritics", "targeted-refuter": "targetedRefuters"}.get(mode)
    if group is None:
        raise QualificationError(f"unsupported live review mode: {mode}")
    live = _mapping(document.get("liveDispatch"), "liveDispatch")
    return tuple(
        _reviewer(document, family) for family in _names(live.get(group), f"liveDispatch.{group}")
    )


def conditional_critics(document: Mapping[str, object]) -> tuple[ConditionalCritic, ...]:
    """Return every declared conditional critic with its policy and scope receipt."""

    live = _mapping(document.get("liveDispatch"), "liveDispatch")
    entries = reviewers(document)
    result: list[ConditionalCritic] = []
    for family in _names(live.get("conditionalCritics"), "liveDispatch.conditionalCritics"):
        entry = _mapping(entries.get(family), f"reviewers.{family}")
        policy = _eligibility(family, entry)
        result.append(
            ConditionalCritic(
                reviewer=_reviewer(document, family),
                policy=policy,
                scope_receipt=_scope_receipt(family, entry, policy),
            )
        )
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
    """

    collected: list[str] = []
    for field in PACKET_FIELDS:
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
    elif not domains <= set(policy.allowed_risk_domains):
        reasons.add("risk-domain-outside-allowlist")
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


def parse_packet(path: Path) -> Mapping[str, object]:
    """Parse the fixed packet context out of one immutable packet file."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise QualificationError(f"packet is not readable: {exc}") from exc
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
    return packet


def bind_record(path: Path) -> tuple[Path, str, Mapping[str, object]]:
    """Resolve one frozen record to its absolute path, digest, and payload."""

    resolved = path.expanduser().resolve()
    if _is_session_local(resolved):
        raise QualificationError(f"review record must not be session-local: {resolved}")
    if not resolved.is_file():
        raise QualificationError(f"review record is not readable: {resolved}")
    try:
        value: object = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"review record cannot be parsed: {exc}") from exc
    return resolved, _sha256(resolved), _mapping(value, "review record")


def bind_packet(
    path: Path, record_path: Path, record_sha256: str
) -> tuple[Path, str, Mapping[str, object]]:
    """Resolve one packet and prove it names exactly the record being resolved."""

    resolved = path.expanduser().resolve()
    if _is_session_local(resolved):
        raise QualificationError(f"packet must not be session-local: {resolved}")
    if not resolved.is_file():
        raise QualificationError(f"packet is not readable: {resolved}")
    packet = parse_packet(resolved)
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
    return resolved, _sha256(resolved), packet


def _selected(
    reviewer: LiveReviewer, selection_class: str, reason_codes: Sequence[str]
) -> dict[str, object]:
    return {
        "family": reviewer.family,
        "agent": reviewer.agent,
        "lens": reviewer.lens,
        "model": reviewer.model,
        "evidence_delivery": reviewer.evidence_delivery,
        "selectionClass": selection_class,
        "reasonCodes": list(reason_codes),
    }


def _manifest(
    *,
    mode: str,
    panel_id: str,
    record: Mapping[str, object],
    record_path: Path,
    record_sha256: str,
    packet_path: Path,
    packet_sha256: str,
    selected: Sequence[Mapping[str, object]],
    skipped: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    try:
        subject_digest = proof_subject_digest(record)
    except ValueError as exc:
        raise QualificationError(f"record has no proof subject digest: {exc}") from exc
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "panelId": panel_id,
        "mode": mode,
        "reviewRecordPath": str(record_path),
        "reviewRecordSha256": record_sha256,
        "subjectDigest": subject_digest,
        # Beyond the packet's recommended shape, and deliberately so: binding the
        # packet bytes as well as the record bytes closes the window in which a
        # resolved packet is edited between resolution and dispatch.
        "packetPath": str(packet_path),
        "packetSha256": packet_sha256,
        "selected": [dict(entry) for entry in selected],
        "skipped": [dict(entry) for entry in skipped],
    }


def select_full_council(
    document: Mapping[str, object],
    record: Mapping[str, object],
    packet: Mapping[str, object],
    *,
    record_path: Path,
    record_sha256: str,
    packet_path: Path,
    packet_sha256: str,
) -> dict[str, object]:
    """Resolve one full council: unconditional critics plus eligible conditionals."""

    decision = select_review_action(record)
    if decision.action != "full-council":
        raise QualificationError(
            f"record does not authorize a full council: status={decision.status!r}, "
            f"action={decision.action!r}, reasons={list(decision.reason_codes)}"
        )
    live = _mapping(document.get("liveDispatch"), "liveDispatch")
    selected = [
        _selected(reviewer, "unconditional", (UNCONDITIONAL_REASON_CODE,))
        for reviewer in live_reviewers(document, "initial")
    ]
    skipped: list[dict[str, object]] = []
    if not selected:
        # Checked here rather than in `validate_qualification`: an evaluation-only
        # checkout legitimately declares no live critic, but a council whose whole
        # roster is conditional could be emptied by one skip.
        raise QualificationError(
            "liveDispatch.initialCritics selects no unconditional critic; a full "
            "council cannot consist of conditional critics alone"
        )
    sources = packet_paths(packet)
    for candidate in conditional_critics(document):
        reasons = fable_skip_reason_codes(candidate.policy, record, sources)
        if reasons:
            skipped.append(
                {
                    "family": candidate.reviewer.family,
                    "selectionClass": "conditional",
                    "reasonCodes": list(reasons),
                }
            )
        else:
            selected.append(
                _selected(candidate.reviewer, "conditional", (candidate.policy.policy,))
            )
    return _manifest(
        mode="initial",
        panel_id=cast(str, live["panelId"]),
        record=record,
        record_path=record_path,
        record_sha256=record_sha256,
        packet_path=packet_path,
        packet_sha256=packet_sha256,
        selected=selected,
        skipped=skipped,
    )


def select_qualification_canary(
    document: Mapping[str, object],
    record: Mapping[str, object],
    packet: Mapping[str, object],
    *,
    family: str,
    policy_id: str,
    selector: str,
    record_path: Path,
    record_sha256: str,
    packet_path: Path,
    packet_sha256: str,
) -> dict[str, object]:
    """Resolve the one narrow manifest that qualifies a conditional critic.

    The production reviewer agent will not run without a resolver manifest, and
    a lane cannot become a conditional critic before its scoped cohort receipt
    exists. That bootstrap is made explicit here instead of being solved by
    relaxing live selection or by recording a receipt nobody earned: this
    manifest names exactly one candidate, carries no unconditional roster, and
    states `qualification-canary-only` as its reason. It cannot be consumed as a
    council roster and it authorizes no live dispatch.
    """

    if policy_id not in CONDITIONAL_POLICIES:
        raise QualificationError(
            f"unknown conditional policy {policy_id!r}; expected one of "
            f"{sorted(CONDITIONAL_POLICIES)}"
        )
    policy = CONDITIONAL_POLICIES[policy_id]
    entry = _mapping(reviewers(document).get(family), f"reviewers.{family}")
    role = entry.get("dispatchRole")
    if role not in (CONDITIONAL_ROLE, "disabled"):
        raise QualificationError(
            f"reviewers.{family}.dispatchRole is {role!r}; a qualification canary may only "
            f"target a {CONDITIONAL_ROLE!r} or 'disabled' lane"
        )
    agent = entry.get("agent")
    declared_selector = entry.get("model")
    if not isinstance(agent, str) or not agent.strip():
        raise QualificationError(f"reviewers.{family}.agent is missing")
    if not isinstance(declared_selector, str) or not declared_selector.strip():
        raise QualificationError(f"reviewers.{family}.model is missing")
    if selector_model(selector) != selector_model(declared_selector):
        raise QualificationError(
            f"canary selector {selector!r} names a different model than "
            f"reviewers.{family}.model {declared_selector!r}"
        )
    if selector_thinking_level(selector) != policy.thinking_level:
        raise QualificationError(
            f"canary selector {selector!r} must resolve at thinking level "
            f"{policy.thinking_level!r} for {policy_id}"
        )
    if role == CONDITIONAL_ROLE and _eligibility(family, entry).policy != policy_id:
        raise QualificationError(
            f"reviewers.{family} is qualified under a different policy than {policy_id!r}"
        )

    decision = select_review_action(record)
    if decision.action != "full-council":
        raise QualificationError(
            f"canary record does not authorize a council-shaped review: "
            f"status={decision.status!r}, action={decision.action!r}, "
            f"reasons={list(decision.reason_codes)}"
        )
    reasons = fable_skip_reason_codes(policy, record, packet_paths(packet))
    if reasons:
        raise QualificationError(
            f"canary record is outside {policy_id}: {list(reasons)}; a qualification "
            "cohort must run inside the eligibility it is qualifying"
        )
    lens = entry.get("lens")
    reviewer = LiveReviewer(
        family=family,
        agent=agent,
        lens=lens if isinstance(lens, str) else "",
        model=selector,
        evidence_delivery=(
            cast(str, entry["evidenceDelivery"])
            if isinstance(entry.get("evidenceDelivery"), str)
            else "repository"
        ),
    )
    live = _mapping(document.get("liveDispatch"), "liveDispatch")
    return _manifest(
        mode="qualification-canary",
        panel_id=cast(str, live["panelId"]),
        record=record,
        record_path=record_path,
        record_sha256=record_sha256,
        packet_path=packet_path,
        packet_sha256=packet_sha256,
        selected=[_selected(reviewer, "conditional", (QUALIFICATION_CANARY_REASON_CODE,))],
        skipped=[],
    )


def manifest_text(manifest: Mapping[str, object]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def write_manifest(path: Path, text: str) -> Path:
    """Persist one resolution exactly once, outside session-local storage."""

    resolved = path.expanduser().resolve()
    if _is_session_local(resolved):
        raise QualificationError(
            f"selection manifest must be durable, not session-local: {resolved}"
        )
    if resolved.exists():
        raise QualificationError(f"refusing to overwrite existing selection manifest: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    try:
        with resolved.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise QualificationError(
            f"refusing to overwrite existing selection manifest: {resolved}"
        ) from exc
    except OSError as exc:
        raise QualificationError(f"selection manifest cannot be written: {exc}") from exc
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
    canary = commands.add_parser(
        "qualification-canary",
        help=(
            "resolve the one narrow manifest that qualifies a conditional critic; "
            "it names no unconditional reviewer and authorizes no live dispatch"
        ),
    )
    canary.add_argument("--family", required=True)
    canary.add_argument("--policy", required=True, choices=sorted(CONDITIONAL_POLICIES))
    canary.add_argument(
        "--selector", required=True, help="exact provider/model:effort the cohort qualifies"
    )
    refuter = commands.add_parser(
        "targeted-refuter",
        help="resolve the fixed refutation pool; never a conditional critic",
    )

    for command in (initial, canary):
        command.add_argument("--record", type=Path, required=True)
        command.add_argument("--packet", type=Path, required=True)
        command.add_argument(
            "--out",
            type=Path,
            required=True,
            help="durable manifest path; an existing file is never overwritten",
        )
    for command in (initial, canary, refuter):
        command.add_argument("--qualification", type=Path, default=DEFAULT_QUALIFICATION)

    args = parser.parse_args(argv)
    try:
        document = load_qualification(args.qualification)
        if args.mode == "targeted-refuter":
            roster = [
                _selected(reviewer, "unconditional", (TARGETED_REFUTER_REASON_CODE,))
                for reviewer in live_reviewers(document, "targeted-refuter")
            ]
            print(json.dumps(roster, sort_keys=True))
            return 0
        record_path, record_sha256, record = bind_record(args.record)
        packet_path, packet_sha256, packet = bind_packet(args.packet, record_path, record_sha256)
        if args.mode == "initial":
            manifest = select_full_council(
                document,
                record,
                packet,
                record_path=record_path,
                record_sha256=record_sha256,
                packet_path=packet_path,
                packet_sha256=packet_sha256,
            )
        else:
            manifest = select_qualification_canary(
                document,
                record,
                packet,
                family=args.family,
                policy_id=args.policy,
                selector=args.selector,
                record_path=record_path,
                record_sha256=record_sha256,
                packet_path=packet_path,
                packet_sha256=packet_sha256,
            )
        text = manifest_text(manifest)
        write_manifest(args.out, text)
    except QualificationError as exc:
        parser.error(str(exc))
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
