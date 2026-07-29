#!/usr/bin/env python3
"""Machine-bound dispatch gate for critical-review sequences.

The gate validates one JSON record against its frozen artifact, changed-file
bytes, proof receipts, invariant coverage, and ordered sequence history. It does
not call providers and cannot turn reviewer agreement into approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast

REVIEW_MODES = frozenset({"initial", "remediation", "material-redesign"})
ACTIONS = frozenset({"full-council", "targeted-refuter", "none"})
RISK_DOMAINS = frozenset(
    {
        "architecture",
        "authorization",
        "cache-invalidation",
        "concurrency",
        "cross-system-boundary",
        "documentation-policy",
        "money-or-assets",
        "persistent-state",
        "privacy",
        "public-protocol",
        "release-supply-chain",
        "secrets-cryptography",
    }
)
PROOF_CLASSES = (
    "fresh-process-smoke",
    "dependency-cycle",
    "cache-invalidation",
    "migration-rollback",
    "authorization",
    "repository-policy",
)
MATERIAL_CHANGE_CATEGORIES = frozenset(
    {
        "architecture",
        "trust-boundary",
        "public-compatibility",
        "persistent-state",
        "migration-rollback",
        "production-effect",
    }
)
RECORD_FIELDS = frozenset(
    {
        "review_sequence_id",
        "review_id",
        "review_mode",
        "parent_review_id",
        "sequence_history",
        "general_review_pass_count",
        "targeted_refutation_used",
        "artifact_path",
        "artifact_digest",
        "changed_files",
        "changed_file_digests",
        "proof_receipts",
        "touched_risk_domains",
        "invariant_proof_matrix",
        "proof_classes",
        "known_deterministic_failures",
        "new_risk_classes",
        "cross_subsystem_omissions",
        "incomplete_invariant_ids",
        "remediated_finding_ids",
        "resolved_finding_ids",
        "disputed_or_unresolved_p01",
        "remediation_scope",
        "lead_verification",
        "material_change_categories",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ReviewDecision:
    """One fail-closed dispatch decision."""

    status: str
    review_sequence_id: str
    review_mode: str
    action: str
    reason_codes: tuple[str, ...]
    next_step: str

    @property
    def permits_provider_dispatch(self) -> bool:
        return self.action in {"full-council", "targeted-refuter"}


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> Sequence[object] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    return cast(Sequence[object], value)


def _strict_strings(
    record: Mapping[str, object], field: str, errors: list[str], *, allow_empty: bool = True
) -> tuple[str, ...]:
    values = _sequence(record.get(field))
    if values is None:
        errors.append(f"invalid-list:{field}")
        return ()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            errors.append(f"invalid-list-entry:{field}")
            continue
        result.append(value.strip())
    if len(result) != len(set(result)):
        errors.append(f"duplicate-list-entry:{field}")
    if not allow_empty and not result:
        errors.append(f"empty-list:{field}")
    return tuple(result)


def _strict_row_strings(
    row: Mapping[str, object], field: str, prefix: str, errors: list[str]
) -> tuple[str, ...]:
    values = _sequence(row.get(field))
    if values is None:
        errors.append(f"{prefix}:{field}")
        return ()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{prefix}:{field}-entry")
            continue
        result.append(value.strip())
    if not result:
        errors.append(f"{prefix}:{field}-empty")
    if len(result) != len(set(result)):
        errors.append(f"{prefix}:{field}-duplicate")
    return tuple(result)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def proof_subject_digest(record: Mapping[str, object]) -> str:
    """Digest the exact frozen artifact and changed-file identity under proof."""

    artifact_digest = record.get("artifact_digest")
    changed_digests = _mapping(record.get("changed_file_digests"))
    if not isinstance(artifact_digest, str) or not _SHA256.fullmatch(artifact_digest):
        raise ValueError("record has no valid artifact_digest")
    if changed_digests is None or not changed_digests:
        raise ValueError("record has no changed_file_digests")
    normalized: dict[str, str] = {}
    for path, digest in changed_digests.items():
        if not path:
            raise ValueError("record has an invalid changed-file path")
        if digest != "DELETED" and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise ValueError(f"record has an invalid digest for {path}")
        normalized[path] = cast(str, digest)
    subject = {
        "artifact_digest": artifact_digest,
        "changed_file_digests": dict(sorted(normalized.items())),
    }
    return hashlib.sha256(
        json.dumps(subject, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def verify_subject_files(record: Mapping[str, object]) -> tuple[str, ...]:
    """Verify that the live artifact and changed paths equal the frozen subject."""

    errors: list[str] = []
    artifact_path_value = record.get("artifact_path")
    artifact_digest = record.get("artifact_digest")
    if not isinstance(artifact_path_value, str) or not artifact_path_value:
        errors.append("missing-artifact-path")
    elif not isinstance(artifact_digest, str) or not _SHA256.fullmatch(artifact_digest):
        errors.append("invalid-artifact-digest")
    else:
        artifact_path = Path(artifact_path_value)
        if not artifact_path.is_file():
            errors.append("artifact-not-readable")
        elif _sha256(artifact_path) != artifact_digest:
            errors.append("artifact-digest-mismatch")

    changed_files = _strict_strings(record, "changed_files", errors, allow_empty=False)
    changed_digests_value = _mapping(record.get("changed_file_digests"))
    if changed_digests_value is None:
        errors.append("missing-changed-file-digests")
        changed_digests: Mapping[str, object] = {}
    else:
        changed_digests = changed_digests_value
    if set(changed_digests.keys()) != set(changed_files):
        errors.append("changed-file-digest-coverage-mismatch")
    for changed_file in changed_files:
        expected: object = changed_digests.get(changed_file)
        if expected == "DELETED":
            if Path(changed_file).exists():
                errors.append(f"deleted-file-still-exists:{changed_file}")
        elif not isinstance(expected, str) or not _SHA256.fullmatch(expected):
            errors.append(f"invalid-changed-file-digest:{changed_file}")
        elif not Path(changed_file).is_file():
            errors.append(f"changed-file-not-readable:{changed_file}")
        elif _sha256(Path(changed_file)) != expected:
            errors.append(f"changed-file-digest-mismatch:{changed_file}")
    return tuple(errors)


def _binding_errors(record: Mapping[str, object]) -> tuple[str, ...]:
    errors = list(verify_subject_files(record))

    try:
        subject_digest = proof_subject_digest(record)
    except ValueError:
        subject_digest = ""
    receipts_value = _mapping(record.get("proof_receipts"))
    if receipts_value is None or not receipts_value:
        errors.append("missing-proof-receipts")
        receipts: Mapping[str, object] = {}
    else:
        receipts = receipts_value
    valid_receipts: set[str] = set()
    for receipt_id, value in receipts.items():
        prefix = f"invalid-proof-receipt:{receipt_id}"
        if not receipt_id:
            errors.append("invalid-proof-receipt-id")
            continue
        receipt = _mapping(value)
        if receipt is None:
            errors.append(prefix)
            continue
        path_value = receipt.get("path")
        expected = receipt.get("sha256")
        if not isinstance(path_value, str) or not isinstance(expected, str) or not _SHA256.fullmatch(expected):
            errors.append(prefix)
            continue
        path = Path(path_value)
        if not path.is_file() or _sha256(path) != expected:
            errors.append(prefix)
            continue
        try:
            payload_value: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(prefix)
            continue
        payload = _mapping(payload_value)
        if (
            payload is None
            or payload.get("schemaVersion") != 1
            or payload.get("result") != "passed"
            or payload.get("exit_code") != 0
            or payload.get("subject_digest") != subject_digest
        ):
            errors.append(prefix)
            continue
        valid_receipts.add(receipt_id)

    proof_classes = _mapping(record.get("proof_classes"))
    if proof_classes is None:
        errors.append("missing-proof-classes")
    else:
        for proof_class in PROOF_CLASSES:
            row = _mapping(proof_classes.get(proof_class))
            if row is None:
                errors.append(f"invalid-proof-class:{proof_class}")
                continue
            status = row.get("status")
            evidence = row.get("evidence_or_justification")
            if status not in {"passed", "not-applicable"}:
                errors.append(f"invalid-proof-class:{proof_class}")
            if not isinstance(evidence, str) or not evidence.strip():
                errors.append(f"missing-proof-evidence:{proof_class}")
            receipt_id = row.get("receipt_id")
            if status == "passed" and receipt_id not in valid_receipts:
                errors.append(f"missing-proof-receipt:{proof_class}")
            if status == "not-applicable" and receipt_id not in (None, ""):
                errors.append(f"unexpected-proof-receipt:{proof_class}")

    return tuple(errors)


def _history_errors(record: Mapping[str, object], mode: object) -> tuple[str, ...]:
    errors: list[str] = []
    history_values = _sequence(record.get("sequence_history"))
    if history_values is None:
        return ("invalid-sequence-history",)

    history_ids: list[str] = []
    general_passes = 0
    refutations = 0
    for index, value in enumerate(history_values):
        prefix = f"invalid-history-row:{index}"
        row = _mapping(value)
        if row is None:
            errors.append(prefix)
            continue
        review_id = row.get("review_id")
        review_mode = row.get("review_mode")
        action = row.get("action")
        if not isinstance(review_id, str) or not review_id.strip():
            errors.append(f"{prefix}:review-id")
        else:
            history_ids.append(review_id.strip())
        if review_mode not in REVIEW_MODES:
            errors.append(f"{prefix}:review-mode")
        if action not in ACTIONS:
            errors.append(f"{prefix}:action")
        elif action == "full-council":
            general_passes += 1
        elif action == "targeted-refuter":
            refutations += 1
        record_path_value = row.get("record_path")
        record_digest = row.get("record_sha256")
        if (
            not isinstance(record_path_value, str)
            or not isinstance(record_digest, str)
            or not _SHA256.fullmatch(record_digest)
        ):
            errors.append(f"{prefix}:record-binding")
        else:
            record_path = Path(record_path_value)
            if not record_path.is_file() or _sha256(record_path) != record_digest:
                errors.append(f"{prefix}:record-binding")
    if len(history_ids) != len(set(history_ids)):
        errors.append("duplicate-history-review-id")

    review_id = record.get("review_id")
    if isinstance(review_id, str) and review_id in history_ids:
        errors.append("current-review-already-in-history")
    parent = record.get("parent_review_id")
    if mode == "initial":
        if history_ids:
            errors.append("initial-review-has-history")
        if parent not in (None, ""):
            errors.append("initial-review-has-parent")
    else:
        if not history_ids:
            errors.append("missing-prior-review-history")
        elif parent != history_ids[-1]:
            errors.append("parent-review-not-latest-history")

    pass_count = record.get("general_review_pass_count")
    if pass_count != general_passes:
        errors.append("general-review-pass-count-mismatch")
    if general_passes > 2:
        errors.append("general-review-pass-limit-exceeded")
    if mode == "initial" and general_passes != 0:
        errors.append("initial-review-already-dispatched")
    if mode == "material-redesign" and general_passes >= 2:
        errors.append("general-review-pass-limit-reached")
    if mode == "material-redesign" and general_passes != 1:
        errors.append("material-redesign-requires-initial-pass")
    if mode == "remediation" and general_passes not in {1, 2}:
        errors.append("remediation-requires-prior-general-pass")

    targeted_used = record.get("targeted_refutation_used")
    if not isinstance(targeted_used, bool) or targeted_used != (refutations > 0):
        errors.append("targeted-refutation-history-mismatch")
    if refutations > 1:
        errors.append("targeted-refutation-limit-exceeded")
    return tuple(errors)


def _matrix_errors(
    record: Mapping[str, object], changed_files: set[str], touched_domains: set[str]
) -> tuple[str, ...]:
    errors: list[str] = []
    matrix = _sequence(record.get("invariant_proof_matrix"))
    if not matrix:
        return ("missing-invariant-proof-matrix",)
    seen_invariants: set[str] = set()
    covered_paths: set[str] = set()
    covered_domains: set[str] = set()
    for index, value in enumerate(matrix):
        prefix = f"invalid-invariant-row:{index}"
        row = _mapping(value)
        if row is None:
            errors.append(prefix)
            continue
        invariant_id = row.get("invariant_id")
        if not isinstance(invariant_id, str) or not invariant_id.strip():
            errors.append(f"{prefix}:invariant-id")
        elif invariant_id in seen_invariants:
            errors.append(f"{prefix}:duplicate-invariant-id")
        else:
            seen_invariants.add(invariant_id)
        paths = set(_strict_row_strings(row, "changed_paths", prefix, errors))
        domains = set(_strict_row_strings(row, "risk_domains", prefix, errors))
        covered_paths.update(paths)
        covered_domains.update(domains)
        if paths - changed_files:
            errors.append(f"{prefix}:unknown-changed-path")
        if domains - touched_domains:
            errors.append(f"{prefix}:unknown-risk-domain")
        for field in ("preserved_guard", "decisive_check", "evidence"):
            field_value = row.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                errors.append(f"{prefix}:{field}")
        if row.get("result") != "passed":
            errors.append(f"{prefix}:result")
    if covered_paths != changed_files:
        errors.append("invariant-changed-path-coverage-mismatch")
    if covered_domains != touched_domains:
        errors.append("invariant-risk-domain-coverage-mismatch")
    return tuple(errors)


def _correction_errors(
    record: Mapping[str, object], *, require_all_resolved: bool
) -> tuple[str, ...]:
    errors: list[str] = []
    remediated = set(_strict_strings(record, "remediated_finding_ids", errors, allow_empty=False))
    resolved = set(_strict_strings(record, "resolved_finding_ids", errors))
    disputed = set(_strict_strings(record, "disputed_or_unresolved_p01", errors))
    if resolved & disputed:
        errors.append("resolved-and-disputed-finding-overlap")
    if resolved | disputed != remediated:
        errors.append("remediation-disposition-coverage-mismatch")
    if require_all_resolved and (resolved != remediated or disputed):
        errors.append("material-redesign-parent-findings-not-resolved")

    scope = _mapping(record.get("remediation_scope"))
    if scope is None:
        errors.append("missing-remediation-scope")
    else:
        scoped_findings = set(_strict_row_strings(scope, "finding_ids", "remediation-scope", errors))
        if scoped_findings != remediated:
            errors.append("remediation-finding-scope-mismatch")
        _strict_row_strings(scope, "adjacent_invariant_ids", "remediation-scope", errors)
        _strict_row_strings(scope, "changed_paths", "remediation-scope", errors)

    verification_values = _sequence(record.get("lead_verification"))
    verified: dict[str, str] = {}
    if not verification_values:
        errors.append("missing-lead-verification")
    else:
        for index, value in enumerate(verification_values):
            prefix = f"invalid-lead-verification:{index}"
            row = _mapping(value)
            if row is None:
                errors.append(prefix)
                continue
            finding_id = row.get("finding_id")
            result = row.get("result")
            evidence = row.get("evidence")
            if not isinstance(finding_id, str) or finding_id not in remediated:
                errors.append(f"{prefix}:finding-id")
                continue
            if finding_id in verified:
                errors.append(f"{prefix}:duplicate")
            if result not in {"resolved", "disputed"}:
                errors.append(f"{prefix}:result")
            if not isinstance(evidence, str) or not evidence.strip():
                errors.append(f"{prefix}:evidence")
            if result in {"resolved", "disputed"}:
                verified[finding_id] = cast(str, result)
    if set(verified) != remediated:
        errors.append("incomplete-lead-verification")
    if {finding for finding, result in verified.items() if result == "resolved"} != resolved:
        errors.append("resolved-verification-mismatch")
    if {finding for finding, result in verified.items() if result == "disputed"} != disputed:
        errors.append("disputed-verification-mismatch")
    return tuple(errors)


def readiness_errors(record: Mapping[str, object]) -> tuple[str, ...]:
    """Return every deterministic pre-dispatch readiness failure."""

    errors: list[str] = []
    sequence_id = record.get("review_sequence_id")
    review_id = record.get("review_id")
    mode = record.get("review_mode")
    if not isinstance(sequence_id, str) or not sequence_id.strip():
        errors.append("missing-review-sequence-id")
    if not isinstance(review_id, str) or not review_id.strip():
        errors.append("missing-review-id")
    if mode not in REVIEW_MODES:
        errors.append("invalid-review-mode")

    unknown_fields = sorted(set(record.keys()) - RECORD_FIELDS)
    errors.extend(f"unknown-record-field:{field}" for field in unknown_fields)
    errors.extend(_binding_errors(record))
    errors.extend(_history_errors(record, mode))

    touched_domains = set(_strict_strings(record, "touched_risk_domains", errors, allow_empty=False))
    unknown_domains = touched_domains - RISK_DOMAINS
    if unknown_domains:
        errors.extend(f"unknown-risk-domain:{domain}" for domain in sorted(unknown_domains))
    changed_files = set(_strict_strings(record, "changed_files", errors, allow_empty=False))
    errors.extend(_matrix_errors(record, changed_files, touched_domains))

    deterministic_failures = _strict_strings(record, "known_deterministic_failures", errors)
    if deterministic_failures:
        errors.append("known-deterministic-failures")
    new_risks = _strict_strings(record, "new_risk_classes", errors)
    if new_risks:
        errors.append("new-risk-class")
    omissions = _strict_strings(record, "cross_subsystem_omissions", errors)
    if len(omissions) >= 2:
        errors.append("multiple-cross-subsystem-omissions")
    incomplete = _strict_strings(record, "incomplete_invariant_ids", errors)
    if incomplete:
        errors.append("incomplete-invariant-proof")

    if mode == "initial":
        for field in ("remediated_finding_ids", "resolved_finding_ids", "disputed_or_unresolved_p01"):
            if _strict_strings(record, field, errors):
                errors.append(f"initial-review-has-{field}")
        if record.get("remediation_scope") not in (None, {}):
            errors.append("initial-review-has-remediation-scope")
        if _sequence(record.get("lead_verification")) not in ((), []):
            errors.append("initial-review-has-lead-verification")
    elif mode == "remediation":
        errors.extend(_correction_errors(record, require_all_resolved=False))
    elif mode == "material-redesign":
        material = set(_strict_strings(record, "material_change_categories", errors, allow_empty=False))
        unknown = material - MATERIAL_CHANGE_CATEGORIES
        if unknown:
            errors.extend(f"unknown-material-change-category:{item}" for item in sorted(unknown))
        errors.extend(_correction_errors(record, require_all_resolved=True))

    return tuple(dict.fromkeys(errors))


def select_review_action(record: Mapping[str, object]) -> ReviewDecision:
    """Select the only permitted next action from one machine-bound record."""

    sequence_id = record.get("review_sequence_id")
    mode = record.get("review_mode")
    normalized_sequence_id = sequence_id.strip() if isinstance(sequence_id, str) else ""
    normalized_mode = mode if isinstance(mode, str) else ""
    errors = readiness_errors(record)
    if errors:
        return ReviewDecision(
            status="not-council-ready",
            review_sequence_id=normalized_sequence_id,
            review_mode=normalized_mode,
            action="none",
            reason_codes=errors,
            next_step="implementation-audit-repair",
        )
    if normalized_mode in {"initial", "material-redesign"}:
        return ReviewDecision(
            status="ready",
            review_sequence_id=normalized_sequence_id,
            review_mode=normalized_mode,
            action="full-council",
            reason_codes=(),
            next_step="freeze-and-dispatch-independent-critics",
        )

    disputed = _sequence(record.get("disputed_or_unresolved_p01")) or ()
    if disputed:
        if record.get("targeted_refutation_used") is True:
            return ReviewDecision(
                status="not-council-ready",
                review_sequence_id=normalized_sequence_id,
                review_mode=normalized_mode,
                action="none",
                reason_codes=("targeted-refutation-limit-reached",),
                next_step="human-disposition",
            )
        return ReviewDecision(
            status="ready",
            review_sequence_id=normalized_sequence_id,
            review_mode=normalized_mode,
            action="targeted-refuter",
            reason_codes=(),
            next_step="freeze-single-claim-refutation-packet",
        )
    return ReviewDecision(
        status="closed",
        review_sequence_id=normalized_sequence_id,
        review_mode=normalized_mode,
        action="none",
        reason_codes=(),
        next_step="record-ledger-dispositions-and-close",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    args = parser.parse_args(argv)
    try:
        payload_value: object = json.loads(args.record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"review record is unreadable: {exc}")
    payload = _mapping(payload_value)
    if payload is None:
        parser.error("review record must be a JSON object")
    decision = select_review_action(payload)
    print(json.dumps(asdict(decision), sort_keys=True))
    return 0 if decision.status in {"ready", "closed"} else 10


if __name__ == "__main__":
    raise SystemExit(main())
