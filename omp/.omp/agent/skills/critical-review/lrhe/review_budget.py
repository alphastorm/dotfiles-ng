#!/usr/bin/env python3
"""Record exact-head end-to-end runs and enforce the two-round review budget.

Receipts contain one head and an append-only runs list. A failed run remains a
failure even if a later run passes; use a new lane directory for a new candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TAIL_BYTES = 2048


def _receipt(path, head):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("runs"), list):
        raise TypeError(f"e2e receipt {path} must contain a head and runs list")
    if value.get("head") != head:
        raise ValueError(f"e2e receipt {path} binds a different head")
    for run in value["runs"]:
        if not isinstance(run, dict) or run.get("head") != head:
            raise ValueError(f"e2e receipt {path} has a run bound to a different head")
    return value


def _output(stream):
    stream.seek(0)
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(65536), b""):
        digest.update(block)
    stream.seek(max(0, stream.tell() - TAIL_BYTES))
    return {"sha256": digest.hexdigest(), "tail": stream.read().decode("utf-8", errors="replace")}


def e2e(lane_dir, round_number, head, name, cwd, argv):
    path = lane_dir / f"e2e-r{round_number}.json"
    receipt = _receipt(path, head) if path.exists() else {"head": head, "runs": []}
    lane_dir.mkdir(parents=True, exist_ok=True)
    cwd = cwd.resolve()
    actual = subprocess.run(["git", "-C", str(cwd), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    if actual.returncode == 0:
        # Inside a checkout the receipt must describe the reviewed bytes, not a declared head.
        if actual.stdout.strip() != head:
            raise ValueError(f"e2e cwd {cwd} is at {actual.stdout.strip()[:12]}, not head {head[:12]}")
        dirty = subprocess.run(["git", "-C", str(cwd), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=False)
        if dirty.returncode != 0 or dirty.stdout.strip():
            raise ValueError(f"e2e cwd {cwd} has uncommitted tracked changes")
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        result = subprocess.run(argv, cwd=cwd, stdout=stdout, stderr=stderr, check=False)
        finished = datetime.now(timezone.utc).isoformat()
        receipt["runs"].append({
            "head": head, "name": name, "argv": list(argv), "cwd": str(cwd),
            "rc": result.returncode, "started_at": started, "finished_at": finished,
            "stdout": _output(stdout), "stderr": _output(stderr),
        })
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return result.returncode


def check(lane_dir, round_number, head, spec_path):
    if round_number > 2:
        raise ValueError("two-round cap: never dispatch a third review round")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise TypeError("review spec must be a JSON object")
    if round_number == 2 and not spec.get("affected_invariants"):
        raise ValueError("fix-only round 2 requires non-empty affected_invariants in the spec")
    path = lane_dir / f"e2e-r{round_number}.json"
    if not path.is_file():
        raise ValueError(f"missing e2e receipt: {path}")
    receipt = _receipt(path, head)
    if not receipt["runs"]:
        raise ValueError(f"e2e receipt {path} has no runs")
    if any(type(run.get("rc")) is not int or run["rc"] != 0 for run in receipt["runs"]):
        raise ValueError(f"e2e receipt {path} has a nonzero or missing rc")


def _round(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("round must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("e2e", help="run and append an exact-head end-to-end receipt")
    gate = commands.add_parser("check", help="refuse an over-budget or unproven review")
    for command in (record, gate):
        command.add_argument("--lane-dir", type=Path, required=True)
        command.add_argument("--round", type=_round, required=True)
        command.add_argument("--head", required=True)
    record.add_argument("--name", required=True)
    record.add_argument("--cwd", type=Path, required=True)
    record.add_argument("argv", nargs=argparse.REMAINDER)
    gate.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            check(args.lane_dir, args.round, args.head, args.spec)
            return 0
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not command:
            parser.error("e2e requires an exact command after --")
        rc = e2e(args.lane_dir, args.round, args.head, args.name, args.cwd, command)
        # subprocess uses negative signal numbers; shells use 128 + signal.
        return 128 - rc if rc < 0 else rc
    except (OSError, ValueError, TypeError) as exc:
        print(f"review-budget: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
