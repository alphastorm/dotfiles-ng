#!/usr/bin/env python3
"""Report parked/successor preparation obligations; never authorize dispatch.

This companion does not parse policy prose into authority, reset sequence
history, or add fields to frozen review records. Counts and state are caller
observations to reconcile with retained ledgers, not attested evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Sequence


def _reference(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    contents = path.read_bytes()
    if not contents.strip():
        raise ValueError(f"empty evidence reference: {path}")
    return {"path": str(path), "sha256": hashlib.sha256(contents).hexdigest()}


def guidance(
    *,
    state: str,
    subject_rounds: int,
    inherited_rounds: int,
    repository_review: Path | None = None,
    parent_ledger: Path | None = None,
) -> dict[str, object]:
    if state not in {"parked", "successor"}:
        raise ValueError("state must be parked or successor")
    if any(type(count) is not int or count < 0 for count in (subject_rounds, inherited_rounds)):
        raise ValueError("round counts must be nonnegative integers")
    if state == "successor" and parent_ledger is None:
        raise ValueError("a successor requires the retained parent ledger")
    policy = _reference(repository_review)
    parent = _reference(parent_ledger)
    required = [
        "Retained parent-to-successor/split lineage, subject identities, rounds, seat invocations, "
        "dispositions and ownership; reconcile caller counts against those ledgers.",
        "Every open parent finding with source IDs and anchors, its child owner, and "
        "invariant coverage including shared boundaries and merge order.",
        "Mandatory founder/policy requirements and validated serious defects; lead-selected "
        "invariants with adopt/reject reasons, relevant preparation sources/boundaries/deltas.",
        "Closed same-class sibling sweeps or explicit failed/parked dispositions before freeze.",
        "Subject-bound class-closure and state-faithful evidence; serious in-scope findings "
        "resolved under governing policy, never erased by renaming the subject.",
    ]
    if policy is not None:
        precedence = (
            "Repository REVIEW.md governs explicit local round/subject/successor and admission "
            "rules; portable defaults apply where it is silent. Reading this reference does "
            "not establish that it grants a fresh budget."
        )
        required.extend([
            "Cite the governing repository rule permitting the proposed successor or next round "
            "and its prerequisite evidence; no budget reset without that explicit rule. "
            "Keep the inherited workstream total even when a child budget is permitted.",
            "Run the repository admission consumer for the changed-path floor and exact head; "
            "record the admitting receipt class (a replay may be evidence without admission). "
            "Then use the normal applicable triage/frozen gate and qualified resolver; this "
            "report does not reconcile a selector/policy mismatch or authorize dispatch.",
        ])
    else:
        precedence = "No repository override supplied: portable sequence budgets/history remain binding."
        required.append(
            "Keep the portable sequence and its full bound history; a successor label cannot "
            "reset its budget. Run review_sequence.py triage and the frozen gate when required."
        )
    if state == "parked":
        required.append(
            "Keep parked bytes retired: never redispatch them; retain their owner, unresolved "
            "findings and explicit disposition. A proposed successor needs its own reconciled "
            "scope and an explicit governing rule before admission."
        )
        next_action = (
            "prepare-successor-under-repository-policy"
            if policy else "retain-parked-subject-and-disposition"
        )
    else:
        next_action = (
            "reconcile-parent-findings-before-repository-admission"
            if policy else "reconcile-lineage-without-resetting-sequence"
        )
    return {
        "state": state,
        "subject_rounds": subject_rounds,
        "inherited_rounds": inherited_rounds,
        "workstream_rounds": subject_rounds + inherited_rounds,
        "counts_source": "caller observations; reconcile against retained ledgers",
        "repository_review": policy,
        "parent_ledger": parent,
        "policy_precedence": precedence,
        "next_action": next_action,
        "required_evidence": required,
        "permits_provider_dispatch": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", choices=("parked", "successor"), required=True)
    parser.add_argument("--subject-rounds", type=int, required=True)
    parser.add_argument("--inherited-rounds", type=int, required=True)
    parser.add_argument("--repository-review", type=Path)
    parser.add_argument("--parent-ledger", type=Path)
    args = parser.parse_args(argv)
    try:
        report = guidance(**vars(args))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
