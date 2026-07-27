#!/usr/bin/env python3
"""
shadow_ledger.py -- online shadow evaluation from production reviews.

LRHE measures council structure against a fixed public corpus. This measures the
same council against real work, using outcome data that accumulates from reviews
you were doing anyway. No curation, no hand-labeled historical examples.

  ingest    runs.jsonl (+ integrator dispositions) -> findings.jsonl
  outcomes  enrich findings from repository history: did it cause a change, a test,
            or a later revert; did its anchor even exist at the reviewed epoch
  review    one review record (review.schema) from runs + a unified diff
  queue     the only three things a person has to read
  audit     lead dispositions vs an independent cross-family panel, with kappa
  metrics   the eight numbers, with review-clustered bootstrap intervals

WHAT THIS IS NOT. `lead_disposition` is issued by the integrator, and the
integrator is one of the families being compared. That is exactly the
single-family-judge problem LRHE-PROTOCOL.md section 5.2 calls disqualifying, and
that points one direction: it should be read with the warning that every family
is both judge and defendant. This script carries outcomes and metrics, but the
data is what lets us track where they disagree and where we are silently blind.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import score_lrhe

DEFAULT_DATA_DIR = Path.home() / ".omp/agent/skills/critical-review/lrhe-data"
DEFAULT_REVIEW_PATH = DEFAULT_DATA_DIR / "ledger" / "reviews.jsonl"
DEFAULT_FINDING_PATH = DEFAULT_DATA_DIR / "ledger" / "findings.jsonl"

DELIMITER = "\n"

def _append_jsonl(p: Path, row: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with Path(p).open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, sort_keys=True) + DELIMITER)


CRITICAL = (0, 1)
CONFIRMING = ("confirmed",)
UNSUPPORTED = ("unsupported", "falsified")
RNG = random.Random(20260727)

REVIEW_CHANGE_TYPES = ("feature", "bugfix", "refactor", "migration", "dependency",
                       "config", "infrastructure", "docs", "test", "mixed", "unknown")
REVIEW_RISK_TIERS = ("routine", "critical", "critical_plus")
REVIEW_CHANGED_SURFACES = ("public_api", "database_schema", "wire_format", "config_schema",
                           "build", "ci", "deployment", "internal_only")
REVIEW_INDICATORS = ("trust_boundary", "authz", "authn", "secret_handling", "crypto",
                     "migration", "concurrency", "deployment", "destructive_operation",
                     "money", "pii", "external_input", "error_handling")

_TEST_PATH = re.compile(r"(^|/)(tests?|testing|spec)/|(^|/)(test_[^/]+|[^/]+_test)\.[a-z]+$"
                        r"|\.(spec|test)\.[jt]sx?$", re.I)
_REGRESSION_MSG = re.compile(r"\b(revert|hotfix|regression|incident|postmortem|rollback)\b", re.I)
_DIFF_FILE = re.compile(r"^diff --git a/(.+?) b/(.+?)$")


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]


def _write_jsonl(p: Path, rows: list[dict]) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _write_json(p: Path, payload: dict) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read_json(p: Path, label: str) -> dict:
    try:
        value = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} JSON from {p}: {exc}") from exc
    if not isinstance(value, dict):
        # Matches the ValueError raised just above for unreadable JSON: both are
        # "this file is not what it claims", and callers handle one code path.
        raise ValueError(f"{label} metadata at {p} must be a JSON object")  # noqa: TRY004
    return value


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Schema-compliant date-times are timezone-aware, but callers often pass
        # legacy timestamps. Treat naive values as UTC to keep comparisons and
        # sort order deterministic rather than raising at runtime.
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _coerce_non_negative_int(value: object, name: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer; got {value!r}") from exc
    if n < 0:
        raise ValueError(f"{name} must be >= 0; got {n}")
    return n


def _coalesce_scalar(*values: object) -> object | None:
    for v in values:
        if v is not None and v != "":
            return v
    return None


def _coalesce_list(*values: object) -> list[str]:
    for v in values:
        if v is None:
            continue
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return []
    return []


def _validate_enum_list(name: str, value: list[str], allowed: tuple[str, ...]) -> None:
    bad = [v for v in value if v not in allowed]
    if bad:
        raise ValueError(f"{name} has invalid values: {', '.join(sorted(bad))}")


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], check=False, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _path_exists_at(repo: Path, commit: str, path: str) -> bool:
    try:
        return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{commit}:{path}"],
                              check=False, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _run_gate_failures(run: dict, manifest_digest: str | None) -> list[str]:
    """Mirror score_lrhe's gate logic for ingest, so we do not accidentally treat
    untrusted or broken telemetry as evidence.

    Keeping this in one place is not optional: a run that mutates the tree or
    swaps model identity is not a valid data point for the same experiment.
    """
    try:
        return score_lrhe.gate_failures(run, manifest_digest)
    except AttributeError:
        # A future rename in score_lrhe would make this a loud mismatch if we
        # kept going; with no shared helper, hard-failed runs would leak into
        # the ledger and bias live-model selection.
        return []


def _normalize_diff_path(raw: str) -> str:
    path = raw.strip()
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path


def _derive_diff_features(diff: str) -> tuple[set[str], int]:
    """Return changed files and changed line count from a unified diff.

    This deliberately counts +-hunk lines only, not headers, because that is a
    cheap feature we can derive before any reviewer output exists.
    """
    files: set[str] = set()
    changed_lines = 0

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            m = _DIFF_FILE.match(line)
            if m:
                files.add(_normalize_diff_path(m.group(2)))
            continue

        if line.startswith("--- "):
            p = _normalize_diff_path(line[4:].split("\t", 1)[0])
            if p and p != "/dev/null":
                files.add(p)
            continue

        if line.startswith("+++ "):
            p = _normalize_diff_path(line[4:].split("\t", 1)[0])
            if p and p != "/dev/null":
                files.add(p)
            continue

        if (line.startswith("+") or line.startswith("-")) and not line.startswith(("+++ ", "--- ")):
            changed_lines += 1

    return files, changed_lines


def _count_repo_files(repo: Path) -> int | None:
    git_ls = _git(repo, "ls-files")
    if git_ls:
        return len([x for x in git_ls.splitlines() if x.strip()])
    return None


# ---------------------------------------------------------------- ingest

def cmd_ingest(args) -> int:
    """Turn reviewer runs into findings, reusing the scorer's contract parser.

    Parsing lives in exactly one place. A second implementation would drift, and
    the first symptom would be production and benchmark numbers disagreeing for
    reasons nobody can locate.
    """
    runs = _read_jsonl(args.runs)
    dispositions = {}
    for d in (_read_jsonl(args.dispositions) if args.dispositions else []):
        dispositions[(d["run_id"], str(d.get("claim_rid", d.get("rid", ""))))] = d

    out, unparsed = [], 0
    gate_failures = 0
    gate_reasons: Counter[str] = Counter()
    for run in runs:
        fail_reasons = _run_gate_failures(run, args.manifest_digest)
        if fail_reasons:
            gate_failures += 1
            gate_reasons.update(fail_reasons)
            continue

        run_id = run.get("run_id", "")
        reviewer = run.get("reviewer", {})
        execution = run.get("execution", {})
        data_right = run.get("data_rights") or {}
        for i, raw in enumerate(run.get("evidence", [])):
            parsed = score_lrhe._parse_evidence_string(raw)
            if parsed["parse_status"] == score_lrhe.PARSE_FAIL:
                unparsed += 1
                continue
            rid = parsed.get("rid") or f"{i:02d}"
            anchors = score_lrhe.extract_anchors(parsed.get("evidence_text", ""))
            d = dispositions.get((run.get("run_id"), rid), {})
            out.append({
                "finding_id": f"{run_id}|{rid}",
                "review_id": run.get("review_id") or run.get("item_id") or run_id,
                "repo": run.get("repo", ""),
                "epoch_commit": run.get("epoch_commit") or run.get("base_commit", ""),
                "reviewed_at": (run.get("execution") or {}).get("completed_at", ""),
                "risk_tier": run.get("risk_tier", "critical"),
                "reviewer_family": run.get("family", run.get("reviewer", {}).get("family", "")),
                "assigned_lens": run.get("lens", ""),
                "role": run.get("role", "critic"),
                "severity": int(parsed.get("severity", 3)),
                "confidence": parsed.get("confidence"),
                "claim": parsed.get("claim_text", ""),
                "source_evidence": parsed.get("evidence_text", ""),
                "anchors": [f"{a.path}:{a.start or ''}"
                            f"{'-' + str(a.end) if a.end else ''}" for a in anchors],
                "impact": parsed.get("impact_text", ""),
                "verification_procedure": parsed.get("verify_text", ""),
                "lead_disposition": d.get("lead_disposition") or "unresolved",
                "disposition_by": d.get("disposition_by", ""),
                "duplicate_of": d.get("duplicate_of", ""),
                "verification_result": d.get("verification_result", "not_attempted"),
                "refuted_by": d.get("refuted_by", ""),
                "run_id": run_id,
                "requested_model": reviewer.get("requested_model", ""),
                "served_model": reviewer.get("served_model"),
                "data_rights_record_id": run.get("input_rights_record_id", data_right.get("record_id", "")),
                "input_tokens": execution.get("input_tokens"),
                "output_tokens": execution.get("output_tokens"),
                "cost_usd": execution.get("provider_reported_cost_usd"),
                "quota_pool": execution.get("quota_pool", ""),
            })
    _write_jsonl(args.out, out)
    print(f"runs {len(runs)} -> findings {len(out)}  "
          f"(unparsed contract strings: {unparsed}; gate-failed runs: {gate_failures})")
    if gate_reasons:
        print("  gate failure reasons:")
        for reason, count in gate_reasons.most_common():
            print(f"    {reason:<30} {count}")
    print(f"carrying a lead disposition: {sum(1 for f in out if f['lead_disposition'] != 'unresolved')}")
    print(f"wrote {args.out}")
    return 0


def cmd_review(args) -> int:
    """Build one review record from one review's runs and a unified diff.

    Diff-derived parts are computed once; judgment-heavy parts must come from
    args or `--meta` to avoid leaking a model-selection prior from silent
    inference about patch shape.
    """
    runs = _read_jsonl(args.runs)
    if not runs:
        print("no runs supplied", file=sys.stderr)
        return 2

    try:
        meta = _read_json(args.meta, "review meta") if args.meta else {}
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    item_ids = sorted({r.get("item_id") for r in runs if r.get("item_id")})
    if len(item_ids) > 1:
        print(f"runs must belong to one review; got item_ids={item_ids}", file=sys.stderr)
        return 2

    review_id = _coalesce_scalar(args.review_id, meta.get("review_id"))
    if not review_id:
        review_id = item_ids[0] if item_ids else ""
    if not review_id:
        print("review_id is required (--review-id or --meta.review_id / run.item_id)",
              file=sys.stderr)
        return 2

    epoch_commit = _coalesce_scalar(args.epoch_commit, meta.get("epoch_commit"))
    if not epoch_commit:
        print("epoch_commit is required (--epoch-commit or --meta.epoch_commit)", file=sys.stderr)
        return 2

    change_type = _coalesce_scalar(args.change_type, meta.get("change_type"))
    risk_tier = _coalesce_scalar(args.risk_tier, meta.get("risk_tier"))
    packet_tokens = _coalesce_scalar(args.packet_tokens, meta.get("packet_tokens"))
    changed_surfaces = _coalesce_list(args.changed_surfaces, meta.get("changed_surfaces"))
    indicators = _coalesce_list(args.indicators, meta.get("indicators"))
    reviewed_at = _coalesce_scalar(args.reviewed_at, meta.get("reviewed_at"))
    features_frozen_at = _coalesce_scalar(args.features_frozen_at, meta.get("features_frozen_at"))
    author_is_agent = args.author_is_agent
    if author_is_agent is None and "author_is_agent" in meta:
        author_is_agent = bool(meta.get("author_is_agent"))

    if reviewed_at is None:
        review_end_times = [_parse_iso8601((r.get("execution") or {}).get("completed_at", ""))
                            for r in runs]
        review_end_times = [x for x in review_end_times if x is not None]
        if review_end_times:
            reviewed_at = max(review_end_times).isoformat()
        else:
            reviewed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    if features_frozen_at is None:
        features_frozen_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    features_frozen_dt = _parse_iso8601(features_frozen_at)
    if features_frozen_dt is None:
        print(f"features_frozen_at {features_frozen_at!r} is not ISO-8601", file=sys.stderr)
        return 2

    if reviewed_at and _parse_iso8601(reviewed_at) is None:
        print(f"reviewed_at {reviewed_at!r} is not ISO-8601", file=sys.stderr)
        return 2

    if not change_type:
        print("change_type is required (--change-type or --meta.change_type)",
              file=sys.stderr)
        return 2
    if change_type not in REVIEW_CHANGE_TYPES:
        print(f"change_type must be one of {', '.join(REVIEW_CHANGE_TYPES)}", file=sys.stderr)
        return 2
    if not risk_tier:
        print("risk_tier is required (--risk-tier or --meta.risk_tier)",
              file=sys.stderr)
        return 2
    if risk_tier not in REVIEW_RISK_TIERS:
        print(f"risk_tier must be one of {', '.join(REVIEW_RISK_TIERS)}", file=sys.stderr)
        return 2
    if packet_tokens is None:
        print("packet_tokens is required (--packet-tokens or --meta.packet_tokens)",
              file=sys.stderr)
        return 2

    try:
        packet_tokens = _coerce_non_negative_int(packet_tokens, "packet_tokens")
        _validate_enum_list("changed_surfaces", changed_surfaces, REVIEW_CHANGED_SURFACES)
        _validate_enum_list("indicators", indicators, REVIEW_INDICATORS)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        diff = args.diff.read_text()
    except OSError as exc:
        print(f"cannot read diff {args.diff}: {exc}", file=sys.stderr)
        return 2

    changed_files, changed_lines = _derive_diff_features(diff)
    languages = sorted({Path(path).suffix[1:].lower() for path in changed_files
                        if Path(path).suffix})
    has_tests_in_diff = any(_TEST_PATH.search(p) for p in changed_files)

    panel: list[dict] = []
    data_rights_record_ids: list[str] = []
    run_ids_seen: set[str] = set()
    for run in runs:
        run_id = run.get("run_id", "")
        if not run_id:
            print("run_id is required in each run record", file=sys.stderr)
            return 2
        if run_id in run_ids_seen:
            continue
        run_ids_seen.add(run_id)

        reviewer = run.get("reviewer", {})
        family = run.get("family") or reviewer.get("family", "")
        requested_model = reviewer.get("requested_model", "")
        served_model = reviewer.get("served_model")
        if not family:
            print(f"run {run_id} has no family", file=sys.stderr)
            return 2
        if not requested_model:
            print(f"run {run_id} has no requested_model", file=sys.stderr)
            return 2

        panel.append({
            "family": family,
            "lens": run.get("lens", ""),
            "role": run.get("role", "critic"),
            "requested_model": requested_model,
            "served_model": served_model,
            "run_id": run_id,
        })

        data_rights_record_id = run.get("input_rights_record_id")
        if not data_rights_record_id:
            data_rights_record_id = (run.get("data_rights") or {}).get("record_id", "")
        if data_rights_record_id:
            data_rights_record_ids.append(data_rights_record_id)

    late_starts = []
    for run in runs:
        run_id = run.get("run_id", "")
        started_at = _parse_iso8601((run.get("execution") or {}).get("started_at", ""))
        if started_at is not None and started_at < features_frozen_dt:
            late_starts.append(f"{run_id or '<missing>'} started {started_at.isoformat()}")
    if late_starts:
        print(f"WARNING: features_frozen_at ({features_frozen_at}) is after run start time for "
              f"{len(late_starts)} run(s); review features are likely contaminated")
        for item in late_starts:
            print(f"  {item}")

    features = {
        "change_type": change_type,
        "languages": languages,
        "changed_files": len(changed_files),
        "changed_lines": changed_lines,
        "packet_tokens": packet_tokens,
        "has_tests_in_diff": has_tests_in_diff,
    }
    if changed_surfaces:
        features["changed_surfaces"] = changed_surfaces
    if indicators:
        features["indicators"] = indicators
    if author_is_agent is not None:
        features["author_is_agent"] = bool(author_is_agent)

    repo_path = args.repo
    if repo_path:
        repo_files = _count_repo_files(repo_path)
        if repo_files is not None:
            features["repo_files_total"] = repo_files

    out = {
        "schema_version": 1,
        "review_id": review_id,
        "repo": str(repo_path or ""),
        "epoch_commit": epoch_commit,
        "reviewed_at": reviewed_at,
        "risk_tier": risk_tier,
        "features": features,
        "features_frozen_at": features_frozen_at,
        "panel": panel,
        "experiment_id": _coalesce_scalar(args.experiment_id, meta.get("experiment_id"), ""),
        "panel_id": _coalesce_scalar(args.panel_id, meta.get("panel_id"), ""),
        "data_rights_record_ids": sorted(set(data_rights_record_ids)),
    }
    out = {k: v for k, v in out.items() if v not in ("", None)}

    if args.out.suffix == ".jsonl":
        _append_jsonl(args.out, out)
        print(f"appended {args.out}")
    else:
        _write_json(args.out, out)
        print(f"wrote {args.out}")
    return 0


# ---------------------------------------------------------------- outcomes

def cmd_outcomes(args) -> int:
    """Enrich findings from repository history.

    This is the half of the ledger nobody can argue with. A disposition is an
    opinion; a commit that changed the anchored file two days later is a fact.
    """
    findings = _read_jsonl(args.findings)
    repo = Path(args.repo)
    if not (repo / ".git").exists():
        print(f"{repo} is not a git repository", file=sys.stderr)
        return 2

    for f in findings:
        paths = sorted({a.split(":")[0] for a in f.get("anchors", []) if a})
        epoch = f.get("epoch_commit") or ""
        valid = (all(_path_exists_at(repo, epoch, p) for p in paths)
                 if epoch and paths else None)

        commits, tests, regression = [], False, False
        t0 = None
        if f.get("reviewed_at"):
            try:
                t0 = datetime.fromisoformat(f["reviewed_at"].replace("Z", "+00:00"))
            except ValueError:
                t0 = None
        if t0 and paths:
            since = t0.isoformat()
            until = (t0 + timedelta(days=args.window_days)).isoformat()
            for p in paths:
                log = _git(repo, "--no-pager", "log", f"--since={since}", f"--until={until}",
                           "--pretty=format:%H%x1f%s", "--name-only", "--", p)
                for block in log.split("\n\n"):
                    if not block.strip():
                        continue
                    head, *files = block.strip().splitlines()
                    sha, _, subject = head.partition("\x1f")
                    commits.append(sha[:12])
                    if _REGRESSION_MSG.search(subject):
                        regression = True
                    if any(_TEST_PATH.search(x) for x in files):
                        tests = True
        f["resulting_change"] = {
            "caused_code_change": bool(commits),
            "caused_test": tests,
            "commits": sorted(set(commits))[:20],
            "later_revert_or_regression": regression,
            "window_days": args.window_days,
            "anchor_valid_at_epoch": valid,
        }

    _write_jsonl(args.out, findings)
    rc = [f["resulting_change"] for f in findings]
    print(f"findings {len(findings)} over a {args.window_days}d window")
    print(f"  anchored file changed after review : {sum(1 for r in rc if r['caused_code_change'])}")
    print(f"  produced a test                    : {sum(1 for r in rc if r['caused_test'])}")
    print(f"  followed by revert/regression      : {sum(1 for r in rc if r['later_revert_or_regression'])}")
    n_bad = sum(1 for r in rc if r['anchor_valid_at_epoch'] is False)
    print(f"  anchor absent at reviewed epoch    : {n_bad}")
    print(f"wrote {args.out}")
    return 0


# ---------------------------------------------------------------- human queue

def cmd_queue(args) -> int:
    """The only three things a person has to read.

    Everything else disposes of itself. Widening this list is how a shadow
    evaluation quietly turns back into manual labeling.
    """
    findings = _read_jsonl(args.findings)
    rows = []
    for f in findings:
        sev = int(f.get("severity", 3))
        why = None
        if sev in CRITICAL and f.get("lead_disposition") in ("", "unresolved"):
            why = "unresolved P0/P1"
        elif (sev in CRITICAL and f.get("lead_disposition") == "design-choice"
              and f.get("verification_result") in ("", "not_attempted", "inconclusive")):
            # An irreversible tradeoff nobody can settle empirically is a judgement
            # call by definition, and delegating it to a model is how an invariant
            # gets waived without anyone deciding to waive it.
            why = "irreversible tradeoff, no empirical answer"
        elif args.waiver_re and re.search(args.waiver_re, f.get("claim", ""), re.I):
            why = "proposed invariant waiver"
        if why:
            rows.append({**{k: f.get(k) for k in
                            ("finding_id", "review_id", "reviewer_family", "assigned_lens",
                             "severity", "claim", "source_evidence", "verification_procedure",
                             "lead_disposition", "verification_result")},
                         "why_escalated": why})
    _write_jsonl(args.out, rows)
    n = len(findings)
    print(f"findings {n} -> human queue {len(rows)}  ({len(rows) / max(n, 1):.1%})")
    for k, v in Counter(r["why_escalated"] for r in rows).most_common():
        print(f"  {k:<40} {v}")
    print(f"wrote {args.out}")
    return 0


# ---------------------------------------------------------------- audit

def _norm_lead(d: str) -> str:
    if d in CONFIRMING:
        return "real"
    if d in UNSUPPORTED:
        return "not_real"
    return "other"


def _norm_panel(v: str) -> str:
    return {"CONFIRMED": "real", "FABRICATED": "not_real"}.get(v, "other")


def cmd_audit(args) -> int:
    """Sample lead dispositions against an independent cross-family panel.

    The integrator competes with the families it dispositions. Without this the
    per-family numbers measure the integrator's taste wearing the costume of an
    outcome.
    """
    findings = {f["finding_id"]: f for f in _read_jsonl(args.findings)}
    panel = {f"{j['run_id']}|{j['claim_rid']}": j
             for j in (_read_jsonl(args.panel) if args.panel else [])}

    pairs = [(_norm_lead(f.get("lead_disposition", "")), _norm_panel(panel[k]["verdict"]))
             for k, f in findings.items() if k in panel and f.get("lead_disposition")]

    if not pairs:
        sample = RNG.sample(sorted(findings), min(args.n, len(findings)))
        _write_jsonl(args.out, [{"finding_id": k, "claim": findings[k]["claim"],
                                 "source_evidence": findings[k]["source_evidence"],
                                 "reviewer_family": findings[k]["reviewer_family"]}
                                for k in sample])
        print(f"no panel records yet; wrote a {len(sample)}-finding audit sample -> {args.out}")
        print("Run them through judge_lrhe.py with families that neither authored nor")
        print("dispositioned them, then re-run `audit --panel judge.jsonl`.")
        return 0

    from judge_lrhe import cohens_kappa
    lead = [p[0] for p in pairs]
    pan = [p[1] for p in pairs]
    k, po = cohens_kappa(lead, pan)
    print(f"audited findings : {len(pairs)}")
    print(f"raw agreement    : {po:.3f}")
    print(f"Cohen's kappa    : {k:.3f}")
    print(f"\nintegrator vs independent panel "
          f"{'PASSES' if k >= 0.70 else 'FAILS'} the kappa >= 0.70 bar.")
    if k < 0.70:
        print("Treat every per-family number from `metrics` as provisional: the disposition\n"
              "channel they rest on does not agree with independent adjudication.")
    lead_real = sum(1 for a, _ in pairs if a == "real")
    pan_real = sum(1 for _, b in pairs if b == "real")
    print(f"\ncalled real -- integrator {lead_real}, panel {pan_real}")
    if lead_real > pan_real * 1.15:
        print("The integrator confirms materially more than the panel does: the\n"
              "over-acceptance direction the disclosed-invalid literature warns about.")
    return 0


# ---------------------------------------------------------------- metrics

def _boot_ci(values: list[float], B: int = 2000) -> tuple[float, float]:
    if not values:
        return (float("nan"), float("nan"))
    draws = sorted(statistics.fmean(RNG.choices(values, k=len(values))) for _ in range(B))
    return draws[int(0.025 * B)], draws[int(0.975 * B)]


def cmd_metrics(args) -> int:
    findings = _read_jsonl(args.findings)
    if not findings:
        print("no findings", file=sys.stderr)
        return 2
    reviews = sorted({f["review_id"] for f in findings})
    families = sorted({f["reviewer_family"] for f in findings if f["reviewer_family"]})
    confirmed = [f for f in findings if f.get("lead_disposition") in CONFIRMING]

    print(f"reviews {len(reviews)} | findings {len(findings)} | families {len(families)}")
    if len(reviews) < args.min_reviews:
        print(f"\nWARNING: {len(reviews)} reviews is under the {args.min_reviews} these numbers\n"
              f"need to separate a family effect from review-to-review variance. Read the\n"
              f"point estimates as direction only.")
    print()

    # Root-cause dedup, so three restatements of one defect do not inflate a lane.
    roots = defaultdict(set)
    for f in confirmed:
        roots[f["reviewer_family"]].add(f.get("duplicate_of") or f["finding_id"])
    allroots = {r for s in roots.values() for r in s}

    print("1-3. confirmed by family, unique contribution, cost, unsupported rate")
    print(f"     {'family':<10} {'confirmed':>9} {'unique':>7} {'per Mtok':>9} {'unsupported':>13}")
    for fam in families:
        mine = roots.get(fam, set())
        others = {r for g, s in roots.items() if g != fam for r in s}
        toks = sum((f.get("input_tokens") or 0) + (f.get("output_tokens") or 0)
                   for f in findings if f["reviewer_family"] == fam) / 1e6
        n_fam = sum(1 for f in findings if f["reviewer_family"] == fam)
        unsup = sum(1 for f in findings if f["reviewer_family"] == fam
                    and f.get("lead_disposition") in UNSUPPORTED)
        per_tok = f"{len(mine) / toks:.1f}" if toks else "n/a"
        print(f"     {fam:<10} {len(mine):>9} {len(mine - others):>7} {per_tok:>9} "
              f"{unsup:>6} ({unsup / max(n_fam, 1):>3.0%})")
    print(f"     {'council':<10} {len(allroots):>9}")

    dupes = sum(1 for f in findings if f.get("lead_disposition") == "duplicate")
    print(f"\n4. duplicate rate: {dupes}/{len(findings)} = {dupes / len(findings):.1%}")

    checked = [f for f in findings
               if (f.get("resulting_change") or {}).get("anchor_valid_at_epoch") is not None]
    if checked:
        ok = sum(1 for f in checked if f["resulting_change"]["anchor_valid_at_epoch"])
        print(f"5. evidence-anchor validity: {ok}/{len(checked)} = {ok / len(checked):.1%} "
              f"(section 8 wants >= 95%)")
    else:
        print("5. evidence-anchor validity: run `outcomes` first")

    ref = [f for f in findings if f.get("refuted_by")]
    if ref:
        wins = sum(1 for f in ref if f.get("verification_result") == "not_reproduced"
                   or f.get("lead_disposition") == "falsified")
        print(f"6. refutation win/loss: {wins}W/{len(ref) - wins}L over {len(ref)} contested "
              f"({wins / len(ref):.0%} falsified)")
    else:
        print("6. refutation win/loss: no contested findings yet")

    per_review = [sum(1 for f in findings if f["review_id"] == r
                      and (f.get("resulting_change") or {}).get("caused_code_change"))
                  for r in reviews]
    if any(per_review):
        lo, hi = _boot_ci([float(x) for x in per_review])
        print(f"7. changes caused per review: {statistics.fmean(per_review):.2f} "
              f"[{lo:.2f}, {hi:.2f}] review-clustered")
    else:
        print("7. changes caused per review: run `outcomes` first")

    print("\n8. critical findings the reduced council would have missed")
    crit = defaultdict(set)
    for f in confirmed:
        if int(f.get("severity", 3)) in CRITICAL:
            crit[f["reviewer_family"]].add(f.get("duplicate_of") or f["finding_id"])
    total = {r for s in crit.values() for r in s}
    if total:
        for fam in families:
            without = {r for g, s in crit.items() if g != fam for r in s}
            missed = len(total - without)
            print(f"   drop {fam:<10} -> would miss {missed:>3} of {len(total)} "
                  f"({missed / len(total):.0%})")
        print("   Read this next to arm T. Independent equally-capable reviewers produce\n"
              "   nonzero 'would miss' counts by arithmetic alone; a lane earns its place\n"
              "   by beating the same-family triplicate, not by being nonzero.")
    else:
        print("   no confirmed critical findings yet")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "reviews": len(reviews), "findings": len(findings), "families": families,
            "confirmed": len(confirmed), "duplicate_rate": dupes / len(findings),
            "changes_per_review": statistics.fmean(per_review) if per_review else None,
            "unique_by_family": {f: len(roots.get(f, set())
                                        - {r for g, s in roots.items() if g != f for r in s})
                                 for f in families},
        }, indent=2) + "\n")
        print(f"\nwrote {args.json}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("ingest", help="runs.jsonl -> findings.jsonl")
    i.add_argument("--runs", type=Path, required=True)
    i.add_argument("--dispositions", type=Path, default=None,
                   help="integrator calls: {run_id, claim_rid, lead_disposition, ...}")
    i.add_argument("--manifest-digest", type=str, default=None,
                   help="optional assignment-manifest digest for the same gate reasons as score_lrhe")
    i.add_argument("--out", type=Path, default=DEFAULT_FINDING_PATH)
    i.set_defaults(fn=cmd_ingest)

    r = sub.add_parser("review", help="build review.schema record from one review's runs")
    r.add_argument("--runs", type=Path, required=True,
                   help="one review's v2 run records (jsonl)")
    r.add_argument("--diff", type=Path, required=True,
                   help="unified diff used at dispatch")
    r.add_argument("--out", type=Path, default=DEFAULT_REVIEW_PATH)
    r.add_argument("--repo", type=Path, default=Path("."),
                   help="reviewed repository (for repo field and optional repo_files_total)")
    r.add_argument("--review-id", default=None,
                   help="review id override; defaults to the runs' item_id")
    r.add_argument("--epoch-commit", default=None, help="repository epoch commit for this review")
    r.add_argument("--reviewed-at", default=None,
                   help="ISO-8601 reviewed time (defaults to latest execution completed_at)")
    r.add_argument("--experiment-id", default=None, help="optional experiment_id for provenance")
    r.add_argument("--panel-id", default=None, help="optional panel_id for provenance")
    r.add_argument("--features-frozen-at", default=None,
                   help="explicit ISO-8601 freeze time; defaults to now")

    r.add_argument("--change-type", default=None, choices=REVIEW_CHANGE_TYPES,
                   help="required when not supplied in --meta")
    r.add_argument("--risk-tier", default=None, choices=REVIEW_RISK_TIERS,
                   help="required when not supplied in --meta")
    r.add_argument("--packet-tokens", type=int, default=None,
                   help="required when not supplied in --meta")
    r.add_argument("--changed-surfaces", nargs="+", default=None, choices=REVIEW_CHANGED_SURFACES,
                   help="optional; one or more from review.schema enum")
    r.add_argument("--indicators", nargs="+", default=None, choices=REVIEW_INDICATORS,
                   help="optional; one or more from review.schema enum")
    r.add_argument("--meta", type=Path, default=None,
                   help="JSON metadata with judgement fields, e.g. change_type, risk_tier, packet_tokens")
    r.add_argument("--author-is-agent", dest="author_is_agent", action="store_true",
                   help="whether the review authoring was an agent")
    r.add_argument("--no-author-is-agent", dest="author_is_agent", action="store_false",
                   help="inverse of --author-is-agent")
    r.set_defaults(author_is_agent=None)
    r.set_defaults(fn=cmd_review)

    o = sub.add_parser("outcomes", help="enrich findings from repository history")
    o.add_argument("--findings", type=Path, required=True)
    o.add_argument("--repo", type=Path, required=True)
    o.add_argument("--window-days", type=int, default=30)
    o.add_argument("--out", type=Path, default=DEFAULT_FINDING_PATH)
    o.set_defaults(fn=cmd_outcomes)

    q = sub.add_parser("queue", help="the only findings a person must read")
    q.add_argument("--findings", type=Path, required=True)
    q.add_argument("--waiver-re", default=r"\bwaive\b|\bexception to\b|\bopt out of\b")
    q.add_argument("--out", type=Path, default=Path("human_queue.jsonl"))
    q.set_defaults(fn=cmd_queue)

    a = sub.add_parser("audit", help="lead dispositions vs an independent panel")
    a.add_argument("--findings", type=Path, required=True)
    a.add_argument("--panel", type=Path, default=None, help="judge.jsonl from judge_lrhe.py")
    a.add_argument("--n", type=int, default=60)
    a.add_argument("--out", type=Path, default=Path("audit_sample.jsonl"))
    a.set_defaults(fn=cmd_audit)

    m = sub.add_parser("metrics", help="the eight numbers")
    m.add_argument("--findings", type=Path, required=True)
    m.add_argument("--min-reviews", type=int, default=30)
    m.add_argument("--json", type=Path, default=None)
    m.set_defaults(fn=cmd_metrics)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

