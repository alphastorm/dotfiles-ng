#!/usr/bin/env python3
"""Prepare an admitted repository council; never dispatch reviewers.

The lead supplies judgments. Existing epoch, sequence, receipt and dispatch CLIs
own their contracts. An existing envelope is verification-only; frozen inputs
and matching receipts can resume without refreezing or repeating a passed proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import qualification
import review_dispatch as dispatch
from review_sequence import ACTIONS, PROOF_CLASSES

HERE = Path(__file__).resolve().parent
PYTHON = HERE / ".venv/bin/python"
MODES = ("design", "initial", "material-redesign", "founder-requested")
GENERATED_PACKET_FIELDS = {
    "review_record_path", "review_record_sha256", "provider_data_allowlist",
    "reviewer_access_profile_allowlist",
}
PACKET_INPUT_FIELDS = set(qualification.PACKET_FIELDS) - GENERATED_PACKET_FIELDS
HONESTY_FIELDS = {
    "known_deterministic_failures", "new_risk_classes", "cross_subsystem_omissions",
    "incomplete_invariant_ids",
}
REQUIRED_RECORD_FIELDS = HONESTY_FIELDS | {
    "touched_risk_domains", "invariant_proof_matrix", "proof_classes",
}
OPTIONAL_RECORD_FIELDS = {
    "remediated_finding_ids", "resolved_finding_ids", "disputed_or_unresolved_p01",
    "remediation_scope", "lead_verification", "material_change_categories",
    "lifecycle_design_artifacts", "founder_followup_authorization",
}
NEW_EPOCH = "use a new review_id in the same sequence; preserve the bound history"
FIX_INPUT = "fix the input and rerun the same command"


def _object(value, field, required, optional=()):
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be an object")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing or unknown:
        raise ValueError(f"{field}: missing={sorted(missing)}, unknown={sorted(unknown)}")
    return value


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{field} must be a nonempty string without NUL")
    return value


def _strings(value, field, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{field} must be {'a nonempty' if nonempty else 'a'} string list")
    for index, item in enumerate(value):
        _text(item, f"{field}[{index}]")
    return value


def _identifier(value, field):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", _text(value, field)):
        raise ValueError(f"{field} must be a safe single directory component")
    return value


def _absolute(value, field):
    path = Path(_text(value, field))
    if not path.is_absolute():
        raise ValueError(f"{field} must be absolute")
    return str(path.resolve())


def _paths(value, field, *, nonempty=False):
    _strings(value, field, nonempty=nonempty)
    try:
        return list(dispatch._repository_files(value)) if value else []
    except dispatch.DispatchError as exc:
        raise ValueError(f"{field}: {exc}") from exc


def validate_input(value):
    """Validate the wrapper shape; leave readiness policy to the existing gates."""
    spec = _object(value, "input", {
        "version", "review_mode", "review_sequence_id", "review_id", "lead_family",
        "prior_epoch", "subject", "assurance_scope", "record_fields", "proofs", "packet_fields",
    }, {"provider_data_restriction"})
    if type(spec["version"]) is not int or spec["version"] != 1:
        raise ValueError("version must be 1")
    if spec["review_mode"] not in MODES:
        raise ValueError(f"review_mode must be one of {MODES}")
    if spec["lead_family"] not in ("gpt", "claude"):
        raise ValueError("lead_family must be gpt or claude")
    _identifier(spec["review_id"], "review_id")
    _text(spec["review_sequence_id"], "review_sequence_id")
    _text(spec["assurance_scope"], "assurance_scope")
    prior = spec["prior_epoch"]
    if prior is not None:
        _object(prior, "prior_epoch", {"record_path", "action"})
        prior["record_path"] = _absolute(prior["record_path"], "prior_epoch.record_path")
        if _text(prior["action"], "prior_epoch.action") not in ACTIONS:
            raise ValueError("prior_epoch.action must be full-council, targeted-refuter or none")
    subject = _object(spec["subject"], "subject", {
        "repository_path", "base", "commit", "artifact_source", "changed_paths", "deleted_paths",
        "evidence_files",
    })
    for field in ("repository_path", "artifact_source"):
        subject[field] = _absolute(subject[field], f"subject.{field}")
    for field in ("base", "commit"):
        if not re.fullmatch(r"[0-9a-f]{40}", _text(subject[field], f"subject.{field}")):
            raise ValueError(f"subject.{field} must be a full lowercase 40-hex commit")
    for field in ("changed_paths", "deleted_paths", "evidence_files"):
        subject[field] = _paths(subject[field], f"subject.{field}", nonempty=field == "evidence_files")
    changed, deleted, evidence = (set(subject[key]) for key in (
        "changed_paths", "deleted_paths", "evidence_files",
    ))
    if not changed | deleted:
        raise ValueError("subject.changed_paths/deleted_paths must name at least one path")
    if changed & deleted or deleted & evidence:
        raise ValueError("subject.deleted_paths must not overlap changed_paths or evidence_files")
    if changed - evidence:
        raise ValueError(f"subject.evidence_files omits changed_paths: {sorted(changed - evidence)}")
    fields = _object(spec["record_fields"], "record_fields", REQUIRED_RECORD_FIELDS, OPTIONAL_RECORD_FIELDS)
    for field in HONESTY_FIELDS | {"touched_risk_domains", "remediated_finding_ids",
                                   "resolved_finding_ids", "disputed_or_unresolved_p01",
                                   "material_change_categories"}:
        if field in fields:
            _strings(fields[field], f"record_fields.{field}")
    matrix = fields["invariant_proof_matrix"]
    if not isinstance(matrix, list) or not matrix:
        raise ValueError("record_fields.invariant_proof_matrix must be a nonempty list")
    for index, row in enumerate(matrix):
        name = f"record_fields.invariant_proof_matrix[{index}]"
        _object(row, name, {"invariant_id", "changed_paths", "risk_domains", "preserved_guard",
                            "decisive_check", "result", "evidence"})
        row["changed_paths"] = _paths(row["changed_paths"], name + ".changed_paths", nonempty=True)
        _strings(row["risk_domains"], name + ".risk_domains", nonempty=True)
        for field in ("invariant_id", "preserved_guard", "decisive_check", "result", "evidence"):
            _text(row[field], name + "." + field)
    scope = fields.get("remediation_scope")
    if scope is not None:
        _object(scope, "record_fields.remediation_scope", {"finding_ids", "adjacent_invariant_ids", "changed_paths"})
        for field in ("finding_ids", "adjacent_invariant_ids"):
            _strings(scope[field], f"record_fields.remediation_scope.{field}", nonempty=True)
        scope["changed_paths"] = _paths(scope["changed_paths"], "record_fields.remediation_scope.changed_paths", nonempty=True)
    if "lead_verification" in fields:
        rows = fields["lead_verification"]
        if not isinstance(rows, list):
            raise ValueError("record_fields.lead_verification must be a list")
        for index, row in enumerate(rows):
            name = f"record_fields.lead_verification[{index}]"
            _object(row, name, {"finding_id", "result", "evidence"})
            for field in row:
                _text(row[field], name + "." + field)
    lifecycle = fields.get("lifecycle_design_artifacts")
    if lifecycle is not None:
        _object(lifecycle, "record_fields.lifecycle_design_artifacts", {"state_machine", "failure_matrix"})
        for field in lifecycle:
            lifecycle[field] = _absolute(lifecycle[field], "record_fields.lifecycle_design_artifacts." + field)
    if fields.get("founder_followup_authorization") is not None:
        fields["founder_followup_authorization"] = _absolute(
            fields["founder_followup_authorization"], "record_fields.founder_followup_authorization")
    classes = _object(fields["proof_classes"], "record_fields.proof_classes", PROOF_CLASSES)
    referenced = set()
    for key, row in classes.items():
        name = "record_fields.proof_classes." + key
        _object(row, name, {"status", "evidence_or_justification"}, {"receipt_id"})
        _text(row["evidence_or_justification"], name + ".evidence_or_justification")
        if row["status"] == "passed":
            referenced.add(_identifier(row.get("receipt_id"), name + ".receipt_id"))
        elif row["status"] != "not-applicable" or "receipt_id" in row:
            raise ValueError(name + " requires passed with receipt_id or not-applicable without it")
    if not isinstance(spec["proofs"], list):
        raise TypeError("proofs must be a list")
    ids = set()
    for index, proof in enumerate(spec["proofs"]):
        name = f"proofs[{index}]"
        _object(proof, name, {"id", "command", "cwd"}, {"reuse_from"})
        identifier = _identifier(proof["id"], name + ".id")
        if identifier in ids:
            raise ValueError(name + ".id is duplicated")
        ids.add(identifier)
        command = proof["command"]
        if not isinstance(command, list) or not command:
            raise ValueError(name + ".command must be a nonempty string list")
        for argument_index, argument in enumerate(command):
            if not isinstance(argument, str) or "\0" in argument:
                raise ValueError(f"{name}.command[{argument_index}] must be a string without NUL")
        _text(command[0], name + ".command[0]")
        proof["cwd"] = _absolute(proof["cwd"], name + ".cwd")
        if "reuse_from" in proof:
            proof["reuse_from"] = _absolute(proof["reuse_from"], name + ".reuse_from")
    if spec["review_mode"] != "design" and not any(identifier.startswith("e2e") for identifier in ids):
        raise ValueError("pre-review end-to-end rule: implementation epochs require a proofs entry whose id starts with e2e")
    if ids != referenced:
        raise ValueError(f"proofs.id must equal referenced receipt IDs: missing={sorted(referenced - ids)}, unreferenced={sorted(ids - referenced)}")
    _object(spec["packet_fields"], "packet_fields", PACKET_INPUT_FIELDS)
    if "provider_data_restriction" in spec:
        _strings(spec["provider_data_restriction"], "provider_data_restriction")
    return spec


def grants(document, lead_family, restriction=None):
    """Read candidate grants, never standing or caller-selected reviewers."""
    pairs = set()
    for group in ("strongCritic", "supplements", "leadFamilySecurity", "architectureSpecialists"):
        for reviewer in qualification.profile_reviewers(document, lead_family, group):
            pairs.add((reviewer.data_allowlist_key, reviewer.access_profile))
    if restriction is not None:
        pairs = {(vendor, profile) for vendor, profile in pairs if vendor in restriction}
    return {
        "provider_data_allowlist": sorted({vendor for vendor, _ in pairs}),
        "reviewer_access_profile_allowlist": sorted({profile for _, profile in pairs}),
    }


def _check_git_subject(repo, subject):
    """Use Git's NUL-delimited inventory, independently of artifact content."""
    dispatch._git(repo, "cat-file", "-e", subject["base"] + "^{commit}")
    fields = dispatch._git(repo, "diff", "--name-status", "--no-renames", "-z",
                           subject["base"], subject["commit"]).split("\0")[:-1]
    actual = {"changed_paths": set(), "deleted_paths": set()}
    for status, path in zip(fields[::2], fields[1::2], strict=True):
        if status not in {"A", "M", "T", "D"}:
            raise ValueError(f"subject.base..commit has unsupported git status {status!r}: {path}")
        actual["deleted_paths" if status == "D" else "changed_paths"].add(path)
    mismatches = []
    for field, paths in actual.items():
        declared = set(subject[field])
        if paths != declared:
            mismatches.append(f"{field}: missing={sorted(paths - declared)}, "
                              f"extra={sorted(declared - paths)}")
    if mismatches:
        raise ValueError("subject.base..commit mismatch: " + "; ".join(mismatches))


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(path):
    return {"path": str(path), "sha256": _sha(Path(path))}


def _load(path):
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _write_record(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _pin(path, content):
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"conflicting artifact: {path}")
    else:
        with path.open("xb") as stream:
            stream.write(content)


class Preparation:
    def __init__(self):
        self.logs = None
        self.frozen = False
        self.step = "input"
        self.code = 2
        self.log = None
        self.next_action = FIX_INPUT

    def begin(self, step, next_action=NEW_EPOCH):
        self.step, self.code = step, 1
        self.next_action = next_action if self.frozen else FIX_INPUT
        with tempfile.NamedTemporaryFile(prefix=step + "-", suffix=".log", dir=self.logs, delete=False) as stream:
            self.log = Path(stream.name)
        print(f"full-council: {step}; log={self.log}", file=sys.stderr)

    def run(self, step, script, *args, expected=0, proof=False, next_action=NEW_EPOCH):
        self.begin(step, next_action)
        command = [str(PYTHON), str(HERE / script), *map(str, args)]
        with self.log.open("wb") as log:
            log.write((json.dumps(command) + "\n").encode())
            log.flush()
            result = subprocess.run(command, stdout=log if proof else subprocess.PIPE, stderr=log, check=False)
            self.code = result.returncode
            if not proof:
                log.write(result.stdout)
        if result.returncode != expected:
            raise ValueError(self.log.read_text(encoding="utf-8", errors="replace")[-4000:].strip())
        return result.stdout

    def decision(self, step, record, *, triage=False):
        raw = self.run(step, "review_sequence.py", *(["--triage"] if triage else []), record,
                       expected=20 if triage else 0,
                       next_action="repair the reported record judgment or history; " + NEW_EPOCH)
        decision = json.loads(raw)
        if (triage and decision.get("status") != "ceremony-required") or decision.get(
            "projected_action" if triage else "action"
        ) != "full-council":
            raise ValueError(f"gate did not select full-council: {raw.decode().strip()}")

    def prepare(self, spec, raw):
        subject = spec["subject"]
        repo = Path(subject["repository_path"])
        out = (Path.home() / ".omp/agent/critical-review" / spec["review_id"]).resolve()
        self.begin("output-directory")
        if out.is_relative_to(repo):
            raise ValueError(f"output directory {out} is inside subject.repository_path")
        out.mkdir(parents=True, exist_ok=True)
        record_path = out / "review-record.json"
        envelope = out / "review-dispatch-envelope.json"
        pinned_input = out / "prepare-input.json"
        self.begin("existing-artifacts")
        record = _load(record_path) if record_path.exists() else None
        frozen = record is not None and "artifact_digest" in record
        self.frozen = frozen or envelope.exists()
        if not self.frozen:
            drafts = {"review-record.json", "prepare-input.json", "artifact.diff",
                      "scope.md", "prepare-logs"}
            entries = list(out.iterdir())
            unknown = sorted(path.name for path in entries if path.name not in drafts)
            if unknown:
                raise ValueError(f"unknown pre-freeze artifacts must be preserved: {unknown}")
            for path in entries:
                if path.name == "prepare-logs" and path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        self.logs = out / "prepare-logs"
        self.logs.mkdir(exist_ok=True)
        self.begin("input-binding")
        if self.frozen and pinned_input.is_file() and pinned_input.read_bytes() != raw:
            raise ValueError("prepare-input.json must be byte-equal to the supplied input")
        if envelope.exists():
            if not pinned_input.is_file():
                self.next_action = "run verify-task directly for this envelope, or " + NEW_EPOCH
                raise ValueError("envelope has no pinned prepare-input.json; not wrapper-owned")
            return self.run("verify-task", "review_dispatch.py", "verify-task", "--envelope", envelope,
                            "--sha256", _sha(envelope))
        if frozen:
            if not pinned_input.is_file() or pinned_input.read_bytes() != raw:
                raise ValueError("prepare-input.json must be byte-equal to the frozen epoch input")
        if not frozen:
            self.run("scaffold", "epoch.py", "scaffold", "--mode", spec["review_mode"],
                     "--sequence-id", spec["review_sequence_id"], "--review-id", spec["review_id"],
                     "--out", record_path)
            record = _load(record_path)
            prior = spec["prior_epoch"]
            if prior:
                row = json.loads(self.run("bind", "epoch.py", "bind", "--record", prior["record_path"],
                                          "--action", prior["action"]))
                previous = _load(Path(prior["record_path"]))
                history = previous.get("sequence_history")
                if not isinstance(history, list):
                    raise ValueError("prior_epoch.record_path: sequence_history must be a list")
                record["sequence_history"] = [*history, row]
                record["parent_review_id"] = row["review_id"]
                record["general_review_pass_count"] = sum(
                    entry.get("action") == "full-council" and entry.get("review_mode") != "design"
                    for entry in record["sequence_history"] if isinstance(entry, dict))
                record["targeted_refutation_used"] = any(
                    entry.get("action") == "targeted-refuter" for entry in record["sequence_history"]
                    if isinstance(entry, dict))
            self.begin("record-fields")
            fields = json.loads(json.dumps(spec["record_fields"]))
            for row in fields["invariant_proof_matrix"]:
                row["changed_paths"] = [str(repo / path) for path in row["changed_paths"]]
            if fields.get("remediation_scope"):
                fields["remediation_scope"]["changed_paths"] = [
                    str(repo / path) for path in fields["remediation_scope"]["changed_paths"]]
            if fields.get("lifecycle_design_artifacts"):
                fields["lifecycle_design_artifacts"] = {
                    key: _binding(path) for key, path in fields["lifecycle_design_artifacts"].items()}
            if fields.get("founder_followup_authorization"):
                fields["founder_followup_authorization"] = _binding(fields["founder_followup_authorization"])
            record.update(fields)
            _write_record(record_path, record)
        self.decision("triage", record_path, triage=True)
        self.begin("repository", "commit or restore the dirty paths named below, then " + NEW_EPOCH)
        dispatch.verify_repository(repo, subject["commit"], subject["evidence_files"])
        self.begin("git-subject")
        _check_git_subject(repo, subject)
        self.begin("authority")
        document = dispatch.bind_live_authority()[2]
        generated = grants(document, spec["lead_family"], spec.get("provider_data_restriction"))
        self.begin("provider-authorization", "honor the provider_data_restriction; "
                   "do not run this council while required grants are blocked")
        try:
            for group in ("strongCritic", "supplements", "leadFamilySecurity"):
                for reviewer in qualification.profile_reviewers(document, spec["lead_family"], group):
                    qualification._require_packet_authorization(reviewer, generated)
        except qualification.QualificationError as exc:
            raise ValueError(f"provider_data_restriction={spec.get('provider_data_restriction')!r} "
                             f"blocks preparation: {exc}") from exc
        self.begin("pin-input")
        _pin(pinned_input, raw)
        artifact = out / "artifact.diff"
        _pin(artifact, Path(subject["artifact_source"]).read_bytes())
        scope = out / "scope.md"
        _pin(scope, spec["assurance_scope"].encode())
        if frozen:
            self.run("recheck", "epoch.py", "recheck", "--record", record_path)
        else:
            self.run("freeze", "epoch.py", "freeze", "--record", record_path, "--artifact", artifact,
                     "--changed", *(repo / path for path in subject["changed_paths"]),
                     "--deleted", *(repo / path for path in subject["deleted_paths"]))
            self.frozen = True
        record = _load(record_path)
        for proof in spec["proofs"]:
            self.begin("receipt-" + proof["id"])
            receipt = Path(proof.get("reuse_from", out / ("proof-" + proof["id"] + ".json")))
            reuse = "reuse_from" in proof or receipt.exists()
            if reuse:
                stored = _load(receipt)
                if stored.get("command") != proof["command"]:
                    raise ValueError(f"proofs.{proof['id']}.command differs from stored receipt {receipt}")
                cwd = stored.get("cwd")
                if not isinstance(cwd, str) or not Path(cwd).is_absolute() or str(Path(cwd).resolve()) != proof["cwd"]:
                    raise ValueError(f"proofs.{proof['id']}.cwd differs from stored receipt {receipt}")
            self.run("proof-" + proof["id"], "make_receipt.py", "--subject-record", record_path,
                     "--receipt", receipt, *( ["--reuse"] if reuse else ["--cwd", proof["cwd"], "--", *proof["command"]]),
                     proof=True, next_action="inspect this proof log; if frozen bytes need correction, " + NEW_EPOCH)
            record["proof_receipts"][proof["id"]] = _binding(receipt)
            _write_record(record_path, record)
        self.decision("full-gate", record_path)
        self.begin("packet")
        packet = {**spec["packet_fields"], **generated,
                  "review_record_path": str(record_path), "review_record_sha256": _sha(record_path)}
        packet_path = out / "packet.md"
        # JSON is YAML, and preserves arbitrary lead-authored JSON value shapes.
        _pin(packet_path, (json.dumps(packet, indent=2, sort_keys=True) + "\n").encode())
        return self.run("prepare", "review_dispatch.py", "prepare", "--review-class", "initial",
                        "--lead-family", spec["lead_family"], "--scope", scope, "--packet", packet_path,
                        "--record", record_path, "--manifest", out / "panel-selection.json",
                        "--repo", repo, "--commit", subject["commit"],
                        *(arg for path in subject["evidence_files"] for arg in ("--file", path)),
                        "--subject", out / "frozen-subject.json", "--receipt", out / "resolver-receipt.json",
                        "--out", envelope)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    preparation = Preparation()
    try:
        preparation.begin("input", "correct the named input field and rerun --input FILE.json")
        preparation.code = 2
        parser = _Parser(description=__doc__)
        commands = parser.add_subparsers(dest="command", required=True)
        prepare = commands.add_parser("prepare")
        prepare.add_argument("--input", type=Path, required=True)
        args = parser.parse_args(argv)
        raw = args.input.read_bytes()
        spec = validate_input(json.loads(raw))
        result = preparation.prepare(spec, raw)
        sys.stdout.buffer.write(result)
        return 0
    except (OSError, ValueError, TypeError, qualification.QualificationError, dispatch.DispatchError) as exc:
        reason = str(exc)
        if preparation.log:
            with preparation.log.open("a", encoding="utf-8") as log:
                log.write(reason + "\n")
        print(f"full-council: {preparation.step} failed (exit {preparation.code}): {reason}\n"
              f"log={preparation.log}\nnext action: {preparation.next_action}", file=sys.stderr)
        return 2 if preparation.step == "input" else 1


if __name__ == "__main__":
    raise SystemExit(main())
