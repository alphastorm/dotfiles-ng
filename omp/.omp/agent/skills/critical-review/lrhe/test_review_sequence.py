#!/usr/bin/env python3
"""Focused regression tests for critical-review convergence controls."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

import review_checks
import make_receipt
from qualification import (
    QualificationError,
    live_reviewers,
    load_qualification,
    validate_qualification,
)
from review_sequence import (
    PROOF_CLASSES,
    SESSION_LOCAL_ROOT,
    proof_subject_digest,
    readiness_errors,
    select_review_action,
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


def test_stable_quick_and_full_check_tiers() -> None:
    assert review_checks.QUICK_TESTS == (
        "test_review_sequence.py",
        "test_runner.py",
        "test_consistency.py",
    )
    assert review_checks.command_for("quick")[-3:] == review_checks.QUICK_TESTS
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


def test_live_panel_roles_are_derived_from_private_authority() -> None:
    if not QUALIFICATION.is_file():
        pytest.skip("private qualification authority is not present in this checkout")
    document = load_qualification(QUALIFICATION)
    root = yaml.safe_load(QUALIFICATION.read_text(encoding="utf-8"))
    reviewers = root["reviewers"]
    live = root["liveDispatch"]
    memberships = [family for group in live.values() if isinstance(group, list) for family in group]
    assert len(memberships) == len(set(memberships)) == len(reviewers)
    assert live["leadFamily"] not in live["initialCritics"]
    assert live["leadFamily"] not in live["targetedRefuters"]
    assert all(
        reviewers[item.family]["dispatchEnabled"] is True
        for item in live_reviewers(document, "initial")
    )
    assert all(
        reviewers[item.family]["dispatchEnabled"] is True
        for item in live_reviewers(document, "targeted-refuter")
    )
    grok = next(item for item in live_reviewers(document, "initial") if item.family == "grok")
    assert grok.model == "xai-oauth/grok-4.5:xhigh"
    assert grok.evidence_delivery == "inline"


@pytest.mark.parametrize("group", ("initialCritics", "targetedRefuters"))
def test_qualification_rejects_lead_as_live_reviewer(group: str) -> None:
    if not QUALIFICATION.is_file():
        pytest.skip("private qualification authority is not present in this checkout")
    current = yaml.safe_load(QUALIFICATION.read_text(encoding="utf-8"))
    current["liveDispatch"][group].append(current["liveDispatch"]["leadFamily"])
    with pytest.raises(QualificationError, match="lead family"):
        validate_qualification(current)


def test_qualification_schema_version_fails_closed() -> None:
    with pytest.raises(QualificationError, match="schemaVersion"):
        validate_qualification({"schemaVersion": 2, "reviewers": {}})


def test_private_qualification_live_gate_fails_closed() -> None:
    if not QUALIFICATION.is_file():
        pytest.skip("private qualification authority is not present in this checkout")
    current = yaml.safe_load(QUALIFICATION.read_text(encoding="utf-8"))
    family = current["liveDispatch"]["initialCritics"][0]
    current["reviewers"][family]["readOnlyBoundary"] = "unknown"
    with pytest.raises(QualificationError, match="readOnlyBoundary"):
        validate_qualification(current)


def test_qualification_rejects_an_incomplete_isolated_contract() -> None:
    if not QUALIFICATION.is_file():
        pytest.skip("private qualification authority is not present in this checkout")
    current = yaml.safe_load(QUALIFICATION.read_text(encoding="utf-8"))
    current["reviewers"]["grok"].pop("canaryReceipt")
    with pytest.raises(QualificationError, match="incomplete isolated contract"):
        validate_qualification(current)


def test_skill_preserves_critical_review_safety_controls() -> None:
    policy = SKILL.read_text(encoding="utf-8")
    required = (
        "Recompute the same artifact and file digests",
        "in one `task`\nbatch",
        "Every returned item receives a ledger row and final disposition",
        "A confirmed P0 or P1 blocks closure",
        "P2/P3 items receive explicit dispositions but do not trigger open-ended debate",
        "There is no majority verdict",
    )
    assert all(control in policy for control in required)
