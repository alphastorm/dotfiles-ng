#!/usr/bin/env python3
"""Turn immutable production review records into versioned router examples.

`router_dataset.py` is the last-mile step between ledger records and the live
router. Every byte this script omits becomes unobservable later; every byte it
adds must preserve provenance because retention and rights withdrawal both need to
answer *which raw rows created this training row*.

Usage is identical in spirit to the other top-level LRHE scripts:

- `build` consumes reviews, findings, and run records and writes one
  `(review, family, lens)` example per cell.
- `verify` re-validates those examples and proves lineage from every example row
  back to the review and each raw run.
- `delete-source` removes every derived row that came from one review or one
  rights record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

sys.path.insert(0, str(Path(__file__).parent))
import shadow_ledger

DEFAULT_PUBLIC_REPO = Path.home() / ".omp/agent/skills/critical-review/lrhe-data"

HERE = Path(__file__).resolve().parent

DEFAULT_DATA_DIR = DEFAULT_PUBLIC_REPO
DEFAULT_REVIEW_PATH = DEFAULT_DATA_DIR / "ledger" / "reviews.jsonl"
DEFAULT_FINDING_PATH = DEFAULT_DATA_DIR / "ledger" / "findings.jsonl"
DEFAULT_RUN_PATH = DEFAULT_DATA_DIR / "ledger" / "runs.jsonl"
DEFAULT_OUTPUT_DIR = DEFAULT_DATA_DIR / "router"
DEFAULT_OUTPUT_PATH = DEFAULT_OUTPUT_DIR / "router_dataset.jsonl"

DEFAULT_REVIEW_SCHEMA_PATH = HERE / "review.schema.json"
DEFAULT_FINDING_SCHEMA_PATH = HERE / "finding.schema.json"
DEFAULT_RUN_SCHEMA_PATH = HERE / "run.schema.json"
DEFAULT_ROUTER_SCHEMA_PATH = HERE / "router-example.schema.json"

EXIT_OK = 0
EXIT_SCHEMA_ERROR = 2
EXIT_DATA_ERROR = 3

CRITICAL_SEVERITIES = {0, 1}


# ---------------------------------------------------------------------------
# JSONL plumbing


def _read_json(path: Path) -> dict[str, Any]:
    """Parse one JSON document with an explicit type check.

    A non-object schema file gets the same treatment as a malformed row: the
    loader fails immediately so we do not continue with a defaulted contract.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        # TRY004 wants TypeError, but this validates a file's contents, not a
        # caller's argument. Callers catch ValueError to exit EXIT_DATA_ERROR;
        # a TypeError escapes them and turns bad input into a traceback.
        raise ValueError(f"schema must be a JSON object: {path}")  # noqa: TRY004
    return raw


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    """Read a line-delimited JSON payload into a list of objects.

    Empty lines are legal in generated files and comments in future append-only logs.
    A malformed line is a hard failure, because silently dropping it hides exactly
    the kind of data loss this dataset is supposed to make visible.
    """
    if not path.is_file():
        raise FileNotFoundError(f"missing {label} file: {path}")

    rows: list[dict[str, Any]] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped_line = raw_line.strip()
        if not stripped_line:
            continue
        try:
            row = json.loads(stripped_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: bad JSON: {exc}") from None
        if not isinstance(row, dict):
            raise ValueError(  # noqa: TRY004 - malformed data, not a caller type error
                f"{path}:{line_no}: expected JSON object, got {type(row).__name__}")
        rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write JSONL in the stable order expected by diff and delete-source scripts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def _run_schema_with_refs(schema_path: Path) -> Draft202012Validator:
    """Build a validator for v2 nested schemas with local refs resolved.

    `run.schema.json` references `lrhe/data-rights.schema.json`. When that ref is
    unresolved, validation appears to pass on ad-hoc fields and then fails in the
    first real run. Binding both documents in one registry makes that failure
    impossible.
    """
    run_schema = _read_json(schema_path)
    data_rights_schema_path = schema_path.parent / "data-rights.schema.json"
    data_rights_schema = _read_json(data_rights_schema_path)

    for label, schema in {"run": run_schema, "data-rights": data_rights_schema}.items():
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise RuntimeError(f"invalid {label} schema {schema_path if label == 'run' else data_rights_schema_path}: {exc.message}") from None

    resources = [
        (schema.get("$id"), Resource.from_contents(schema))
        for schema in (run_schema, data_rights_schema)
        if isinstance(schema.get("$id"), str)
    ]
    return Draft202012Validator(
        run_schema,
        registry=Registry().with_resources(resources),
        format_checker=FormatChecker(),
    )


def _build_validator(schema_path: Path) -> Draft202012Validator:
    """Parse and compile a schema once.

    We validate both raw and derived rows so schema mismatches fail closed.
    """
    schema = _read_json(schema_path)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise RuntimeError(f"invalid schema {schema_path}: {exc.message}") from None

    if schema_path.name == "run.schema.json":
        return _run_schema_with_refs(schema_path)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _iter_schema_errors(record: dict[str, Any], validator: Draft202012Validator):
    return sorted(
        validator.iter_errors(record),
        key=lambda err: (list(err.absolute_path), err.message),
    )


def _fmt_jsonpath(err) -> str:
    return "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in err.absolute_path
    )


# ---------------------------------------------------------------------------
# Time and key utilities


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso8601(raw: str | None, label: str) -> datetime:
    if raw is None:
        raise ValueError(f"{label}: expected ISO-8601 string, got None")
    if not isinstance(raw, str):
        # Same reason as _read_json: run records come from disk, and the caller at
        # the temporal-leakage check catches ValueError to report a data error.
        raise ValueError(  # noqa: TRY004
            f"{label}: expected ISO-8601 string, got {type(raw).__name__}")
    value = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid timestamp {raw!r}: {exc}") from None


def _days_between(start: datetime, end: datetime) -> int:
    """Non-negative day delta used for outcome maturity.

    A review with a later observed window than reviewed-at still counts as zero.
    That keeps the field monotonic and avoids negative maturities from broken
    recorder clocks.
    """
    delta = (end.date() - start.date()).days
    return int(delta) if delta > 0 else 0


def _stable_short_id(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _run_started_at(run: dict[str, Any]) -> datetime | None:
    started = (run.get("execution") or {}).get("started_at")
    if not isinstance(started, str):
        return None
    try:
        return _parse_iso8601(started, f"run {run.get('run_id', '<missing>')}.execution.started_at")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Hard gates: intentionally match score_lrhe.gate_failures semantics


def hard_gate_failures(run: dict[str, Any]) -> list[str]:
    """Every hard failure that disqualifies run evidence.

    The router dataset must exclude exactly what the scorer excludes, or it will
    learn from records that never made it into metrics.
    """
    safety = run.get("safety") or {}
    reviewer = run.get("reviewer") or {}
    out: list[str] = []

    if safety.get("telemetry_complete") is not True:
        out.append("telemetry_incomplete")
    if safety.get("schema_valid") is not True:
        out.append("reviewer_output_invalid")
    if int(safety.get("tool_violations") or 0) > 0:
        out.append("tool_violation")
    if safety.get("wrote_to_repo") is not False:
        out.append("wrote_to_repo")
    if safety.get("spawned_subagent") is not False:
        out.append("spawned_subagent")
    if safety.get("consumed_peer_output") is not False:
        out.append("consumed_peer_output")
    if safety.get("repo_digest_before") != safety.get("repo_digest_after"):
        out.append("repo_mutated")
    if safety.get("timed_out") is not False:
        out.append("timed_out")
    if safety.get("provider_error"):
        out.append("provider_error")

    if reviewer.get("identity_verified") is not True:
        out.append("identity_unverified")
    if reviewer.get("fallback_detected") is not False:
        out.append("fallback_detected")
    if not reviewer.get("served_model") or reviewer.get("served_model") != reviewer.get("requested_model"):
        out.append("model_mismatch")

    return out


def _decision_authority_for_role(role: str | None) -> str:
    """Map evidence-source role to the strongest legal decision authority.

    `subtractive` is forbidden until LRHE and live outcomes show removing a
    required critic does not materially reduce critical recall, so we never emit it.
    """
    role = (role or "").lower()
    if role == "refuter":
        return "refuter_selection"
    if role == "critic":
        return "additive_selection"
    return "advisory"


def _canonical_finding_id(fid: str | None, by_id: dict[str, dict[str, Any]]) -> str | None:
    if not fid:
        return None
    seen: set[str] = set()
    current = fid
    while current:
        if current in seen:
            return current
        seen.add(current)
        node = by_id.get(current)
        if not node:
            return current
        dup = node.get("duplicate_of")
        if not dup:
            return current
        if dup == current:
            return current
        current = dup
    return fid


def _example_id(review_id: str, family: str, lens: str, run_ids: list[str], version: str) -> str:
    return f"{version}|{review_id}|{family}|{lens}|{_stable_short_id(*run_ids)}"


def _source_data_rights(review: dict[str, Any], run: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen = set()
    for rid in review.get("data_rights_record_ids", []):
        if isinstance(rid, str) and rid not in seen:
            out.append(rid)
            seen.add(rid)
    run_right = run.get("input_rights_record_id")
    if isinstance(run_right, str) and run_right not in seen:
        out.append(run_right)
        seen.add(run_right)
    return out


def _features(review: dict[str, Any]) -> dict[str, Any]:
    """Features are copied verbatim from `review.schema.json`.

    Recomputing or sanitizing would break the immutability contract. Labels are the
    only part of this pipeline allowed to accrue after dispatch.
    """
    feats = review.get("features")
    if not isinstance(feats, dict):
        return {}
    return dict(feats)


def _result_window_days(review: dict[str, Any]) -> tuple[int, bool]:
    outcomes = review.get("outcomes") or {}
    reviewed_at_raw = review.get("reviewed_at")
    observed_raw = outcomes.get("observed_through")

    if reviewed_at_raw is None or observed_raw is None:
        return 0, False

    reviewed_at = _parse_iso8601(str(reviewed_at_raw), "review.reviewed_at")
    observed_at = _parse_iso8601(str(observed_raw), "review.outcomes.observed_through")
    return _days_between(reviewed_at, observed_at), True


def _panel_run_row_by_cell(
    review: dict[str, Any],
    run_by_id: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Map review panel rows to non-gate-failed run records, grouped by cell.

    If a cell appears multiple times, we keep the full lineage but we only train on
    one canonical run for that cell.
    """
    out: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)

    for row in review.get("panel", []):
        if not isinstance(row, dict):
            continue
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        run = run_by_id.get(run_id)
        if not run:
            continue
        if hard_gate_failures(run):
            continue
        family = row.get("family", "")
        lens = row.get("lens", "")
        out[(str(family), str(lens))].append((row, run))

    return out


def _count_labels(
    findings: list[dict[str, Any]],
    unique_allowed: bool,
    outcomes: dict[str, Any],
    family: str,
    confirmed_by_family: dict[str, set[str]],
) -> dict[str, Any]:
    """Count router labels for one cell using one panel lane and one lens."""
    # Vocabulary comes from shadow_ledger, never a second copy here. It already
    # drifted once: this counted only `unsupported` while the ledger has always
    # treated `falsified` as unsupported too, so a family whose findings were
    # actively refuted looked cleaner than one whose findings were merely
    # unsupported -- understating exactly the false-positive burden these labels
    # exist to price.
    confirmed = [f for f in findings if f.get("lead_disposition") in shadow_ledger.CONFIRMING]
    unsupported = [f for f in findings if f.get("lead_disposition") in shadow_ledger.UNSUPPORTED]
    duplicate = [f for f in findings if f.get("lead_disposition") == "duplicate"]

    if unique_allowed:
        others = {
            fid
            for fam, fam_findings in confirmed_by_family.items()
            if fam != family
            for fid in fam_findings
        }
        unique_to_candidate = len(confirmed_by_family.get(family, set()) - others)
    else:
        unique_to_candidate = None

    caused_change = None
    escaped = None
    if outcomes:
        if outcomes.get("observed_through") is not None:
            caused_change = bool(
                outcomes.get("caused_test")
                or outcomes.get("caused_code_change")
                or outcomes.get("caused_design_change")
                or outcomes.get("caused_rollback")
                or outcomes.get("decision_changed")
            )
            escaped = bool(outcomes.get("escaped_defect", False))

    return {
        "n_confirmed": len(confirmed),
        "n_confirmed_critical": sum(1 for f in confirmed if f.get("severity") in CRITICAL_SEVERITIES),
        "n_unsupported": len(unsupported),
        "n_duplicate": len(duplicate),
        "n_unique_to_candidate": unique_to_candidate,
        "caused_change": caused_change,
        "escaped_defect_attributable": escaped,
    }


def _confirmed_sets_by_family(
    findings: list[dict[str, Any]],
) -> tuple[dict[str, set[str]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {
        f.get("finding_id", ""): f for f in findings if f.get("finding_id")
    }
    families: dict[str, set[str]] = defaultdict(set)
    for finding in findings:
        if finding.get("lead_disposition") != "confirmed":
            continue
        family = finding.get("reviewer_family")
        if not isinstance(family, str):
            continue
        families[family].add(_canonical_finding_id(finding.get("finding_id"), by_id) or "")
    return families, by_id


def _cell_lineage_run_ids(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[str]:
    run_ids = {
        r.get("run_id")
        for _, r in pairs
        if isinstance(r.get("run_id"), str)
    }
    return sorted(run_ids)


# ---------------------------------------------------------------------------
# Commands


def cmd_build(args: argparse.Namespace) -> int:
    """Build versioned router examples from immutable ledger rows."""
    reviews = _read_jsonl(args.reviews, "reviews")
    findings = _read_jsonl(args.findings, "findings")
    runs = _read_jsonl(args.runs, "runs")

    review_validator = _build_validator(args.review_schema)
    finding_validator = _build_validator(args.finding_schema)
    run_validator = _build_validator(args.run_schema)
    router_validator = _build_validator(args.router_schema)

    bad: list[str] = []
    for idx, row in enumerate(reviews, 1):
        for err in _iter_schema_errors(row, review_validator)[:5]:
            bad.append(f"review[{idx}] {_fmt_jsonpath(err)}: {err.message}")
    for idx, row in enumerate(findings, 1):
        for err in _iter_schema_errors(row, finding_validator)[:5]:
            bad.append(f"finding[{idx}] {_fmt_jsonpath(err)}: {err.message}")
    for idx, row in enumerate(runs, 1):
        for err in _iter_schema_errors(row, run_validator)[:5]:
            bad.append(f"run[{idx}] {_fmt_jsonpath(err)}: {err.message}")
    if bad:
        print("input schema validation failed:", file=sys.stderr)
        for line in bad[:120]:
            print(f"  {line}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR

    finding_by_review: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        review_id = finding.get("review_id")
        if isinstance(review_id, str):
            finding_by_review[review_id].append(finding)

    run_by_id: dict[str, dict[str, Any]] = {}
    run_duplicates: set[str] = set()
    for run in runs:
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        if run_id in run_by_id:
            run_duplicates.add(run_id)
        run_by_id[run_id] = run

    if run_duplicates:
        print("duplicate run_id in runs: " + ", ".join(sorted(run_duplicates)), file=sys.stderr)
        return EXIT_DATA_ERROR

    examples: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()

    for review in reviews:
        review_id = review.get("review_id", "<missing_review_id>")
        panel = review.get("panel")

        if not isinstance(panel, list) or not panel:
            counters["reviews_with_no_panel"] += 1
            print(f"review {review_id}: panel must be a non-empty array", file=sys.stderr)
            continue

        counters["panel_rows_seen"] += len(panel)
        surviving: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in panel:
            if not isinstance(row, dict):
                counters["panel_rows_invalid"] += 1
                continue
            run_id = row.get("run_id")
            if not isinstance(run_id, str):
                counters["panel_rows_missing_run_id"] += 1
                continue
            run = run_by_id.get(run_id)
            if run is None:
                counters["panel_rows_missing_run_record"] += 1
                continue
            fail_reasons = hard_gate_failures(run)
            if fail_reasons:
                counters["panel_rows_gate_failed"] += 1
                for reason in fail_reasons:
                    counters[f"gate_fail:{reason}"] += 1
                continue
            surviving.append((row, run))

        by_cell = _panel_run_row_by_cell(review, run_by_id)
        for cell, pairs in by_cell.items():
            if len(pairs) > 1:
                counters["cells_with_multiple_live_runs"] += 1
                run_ids = ", ".join(_cell_lineage_run_ids(pairs))
                print(f"review {review_id} cell {cell} has {len(pairs)} live runs; keeping {pairs[0][1].get('run_id')} and lineage={run_ids}", file=sys.stderr)

        counters["panel_rows_surviving_gates"] += len(surviving)

        starts: list[datetime] = []
        for _, r in surviving:
            started_at = _run_started_at(r)
            if started_at:
                starts.append(started_at)

        if not starts:
            counters["reviews_missing_panel_start"] += 1
            print(f"review {review_id}: no parsable panel run started_at; skipping", file=sys.stderr)
            continue

        earliest_start = min(starts)

        try:
            frozen_at = _parse_iso8601(
                review.get("features_frozen_at"),
                f"review {review_id}.features_frozen_at",
            )
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return EXIT_DATA_ERROR

        if frozen_at > earliest_start:
            counters["temporal_leakage_reviews"] += 1
            print(
                f"review {review_id}: features_frozen_at={review.get('features_frozen_at')} "
                f"later than earliest panel run start={earliest_start.isoformat()}"
            )
            continue

        review_findings = finding_by_review.get(str(review_id), [])
        confirmed_by_family, _ = _confirmed_sets_by_family(review_findings)

        complete_cells = len(surviving)
        if complete_cells == len(panel):
            counters["complete_panels"] += 1
        else:
            counters["incomplete_panels"] += 1

        maturity_days, outcome_known = _result_window_days(review)

        for (family, lens), pairs in sorted(by_cell.items()):
            if not pairs:
                continue

            sorted_pairs = sorted(
                pairs,
                key=lambda pair: _run_started_at(pair[1])
                or datetime.max.replace(tzinfo=timezone.utc),
            )
            chosen_row, chosen_run = sorted_pairs[0]

            candidate_findings = [
                f for f in review_findings if f.get("reviewer_family") == family
            ]
            candidate_finding_ids = sorted({
                str(f.get("finding_id"))
                for f in candidate_findings
                if isinstance(f.get("finding_id"), str)
            })
            outcomes = review.get("outcomes") or {}
            label_counts = _count_labels(
                candidate_findings,
                unique_allowed=(complete_cells == len(panel)),
                outcomes=outcomes,
                family=family,
                confirmed_by_family=confirmed_by_family,
            )

            execution = chosen_run.get("execution") or {}
            labels = dict(label_counts)
            labels["cost_usd"] = execution.get("provider_reported_cost_usd")
            labels["latency_ms"] = execution.get("latency_ms")
            # Lineage to a null outcome can be valid; this is why we keep the null
            # explicitly in the output row.
            if not outcome_known:
                labels["caused_change"] = None
                labels["escaped_defect_attributable"] = None

            source_run_ids = _cell_lineage_run_ids(sorted_pairs)
            example = {
                "schema_version": 1,
                "example_id": _example_id(str(review_id), family, lens, source_run_ids, args.dataset_version),
                "dataset_version": args.dataset_version,
                "built_at": _utc_now(),
                "source": {
                    "review_id": str(review_id),
                    "epoch_commit": str(review.get("epoch_commit", "")),
                    "run_ids": source_run_ids,
                    "finding_ids": candidate_finding_ids,
                    "data_rights_record_ids": _source_data_rights(review, chosen_run),
                    "provenance": "production_review",
                },
                "features": _features(review),
                "candidate": {
                    "family": str(family),
                    "lens": str(lens),
                    "role": str(chosen_row.get("role", "")),
                    "requested_model": str(chosen_row.get("requested_model", "")),
                    "served_model": chosen_row.get("served_model"),
                },
                "labels": labels,
                "label_maturity_days": maturity_days,
                "decision_authority": _decision_authority_for_role(chosen_row.get("role")),
                "holdout_group": str(review_id),
            }

            errs = list(_iter_schema_errors(example, router_validator))
            if errs:
                for err in errs[:10]:
                    print(
                        f"example {example['example_id']} schema error at {_fmt_jsonpath(err)}: {err.message}",
                        file=sys.stderr,
                    )
                return EXIT_SCHEMA_ERROR

            examples.append(example)
            counters["examples_emitted"] += 1

    _write_jsonl(args.out, examples)

    print(f"wrote {len(examples)} examples to {args.out}")
    print(f"output dataset_version: {args.dataset_version}")
    print("build summary:")
    print(f"  complete panels: {counters['complete_panels']}")
    print(f"  incomplete panels: {counters['incomplete_panels']}")
    print(f"  temporal-leakage rejections: {counters['temporal_leakage_reviews']}")
    print(f"  reviews with no panel: {counters['reviews_with_no_panel']}")
    print(f"  panel rows seen: {counters['panel_rows_seen']}")
    print(f"  panel rows surviving gates: {counters['panel_rows_surviving_gates']}")
    print(f"  panel rows missing run record: {counters['panel_rows_missing_run_record']}")
    print(f"  panel rows gate-failed: {counters['panel_rows_gate_failed']}")
    print(f"  gate-failed run run failures: {counters['panel_rows_gate_failed']}")
    print(f"  reviews missing panel start times: {counters['reviews_missing_panel_start']}")
    print(f"  cells with duplicate live runs: {counters['cells_with_multiple_live_runs']}")

    if any(k.startswith("gate_fail:") for k in counters):
        print("  gate failure reasons:")
        for k, v in sorted((k, n) for k, n in counters.items() if k.startswith("gate_fail:")):
            print(f"    {k.removeprefix('gate_fail:')}: {v}")

    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    """Validate derived examples against schema and raw lineage."""
    examples = _read_jsonl(args.examples, "router examples")
    reviews = _read_jsonl(args.reviews, "reviews")
    runs = _read_jsonl(args.runs, "runs")

    router_validator = _build_validator(args.router_schema)

    review_ids = {r.get("review_id") for r in reviews if isinstance(r.get("review_id"), str)}
    run_ids = {r.get("run_id") for r in runs if isinstance(r.get("run_id"), str)}

    errors: list[str] = []
    for idx, row in enumerate(examples, 1):
        for err in _iter_schema_errors(row, router_validator)[:3]:
            errors.append(f"example[{idx}] {_fmt_jsonpath(err)}: {err.message}")

        source = row.get("source")
        if not isinstance(source, dict):
            errors.append(f"example[{idx}] source must be object, got {type(source).__name__}")
            continue

        review_id = source.get("review_id")
        if review_id not in review_ids:
            errors.append(f"example[{idx}] source.review_id {review_id!r} not present in review input")

        raw_run_ids = source.get("run_ids", [])
        if not isinstance(raw_run_ids, list):
            errors.append(f"example[{idx}] source.run_ids must be an array")
            continue

        for run_id in raw_run_ids:
            if not isinstance(run_id, str):
                errors.append(f"example[{idx}] source.run_ids contains non-string value {run_id!r}")
                continue
            if run_id not in run_ids:
                errors.append(f"example[{idx}] source.run_ids includes missing run_id {run_id!r}")

    if errors:
        print("verify failed:", file=sys.stderr)
        for line in errors[:120]:
            print(f"  {line}", file=sys.stderr)
        return EXIT_DATA_ERROR

    print(f"verify passed: {len(examples)} examples")
    return EXIT_OK


def cmd_delete_source(args: argparse.Namespace) -> int:
    """Delete derived examples that came from one review or one rights record."""
    examples = _read_jsonl(args.examples, "router examples")
    out = args.out or args.examples

    kept: list[dict[str, Any]] = []
    removed = 0

    for row in examples:
        source = row.get("source")
        if not isinstance(source, dict):
            kept.append(row)
            continue

        if args.review_id and source.get("review_id") == args.review_id:
            removed += 1
            continue

        if args.data_rights_record_id:
            rights = source.get("data_rights_record_ids", [])
            if isinstance(rights, list) and args.data_rights_record_id in rights:
                removed += 1
                continue

        kept.append(row)

    _write_jsonl(out, kept)

    target = args.review_id or args.data_rights_record_id
    print(f"delete-source removed {removed} examples for {target!r}")
    print(f"remaining examples: {len(kept)} in {out}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # -------------------- build
    b = sub.add_parser(
        "build",
        help="emit one router example per (review, family, lens) with enforced invariants",
    )
    b.add_argument("--reviews", type=Path, default=DEFAULT_REVIEW_PATH, help=f"review records (default: {DEFAULT_REVIEW_PATH})")
    b.add_argument("--findings", type=Path, default=DEFAULT_FINDING_PATH, help=f"finding records (default: {DEFAULT_FINDING_PATH})")
    b.add_argument("--runs", type=Path, default=DEFAULT_RUN_PATH, help=f"run records v2 nested (default: {DEFAULT_RUN_PATH})")
    b.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_PATH, help=f"output dataset (default: {DEFAULT_OUTPUT_PATH})")
    b.add_argument("--dataset-version", default="router-v1")
    b.add_argument("--review-schema", dest="review_schema", type=Path, default=DEFAULT_REVIEW_SCHEMA_PATH)
    b.add_argument("--finding-schema", dest="finding_schema", type=Path, default=DEFAULT_FINDING_SCHEMA_PATH)
    b.add_argument("--run-schema", dest="run_schema", type=Path, default=DEFAULT_RUN_SCHEMA_PATH)
    b.add_argument("--router-schema", dest="router_schema", type=Path, default=DEFAULT_ROUTER_SCHEMA_PATH)
    b.set_defaults(fn=cmd_build)

    # -------------------- verify
    v = sub.add_parser("verify", help="validate examples and lineage references")
    v.add_argument("--examples", type=Path, default=DEFAULT_OUTPUT_PATH, help=f"router_dataset.jsonl path (default: {DEFAULT_OUTPUT_PATH})")
    v.add_argument("--reviews", type=Path, default=DEFAULT_REVIEW_PATH, help=f"review records (default: {DEFAULT_REVIEW_PATH})")
    v.add_argument("--runs", type=Path, default=DEFAULT_RUN_PATH, help=f"run records (default: {DEFAULT_RUN_PATH})")
    v.add_argument("--router-schema", dest="router_schema", type=Path, default=DEFAULT_ROUTER_SCHEMA_PATH)
    v.set_defaults(fn=cmd_verify)

    # -------------------- delete-source
    d = sub.add_parser("delete-source", help="delete examples derived from one source review/rights record")
    d.add_argument("--examples", type=Path, default=DEFAULT_OUTPUT_PATH, help=f"router_dataset.jsonl path (default: {DEFAULT_OUTPUT_PATH})")
    d.add_argument("--out", type=Path, default=None,
                   help="output path (default: same as --examples)")
    who = d.add_mutually_exclusive_group(required=True)
    who.add_argument("--review-id", dest="review_id")
    who.add_argument("--data-rights-record-id", dest="data_rights_record_id")
    d.set_defaults(fn=cmd_delete_source)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
