#!/usr/bin/env python3
"""Run one exact command and emit a subject-bound passing proof receipt."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence, cast

from review_sequence import proof_subject_digest, verify_subject_files

EXIT_SUBJECT_MISMATCH = 10


def _load_subject(path: Path) -> Mapping[str, object]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"subject record is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("subject record must be a JSON object")
    return cast(Mapping[str, object], value)


def _write_receipt(
    path: Path,
    *,
    subject_digest: str,
    command: Sequence[str],
    cwd: Path,
) -> None:
    payload = {
        "schemaVersion": 1,
        "subject_digest": subject_digest,
        "command": list(command),
        "cwd": str(cwd.resolve()),
        "exit_code": 0,
        "result": "passed",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject-record", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = tuple(args.command[1:] if args.command[:1] == ["--"] else args.command)
    if not command:
        parser.error("an exact command is required after --")

    if args.receipt.exists():
        args.receipt.unlink()
    try:
        subject = _load_subject(args.subject_record)
        subject_digest = proof_subject_digest(subject)
    except ValueError as exc:
        parser.error(str(exc))
    errors = verify_subject_files(subject)
    if errors:
        print(f"subject does not match live files: {', '.join(errors)}", file=sys.stderr)
        return EXIT_SUBJECT_MISMATCH

    returncode = subprocess.run(command, cwd=args.cwd, check=False).returncode
    if returncode != 0:
        return returncode
    errors = verify_subject_files(subject)
    if errors:
        print(f"subject changed while proof ran: {', '.join(errors)}", file=sys.stderr)
        return EXIT_SUBJECT_MISMATCH
    _write_receipt(
        args.receipt,
        subject_digest=subject_digest,
        command=command,
        cwd=args.cwd,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
