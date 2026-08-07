#!/usr/bin/env python3
"""Deterministic epoch mechanics for critical-review records.

`scaffold` emits a pre-freeze triage draft, `freeze` binds the frozen subject
digests into a record, `recheck` re-verifies the live tree against the frozen
subject, and `bind` prints one sequence-history row for a prior epoch record.
The tool computes and verifies; it never selects a dispatch action, never
calls a provider, and never chooses what enters the artifact. The lead still
materializes `artifact.diff` and owns every inclusion and exclusion decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping, Sequence, cast

from review_sequence import (
    ACTIONS,
    REVIEW_MODES,
    _is_session_local,
    _sha256,
    proof_subject_digest,
    verify_subject_files,
)

EXIT_PRECONDITION = 10

DRAFT_TEMPLATE: dict[str, object] = {
    "parent_review_id": None,
    "sequence_history": [],
    "general_review_pass_count": 0,
    "targeted_refutation_used": False,
    "known_deterministic_failures": [],
    "new_risk_classes": [],
    "cross_subsystem_omissions": [],
    "incomplete_invariant_ids": [],
    "remediated_finding_ids": [],
    "resolved_finding_ids": [],
    "disputed_or_unresolved_p01": [],
    "remediation_scope": None,
    "lead_verification": [],
    "material_change_categories": [],
}

SUBJECT_DEFAULTS: dict[str, object] = {
    "proof_receipts": {},
    "proof_classes": {},
    "touched_risk_domains": [],
    "invariant_proof_matrix": [],
}


def _load_record(path: Path) -> dict[str, object]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"record is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError("record must be a JSON object")
    return cast(dict[str, object], value)


def _write_record(path: Path, record: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _emit(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True))


def cmd_scaffold(args: argparse.Namespace) -> int:
    record: dict[str, object] = {
        "review_sequence_id": args.sequence_id,
        "review_id": args.review_id,
        "review_mode": args.mode,
        **{key: json.loads(json.dumps(value)) for key, value in DRAFT_TEMPLATE.items()},
    }
    _write_record(args.out, record)
    _emit({"op": "scaffold", "record": str(args.out), "review_mode": args.mode})
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    try:
        record = _load_record(args.record)
    except (TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PRECONDITION

    changed = [Path(path).resolve() for path in args.changed]
    deleted = [Path(path).resolve() for path in args.deleted]
    artifact = Path(args.artifact).resolve()
    errors: list[str] = []
    seen: set[str] = set()
    for path in (*changed, *deleted):
        key = str(path)
        if key in seen:
            errors.append(f"duplicate-changed-path:{key}")
        seen.add(key)
    for path in (artifact, *changed, *deleted):
        if _is_session_local(path):
            errors.append(f"ephemeral-review-path:{path}")
    if not artifact.is_file():
        errors.append(f"artifact-not-readable:{artifact}")
    for path in changed:
        if not path.is_file():
            errors.append(f"changed-file-not-readable:{path}")
    for path in deleted:
        if path.exists():
            errors.append(f"deleted-file-still-exists:{path}")
    if not changed and not deleted:
        errors.append("no-changed-files")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    digests: dict[str, str] = {str(path): _sha256(path) for path in changed}
    digests.update({str(path): "DELETED" for path in deleted})
    record["artifact_path"] = str(artifact)
    record["artifact_digest"] = _sha256(artifact)
    record["changed_files"] = sorted(digests)
    record["changed_file_digests"] = dict(sorted(digests.items()))
    for key, default in SUBJECT_DEFAULTS.items():
        record.setdefault(key, json.loads(json.dumps(default)))

    out = args.out or args.record
    _write_record(out, record)
    _emit(
        {
            "op": "freeze",
            "record": str(out),
            "subject_digest": proof_subject_digest(record),
            "changed_files": len(digests),
        }
    )
    return 0


def cmd_recheck(args: argparse.Namespace) -> int:
    try:
        record = _load_record(args.record)
    except (TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PRECONDITION
    errors = verify_subject_files(record)
    if errors:
        _emit({"op": "recheck", "record": str(args.record), "drift": list(errors)})
        return EXIT_PRECONDITION
    _emit(
        {
            "op": "recheck",
            "record": str(args.record),
            "drift": [],
            "subject_digest": proof_subject_digest(record),
        }
    )
    return 0


def cmd_bind(args: argparse.Namespace) -> int:
    try:
        record = _load_record(args.record)
    except (TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PRECONDITION
    review_id = record.get("review_id")
    review_mode = record.get("review_mode")
    if not isinstance(review_id, str) or not review_id.strip():
        print("prior record has no review_id", file=sys.stderr)
        return EXIT_PRECONDITION
    if review_mode not in REVIEW_MODES:
        print("prior record has no valid review_mode", file=sys.stderr)
        return EXIT_PRECONDITION
    _emit(
        {
            "review_id": review_id,
            "review_mode": review_mode,
            "action": args.action,
            "record_path": str(args.record.resolve()),
            "record_sha256": _sha256(args.record),
        }
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    scaffold = commands.add_parser(
        "scaffold", help="emit a pre-freeze triage draft record"
    )
    scaffold.add_argument("--mode", choices=sorted(REVIEW_MODES), required=True)
    scaffold.add_argument("--sequence-id", required=True)
    scaffold.add_argument("--review-id", required=True)
    scaffold.add_argument("--out", type=Path, required=True)
    scaffold.set_defaults(handler=cmd_scaffold)

    freeze = commands.add_parser(
        "freeze", help="bind artifact and changed-file digests into a record"
    )
    freeze.add_argument("--record", type=Path, required=True)
    freeze.add_argument("--artifact", type=Path, required=True)
    freeze.add_argument("--changed", nargs="*", default=[], metavar="FILE")
    freeze.add_argument("--deleted", nargs="*", default=[], metavar="FILE")
    freeze.add_argument("--out", type=Path)
    freeze.set_defaults(handler=cmd_freeze)

    recheck = commands.add_parser(
        "recheck", help="verify the live tree against the frozen subject"
    )
    recheck.add_argument("--record", type=Path, required=True)
    recheck.set_defaults(handler=cmd_recheck)

    bind = commands.add_parser(
        "bind", help="print one sequence-history row for a prior epoch record"
    )
    bind.add_argument("--record", type=Path, required=True)
    bind.add_argument("--action", choices=sorted(ACTIONS), required=True)
    bind.set_defaults(handler=cmd_bind)

    args = parser.parse_args(argv)
    return cast(int, args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
