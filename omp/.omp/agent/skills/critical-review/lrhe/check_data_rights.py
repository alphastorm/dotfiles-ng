#!/usr/bin/env python3
"""Resolve one provider egress decision before a request is assembled.

Exit status is the runner contract:

    0   allow       stdout is the validated data_rights record; embed it verbatim
    10  deny        policy resolved and forbids this use
    20  unresolved  a required fact is missing, unknown, or contradictory
    2   usage       argparse

Deny and unresolved both stop egress. They stay distinct because an explicit
prohibition and missing evidence need different remediation: one is a decision,
the other is homework.

Two distinctions do the real work here.

**Gating facts vs. downstream-use facts.** Whether this classification may travel
this route is a gate: unknown means stop. Whether the raw response may later be
captured, exported, or used to train a router is *not* a gate -- it is a
restriction recorded on the record and enforced later. Conflating them forces the
policy author to write `rawOutputCaptureStatus: allowed` just to get a public
benchmark item through, which is how a registry ends up asserting a permission
nobody was given. `contract_pending` is a legitimate, shippable value.

**Demanded controls vs. observed controls.** A policy's `requiredControls` states
what must be true of the account. Re-reading that same file to confirm it is
checking the policy against itself. Facts about the live account -- notably
Claude's model-improvement setting -- must be supplied by the caller, and their
absence on a route that demands them is unresolved, never a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

HERE = Path(__file__).parent

EXIT_ALLOW = 0
EXIT_DENY = 10
EXIT_UNRESOLVED = 20

# Which policy field permits which classification. Anything absent from this map
# has no route to permission and is denied -- including `unknown`, which is the
# whole point: an unclassified input is not a public one.
CLASSIFICATION_GATE = {
    "public_corpus": "publicCorpusAllowed",
    "carrythrough_owned_internal": "carrythroughOwnedInternalAllowed",
    "customer_confidential": "customerDataAllowed",
    "third_party_confidential": "thirdPartyConfidentialAllowed",
}

# Classifications with no permitting field at all. Listed explicitly so a reader
# can see they were considered and refused, not merely forgotten.
ALWAYS_BLOCKED = {
    "personal_data",
    "secrets_or_credentials",
    "export_or_regulatory_restricted",
    "unknown",
}

# Only this classification may proceed on the strength of the policy alone. Every
# other permitted one additionally needs per-item authorization.
SELF_AUTHORIZING = {"public_corpus"}

INPUT_OWNER = {
    "public_corpus": "public_upstream_subject_to_source_license",
    "carrythrough_owned_internal": "carrythrough_owned",
}

# Controls the route demands of the live account, and how the caller supplies the
# observation. A route listed here without its observation is unresolved.
OBSERVED_CONTROLS = {
    "claude-code-subscription": {"modelImprovementEnabled": "model_improvement_enabled"},
}

# Restrictions that hold regardless of route while the contract questions in
# notes/OPEN_QUESTIONS.md are open. These are enforced, not merely recorded.
FORBIDDEN_USES = ("routerTrainingAllowed", "modelTrainingAllowed")

INDETERMINATE = {"contract_pending", "unknown"}


class ResolutionError(Exception):
    """A required fact could not be established."""


# ------------------------------------------------------------------ plumbing

def _bool_arg(value: str) -> bool:
    v = value.strip().lower()
    if v in {"true", "1", "yes"}:
        return True
    if v in {"false", "0", "no"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResolutionError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResolutionError(f"{label} {path} must contain a JSON object")
    return value


def _validation_errors(instance: Any, schema: dict[str, Any]) -> list[str]:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ResolutionError(f"invalid JSON Schema: {exc.message}") from exc
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path)
        + f": {e.message}"
        for e in sorted(validator.iter_errors(instance),
                        key=lambda e: (list(e.absolute_path), e.message))
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_registry(path: Path, schema_path: Path) -> dict[str, Any]:
    try:
        registry = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ResolutionError(f"cannot read policy registry {path}: {exc}") from exc
    if not isinstance(registry, dict):
        raise ResolutionError(f"policy registry {path} must contain a mapping")

    errors = _validation_errors(registry, _read_json(schema_path, "provider-policy schema"))
    if errors:
        raise ResolutionError("policy registry failed validation: " + "; ".join(errors))

    ids = [p["policyId"] for p in registry["policies"]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ResolutionError(f"duplicate policyId values: {', '.join(dupes)}")
    return registry


# ------------------------------------------------------------------ decision

def _emit(decision: str, reason_code: str, message: str, args) -> int:
    json.dump({
        "decision": decision,
        "reason_code": reason_code,
        "message": message,
        "item_id": args.item_id,
        "classification": args.classification,
        "provider_route": args.provider_route,
        "policy_id": args.policy_id,
    }, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return EXIT_DENY if decision == "deny" else EXIT_UNRESOLVED


def evaluate(args: argparse.Namespace) -> int:
    deny = lambda c, m: _emit("deny", c, m, args)            # noqa: E731
    unresolved = lambda c, m: _emit("unresolved", c, m, args)  # noqa: E731

    try:
        registry = _load_registry(args.policies, args.policy_schema)
    except ResolutionError as exc:
        return unresolved("registry_unresolved", str(exc))

    policies = {p["policyId"]: p for p in registry["policies"]}
    policy = policies.get(args.policy_id)
    if policy is None:
        return unresolved("unknown_policy",
                          f"policyId {args.policy_id!r} is not in the registry")
    if policy["providerRoute"] != args.provider_route:
        return unresolved(
            "route_mismatch",
            f"policyId {args.policy_id!r} governs route {policy['providerRoute']!r}, "
            f"not {args.provider_route!r}",
        )

    # 1. May this classification travel at all?
    if args.classification in ALWAYS_BLOCKED:
        return deny("classification_blocked",
                    f"{args.classification} has no permitting policy field on any route")
    gate = CLASSIFICATION_GATE.get(args.classification)
    if gate is None:
        return unresolved("unknown_classification",
                          f"classification {args.classification!r} is not recognized")
    permitted = policy.get(gate)
    if permitted is None or permitted in INDETERMINATE:
        return unresolved("indeterminate_classification_gate",
                          f"policy field {gate} is {permitted!r}")
    if permitted is not True:
        return deny("classification_not_permitted",
                    f"{gate} is false for policy {args.policy_id}")

    # 2. Per-item authorization, for everything the policy does not self-authorize.
    if args.classification not in SELF_AUTHORIZING and args.item_authorized is not True:
        return deny(
            "item_authorization_required",
            f"{args.classification} requires explicit per-item authorization; pass "
            "--item-authorized only when the item record actually carries it",
        )

    # 3. Controls the route demands of the live account. The policy states the
    #    demand; the caller must supply the observation.
    for control, dest in OBSERVED_CONTROLS.get(args.provider_route, {}).items():
        demanded = policy.get("requiredControls", {}).get(control)
        observed = getattr(args, dest)
        if demanded is None:
            return unresolved("missing_required_control",
                              f"route {args.provider_route} needs requiredControls.{control}")
        if observed is None:
            return unresolved(
                "unobserved_control",
                f"route {args.provider_route} demands {control}={demanded!r} but no "
                f"observation was supplied; pass --{dest.replace('_', '-')}",
            )
        if observed != demanded:
            return deny("control_violated",
                        f"{control} is {observed!r}; policy demands {demanded!r}")

    # 4. Uses that stay forbidden while the contract questions are open. Unlike the
    #    capture/retention descriptors these are enforced: a registry that flipped
    #    one to true would be granting a permission nobody has been given.
    for field in FORBIDDEN_USES:
        if policy.get(field) is not False:
            return deny("forbidden_use_enabled",
                        f"{field} must be false until written clarification is on file")
    if policy.get("internalEvaluationAllowed") is not True:
        return deny("internal_evaluation_not_allowed",
                    "internalEvaluationAllowed must be true to run an evaluation")

    record = {
        "record_id": f"rights-{args.item_id}-{args.provider_route}",
        "item_id": args.item_id,
        "repository_id": args.repository_id,
        "classification": args.classification,
        "input_owner": INPUT_OWNER[args.classification],
        "rights_basis": args.rights_basis or [
            f"{args.classification} permitted by {gate} in policy {args.policy_id}",
            f"terms snapshot {policy['termsSnapshotId']}",
        ],
        "explicit_authorization": True,
        "authorized_provider_routes": [args.provider_route],
        "policy_id": policy["policyId"],
        "terms_snapshot_id": policy["termsSnapshotId"],
        "provider_route": policy["providerRoute"],
        "account_type": policy["accountType"],
        "provider_authorized": True,
        "customer_data_allowed": policy["customerDataAllowed"],
        "third_party_confidential_allowed": policy["thirdPartyConfidentialAllowed"],
        "model_improvement_enabled": args.model_improvement_enabled,
        "provider_training_use": policy["providerTrainingUse"],
        "provider_retention": policy["providerRetention"],
        # Recorded, not gating. `contract_pending` here means the raw response is
        # held under the evaluation record and may not be exported or reused until
        # the clarification in notes/OPEN_QUESTIONS.md comes back.
        "raw_output_capture_status": policy["rawOutputCaptureStatus"],
        "internal_evaluation_allowed": policy["internalEvaluationAllowed"],
        "router_training_allowed": policy["routerTrainingAllowed"],
        "model_training_allowed": policy["modelTrainingAllowed"],
        "egress_decision": "allow",
        "decision_reason": (
            f"{args.classification} permitted by {gate}; route {args.provider_route} "
            f"authorized under {policy['policyId']}; router and model training remain "
            f"blocked and raw capture is {policy['rawOutputCaptureStatus']}."
        ),
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checked_by": "check_data_rights.py",
        "policy_digest": _sha256(args.policies),
    }
    if policy.get("termsSnapshotDigest"):
        record["terms_snapshot_digest"] = policy["termsSnapshotDigest"]
    record = {k: v for k, v in record.items() if v is not None}

    try:
        errors = _validation_errors(record, _read_json(args.data_rights_schema,
                                                       "data-rights schema"))
    except ResolutionError as exc:
        return unresolved("output_schema_unresolved", str(exc))
    if errors:
        # The guard refusing its own output is a bug in the guard, but it must
        # still fail closed rather than emit an unvalidated record.
        return unresolved("output_record_invalid",
                          "generated record failed validation: " + "; ".join(errors))

    json.dump(record, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return EXIT_ALLOW


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--item-id", required=True)
    ap.add_argument("--repository-id", default=None)
    ap.add_argument("--classification", required=True,
                    help="one of: " + ", ".join(sorted(set(CLASSIFICATION_GATE) | ALWAYS_BLOCKED)))
    ap.add_argument("--provider-route", "--route", dest="provider_route", required=True)
    ap.add_argument("--policy-id", required=True)
    ap.add_argument("--item-authorized", dest="item_authorized", action="store_true",
                    default=False,
                    help="the item record carries explicit authorization for this route. "
                         "Omission is not authorization")
    ap.add_argument("--model-improvement-enabled", dest="model_improvement_enabled",
                    type=_bool_arg, default=None,
                    help="OBSERVED account setting, not the policy's demand. Required on "
                         "routes whose policy demands it; absence is unresolved")
    ap.add_argument("--rights-basis", nargs="*", default=None,
                    help="override the recorded basis strings")
    ap.add_argument("--policies", type=Path, default=HERE / "provider-policies.yaml")
    ap.add_argument("--policy-schema", type=Path, default=HERE / "provider-policy.schema.json")
    ap.add_argument("--data-rights-schema", type=Path, default=HERE / "data-rights.schema.json")
    return evaluate(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
