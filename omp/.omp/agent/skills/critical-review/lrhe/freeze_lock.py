#!/usr/bin/env python3
"""freeze_lock.py -- capture immutable starting-state metadata for LRHE result sets.

The public harness can be rebuilt from source control; the private corpus is
accumulated evidence. Both must be pinned before any result is accepted as
derived from a declared input state. This command stores that pin in
`runs/LOCK.json` beside that evidence so a future verifier can prove it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


EXIT_OK = 0
EXIT_MISMATCH = 10
EXIT_ERROR = 1


# `Path.home()` targets the checked-out top-level repositories directly. The
# harness package directory is a stow symlink, so deriving repo roots from
# `.../lrhe/..` resolves the dotfiles parent and can silently cross mount.
DEFAULT_PUBLIC_REPO = Path.home() / ".dotfiles"
DEFAULT_PRIVATE_REPO = Path.home() / ".dotfiles-private"
DEFAULT_DATA_DIR = Path.home() / ".omp/agent/skills/critical-review/lrhe-data"
DEFAULT_CORPUS = DEFAULT_DATA_DIR / "corpus.jsonl"
DEFAULT_ASSIGNMENTS_MANIFEST = DEFAULT_DATA_DIR / "assignments.manifest.json"
DEFAULT_TERMS_DIR = DEFAULT_DATA_DIR / "terms"
DEFAULT_LOCK_PATH = DEFAULT_DATA_DIR / "runs/LOCK.json"
MANIFEST_NAME = "MANIFEST.sha256"


ManifestEntry = dict[str, dict[str, str]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_command_version(name: str) -> str | None:
    """Return exactly what `<name> --version` reports, or None when absent.

    A missing CLI for this line item is not a soft failure: provenance must stay
    explicit about tool availability, including a null marker.
    """
    try:
        proc = subprocess.run(
            [name, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return None

    if proc.returncode != 0:
        return None

    output = (proc.stdout or proc.stderr or "").strip()
    if not output:
        return None
    return output.splitlines()[0].strip()


def _git_state(path: Path) -> dict[str, Any]:
    """Capture commit and dirtiness for a repo that names the harness state."""
    commit_cmd = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if commit_cmd.returncode != 0:
        raise RuntimeError(f"{path}: git rev-parse HEAD failed: {commit_cmd.stderr.strip() or commit_cmd.stdout.strip()}")

    status_cmd = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status_cmd.returncode != 0:
        raise RuntimeError(f"{path}: git status failed: {status_cmd.stderr.strip() or status_cmd.stdout.strip()}")

    return {
        "path": str(path),
        "commit": commit_cmd.stdout.strip(),
        "dirty": bool(status_cmd.stdout.strip()),
    }


def _parse_manifest_terms(terms_dir: Path) -> tuple[str, ManifestEntry]:
    manifest = terms_dir / MANIFEST_NAME
    if not manifest.is_file():
        raise RuntimeError(f"missing terms manifest: {manifest}")

    raw = manifest.read_text(encoding="utf-8").splitlines()
    if not raw:
        raise RuntimeError(f"empty terms manifest: {manifest}")

    pattern = re.compile(r"^([0-9a-fA-F]{64})\s+(.+)$")
    grouped: ManifestEntry = {}
    for line_no, line in enumerate(raw, start=1):
        if not line.strip():
            continue
        match = pattern.fullmatch(line)
        if not match:
            raise RuntimeError(f"invalid manifest line {line_no} in {manifest}: {line!r}")
        digest, raw_name = match.groups()
        rel = PurePosixPath(raw_name)
        if rel.is_absolute() or not rel.parts:
            raise RuntimeError(f"invalid manifest path {raw_name!r} at {manifest}:{line_no}")
        snapshot = rel.parts[0]
        grouped.setdefault(snapshot, {})[raw_name] = digest.lower()

    return _sha256_file(manifest), grouped


def _collect_inputs(args: argparse.Namespace) -> dict[str, Any]:
    terms_manifest_sha, term_items = _parse_manifest_terms(args.terms_dir)
    corpus = args.corpus
    answer_key = args.answer_key or corpus

    return {
        "public_repo": _git_state(args.public_repo),
        "private_repo": _git_state(args.private_repo),
        "corpus": {
            "path": str(corpus),
            "sha256": _sha256_file(corpus),
        },
        "answer_key": {
            "path": str(answer_key),
            "sha256": _sha256_file(answer_key),
        },
        "assignments_manifest": {
            "path": str(args.assignments_manifest),
            "sha256": _sha256_file(args.assignments_manifest),
        },
        "versions": {
            "omp": _run_command_version("omp"),
            "claude_code": _run_command_version("claude"),
        },
        "terms": {
            "manifest_path": str(args.terms_dir / MANIFEST_NAME),
            "manifest_sha256": terms_manifest_sha,
            "snapshot_digests": term_items,
        },
    }


def _lock_id(inputs: dict[str, Any]) -> str:
    payload = json.dumps(
        inputs,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _build_record(args: argparse.Namespace) -> dict[str, Any]:
    inputs = _collect_inputs(args)
    return {
        "schema": "lrhe-lock-v1",
        "lock_id": _lock_id(inputs),
        "created_utc": _utc_now(),
        "lock_inputs": inputs,
    }


def _write_lock(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


_MISSING = object()

def _collect_diffs(expected: Any, actual: Any, prefix: str = "") -> list[tuple[str, Any, Any]]:
    diffs: list[tuple[str, Any, Any]] = []

    if isinstance(expected, dict) and isinstance(actual, dict):
        keys = set(expected) | set(actual)
        for key in sorted(keys):
            e = expected.get(key, _MISSING)
            a = actual.get(key, _MISSING)
            next_prefix = f"{prefix}.{key}" if prefix else key
            diffs.extend(_collect_diffs(e, a, next_prefix))
        return diffs

    if isinstance(expected, list) and isinstance(actual, list):
        max_len = max(len(expected), len(actual))
        for index in range(max_len):
            e = expected[index] if index < len(expected) else _MISSING
            a = actual[index] if index < len(actual) else _MISSING
            next_prefix = f"{prefix}[{index}]"
            diffs.extend(_collect_diffs(e, a, next_prefix))
        return diffs

    if expected is _MISSING and actual is _MISSING:
        return diffs
    if expected is _MISSING:
        diffs.append((f"{prefix}", "<missing>", actual))
        return diffs
    if actual is _MISSING:
        diffs.append((f"{prefix}", expected, "<missing>"))
        return diffs
    if expected != actual:
        diffs.append((prefix, expected, actual))
    return diffs

def cmd_freeze(args: argparse.Namespace) -> int:
    args.corpus = args.corpus or (args.data_dir / "corpus.jsonl")
    args.answer_key = args.answer_key or args.corpus
    args.assignments_manifest = args.assignments_manifest or (args.data_dir / "assignments.manifest.json")
    args.terms_dir = args.terms_dir or (args.data_dir / "terms")
    args.lock = args.lock

    try:
        record = _build_record(args)
    except Exception as exc:
        print(f"failed to build lock: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        _write_lock(args.lock, record)
    except OSError as exc:
        print(f"failed to write lock {args.lock}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"wrote lock: {args.lock}")
    print(json.dumps(record, sort_keys=True, indent=2))
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    args.public_repo = args.public_repo
    args.private_repo = args.private_repo
    args.corpus = args.corpus
    args.answer_key = args.answer_key
    args.assignments_manifest = args.assignments_manifest or (args.data_dir / "assignments.manifest.json")
    args.terms_dir = args.terms_dir or (args.data_dir / "terms")
    args.lock = args.lock

    if not args.lock.is_file():
        print(f"lock file missing: {args.lock}", file=sys.stderr)
        return EXIT_ERROR

    try:
        current_inputs = _collect_inputs(args)
    except Exception as exc:
        print(f"failed to recalc inputs: {exc}", file=sys.stderr)
        return EXIT_ERROR

    stored = json.loads(args.lock.read_text(encoding="utf-8"))
    stored_inputs = stored.get("lock_inputs")
    if stored_inputs is None:
        print(f"lock file {args.lock} is invalid: missing lock_inputs", file=sys.stderr)
        return EXIT_ERROR

    diffs = _collect_diffs(stored_inputs, current_inputs, "lock_inputs")
    recomputed_id = _lock_id(current_inputs)
    if stored.get("lock_id") != recomputed_id:
        diffs.append(("lock_id", stored.get("lock_id"), recomputed_id))

    if not diffs:
        print(f"verify passed: {args.lock}")
        print(f"lock_id: {recomputed_id}")
        print(f"lock_utc: {stored.get('created_utc')}")
        return EXIT_OK

    print(f"verify failed for {args.lock}", file=sys.stderr)
    for path, expected, actual in diffs:
        print(f"  drift: {path}", file=sys.stderr)
        print(f"    lock: {json.dumps(expected, sort_keys=True)}", file=sys.stderr)
        print(f"    now : {json.dumps(actual, sort_keys=True)}", file=sys.stderr)
    return EXIT_MISMATCH


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--public-repo",
        type=Path,
        default=DEFAULT_PUBLIC_REPO,
        help=(
            "public harness git checkout whose commit pins the harness code "
            f"(default: {DEFAULT_PUBLIC_REPO})"
        ),
    )
    common.add_argument(
        "--private-repo",
        type=Path,
        default=DEFAULT_PRIVATE_REPO,
        help=(
            "private corpus git checkout whose commit pins the corpus and terms "
            f"(default: {DEFAULT_PRIVATE_REPO})"
        ),
    )
    common.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=(
            "private LRHE data root. This path is absolute because `lrhe/` is a "
            "stow symlink and `..` from inside it resolves to the dotfiles parent "
            "(not the private package root). "
            f"(default: {DEFAULT_DATA_DIR})"
        ),
    )
    common.add_argument(
        "--corpus",
        type=Path,
        help="corpus JSONL path (labels live here by default)",
    )
    common.add_argument(
        "--answer-key",
        type=Path,
        help="answer-key path (defaults to --corpus)",
    )
    common.add_argument(
        "--assignments-manifest",
        type=Path,
        help="path to assignments manifest JSON",
    )
    common.add_argument(
        "--terms-dir",
        type=Path,
        help="directory containing terms/MANIFEST.sha256 (default: data-dir/terms)",
    )

    freeze = sub.add_parser("freeze", help="record a new lock file")
    freeze.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK_PATH,
        help=(
            "where to write the lock (default: "
            f"{DEFAULT_LOCK_PATH})"
        ),
    )
    freeze.set_defaults(fn=cmd_freeze)

    verify = sub.add_parser("verify", help="compare live inputs against a saved lock")
    verify.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK_PATH,
        help=(
            "lock to verify against (default: "
            f"{DEFAULT_LOCK_PATH})"
        ),
    )
    verify.set_defaults(fn=cmd_verify)

    for parser in (freeze, verify):
        for a in common._actions:
            if a.option_strings == ["-h", "--help"]:
                continue
            parser._add_action(a)

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Fill values that are intentionally defaulted from --data-dir after parse.
    data_dir = args.data_dir
    args.public_repo = Path(args.public_repo)
    args.private_repo = Path(args.private_repo)
    args.corpus = Path(args.corpus or (data_dir / "corpus.jsonl"))
    args.answer_key = Path(args.answer_key) if args.answer_key is not None else None
    if args.answer_key is None:
        args.answer_key = args.corpus
    args.assignments_manifest = Path(args.assignments_manifest or (data_dir / "assignments.manifest.json"))
    args.terms_dir = Path(args.terms_dir or (data_dir / "terms"))
    args.lock = Path(args.lock)

    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
