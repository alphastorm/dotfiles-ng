#!/usr/bin/env python3
"""
shadow_ledger.py -- online shadow evaluation from production reviews.

LRHE measures council structure against a fixed public corpus. This measures the
same council against real work, using outcome data that accumulates from reviews
you were doing anyway. No curation, no hand-labeled historical examples.

  ingest    runs.jsonl (+ integrator dispositions) -> findings.jsonl
  outcomes  enrich from repository history: did it cause a change, a test, or a
            later revert; did its anchor even exist at the reviewed epoch
  queue     the only three things a person has to read
  audit     lead dispositions vs an independent cross-family panel, with kappa
  metrics   the eight numbers, with review-clustered bootstrap intervals

WHAT THIS IS NOT. `lead_disposition` is issued by the integrator, and the
integrator is one of the families being compared. That is exactly the
single-family-judge problem LRHE-PROTOCOL.md section 5.2 calls disqualifying, and
it points one way: an integrator confirms findings phrased the way it phrases
things. Everything downstream inherits that, so `audit` exists and its kappa
belongs beside every per-family number printed here. Repository outcomes and
refutation results are the parts nobody can talk out of their verdict; weight them
accordingly.
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
from datetime import datetime, timedelta
from pathlib import Path

import score_lrhe

CRITICAL = (0, 1)
CONFIRMING = ("confirmed",)
UNSUPPORTED = ("unsupported", "falsified")
RNG = random.Random(20260727)


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]


def _write_jsonl(p: Path, rows: list[dict]) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _path_exists_at(repo: Path, commit: str, path: str) -> bool:
    try:
        return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{commit}:{path}"],
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


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
    for run in runs:
        for i, raw in enumerate(run.get("evidence", [])):
            parsed = score_lrhe._parse_evidence_string(raw)
            if parsed["parse_status"] == score_lrhe.PARSE_FAIL:
                unparsed += 1
                continue
            rid = parsed.get("rid") or f"{i:02d}"
            anchors = score_lrhe.extract_anchors(parsed.get("evidence_text", ""))
            d = dispositions.get((run["run_id"], rid), {})
            out.append({
                "finding_id": f"{run['run_id']}|{rid}",
                "review_id": run.get("review_id") or run.get("item_id") or run["run_id"],
                "repo": run.get("repo", ""),
                "epoch_commit": run.get("epoch_commit") or run.get("base_commit", ""),
                "reviewed_at": run.get("reviewed_at", ""),
                "risk_tier": run.get("risk_tier", "critical"),
                "reviewer_family": run.get("family", ""),
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
                "lead_disposition": d.get("lead_disposition", ""),
                "disposition_by": d.get("disposition_by", ""),
                "duplicate_of": d.get("duplicate_of", ""),
                "verification_result": d.get("verification_result", "not_attempted"),
                "refuted_by": d.get("refuted_by", ""),
                "input_tokens": run.get("input_tokens"),
                "output_tokens": run.get("output_tokens"),
                "cost_usd": run.get("cost_usd"),
                "quota_pool": run.get("quota_pool", ""),
            })
    _write_jsonl(args.out, out)
    print(f"runs {len(runs)} -> findings {len(out)}  (unparsed contract strings: {unparsed})")
    print(f"carrying a lead disposition: {sum(1 for f in out if f['lead_disposition'])}")
    print(f"wrote {args.out}")
    return 0


# ---------------------------------------------------------------- outcomes

_TEST_PATH = re.compile(r"(^|/)(tests?|testing|spec)/|(^|/)(test_[^/]+|[^/]+_test)\.[a-z]+$"
                        r"|\.(spec|test)\.[jt]sx?$", re.I)
_REGRESSION_MSG = re.compile(r"\b(revert|hotfix|regression|incident|postmortem|rollback)\b", re.I)


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
                log = _git(repo, "log", f"--since={since}", f"--until={until}",
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
    n_bad = sum(1 for r in rc if r["anchor_valid_at_epoch"] is False)
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
    i.add_argument("--out", type=Path, default=Path("findings.jsonl"))
    i.set_defaults(fn=cmd_ingest)

    o = sub.add_parser("outcomes", help="enrich findings from repository history")
    o.add_argument("--findings", type=Path, required=True)
    o.add_argument("--repo", type=Path, required=True)
    o.add_argument("--window-days", type=int, default=30)
    o.add_argument("--out", type=Path, default=Path("findings.jsonl"))
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
