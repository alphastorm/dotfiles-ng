#!/usr/bin/env python3
"""
judge_lrhe.py -- cross-family adjudication and cold refutation, with the human
reduced to a fixed, small, honest role.

score_lrhe.py consumes judge.jsonl and exec.jsonl. Nothing produced either. The
judge panel in LRHE-PROTOCOL.md section 5.2 was a paragraph, so in practice every
claim got hand-labeled; the REFUTED branch of section 5.3 was reachable only from
container execution. This closes both gaps with the same plumbing.

Two channels, deliberately separate, because they answer different questions:

  ADJUDICATION (`prompts` / `ingest`) -> judge.jsonl
      "Does this claim match a ground-truth defect?"  CONFIRMED / PLAUSIBLE /
      FABRICATED, decided by two families that did not author it.

  COLD REFUTATION (`refute` / `ingest-refutation`) -> exec.jsonl
      "Can this claim be falsified against the code?"  confirmed / falsified /
      unresolved, decided by one family that sees only the normalized claim, its
      evidence, the repository epoch, and the test that would settle it.

The second is what makes execution-free REFUTED possible, and it is where a cheap
fifth family earns its place: it runs per disputed P0/P1 claim, not per review, so
a review with nothing in dispute costs nothing.

The human is not removable. Judge-human agreement (kappa >= 0.70) is a hard gate
in section 8, and a panel calibrated against nothing measures nothing. What IS
removable is labeling the other ~1,950 claims:

  1. Deterministic gates first. score_lrhe.py already settles unparsed claims and
     claims anchored at paths that do not exist. Those never reach a judge.
  2. Two non-authoring families per claim; agreement is the common case.
  3. A third non-authoring family breaks ties -- one call instead of one person,
     and it gets cheaper as the council grows.
  4. Only unresolved splits, plus one fixed 60-claim calibration sample, reach you.

A note on majority. It settles a JUDGING label here, never a review finding. The
deployment topology is explicit that agreement between reviewers is metadata, not
proof; nothing in this file promotes a claim because reviewers agreed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

VERDICTS = ("CONFIRMED", "PLAUSIBLE", "FABRICATED")
VERDICT_TIE_ORDER = {v: i for i, v in enumerate(VERDICTS)}
REFUTE_OUTCOMES = ("confirmed", "falsified", "unresolved")

# Verbatim from SWE-PRBench `dataset/rubric.md` section 1. Reused rather than
# reinvented: the protocol's comparison point is their published baseline, and a
# different rubric would silently make the numbers incomparable.
RUBRIC = """\
CONFIRMED -- all of the following hold:
  1. The comment identifies a specific issue in the code.
  2. A ground-truth label describes the SAME underlying issue (wording may differ).
  3. The issue concerns the same file or functional area.
  4. The concern would lead to the same kind of code change as the label.

PLAUSIBLE:
  1. The comment is grounded in code visible in the provided context.
  2. The observation is factually correct about the code.
  3. A reasonable engineer might raise the concern.
  4. NO ground-truth label describes this specific concern.

FABRICATED -- any of the following holds:
  1. References code, functions or behavior not present in the provided context.
  2. Makes factually incorrect claims about the code.
  3. Describes a bug that does not exist in the shown code.
  4. Invents method signatures, variable names or behavior.
"""

JUDGE_PROMPT = """\
You are adjudicating one code-review comment against the ground truth for a change.
You did not write this comment. Judge it; do not improve it.

{rubric}
Return JSON only:
  {{"verdict": "CONFIRMED"|"PLAUSIBLE"|"FABRICATED",
    "label_id": "<id of the matched ground-truth label, or null>",
    "confidence": 0.0-1.0,
    "rationale": "<one sentence>"}}

PLAUSIBLE is not a failure grade. Human review is not exhaustive, so a correct
observation that no label happens to cover is PLAUSIBLE, never FABRICATED.
Reserve FABRICATED for claims the code in front of you contradicts.

--- CHANGE UNDER REVIEW ---
{goal}

{problem}

--- FILES IN SCOPE ---
{files}

--- DIFF ---
{diff}

--- GROUND-TRUTH LABELS ---
{labels}

--- COMMENT TO ADJUDICATE ---
severity asserted: P{severity}   confidence asserted: {confidence}
claim:    {claim}
evidence: {evidence}
impact:   {impact}
"""

# The cold refuter sees no ground truth and no peer output, by construction. It is
# given the claim, its evidence, the immutable epoch, and the settling test -- and
# nothing that would let it agree by social proof.
REFUTE_PROMPT = """\
Attempt to falsify the finding below. You are not reviewing the change and you are
not looking for additional issues. Trace every relevant execution and state path
and decide whether this specific claim survives.

You have not seen any other reviewer's output, and you should not speculate about
what they concluded. Agreement is not evidence.

Return JSON only:
  {{"outcome": "confirmed"|"falsified"|"unresolved",
    "primary_evidence": "<path:line references that settle it>",
    "verification_procedure": "<a concrete command or test that would decide it>",
    "rationale": "<two sentences at most>"}}

Use `unresolved` honestly. A claim you cannot settle from the evidence provided is
unresolved, not falsified -- reporting it falsified is how a real defect gets
waved through.

--- REPOSITORY EPOCH (immutable) ---
repo:          {repo}
base_commit:   {base_commit}
files in scope:
{files}

--- CLAIM UNDER TEST ---
asserted severity: P{severity}
claim:    {claim}
evidence: {evidence}
impact:   {impact}
proposed verification: {verify}

--- SUPPORTING CONTEXT ---
{diff}
"""


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]


def _write_jsonl(p: Path, rows: list[dict]) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _read_csv(p: Path) -> list[dict]:
    with open(p) as fh:
        return list(csv.DictReader(fh))


def _stable_hash(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


def panel_for(claim_key: str, author: str, families: list[str], size: int) -> list[str]:
    """`size` families that did not author the claim, chosen deterministically.

    Rotating the offset by claim keeps any one family from judging a fixed slice of
    the corpus, which would let its idiosyncrasies load onto specific items instead
    of averaging out.
    """
    eligible = [f for f in families if f != author]
    if not eligible:
        return []
    start = _stable_hash(claim_key) % len(eligible)
    return [eligible[(start + i) % len(eligible)] for i in range(min(size, len(eligible)))]


# Claims the deterministic gates in section 5.1 already settled. A judge call buys
# nothing here: an unparsed claim has no content to judge, and a claim anchored
# outside the item's own file list is FABRICATED by construction.
def _needs_judging(row: dict) -> bool:
    if row.get("parse_status") == "fail":
        return False
    if row.get("verdict") in ("UNPARSED", "REFUTED"):
        return False
    if str(row.get("has_anchor")) == "True" and str(row.get("anchor_paths_exist")) == "False":
        return False
    return True


# ---------------------------------------------------------------- adjudication

def cmd_prompts(args) -> int:
    corpus = {it["item_id"]: it for it in _read_jsonl(args.corpus)}
    runs = {r["run_id"]: r for r in _read_jsonl(args.runs)}
    families = args.families or sorted({r.get("family", "") for r in runs.values()} - {""})

    claims = _read_csv(args.claims)
    eligible = [c for c in claims if _needs_judging(c)]

    out, missing = [], 0
    for c in eligible:
        run, item = runs.get(c["run_id"]), corpus.get(c["item_id"])
        if not run or not item:
            missing += 1
            continue
        key = f"{c['run_id']}|{c['rid']}"
        for jf in panel_for(key, run.get("family", ""), families, args.panel_size):
            out.append({
                "judge_id": f"{key}|{jf}", "run_id": c["run_id"], "claim_rid": c["rid"],
                "item_id": c["item_id"], "author_family": run.get("family", ""),
                "judge_family": jf, "role": "judge", "round": 1,
                "prompt": _render_judge(item, c),
            })

    _write_jsonl(args.out, out)
    skipped = len(claims) - len(eligible)
    print(f"claims {len(claims)} | settled deterministically, no judge needed: {skipped} "
          f"({skipped / max(len(claims), 1):.0%})")
    print(f"judge calls: {len(out)}  ({len(eligible)} claims x panel {args.panel_size})")
    print(f"judge pool: {len(families)} families, {len(families) - 1} eligible per claim")
    if missing:
        print(f"  WARNING: {missing} claims reference an unknown run or item")
    print(f"wrote {args.out}\n")
    print("Respond with one JSON object per prompt into a JSONL file carrying at least")
    print("  {judge_id, verdict, label_id, confidence}")
    print("then run `judge_lrhe.py ingest`.")
    return 0


def _render_judge(item: dict, claim: dict) -> str:
    labels = item.get("labels") or []
    label_txt = "\n".join(
        f"  [{lab['label_id']}] severity P{lab.get('severity')} "
        f"{'/'.join(s['path'] for s in lab.get('sites', []))}: "
        f"{(lab.get('description') or '')[:400]}"
        for lab in labels) or "  (none -- this item has no ground-truth defects)"
    return JUDGE_PROMPT.format(
        rubric=RUBRIC, goal=item.get("goal", ""),
        problem=(item.get("problem_statement") or "")[:4000],
        files="\n".join(f"  {p}" for p in item.get("repo_files", [])) or "  (unspecified)",
        diff=(item.get("design_or_diff") or "")[:60000], labels=label_txt,
        severity=claim.get("severity", ""), confidence=claim.get("confidence", ""),
        claim=claim.get("claim_text", ""), evidence=claim.get("evidence_text", ""),
        impact=claim.get("impact_text", ""))


def _top_with_tiebreak(
    counter: Counter[str], tie_priority: dict[str, int] | None = None
) -> tuple[str, int]:
    """Return a deterministic majority-style winner and frequency from a Counter.

    Counter.most_common() breaks ties by insertion order, and ingest may be replayed
    with response shards concatenated in a different order. Those reorders must never
    change the aggregate outcome, so ties are sorted explicitly with a fixed priority.
    """
    max_count = max(counter.values())
    winners = [v for v, c in counter.items() if c == max_count]
    if tie_priority is None:
        winners.sort(key=str)
    else:
        fallback = len(tie_priority)
        winners.sort(key=lambda v: (tie_priority.get(v, fallback), str(v)))
    return winners[0], max_count


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def cmd_ingest(args) -> int:
    prompts = {p["judge_id"]: p for p in _read_jsonl(args.prompts)}
    responses = _read_jsonl(args.responses)

    by_claim: dict[tuple[str, str], list[dict]] = defaultdict(list)
    judgments: list[dict] = []
    unmatched = 0
    cross_family_claims: set[tuple[str, str]] = set()

    for r in responses:
        p = prompts.get(r.get("judge_id", ""))
        verdict = str(r.get("verdict", "")).strip().upper()
        if not p or verdict not in VERDICTS:
            unmatched += 1
            continue
        key = (str(p["run_id"]), str(p["claim_rid"]))
        judgment = {
            "judge_id": p["judge_id"],
            "run_id": key[0],
            "claim_rid": key[1],
            "item_id": str(p.get("item_id", "")),
            "author_family": str(p.get("author_family", "")).strip(),
            "judge_family": str(r.get("judge_family", p.get("judge_family", ""))).strip(),
            "round": _coerce_int(r.get("round", p.get("round", 1)), 1),
            "verdict": verdict,
            "label_id": r.get("label_id") or "",
            "confidence": _coerce_float(r.get("confidence"), 0.0),
            "rationale": (r.get("rationale") or "")[:400],
        }
        if judgment["judge_family"] == judgment["author_family"]:
            cross_family_claims.add(key)

        by_claim[key].append(judgment)
        judgments.append(judgment)

    # Persist raw per-invocation records first, then aggregate deterministically
    # from those rows (plus the explicit constraint checks below).
    _write_jsonl(args.out_judgments, sorted(
        judgments,
        key=lambda r: (r["run_id"], r["claim_rid"], r["judge_family"], r["round"], r["judge_id"])
    ))

    scorable_by_claim: dict[tuple[str, str], list[dict]] = {}
    for key, rows in by_claim.items():
        if key in cross_family_claims:
            continue
        scorable_by_claim[key] = sorted(
            rows, key=lambda r: (r["round"], r["judge_family"], r["judge_id"], r["verdict"])
        )

    judged, queue, stats = [], [], Counter()
    for (run_id, rid), votes in sorted(scorable_by_claim.items()):
        tally = Counter(v["verdict"] for v in votes)
        top, n_top = _top_with_tiebreak(tally, tie_priority=VERDICT_TIE_ORDER)
        if n_top > len(votes) / 2:
            winners = [v for v in votes if v["verdict"] == top]
            stats["unanimous" if len(tally) == 1 else "majority"] += 1
            verdict, needs_human = top, False
        else:
            # A split with no majority is exactly what the protocol routes to a
            # person. Until they rule, PLAUSIBLE is the only interim verdict that
            # moves no headline number: not credited as a hit, not penalized as a
            # hallucination.
            winners, verdict, needs_human = votes, "PLAUSIBLE", True
            stats["split_to_human"] += 1

        # A verdict the panel agrees on can still hide a disagreement about WHICH
        # defect was found. That is a different error and it corrupts the 1:1
        # matching, so it escalates too.
        label_votes = Counter(v["label_id"] for v in winners if v["label_id"])
        label_id = _top_with_tiebreak(label_votes)[0] if label_votes else ""
        if verdict == "CONFIRMED" and len(label_votes) > 1:
            stats["label_disagreement_to_human"] += 1
            needs_human = True

        rec = {
            "run_id": run_id,
            "claim_rid": rid,
            "verdict": verdict,
            "label_id": label_id,
            "affinity": round(sum(v["confidence"] for v in winners) / max(len(winners), 1), 3),
            "panel": [v["judge_family"] for v in votes],
            "unanimous": len(tally) == 1,
            "needs_human": needs_human,
            "votes": {v["judge_family"]: v["verdict"] for v in votes},
        }
        judged.append(rec)
        if needs_human:
            queue.append({**rec, "rationales": {v["judge_family"]: v["rationale"] for v in votes}})

    _write_jsonl(args.out, judged)
    if args.human_queue:
        _write_jsonl(args.human_queue, queue)

    n = len(judged)
    print(f"adjudicated {n} claims from {len(judgments)} judge invocations")
    for k, v in sorted(stats.items()):
        print(f"  {k:<28} {v:>5}  ({v / max(n, 1):.0%})")
    if unmatched:
        print(f"  {'unmatched/invalid responses':<28} {unmatched:>5}")
    if cross_family_claims:
        print(f"  {'cross-family violations':<28} {len(cross_family_claims):>5}")
        print(f"  Refusing {len(cross_family_claims)} claims with same-family judging")
        print("  Fix responses and re-run to score only safe claims")
    print(f"\nwrote {args.out}")
    print(f"wrote {args.out_judgments}")
    if args.human_queue:
        print(f"human queue: {len(queue)} claims -> {args.human_queue} "
              f"({len(queue) / max(n, 1):.0%})")
    if stats["split_to_human"] and args.tiebreak_out:
        _emit_tiebreak(prompts, scorable_by_claim, args)
    elif stats["split_to_human"]:
        print(f"\n{stats['split_to_human']} claims split with no majority. Pass "
              f"--tiebreak-out to spend one extra non-authoring judge on those claims\n"
              f"instead of your own time; re-ingest the combined responses afterwards.")
    return 1 if cross_family_claims else 0


def _emit_tiebreak(prompts: dict, by_claim: dict, args) -> None:
    """One more non-authoring judge for split claims. A call, not a person."""
    seen = {k: {v["judge_family"] for v in votes} for k, votes in by_claim.items()}
    by_key: dict[tuple[str, str], dict] = {}
    for p in prompts.values():
        by_key.setdefault((p["run_id"], str(p["claim_rid"])), p)

    extra = []
    for key in sorted(by_claim):
        votes = sorted(by_claim[key], key=lambda v: (v["round"], v["judge_family"], v["judge_id"]))
        top, n_top = _top_with_tiebreak(Counter(v["verdict"] for v in votes), tie_priority=VERDICT_TIE_ORDER)
        if n_top > len(votes) / 2:
            continue
        base = by_key.get(key)
        if not base:
            continue
        pool = [f for f in (args.families or [])
                if f != base["author_family"] and f not in seen[key]]
        if not pool:
            continue
        jf = pool[_stable_hash(f"{key[0]}|{key[1]}|tiebreak") % len(pool)]
        extra.append({**base, "judge_id": f"{key[0]}|{key[1]}|{jf}",
                      "judge_family": jf, "round": 2})
    _write_jsonl(args.tiebreak_out, extra)
    print(f"\ntiebreak: {len(extra)} extra judge calls -> {args.tiebreak_out}")
    print("  concatenate their responses with round 1 and re-run ingest.")




# ---------------------------------------------------------------- refutation

def cmd_refute(args) -> int:
    """Emit cold-refutation packets for disputed high-severity claims.

    Selection is the whole point. Refuting everything wastes the family; refuting
    nothing leaves the REFUTED branch unreachable outside S2/S3. A claim qualifies
    when it is consequential (P0/P1) AND unsettled -- the judge panel split, or it
    was promoted without an executable check behind it.
    """
    corpus = {it["item_id"]: it for it in _read_jsonl(args.corpus)}
    claims = _read_csv(args.claims)
    judge = {(j["run_id"], str(j["claim_rid"])): j for j in _read_jsonl(args.judge)} \
        if args.judge else {}

    picked = []
    for c in claims:
        try:
            sev = int(c.get("severity") or 3)
        except ValueError:
            continue
        if sev > args.max_severity or not _needs_judging(c):
            continue
        j = judge.get((c["run_id"], c["rid"]))
        disputed = (j is None) or j.get("needs_human") or not j.get("unanimous")
        if args.all_high_severity or disputed:
            picked.append((c, j))

    out = []
    for c, j in picked:
        item = corpus.get(c["item_id"])
        if not item:
            continue
        out.append({
            "refute_id": f"{c['run_id']}|{c['rid']}|{args.refuter}",
            "run_id": c["run_id"], "claim_rid": c["rid"], "item_id": c["item_id"],
            "family": args.refuter, "role": "refuter", "arm": "R",
            "why": ("panel split" if j and j.get("needs_human") else
                    "not unanimous" if j and not j.get("unanimous") else
                    "unadjudicated" if j is None else "high severity"),
            "prompt": REFUTE_PROMPT.format(
                repo=item.get("repo", "(withheld)"),
                base_commit=item.get("base_commit", "(withheld)"),
                files="\n".join(f"  {p}" for p in item.get("repo_files", [])) or "  (unspecified)",
                severity=c.get("severity", ""), claim=c.get("claim_text", ""),
                evidence=c.get("evidence_text", ""), impact=c.get("impact_text", ""),
                verify=c.get("verify_text", "") or "(none proposed)",
                diff=(item.get("design_or_diff") or "")[:60000]),
        })

    _write_jsonl(args.out, out)
    print(f"claims {len(claims)} | P0-P{args.max_severity} and disputed: {len(out)}")
    print(f"refuter: {args.refuter}  ({len(out)} calls, ~{len(out) / max(len(claims), 1):.0%} "
          f"of all claims)")
    print(f"wrote {args.out}\n")
    print("Respond with one JSON object per prompt carrying at least")
    print("  {refute_id, outcome, primary_evidence, verification_procedure}")
    print("then run `judge_lrhe.py ingest-refutation`.")
    return 0


def cmd_ingest_refutation(args) -> int:
    """Fold refutation outcomes into exec.jsonl, which drives the REFUTED branch.

    Section 5.3 puts REFUTED above every judge verdict: a claim three families
    raised at conf=0.95 is REFUTED if the check comes back clean. That precedence is
    the mechanism the whole design rests on, so only `falsified` writes a refutation
    -- `unresolved` deliberately writes nothing rather than resolving by silence.
    """
    prompts = {p["refute_id"]: p for p in _read_jsonl(args.prompts)}
    rows, stats, unmatched = [], Counter(), 0
    for r in _read_jsonl(args.responses):
        p = prompts.get(r.get("refute_id", ""))
        outcome = str(r.get("outcome", "")).strip().lower()
        if not p or outcome not in REFUTE_OUTCOMES:
            unmatched += 1
            continue
        stats[outcome] += 1
        if outcome == "unresolved":
            continue
        rows.append({
            "run_id": p["run_id"], "claim_rid": p["claim_rid"],
            "ran": True,
            # exec_reproduced False == the predicted failure did not occur == REFUTED.
            "reproduced": outcome == "confirmed",
            "cmd": (r.get("verification_procedure") or "")[:400],
            "refuter_family": p["family"],
            "primary_evidence": (r.get("primary_evidence") or "")[:400],
            "exit_code": 0,
        })
    _write_jsonl(args.out, rows)
    total = sum(stats.values())
    print(f"refutations: {total}")
    for k in REFUTE_OUTCOMES:
        print(f"  {k:<12} {stats[k]:>5}  ({stats[k] / max(total, 1):.0%})")
    if unmatched:
        print(f"  unmatched  {unmatched}")
    print(f"\nwrote {len(rows)} exec records -> {args.out}")
    print("`unresolved` writes nothing on purpose: an unsettled claim must not be")
    print("resolved by silence in either direction.")
    if stats["unresolved"]:
        print(f"\n{stats['unresolved']} unresolved P0/P1 claims are what you actually have to read.")
    return 0


# ---------------------------------------------------------------- calibration

def cmd_calibrate(args) -> int:
    """Sample claims for blind hand-labeling, stratified across strata and verdicts.

    Section 8 gates on kappa >= 0.70 against hand labels. The sample must span
    verdicts, or kappa is estimated on whatever the panel emitted most of and says
    nothing about the categories carrying the decision.
    """
    judge = {(j["run_id"], str(j["claim_rid"])): j for j in _read_jsonl(args.judge)}
    pool = []
    for c in _read_csv(args.claims):
        j = judge.get((c["run_id"], c["rid"]))
        if j:
            pool.append({**c, "_verdict": j["verdict"], "_stratum": c.get("item_id", "")[:2]})

    cells = defaultdict(list)
    for r in pool:
        cells[(r["_stratum"], r["_verdict"])].append(r)
    rng = random.Random(args.seed)
    for v in cells.values():
        rng.shuffle(v)

    picked, keys = [], sorted(cells)
    while len(picked) < args.n and any(cells[k] for k in keys):
        for k in keys:
            if len(picked) >= args.n:
                break
            if cells[k]:
                picked.append(cells[k].pop())

    fields = ["run_id", "claim_rid", "item_id", "stratum", "claim_text", "evidence_text",
              "human_verdict", "human_label_id"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        # The panel's answer is withheld on purpose: seeing it first is what makes
        # an agreement statistic meaningless.
        w.writerows({"run_id": r["run_id"], "claim_rid": r["rid"], "item_id": r["item_id"],
                     "stratum": r["_stratum"], "claim_text": r.get("claim_text", ""),
                     "evidence_text": r.get("evidence_text", ""),
                     "human_verdict": "", "human_label_id": ""} for r in picked)
    print(f"wrote {len(picked)} claims for blind hand-labeling -> {args.out}")
    print(f"cells sampled: {len({(r['_stratum'], r['_verdict']) for r in picked})} "
          f"of {len(cells)} (stratum x verdict)")
    print("Fill `human_verdict` with CONFIRMED / PLAUSIBLE / FABRICATED, then:")
    print(f"  judge_lrhe.py kappa --calibration {args.out} --judge {args.judge}")
    return 0


def cohens_kappa(a: list[str], b: list[str]) -> tuple[float, float]:
    """(kappa, raw agreement) for two label sequences."""
    n = len(a)
    if not n:
        return float("nan"), float("nan")
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return ((po - pe) / (1 - pe) if pe < 1 else 1.0), po


def cmd_kappa(args) -> int:
    judge = {(j["run_id"], str(j["claim_rid"])): j for j in _read_jsonl(args.judge)}
    rows = [r for r in _read_csv(args.calibration) if (r.get("human_verdict") or "").strip()]
    if not rows:
        print("no labeled rows in the calibration file", file=sys.stderr)
        return 2

    pairs = [(r["human_verdict"].strip().upper(),
              judge[(r["run_id"], str(r["claim_rid"]))]["verdict"])
             for r in rows if (r["run_id"], str(r["claim_rid"])) in judge]
    if not pairs:
        print("no labeled row matched a judge record", file=sys.stderr)
        return 2
    human, panel = [p[0] for p in pairs], [p[1] for p in pairs]
    k, po = cohens_kappa(human, panel)

    print(f"calibration pairs: {len(pairs)}")
    print(f"raw agreement    : {po:.3f}")
    print(f"Cohen's kappa    : {k:.3f}")
    print(f"\nGATE (section 8): kappa >= 0.70 -> {'PASS' if k >= 0.70 else 'FAIL'}")
    if k < 0.70:
        print("At this kappa the headline numbers are judge noise. SWE-PRBench reported\n"
              "0.75 against its rubric and 0.616 across judges, so 0.70 is achievable --\n"
              "but no amount of bootstrapping repairs a panel that misses it.")
    cats = sorted(set(human) | set(panel))
    print("\nconfusion (rows human, cols panel):")
    print("            " + "".join(f"{c[:9]:>11}" for c in cats))
    for h in cats:
        print(f"  {h[:10]:<10}" + "".join(
            f"{sum(1 for x, y in pairs if x == h and y == c):>11}" for c in cats))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prompts", help="emit cross-family judging tasks")
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--runs", type=Path, required=True)
    p.add_argument("--claims", type=Path, required=True, help="claims.csv from score_lrhe.py")
    p.add_argument("--families", nargs="*", default=None,
                   help="judge pool; defaults to every family seen in --runs")
    p.add_argument("--panel-size", type=int, default=2)
    p.add_argument("--out", type=Path, default=Path("judge_prompts.jsonl"))
    p.set_defaults(fn=cmd_prompts)

    i = sub.add_parser("ingest", help="aggregate panel votes into judge.jsonl")
    i.add_argument("--prompts", type=Path, required=True)
    i.add_argument("--responses", type=Path, required=True)
    i.add_argument("--out", type=Path, default=Path("judge.jsonl"))
    i.add_argument("--human-queue", type=Path, default=Path("human_queue.jsonl"))
    i.add_argument("--out-judgments", type=Path, default=Path("judgments.jsonl"),
                   help="raw per-judge records; one row per invocation")
    i.add_argument("--tiebreak-out", type=Path, default=None,
                   help="emit one extra non-authoring judge for split claims")
    i.add_argument("--families", nargs="*", default=None, help="pool for tiebreak selection")
    i.set_defaults(fn=cmd_ingest)

    r = sub.add_parser("refute", help="cold-refutation packets for disputed P0/P1 claims")
    r.add_argument("--corpus", type=Path, required=True)
    r.add_argument("--claims", type=Path, required=True)
    r.add_argument("--judge", type=Path, default=None,
                   help="judge.jsonl; without it every high-severity claim counts as disputed")
    r.add_argument("--refuter", default="glm", help="family running the cold-refuter role")
    r.add_argument("--max-severity", type=int, default=1, help="P0/P1 by default")
    r.add_argument("--all-high-severity", action="store_true",
                   help="refute every P0/P1 claim, not only the disputed ones (critical+)")
    r.add_argument("--out", type=Path, default=Path("refute_prompts.jsonl"))
    r.set_defaults(fn=cmd_refute)

    ir = sub.add_parser("ingest-refutation", help="fold refutations into exec.jsonl")
    ir.add_argument("--prompts", type=Path, required=True)
    ir.add_argument("--responses", type=Path, required=True)
    ir.add_argument("--out", type=Path, default=Path("exec.jsonl"))
    ir.set_defaults(fn=cmd_ingest_refutation)

    c = sub.add_parser("calibrate", help="sample claims for blind hand-labeling")
    c.add_argument("--claims", type=Path, required=True)
    c.add_argument("--judge", type=Path, required=True)
    c.add_argument("--n", type=int, default=60)
    c.add_argument("--seed", type=int, default=20260726)
    c.add_argument("--out", type=Path, default=Path("calibration_queue.csv"))
    c.set_defaults(fn=cmd_calibrate)

    k = sub.add_parser("kappa", help="judge-human agreement against the section 8 gate")
    k.add_argument("--calibration", type=Path, required=True)
    k.add_argument("--judge", type=Path, required=True)
    k.set_defaults(fn=cmd_kappa)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
