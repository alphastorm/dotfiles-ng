#!/usr/bin/env python3
"""Stable quick and full proof commands for the critical-review skill."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Sequence

import make_receipt

HERE = Path(__file__).resolve().parent
VENV_PYTHON = HERE / ".venv/bin/python"
QUICK_TESTS = (
    "test_review_sequence.py",
    "test_runner.py",
    "test_consistency.py",
)
FULL_TESTS = (*QUICK_TESTS, "test_invariants.py")


def command_for(tier: str) -> tuple[str, ...]:
    """Return the exact cwd-independent pytest command for one tier."""

    if tier == "quick":
        tests = QUICK_TESTS
    elif tier == "full":
        tests = FULL_TESTS
    else:
        raise ValueError(f"unknown review check tier: {tier}")
    return (str(VENV_PYTHON), "-m", "pytest", "-q", *tests)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tier", choices=("quick", "full"))
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--subject-record", type=Path)
    args = parser.parse_args(argv)

    if not VENV_PYTHON.is_file():
        parser.error(f"missing review environment: {VENV_PYTHON}")
    command = command_for(args.tier)
    if args.receipt:
        if args.subject_record is None:
            parser.error("--receipt requires --subject-record")
        return make_receipt.main(
            [
                "--subject-record",
                str(args.subject_record),
                "--receipt",
                str(args.receipt),
                "--cwd",
                str(HERE),
                "--",
                *command,
            ]
        )
    return subprocess.run(command, cwd=HERE, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
