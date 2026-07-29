#!/usr/bin/env python3
"""Ledger and router invariants for the last untested modules.

They are intentionally focused on invariants that can go wrong silently:
    - v2 nested fields being ignored in ingest/review
    - hard-gate failures being counted but still tolerated
    - temporal leakage between feature freeze and run start
    - label-time and disposition accounting semantics
    - lock replayability and determinism contracts
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from test_invariants import HERE, PY, _one_run, _run


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _read_jsonl_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_validator(path: Path) -> Draft202012Validator:
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")), format_checker=FormatChecker())


def _run_cmd(args: list[str], cwd: Path = HERE, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([PY, *args], cwd=cwd, capture_output=True, text=True, check=False)


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _run_for_review(
    run_id: str,
    family: str,
    item_id: str,
    started_at: str,
    served: str,
    requested: str | None = None,
    *,
    completed_at: str | None = None,
    evidence: list[str] | None = None,
    additional: dict | None = None,
) -> dict:
    requested = requested or served
    row = {
        "run_id": run_id,
        "item_id": item_id,
        "family": family,
        "lens": "floor",
        "execution.started_at": started_at,
        "execution.completed_at": completed_at or started_at,
        "reviewer.requested_model": requested,
        "reviewer.served_model": served,
        "evidence": evidence if evidence is not None else [
            "R01|P1|conf=0.80|claim=synthetic finding|evidence=src/main.py:10 observed|impact=test|verify=lint",
        ],
    }
    if additional:
        row.update(additional)
    return _one_run(**row)


def _review_record(*, review_id: str, runs: list[dict],
                  reviewed_at: str = _iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
                  features_frozen_at: str | None = None,
                  outcomes: dict | None = None,
                  ) -> dict:
    if not runs:
        raise ValueError("review must include at least one run")

    panel = []
    rights_ids: list[str] = []
    for run in runs:
        reviewer = run.get("reviewer", {})
        panel.append({
            "family": run.get("family", ""),
            "lens": run.get("lens", ""),
            "role": run.get("role", "critic"),
            "requested_model": reviewer.get("requested_model", ""),
            "served_model": reviewer.get("served_model", None),
            "run_id": run["run_id"],
        })
        right = run.get("input_rights_record_id")
        if isinstance(right, str):
            rights_ids.append(right)

    review: dict[str, object] = {
        "schema_version": 1,
        "review_id": review_id,
        "repo": "synthetic-repo",
        "epoch_commit": runs[0].get("input_rights_record_id", "epoch-commit"),
        "reviewed_at": reviewed_at,
        "risk_tier": "critical",
        "panel": panel,
        "features": {
            "change_type": "feature",
            "languages": ["py"],
            "changed_files": 1,
            "changed_lines": 1,
            "packet_tokens": 123,
        },
        "features_frozen_at": features_frozen_at or reviewed_at,
        "data_rights_record_ids": sorted(set(rights_ids)),
    }
    if outcomes is not None:
        review["outcomes"] = outcomes
    return review


def _finding_payload(*, review_id: str, finding_id: str, family: str, run_id: str,
                    disposition: str, severity: int = 2) -> dict:
    return {
        "finding_id": finding_id,
        "review_id": review_id,
        "reviewer_family": family,
        "run_id": run_id,
        "severity": severity,
        "claim": "synthetic finding",
        "lead_disposition": disposition,
    }


def test_shadow_ingest_keeps_nested_run_fields_and_cost(tmp_path: Path):
    """Empty v2 fields hid real telemetry for years; missing nested extraction makes training silent."""
    run = _run_for_review(
        run_id="nested-01",
        family="claude",
        item_id="S1-0001",
        started_at=_iso(datetime(2026, 2, 1, tzinfo=timezone.utc)),
        served="claude/pinned",
        additional={"execution.provider_reported_cost_usd": 0.01},
    )
    runs_path = tmp_path / "runs.jsonl"
    findings_path = tmp_path / "findings.jsonl"
    _write_jsonl(runs_path, [run])

    _run(["shadow_ledger.py", "ingest", "--runs", str(runs_path), "--out", str(findings_path)])
    out = _read_jsonl_rows(findings_path)
    assert len(out) == 1
    finding = out[0]
    assert finding["reviewer_family"] == "claude"
    assert finding["requested_model"] == "claude/pinned"
    assert finding["served_model"] == "claude/pinned"
    assert finding["run_id"] == "nested-01"
    assert finding["cost_usd"] == 0.01
    assert finding["cost_usd"] is not None
def test_shadow_ingest_counts_and_refuses_hard_gate_run_failures(tmp_path: Path):
    """A run that trips one hard gate must not produce evidence and must still be counted."""
    run = _run_for_review(
        run_id="bad-02",
        family="claude",
        item_id="S1-0002",
        started_at=_iso(datetime(2026, 2, 1, tzinfo=timezone.utc)),
        served="claude/pinned",
        additional={"safety.tool_violations": 1},
    )
    runs_path = tmp_path / "runs.jsonl"
    findings_path = tmp_path / "findings.jsonl"
    _write_jsonl(runs_path, [run])

    result = _run_cmd([
        "shadow_ledger.py", "ingest", "--runs", str(runs_path), "--out", str(findings_path)
    ])
    assert result.returncode == 0
    assert _read_jsonl_rows(findings_path) == []
    combined = result.stdout + result.stderr
    assert "gate-failed runs: 1" in combined
    assert "tool_violation" in combined


def test_shadow_ingest_outputs_schema_valid_findings(tmp_path: Path):
    """Shadow findings are production inputs; any schema break must be caught now, not in training."""
    run = _run_for_review(
        run_id="schema-03",
        family="claude",
        item_id="S1-0003",
        started_at=_iso(datetime(2026, 2, 2, tzinfo=timezone.utc)),
        served="claude/pinned",
    )
    runs_path = tmp_path / "runs.jsonl"
    findings_path = tmp_path / "findings.jsonl"
    _write_jsonl(runs_path, [run])

    _run(["shadow_ledger.py", "ingest", "--runs", str(runs_path), "--out", str(findings_path)])

    validator = _load_validator(HERE / "finding.schema.json")
    findings = _read_jsonl_rows(findings_path)
    assert findings
    for idx, row in enumerate(findings, 1):
        errors = list(validator.iter_errors(row))
        if errors:
            details = "; ".join(f"{idx}:{e.message}" for e in errors)
            pytest.fail(f"finding schema violation for row {idx}: {details}")


def test_shadow_review_is_schema_valid_and_warns_when_freeze_is_late(tmp_path: Path):
    """Feature clocks are a hard boundary; the router must never learn from post-run snapshots."""
    run = _run_for_review(
        run_id="review-04",
        family="claude",
        item_id="S1-0004",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        served="claude/pinned",
    )
    runs_path = tmp_path / "runs.jsonl"
    diff_path = tmp_path / "diff.diff"
    review_path = tmp_path / "review.json"
    _write_jsonl(runs_path, [run])
    diff_path.write_text(
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1 +1 @@\n"
        "-print(1)\n"
        "+print(2)\n",
        encoding="utf-8",
    )

    result = _run_cmd([
        "shadow_ledger.py",
        "review",
        "--runs", str(runs_path),
        "--diff", str(diff_path),
        "--out", str(review_path),
        "--epoch-commit", "review-epoch-04",
        "--review-id", "rev-04",
        "--change-type", "bugfix",
        "--risk-tier", "critical",
        "--packet-tokens", "99",
        "--features-frozen-at", "2026-01-02T00:00:00Z",
    ])

    assert result.returncode == 0
    assert "WARNING: features_frozen_at" in result.stdout

    validator = _load_validator(HERE / "review.schema.json")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert not list(validator.iter_errors(review))


# -------------------------------------------------------------- router_dataset

def _run_router_build(tmp_path: Path, *, review: dict, findings: list[dict], runs: list[dict]) -> tuple[subprocess.CompletedProcess, Path]:
    review_path = tmp_path / "reviews.jsonl"
    finding_path = tmp_path / "findings.jsonl"
    run_path = tmp_path / "runs.jsonl"
    out_path = tmp_path / "router.jsonl"

    _write_jsonl(review_path, [review])
    _write_jsonl(finding_path, findings)
    _write_jsonl(run_path, runs)

    result = _run_cmd([
        "router_dataset.py",
        "build",
        "--reviews", str(review_path),
        "--findings", str(finding_path),
        "--runs", str(run_path),
        "--out", str(out_path),
    ])
    return result, out_path


def test_router_dataset_refuses_temporal_leakage_from_frozen_features(tmp_path: Path):
    """The freeze clock must not advance after a run starts; doing so leaks the run itself."""
    run = _run_for_review(
        run_id="r-leak",
        family="claude",
        item_id="S1-0010",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        served="claude/pinned",
    )
    review = _review_record(
        review_id="rev-leak",
        runs=[run],
        reviewed_at=_iso(datetime(2026, 1, 3, tzinfo=timezone.utc)),
        features_frozen_at=_iso(datetime(2026, 1, 4, tzinfo=timezone.utc)),
    )
    findings = [_finding_payload(
        review_id="rev-leak",
        finding_id="f-leak-1",
        family="claude",
        run_id="r-leak",
        disposition="unsupported",
    )]

    result, out_path = _run_router_build(
        tmp_path,
        review=review,
        findings=findings,
        runs=[run],
    )

    assert result.returncode == 0
    out_rows = _read_jsonl_rows(out_path)
    assert not out_rows
    combined = result.stdout + result.stderr
    assert "temporal-leakage rejections" in combined
    assert (
        "features_frozen_at=2026-01-04T00:00:00+00:00" in combined
        or "features_frozen_at=2026-01-04T00:00:00Z" in combined
    )


def test_router_dataset_emits_maturity_zero_and_null_outcomes_without_observed_through(tmp_path: Path):
    """No observed window is the oldest possible maturity: zero days and null outcome labels."""
    started = _iso(datetime(2026, 1, 1, tzinfo=timezone.utc))
    run = _run_for_review(
        run_id="r-nil",
        family="claude",
        item_id="S1-0011",
        started_at=started,
        served="claude/pinned",
    )
    review = _review_record(
        review_id="rev-nil",
        runs=[run],
        reviewed_at=_iso(datetime(2026, 1, 2, tzinfo=timezone.utc)),
        features_frozen_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )
    findings: list[dict] = []

    result, out_path = _run_router_build(
        tmp_path,
        review=review,
        findings=findings,
        runs=[run],
    )
    assert result.returncode == 0
    rows = _read_jsonl_rows(out_path)
    assert len(rows) == 1
    ex = rows[0]
    assert ex["label_maturity_days"] == 0
    assert ex["labels"]["caused_change"] is None
    assert ex["labels"]["escaped_defect_attributable"] is None


def test_router_dataset_matures_labels_ninety_days_for_observed_window(tmp_path: Path):
    """Observed-through age is an honest time axis; a ninety-day window should be visible in the example."""
    run = _run_for_review(
        run_id="r-aged",
        family="claude",
        item_id="S1-0012",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        served="claude/pinned",
    )
    review = _review_record(
        review_id="rev-aged",
        runs=[run],
        reviewed_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        features_frozen_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        outcomes={
            "observed_through": _iso(datetime(2026, 4, 1, tzinfo=timezone.utc)),
        },
    )

    result, out_path = _run_router_build(
        tmp_path,
        review=review,
        findings=[
            _finding_payload(
                review_id="rev-aged",
                finding_id="f-aged-1",
                family="claude",
                run_id="r-aged",
                disposition="unsupported",
            ),
        ],
        runs=[run],
    )

    assert result.returncode == 0
    ex = _read_jsonl_rows(out_path)[0]
    assert ex["label_maturity_days"] == 90


def test_router_dataset_never_emits_subtractive_authority(tmp_path: Path):
    """The router should not be told to remove a required critic without explicit protocol proof."""
    run_c = _run_for_review(
        run_id="r-critic",
        family="claude",
        item_id="S1-0013",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        served="claude/pinned",
    )
    run_r = _run_for_review(
        run_id="r-refute",
        family="grok",
        item_id="S1-0013",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        served="grok/mini",
        additional={"role": "refuter"},
    )
    review = _review_record(
        review_id="rev-authority",
        runs=[run_c, run_r],
        reviewed_at=_iso(datetime(2026, 1, 2, tzinfo=timezone.utc)),
        features_frozen_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )

    result, out_path = _run_router_build(
        tmp_path,
        review=review,
        findings=[
            _finding_payload(
                review_id="rev-authority",
                finding_id="f-auth-1",
                family="claude",
                run_id="r-critic",
                disposition="confirmed",
            ),
            _finding_payload(
                review_id="rev-authority",
                finding_id="f-auth-2",
                family="grok",
                run_id="r-refute",
                disposition="confirmed",
            ),
        ],
        runs=[run_c, run_r],
    )

    assert result.returncode == 0
    rows = _read_jsonl_rows(out_path)
    assert rows
    assert "subtractive" not in {row["decision_authority"] for row in rows}


def test_router_dataset_counts_falsified_as_unsupported_in_labeling(tmp_path: Path):
    """Family outcome counts must treat refutation-as-unsupported as explicit false positives."""
    run_a = _run_for_review(
        run_id="r-a",
        family="claude",
        item_id="S1-0014",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        served="claude/pinned",
    )
    run_b = _run_for_review(
        run_id="r-b",
        family="grok",
        item_id="S1-0014",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        served="grok/pin",
    )
    review = _review_record(
        review_id="rev-disposition",
        runs=[run_a, run_b],
        reviewed_at=_iso(datetime(2026, 1, 2, tzinfo=timezone.utc)),
        features_frozen_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )

    findings = [
        _finding_payload(
            review_id="rev-disposition",
            finding_id="a-1",
            family="claude",
            run_id="r-a",
            disposition="confirmed",
            severity=1,
        ),
        _finding_payload(
            review_id="rev-disposition",
            finding_id="a-2",
            family="claude",
            run_id="r-a",
            disposition="confirmed",
            severity=2,
        ),
        _finding_payload(
            review_id="rev-disposition",
            finding_id="a-3",
            family="claude",
            run_id="r-a",
            disposition="unsupported",
            severity=0,
        ),
        _finding_payload(
            review_id="rev-disposition",
            finding_id="b-1",
            family="grok",
            run_id="r-b",
            disposition="unsupported",
            severity=2,
        ),
        _finding_payload(
            review_id="rev-disposition",
            finding_id="b-2",
            family="grok",
            run_id="r-b",
            disposition="falsified",
            severity=2,
        ),
    ]

    result, out_path = _run_router_build(
        tmp_path,
        review=review,
        findings=findings,
        runs=[run_a, run_b],
    )

    assert result.returncode == 0
    rows = _read_jsonl_rows(out_path)
    by_family = {row["candidate"]["family"]: row["labels"] for row in rows}
    assert by_family["claude"]["n_confirmed"] == 2
    assert by_family["claude"]["n_unsupported"] == 1
    assert by_family["grok"]["n_confirmed"] == 0
    assert by_family["grok"]["n_unsupported"] == 2


def test_router_verify_refuses_missing_run_lineage_and_names_dangling_id(tmp_path: Path):
    """Lost inputs must block promotion, and the missing run_id has to be named for deletion."""
    run = _run_for_review(
        run_id="r-verify",
        family="claude",
        item_id="S1-0015",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        served="claude/pinned",
    )
    review = _review_record(
        review_id="rev-verify",
        runs=[run],
        reviewed_at=_iso(datetime(2026, 1, 2, tzinfo=timezone.utc)),
        features_frozen_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )
    findings = [_finding_payload(
        review_id="rev-verify",
        finding_id="fv-1",
        family="claude",
        run_id="r-verify",
        disposition="confirmed",
    )]
    build_result, out_path = _run_router_build(
        tmp_path,
        review=review,
        findings=findings,
        runs=[run],
    )
    assert build_result.returncode == 0
    empty_runs = tmp_path / "runs-empty.jsonl"
    empty_runs.write_text("", encoding="utf-8")
    review_path = tmp_path / "reviews.jsonl"
    _write_jsonl(review_path, [review])
    # Keep the original review and findings that produced the example.
    verify = _run_cmd([
        "router_dataset.py",
        "verify",
        "--examples", str(out_path),
        "--reviews", str(review_path),
        "--runs", str(empty_runs),
    ])

    assert verify.returncode != 0
    assert "source.run_ids includes missing run_id 'r-verify'" in verify.stdout + verify.stderr


def test_router_delete_source_keeps_other_reviews_only(tmp_path: Path):
    """Surgical deletion must not erase unrelated records when one review is retracted."""
    run_one = _run_for_review(
        run_id="r-keep-1",
        family="claude",
        item_id="S1-0016",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        served="claude/pinned",
    )
    run_two = _run_for_review(
        run_id="r-keep-2",
        family="grok",
        item_id="S1-0017",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        served="grok/pin",
    )
    review_one = _review_record(
        review_id="rev-keep-1",
        runs=[run_one],
        reviewed_at=_iso(datetime(2026, 1, 2, tzinfo=timezone.utc)),
        features_frozen_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )
    review_two = _review_record(
        review_id="rev-keep-2",
        runs=[run_two],
        reviewed_at=_iso(datetime(2026, 1, 2, tzinfo=timezone.utc)),
        features_frozen_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )

    findings = [
        _finding_payload(review_id="rev-keep-1", finding_id="f-1", family="claude", run_id="r-keep-1", disposition="confirmed"),
        _finding_payload(review_id="rev-keep-2", finding_id="f-2", family="grok", run_id="r-keep-2", disposition="confirmed"),
    ]

    all_reviews = tmp_path / "reviews.jsonl"
    all_findings = tmp_path / "findings.jsonl"
    all_runs = tmp_path / "runs.jsonl"
    dataset = tmp_path / "router.jsonl"

    _write_jsonl(all_reviews, [review_one, review_two])
    _write_jsonl(all_findings, findings)
    _write_jsonl(all_runs, [run_one, run_two])
    build = _run_cmd([
        "router_dataset.py",
        "build",
        "--reviews", str(all_reviews),
        "--findings", str(all_findings),
        "--runs", str(all_runs),
        "--out", str(dataset),
    ])
    assert build.returncode == 0

    rows_before = _read_jsonl_rows(dataset)
    assert len(rows_before) == 2
    assert {row["source"]["review_id"] for row in rows_before} == {"rev-keep-1", "rev-keep-2"}

    delete = _run_cmd([
        "router_dataset.py",
        "delete-source",
        "--examples", str(dataset),
        "--review-id", "rev-keep-1",
    ])
    assert delete.returncode == 0

    rows_after = _read_jsonl_rows(dataset)
    assert len(rows_after) == 1
    assert rows_after[0]["source"]["review_id"] == "rev-keep-2"


def test_router_dataset_examples_validate_router_schema(tmp_path: Path):
    """Every emitted example is production input to a train/test boundary and must remain schema-valid."""
    run = _run_for_review(
        run_id="r-valid", family="claude", item_id="S1-0018",
        started_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)), served="claude/pinned",
    )
    review = _review_record(
        review_id="rev-valid",
        runs=[run],
        reviewed_at=_iso(datetime(2026, 1, 2, tzinfo=timezone.utc)),
        features_frozen_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
    )
    findings = [
        _finding_payload(
            review_id="rev-valid",
            finding_id="fv-valid-1",
            family="claude",
            run_id="r-valid",
            disposition="confirmed",
            severity=1,
        )
    ]

    result, out_path = _run_router_build(tmp_path, review=review, findings=findings, runs=[run])
    assert result.returncode == 0

    validator = _load_validator(HERE / "router-example.schema.json")
    rows = _read_jsonl_rows(out_path)
    assert rows
    for row in rows:
        errors = list(validator.iter_errors(row))
        if errors:
            detail = "; ".join(f"{e.path}: {e.message}" for e in errors)
            pytest.fail(f"router example schema violation: {detail}")


# ------------------------------------------------------------- freeze_lock
def _init_temp_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    marker = path / "state.txt"
    marker.write_text("seed", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "state.txt"], check=True)
    subprocess.run([
        "git", "-C", str(path),
        "-c", "user.name=ci",
        "-c", "user.email=ci@example.com",
        "commit", "-m", "init",
    ], check=True)


def _write_terms_manifest(path: Path, digest: str = "0" * 64, rel: str = "terms/manifest.txt") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "MANIFEST.sha256").write_text(f"{digest}  {rel}\n", encoding="utf-8")


def test_freeze_lock_roundtrip_and_drift_detection(tmp_path: Path):
    """A lock must replay byte-for-byte and explicitly name drifted inputs when it does not."""
    public_repo = tmp_path / "public"
    private_repo = tmp_path / "private"
    _init_temp_repo(public_repo)
    _init_temp_repo(private_repo)

    data_dir = tmp_path / "ledger-data"
    corpus = data_dir / "corpus.jsonl"
    assignments = data_dir / "assignments.manifest.json"
    lock = data_dir / "LOCK.json"
    terms_dir = data_dir / "terms"

    data_dir.mkdir(parents=True)
    corpus.write_text('{"item_id":"S1-0001"}\n', encoding="utf-8")
    assignments.write_text('{"assignment":"v1"}\n', encoding="utf-8")
    _write_terms_manifest(terms_dir)

    freeze = _run_cmd([
        "freeze_lock.py",
        "freeze",
        "--public-repo", str(public_repo),
        "--private-repo", str(private_repo),
        "--data-dir", str(data_dir),
        "--lock", str(lock),
    ])
    assert freeze.returncode == 0

    verify_ok = _run_cmd([
        "freeze_lock.py",
        "verify",
        "--public-repo", str(public_repo),
        "--private-repo", str(private_repo),
        "--data-dir", str(data_dir),
        "--lock", str(lock),
    ])
    assert verify_ok.returncode == 0

    corpus.write_text('{"item_id":"S1-0001"}\n{"item_id":"S1-0002"}\n', encoding="utf-8")
    verify_bad = _run_cmd([
        "freeze_lock.py",
        "verify",
        "--public-repo", str(public_repo),
        "--private-repo", str(private_repo),
        "--data-dir", str(data_dir),
        "--lock", str(lock),
    ])

    assert verify_bad.returncode != 0
    combined = verify_bad.stdout + verify_bad.stderr
    assert "drift: lock_inputs.corpus.sha256" in combined


def test_freeze_refuses_a_dirty_tree_unless_told_otherwise(tmp_path: Path):
    """A lock is a claim about a state someone can return to.

    `commit` and `dirty` are both recorded and both diffed at verify, so a lock
    taken over uncommitted work reports drift against the very tree it came from
    as soon as that work lands. The refusal lives here rather than in preflight
    because this is the point of effect: the freeze is the last manual step, and
    the qualification steps before it leave the tree legitimately dirty.
    """
    public_repo = tmp_path / "public"
    private_repo = tmp_path / "private"
    _init_temp_repo(public_repo)
    _init_temp_repo(private_repo)

    data_dir = tmp_path / "ledger-data"
    lock = data_dir / "LOCK.json"
    data_dir.mkdir(parents=True)
    (data_dir / "corpus.jsonl").write_text('{"item_id":"S1-0001"}\n', encoding="utf-8")
    (data_dir / "assignments.manifest.json").write_text('{"assignment":"v1"}\n', encoding="utf-8")
    _write_terms_manifest(data_dir / "terms")

    common = ["--public-repo", str(public_repo), "--private-repo", str(private_repo),
              "--data-dir", str(data_dir), "--lock", str(lock)]
    (private_repo / "in-progress.txt").write_text("uncommitted work", encoding="utf-8")

    refused = _run_cmd(["freeze_lock.py", "freeze", *common])
    combined = refused.stdout + refused.stderr
    assert refused.returncode != 0, combined
    assert "refusing to freeze" in combined, combined
    assert not lock.exists(), "a refused freeze must not leave a lock behind"

    allowed = _run_cmd(["freeze_lock.py", "freeze", "--allow-dirty", *common])
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    recorded = json.loads(lock.read_text())["lock_inputs"]
    assert recorded["private_repo"]["dirty"] is True, "an override must still record what it overrode"
    assert recorded["public_repo"]["dirty"] is False


def test_freeze_lock_names_an_unreadable_tool_instead_of_calling_it_drift(tmp_path: Path):
    """A silent version probe is not a toolchain change, and must not read as one.

    `versions` is re-collected at verify and compared against the lock, so any
    flake in the probe becomes a drift report. `omp --version` boots a Node
    launcher and takes ~2s idle; under a loaded test run it once came back empty
    and the lock reported `drift: versions.omp` for a version that had not moved.
    Verification still fails -- an unverifiable input is not a passing one -- but
    it must say which of the two things happened.
    """
    public_repo = tmp_path / "public"
    private_repo = tmp_path / "private"
    _init_temp_repo(public_repo)
    _init_temp_repo(private_repo)

    data_dir = tmp_path / "ledger-data"
    lock = data_dir / "LOCK.json"
    data_dir.mkdir(parents=True)
    (data_dir / "corpus.jsonl").write_text('{"item_id":"S1-0001"}\n', encoding="utf-8")
    (data_dir / "assignments.manifest.json").write_text('{"assignment":"v1"}\n', encoding="utf-8")
    _write_terms_manifest(data_dir / "terms")

    common = ["--public-repo", str(public_repo), "--private-repo", str(private_repo),
              "--data-dir", str(data_dir), "--lock", str(lock)]
    assert _run_cmd(["freeze_lock.py", "freeze", *common]).returncode == 0

    recorded = json.loads(lock.read_text())["lock_inputs"]["versions"]
    if not any(recorded.values()):
        pytest.skip("no versioned tool on PATH to make unreadable")

    # A PATH without the CLIs reproduces "the probe answered at freeze, not now"
    # without depending on which tools this machine happens to have.
    blind = subprocess.run([PY, "freeze_lock.py", "verify", *common], cwd=HERE,
                           capture_output=True, text=True, check=False,
                           env={**os.environ, "PATH": "/usr/bin:/bin"})
    combined = blind.stdout + blind.stderr
    assert blind.returncode != 0, "an input that cannot be re-read must not verify"
    assert "unreadable: lock_inputs.versions." in combined, combined
    assert "drift: lock_inputs.versions." not in combined, (
        "a probe that went silent was reported as a version change: " + combined)



# --------------------------------------------------------------- judge_lrhe

def test_judge_lrhe_ingest_is_order_deterministic_for_bytes(tmp_path: Path):
    """Judgment aggregation must be order-independent so shard order never changes training labels."""
    prompts_path = tmp_path / "prompts.jsonl"
    responses_a = tmp_path / "responses-a.jsonl"
    responses_b = tmp_path / "responses-b.jsonl"
    out_a = tmp_path / "judge-a.jsonl"
    out_b = tmp_path / "judge-b.jsonl"
    judgments_a = tmp_path / "judgments-a.jsonl"
    judgments_b = tmp_path / "judgments-b.jsonl"

    prompts = [
        {
            "judge_id": "r1|01|grok",
            "run_id": "r1",
            "claim_rid": "01",
            "item_id": "S1-0001",
            "author_family": "claude",
            "judge_family": "grok",
            "role": "judge",
            "round": 1,
        },
        {
            "judge_id": "r1|01|gemi",
            "run_id": "r1",
            "claim_rid": "01",
            "item_id": "S1-0001",
            "author_family": "claude",
            "judge_family": "gemi",
            "role": "judge",
            "round": 1,
        },
    ]
    _write_jsonl(prompts_path, prompts)

    # `served_model` and `--expect` below are what the identity gate reads. A judgement
    # from a model nobody requested is not that family's judgement, and ingest refuses the
    # whole claim rather than half its panel -- so a fixture that omits them exercises the
    # refusal, not the aggregation this test is about.
    responses_one = [
        {"judge_id": "r1|01|grok", "verdict": "CONFIRMED", "label_id": "L1", "confidence": 0.9,
         "judge_family": "grok", "served_model": "xai-oauth/grok-build"},
        {"judge_id": "r1|01|gemi", "verdict": "CONFIRMED", "label_id": "L1", "confidence": 0.8,
         "judge_family": "gemi", "served_model": "google-antigravity/gemini-3.6-flash"},
    ]
    responses_two = list(reversed(responses_one))
    _write_jsonl(responses_a, responses_one)
    _write_jsonl(responses_b, responses_two)
    expect = ["grok=xai-oauth/grok-build", "gemi=google-antigravity/gemini-3.6-flash:high"]

    first = _run_cmd([
        "judge_lrhe.py",
        "ingest",
        "--prompts", str(prompts_path),
        "--responses", str(responses_a),
        "--out", str(out_a),
        "--out-judgments", str(judgments_a), "--expect", *expect,
    ])
    assert first.returncode == 0

    second = _run_cmd([
        "judge_lrhe.py",
        "ingest",
        "--prompts", str(prompts_path),
        "--responses", str(responses_b),
        "--out", str(out_b),
        "--out-judgments", str(judgments_b), "--expect", *expect,
    ])
    assert second.returncode == 0

    assert out_a.read_bytes() == out_b.read_bytes()
    assert judgments_a.read_bytes() == judgments_b.read_bytes()


def test_judge_lrhe_refuses_same_family_judging(tmp_path: Path):
    """Judging a run with the same family as the author is not evidence and must fail ingestion."""
    prompts_path = tmp_path / "prompts.jsonl"
    responses_path = tmp_path / "responses.jsonl"
    judge_out = tmp_path / "judge.jsonl"
    out_judgments = tmp_path / "judgments.jsonl"

    _write_jsonl(prompts_path, [{
        "judge_id": "r1|01|claude",
        "run_id": "r1",
        "claim_rid": "01",
        "item_id": "S1-0001",
        "author_family": "claude",
        "judge_family": "claude",
        "role": "judge",
        "round": 1,
    }])
    _write_jsonl(responses_path, [{
        "judge_id": "r1|01|claude",
        "verdict": "CONFIRMED",
        "label_id": "L1",
        "confidence": 0.8,
        "judge_family": "claude",
    }])

    result = _run_cmd([
        "judge_lrhe.py",
        "ingest",
        "--prompts", str(prompts_path),
        "--responses", str(responses_path),
        "--out", str(judge_out),
        "--out-judgments", str(out_judgments),
    ])

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Refusing 1 claims with same-family judging" in combined
    assert "cross-family violations" in combined
