#!/usr/bin/env python3
"""Focused regression tests for critical-review convergence controls."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

import epoch
import review_checks
import make_receipt
import qualification
from qualification import (
    FABLE_NON_SECURITY_ARCHITECTURE_V1,
    LIVE_PANEL_ID,
    QualificationError,
    READ_ONLY_REPOSITORY_TOOLS,
    SCHEMA_VERSION,
    bind_packet,
    bind_record,
    conditional_critics,
    fable_skip_reason_codes,
    live_reviewers,
    load_qualification,
    select_full_council,
    validate_qualification,
)
from review_sequence import (
    EXIT_CEREMONY_REQUIRED,
    PROOF_CLASSES,
    SESSION_LOCAL_ROOT,
    proof_subject_digest,
    readiness_errors,
    select_review_action,
    select_triage_action,
    verify_subject_files,
)

QUALIFICATION = Path.home() / ".omp/agent/skills/critical-review/qualification.yml"
SKILL = Path(__file__).resolve().parent.parent / "SKILL.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _proof_classes() -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for proof_class in PROOF_CLASSES:
        if proof_class in {"fresh-process-smoke", "repository-policy"}:
            rows[proof_class] = {
                "status": "passed",
                "evidence_or_justification": f"proof:{proof_class}",
                "receipt_id": "focused-proof",
            }
        else:
            rows[proof_class] = {
                "status": "not-applicable",
                "evidence_or_justification": f"No {proof_class} boundary is touched.",
            }
    return rows


def _history_row(tmp_path: Path, review_id: str, mode: str, action: str) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    record_path = tmp_path / f"{review_id}.json"
    _write_json(record_path, {"review_id": review_id, "action": action})
    return {
        "review_id": review_id,
        "review_mode": mode,
        "action": action,
        "record_path": str(record_path),
        "record_sha256": _sha256(record_path),
    }


def _history(tmp_path: Path) -> list[dict[str, str]]:
    return [_history_row(tmp_path, "CR-initial", "initial", "full-council")]


def _ready_record(tmp_path: Path, mode: str = "initial") -> dict[str, object]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    artifact = tmp_path / "artifact.diff"
    artifact.write_text("frozen review artifact\n", encoding="utf-8")
    changed_a = tmp_path / "controller.py"
    changed_b = tmp_path / "policy.md"
    changed_a.write_text("controller\n", encoding="utf-8")
    changed_b.write_text("policy\n", encoding="utf-8")
    changed_files = [str(changed_a), str(changed_b)]
    changed_file_digests = {path: _sha256(Path(path)) for path in changed_files}
    subject_record = {
        "artifact_digest": _sha256(artifact),
        "changed_file_digests": changed_file_digests,
    }
    receipt = tmp_path / "focused-proof.json"
    _write_json(
        receipt,
        {
            "schemaVersion": 1,
            "result": "passed",
            "exit_code": 0,
            "tier": "full",
            "subject_digest": proof_subject_digest(subject_record),
        },
    )

    record: dict[str, object] = {
        "review_sequence_id": "CRS-20260729-example",
        "review_id": "CR-current",
        "review_mode": mode,
        "parent_review_id": None,
        "sequence_history": [],
        "general_review_pass_count": 0,
        "targeted_refutation_used": False,
        "artifact_path": str(artifact),
        "artifact_digest": _sha256(artifact),
        "changed_files": changed_files,
        "changed_file_digests": changed_file_digests,
        "proof_receipts": {"focused-proof": {"path": str(receipt), "sha256": _sha256(receipt)}},
        "touched_risk_domains": ["architecture", "documentation-policy"],
        "invariant_proof_matrix": [
            {
                "invariant_id": "INV-001",
                "changed_paths": changed_files,
                "risk_domains": ["architecture", "documentation-policy"],
                "preserved_guard": "The dispatch gate remains fail closed.",
                "decisive_check": "focused proof receipt",
                "result": "passed",
                "evidence": "focused-proof.json",
            }
        ],
        "proof_classes": _proof_classes(),
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
    if mode in {"remediation", "material-redesign"}:
        record.update(
            {
                "parent_review_id": "CR-initial",
                "sequence_history": _history(tmp_path),
                "general_review_pass_count": 1,
                "remediated_finding_ids": ["P1-001"],
                "resolved_finding_ids": ["P1-001"],
                "remediation_scope": {
                    "finding_ids": ["P1-001"],
                    "adjacent_invariant_ids": ["INV-001"],
                    "changed_paths": changed_files,
                },
                "lead_verification": [
                    {
                        "finding_id": "P1-001",
                        "result": "resolved",
                        "evidence": "focused proof passed",
                    }
                ],
            }
        )
    if mode == "material-redesign":
        record["material_change_categories"] = ["architecture"]
    return record


SUBJECT_FIELDS = (
    "artifact_path",
    "artifact_digest",
    "changed_files",
    "changed_file_digests",
    "proof_receipts",
    "proof_classes",
    "invariant_proof_matrix",
    "touched_risk_domains",
)


def _draft_record(tmp_path: Path, mode: str = "remediation") -> dict[str, object]:
    """A pre-freeze triage draft: the ready record minus every subject field."""
    record = _ready_record(tmp_path, mode)
    for field in SUBJECT_FIELDS:
        record.pop(field)
    return record


def _design_record(tmp_path: Path) -> dict[str, object]:
    """A ready design-mode record: the frozen subject is the design document."""
    record = _ready_record(tmp_path, "initial")
    record["review_mode"] = "design"
    design = tmp_path / "design.md"
    design.write_text("# design\n", encoding="utf-8")
    record["artifact_path"] = str(design)
    record["artifact_digest"] = _sha256(design)
    record["changed_files"] = [str(design)]
    record["changed_file_digests"] = {str(design): _sha256(design)}
    record["proof_receipts"] = {}
    record["proof_classes"] = {
        proof_class: {
            "status": "not-applicable",
            "evidence_or_justification": "Design-only epoch; no implementation exists.",
        }
        for proof_class in PROOF_CLASSES
    }
    record["invariant_proof_matrix"] = [
        {
            "invariant_id": "INV-001",
            "changed_paths": [str(design)],
            "risk_domains": ["architecture", "documentation-policy"],
            "preserved_guard": "The dispatch gate remains fail closed.",
            "decisive_check": "design document inspection",
            "result": "passed",
            "evidence": "design.md review",
        }
    ]
    return record


def test_design_mode_reviews_a_document_without_receipts(tmp_path: Path) -> None:
    record = _design_record(tmp_path)
    decision = select_review_action(record)
    assert (decision.status, decision.action) == ("ready", "full-council")
    triage = select_triage_action(record)
    assert (triage.status, triage.projected_action) == ("ceremony-required", "full-council")


def test_design_pass_does_not_consume_the_implementation_council(tmp_path: Path) -> None:
    record = _ready_record(tmp_path, "initial")
    record["sequence_history"] = [_history_row(tmp_path, "CR-design", "design", "full-council")]
    record["parent_review_id"] = "CR-design"
    decision = select_review_action(record)
    assert (decision.status, decision.action) == ("ready", "full-council")


def test_design_reviews_are_single_first_and_never_remediation_bases(tmp_path: Path) -> None:
    second = _design_record(tmp_path / "second")
    second["sequence_history"] = [
        _history_row(tmp_path / "second", "CR-design", "design", "full-council")
    ]
    second["parent_review_id"] = "CR-design"
    assert "design-review-has-history" in readiness_errors(second)

    late = _ready_record(tmp_path / "late", "remediation")
    late["sequence_history"] = [
        _history_row(tmp_path / "late", "CR-initial", "initial", "full-council"),
        _history_row(tmp_path / "late", "CR-design", "design", "full-council"),
    ]
    late["parent_review_id"] = "CR-design"
    assert "design-review-not-first" in readiness_errors(late)

    unearned = _ready_record(tmp_path / "unearned", "remediation")
    unearned["sequence_history"] = [
        _history_row(tmp_path / "unearned", "CR-design", "design", "full-council")
    ]
    unearned["parent_review_id"] = "CR-design"
    unearned["general_review_pass_count"] = 0
    assert "remediation-requires-prior-general-pass" in readiness_errors(unearned)


def test_design_records_still_fail_closed_without_receipts_for_passed_classes(
    tmp_path: Path,
) -> None:
    record = _design_record(tmp_path)
    record["proof_classes"]["repository-policy"] = {
        "status": "passed",
        "evidence_or_justification": "docs policy lint",
        "receipt_id": "absent-proof",
    }
    assert "missing-proof-receipt:repository-policy" in readiness_errors(record)
    implementation = _ready_record(tmp_path / "impl")
    implementation["proof_receipts"] = {}
    assert "missing-proof-receipts" in readiness_errors(implementation)


def test_review_mode_selection(tmp_path: Path) -> None:
    initial = select_review_action(_ready_record(tmp_path / "initial", "initial"))
    remediation = select_review_action(_ready_record(tmp_path / "remediation", "remediation"))
    redesign = select_review_action(_ready_record(tmp_path / "redesign", "material-redesign"))
    assert (initial.status, initial.action) == ("ready", "full-council")
    assert (remediation.status, remediation.action) == ("closed", "none")
    assert (redesign.status, redesign.action) == ("ready", "full-council")


def test_structured_failure_and_risk_entries_fail_closed(tmp_path: Path) -> None:
    record = _ready_record(tmp_path)
    record["known_deterministic_failures"] = [{"id": "FAIL-1"}]
    record["new_risk_classes"] = [{"id": "AUTH"}]
    decision = select_review_action(record)
    assert decision.status == "not-council-ready"
    assert "invalid-list-entry:known_deterministic_failures" in decision.reason_codes
    assert "invalid-list-entry:new_risk_classes" in decision.reason_codes


def test_frozen_file_and_receipt_digests_are_enforced(tmp_path: Path) -> None:
    record = _ready_record(tmp_path)
    Path(record["changed_files"][0]).write_text("changed after freeze\n", encoding="utf-8")
    decision = select_review_action(record)
    assert any(
        reason.startswith("changed-file-digest-mismatch:") for reason in decision.reason_codes
    )

    record = _ready_record(tmp_path / "receipt")
    receipt_path = Path(record["proof_receipts"]["focused-proof"]["path"])
    receipt_path.write_text('{"result":"passed"}\n', encoding="utf-8")
    decision = select_review_action(record)
    assert "invalid-proof-receipt:focused-proof" in decision.reason_codes


def test_receipt_from_an_earlier_subject_is_rejected(tmp_path: Path) -> None:
    record = _ready_record(tmp_path)
    receipt_path = Path(record["proof_receipts"]["focused-proof"]["path"])
    payload = json.loads(receipt_path.read_text())
    payload["subject_digest"] = "0" * 64
    _write_json(receipt_path, payload)
    record["proof_receipts"]["focused-proof"]["sha256"] = _sha256(receipt_path)
    assert "invalid-proof-receipt:focused-proof" in readiness_errors(record)

    record = _ready_record(tmp_path / "invalid-digest")
    record["artifact_digest"] = 7
    decision = select_review_action(record)
    assert decision.status == "not-council-ready"
    assert "invalid-artifact-digest" in decision.reason_codes


def test_session_local_review_evidence_fails_closed(tmp_path: Path) -> None:
    record = _ready_record(tmp_path)
    record["artifact_path"] = str(SESSION_LOCAL_ROOT / "ephemeral-artifact.diff")
    record["proof_receipts"]["focused-proof"]["path"] = str(
        SESSION_LOCAL_ROOT / "ephemeral-receipt.json"
    )
    reasons = readiness_errors(record)
    assert "ephemeral-review-path:artifact" in reasons
    assert "ephemeral-review-path:receipt:focused-proof" in reasons

    remediation = _ready_record(tmp_path / "remediation", "remediation")
    remediation["sequence_history"][0]["record_path"] = str(
        SESSION_LOCAL_ROOT / "ephemeral-history.json"
    )
    assert "invalid-history-row:0:ephemeral-record-path" in readiness_errors(remediation)


def test_matrix_must_cover_every_changed_path_and_risk_domain(tmp_path: Path) -> None:
    record = _ready_record(tmp_path)
    row = record["invariant_proof_matrix"][0]
    row["changed_paths"] = row["changed_paths"][:1]
    row["risk_domains"] = ["architecture"]
    reasons = readiness_errors(record)
    assert "invariant-changed-path-coverage-mismatch" in reasons
    assert "invariant-risk-domain-coverage-mismatch" in reasons


def test_sequence_history_derives_parent_and_pass_limits(tmp_path: Path) -> None:
    record = _ready_record(tmp_path, "material-redesign")
    record["general_review_pass_count"] = 0
    assert "general-review-pass-count-mismatch" in readiness_errors(record)

    record = _ready_record(tmp_path / "parent", "material-redesign")
    record["parent_review_id"] = "CR-not-latest"
    assert "parent-review-not-latest-history" in readiness_errors(record)

    record = _ready_record(tmp_path / "third", "material-redesign")
    record["sequence_history"] = [
        *_history(tmp_path / "third"),
        _history_row(tmp_path / "third", "CR-redesign", "material-redesign", "full-council"),
    ]
    record["parent_review_id"] = "CR-redesign"
    record["general_review_pass_count"] = 2
    assert "general-review-pass-limit-reached" in readiness_errors(record)

    record = _ready_record(tmp_path / "history-drift", "remediation")
    history_path = Path(record["sequence_history"][0]["record_path"])
    history_path.write_text("changed history\n", encoding="utf-8")
    assert "invalid-history-row:0:record-binding" in readiness_errors(record)


def test_unknown_record_fields_fail_closed(tmp_path: Path) -> None:
    record = _ready_record(tmp_path)
    record["general_review_passes"] = 0
    assert "unknown-record-field:general_review_passes" in readiness_errors(record)


def test_disputed_remediation_honestly_selects_one_refuter(tmp_path: Path) -> None:
    record = _ready_record(tmp_path, "remediation")
    record["resolved_finding_ids"] = []
    record["disputed_or_unresolved_p01"] = ["P1-001"]
    record["lead_verification"] = [
        {
            "finding_id": "P1-001",
            "result": "disputed",
            "evidence": "direct reproducer is inconclusive",
        }
    ]
    decision = select_review_action(record)
    assert (decision.status, decision.action) == ("ready", "targeted-refuter")

    record["sequence_history"] = [
        *_history(tmp_path),
        _history_row(tmp_path, "CR-refuter", "remediation", "targeted-refuter"),
    ]
    record["parent_review_id"] = "CR-refuter"
    record["targeted_refutation_used"] = True
    decision = select_review_action(record)
    assert decision.next_step == "human-disposition"
    assert decision.reason_codes == ("targeted-refutation-limit-reached",)


def test_remediation_scope_is_exact(tmp_path: Path) -> None:
    record = _ready_record(tmp_path, "remediation")
    record["remediation_scope"]["finding_ids"].append("P1-UNRELATED")
    assert "remediation-finding-scope-mismatch" in readiness_errors(record)


def test_material_redesign_requires_resolved_parent_findings(tmp_path: Path) -> None:
    record = _ready_record(tmp_path, "material-redesign")
    record["resolved_finding_ids"] = []
    record["disputed_or_unresolved_p01"] = ["P1-001"]
    record["lead_verification"] = [
        {"finding_id": "P1-001", "result": "disputed", "evidence": "still disputed"}
    ]
    assert "material-redesign-parent-findings-not-resolved" in readiness_errors(record)


def test_systemic_reset_lifecycle_has_no_automatic_redispatch(tmp_path: Path) -> None:
    initial = _ready_record(tmp_path / "initial")
    assert select_review_action(initial).action == "full-council"

    remediation = _ready_record(tmp_path / "remediation", "remediation")
    remediation["cross_subsystem_omissions"] = ["AUTH-OMISSION", "CACHE-OMISSION"]
    reset = select_review_action(remediation)
    assert reset.status == "not-council-ready"
    assert not reset.permits_provider_dispatch
    assert reset.next_step == "implementation-audit-repair"


def test_localized_remediation_lifecycle_closes_without_redispatch(tmp_path: Path) -> None:
    assert select_review_action(_ready_record(tmp_path / "initial")).action == "full-council"
    closed = select_review_action(_ready_record(tmp_path / "remediation", "remediation"))
    assert (closed.status, closed.action) == ("closed", "none")


def test_machine_record_cli_emits_the_selected_action(tmp_path: Path, capsys) -> None:
    from review_sequence import main

    record_path = tmp_path / "review-record.json"
    _write_json(record_path, _ready_record(tmp_path / "record"))
    assert main([str(record_path)]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "full-council"


def test_triage_projects_lead_close_for_a_clean_remediation_draft(tmp_path: Path) -> None:
    triage = select_triage_action(_draft_record(tmp_path / "draft"))
    assert (triage.status, triage.projected_action) == ("lead-close", "none")
    assert triage.next_step == "lightweight-close"
    frozen = select_triage_action(_ready_record(tmp_path / "full", "remediation"))
    assert frozen.status == "lead-close"


def test_triage_requires_ceremony_before_any_dispatching_epoch(tmp_path: Path) -> None:
    initial = select_triage_action(_draft_record(tmp_path / "initial", "initial"))
    assert (initial.status, initial.projected_action) == ("ceremony-required", "full-council")
    assert initial.next_step == "freeze-epoch-and-run-full-gate"

    disputed = _draft_record(tmp_path / "disputed")
    disputed["resolved_finding_ids"] = []
    disputed["disputed_or_unresolved_p01"] = ["P1-001"]
    disputed["lead_verification"] = [
        {"finding_id": "P1-001", "result": "disputed", "evidence": "reproduced counterexample"}
    ]
    decision = select_triage_action(disputed)
    assert (decision.status, decision.projected_action) == (
        "ceremony-required",
        "targeted-refuter",
    )


def test_triage_fails_closed_on_honesty_flags_and_a_spent_refutation(tmp_path: Path) -> None:
    flagged = _draft_record(tmp_path / "flagged")
    flagged["known_deterministic_failures"] = ["pytest -k budget fails"]
    decision = select_triage_action(flagged)
    assert decision.status == "not-triage-ready"
    assert "known-deterministic-failures" in decision.reason_codes
    assert decision.next_step == "implementation-audit-repair"

    spent = _draft_record(tmp_path / "spent")
    spent["sequence_history"] = [
        _history_row(tmp_path / "spent", "CR-initial", "initial", "full-council"),
        _history_row(tmp_path / "spent", "CR-refuted", "remediation", "targeted-refuter"),
    ]
    spent["parent_review_id"] = "CR-refuted"
    spent["targeted_refutation_used"] = True
    spent["resolved_finding_ids"] = []
    spent["disputed_or_unresolved_p01"] = ["P1-001"]
    spent["lead_verification"] = [
        {"finding_id": "P1-001", "result": "disputed", "evidence": "still reproducible"}
    ]
    exhausted = select_triage_action(spent)
    assert exhausted.status == "not-triage-ready"
    assert exhausted.reason_codes == ("targeted-refutation-limit-reached",)
    assert exhausted.next_step == "human-disposition"


def test_triage_cli_never_emits_a_dispatch_action(tmp_path: Path, capsys) -> None:
    from review_sequence import main

    record_path = tmp_path / "draft.json"
    _write_json(record_path, _draft_record(tmp_path / "draft"))
    assert main([str(record_path), "--triage"]) == 0
    close_payload = json.loads(capsys.readouterr().out)
    assert close_payload["projected_action"] == "none"
    assert "action" not in close_payload

    initial_path = tmp_path / "initial-draft.json"
    _write_json(initial_path, _draft_record(tmp_path / "initial", "initial"))
    assert main([str(initial_path), "--triage"]) == EXIT_CEREMONY_REQUIRED
    ceremony_payload = json.loads(capsys.readouterr().out)
    assert ceremony_payload["status"] == "ceremony-required"
    assert "action" not in ceremony_payload


def test_stable_quick_and_full_check_tiers() -> None:
    assert review_checks.QUICK_TESTS == (
        "test_review_sequence.py",
        "test_runner.py",
        "test_consistency.py",
        "test_invariants.py::test_evaluation_agents_are_hidden_unless_the_lrhe_overlay_is_loaded",
        "test_invariants.py::test_evaluation_overlay_keeps_failed_lanes_hidden",
    )
    assert (
        review_checks.command_for("quick")[-len(review_checks.QUICK_TESTS) :]
        == review_checks.QUICK_TESTS
    )
    assert review_checks.command_for("full")[-1] == "full"
    commands = review_checks.full_commands()
    assert commands[0][1:] == ("check", ".", "--exclude", ".venv")
    assert commands[1][-2:] == ("test_consistency.py", "-q")
    assert commands[2][-3:] == ("pytest", "-q", "--durations=10")
    assert "TRANSPORTS" in commands[3][-1]
    with pytest.raises(ValueError, match="unknown review check tier"):
        review_checks.command_for("other")


def test_full_tier_runs_public_ci_under_clean_home(monkeypatch) -> None:
    calls: list[tuple[tuple[str, ...], Path, str]] = []

    class Result:
        returncode = 0

    def run(command, *, cwd, env, check):
        assert check is False
        calls.append((command, cwd, env["HOME"]))
        return Result()

    monkeypatch.setattr(review_checks.subprocess, "run", run)
    assert review_checks.run_full_checks() == 0
    assert calls[0][0] == review_checks.command_for("quick")
    assert [call[0] for call in calls[1:]] == list(review_checks.full_commands())
    assert all(call[1] == review_checks.HERE for call in calls)
    assert len({call[2] for call in calls[1:]}) == 1
    assert calls[0][2] != calls[1][2]
    assert "critical-review-ci-" in calls[1][2]


def test_check_wrapper_writes_only_subject_bound_passing_receipts(
    tmp_path: Path, monkeypatch
) -> None:
    interpreter = tmp_path / "python"
    interpreter.touch()
    monkeypatch.setattr(review_checks, "VENV_PYTHON", interpreter)
    linter = tmp_path / "ruff"
    linter.touch()
    monkeypatch.setattr(review_checks, "VENV_RUFF", linter)

    class Result:
        returncode = 0

    calls: list[object] = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    monkeypatch.setattr(review_checks.subprocess, "run", run)
    monkeypatch.setattr(make_receipt.subprocess, "run", run)
    artifact = tmp_path / "artifact.diff"
    changed = tmp_path / "changed.py"
    artifact.write_text("artifact\n", encoding="utf-8")
    changed.write_text("source\n", encoding="utf-8")
    subject = {
        "artifact_path": str(artifact),
        "artifact_digest": _sha256(artifact),
        "changed_files": [str(changed)],
        "changed_file_digests": {str(changed): _sha256(changed)},
    }
    subject_path = tmp_path / "subject.json"
    _write_json(subject_path, subject)
    receipt = tmp_path / "full-receipt.json"
    assert (
        review_checks.main(
            ["full", "--receipt", str(receipt), "--subject-record", str(subject_path)]
        )
        == 0
    )
    payload = json.loads(receipt.read_text())
    assert payload["result"] == "passed"
    assert payload["exit_code"] == 0
    assert payload["subject_digest"] == proof_subject_digest(subject)
    assert tuple(payload["command"]) == review_checks.command_for("full")
    assert len(calls) == 1

    changed.write_text("stale source\n", encoding="utf-8")
    assert (
        review_checks.main(
            ["full", "--receipt", str(receipt), "--subject-record", str(subject_path)]
        )
        == make_receipt.EXIT_SUBJECT_MISMATCH
    )
    assert len(calls) == 1


def test_generic_receipt_runner_binds_and_rechecks_subject(tmp_path: Path) -> None:
    record = _ready_record(tmp_path / "stable")
    record_path = tmp_path / "stable-record.json"
    _write_json(record_path, record)
    receipt = tmp_path / "generic-proof.json"
    assert (
        make_receipt.main(
            [
                "--subject-record",
                str(record_path),
                "--receipt",
                str(receipt),
                "--",
                sys.executable,
                "-c",
                "print('proof passed')",
            ]
        )
        == 0
    )
    payload = json.loads(receipt.read_text())
    assert payload["subject_digest"] == proof_subject_digest(record)
    assert payload["result"] == "passed"

    drifting = _ready_record(tmp_path / "drifting")
    drifting_path = tmp_path / "drifting-record.json"
    _write_json(drifting_path, drifting)
    changed = Path(drifting["changed_files"][0])
    drift_receipt = tmp_path / "drifting-proof.json"
    assert (
        make_receipt.main(
            [
                "--subject-record",
                str(drifting_path),
                "--receipt",
                str(drift_receipt),
                "--",
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(changed)!r}).write_text('drifted')",
            ]
        )
        == make_receipt.EXIT_SUBJECT_MISMATCH
    )
    assert not drift_receipt.exists()

    malformed_path = tmp_path / "malformed-subject.json"
    _write_json(malformed_path, [])
    with pytest.raises(SystemExit):
        make_receipt.main(
            [
                "--subject-record",
                str(malformed_path),
                "--receipt",
                str(tmp_path / "malformed-receipt.json"),
                "--",
                sys.executable,
                "-c",
                "pass",
            ]
        )


def test_receipt_reuse_verifies_subject_binding_without_rewriting(tmp_path: Path) -> None:
    record = _ready_record(tmp_path / "stable")
    record_path = tmp_path / "stable-record.json"
    _write_json(record_path, record)
    receipt = tmp_path / "reused-proof.json"
    minted = make_receipt.main(
        [
            "--subject-record",
            str(record_path),
            "--receipt",
            str(receipt),
            "--",
            sys.executable,
            "-c",
            "print('proof passed')",
        ]
    )
    assert minted == 0
    frozen_bytes = receipt.read_bytes()

    reuse_args = ["--subject-record", str(record_path), "--receipt", str(receipt), "--reuse"]
    assert make_receipt.main(reuse_args) == 0
    assert receipt.read_bytes() == frozen_bytes

    changed = Path(record["changed_files"][0])
    original = changed.read_text(encoding="utf-8")
    changed.write_text("drifted\n", encoding="utf-8")
    assert make_receipt.main(reuse_args) == make_receipt.EXIT_SUBJECT_MISMATCH
    changed.write_text(original, encoding="utf-8")
    assert make_receipt.main(reuse_args) == 0

    tampered = tmp_path / "tampered-proof.json"
    payload = json.loads(receipt.read_text())
    payload["subject_digest"] = "0" * 64
    _write_json(tampered, payload)
    assert (
        make_receipt.main(
            ["--subject-record", str(record_path), "--receipt", str(tampered), "--reuse"]
        )
        == make_receipt.EXIT_SUBJECT_MISMATCH
    )

    with pytest.raises(SystemExit):
        make_receipt.main([*reuse_args, "--", sys.executable, "-c", "pass"])


FABLE_POLICY = FABLE_NON_SECURITY_ARCHITECTURE_V1


def _live_qualification() -> dict:
    """Return the private live panel definition, or skip with the reason.

    The resolver and the private file activate together, so a file still at an
    older `schemaVersion` is a pending atomic activation rather than a failure of
    the behaviour under test. `test_consistency.py` owns the loud version check,
    so exactly one test names that gap instead of every test here failing on it.
    """
    if not QUALIFICATION.is_file():
        pytest.skip("private qualification authority is not present in this checkout")
    document = yaml.safe_load(QUALIFICATION.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schemaVersion") != SCHEMA_VERSION:
        pytest.skip(
            f"private qualification is schemaVersion "
            f"{(document or {}).get('schemaVersion')!r}; v{SCHEMA_VERSION} activation "
            "is pending"
        )
    return document


def _unconditional_entry(agent: str, model: str, lens: str) -> dict:
    return {
        "dispatchRole": "primary_critic",
        "dispatchEnabled": True,
        "evaluationEnabled": True,
        "lens": lens,
        "agent": agent,
        "model": model,
        "providerCanary": "passed",
        "schemaValid": True,
        "readOnlyBoundary": "passed",
        "evidenceDelivery": "repository",
        "tools": list(READ_ONLY_REPOSITORY_TOOLS),
        "canaryReceipt": f"lrhe-data/{agent}-trace.json",
    }


def _conditional_entry() -> dict:
    return {
        "dispatchRole": "conditional_critic",
        "dispatchEnabled": True,
        "evaluationEnabled": True,
        "lens": "architecture",
        "agent": "review-claude",
        "model": "anthropic/claude-fable-5:max",
        "fallbackAllowed": False,
        "qualification": {
            "common": {
                "schemaValid": True,
                "readOnlyBoundary": "passed",
                "exactServedModelRequired": "anthropic/claude-fable-5",
            },
            "scopes": {
                "non-security-architecture": {
                    "status": "passed",
                    "canaryReceipt": "lrhe-data/fable-max-architecture-cohort.json",
                },
                "security": {
                    "status": "ineligible",
                    "boundaryEvidence": ["lrhe-data/fable-max-refusals.json"],
                },
            },
        },
        "eligibility": {
            "policy": FABLE_POLICY.policy,
            "allowedReviewModes": list(FABLE_POLICY.allowed_review_modes),
            "allRiskDomainsIn": list(FABLE_POLICY.allowed_risk_domains),
            "requiredProofClassStatuses": {"authorization": "not-applicable"},
            "denyPathComponentRegex": FABLE_POLICY.deny_path_pattern,
            "onUnknown": "skip",
        },
    }


def _panel(with_conditional: bool = True) -> dict:
    """One synthetic v6 panel: three unconditional critics and one conditional."""
    reviewers = {
        "claude-opus": _unconditional_entry(
            "review-claude-opus", "anthropic/claude-opus-5:max", "security"
        ),
        "gemini": _unconditional_entry(
            "review-gemini", "google-antigravity/gemini-3.6-flash:high", "whole_repo"
        ),
        "grok": _unconditional_entry("review-grok", "xai-oauth/grok-4.5:xhigh", "adversarial"),
        "glm": {
            **_unconditional_entry("review-glm-floor", "opencode-go/glm-5.2:max", "refuter"),
            "dispatchRole": "targeted_refuter",
        },
    }
    if with_conditional:
        reviewers["claude"] = _conditional_entry()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "canaryLedgers": {
            "live": {
                "path": "lrhe-data/canary-v3.jsonl",
                "mode": "append-only",
                "prefixRows": 4,
                "prefixSha256": "0" * 64,
                "authority": "live-qualification",
            }
        },
        "liveDispatch": {
            "panelId": LIVE_PANEL_ID,
            "leadFamily": "gpt",
            "initialCritics": ["claude-opus", "gemini", "grok"],
            "conditionalCritics": ["claude"] if with_conditional else [],
            "targetedRefuters": ["glm"],
            "evaluationOnly": [],
            "disabled": [],
        },
        "reviewers": reviewers,
    }


def _remint_receipt(record: dict) -> None:
    """Re-bind the proof receipt after the frozen subject legitimately changes."""
    receipt = record.get("proof_receipts", {}).get("focused-proof")
    if receipt is None:
        return
    path = Path(receipt["path"])
    _write_json(
        path,
        {
            "schemaVersion": 1,
            "result": "passed",
            "exit_code": 0,
            "tier": "full",
            "subject_digest": proof_subject_digest(record),
        },
    )
    receipt["sha256"] = _sha256(path)


def _with_domains(record: dict, domains: list[str]) -> dict:
    record["touched_risk_domains"] = domains
    for row in record["invariant_proof_matrix"]:
        row["risk_domains"] = domains
    return record


def _with_changed_file(record: dict, path: Path) -> dict:
    path.write_text("changed\n", encoding="utf-8")
    record["changed_files"] = [*record["changed_files"], str(path)]
    record["changed_file_digests"] = {
        **record["changed_file_digests"],
        str(path): _sha256(path),
    }
    for row in record["invariant_proof_matrix"]:
        row["changed_paths"] = record["changed_files"]
    _remint_receipt(record)
    return record


def _packet_file(tmp_path: Path, record_path: Path, design_or_diff: str = "controller.py") -> Path:
    packet_path = tmp_path / "packet.md"
    context = {
        "review_record_path": str(record_path),
        "review_record_sha256": _sha256(record_path),
        "goal": "keep the dispatch gate fail closed",
        "non_goals": ["no provider or protocol change"],
        "requirements": ["the resolver owns the roster"],
        "invariants": ["INV-001"],
        "trust_boundaries": ["none"],
        "data_or_state_transitions": ["none"],
        "rollback_contract": "revert the commit",
        "compatibility_contract": "no public surface change",
        "design_or_diff": design_or_diff,
        "known_open_questions": [],
        "rejected_alternatives_and_reasons": ["a second panel authority"],
        "provider_data_allowlist": ["anthropic"],
    }
    packet_path.write_text(
        "# packet\n\n```yaml\n" + yaml.safe_dump(context, sort_keys=True) + "```\n",
        encoding="utf-8",
    )
    return packet_path


def _resolve(
    tmp_path: Path,
    record: dict,
    document: dict | None = None,
    design_or_diff: str = "controller.py",
) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    record_path = tmp_path / "review-record.json"
    _write_json(record_path, record)
    packet_path = _packet_file(tmp_path, record_path, design_or_diff)
    bound_record, record_digest, payload = bind_record(record_path)
    bound_packet, packet_digest, packet = bind_packet(packet_path, bound_record, record_digest)
    return select_full_council(
        _panel() if document is None else document,
        payload,
        packet,
        record_path=bound_record,
        record_sha256=record_digest,
        packet_path=bound_packet,
        packet_sha256=packet_digest,
    )


def _families(manifest: dict, key: str = "selected") -> list[str]:
    return [entry["family"] for entry in manifest[key]]


def test_live_panel_roles_are_derived_from_private_authority() -> None:
    root = _live_qualification()
    document = load_qualification(QUALIFICATION)
    reviewers = root["reviewers"]
    live = root["liveDispatch"]
    memberships = [family for group in live.values() if isinstance(group, list) for family in group]
    assert len(memberships) == len(set(memberships)) == len(reviewers)
    for group in ("initialCritics", "conditionalCritics", "targetedRefuters"):
        assert live["leadFamily"] not in live[group]
    assert all(
        reviewers[item.family]["dispatchEnabled"] is True
        for item in live_reviewers(document, "initial")
    )
    assert all(
        reviewers[item.family]["dispatchEnabled"] is True
        for item in live_reviewers(document, "targeted-refuter")
    )
    canaries = [str(entry["lastCanary"]) for entry in reviewers.values() if "lastCanary" in entry]
    assert canaries, "no lane records lastCanary"
    assert str(root["lastUpdated"]) >= max(canaries), (
        "qualification lastUpdated predates its newest lastCanary"
    )


@pytest.mark.parametrize("group", ("initialCritics", "conditionalCritics", "targetedRefuters"))
def test_qualification_rejects_lead_as_live_reviewer(group: str) -> None:
    current = _live_qualification()
    current["liveDispatch"][group].append(current["liveDispatch"]["leadFamily"])
    with pytest.raises(QualificationError, match="lead family"):
        validate_qualification(current)


def test_qualification_schema_version_fails_closed() -> None:
    with pytest.raises(QualificationError, match="schemaVersion"):
        validate_qualification({"schemaVersion": 2, "reviewers": {}})


def test_private_qualification_live_gate_fails_closed() -> None:
    current = _live_qualification()
    family = current["liveDispatch"]["initialCritics"][0]
    current["reviewers"][family]["readOnlyBoundary"] = "unknown"
    with pytest.raises(QualificationError, match="readOnlyBoundary"):
        validate_qualification(current)


def test_qualification_rejects_an_incomplete_evidence_contract() -> None:
    current = _live_qualification()
    current["reviewers"]["grok"].pop("canaryReceipt")
    with pytest.raises(QualificationError, match="incomplete evidence contract"):
        validate_qualification(current)


def test_qualification_rejects_repository_delivery_without_repository_tools() -> None:
    current = _live_qualification()
    current["reviewers"]["grok"]["tools"] = []
    with pytest.raises(QualificationError, match="for repository delivery"):
        validate_qualification(current)


def test_qualification_pins_the_activated_panel_id() -> None:
    """The resolver and the panel definition activate together or not at all."""
    document = _panel()
    document["liveDispatch"]["panelId"] = "critical-review-primary-v2"
    with pytest.raises(QualificationError, match="panelId must be"):
        validate_qualification(document)


def test_safe_architecture_record_selects_every_critic_including_the_conditional_one(
    tmp_path: Path,
) -> None:
    manifest = _resolve(tmp_path, _ready_record(tmp_path / "safe"))
    assert _families(manifest) == ["claude-opus", "gemini", "grok", "claude"]
    assert manifest["skipped"] == []
    conditional = manifest["selected"][-1]
    assert conditional["selectionClass"] == "conditional"
    assert conditional["reasonCodes"] == [FABLE_POLICY.policy]
    assert conditional["model"] == "anthropic/claude-fable-5:max"
    assert conditional["lens"] == "architecture"
    assert all(entry["selectionClass"] == "unconditional" for entry in manifest["selected"][:3])
    assert all(
        entry["reasonCodes"] == ["configured-primary-critic"] for entry in manifest["selected"][:3]
    )


def test_safe_design_record_selects_the_conditional_critic(tmp_path: Path) -> None:
    """`initial` is the full-council resolution, not the record's review mode."""
    manifest = _resolve(tmp_path, _design_record(tmp_path / "design"))
    assert manifest["mode"] == "initial"
    assert _families(manifest) == ["claude-opus", "gemini", "grok", "claude"]


@pytest.mark.parametrize(
    "domain",
    (
        "authorization",
        "money-or-assets",
        "privacy",
        "release-supply-chain",
        "secrets-cryptography",
        "cross-system-boundary",
        "public-protocol",
    ),
)
def test_denied_and_ambiguous_domains_skip_only_the_conditional_critic(
    tmp_path: Path, domain: str
) -> None:
    record = _with_domains(_ready_record(tmp_path / "denied"), [domain])
    _remint_receipt(record)
    manifest = _resolve(tmp_path, record)
    assert _families(manifest) == ["claude-opus", "gemini", "grok"]
    assert manifest["skipped"] == [
        {
            "family": "claude",
            "selectionClass": "conditional",
            "reasonCodes": ["risk-domain-outside-allowlist"],
        }
    ]


def test_one_denied_domain_beside_safe_domains_still_skips_the_conditional_critic(
    tmp_path: Path,
) -> None:
    record = _with_domains(_ready_record(tmp_path / "mixed"), ["architecture", "privacy"])
    manifest = _resolve(tmp_path, record)
    assert _families(manifest) == ["claude-opus", "gemini", "grok"]
    assert _families(manifest, "skipped") == ["claude"]


def test_an_applicable_proof_class_status_skips_the_conditional_critic(tmp_path: Path) -> None:
    record = _ready_record(tmp_path / "proof-classes")
    record["proof_classes"]["authorization"] = {
        "status": "passed",
        "evidence_or_justification": "the proven access path is cited",
        "receipt_id": "focused-proof",
    }
    manifest = _resolve(tmp_path, record)
    assert _families(manifest) == ["claude-opus", "gemini", "grok"]
    assert manifest["skipped"][0]["reasonCodes"] == ["authorization-proof-applicable"]


# These two tests carry no deny-list token in their names on purpose: the filter
# runs over absolute paths, and pytest derives `tmp_path` from the test name, so a
# test called `..._security_...` would match its own directory and pass without
# ever exercising the path it means to test. Each keeps a control resolution for
# the same reason.
def test_a_denied_changed_path_skips_the_conditional_critic(tmp_path: Path) -> None:
    """The path filter holds even when the declared domain list looks safe."""
    record = _ready_record(tmp_path / "subject")
    control = _resolve(tmp_path / "control", record)
    assert _families(control, "skipped") == []

    _with_changed_file(record, tmp_path / "subject" / "oauth_gateway.py")
    manifest = _resolve(tmp_path / "denied", record)
    assert _families(manifest) == ["claude-opus", "gemini", "grok"]
    assert manifest["skipped"][0]["reasonCodes"] == ["security-sensitive-path"]


def test_a_denied_packet_source_path_skips_the_conditional_critic(tmp_path: Path) -> None:
    record = _ready_record(tmp_path / "subject")
    control = _resolve(tmp_path / "control", record)
    assert _families(control, "skipped") == []

    manifest = _resolve(tmp_path / "denied", record, design_or_diff="src/auth/session.py")
    assert _families(manifest) == ["claude-opus", "gemini", "grok"]
    assert manifest["skipped"][0]["reasonCodes"] == ["security-sensitive-path"]


def test_every_independent_skip_reason_is_recorded(tmp_path: Path) -> None:
    record = _with_domains(_ready_record(tmp_path / "everything"), ["privacy"])
    record["proof_classes"]["authorization"] = {
        "status": "passed",
        "evidence_or_justification": "the authorization path is proven",
        "receipt_id": "focused-proof",
    }
    _with_changed_file(record, tmp_path / "everything" / "tls_handshake.py")
    manifest = _resolve(tmp_path, record)
    assert manifest["skipped"][0]["reasonCodes"] == [
        "authorization-proof-applicable",
        "risk-domain-outside-allowlist",
        "security-sensitive-path",
    ]


@pytest.mark.parametrize("domains", ([], ["not-a-real-domain"]))
def test_empty_or_unknown_domains_fail_the_whole_record_not_just_the_critic(
    tmp_path: Path, domains: list[str]
) -> None:
    """The strict whole-record failure is preserved, not softened into a skip."""
    record = _with_domains(_ready_record(tmp_path / "domains"), domains)
    assert select_review_action(record).action == "none"
    with pytest.raises(QualificationError, match="does not authorize a full council"):
        _resolve(tmp_path, record)


def test_the_eligibility_policy_alone_still_names_an_empty_domain_list(tmp_path: Path) -> None:
    record = _with_domains(_ready_record(tmp_path / "policy-only"), [])
    assert fable_skip_reason_codes(FABLE_POLICY, record) == ("risk-domains-empty",)


def test_targeted_refuter_resolution_never_contains_a_conditional_critic() -> None:
    document = _panel()
    assert [item.reviewer.family for item in conditional_critics(document)] == ["claude"]
    assert [item.family for item in live_reviewers(document, "targeted-refuter")] == ["glm"]
    assert [item.family for item in live_reviewers(document, "initial")] == [
        "claude-opus",
        "gemini",
        "grok",
    ]


def test_the_unconditional_council_survives_an_absent_conditional_critic(
    tmp_path: Path,
) -> None:
    manifest = _resolve(tmp_path, _ready_record(tmp_path / "nofable"), _panel(False))
    assert _families(manifest) == ["claude-opus", "gemini", "grok"]
    assert manifest["skipped"] == []


def test_opus_is_selected_for_every_full_council(tmp_path: Path) -> None:
    for name, record in (
        ("safe", _ready_record(tmp_path / "opus-safe")),
        ("denied", _with_domains(_ready_record(tmp_path / "opus-denied"), ["privacy"])),
    ):
        manifest = _resolve(tmp_path / name, record)
        opus = next(entry for entry in manifest["selected"] if entry["family"] == "claude-opus")
        assert opus["model"] == "anthropic/claude-opus-5:max"
        assert opus["selectionClass"] == "unconditional"


def test_manifest_binds_the_record_packet_and_subject_digests(tmp_path: Path) -> None:
    record = _ready_record(tmp_path / "binding")
    manifest = _resolve(tmp_path, record)
    record_path = (tmp_path / "review-record.json").resolve()
    packet_path = (tmp_path / "packet.md").resolve()
    assert manifest["schemaVersion"] == 1
    assert manifest["panelId"] == LIVE_PANEL_ID
    assert manifest["reviewRecordPath"] == str(record_path)
    assert manifest["reviewRecordSha256"] == _sha256(record_path)
    assert manifest["subjectDigest"] == proof_subject_digest(record)
    assert manifest["packetPath"] == str(packet_path)
    assert manifest["packetSha256"] == _sha256(packet_path)


def test_manifest_binding_changes_with_the_record(tmp_path: Path) -> None:
    first = _resolve(tmp_path / "one", _ready_record(tmp_path / "one" / "subject"))
    second_record = _ready_record(tmp_path / "two" / "subject")
    second_record["review_id"] = "CR-second"
    second = _resolve(tmp_path / "two", second_record)
    assert first["reviewRecordSha256"] != second["reviewRecordSha256"]
    assert first["subjectDigest"] != second["subjectDigest"]


def test_a_packet_bound_to_another_record_fails_closed(tmp_path: Path) -> None:
    record_path = tmp_path / "review-record.json"
    _write_json(record_path, _ready_record(tmp_path / "subject"))
    other = tmp_path / "other-record.json"
    _write_json(other, _ready_record(tmp_path / "other"))
    packet = _packet_file(tmp_path, other)
    bound_record, digest, _ = bind_record(record_path)
    with pytest.raises(QualificationError, match="does not bind the resolved record"):
        bind_packet(packet, bound_record, digest)


def test_a_stale_packet_digest_fails_closed(tmp_path: Path) -> None:
    record = _ready_record(tmp_path / "subject")
    record_path = tmp_path / "review-record.json"
    _write_json(record_path, record)
    packet = _packet_file(tmp_path, record_path)
    record["review_id"] = "CR-edited-after-the-packet"
    _write_json(record_path, record)
    bound_record, digest, _ = bind_record(record_path)
    with pytest.raises(QualificationError, match="does not match the record digest"):
        bind_packet(packet, bound_record, digest)


def test_resolver_cli_persists_exactly_the_manifest_it_prints(tmp_path: Path, capsys) -> None:
    qualification_path = tmp_path / "qualification.yml"
    qualification_path.write_text(yaml.safe_dump(_panel()), encoding="utf-8")
    record_path = tmp_path / "review-record.json"
    _write_json(record_path, _ready_record(tmp_path / "subject"))
    packet = _packet_file(tmp_path, record_path)
    out = tmp_path / "selection" / "panel-selection.json"
    argv = [
        "initial",
        "--record",
        str(record_path),
        "--packet",
        str(packet),
        "--out",
        str(out),
        "--qualification",
        str(qualification_path),
    ]
    assert qualification.main(argv) == 0
    printed = capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == printed
    manifest = json.loads(printed)
    assert _families(manifest) == ["claude-opus", "gemini", "grok", "claude"]

    # A second resolution never silently replaces the roster that was dispatched.
    with pytest.raises(SystemExit):
        qualification.main(argv)
    assert out.read_text(encoding="utf-8") == printed


def test_resolver_cli_refuses_a_session_local_manifest(tmp_path: Path) -> None:
    qualification_path = tmp_path / "qualification.yml"
    qualification_path.write_text(yaml.safe_dump(_panel()), encoding="utf-8")
    record_path = tmp_path / "review-record.json"
    _write_json(record_path, _ready_record(tmp_path / "subject"))
    packet = _packet_file(tmp_path, record_path)
    out = SESSION_LOCAL_ROOT / "ephemeral" / "panel-selection.json"
    with pytest.raises(SystemExit):
        qualification.main(
            [
                "initial",
                "--record",
                str(record_path),
                "--packet",
                str(packet),
                "--out",
                str(out),
                "--qualification",
                str(qualification_path),
            ]
        )
    assert not out.exists()


def test_a_conditional_critic_activates_its_scope_and_role_together() -> None:
    missing_eligibility = _panel()
    missing_eligibility["reviewers"]["claude"].pop("eligibility")
    with pytest.raises(QualificationError, match="eligibility"):
        validate_qualification(missing_eligibility)

    unqualified_scope = _panel()
    scopes = unqualified_scope["reviewers"]["claude"]["qualification"]["scopes"]
    scopes["non-security-architecture"] = {
        "status": "failed",
        "boundaryEvidence": ["lrhe-data/fable-max-refusals.json"],
    }
    with pytest.raises(QualificationError, match="must be 'passed'"):
        validate_qualification(unqualified_scope)

    stray = _panel()
    stray["reviewers"]["gemini"]["eligibility"] = _conditional_entry()["eligibility"]
    with pytest.raises(QualificationError, match="without the 'conditional_critic' role"):
        validate_qualification(stray)


def test_a_refused_scope_keeps_its_boundary_evidence() -> None:
    document = _panel()
    document["reviewers"]["claude"]["qualification"]["scopes"]["security"] = {
        "status": "ineligible",
        "boundaryEvidence": [],
    }
    with pytest.raises(QualificationError, match="must retain the evidence"):
        validate_qualification(document)


def test_conditional_selector_and_eligibility_config_are_exact() -> None:
    downgraded = _panel()
    downgraded["reviewers"]["claude"]["model"] = "anthropic/claude-fable-5:high"
    with pytest.raises(QualificationError, match="thinking level 'max'"):
        validate_qualification(downgraded)

    mismatched = _panel()
    mismatched["reviewers"]["claude"]["qualification"]["common"]["exactServedModelRequired"] = (
        "anthropic/claude-opus-5"
    )
    with pytest.raises(QualificationError, match="exactServedModelRequired"):
        validate_qualification(mismatched)

    widened = _panel()
    widened["reviewers"]["claude"]["eligibility"]["allRiskDomainsIn"] = [
        *FABLE_POLICY.allowed_risk_domains,
        "authorization",
    ]
    with pytest.raises(QualificationError, match="allRiskDomainsIn must be"):
        validate_qualification(widened)

    relaxed = _panel()
    relaxed["reviewers"]["claude"]["eligibility"]["denyPathComponentRegex"] = r"(?i)nothing"
    with pytest.raises(QualificationError, match="denyPathComponentRegex"):
        validate_qualification(relaxed)

    permissive = _panel()
    permissive["reviewers"]["claude"]["eligibility"]["requiredProofClassStatuses"] = {
        "authorization": "passed"
    }
    with pytest.raises(QualificationError, match="requiredProofClassStatuses"):
        validate_qualification(permissive)


def test_a_conditional_critic_can_never_declare_a_fallback() -> None:
    document = _panel()
    document["reviewers"]["claude"]["fallbackAllowed"] = True
    with pytest.raises(QualificationError, match="never a fallback"):
        validate_qualification(document)


def test_the_scope_receipt_path_stays_inside_the_skill_data_root() -> None:
    for bad in ("/etc/passwd.json", "../escape.json", "lrhe-data/cohort.yaml"):
        document = _panel()
        document["reviewers"]["claude"]["qualification"]["scopes"]["non-security-architecture"][
            "canaryReceipt"
        ] = bad
        with pytest.raises(QualificationError, match="canaryReceipt"):
            validate_qualification(document)


def test_skill_preserves_critical_review_safety_controls() -> None:
    policy = SKILL.read_text(encoding="utf-8")
    required = (
        "Recompute the same artifact and file digests",
        "until every member has settled",
        "Every returned item receives a ledger row and final disposition",
        "A confirmed P0 or P1 blocks closure",
        "P2/P3 items receive explicit dispositions but do not trigger open-ended debate",
        "There is no majority verdict",
    )
    assert all(control in policy for control in required)


def test_epoch_tool_scaffolds_freezes_and_rechecks(tmp_path: Path) -> None:
    draft = tmp_path / "draft.json"
    assert (
        epoch.main(
            [
                "scaffold",
                "--mode",
                "initial",
                "--sequence-id",
                "CRS-tool",
                "--review-id",
                "CR-tool",
                "--out",
                str(draft),
            ]
        )
        == 0
    )
    scaffolded = json.loads(draft.read_text())
    triage = select_triage_action(scaffolded)
    assert (triage.status, triage.projected_action) == ("ceremony-required", "full-council")

    changed = tmp_path / "controller.py"
    changed.write_text("controller\n", encoding="utf-8")
    gone = tmp_path / "legacy.py"
    artifact = tmp_path / "artifact.diff"
    artifact.write_text("diff\n", encoding="utf-8")
    assert (
        epoch.main(
            [
                "freeze",
                "--record",
                str(draft),
                "--artifact",
                str(artifact),
                "--changed",
                str(changed),
                "--deleted",
                str(gone),
            ]
        )
        == 0
    )
    frozen = json.loads(draft.read_text())
    assert frozen["changed_file_digests"][str(gone.resolve())] == "DELETED"
    assert verify_subject_files(frozen) == ()
    assert epoch.main(["recheck", "--record", str(draft)]) == 0

    changed.write_text("drifted\n", encoding="utf-8")
    assert epoch.main(["recheck", "--record", str(draft)]) == epoch.EXIT_PRECONDITION


def test_epoch_freeze_fails_closed_on_bad_paths(tmp_path: Path) -> None:
    draft = tmp_path / "draft.json"
    epoch.main(
        [
            "scaffold",
            "--mode",
            "remediation",
            "--sequence-id",
            "CRS-guard",
            "--review-id",
            "CR-guard",
            "--out",
            str(draft),
        ]
    )
    artifact = tmp_path / "artifact.diff"
    artifact.write_text("diff\n", encoding="utf-8")
    present = tmp_path / "present.py"
    present.write_text("x\n", encoding="utf-8")
    base = ["freeze", "--record", str(draft), "--artifact", str(artifact)]
    assert epoch.main([*base, "--deleted", str(present)]) == epoch.EXIT_PRECONDITION
    assert epoch.main([*base, "--changed", str(tmp_path / "absent.py")]) == epoch.EXIT_PRECONDITION
    assert epoch.main(base) == epoch.EXIT_PRECONDITION
    assert epoch.main([*base, "--changed", str(present), str(present)]) == epoch.EXIT_PRECONDITION


def test_epoch_bind_emits_a_verifiable_history_row(tmp_path: Path, capsys) -> None:
    prior = tmp_path / "CR-prior.json"
    _write_json(prior, {"review_id": "CR-prior", "review_mode": "initial"})
    assert epoch.main(["bind", "--record", str(prior), "--action", "full-council"]) == 0
    row = json.loads(capsys.readouterr().out)
    assert row["record_sha256"] == _sha256(prior)

    record = _ready_record(tmp_path, "remediation")
    record["sequence_history"] = [row]
    record["parent_review_id"] = "CR-prior"
    reasons = readiness_errors(record)
    assert "invalid-history-row:0:record-binding" not in reasons
    assert "parent-review-not-latest-history" not in reasons


def test_epoch_ledger_normalizes_reviewer_yields(tmp_path: Path, capsys) -> None:
    """One row per returned item, mechanical columns filled, judgment left empty.

    Severity orders the skeleton (P0 first), U-rows prefill `unresolved` as the
    Result, and impact text survives inside the Finding cell -- normalization
    must not discard reviewer signal the lead still has to verify.
    """
    claude = tmp_path / "claude.json"
    grok = tmp_path / "grok.json"
    _write_json(
        claude,
        {
            "summary": "two findings",
            "evidence": [
                "R1|P1|conf=0.85|claim=rollback deletes restore.sh|evidence=deploy/rollback.sh:41-58"
                "|impact=failed rollback strands state|verify=rehearse in scratch clone",
            ],
            "unresolved": [
                "U1|P2|conf=0.6|question=does retry re-enter after SIGTERM?"
                "|missing=signal path for worker.py:120-160|verify=send SIGTERM during retry",
            ],
        },
    )
    _write_json(
        grok,
        {
            "summary": "one finding",
            "evidence": [
                "R1|P0|conf=1.00|claim=token comparison is not constant time|evidence=auth.py:1-2"
                "|impact=timing oracle|verify=statistical timing test",
            ],
            "unresolved": [],
        },
    )
    out = tmp_path / "ledger.md"
    code = epoch.main(
        [
            "ledger",
            "--member",
            f"claude={claude}",
            "--member",
            f"grok={grok}",
            "--review-id",
            "CR-test",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    emitted = json.loads(capsys.readouterr().out)
    assert (emitted["rows"], emitted["unresolved_rows"]) == (3, 1)
    assert emitted["members"] == ["claude", "grok"]

    text = out.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "# Finding ledger — CR-test"
    body = [line for line in lines if line.startswith("| ") and " --- " not in line]
    assert body[0].startswith("| Finding | Sources |")
    assert "grok-R1" in body[1], "P0 must sort before P1"
    assert "claude-R1" in body[2]
    assert "impact: failed rollback strands state" in body[2]
    assert "claude-U1" in body[3]
    assert "| missing: signal path for worker.py:120-160 |" in body[3]
    assert "| unresolved |" in body[3]


def test_epoch_ledger_refuses_bad_rows_existing_output_and_bad_members(tmp_path: Path) -> None:
    """A malformed row refuses the whole scaffold instead of dropping feedback."""
    malformed = tmp_path / "claude.json"
    _write_json(
        malformed,
        {
            "summary": "s",
            "evidence": ["R1|P1|conf=0.855|claim=x|evidence=y|impact=z|verify=v"],
            "unresolved": [],
        },
    )
    out = tmp_path / "ledger.md"
    code = epoch.main(["ledger", "--member", f"claude={malformed}", "--out", str(out)])
    assert code == epoch.EXIT_PRECONDITION
    assert not out.exists(), "a refused scaffold must write nothing"

    valid = tmp_path / "grok.json"
    _write_json(valid, {"summary": "s", "evidence": [], "unresolved": []})
    out.write_text("lead judgment already here\n", encoding="utf-8")
    code = epoch.main(["ledger", "--member", f"grok={valid}", "--out", str(out)])
    assert code == epoch.EXIT_PRECONDITION
    assert out.read_text(encoding="utf-8") == "lead judgment already here\n"

    fresh = tmp_path / "fresh.md"
    assert (
        epoch.main(
            [
                "ledger",
                "--member",
                f"grok={valid}",
                "--member",
                f"grok={valid}",
                "--out",
                str(fresh),
            ]
        )
        == epoch.EXIT_PRECONDITION
    )
    incomplete = tmp_path / "incomplete.json"
    _write_json(incomplete, {"summary": "s", "evidence": []})
    assert (
        epoch.main(["ledger", "--member", f"kimi={incomplete}", "--out", str(fresh)])
        == epoch.EXIT_PRECONDITION
    )
    assert not fresh.exists()
