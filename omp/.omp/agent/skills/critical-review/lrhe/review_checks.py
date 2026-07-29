#!/usr/bin/env python3
"""Stable quick and full proof commands for the critical-review skill."""
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

import make_receipt

HERE = Path(__file__).resolve().parent
VENV_PYTHON = HERE / ".venv/bin/python"
VENV_RUFF = HERE / ".venv/bin/ruff"
QUICK_TESTS = (
    "test_review_sequence.py",
    "test_runner.py",
    "test_consistency.py",
)
_TRANSPORT_ASSERTION = (
    "import run_review; "
    "assert 'live' not in run_review.TRANSPORTS; "
    "assert run_review.TRANSPORTS['none'] is run_review.no_egress_transport; "
    "print('transports:', sorted(run_review.TRANSPORTS))"
)


def command_for(tier: str) -> tuple[str, ...]:
    """Return the cwd-independent command wrapped by a proof receipt."""

    if tier == "quick":
        return (str(VENV_PYTHON), "-m", "pytest", "-q", *QUICK_TESTS)
    if tier == "full":
        return (str(VENV_PYTHON), str(Path(__file__).resolve()), "full")
    raise ValueError(f"unknown review check tier: {tier}")


def full_commands() -> tuple[tuple[str, ...], ...]:
    """Return the fail-fast public-CI commands in execution order."""

    return (
        (str(VENV_RUFF), "check", ".", "--exclude", ".venv"),
        (str(VENV_PYTHON), "-m", "pytest", "test_consistency.py", "-q"),
        (str(VENV_PYTHON), "-m", "pytest", "-q", "--durations=10"),
        (str(VENV_PYTHON), "-c", _TRANSPORT_ASSERTION),
    )



def run_full_checks() -> int:
    """Run the complete public Actions contract with an isolated home."""
    quick_environment = os.environ.copy()
    quick_returncode = subprocess.run(
        command_for("quick"),
        cwd=HERE,
        env=quick_environment,
        check=False,
    ).returncode
    if quick_returncode != 0:
        return quick_returncode


    with tempfile.TemporaryDirectory(prefix="critical-review-ci-") as clean_home:
        environment = os.environ.copy()
        environment["HOME"] = clean_home
        for command in full_commands():
            returncode = subprocess.run(
                command,
                cwd=HERE,
                env=environment,
                check=False,
            ).returncode
            if returncode != 0:
                return returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tier", choices=("quick", "full"))
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--subject-record", type=Path)
    args = parser.parse_args(argv)

    if not VENV_PYTHON.is_file():
        parser.error(f"missing review environment: {VENV_PYTHON}")
    if args.tier == "full" and not VENV_RUFF.is_file():
        parser.error(f"missing review linter: {VENV_RUFF}")
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
    if args.tier == "full":
        return run_full_checks()
    return subprocess.run(command, cwd=HERE, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
