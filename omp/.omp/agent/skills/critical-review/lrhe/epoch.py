#!/usr/bin/env python3
"""Deterministic epoch mechanics for critical-review records.

`scaffold` emits a pre-freeze triage draft, `freeze` binds the frozen subject
digests into a record, `recheck` re-verifies the live tree against the frozen
subject, `bind` prints one sequence-history row for a prior epoch record, and
`ledger` normalizes reviewer yields into the finding-ledger skeleton.
The tool computes and verifies; it never selects a dispatch action, never
calls a provider, and never chooses what enters the artifact. The lead still
materializes `artifact.diff` and owns every inclusion and exclusion decision.
"""

from __future__ import annotations

import argparse
import json
import re
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

LEDGER_COLUMNS = (
    "Finding",
    "Sources",
    "Evidence",
    "Severity",
    "Confidence",
    "Verification",
    "Result",
    "Disposition",
    "Change",
    "Rationale",
)
_CONF = r"0(?:\.[0-9]{1,2})?|1(?:\.0{1,2})?"
EVIDENCE_ROW = re.compile(
    rf"^R(?P<row>[1-9][0-9]*)\|P(?P<severity>[0-3])\|conf=(?P<conf>{_CONF})"
    r"\|claim=(?P<claim>.+?)\|evidence=(?P<evidence>.+?)"
    r"\|impact=(?P<impact>.+?)\|verify=(?P<verify>.+)$"
)
UNRESOLVED_ROW = re.compile(
    rf"^U(?P<row>[1-9][0-9]*)\|P(?P<severity>[0-3])\|conf=(?P<conf>{_CONF})"
    r"\|question=(?P<question>.+?)\|missing=(?P<missing>.+?)\|verify=(?P<verify>.+)$"
)

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


def cmd_ledger(args: argparse.Namespace) -> int:
    """Normalize reviewer yields into one finding-ledger skeleton.

    Every evidence and unresolved item becomes one table row with the
    mechanical columns filled -- Finding, Sources, Evidence, Severity,
    Confidence, Verification, and `unresolved` prefilled as the Result of
    U-rows. Result, Disposition, Change, and Rationale stay empty: they are
    the lead's verification judgment, which this tool never performs. Rows
    that fail the pinned pipe grammar refuse the whole scaffold rather than
    dropping feedback silently.
    """
    try:
        manifest: object = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"unreadable-manifest:{exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    selected = manifest.get("selected") if isinstance(manifest, dict) else None
    if not isinstance(selected, list):
        print("invalid-manifest:selected must be a list", file=sys.stderr)
        return EXIT_PRECONDITION
    selected_ids: list[str] = []
    for index, row in enumerate(selected):
        reviewer_id = row.get("reviewer_id") if isinstance(row, dict) else None
        if not isinstance(reviewer_id, str) or not reviewer_id:
            print(f"invalid-manifest:selected[{index}].reviewer_id", file=sys.stderr)
            return EXIT_PRECONDITION
        selected_ids.append(reviewer_id)
    if len(selected_ids) != len(set(selected_ids)):
        print("invalid-manifest:duplicate reviewer_id", file=sys.stderr)
        return EXIT_PRECONDITION
    selected_set = set(selected_ids)

    errors: list[str] = []
    members: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for spec in args.member:
        reviewer_id, separator, path = spec.partition("=")
        reviewer_id = reviewer_id.strip()
        if not separator or not reviewer_id or not path:
            errors.append(f"invalid-member-spec:{spec}")
            continue
        if reviewer_id in seen:
            errors.append(f"duplicate-member:{reviewer_id}")
        seen.add(reviewer_id)
        if reviewer_id not in selected_set:
            errors.append(f"member-not-selected:{reviewer_id}")
        members.append((reviewer_id, Path(path)))

    rows: list[dict[str, str]] = []
    for reviewer_id, path in members:
        try:
            value: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"unreadable-member:{reviewer_id}: {exc}")
            continue
        if not isinstance(value, dict) or not {"summary", "evidence", "unresolved"} <= set(value):
            errors.append(
                f"invalid-member-result:{reviewer_id}: summary, evidence, and unresolved "
                "are required"
            )
            continue
        for kind, pattern in (("evidence", EVIDENCE_ROW), ("unresolved", UNRESOLVED_ROW)):
            items = value.get(kind)
            if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
                errors.append(f"invalid-member-result:{reviewer_id}: {kind} must be a string list")
                continue
            for index, item in enumerate(items):
                match = pattern.fullmatch(item)
                if match is None:
                    errors.append(f"unparseable-row:{reviewer_id}:{kind}[{index}]:{item[:60]}")
                    continue
                rows.append({"reviewer_id": reviewer_id, "kind": kind, **match.groupdict()})

    if args.out.exists():
        errors.append(f"ledger-already-exists:{args.out}")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    rows.sort(key=lambda row: (row["severity"], row["reviewer_id"], row["kind"], int(row["row"])))
    lines: list[str] = []
    if args.review_id:
        lines.extend((f"# Finding ledger — {args.review_id}", ""))
    lines.append("| " + " | ".join(LEDGER_COLUMNS) + " |")
    lines.append("|" + " --- |" * len(LEDGER_COLUMNS))
    for row in rows:
        if row["kind"] == "evidence":
            finding = (
                f"{row['reviewer_id']}-R{row['row']} — {row['claim']} "
                f"— impact: {row['impact']}"
            )
            evidence, result = row["evidence"], ""
        else:
            finding = f"{row['reviewer_id']}-U{row['row']} — {row['question']}"
            evidence, result = f"missing: {row['missing']}", "unresolved"
        cells = (
            finding,
            row["reviewer_id"],
            evidence,
            f"P{row['severity']}",
            row["conf"],
            row["verify"],
            result,
            "",
            "",
            "",
        )
        lines.append("| " + " | ".join(cells) + " |")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _emit(
        {
            "op": "ledger",
            "out": str(args.out),
            "members": sorted(seen),
            "manifest": str(args.manifest),
            "rows": len(rows),
            "unresolved_rows": sum(1 for row in rows if row["kind"] == "unresolved"),
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

    ledger = commands.add_parser(
        "ledger", help="normalize reviewer yields into a finding-ledger skeleton"
    )
    ledger.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="the immutable panel-selection manifest whose reviewer_ids may be attributed",
    )
    ledger.add_argument(
        "--member",
        action="append",
        required=True,
        metavar="REVIEWER_ID=RESULT.json",
        help=(
            "the reviewer_id exactly as the selection manifest names it, and that "
            "member's yielded summary/evidence/unresolved JSON"
        ),
    )
    ledger.add_argument("--review-id", default="")
    ledger.add_argument("--out", type=Path, required=True)
    ledger.set_defaults(handler=cmd_ledger)

    args = parser.parse_args(argv)
    return cast(int, args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
