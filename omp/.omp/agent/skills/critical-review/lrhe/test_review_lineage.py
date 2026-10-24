"""Reporting must preserve lineage without becoming a dispatch authority."""

import hashlib
import json

import pytest


def test_parked_subject_is_not_a_fresh_dispatch_budget():
    from review_lineage import guidance

    report = guidance(state="parked", subject_rounds=3, inherited_rounds=3)
    assert report["next_action"] == "retain-parked-subject-and-disposition"
    assert report["workstream_rounds"] == 6
    assert report["permits_provider_dispatch"] is False
    assert "action" not in report
    assert any("never redispatch" in item for item in report["required_evidence"])


def test_successor_preserves_parent_and_repository_precedence(tmp_path):
    from review_lineage import guidance

    policy = tmp_path / "REVIEW.md"
    policy.write_text("Repository subject budget and admission rules.\n")
    parent = tmp_path / "ledger.md"
    parent.write_text("#560 parked after three rounds; findings retained.\n")
    report = guidance(
        state="successor", subject_rounds=0, inherited_rounds=3,
        repository_review=policy, parent_ledger=parent,
    )
    assert report["next_action"] == "reconcile-parent-findings-before-repository-admission"
    assert report["workstream_rounds"] == 3
    assert report["repository_review"]["sha256"] == hashlib.sha256(policy.read_bytes()).hexdigest()
    assert report["parent_ledger"]["sha256"] == hashlib.sha256(parent.read_bytes()).hexdigest()
    assert report["permits_provider_dispatch"] is False
    evidence = " ".join(report["required_evidence"])
    assert "anchors" in evidence
    assert "receipt class" in evidence
    assert "no budget reset" in evidence


def test_successor_without_repository_rule_cannot_reset_portable_budget(tmp_path):
    from review_lineage import guidance

    parent = tmp_path / "ledger.md"
    parent.write_text("parent findings\n")
    report = guidance(state="successor", subject_rounds=0, inherited_rounds=3, parent_ledger=parent)
    assert report["next_action"] == "reconcile-lineage-without-resetting-sequence"
    assert "portable sequence" in report["policy_precedence"]


@pytest.mark.parametrize("kwargs", [
    {"state": "successor", "subject_rounds": 0, "inherited_rounds": 3},
    {"state": "parked", "subject_rounds": -1, "inherited_rounds": 0},
    {"state": "parked", "subject_rounds": 3, "inherited_rounds": -1},
    {"state": "landed", "subject_rounds": 3, "inherited_rounds": 0},
])
def test_invalid_lineage_facts_are_refused(kwargs):
    from review_lineage import guidance

    with pytest.raises(ValueError):
        guidance(**kwargs)


def test_cli_is_reporting_only(capsys):
    from review_lineage import main

    assert main(["--state", "parked", "--subject-rounds", "3", "--inherited-rounds", "0"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["permits_provider_dispatch"] is False
    assert "action" not in report
    assert report["counts_source"] == "caller observations; reconcile against retained ledgers"
