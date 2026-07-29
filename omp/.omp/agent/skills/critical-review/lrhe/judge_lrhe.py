#!/usr/bin/env python3
"""
judge_lrhe.py -- cross-family adjudication and cold refutation, with the human
reduced to a fixed, small, honest role.

score_lrhe.py consumes judge.jsonl and separately supplied execution evidence. The
judge panel in LRHE-PROTOCOL.md section 5.2 was a paragraph, so in practice every
claim got hand-labeled. This closes that adjudication gap without weakening the
REFUTED branch's requirement for a command that actually ran.

Two channels, deliberately separate, because they answer different questions:

  ADJUDICATION (`prompts` / `ingest`) -> judge.jsonl
      "Does this claim match a ground-truth defect?"  CONFIRMED / PLAUSIBLE /
      FABRICATED, decided by two families that did not author it.

  COLD REFUTATION (`refute` / `ingest-refutation`) -> refuter-opinions.jsonl
      "Can this claim be falsified against the code?"  confirmed / falsified /
      unresolved, decided by one family that sees only the normalized claim, its
      evidence, the repository epoch, and the test that would settle it.

Cold-refuter output is diagnostic model opinion, never execution evidence. The
REFUTED branch is reachable only from a schema-valid record of a command that
actually ran.

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

import yaml

# The adjudication prompt lives in judge_render so `auto_reliability.py` renders the
# same bytes for the same claim. Re-exported here because this module's callers and
# tests have always read RUBRIC/JUDGE_PROMPT off it.
from judge_render import JUDGE_PROMPT, RUBRIC, render_judge

VERDICTS = ("CONFIRMED", "PLAUSIBLE", "FABRICATED")
VERDICT_TIE_ORDER = {v: i for i, v in enumerate(VERDICTS)}
REFUTE_OUTCOMES = ("confirmed", "falsified", "unresolved")

__all__ = ["JUDGE_PROMPT", "RUBRIC"]

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


class PanelUnfillable(Exception):
    """No assignment exists that satisfies the independence constraint."""


def panel_for(claim_key: str, author: str, families: list[str], size: int) -> list[str]:
    """`size` DISTINCT families, none of them the claim's author, chosen deterministically.

    Rotating the offset by claim keeps any one family from judging a fixed slice of
    the corpus, which would let its idiosyncrasies load onto specific items instead
    of averaging out.

    Refuses rather than under-fills. This used to `return []` when the author was the
    only family and `min(size, len(eligible))` otherwise, so a mis-specified pool
    produced a claim with no judges, or one judge where the protocol requires two, and
    said nothing -- the claim simply never appeared in the output file. `cmd_ingest`
    already refuses a judgement whose family authored the claim; the assignment side had
    no equivalent, which is the same asymmetry that let the reviewer tool surface be
    enforced in prose and measured nowhere.
    """
    eligible = [f for f in families if f != author]
    if len(eligible) < size:
        raise PanelUnfillable(
            f"claim {claim_key} authored by {author!r} needs {size} distinct non-authoring "
            f"families and the pool {sorted(families)} offers {len(eligible)}: "
            f"{sorted(eligible)}. Two judges that are the same family are one judge, and "
            f"one judge cannot be a majority of two.")
    start = _stable_hash(claim_key) % len(eligible)
    return [eligible[(start + i) % len(eligible)] for i in range(size)]


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
AGENTS = Path.home() / ".omp/agent/agents"


def declared_model(family: str) -> str | None:
    """The selector `judge-<family>.md` declares, or None when unreadable.

    Reviewer runs carry `identity_verified`; judgements carried nothing, so a silent
    provider fallback would have left every judgement attributed to a family that never
    answered and no way to notice afterwards. `judge_family` comes from the PROMPT, which
    is the request, not the answer.
    """
    definition = AGENTS / f"judge-{family}.md"
    if not family or not definition.is_file():
        return None
    text = definition.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    models = (yaml.safe_load(text.split("---", 2)[1]) or {}).get("model") or []
    return str(models[0]) if models else None


def identity_of(family: str, served: str | None,
                expect: dict[str, str] | None = None) -> bool | None:
    """Did the family we asked for answer? None when the question is unanswerable.

    The expected selector comes from `--expect family=selector` when given, and from
    `judge-<family>.md` otherwise. Explicit wins because the agent definitions are stowed
    from the PRIVATE package: deriving the expectation from them only would make this
    check pass locally and abstain everywhere else, which is the same environment
    dependence that made the judge-agent test fail the public build.

    Compared on the provider/model prefix: the definition pins
    `anthropic/claude-opus-5:max` and the session record reports
    `anthropic/claude-opus-5`, so the thinking-level suffix is not part of the identity.
    """
    want = (expect or {}).get(family) or declared_model(family)
    if want is None or not served:
        return None
    return str(served).split(":")[0] == want.split(":")[0]


def cmd_prompts(args) -> int:
    corpus = {it["item_id"]: it for it in _read_jsonl(args.corpus)}
    runs = {r["run_id"]: r for r in _read_jsonl(args.runs)}
    families = args.families or sorted({r.get("family", "") for r in runs.values()} - {""})

    claims = _read_csv(args.claims)
    eligible = [c for c in claims if _needs_judging(c)]

    out, missing, unfillable = [], 0, []
    for c in eligible:
        run, item = runs.get(c["run_id"]), corpus.get(c["item_id"])
        if not run or not item:
            missing += 1
            continue
        key = f"{c['run_id']}|{c['rid']}"
        try:
            panel = panel_for(key, run.get("family", ""), families, args.panel_size)
        except PanelUnfillable as exc:
            unfillable.append(str(exc))
            continue
        for jf in panel:
            out.append({
                "judge_id": f"{key}|{jf}", "run_id": c["run_id"], "claim_rid": c["rid"],
                "item_id": c["item_id"], "author_family": run.get("family", ""),
                "judge_family": jf, "role": "judge", "round": 1,
                "prompt": _render_judge(item, c),
            })

    # Nothing is written when any claim cannot be assigned. A partial file would be
    # dispatched, ingested, and scored, and the claims missing from it would read as
    # claims nobody happened to dispute rather than claims nobody was allowed to judge.
    if unfillable:
        print(f"REFUSED: {len(unfillable)} of {len(eligible)} eligible claims cannot be "
              f"assigned {args.panel_size} independent judges. No prompts written.",
              file=sys.stderr)
        for line in unfillable[:3]:
            print(f"  {line}", file=sys.stderr)
        if len(unfillable) > 3:
            print(f"  ... and {len(unfillable) - 3} more", file=sys.stderr)
        return 2

    _write_jsonl(args.out, out)
    skipped = len(claims) - len(eligible)
    print(f"claims {len(claims)} | settled deterministically, no judge needed: {skipped} "
          f"({skipped / max(len(claims), 1):.0%})")
    print(f"judge calls: {len(out)}  ({len(eligible)} claims x panel {args.panel_size})")
    print(f"judge pool: {len(families)} families, {args.panel_size} assigned per claim")
    if missing:
        print(f"  WARNING: {missing} claims reference an unknown run or item")
    print(f"wrote {args.out}\n")
    print("Respond with one JSON object per prompt into a JSONL file carrying at least")
    print("  {judge_id, verdict, label_id, confidence}")
    print("then run `judge_lrhe.py ingest`.")
    return 0


# The canonical panel's prompt IS the audit's prompt; see judge_render for why the
# implementation is not allowed to live in two places.
_render_judge = render_judge


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
    expect = dict(pair.split("=", 1) for pair in (args.expect or []))
    prompts = {p["judge_id"]: p for p in _read_jsonl(args.prompts)}
    responses = _read_jsonl(args.responses)

    by_claim: dict[tuple[str, str], list[dict]] = defaultdict(list)
    judgments: list[dict] = []
    unmatched = 0
    cross_family_claims: set[tuple[str, str]] = set()
    identity_failed: set[tuple[str, str]] = set()

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
            # The answer, not the request. `judge_family` above is what we asked for.
            "served_model": r.get("served_model") or "",
            "identity_verified": identity_of(
                str(r.get("judge_family", p.get("judge_family", ""))).strip(),
                r.get("served_model"), expect),
        }
        if judgment["judge_family"] == judgment["author_family"]:
            cross_family_claims.add(key)
        # A judgement from a model nobody requested is not that family's judgement, and
        # `identity_verified: None` means the question could not be answered at all.
        # Neither is scorable, and both drop the whole claim rather than half its panel.
        if judgment["identity_verified"] is not True:
            identity_failed.add(key)

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
        if key in cross_family_claims or key in identity_failed:
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
    if identity_failed:
        print(f"  {'unverified judge identity':<28} {len(identity_failed):>5}")
        print(f"  Refusing {len(identity_failed)} claims: a judgement from a model nobody")
        print("  requested is not that family's judgement, and an unverifiable one is not")
        print("  a judgement at all. Pass served_model on every reply, harvested from the")
        print("  session record rather than the answer.")
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
    return 1 if (cross_family_claims or identity_failed) else 0


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
    print("then run `judge_lrhe.py ingest-refutation` to record diagnostic opinions.")
    return 0


def cmd_ingest_refutation(args) -> int:
    """Record cold-refutation outcomes as diagnostic model opinions.

    Section 5.3 puts REFUTED above every judge verdict. Treating a model's textual
    answer as though its proposed verification had run let one opinion force that
    verdict without execution. This command avoids that precedence hazard by
    emitting no execution fields; only runner-attested evidence may reach the
    REFUTED branch. Unresolved opinions remain useful diagnostics and are retained.
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
        rows.append({
            "run_id": p["run_id"],
            "claim_rid": p["claim_rid"],
            "kind": "model_opinion",
            "outcome": outcome,
            "refuter_family": p["family"],
            "primary_evidence": (r.get("primary_evidence") or "")[:400],
            "proposed_verification": (r.get("verification_procedure") or "")[:400],
            "rationale": (r.get("rationale") or "")[:400],
        })
    _write_jsonl(args.out, rows)
    total = sum(stats.values())
    print(f"refutations: {total}")
    for k in REFUTE_OUTCOMES:
        print(f"  {k:<12} {stats[k]:>5}  ({stats[k] / max(total, 1):.0%})")
    if unmatched:
        print(f"  unmatched  {unmatched}")
    print(f"\nwrote {len(rows)} refuter opinions -> {args.out}")
    print("These are diagnostic opinions, not execution evidence.")
    print("`score_lrhe.py --exec` will refuse them.")
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
    print(f"  judge_lrhe.py kappa --calibration {args.out} \\")
    print("      --judge <one row per claim, e.g. judge-floor-agg.jsonl> \\")
    print("      --corpus <corpus.jsonl>")
    print("`--corpus` is required for this packet format, and `--judge` must be an")
    print("aggregated file: a per-judge file holds several rows per claim and only one")
    print("of them would be compared. `kappa` rejects duplicate keys rather than guess.")
    print("The packet's run_id must be the one the judge file carries -- ingest assigns")
    print("its own, so a packet built from prompt-stage ids will not join.")
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


GATE_KIND = "case"
SUPPLEMENT_KIND = "case_supplement"


def load_judge_index(path) -> tuple[dict[tuple[str, str], dict], list[str]]:
    """Index an aggregated judge file by claim, or say why it cannot be used.

    A per-judge file carries one row per reviewer per claim. Indexing it by claim
    silently kept whichever row came last, so the rater was scored against an
    arbitrary panel member -- that produced a number rather than an error, which is
    the worse failure. Duplicates are refused instead.
    """
    judge: dict[tuple[str, str], dict] = {}
    duplicates: dict[tuple[str, str], int] = {}
    for row in _read_jsonl(path):
        if "run_id" not in row or "claim_rid" not in row:
            continue
        key = (str(row["run_id"]).strip(), str(row["claim_rid"]).strip())
        if key in judge:
            duplicates[key] = duplicates.get(key, 1) + 1
            continue
        judge[key] = row

    violations: list[str] = []
    if not judge:
        violations.append(
            f"{path} yielded no usable judge rows: every record is missing run_id or "
            "claim_rid. A raw judge-responses file is keyed by judge_id, not by claim "
            "-- pass an ingested or aggregated file.")
    if duplicates:
        sample = ", ".join(f"{r}+{c}" for r, c in sorted(duplicates)[:3])
        violations.append(
            f"{path} holds several rows for {len(duplicates)} claim(s) (up to "
            f"{max(duplicates.values())}); e.g. {sample}. That is a per-judge file, and "
            "scoring one rater against one arbitrary panel member is not the gate. Pass "
            "the aggregated file with one row per claim.")
    return judge, violations


def blinded_calibration_pairs(packet, case_map, judge, *, expect_gating: int = 60):
    """(gate pairs, supplemental rows, violations) for a blinded case-id packet.

    The one loader behind both `judge_lrhe.py kappa` and preflight, so the CLI and the
    status line cannot disagree about what the gate measured.

    Opaque `case_id` is resolved through the private case map rather than joined on
    `run_id`: that suffix was a function of when ingest ran and is a digest of the reply
    now, so the frozen selection's keys match nothing in a current judge file.

    `kind` decides membership. `case` rows are the preregistered gate and every one of
    them must be labelled. `case_supplement` rows may sit in the frozen packet but are
    reported separately and never enter kappa -- the gate is the frozen 60, and widening
    it to 65 would quietly restate a preregistered number. `control` rows are planted
    and are refused outright.
    """
    cases: dict[str, dict] = {}
    duplicate_cases: set[str] = set()
    for case in _read_jsonl(case_map):
        case_id = str(case.get("case_id", "")).strip()
        if not case_id:
            continue
        if case_id in cases:
            duplicate_cases.add(case_id)
        else:
            cases[case_id] = case

    violations: list[str] = []
    compared: list[dict] = []
    supplemental: list[dict] = []
    seen_case_ids: dict[str, int] = {}
    seen_judge_keys: dict[tuple[str, str], int] = {}

    for line_number, row in enumerate(_read_csv(packet), start=2):
        case_id = str(row.get("case_id") or "").strip()
        verdict = str(row.get("human_verdict") or "").strip().upper()
        label = str(row.get("human_label_id") or "").strip()
        where = f"CSV row {line_number}"

        if not case_id:
            violations.append(f"{where}: missing case_id")
            continue
        if case_id in seen_case_ids:
            violations.append(
                f"{where}: duplicate case_id {case_id!r} (first seen on row "
                f"{seen_case_ids[case_id]})")
            continue
        seen_case_ids[case_id] = line_number
        if case_id in duplicate_cases:
            violations.append(f"{where}: case_id {case_id!r} is duplicated in the case map")
            continue
        case = cases.get(case_id)
        if case is None:
            violations.append(f"{where}: unknown case_id {case_id!r}")
            continue

        kind = str(case.get("kind") or "").strip()
        if kind == SUPPLEMENT_KIND:
            supplemental.append({"case_id": case_id, "human_verdict": verdict,
                                 "human_label_id": label})
            if verdict and verdict not in VERDICTS:
                violations.append(
                    f"{where}: invalid human_verdict {verdict!r}; expected one of "
                    f"{', '.join(VERDICTS)}")
            continue
        if kind != GATE_KIND:
            violations.append(
                f"{where}: case_id {case_id!r} maps to kind {kind!r}, which is not part "
                f"of the human gate (expected {GATE_KIND!r})")
            continue

        if not verdict:
            violations.append(f"{where}: human_verdict is empty")
        elif verdict not in VERDICTS:
            violations.append(
                f"{where}: invalid human_verdict {verdict!r}; expected one of "
                f"{', '.join(VERDICTS)}")

        label_set = None
        raw_labels = case.get("label_ids")
        if isinstance(raw_labels, list):
            label_set = {str(x).strip() for x in raw_labels if str(x).strip()}
        if verdict == "CONFIRMED":
            if not label:
                violations.append(f"{where}: CONFIRMED requires human_label_id")
            elif label_set is None:
                violations.append(
                    f"{where}: no item label set is available to validate "
                    f"human_label_id {label!r}")
            elif label not in label_set:
                violations.append(
                    f"{where}: human_label_id {label!r} is not valid for case {case_id!r}")
        elif verdict in ("PLAUSIBLE", "FABRICATED") and label:
            violations.append(f"{where}: {verdict} requires an empty human_label_id")

        run_id = str(case.get("run_id") or "").strip()
        claim_rid = str(case.get("claim_rid") or "").strip()
        if not run_id or not claim_rid:
            violations.append(
                f"{where}: case_id {case_id!r} has no complete run_id + claim_rid mapping")
            continue
        judge_key = (run_id, claim_rid)
        if judge_key in seen_judge_keys:
            violations.append(
                f"{where}: duplicate resolved judge key {run_id!r} + {claim_rid!r} "
                f"(first seen on row {seen_judge_keys[judge_key]})")
            continue
        seen_judge_keys[judge_key] = line_number
        judge_row = judge.get(judge_key)
        if judge_row is None:
            violations.append(
                f"{where}: no judge record for {run_id!r} + {claim_rid!r}")
            continue
        if verdict in VERDICTS:
            compared.append({
                "case_id": case_id,
                "human_verdict": verdict,
                "human_label_id": label,
                "panel_verdict": str(judge_row.get("verdict") or "").strip().upper(),
                "panel_label_id": str(judge_row.get("label_id") or "").strip(),
            })

    gating_seen = len(compared) + sum(
        1 for cid in seen_case_ids
        if str((cases.get(cid) or {}).get("kind") or "").strip() == GATE_KIND
        and cid not in {row["case_id"] for row in compared})
    if gating_seen != expect_gating:
        violations.append(
            f"the gate is the frozen {expect_gating} {GATE_KIND} rows, found {gating_seen}")
    return compared, supplemental, violations


def calibration_agreement(compared: list[dict]) -> dict:
    """Every figure the section 8 gate reports, from one list of paired judgements."""
    verdict_kappa, verdict_raw = cohens_kappa(
        [r["human_verdict"] for r in compared], [r["panel_verdict"] for r in compared])
    both = [r for r in compared if r["human_verdict"] == r["panel_verdict"] == "CONFIRMED"]
    human_labels = [r["human_label_id"] for r in both]
    panel_labels = [r["panel_label_id"] for r in both]
    label_kappa, label_raw = cohens_kappa(human_labels, panel_labels) if both \
        else (float("nan"), float("nan"))
    composite_kappa, composite_raw = cohens_kappa(
        [f"CONFIRMED:{r['human_label_id']}" if r["human_verdict"] == "CONFIRMED"
         else r["human_verdict"] for r in compared],
        [f"CONFIRMED:{r['panel_label_id']}" if r["panel_verdict"] == "CONFIRMED"
         else r["panel_verdict"] for r in compared])
    return {"n": len(compared), "verdict_kappa": verdict_kappa, "verdict_raw": verdict_raw,
            "both_confirmed": len(both), "label_kappa": label_kappa, "label_raw": label_raw,
            "label_single_category": bool(both) and
            len(set(human_labels) | set(panel_labels)) == 1,
            "composite_kappa": composite_kappa, "composite_raw": composite_raw}


def cmd_kappa(args) -> int:
    judge, violations = load_judge_index(args.judge)
    supplemental: list[dict] = []

    if args.case_map is not None:
        # Blinded case-id packet: one loader, shared with preflight.
        compared, supplemental, packet_violations = blinded_calibration_pairs(
            args.calibration, args.case_map, judge, expect_gating=args.expect_rows)
        violations.extend(packet_violations)
        return _report_calibration(compared, supplemental, violations)

    rows = _read_csv(args.calibration)
    if len(rows) != args.expect_rows:
        violations.append(
            f"expected exactly {args.expect_rows} rows, found {len(rows)}")

    corpus_labels: dict[str, set[str]] = {}
    if args.corpus is not None:
        for item in _read_jsonl(args.corpus):
            item_id = str(item.get("item_id", "")).strip()
            corpus_labels[item_id] = {
                str(label.get("label_id", "")).strip()
                for label in item.get("labels", [])
                if str(label.get("label_id", "")).strip()
            }
    else:
        violations.append(
            "--corpus is required for legacy run_id + claim_rid packets")

    # The frozen 60-claim selection carries the retired timestamp suffix on every
    # run_id, so an exact join against a current judge file matches nothing --
    # `auto_reliability._run_stem` documents the change. That module already owns the
    # sound resolution (exact first, then a stem that identifies exactly one run,
    # refusing an ambiguous stem rather than guessing), so reuse it instead of
    # rewriting the packet: those ids are also the audit's selection provenance.
    selection_keys = [
        (str(row.get("run_id") or "").strip(), str(row.get("claim_rid") or "").strip())
        for row in rows
        if str(row.get("run_id") or "").strip() and str(row.get("claim_rid") or "").strip()
    ]
    resolved_keys: dict[tuple[str, str], tuple[str, str]] = {}
    if selection_keys and judge:
        import auto_reliability
        resolved_keys, stem_problems = auto_reliability.resolve_selection(
            selection_keys, judge)
        violations.extend(f"selection key: {problem}" for problem in stem_problems)

    seen_packet_keys: dict[tuple[str, ...], int] = {}
    seen_judge_keys: dict[tuple[str, str], int] = {}
    compared: list[dict] = []
    for line_number, row in enumerate(rows, start=2):
        verdict = str(row.get("human_verdict") or "").strip().upper()
        human_label = str(row.get("human_label_id") or "").strip()
        if not verdict:
            violations.append(f"CSV row {line_number}: human_verdict is empty")
        elif verdict not in VERDICTS:
            violations.append(
                f"CSV row {line_number}: invalid human_verdict {verdict!r}; "
                f"expected one of {', '.join(VERDICTS)}")

        judge_key: tuple[str, str] | None = None
        run_id = str(row.get("run_id") or "").strip()
        claim_rid = str(row.get("claim_rid") or "").strip()
        if not run_id or not claim_rid:
            violations.append(
                f"CSV row {line_number}: missing run_id + claim_rid key")
        else:
            packet_key = ("run_id+claim_rid", run_id, claim_rid)
            if packet_key in seen_packet_keys:
                violations.append(
                    f"CSV row {line_number}: duplicate run_id + claim_rid "
                    f"{run_id!r} + {claim_rid!r} "
                    f"(first seen on row {seen_packet_keys[packet_key]})")
            else:
                seen_packet_keys[packet_key] = line_number
            judge_key = (run_id, claim_rid)
        item_id = str(row.get("item_id") or "").strip()
        label_set = corpus_labels.get(item_id) if item_id else None

        # Resolve before the duplicate check: two packet rows that land on one claim
        # are one calibration observation however their ids were spelled.
        if judge_key is not None:
            judge_key = resolved_keys.get(judge_key, judge_key)

        judge_row = None
        if judge_key is not None:
            if judge_key in seen_judge_keys:
                first = seen_judge_keys[judge_key]
                if first != line_number:
                    violations.append(
                        f"CSV row {line_number}: duplicate resolved judge key "
                        f"{judge_key[0]!r} + {judge_key[1]!r} "
                        f"(first seen on row {first})")
            else:
                seen_judge_keys[judge_key] = line_number
            judge_row = judge.get(judge_key)
            if judge_row is None:
                violations.append(
                    f"CSV row {line_number}: no judge record for "
                    f"{judge_key[0]!r} + {judge_key[1]!r}")

        if verdict == "CONFIRMED":
            if not human_label:
                violations.append(
                    f"CSV row {line_number}: CONFIRMED requires human_label_id")
            elif label_set is None:
                violations.append(
                    f"CSV row {line_number}: no item label set is available to "
                    f"validate human_label_id {human_label!r}")
            elif human_label not in label_set:
                violations.append(
                    f"CSV row {line_number}: human_label_id {human_label!r} is "
                    f"not valid for item {item_id!r}")
        elif verdict in ("PLAUSIBLE", "FABRICATED") and human_label:
            violations.append(
                f"CSV row {line_number}: {verdict} requires an empty "
                "human_label_id")

        if judge_row is not None and verdict in VERDICTS:
            compared.append({
                "case_id": "",
                "human_verdict": verdict,
                "human_label_id": human_label,
                "panel_verdict": str(judge_row.get("verdict") or "").strip().upper(),
                "panel_label_id": str(judge_row.get("label_id") or "").strip(),
            })

    return _report_calibration(compared, supplemental, violations)


def _report_calibration(compared: list[dict], supplemental: list[dict],
                        violations: list[str]) -> int:
    """Print the section 8 figures, or the reasons the packet cannot produce them."""
    if violations:
        print(f"invalid calibration packet ({len(violations)} violation(s)):", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 2

    m = calibration_agreement(compared)
    human = [row["human_verdict"] for row in compared]
    panel = [row["panel_verdict"] for row in compared]

    print(f"calibration pairs: {m['n']}")
    if supplemental:
        labelled = sum(1 for row in supplemental if row["human_verdict"])
        print(f"supplemental cases (not in the gate): {len(supplemental)} "
              f"({labelled} labelled)")
    print("\nverdict agreement:")
    print(f"  raw agreement : {m['verdict_raw']:.3f}")
    print(f"  Cohen's kappa : {m['verdict_kappa']:.3f}")

    print("\nexact matched-label agreement conditional on CONFIRMED:")
    print(f"  n             : {m['both_confirmed']}")
    if not m["both_confirmed"]:
        print("  raw agreement : n/a (no row was CONFIRMED by both raters)")
        print("  Cohen's kappa : n/a (no row was CONFIRMED by both raters)")
    else:
        print(f"  raw agreement : {m['label_raw']:.3f}")
        if m["label_single_category"]:
            print("  Cohen's kappa : n/a (only one label category was observed)")
        else:
            print(f"  Cohen's kappa : {m['label_kappa']:.3f}")

    print(f"\nGATE (section 8): kappa >= 0.70 -> "
          f"{'PASS' if m['verdict_kappa'] >= 0.70 else 'FAIL'}")
    print(f"composite kappa: {m['composite_kappa']:.3f} "
          f"(raw agreement {m['composite_raw']:.3f})")
    print("A composite below the verdict figure means the panel and rater are "
          "matching different defects.")
    if m["verdict_kappa"] < 0.70:
        print("At this kappa the headline numbers are judge noise. SWE-PRBench reported\n"
              "0.75 against its rubric and 0.616 across judges, so 0.70 is achievable --\n"
              "but no amount of bootstrapping repairs a panel that misses it.")
    pairs = list(zip(human, panel, strict=True))
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
    i.add_argument("--expect", nargs="*", default=None, metavar="FAMILY=SELECTOR",
                   help="the selector each judge family must have been served, e.g. "
                        "claude=anthropic/claude-opus-5:max. A judgement whose served "
                        "model does not match, or cannot be checked, drops its whole "
                        "claim. Falls back to judge-<family>.md when omitted, which is "
                        "convenient locally and unavailable wherever the private agent "
                        "definitions are not stowed")
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

    ir = sub.add_parser(
        "ingest-refutation",
        help="record cold-refuter responses as diagnostic model opinions",
    )
    ir.add_argument("--prompts", type=Path, required=True)
    ir.add_argument("--responses", type=Path, required=True)
    ir.add_argument("--out", type=Path, default=Path("refuter-opinions.jsonl"))
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
    k.add_argument("--expect-rows", type=int, default=60,
                   help="gate size required before agreement is computed. With "
                        "--case-map this counts only `kind == case` rows, so a 65-row "
                        "packet holding 5 case_supplement rows still satisfies 60")
    k.add_argument("--case-map", type=Path, default=None,
                   help="blinded case_id -> judge key JSONL. `case` rows are the gate, "
                        "`case_supplement` rows are reported but never scored, and "
                        "`control` rows are rejected")
    k.add_argument("--corpus", type=Path, default=None,
                   help="item labels; required for legacy run_id + claim_rid packets")
    k.set_defaults(fn=cmd_kappa)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
