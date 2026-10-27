#!/usr/bin/env python3
"""Public, no-provider integration coverage for the admitted-council wrapper."""
from __future__ import annotations

import copy
import json
import os
import subprocess

import pytest
import yaml

import full_council as fc
import qualification
import review_dispatch as rd
from test_review_dispatch import _git, _packet_text
from test_review_sequence import _design_record, _founder_record, _panel, _ready_record


def make_case(tmp_path, mode="initial"):
    """Build only public synthetic data; also usable by an end-to-end smoke."""
    home = tmp_path / "home"
    authority_path = home / ".omp/agent/skills/critical-review/qualification.yml"
    authority_path.parent.mkdir(parents=True)
    document = _panel()
    authority_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    (authority_path.parent / rd.RECEIPT_SCHEMA_FILENAME).write_text(
        rd.receipt_schema_text(qualification.validate_qualification(document)), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "design.md").write_text("# Design\nKeep the dispatch boundary fail closed.\n", encoding="utf-8")
    (repo / "controller.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "deleted.py").write_text("RETIRED = True\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "predecessor")
    (repo / "deleted.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "subject")
    record = (_design_record(tmp_path / "records") if mode == "design" else
              _founder_record(tmp_path / "records") if mode == "founder-requested" else
              _ready_record(tmp_path / "records", mode))
    fields = {key: copy.deepcopy(value) for key, value in record.items()
              if key in fc.REQUIRED_RECORD_FIELDS | fc.OPTIONAL_RECORD_FIELDS}
    changed = ["design.md"] if mode == "design" else ["controller.py"]
    fields["invariant_proof_matrix"][0]["changed_paths"] = [*changed, "deleted.py"]
    if fields.get("remediation_scope"):
        fields["remediation_scope"]["changed_paths"] = [*changed, "deleted.py"]
    if fields.get("founder_followup_authorization"):
        fields["founder_followup_authorization"] = fields["founder_followup_authorization"]["path"]
    history = record["sequence_history"]
    packet = qualification._parse_packet_text(_packet_text(
        document, record_path="/not-yet-frozen.json", record_sha256="0" * 64,
        diff="Remove deleted.py while keeping the dispatch boundary fail closed."))
    counter = tmp_path / "proof-executions.txt"
    proof = {
        "id": "focused-proof",
        "command": [str(fc.PYTHON), "-c", "from pathlib import Path; "
                    f"p=Path({str(counter)!r}); p.write_text(p.read_text()+'run\\n' if p.exists() else 'run\\n'); "
                    "print('proof-only-stdout'); import sys; print('proof-only-stderr', file=sys.stderr)"],
        "cwd": str(repo.resolve()),
    }
    spec = {
        "version": 1, "review_mode": mode,
        "review_sequence_id": record["review_sequence_id"], "review_id": "CR-" + mode,
        "lead_family": "gpt",
        "prior_epoch": {"record_path": history[-1]["record_path"], "action": history[-1]["action"]} if history else None,
        "subject": {"repository_path": str(repo.resolve()), "commit": _git(repo, "rev-parse", "HEAD").strip(),
                    "artifact_source": str(repo / "design.md"), "changed_paths": changed,
                    "deleted_paths": ["deleted.py"], "evidence_files": changed},
        "assurance_scope": "# Assurance scope\nProduction/hard-to-reverse: dispatch integrity.\n",
        "record_fields": fields, "proofs": [] if mode == "design" else [proof],
        "packet_fields": {key: value for key, value in packet.items() if key in fc.PACKET_INPUT_FIELDS},
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return {"spec": spec, "input": input_path, "home": home, "authority": authority_path,
            "document": document, "repo": repo, "counter": counter,
            "out": home / ".omp/agent/critical-review" / spec["review_id"]}


def invoke(case):
    return subprocess.run([str(fc.PYTHON), str(fc.HERE / "full_council.py"), "prepare", "--input", str(case["input"])],
                          capture_output=True, env={**os.environ, "HOME": str(case["home"])}, check=False)


def save_input(case):
    case["input"].write_text(json.dumps(case["spec"], indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def case(tmp_path, monkeypatch):
    result = make_case(tmp_path)
    monkeypatch.setenv("HOME", str(result["home"]))
    monkeypatch.setattr(rd, "LIVE_AUTHORITY", result["authority"])
    return result


@pytest.mark.parametrize("mode", fc.MODES)
def test_every_mode_prepares_and_verifies_identical_bytes(tmp_path, mode):
    case = make_case(tmp_path, mode)
    first = invoke(case)
    assert first.returncode == 0, first.stderr.decode()
    payload = json.loads(first.stdout)
    assert payload["task_input"]["tasks"]
    assert b"proof-only-stdout" not in first.stdout
    assert b"proof-only-stderr" not in first.stderr
    record = json.loads((case["out"] / "review-record.json").read_text())
    assert record["changed_file_digests"][str(case["repo"] / "deleted.py")] == "DELETED"
    packet = qualification.parse_packet(case["out"] / "packet.md")
    assert packet["review_record_sha256"] == fc._sha(case["out"] / "review-record.json")
    receipt = json.loads((case["out"] / "resolver-receipt.json").read_text())
    assert receipt["reviewClass"] == "initial"
    subject = json.loads((case["out"] / "frozen-subject.json").read_text())
    assert "deleted.py" not in subject["files"]
    second = invoke(case)
    assert second.returncode == 0, second.stderr.decode()
    assert second.stdout == first.stdout
    assert b"verify-task;" in second.stderr
    for step in (b"scaffold;", b"bind;", b"triage;", b"freeze;", b"prepare;", b"proof-focused-proof;"):
        assert step not in second.stderr
    if mode != "design":
        assert case["counter"].read_text() == "run\n"
        proof_logs = list((case["out"] / "prepare-logs").glob("proof-focused-proof-*.log"))
        assert "proof-only-stdout" in proof_logs[0].read_text()
        assert "proof-only-stderr" in proof_logs[0].read_text()


@pytest.mark.parametrize("code", [0, 10])
def test_triage_other_exits_never_reach_freeze_or_prepare(case, monkeypatch, code):
    steps = []
    original = fc.subprocess.run

    def run(command, **kwargs):
        if "review_sequence.py" in str(command[1]):
            return subprocess.CompletedProcess(command, code, b'{"status":"lead-close","projected_action":"none"}')
        steps.append(command)
        return original(command, **kwargs)

    monkeypatch.setattr(fc.subprocess, "run", run)
    with pytest.raises(ValueError):
        fc.Preparation().prepare(fc.validate_input(copy.deepcopy(case["spec"])), case["input"].read_bytes())
    assert not any("freeze" in command or "prepare" in command for command in steps)
    assert not case["counter"].exists()


def stop_full_gate(case, monkeypatch):
    original = fc.Preparation.run
    steps = []

    def run(self, step, script, *args, **kwargs):
        steps.append(step)
        if step == "full-gate":
            return b'{"status":"closed","action":"none"}'
        return original(self, step, script, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(fc.Preparation, "run", run)
        with pytest.raises(ValueError, match="gate did not select full-council"):
            fc.Preparation().prepare(fc.validate_input(copy.deepcopy(case["spec"])), case["input"].read_bytes())
    assert "prepare" not in steps


def test_full_gate_without_council_stops_and_resume_reuses_receipt(case, monkeypatch):
    stop_full_gate(case, monkeypatch)
    receipt = case["out"] / "proof-focused-proof.json"
    previous = receipt.read_bytes(), receipt.stat().st_mtime_ns
    result = invoke(case)
    assert result.returncode == 0, result.stderr.decode()
    assert b"recheck;" in result.stderr and b"freeze;" not in result.stderr
    assert case["counter"].read_text() == "run\n"
    assert (receipt.read_bytes(), receipt.stat().st_mtime_ns) == previous


@pytest.mark.parametrize("field,value", [("command", ["different-command"]), ("cwd", "/"), ("cwd", None)])
def test_receipt_identity_mismatch_never_reexecutes(case, monkeypatch, field, value):
    stop_full_gate(case, monkeypatch)
    receipt = case["out"] / "proof-focused-proof.json"
    stored = json.loads(receipt.read_text())
    stored[field] = value
    receipt.write_text(json.dumps(stored))
    before = receipt.read_bytes()
    result = invoke(case)
    assert result.returncode == 1
    assert f"proofs.focused-proof.{field} differs".encode() in result.stderr
    assert b"next action:" in result.stderr and b"log=" in result.stderr
    assert receipt.read_bytes() == before
    assert case["counter"].read_text() == "run\n"


def test_explicit_reuse_from_never_falls_back_to_execution(case):
    case["spec"]["proofs"][0]["reuse_from"] = str(case["home"] / "absent-receipt.json")
    save_input(case)
    result = invoke(case)
    assert result.returncode == 1
    assert b"absent-receipt.json" in result.stderr
    assert not case["counter"].exists()


@pytest.mark.parametrize("kind", ["dirty", "wrong-commit", "directory", "symlink"])
def test_repository_refusal_before_proofs(case, kind):
    if kind == "dirty":
        (case["repo"] / "controller.py").write_text("dirty\n")
    elif kind == "wrong-commit":
        case["spec"]["subject"]["commit"] = "0" * 40
    elif kind == "directory":
        (case["repo"] / "nested").mkdir()
        (case["repo"] / "nested/file.py").write_text("VALUE=1\n")
        _git(case["repo"], "add", "-A")
        _git(case["repo"], "commit", "-q", "-m", "directory")
        case["spec"]["subject"]["commit"] = _git(case["repo"], "rev-parse", "HEAD").strip()
        case["spec"]["subject"]["evidence_files"].append("nested")
    else:
        (case["repo"] / "link.py").symlink_to("controller.py")
        _git(case["repo"], "add", "-A")
        _git(case["repo"], "commit", "-q", "-m", "symlink")
        case["spec"]["subject"]["commit"] = _git(case["repo"], "rev-parse", "HEAD").strip()
        case["spec"]["subject"]["evidence_files"].append("link.py")
    save_input(case)
    result = invoke(case)
    assert result.returncode == 1
    assert b"repository failed" in result.stderr
    assert not case["counter"].exists()
    assert not (case["out"] / "review-dispatch-envelope.json").exists()


@pytest.mark.parametrize("mutation,field", [
    (lambda s: s.update(review_mode="remediation"), "review_mode"),
    (lambda s: s.update(standing="independent"), "standing"),
    (lambda s: s.update(version=True), "version"),
    (lambda s: s.update(review_id="../escape"), "review_id"),
    (lambda s: s["subject"].update(repository_path="relative"), "subject.repository_path"),
    (lambda s: s["subject"].update(evidence_files=["../escape"]), "subject.evidence_files"),
    (lambda s: s["subject"]["evidence_files"].append("deleted.py"), "subject.deleted_paths"),
    (lambda s: s["record_fields"].pop("known_deterministic_failures"), "known_deterministic_failures"),
    (lambda s: s["record_fields"].update(general_review_pass_count=0), "general_review_pass_count"),
    (lambda s: s["packet_fields"].update(provider_data_allowlist=["openai"]), "provider_data_allowlist"),
    (lambda s: s["proofs"].append(copy.deepcopy(s["proofs"][0])), "proofs[1].id"),
    (lambda s: s["proofs"][0].update(command=[]), "proofs[0].command"),
])
def test_strict_input_names_invalid_field(case, mutation, field):
    mutation(case["spec"])
    with pytest.raises(ValueError) as error:
        fc.validate_input(case["spec"])
    assert field in str(error.value)


@pytest.mark.parametrize("lead", ["gpt", "claude"])
def test_grants_come_only_from_full_council_profile(case, lead):
    document = case["document"]
    profile = document["liveDispatch"]["byLeadFamily"][lead]
    candidates = {name for group in ("strongCritic", "supplements", "leadFamilySecurity", "architectureSpecialists")
                  for name in profile[group]}
    expected = [document["reviewers"][name] for name in candidates]
    grants = fc.grants(document, lead)
    assert grants["provider_data_allowlist"] == sorted({row["data_allowlist_key"] for row in expected})
    assert grants["reviewer_access_profile_allowlist"] == sorted({row["access_profile"] for row in expected})
    restricted = fc.grants(document, lead, ["openai"])
    assert restricted["provider_data_allowlist"] == ["openai"]
    assert restricted["reviewer_access_profile_allowlist"] == sorted(
        {row["access_profile"] for row in expected if row["data_allowlist_key"] == "openai"})


def test_restricted_required_provider_is_refused_not_substituted(case):
    case["spec"]["provider_data_restriction"] = ["openai"]
    save_input(case)
    result = invoke(case)
    assert result.returncode == 1
    assert b"prepare failed" in result.stderr
    assert b"does not authorize" in result.stderr
    assert not (case["out"] / "review-dispatch-envelope.json").exists()


def test_frozen_input_must_be_byte_equal(case, monkeypatch):
    stop_full_gate(case, monkeypatch)
    case["input"].write_bytes(case["input"].read_bytes() + b"\n")
    result = invoke(case)
    assert result.returncode == 1
    assert b"byte-equal" in result.stderr
    assert case["counter"].read_text() == "run\n"


def test_output_inside_repository_is_refused(case):
    case["spec"]["subject"]["repository_path"] = str(case["home"])
    save_input(case)
    result = invoke(case)
    assert result.returncode == 1
    assert b"inside subject.repository_path" in result.stderr
    assert not case["out"].exists()


def test_initial_can_bind_design_history(tmp_path):
    case = make_case(tmp_path)
    previous = _design_record(tmp_path / "previous")
    previous["review_id"] = "CR-prior-design"
    record_path = tmp_path / "prior-design.json"
    record_path.write_text(json.dumps(previous))
    case["spec"]["prior_epoch"] = {"record_path": str(record_path), "action": "full-council"}
    save_input(case)
    result = invoke(case)
    assert result.returncode == 0, result.stderr.decode()
    record = json.loads((case["out"] / "review-record.json").read_text())
    assert record["parent_review_id"] == "CR-prior-design"
    assert record["general_review_pass_count"] == 0


def test_failed_proof_reports_exit_stderr_log_and_next_action(case):
    case["spec"]["proofs"][0]["command"] = [str(fc.PYTHON), "-c", "import sys; print('decisive failure', file=sys.stderr); sys.exit(7)"]
    save_input(case)
    result = invoke(case)
    assert result.returncode == 1
    for expected in (b"proof-focused-proof failed (exit 7)", b"decisive failure", b"log=", b"next action:"):
        assert expected in result.stderr
    assert result.stdout == b""
    assert not (case["out"] / "proof-focused-proof.json").exists()
