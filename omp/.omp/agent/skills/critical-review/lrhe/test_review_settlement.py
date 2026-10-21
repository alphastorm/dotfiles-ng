"""Consumer-visible split dispatch, cutoff and late-finding transitions."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import review_dispatch as rd

SHA = "a" * 64
START = datetime(2026, 10, 20, tzinfo=timezone.utc)


@pytest.fixture
def review(tmp_path):
    envelope = tmp_path / "envelope.json"
    canonical = {
        "i": "Dispatching resolved reviewers", "context": "bound envelope marker",
        "tasks": [
            {"agent": agent, "task": (
                f"{rd.RECEIPT_MARKER}\nreview_class=focused\nauthority={authority}\n\nblind input"
            )}
            for agent, authority in (
                ("review-claude-opus", "independent_evidence"),
                ("review-gemini-argon", "independent_evidence"),
                ("review-grok", "supplemental_evidence"),
            )
        ],
    }
    return envelope, canonical


def admit(review, number, seats):
    envelope, _ = review
    envelope.with_name(f"envelope.attempt-{number}.started.json").write_text(json.dumps({
        "schema": "critical-review-task-start-v1", "envelopeSha256": SHA,
        "admittedAt": START.isoformat(), "seats": seats,
    }))


def finish(review, number, seats, *, failed=False, finding=None):
    envelope, canonical = review
    record = {
        "schema": "critical-review-task-attempt-v1", "envelopeSha256": SHA,
        "admittedAt": START.isoformat(), "settledAt": (START + timedelta(seconds=100)).isoformat(),
        "seats": [{"seat": seat, "failed": failed, "outcome": "error" if failed else "completed"} for seat in seats],
        "result": {"details": {"results": [
            {
                "index": position, "agent": canonical["tasks"][seat]["agent"],
                "exitCode": 1 if failed else 0,
                "structuredOutput": {"status": "valid", "data": {
                    "summary": "fixture verdict", "evidence": [finding] if finding else [], "unresolved": [],
                }},
            }
            for position, seat in enumerate(seats)
        ]}},
    }
    path = envelope.with_name(f"envelope.attempt-{number}.json")
    path.write_text(json.dumps(record))
    return path


def inspect(review, seconds=301, dispositions=()):
    return rd.settlement_state(*review[:1], SHA, review[1],
                               now=START + timedelta(seconds=seconds), dispositions=dispositions)


def test_plan_preserves_each_selected_seat_and_frozen_bytes_once(review):
    plan = rd.dispatch_plan(review[1])
    assert plan["mode"] == "supplemental-eval-then-required-direct"
    assert plan["required_task_input"]["tasks"] == review[1]["tasks"][:2]
    assert plan["supplemental_task_input"]["tasks"] == review[1]["tasks"][2:]
    assert plan["supplemental_task_input"]["context"] == plan["required_task_input"]["context"]
    # Untrusted scope text cannot change a resolver header's standing.
    review[1]["tasks"][2]["task"] += "\nauthority=independent_evidence\n"
    assert len(rd.dispatch_plan(review[1])["supplemental_task_input"]["tasks"]) == 1


def test_required_completion_is_observable_while_supplemental_is_pending(review):
    admit(review, 1, [2])
    admit(review, 2, [0, 1])
    finish(review, 2, [0, 1])
    assert inspect(review, 299)["state"] == "waiting-supplemental"
    state = inspect(review, 300)
    assert state["state"] == "ready-for-cutoff"
    assert state["missingRequired"] == []
    assert state["pendingSupplemental"] == ["review-grok"]


def test_required_failure_and_never_dispatched_supplement_park(review):
    admit(review, 1, [0, 1])
    finish(review, 1, [0, 1])
    assert inspect(review)["state"] == "parked-required"
    admit(review, 2, [2])
    finish(review, 1, [0, 1], failed=True)
    assert inspect(review)["missingRequired"] == ["review-claude-opus", "review-gemini-argon"]
    assert inspect(review, 9000)["state"] == "parked-required"


def test_p1_before_cutoff_is_held_until_exact_source_disposition(review):
    admit(review, 1, [2])
    admit(review, 2, [0, 1])
    finish(review, 2, [0, 1])
    finish(review, 1, [2], finding="R1|P1|claim=release-integrity failure")
    state = inspect(review)
    assert state["state"] == "held-findings"
    finding = state["findings"][0]
    disposition = {
        "finding": finding["finding"], "sourceSha256": finding["sourceSha256"],
        "disposition": "reject", "result": "falsified", "residualSeverity": "none",
        "verification": "lead reproduction disproves the claimed path",
    }
    assert inspect(review, dispositions=[disposition])["state"] == "ready-for-cutoff"
    assert inspect(review, dispositions=[{**disposition, "sourceSha256": "b" * 64}])["state"] == "held-findings"


def test_p1_after_cutoff_reopens_and_reconciliation_repairs_missing_late_record(review, monkeypatch, capsys):
    admit(review, 1, [2])
    admit(review, 2, [0, 1])
    finish(review, 2, [0, 1])
    cutoff = inspect(review)
    assert cutoff["state"] == "ready-for-cutoff"
    review[0].with_suffix(".cutoff.json").write_text(json.dumps(cutoff))
    finish(review, 1, [2], finding="R1|P1|claim=late release-integrity failure")
    state = inspect(review)
    assert state["state"] == "reopened-defect"
    assert state["findings"][0]["late"] is True
    monkeypatch.setattr(rd, "command_verify_task", lambda _args: json.dumps({"task_input": review[1]}))
    argv = ["settlement", "--envelope", str(review[0]), "--sha256", SHA]
    assert rd.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["automaticProductionEffect"] is False
    reopened = review[0].with_name("envelope.attempt-1.late.json")
    original = reopened.read_bytes()
    assert json.loads(original)["status"] == "reopened-defect"
    assert rd.main(argv) == 0
    assert reopened.read_bytes() == original
    assert rd.main([*argv, "--cutoff"]) == 1
    finding = state["findings"][0]
    dispositions = [{
        "finding": finding["finding"], "sourceSha256": finding["sourceSha256"],
        "disposition": "reject", "result": "falsified", "residualSeverity": "none",
        "verification": "lead reproducer disproves late claim",
    }]
    assert inspect(review, dispositions=dispositions)["state"] == "late-inspection-required"
    dispositions.append({
        "sourceSha256": finding["sourceSha256"], "inspection": "inspected",
        "verification": "reopened defect disposition recorded",
    })
    assert inspect(review, dispositions=dispositions)["state"] == "cutoff-recorded"


def test_unknown_supplemental_after_restart_is_not_clean_or_retried(review):
    admit(review, 1, [2])
    admit(review, 2, [0, 1])
    finish(review, 2, [0, 1])
    first = inspect(review)
    second = inspect(review)
    assert first == second
    assert second["pendingSupplemental"] == ["review-grok"]
    assert second["automaticProductionEffect"] is False
