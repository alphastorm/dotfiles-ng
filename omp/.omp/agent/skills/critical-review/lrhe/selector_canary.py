#!/usr/bin/env python3
"""Validate and score non-authorizing RepoPrompt selector canaries.

This module never calls a provider. Selector runs are collected externally; this
harness proves their corpus binding, rejects Oracle content by construction, and
scores only normalized path selections and bound telemetry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any

EXIT_OK = 0
EXIT_INVALID = 10
EXIT_OUTPUT_REFUSED = 20

CORPUS_SCHEMA = "critical-review-selector-corpus-v1"
RUN_SCHEMA = "critical-review-selector-run-v1"
REPORT_SCHEMA = "critical-review-selector-score-v1"
SELECTION_SCHEMA = "critical-review-selector-selection-v1"
ROLES = frozenset({"required", "allowed_support", "decoy"})
EXPECTED_ARMS = (
    ("gpt-5.6-sol-low", "codexExec", "gpt-5.6-sol-low"),
    ("kimi-k3", "openCode", "opencode-go/kimi-k3"),
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
IDENTIFIER_RE = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")

CORPUS_KEYS = {
    "arms",
    "cases",
    "corpusId",
    "files",
    "replicates",
    "schema",
    "workspace",
    "workspaceSha256",
}
ARM_KEYS = {"agent", "id", "model"}
CASE_KEYS = {"design", "id"}
FILE_KEYS = {"bytes", "caseId", "path", "role", "sha256"}
RUN_KEYS = {
    "armId",
    "caseId",
    "corpusId",
    "durationMs",
    "exportResponse",
    "finalSelectionSha256",
    "oracleDisposition",
    "preOracleSelectionSha256",
    "promptSha256",
    "replicate",
    "runId",
    "schema",
    "selectedByteTotal",
    "selectedPathTokens",
    "selectedPaths",
    "selectedTokenTotal",
    "workspaceSha256",
}
PATH_TOKEN_KEYS = {"path", "tokens"}


class SelectorCanaryError(ValueError):
    pass


class OutputRefusal(RuntimeError):
    pass


@dataclass(frozen=True)
class Arm:
    id: str
    agent: str
    model: str


@dataclass(frozen=True)
class Case:
    id: str
    design: str


@dataclass(frozen=True)
class FileFact:
    path: str
    case_id: str
    role: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class Corpus:
    root: Path
    workspace: Path
    manifest_path: Path
    manifest_sha256: str
    corpus_id: str
    workspace_sha256: str
    replicates: int
    arms: tuple[Arm, ...]
    cases: tuple[Case, ...]
    files: dict[str, FileFact]


@dataclass(frozen=True)
class Run:
    run_id: str
    arm_id: str
    case_id: str
    replicate: int
    selected_paths: tuple[str, ...]
    path_tokens: dict[str, int]
    selected_bytes: int
    selected_tokens: int
    duration_ms: int
    oracle_selection_changed: bool


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SelectorCanaryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise SelectorCanaryError(f"non-finite JSON number is forbidden: {value}")


def _decode_json(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SelectorCanaryError(f"{label} is not UTF-8: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, SelectorCanaryError) as exc:
        raise SelectorCanaryError(f"invalid {label}: {exc}") from exc


def _read_bytes(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise SelectorCanaryError(f"{label} is not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SelectorCanaryError(f"cannot read {label} {path}: {exc}") from exc


def _expect_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SelectorCanaryError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise SelectorCanaryError(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise SelectorCanaryError(f"{label} must be a lowercase hyphenated identifier")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SelectorCanaryError(f"{label} must be a lowercase SHA-256")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SelectorCanaryError(f"{label} must be an integer >= {minimum}")
    return value


def _relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SelectorCanaryError(f"{label} must be a nonempty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SelectorCanaryError(f"{label} escapes or is not normalized: {value!r}")
    normalized = path.as_posix()
    if normalized != value:
        raise SelectorCanaryError(f"{label} is not normalized: {value!r}")
    return normalized


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _workspace_digest(rows: list[dict[str, object]]) -> str:
    normalized = [
        {"bytes": row["bytes"], "path": row["path"], "sha256": row["sha256"]}
        for row in rows
    ]
    return _digest(_canonical_json(normalized))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def prompt_text(case: Case) -> str:
    return (
        "CRITICAL_REVIEW_SELECTOR_CANARY_V1\n\n"
        "This is an offline, non-authorizing selector canary. Oracle prose is discarded "
        "and must not be treated as a plan, review, finding, or decision.\n\n"
        f"Primary design: {case.design}\n\n"
        "Curate the minimum repository context an OMP lead needs to verify the design "
        "against its implementation and directly relevant interfaces. Select the design "
        "and only implementation, dependency, or contract files needed to resolve its "
        "requirements and open question. Prefer precise slices when a whole file is not "
        "needed. Do not infer authority or issue a release/review verdict. Stay inside the "
        "loaded workspace and do not use prior result, score, receipt, or reviewer output."
    )


def prompt_sha256(case: Case) -> str:
    return _digest(prompt_text(case).encode())


def load_corpus(root: Path) -> Corpus:
    root = root.resolve()
    manifest_path = root / "control/corpus-manifest.v1.json"
    raw = _read_bytes(manifest_path, "corpus manifest")
    manifest = _expect_keys(_decode_json(raw, "corpus manifest"), CORPUS_KEYS, "corpus")

    if manifest["schema"] != CORPUS_SCHEMA:
        raise SelectorCanaryError(f"unsupported corpus schema: {manifest['schema']!r}")
    corpus_id = _identifier(manifest["corpusId"], "corpusId")
    if corpus_id != "selector-canary-v1":
        raise SelectorCanaryError(f"unexpected corpusId: {corpus_id}")
    if manifest["workspace"] != "workspace":
        raise SelectorCanaryError("workspace must be the fixed path 'workspace'")
    workspace = root / "workspace"
    if workspace.is_symlink() or not workspace.is_dir():
        raise SelectorCanaryError(f"workspace is not a regular directory: {workspace}")
    replicates = _integer(manifest["replicates"], "replicates", minimum=1)
    if replicates != 3:
        raise SelectorCanaryError("selector-canary-v1 requires exactly three replicates")

    arms_value = manifest["arms"]
    if not isinstance(arms_value, list):
        raise SelectorCanaryError("arms must be an array")
    arms: list[Arm] = []
    for index, value in enumerate(arms_value):
        row = _expect_keys(value, ARM_KEYS, f"arms[{index}]")
        arm_id = _identifier(row["id"], f"arms[{index}].id")
        agent = row["agent"]
        model = row["model"]
        if not isinstance(agent, str) or not agent or not isinstance(model, str) or not model:
            raise SelectorCanaryError(f"arms[{index}] agent and model must be nonempty strings")
        arms.append(Arm(arm_id, agent, model))
    if tuple((arm.id, arm.agent, arm.model) for arm in arms) != EXPECTED_ARMS:
        raise SelectorCanaryError("selector-canary-v1 arm order or identity changed")

    cases_value = manifest["cases"]
    if not isinstance(cases_value, list) or len(cases_value) != 4:
        raise SelectorCanaryError("cases must contain exactly four rows")
    cases: list[Case] = []
    seen_cases: set[str] = set()
    for index, value in enumerate(cases_value):
        row = _expect_keys(value, CASE_KEYS, f"cases[{index}]")
        case_id = _identifier(row["id"], f"cases[{index}].id")
        design = _relative_path(row["design"], f"cases[{index}].design")
        if case_id in seen_cases:
            raise SelectorCanaryError(f"duplicate case id: {case_id}")
        seen_cases.add(case_id)
        cases.append(Case(case_id, design))

    files_value = manifest["files"]
    if not isinstance(files_value, list) or not files_value:
        raise SelectorCanaryError("files must be a nonempty array")
    files: dict[str, FileFact] = {}
    digest_rows: list[dict[str, object]] = []
    for index, value in enumerate(files_value):
        row = _expect_keys(value, FILE_KEYS, f"files[{index}]")
        path = _relative_path(row["path"], f"files[{index}].path")
        case_id = _identifier(row["caseId"], f"files[{index}].caseId")
        role = row["role"]
        size = _integer(row["bytes"], f"files[{index}].bytes")
        digest = _sha256(row["sha256"], f"files[{index}].sha256")
        if case_id not in seen_cases:
            raise SelectorCanaryError(f"file {path} names unknown case {case_id}")
        if role not in ROLES:
            raise SelectorCanaryError(f"file {path} has invalid role {role!r}")
        if path in files:
            raise SelectorCanaryError(f"duplicate file path: {path}")
        full_path = workspace / path
        data = _read_bytes(full_path, f"workspace file {path}")
        if len(data) != size or _digest(data) != digest:
            raise SelectorCanaryError(f"workspace file binding mismatch: {path}")
        files[path] = FileFact(path, case_id, role, size, digest)
        digest_rows.append({"bytes": size, "path": path, "sha256": digest})

    actual_paths: set[str] = set()
    for candidate in workspace.rglob("*"):
        if candidate.is_symlink():
            raise SelectorCanaryError(f"workspace contains symlink: {candidate}")
        if candidate.is_file():
            actual_paths.add(candidate.relative_to(workspace).as_posix())
    if actual_paths != set(files):
        raise SelectorCanaryError(
            f"workspace file set differs: missing={sorted(set(files) - actual_paths)}, "
            f"extra={sorted(actual_paths - set(files))}"
        )

    for case in cases:
        fact = files.get(case.design)
        if fact is None or fact.case_id != case.id or fact.role != "required":
            raise SelectorCanaryError(f"case design is not required and owned by its case: {case.id}")
        if not any(item.case_id == case.id and item.role == "required" for item in files.values()):
            raise SelectorCanaryError(f"case has no required files: {case.id}")

    workspace_sha = _sha256(manifest["workspaceSha256"], "workspaceSha256")
    if _workspace_digest(digest_rows) != workspace_sha:
        raise SelectorCanaryError("workspaceSha256 does not match the manifested files")

    return Corpus(
        root=root,
        workspace=workspace,
        manifest_path=manifest_path,
        manifest_sha256=_digest(raw),
        corpus_id=corpus_id,
        workspace_sha256=workspace_sha,
        replicates=replicates,
        arms=tuple(arms),
        cases=tuple(cases),
        files=files,
    )


def _selection_sha(paths: tuple[str, ...]) -> str:
    return _digest(
        _canonical_json({"schema": SELECTION_SCHEMA, "selectedPaths": list(paths)})
    )


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], str]:
    raw = _read_bytes(path, "selector run file")
    if raw and not raw.endswith(b"\n"):
        raise SelectorCanaryError("selector run JSONL must end with a newline")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            raise SelectorCanaryError(f"blank selector run line: {line_number}")
        value = _decode_json(line, f"selector run line {line_number}")
        if not isinstance(value, dict):
            raise SelectorCanaryError(f"selector run line {line_number} must be an object")
        rows.append(value)
    return rows, _digest(raw)


def load_runs(corpus: Corpus, path: Path) -> tuple[dict[tuple[str, str, int], Run], str]:
    rows, runs_sha = _load_jsonl(path)
    arms = {arm.id: arm for arm in corpus.arms}
    cases = {case.id: case for case in corpus.cases}
    matrix: dict[tuple[str, str, int], Run] = {}

    for index, value in enumerate(rows):
        row = _expect_keys(value, RUN_KEYS, f"runs[{index}]")
        if row["schema"] != RUN_SCHEMA:
            raise SelectorCanaryError(f"runs[{index}] has unsupported schema")
        if row["corpusId"] != corpus.corpus_id:
            raise SelectorCanaryError(f"runs[{index}] corpusId mismatch")
        if row["workspaceSha256"] != corpus.workspace_sha256:
            raise SelectorCanaryError(f"runs[{index}] workspaceSha256 mismatch")
        arm_id = _identifier(row["armId"], f"runs[{index}].armId")
        case_id = _identifier(row["caseId"], f"runs[{index}].caseId")
        replicate = _integer(row["replicate"], f"runs[{index}].replicate", minimum=1)
        if arm_id not in arms or case_id not in cases or replicate > corpus.replicates:
            raise SelectorCanaryError(f"runs[{index}] names an unknown matrix cell")
        run_id = row["runId"]
        expected_run_id = f"{arm_id}:{case_id}:r{replicate}"
        if run_id != expected_run_id:
            raise SelectorCanaryError(f"runs[{index}] runId must be {expected_run_id!r}")
        if row["promptSha256"] != prompt_sha256(cases[case_id]):
            raise SelectorCanaryError(f"runs[{index}] promptSha256 mismatch")
        if row["oracleDisposition"] != "run-discarded" or row["exportResponse"] is not False:
            raise SelectorCanaryError(f"runs[{index}] must discard Oracle output and disable export")

        selected_value = row["selectedPaths"]
        if not isinstance(selected_value, list):
            raise SelectorCanaryError(f"runs[{index}].selectedPaths must be an array")
        selected = tuple(
            _relative_path(item, f"runs[{index}].selectedPaths") for item in selected_value
        )
        if tuple(sorted(set(selected))) != selected:
            raise SelectorCanaryError(f"runs[{index}].selectedPaths must be sorted and unique")
        unknown = sorted(set(selected) - set(corpus.files))
        if unknown:
            raise SelectorCanaryError(f"runs[{index}] selected unmanifested paths: {unknown}")

        token_rows = row["selectedPathTokens"]
        if not isinstance(token_rows, list):
            raise SelectorCanaryError(f"runs[{index}].selectedPathTokens must be an array")
        tokens: dict[str, int] = {}
        token_order: list[str] = []
        for token_index, token_value in enumerate(token_rows):
            token_row = _expect_keys(
                token_value, PATH_TOKEN_KEYS, f"runs[{index}].selectedPathTokens[{token_index}]"
            )
            token_path = _relative_path(token_row["path"], "selectedPathTokens.path")
            if token_path in tokens:
                raise SelectorCanaryError(f"runs[{index}] repeats token path {token_path}")
            tokens[token_path] = _integer(token_row["tokens"], "selectedPathTokens.tokens")
            token_order.append(token_path)
        if tuple(token_order) != selected:
            raise SelectorCanaryError(f"runs[{index}] token rows must match selectedPaths order")

        selected_bytes = _integer(row["selectedByteTotal"], "selectedByteTotal")
        expected_bytes = sum(corpus.files[item].bytes for item in selected)
        if selected_bytes != expected_bytes:
            raise SelectorCanaryError(f"runs[{index}] selectedByteTotal mismatch")
        selected_tokens = _integer(row["selectedTokenTotal"], "selectedTokenTotal")
        if selected_tokens != sum(tokens.values()):
            raise SelectorCanaryError(f"runs[{index}] selectedTokenTotal mismatch")
        duration_ms = _integer(row["durationMs"], "durationMs")
        expected_selection_sha = _selection_sha(selected)
        pre_sha = _sha256(row["preOracleSelectionSha256"], "preOracleSelectionSha256")
        final_sha = _sha256(row["finalSelectionSha256"], "finalSelectionSha256")
        if pre_sha != expected_selection_sha:
            raise SelectorCanaryError(f"runs[{index}] pre-Oracle selection digest mismatch")

        key = (arm_id, case_id, replicate)
        if key in matrix:
            raise SelectorCanaryError(f"duplicate selector matrix cell: {key}")
        matrix[key] = Run(
            run_id=run_id,
            arm_id=arm_id,
            case_id=case_id,
            replicate=replicate,
            selected_paths=selected,
            path_tokens=tokens,
            selected_bytes=selected_bytes,
            selected_tokens=selected_tokens,
            duration_ms=duration_ms,
            oracle_selection_changed=pre_sha != final_sha,
        )

    expected = {
        (arm.id, case.id, replicate)
        for arm in corpus.arms
        for case in corpus.cases
        for replicate in range(1, corpus.replicates + 1)
    }
    if set(matrix) != expected:
        raise SelectorCanaryError(
            f"selector matrix incomplete: missing={sorted(expected - set(matrix))}, "
            f"extra={sorted(set(matrix) - expected)}"
        )
    return matrix, runs_sha


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(float(Fraction(numerator, denominator)), 6)


def _subset_totals(paths: set[str], corpus: Corpus, run: Run) -> dict[str, int]:
    return {
        "files": len(paths),
        "bytes": sum(corpus.files[path].bytes for path in paths),
        "tokens": sum(run.path_tokens[path] for path in paths),
    }


def score(corpus: Corpus, matrix: dict[tuple[str, str, int], Run], runs_sha: str) -> dict[str, Any]:
    case_order = {case.id: index for index, case in enumerate(corpus.cases)}
    arm_order = {arm.id: index for index, arm in enumerate(corpus.arms)}
    all_paths = set(corpus.files)
    cells: list[dict[str, Any]] = []

    for key in sorted(matrix, key=lambda item: (arm_order[item[0]], case_order[item[1]], item[2])):
        run = matrix[key]
        selected = set(run.selected_paths)
        required = {
            path for path, fact in corpus.files.items()
            if fact.case_id == run.case_id and fact.role == "required"
        }
        support = {
            path for path, fact in corpus.files.items()
            if fact.case_id == run.case_id and fact.role == "allowed_support"
        }
        decoy = {
            path for path, fact in corpus.files.items()
            if fact.case_id == run.case_id and fact.role == "decoy"
        }
        foreign = {path for path in all_paths if corpus.files[path].case_id != run.case_id}
        selected_required = selected & required
        selected_support = selected & support
        selected_decoy = selected & decoy
        selected_foreign = selected & foreign
        false_inclusions = selected_decoy | selected_foreign
        cells.append({
            "armId": run.arm_id,
            "caseId": run.case_id,
            "replicate": run.replicate,
            "runId": run.run_id,
            "selectedPaths": list(run.selected_paths),
            "selectedRequiredPaths": sorted(selected_required),
            "missedRequiredPaths": sorted(required - selected),
            "selectedAllowedSupportPaths": sorted(selected_support),
            "selectedDecoyPaths": sorted(selected_decoy),
            "crossCaseLeakagePaths": sorted(selected_foreign),
            "falseInclusionPaths": sorted(false_inclusions),
            "criticalPathRecall": _rate(len(selected_required), len(required)),
            "falseInclusionRate": _rate(len(false_inclusions), len(selected)),
            "crossCaseLeakageRate": _rate(len(selected_foreign), len(selected)),
            "selected": _subset_totals(selected, corpus, run),
            "allowedSupport": _subset_totals(selected_support, corpus, run),
            "decoy": _subset_totals(selected_decoy, corpus, run),
            "crossCase": _subset_totals(selected_foreign, corpus, run),
            "falseInclusion": _subset_totals(false_inclusions, corpus, run),
            "durationMs": run.duration_ms,
            "oracleSelectionChanged": run.oracle_selection_changed,
        })

    stability: list[dict[str, Any]] = []
    case_stability: dict[tuple[str, str], Fraction] = {}
    for arm in corpus.arms:
        for case in corpus.cases:
            runs = [matrix[(arm.id, case.id, replicate)] for replicate in range(1, 4)]
            pairs = []
            fractions = []
            for left_index, right_index in ((0, 1), (0, 2), (1, 2)):
                left = set(runs[left_index].selected_paths)
                right = set(runs[right_index].selected_paths)
                intersection = len(left & right)
                union = len(left | right)
                value = Fraction(1, 1) if union == 0 else Fraction(intersection, union)
                fractions.append(value)
                pairs.append({
                    "leftReplicate": left_index + 1,
                    "rightReplicate": right_index + 1,
                    "intersection": intersection,
                    "union": union,
                    "jaccard": round(float(value), 6),
                })
            mean = sum(fractions, Fraction(0, 1)) / len(fractions)
            case_stability[(arm.id, case.id)] = mean
            stability.append({
                "armId": arm.id,
                "caseId": case.id,
                "pairs": pairs,
                "meanJaccard": round(float(mean), 6),
            })

    aggregates = []
    for arm in corpus.arms:
        arm_cells = [cell for cell in cells if cell["armId"] == arm.id]
        required_hits = sum(len(cell["selectedRequiredPaths"]) for cell in arm_cells)
        required_total = sum(
            len({path for path, fact in corpus.files.items()
                 if fact.case_id == cell["caseId"] and fact.role == "required"})
            for cell in arm_cells
        )
        selected_total = sum(cell["selected"]["files"] for cell in arm_cells)
        support_selected = sum(cell["allowedSupport"]["files"] for cell in arm_cells)
        support_total = sum(
            len({path for path, fact in corpus.files.items()
                 if fact.case_id == cell["caseId"] and fact.role == "allowed_support"})
            for cell in arm_cells
        )
        false_total = sum(cell["falseInclusion"]["files"] for cell in arm_cells)
        foreign_total = sum(cell["crossCase"]["files"] for cell in arm_cells)
        stability_mean = sum(
            (case_stability[(arm.id, case.id)] for case in corpus.cases), Fraction(0, 1)
        ) / len(corpus.cases)
        aggregates.append({
            "armId": arm.id,
            "scoredCells": len(arm_cells),
            "selectedFiles": selected_total,
            "selectedBytes": sum(cell["selected"]["bytes"] for cell in arm_cells),
            "selectedTokens": sum(cell["selected"]["tokens"] for cell in arm_cells),
            "meanSelectedBytes": round(
                sum(cell["selected"]["bytes"] for cell in arm_cells) / len(arm_cells), 6
            ),
            "meanSelectedTokens": round(
                sum(cell["selected"]["tokens"] for cell in arm_cells) / len(arm_cells), 6
            ),
            "microCriticalRecall": _rate(required_hits, required_total),
            "pooledAllowedSupportInclusionRate": _rate(support_selected, support_total),
            "pooledFalseInclusionRate": _rate(false_total, selected_total),
            "pooledCrossCaseLeakageRate": _rate(foreign_total, selected_total),
            "selectedAllowedSupportFiles": support_selected,
            "selectedAllowedSupportBytes": sum(
                cell["allowedSupport"]["bytes"] for cell in arm_cells
            ),
            "selectedAllowedSupportTokens": sum(
                cell["allowedSupport"]["tokens"] for cell in arm_cells
            ),
            "falseInclusionFiles": false_total,
            "falseInclusionBytes": sum(
                cell["falseInclusion"]["bytes"] for cell in arm_cells
            ),
            "falseInclusionTokens": sum(
                cell["falseInclusion"]["tokens"] for cell in arm_cells
            ),
            "meanCaseStability": round(float(stability_mean), 6),
            "meanDurationMs": round(
                sum(cell["durationMs"] for cell in arm_cells) / len(arm_cells), 6
            ),
        })

    return {
        "schemaVersion": 1,
        "metricVersion": REPORT_SCHEMA,
        "corpusId": corpus.corpus_id,
        "corpusManifestSha256": corpus.manifest_sha256,
        "workspaceSha256": corpus.workspace_sha256,
        "runsSha256": runs_sha,
        "matrix": {"expectedCells": 24, "scoredCells": len(cells)},
        "arms": [
            {"id": arm.id, "agent": arm.agent, "model": arm.model} for arm in corpus.arms
        ],
        "cells": cells,
        "stability": stability,
        "armAggregates": aggregates,
    }


def _write_new(path: Path, content: str) -> None:
    path = path.resolve()
    if path.exists() or path.is_symlink():
        raise OutputRefusal(f"refusing to overwrite output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd: int | None = None
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            fd = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o444)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except BaseException:
        if fd is not None:
            os.close(fd)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _validate_output_path(corpus: Corpus, path: Path) -> Path:
    resolved = path.resolve()
    if _is_within(resolved, corpus.workspace) or _is_within(
        resolved, corpus.root / "control"
    ):
        raise OutputRefusal("run and score artifacts must remain outside workspace/ and control/")
    return resolved


def cmd_validate(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus_root)
    message = f"{corpus.corpus_id}: {len(corpus.files)} files, {len(corpus.cases)} cases"
    if args.runs is not None:
        matrix, _runs_sha = load_runs(corpus, args.runs)
        message += f", {len(matrix)} selector runs"
    print(message)
    return EXIT_OK


def cmd_prompts(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus_root)
    out = _validate_output_path(corpus, args.out)
    rows = [
        {
            "caseId": case.id,
            "instructions": prompt_text(case),
            "promptSha256": prompt_sha256(case),
            "schema": "critical-review-selector-prompt-v1",
        }
        for case in corpus.cases
    ]
    content = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    _write_new(out, content)
    print(out)
    return EXIT_OK


def cmd_score(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus_root)
    matrix, runs_sha = load_runs(corpus, args.runs)
    report = score(corpus, matrix, runs_sha)
    out = _validate_output_path(corpus, args.out)
    _write_new(out, json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(out)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate the frozen corpus and runs")
    validate.add_argument("--corpus-root", type=Path, required=True)
    validate.add_argument("--runs", type=Path)
    validate.set_defaults(fn=cmd_validate)

    prompts = subparsers.add_parser("prompts", help="emit selector-safe prompts")
    prompts.add_argument("--corpus-root", type=Path, required=True)
    prompts.add_argument("--out", type=Path, required=True)
    prompts.set_defaults(fn=cmd_prompts)

    score_parser = subparsers.add_parser("score", help="score one complete selector matrix")
    score_parser.add_argument("--corpus-root", type=Path, required=True)
    score_parser.add_argument("--runs", type=Path, required=True)
    score_parser.add_argument("--out", type=Path, required=True)
    score_parser.set_defaults(fn=cmd_score)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except OutputRefusal as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_OUTPUT_REFUSED
    except (OSError, SelectorCanaryError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    raise SystemExit(main())
