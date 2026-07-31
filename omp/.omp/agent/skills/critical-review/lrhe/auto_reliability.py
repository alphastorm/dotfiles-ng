#!/usr/bin/env python3
"""
auto_reliability.py -- blinded re-adjudication of a sample of the canonical panel
by a fresh, more diverse jury, and the agreement statistics that describe it.

This is NOT the section 8 human calibration and it cannot become it. The gate in
LRHE-PROTOCOL.md section 8 is judge-versus-human agreement; everything here is
model-versus-model. It answers a narrower engineering question -- is the existing
panel internally stable, do fresh families reproduce its decisions, and do the
material conclusions survive reasonable automated re-adjudication -- and it must
never be reported as the preregistered gate. `human_verdict` and `human_label_id`
are not written by anything in this file.

Five properties make the comparison mean something:

  SAME INSTRUMENT     the prompt comes from judge_render.render_judge, the same
                      pure function that produced the canonical panel's 630
                      prompts. Improving the evidence surface for the fresh jury
                      would confound the comparison it exists to make.
  BLIND               cases carry opaque `AR-NNNN` ids. The canonical verdict,
                      the authoring family, the stratum, the arm and the trap
                      markers live only in the private case map, and the map is
                      never rendered into a prompt.
  INTERLEAVED         controls with known answers are shuffled into the same id
                      space as real cases, so a control is indistinguishable from
                      a case in both the prompt and the dispatch order.
  FROZEN              build writes one manifest with a prompt hash per case. A
                      response whose prompt hash is not in that manifest is not a
                      judgement of the case it claims.
  NON-AUTHORING       all five families are non-authoring for this cohort: the
                      floor reviews were written by Kimi, GLM and DeepSeek.

Old and fresh votes are deliberately never pooled. The canonical panel's votes are
the production measurement; the fresh votes are a repeat measurement. Combining
them into one consensus would destroy the only comparison available.

Commands
  build      selection + optional control spec -> frozen cases, prompts, assignments
  ingest     dispatcher responses             -> judgments-fresh.jsonl, strictly validated
  aggregate  fresh judgments                  -> aggregate-fresh.jsonl (3-of-5, no tie-order)
  analyze    everything                       -> agreement.json
  budget     assignments + price table        -> projected spend, fails closed over the cap
  verify     the namespace                    -> hashes, freeze precedence, canonical receipt
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from judge_render import evidence_surface
from judge_render import label_ids as item_label_ids
from judge_render import render_judge

VERDICTS = ("CONFIRMED", "PLAUSIBLE", "FABRICATED")
UNRESOLVED = "UNRESOLVED"
CASE_ID_FMT = "AR-{:04d}"

# The four active families and the exact selector each must be served. Requested,
# not trusted: `ingest` compares this against what the session record says answered,
# and a mismatch drops the response rather than relabelling it. The retired local
# Qwen lane remains only in historical response and manifest artifacts.
FAMILIES: dict[str, dict[str, str]] = {
    "claude": {"agent": "judge-claude", "selector": "anthropic/claude-opus-5:max",
               "role": "fresh repeat of an original judge family"},
    "gemini": {"agent": "judge-gemini", "selector": "google-antigravity/gemini-3.6-flash:high",
               "role": "fresh repeat of an original judge family"},
    "grok": {"agent": "judge-grok", "selector": "xai-oauth/grok-build",
             "role": "fresh repeat of an original judge family"},
    "gpt": {"agent": "judge-gpt-auto", "selector": "openai-codex/gpt-5.6-sol:high",
            "role": "new judge family"},
}
# GPT did not participate in the canonical panel, so it alone can receive a
# within-family repeat that is not already available from the old votes.
REPEAT_FAMILIES = ("gpt",)

# What MANIFEST.sha256 covers: inputs that must not move once a response exists, and
# deliberately not the dispatcher, the responses or any later analysis output.
FROZEN_INPUTS = ("cases.jsonl", "case-map.private.jsonl", "controls.jsonl",
                 "assignments.jsonl", "selection-60.csv", "human-packet.csv",
                 "build-manifest.json", "controls-spec.yml")


# ------------------------------------------------------------------ small io

def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]


def write_jsonl(p: Path, rows: list[dict]) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def read_csv_rows(p: Path) -> list[dict]:
    with open(p) as fh:
        return list(csv.DictReader(fh))


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------- statistics

def cohens_kappa(a: list[str], b: list[str]) -> tuple[float, float]:
    """(kappa, raw agreement) for two aligned label sequences."""
    n = len(a)
    if not n:
        return float("nan"), float("nan")
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return ((po - pe) / (1 - pe) if pe < 1 else 1.0), po


def krippendorff_alpha_nominal(units: list[list[str]]) -> float:
    """Nominal alpha over units that may carry different numbers of raters.

    Computed from the coincidence matrix rather than from pairwise averages, which
    is what makes a unit with 3 votes and a unit with 5 votes both usable. Units
    with fewer than two votes carry no information about agreement and are dropped
    -- silently including them as perfect agreement is how alpha gets inflated.

    The pairs iterated are distinct rater SLOTS, not distinct values: the matrix
    diagonal is the agreement, so skipping pairs whose two verdicts happen to be
    equal measures disagreement against nothing and returns alpha near zero on a
    unanimous panel.
    """
    usable = [u for u in units if len(u) >= 2]
    if not usable:
        return float("nan")
    coincide: defaultdict[tuple[str, str], float] = defaultdict(float)
    for u in usable:
        m = len(u)
        w = 1.0 / (m - 1)
        for i in range(m):
            for j in range(m):
                if i != j:
                    coincide[(u[i], u[j])] += w
    # Each unit contributes exactly m_u to the total mass, so `grand` is the number
    # of judgements and both disagreements normalise against the same denominator.
    grand = sum(coincide.values())
    if grand <= 1:
        return float("nan")
    marginal: defaultdict[str, float] = defaultdict(float)
    for (x, _), w in coincide.items():
        marginal[x] += w
    do = sum(w for (x, y), w in coincide.items() if x != y) / grand
    de = sum(
        marginal[x] * marginal[y] / (grand * (grand - 1))
        for x in marginal for y in marginal if x != y
    )
    if de <= 0:
        return float("nan") if do > 0 else 1.0
    return 1.0 - do / de


def fleiss_kappa(units: list[list[str]], categories: tuple[str, ...]) -> tuple[float, int]:
    """(kappa, n_units). Restricted to units with the modal rater count.

    Fleiss assumes a fixed number of raters per unit. Padding short units, or
    averaging over mixed arities, reports a coefficient for a design that was not
    run, so the arity actually used is returned alongside the figure.
    """
    if not units:
        return float("nan"), 0
    arity = Counter(len(u) for u in units).most_common(1)[0][0]
    kept = [u for u in units if len(u) == arity]
    n = len(kept)
    if n == 0 or arity < 2:
        return float("nan"), 0
    p_j = {c: sum(u.count(c) for u in kept) / (n * arity) for c in categories}
    p_i = [
        (sum(u.count(c) ** 2 for c in categories) - arity) / (arity * (arity - 1))
        for u in kept
    ]
    p_bar = sum(p_i) / n
    pe = sum(v * v for v in p_j.values())
    if pe >= 1:
        return 1.0, n
    return (p_bar - pe) / (1 - pe), n


def gwet_ac1(units: list[list[str]], categories: tuple[str, ...]) -> tuple[float, int]:
    """(AC1, n_units). Secondary, non-gating: PLAUSIBLE dominates the natural mix.

    Kappa's chance term collapses when one category carries most of the mass, which
    is exactly this distribution. AC1 estimates chance agreement differently and is
    reported so a low kappa on a skewed sample is not read as low agreement.
    """
    usable = [u for u in units if len(u) >= 2]
    n = len(usable)
    if not n:
        return float("nan"), 0
    pa = sum(
        (sum(u.count(c) ** 2 for c in categories) - len(u)) / (len(u) * (len(u) - 1))
        for u in usable
    ) / n
    pi = {
        c: sum(u.count(c) / len(u) for u in usable) / n
        for c in categories
    }
    q = len(categories)
    pe = sum(p * (1 - p) for p in pi.values()) / (q - 1) if q > 1 else 0.0
    if pe >= 1:
        return 1.0, n
    return (pa - pe) / (1 - pe), n


def confusion(pairs: list[tuple[str, str]], categories: list[str]) -> dict[str, dict[str, int]]:
    return {
        r: {c: sum(1 for x, y in pairs if x == r and y == c) for c in categories}
        for r in categories
    }


# ------------------------------------------------------------------- build

def _selection_keys(rows: list[dict]) -> list[tuple[str, str]]:
    """(run_id, claim_rid) for each selection row, order preserved and verbatim."""
    out = []
    for r in rows:
        rid = r.get("claim_rid") or r.get("rid") or ""
        out.append((str(r.get("run_id", "")), str(rid)))
    return out


def _run_stem(run_id: str) -> str:
    """`S1-11c5338b-glm-floor-<suffix>` without the suffix.

    `run_id` used to be a function of when ingest ran; it is a digest of the reply
    now. The frozen 60-claim selection predates that change, so every key in it
    carries the retired timestamp suffix and matches nothing in the current claims
    or judge files. The item, the authoring family and the lens -- everything that
    identifies WHICH review a row is -- live in the part before the suffix.
    """
    return run_id.rsplit("-", 1)[0]


def resolve_selection(keys: list[tuple[str, str]], claims: dict[tuple[str, str], dict]) -> \
        tuple[dict[tuple[str, str], tuple[str, str]], list[str]]:
    """Map each selection key onto a current claim key, or say why it cannot be.

    Exact keys are used as-is. A key that matches nothing is retried on its stem,
    and that retry is only sound while a stem identifies exactly one run -- so an
    ambiguous stem is a refusal, not a guess. The original key is what the audit
    reports as its selection provenance; the resolved key is what it points at.
    """
    by_stem: dict[str, set[str]] = defaultdict(set)
    for run_id, _rid in claims:
        by_stem[_run_stem(run_id)].add(run_id)

    resolved: dict[tuple[str, str], tuple[str, str]] = {}
    problems: list[str] = []
    for run_id, rid in keys:
        if (run_id, rid) in claims:
            resolved[(run_id, rid)] = (run_id, rid)
            continue
        candidates = by_stem.get(_run_stem(run_id), set())
        if len(candidates) == 1:
            current = next(iter(candidates))
            if (current, rid) in claims:
                resolved[(run_id, rid)] = (current, rid)
                continue
            problems.append(f"selection row {run_id}|{rid} resolves to run {current}, which "
                            f"has no claim {rid}")
        elif not candidates:
            problems.append(f"selection row {run_id}|{rid} matches no claim, and its stem "
                            f"{_run_stem(run_id)} matches no run")
        else:
            problems.append(f"selection row {run_id}|{rid} is ambiguous: stem "
                            f"{_run_stem(run_id)} matches {len(candidates)} runs")
    return resolved, problems


def _load_control_spec(p: Path) -> list[dict]:
    text = Path(p).read_text()
    if p.suffix in (".yml", ".yaml"):
        import yaml
        doc = yaml.safe_load(text)
        return list(doc.get("controls") or []) if isinstance(doc, dict) else list(doc or [])
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def _validate_control(spec: dict, item: dict) -> list[str]:
    """The construction rules from the audit design, checked mechanically.

    A control whose expected answer rests on someone's opinion is not a control, so
    each category has to be falsifiable against what the judge is shown: the label
    id a CONFIRMED control claims must exist, the token a FABRICATED control invents
    must be absent from the code, and the observation a PLAUSIBLE control makes must
    be present in it.

    Checked against `evidence_surface`, never the rendered prompt. The prompt
    contains the claim, so an invented symbol appears in it by construction and both
    token checks would pass for every control ever written.
    """
    surface = evidence_surface(item)
    problems: list[str] = []
    expected = spec.get("expected")
    labels = set(item_label_ids(item))
    if expected == "CONFIRMED":
        if spec.get("label_id") not in labels:
            problems.append(
                f"{spec['control_id']}: expects CONFIRMED against label "
                f"{spec.get('label_id')!r}, which item {item['item_id']} does not carry")
    elif expected == "FABRICATED":
        token = spec.get("contradicted_by")
        if not token:
            problems.append(f"{spec['control_id']}: FABRICATED needs `contradicted_by`, the "
                            "token the shown code must not contain")
        elif token in surface:
            problems.append(f"{spec['control_id']}: `contradicted_by` {token!r} DOES appear in "
                            "the evidence surface, so the claim is not mechanically false")
        if spec.get("label_id"):
            problems.append(f"{spec['control_id']}: FABRICATED must not name a label")
    elif expected == "PLAUSIBLE":
        token = spec.get("grounded_in")
        if not token:
            problems.append(f"{spec['control_id']}: PLAUSIBLE needs `grounded_in`, the token "
                            "that makes the observation checkable")
        elif token not in surface:
            problems.append(f"{spec['control_id']}: `grounded_in` {token!r} is absent from the "
                            "evidence surface, so the observation is not grounded")
        if spec.get("label_id"):
            problems.append(f"{spec['control_id']}: PLAUSIBLE must not name a label")
        if not spec.get("no_label_rationale"):
            problems.append(f"{spec['control_id']}: PLAUSIBLE needs `no_label_rationale` "
                            "recording why no ground-truth label covers it")
    else:
        problems.append(f"{spec.get('control_id')}: expected must be one of {VERDICTS}")
    return problems


def cmd_build(args) -> int:
    out = Path(args.out)
    corpus = {it["item_id"]: it for it in read_jsonl(args.corpus)}
    claims = {(c["run_id"], str(c["rid"])): c for c in read_csv_rows(args.claims)}
    judge = {(j["run_id"], str(j["claim_rid"])): j for j in read_jsonl(args.judge)}
    selection = read_csv_rows(args.selection)
    keys = _selection_keys(selection)

    problems: list[str] = []
    if len(keys) != len(set(keys)):
        problems.append("the selection carries duplicate (run_id, claim_rid) keys")

    resolved, resolve_problems = resolve_selection(keys, claims)
    problems.extend(resolve_problems)

    records: list[dict] = []

    def add_case(sel_key: tuple[str, str], kind: str) -> None:
        if sel_key not in resolved:
            return
        run_id, rid = resolved[sel_key]
        claim = claims[(run_id, rid)]
        panel = judge.get((run_id, rid))
        if panel is None:
            problems.append(f"selection row {sel_key[0]}|{sel_key[1]} resolves to "
                            f"{run_id}|{rid}, which has no canonical verdict")
            return
        item = corpus.get(claim["item_id"])
        if item is None:
            problems.append(f"selection row {sel_key[0]}|{sel_key[1]} references unknown item "
                            f"{claim['item_id']}")
            return
        records.append({
            "kind": kind, "run_id": run_id, "claim_rid": rid,
            "selection_run_id": sel_key[0], "selection_claim_rid": sel_key[1],
            "run_id_resolved": run_id != sel_key[0],
            "item_id": claim["item_id"], "stratum": claim["item_id"][:2],
            "prompt": render_judge(item, claim),
            "label_ids": item_label_ids(item),
            "panel_verdict": panel["verdict"], "panel_label_id": panel.get("label_id") or "",
            "panel_needs_human": bool(panel.get("needs_human")),
            "panel_unanimous": bool(panel.get("unanimous")),
            "panel_families": list(panel.get("panel") or []),
            "severity": claim.get("severity", ""),
            "control_expected": None, "control_label_id": None, "control_id": None,
        })

    for sel_key in keys:
        add_case(sel_key, "case")

    # The eight canonical needs_human claims must all be re-adjudicated, and only
    # three of them fell into the frozen 60. The other five are added as a labelled
    # supplement rather than by resampling: the balanced 60 is the reliability
    # denominator, and quietly growing it would change what every coefficient below
    # is an estimate of. `analyze` reports on `kind == "case"` and calls the
    # supplement out separately.
    seen_keys = {(r["run_id"], r["claim_rid"]) for r in records}
    supplement_keys = _selection_keys(read_csv_rows(args.supplement)) if args.supplement \
        and Path(args.supplement).suffix == ".csv" else (
            [(str(r["run_id"]), str(r.get("claim_rid") or r.get("rid")))
             for r in read_jsonl(args.supplement)] if args.supplement else [])
    extra_resolved, extra_problems = resolve_selection(supplement_keys, claims)
    problems.extend(extra_problems)
    resolved.update(extra_resolved)
    for sel_key in supplement_keys:
        if extra_resolved.get(sel_key) in seen_keys:
            continue
        add_case(sel_key, "case_supplement")

    controls_spec = _load_control_spec(args.controls) if args.controls else []
    for spec in controls_spec:
        item = corpus.get(spec.get("item_id", ""))
        if item is None:
            problems.append(f"{spec.get('control_id')}: unknown item {spec.get('item_id')!r}")
            continue
        synthetic = {
            "severity": spec.get("severity", 2), "confidence": spec.get("confidence", 0.7),
            "claim_text": spec.get("claim_text", ""),
            "evidence_text": spec.get("evidence_text", ""),
            "impact_text": spec.get("impact_text", ""),
        }
        prompt = render_judge(item, synthetic)
        problems.extend(_validate_control(spec, item))
        records.append({
            "kind": "control", "run_id": "", "claim_rid": "",
            "item_id": spec["item_id"], "stratum": spec["item_id"][:2],
            "prompt": prompt, "label_ids": item_label_ids(item),
            "panel_verdict": "", "panel_label_id": "", "panel_needs_human": False,
            "panel_unanimous": False, "panel_families": [],
            "severity": spec.get("severity", 2),
            "control_expected": spec["expected"],
            "control_label_id": spec.get("label_id") or None,
            "control_id": spec["control_id"],
        })

    if problems:
        print(f"REFUSED: {len(problems)} problem(s) in the audit selection. Nothing written.",
              file=sys.stderr)
        for line in problems[:12]:
            print(f"  {line}", file=sys.stderr)
        if len(problems) > 12:
            print(f"  ... and {len(problems) - 12} more", file=sys.stderr)
        return 2

    # Opaque ids are assigned over the SHUFFLED union, so a control does not sit in
    # a contiguous block at the end of the id space and dispatch order does not
    # segment cases from controls. Seeded, because a manifest nobody can rebuild is
    # not a frozen manifest.
    rng = random.Random(args.seed)
    rng.shuffle(records)
    for i, rec in enumerate(records, 1):
        rec["case_id"] = CASE_ID_FMT.format(i)
        rec["prompt_sha256"] = sha256_text(rec["prompt"])

    families = args.families or list(FAMILIES)
    unknown = [f for f in families if f not in FAMILIES]
    if unknown:
        print(f"REFUSED: unknown families {unknown}", file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    (out / "prompts").mkdir(exist_ok=True)
    for rec in records:
        (out / "prompts" / f"{rec['case_id']}.txt").write_text(rec["prompt"])

    # cases.jsonl is the dispatchable surface: it carries the prompt and the label
    # set a judgement may cite, and nothing that identifies the case.
    write_jsonl(out / "cases.jsonl", [
        {"case_id": r["case_id"], "prompt_sha256": r["prompt_sha256"],
         "label_ids": r["label_ids"], "prompt": r["prompt"]}
        for r in sorted(records, key=lambda r: r["case_id"])])

    # Everything blinded lives here, and only here.
    write_jsonl(out / "case-map.private.jsonl", [
        {k: v for k, v in r.items() if k != "prompt"}
        for r in sorted(records, key=lambda r: r["case_id"])])

    write_jsonl(out / "controls.jsonl", [
        {"case_id": r["case_id"], "control_id": r["control_id"],
         "expected": r["control_expected"], "expected_label_id": r["control_label_id"],
         "item_id": r["item_id"], "prompt_sha256": r["prompt_sha256"]}
        for r in sorted(records, key=lambda r: r["case_id"]) if r["kind"] == "control"])

    assignments = []
    for rec in sorted(records, key=lambda r: r["case_id"]):
        for fam in families:
            reps = args.reps if fam in REPEAT_FAMILIES else 1
            for rep in range(1, reps + 1):
                assignments.append({
                    "assignment_id": f"{rec['case_id']}|{fam}|{rep}",
                    "case_id": rec["case_id"], "family": fam, "rep": rep,
                    "agent": FAMILIES[fam]["agent"],
                    "requested_selector": FAMILIES[fam]["selector"],
                    "prompt_sha256": rec["prompt_sha256"],
                })
    write_jsonl(out / "assignments.jsonl", assignments)

    with open(out / "selection-60.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["run_id", "claim_rid"])
        w.writeheader()
        w.writerows({"run_id": k[0], "claim_rid": k[1]} for k in keys)

    # The eventual human packet: case ids and three blank columns, nothing else. The
    # canonical CSV leaked item_id and stratum, including the `S4` trap prefix, and a
    # labeller cannot use those but a reader of the file can.
    with open(out / "human-packet.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["case_id", "human_verdict", "human_label_id"])
        w.writeheader()
        w.writerows({"case_id": r["case_id"], "human_verdict": "", "human_label_id": ""}
                    for r in sorted(records, key=lambda r: r["case_id"])
                    if r["kind"] in ("case", "case_supplement"))

    n_cases = sum(1 for r in records if r["kind"] == "case")
    n_supplement = sum(1 for r in records if r["kind"] == "case_supplement")
    n_controls = sum(1 for r in records if r["kind"] == "control")
    manifest = {
        "frozen_at": now_utc(),
        "seed": args.seed,
        "n_cases": n_cases, "n_supplement": n_supplement, "n_controls": n_controls,
        "families": {f: FAMILIES[f] for f in families},
        "repeat_families": [f for f in families if f in REPEAT_FAMILIES],
        "reps_for_repeat_families": args.reps,
        "expected_responses": len(assignments),
        "selection_source": str(args.selection),
        "selection_source_sha256": sha256_file(args.selection),
        "claims_source_sha256": sha256_file(args.claims),
        "corpus_source_sha256": sha256_file(args.corpus),
        "canonical_judge_sha256": sha256_file(args.judge),
        "controls_source_sha256": sha256_file(args.controls) if args.controls else None,
        "supplement_source_sha256": sha256_file(args.supplement) if args.supplement else None,
        "supplement_note": "cases added outside the frozen 60 so that every canonical "
                           "needs_human claim is re-adjudicated; excluded from the "
                           "headline agreement denominators",
        "renderer": "judge_render.render_judge",
        "human_judge_reliability": {"status": "not_measured",
                                    "preregistered_gate_closed": False},
    }
    (out / "build-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    # The manifest attests the FROZEN INPUTS and nothing else. Hashing the whole
    # directory sweeps in the dispatcher, the responses and every later analysis
    # output, so the first fix to a runner invalidates a manifest whose job is to
    # prove the cases did not move -- and a check that always fails gets ignored,
    # which is worse than not having it.
    digests = {
        p.name: sha256_file(p)
        for p in (out / n for n in FROZEN_INPUTS) if p.is_file()
    }
    for p in sorted((out / "prompts").glob("*.txt")):
        digests[f"prompts/{p.name}"] = sha256_file(p)
    (out / "MANIFEST.sha256").write_text(
        "".join(f"{d}  {n}\n" for n, d in sorted(digests.items())))

    print(f"cases {n_cases} | supplement {n_supplement} | controls {n_controls} | "
          f"families {len(families)}")
    print(f"assignments {len(assignments)}  "
          f"({n_cases + n_supplement + n_controls} x {len(families)}, "
          f"{args.reps} reps for {[f for f in families if f in REPEAT_FAMILIES]})")
    print(f"frozen at {manifest['frozen_at']}  seed {args.seed}")
    print(f"wrote {out}/")
    for name in ("cases.jsonl", "case-map.private.jsonl", "controls.jsonl",
                 "assignments.jsonl", "selection-60.csv", "human-packet.csv",
                 "build-manifest.json", "MANIFEST.sha256"):
        print(f"  {name}")
    print(f"  prompts/  ({len(records)} files)")
    return 0


# ------------------------------------------------------------------ ingest

def _finite_unit(value: object) -> bool:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and 0.0 <= f <= 1.0


def normalize_label_id(raw: object, labels: set[str]) -> tuple[str, bool]:
    """(label_id, was_normalized). Accepts the rendered label line as the id it opens with.

    The labels block renders as `  [c_1] severity P2 path: description`, and a model
    asked for "the id of the matched label" quite reasonably answers with the line it
    matched rather than the bracketed token. That is the same value differently
    spelled, and the first smoke run lost four otherwise-valid judgements to it --
    which would have been recorded as panel disagreement rather than as a parsing
    choice of ours.

    Extraction is accepted ONLY when the bracketed token is itself in the case's label
    set, so this widens the spelling and never the vocabulary: an id that is simply
    wrong is still rejected.
    """
    if raw is None:
        return "", False
    text = str(raw).strip()
    if not text or text in labels:
        return text, False
    if text.startswith("["):
        candidate = text[1:].split("]", 1)[0].strip()
        if candidate in labels:
            return candidate, True
    # Some replies drop the brackets and answer `superseded severity P0 path: ...`.
    # Same value again, so the leading token is accepted on the same terms. A reply
    # that drops the id itself is NOT recovered by inferring the item's only label:
    # that would be guessing what the judge meant, which is the one thing this
    # function must never do.
    head = text.split(None, 1)[0].strip() if text.split() else ""
    if head in labels:
        return head, True
    return text, False


# Fields only a deterministic runner may ever set. A judgement is an opinion, and the
# scorer puts execution above every judge verdict, so an opinion carrying these is
# refused rather than sanitised: `cmd_ingest`'s output projection would strip them
# silently, and a dispatcher confused enough to send them is a dispatcher whose other
# telemetry should not be trusted either.
EXECUTION_ONLY_KEYS = ("ran", "exit_code", "reproduced", "stdout_sha256", "stderr_sha256",
                       "repo_digest_before", "repo_digest_after", "runner_version")


def validate_response(resp: dict, assignment: dict, labels: set[str]) -> list[str]:
    """Every reason this response is not a usable judgement of its assigned case.

    Fails closed on absence throughout. Missing telemetry is not a clean run: it is
    a run whose identity, tool surface and prompt could not be checked, and the
    canonical panel already learned that a gate reading `0` because nothing supplied
    it is worse than no gate.
    """
    bad: list[str] = []
    asserted_execution = [k for k in EXECUTION_ONLY_KEYS if k in resp]
    if asserted_execution:
        bad.append(f"response carries runner-only field(s) {asserted_execution}; a model "
                   f"opinion may not assert execution")
    verdict = str(resp.get("verdict", "")).strip().upper()
    if verdict not in VERDICTS:
        bad.append(f"verdict {resp.get('verdict')!r} is not one of {VERDICTS}")
    label, _ = normalize_label_id(resp.get("label_id"), labels)
    if verdict == "CONFIRMED":
        if not label:
            bad.append("CONFIRMED requires a label_id")
        elif label not in labels:
            bad.append(f"label_id {label!r} is not in this case's label set")
    elif verdict in ("PLAUSIBLE", "FABRICATED") and label:
        bad.append(f"{verdict} must carry label_id null, got {label!r}")
    if not _finite_unit(resp.get("confidence")):
        bad.append(f"confidence {resp.get('confidence')!r} is not a finite value in [0,1]")

    if resp.get("prompt_sha256") != assignment["prompt_sha256"]:
        bad.append("prompt_sha256 does not match the frozen manifest")
    if str(resp.get("family", "")) != assignment["family"]:
        bad.append(f"family {resp.get('family')!r} was not the family assigned")
    if str(resp.get("requested_selector", "")) != assignment["requested_selector"]:
        bad.append("requested_selector does not match the frozen manifest")

    served = resp.get("served_model")
    if not served:
        bad.append("served_model absent; identity cannot be verified")
    elif resp.get("identity_verified") is not True:
        bad.append(f"identity_verified is {resp.get('identity_verified')!r}, served {served!r}")
    if resp.get("fallback_used"):
        bad.append("a fallback model answered")
    tools = resp.get("named_tools")
    if tools is None:
        bad.append("named_tools absent; the tool surface cannot be verified")
    elif tools:
        bad.append(f"the judge named tools {tools}")
    return bad


def cmd_ingest(args) -> int:
    assignments = {a["assignment_id"]: a for a in read_jsonl(args.manifest)}
    cases = {c["case_id"]: c for c in read_jsonl(args.cases)}

    responses: list[dict] = []
    resp_dir = Path(args.responses)
    files = sorted(resp_dir.glob("*.jsonl")) if resp_dir.is_dir() else [resp_dir]
    for f in files:
        responses.extend(read_jsonl(f))

    kept: list[dict] = []
    rejected: list[dict] = []
    seen: dict[str, str] = {}
    for r in responses:
        aid = str(r.get("assignment_id", ""))
        a = assignments.get(aid)
        if a is None:
            rejected.append({"assignment_id": aid, "reasons": ["no such assignment"]})
            continue
        if str(r.get("case_id", "")) != a["case_id"]:
            rejected.append({"assignment_id": aid,
                             "reasons": [f"case_id {r.get('case_id')!r} is a cross-case response"]})
            continue
        labels = set(cases[a["case_id"]]["label_ids"])
        reasons = validate_response(r, a, labels)
        if reasons:
            rejected.append({"assignment_id": aid, "reasons": reasons})
            continue
        # Later duplicates are rejected rather than overwriting: a re-dispatch that
        # produced a second answer is two measurements, and silently keeping the
        # last one picks a winner by file order.
        if aid in seen:
            rejected.append({"assignment_id": aid, "reasons": ["duplicate response"]})
            continue
        seen[aid] = r.get("finished_at", "")
        kept.append({
            "assignment_id": aid, "case_id": a["case_id"], "family": a["family"],
            "rep": a["rep"], "verdict": str(r["verdict"]).strip().upper(),
            "label_id": normalize_label_id(r.get("label_id"), labels)[0],
            "label_id_normalized": normalize_label_id(r.get("label_id"), labels)[1],
            "label_id_as_returned": (str(r.get("label_id")) if r.get("label_id") else ""),
            "confidence": float(r["confidence"]),
            "rationale": (str(r.get("rationale") or ""))[:400],
            "requested_selector": a["requested_selector"],
            "served_model": r["served_model"],
            "provider_route": r.get("provider_route") or "",
            "response_id": r.get("response_id") or "",
            "runtime_request_id": r.get("runtime_request_id") or "",
            "logical_turn_id": r.get("logical_turn_id") or "",
            "identity_verified": True,
            "provider_fingerprint": r.get("provider_fingerprint"),
            "provider_fingerprint_observation":
                r.get("provider_fingerprint_observation") or "not_observed",
            "prompt_sha256": r["prompt_sha256"],
            "session_file": r.get("session_file") or "",
            "cost_usd_listed": r.get("cost_usd_listed"),
            "started_at": r.get("started_at") or "", "finished_at": r.get("finished_at") or "",
        })

    write_jsonl(args.out, sorted(kept, key=lambda r: (r["case_id"], r["family"], r["rep"])))
    if rejected:
        write_jsonl(Path(args.out).with_name("rejected-fresh.jsonl"), rejected)

    missing = sorted(set(assignments) - set(seen))
    n_exp = len(assignments)
    rate = len(rejected) / max(len(responses), 1)
    print(f"responses {len(responses)} | accepted {len(kept)} | rejected {len(rejected)} "
          f"({rate:.1%})")
    print(f"expected {n_exp} | missing {len(missing)}")
    for row in rejected[:8]:
        print(f"  reject {row['assignment_id']}: {'; '.join(row['reasons'])}")
    if len(rejected) > 8:
        print(f"  ... and {len(rejected) - 8} more -> rejected-fresh.jsonl")
    print(f"wrote {args.out}")
    if missing:
        print(f"\n{len(missing)} assignments have no valid response. `aggregate` refuses a "
              f"partial matrix:\nthe gaps in a partial panel read as agreement.")
        for aid in missing[:8]:
            print(f"  missing {aid}")
    return 1 if (missing or rejected) else 0


# --------------------------------------------------------------- aggregate

# Bumped when `aggregate_case` changes what it will record as settled, so a derivative
# aggregate says which policy produced it instead of being dated by filename.
#
#   v1  `needs_resolution` fired only on a CONFIRMED majority whose voters named
#       different label_ids. Vote margin was not consulted, so a 3-2 split was recorded
#       identically to a 5-0 and `human_queue.jsonl` stayed empty over 22 bare majorities.
#   v2  a majority that clears the threshold by exactly one vote also escalates, and
#       `label_split` is keyed to label ambiguity rather than to `needs_resolution`.
#
# `aggregate-fresh.jsonl` was produced under v1 and is preserved as-is. A v2 recomputation
# is a versioned derivative (`aggregate-fresh-v2.jsonl`), never an overwrite.
AGGREGATION_POLICY_VERSION = 2


def aggregate_case(votes: list[dict], min_majority: int) -> dict:
    """3-of-5 absolute majority, no tie-order, and no forced result at 2-2-1.

    `_top_with_tiebreak` in the canonical path settles ties by a fixed category
    order, which is right when the aggregate has to be total. Here it would invent
    a decision the jury did not reach, and the unresolved rate is one of the
    findings, so a plurality below the threshold stays UNRESOLVED.

    A majority that clears the threshold by exactly one vote still returns its verdict,
    but is escalated via `needs_resolution` rather than recorded as settled. See the
    comment on that assignment for the measurement behind it.
    """
    tally = Counter(v["verdict"] for v in votes)
    best = max(tally.values()) if tally else 0
    leaders = [v for v, c in tally.items() if c == best]
    if best >= min_majority and len(leaders) == 1:
        verdict = leaders[0]
        winners = [v for v in votes if v["verdict"] == verdict]
        label_votes = Counter(v["label_id"] for v in winners if v["label_id"])
        label_ambiguous = verdict == "CONFIRMED" and len(label_votes) != 1
        # A majority that only just clears the threshold is one vote from a tie, and
        # measured on this corpus that is where the consensus errors live: pooled over
        # two independent 5-voter panels, accuracy is 0.72 at a bare majority against
        # 0.96 at 4-1 and 0.99 at 5-0, and the bare bucket holds 13 of 16 errors.
        # Recording it as settled is what left `human_queue.jsonl` empty while six
        # verdicts were wrong. Triage, not a filter: 3 of the 15 carried a wider margin.
        needs_resolution = label_ambiguous or best == min_majority
        label_id = label_votes.most_common(1)[0][0] if len(label_votes) == 1 else ""
        # A CONFIRMED majority whose voters named different defects has a verdict and
        # no matched label. That is a different failure from a split verdict and it
        # corrupts the 1:1 matching the recall figures rest on.
        return {"verdict": verdict, "label_id": label_id,
                "n_top": best, "needs_resolution": needs_resolution,
                "label_split": sorted(label_votes) if label_ambiguous else []}
    return {"verdict": UNRESOLVED, "label_id": "", "n_top": best,
            "needs_resolution": True, "label_split": []}


def cmd_aggregate(args) -> int:
    fresh = read_jsonl(args.fresh)
    cmap = {c["case_id"]: c for c in read_jsonl(args.case_map)}
    assignments = read_jsonl(args.manifest)

    expected: dict[str, set[str]] = defaultdict(set)
    for a in assignments:
        if int(a["rep"]) == 1:
            expected[a["case_id"]].add(a["family"])

    # Only rep 1 forms the panel. A second GPT or Qwen answer is a repeatability
    # measurement; counting it as a sixth vote would give two families three votes
    # between them and quietly change what a majority means.
    by_case: dict[str, list[dict]] = defaultdict(list)
    for r in fresh:
        if int(r["rep"]) == 1:
            by_case[r["case_id"]].append(r)

    incomplete = sorted(
        cid for cid, fams in expected.items()
        if {v["family"] for v in by_case.get(cid, [])} != fams
    )
    if incomplete and not args.allow_partial:
        print(f"REFUSED: {len(incomplete)} of {len(expected)} cases have an incomplete panel. "
              f"Nothing written.", file=sys.stderr)
        for cid in incomplete[:8]:
            have = sorted(v["family"] for v in by_case.get(cid, []))
            print(f"  {cid}: have {have}, need {sorted(expected[cid])}", file=sys.stderr)
        print("  A partial panel is not a smaller panel: the missing votes read as "
              "agreement.", file=sys.stderr)
        return 2

    rows = []
    for cid in sorted(expected):
        votes = sorted(by_case.get(cid, []), key=lambda v: v["family"])
        if not votes:
            continue
        agg = aggregate_case(votes, args.min_majority)
        meta = cmap.get(cid, {})
        rows.append({
            "case_id": cid, "kind": meta.get("kind", ""),
            "aggregation_policy_version": AGGREGATION_POLICY_VERSION,
            "fresh_verdict": agg["verdict"], "fresh_label_id": agg["label_id"],
            "n_top": agg["n_top"], "n_votes": len(votes),
            "needs_resolution": agg["needs_resolution"], "label_split": agg["label_split"],
            "votes": {v["family"]: v["verdict"] for v in votes},
            "vote_labels": {v["family"]: v["label_id"] for v in votes},
            "mean_confidence": round(sum(v["confidence"] for v in votes) / len(votes), 3),
        })
    write_jsonl(args.out, rows)

    mix = Counter(r["fresh_verdict"] for r in rows)
    n = len(rows)
    print(f"aggregated {n} cases at min-majority {args.min_majority} of {args.panel_size}")
    for v in (*VERDICTS, UNRESOLVED):
        print(f"  {v:<12} {mix[v]:>4}  ({mix[v] / max(n, 1):.1%})")
    print(f"  {'needs label':<12} {sum(1 for r in rows if r['label_split']):>4}")
    print(f"wrote {args.out}")
    return 0


# ---------------------------------------------------------------- analyze

def _composite(verdict: str, label: str) -> str:
    return f"CONFIRMED:{label}" if verdict == "CONFIRMED" else verdict


def cmd_analyze(args) -> int:
    cmap = {c["case_id"]: c for c in read_jsonl(args.case_map)}
    fresh = read_jsonl(args.fresh)
    agg = {r["case_id"]: r for r in read_jsonl(args.aggregate)}
    canon_raw = read_jsonl(args.canonical_raw) if args.canonical_raw else []

    # The headline denominators are the frozen 60 and nothing else. Supplements are
    # judged and reported, never pooled into alpha or kappa.
    real = [cid for cid, m in cmap.items() if m.get("kind") == "case"]
    supplement = [cid for cid, m in cmap.items() if m.get("kind") == "case_supplement"]
    controls = [cid for cid, m in cmap.items() if m.get("kind") == "control"]

    rep1 = [r for r in fresh if int(r["rep"]) == 1]
    by_case: dict[str, list[dict]] = defaultdict(list)
    by_family: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rep1:
        by_case[r["case_id"]].append(r)
        by_family[r["family"]][r["case_id"]] = r

    report: dict = {
        "generated_at": now_utc(),
        "scope": {"cases": len(real), "supplement": len(supplement),
                  "controls": len(controls),
                  "families": sorted(by_family), "rep1_judgements": len(rep1)},
        "human_judge_reliability": {"status": "not_measured",
                                    "preregistered_gate_closed": False},
        "not_measurable": [
            "OC_SCREEN and T_OC remain NOT MEASURABLE on judge-dependent outcomes: "
            "neither arm was adjudicated, and this audit did not adjudicate them.",
            "The floor panel still has no matched same-family null.",
            "Automated judge reliability does not close the diversity/decorrelation gate.",
        ],
    }

    # ---- 1-4: agreement among the fresh five, over real cases only.
    units = [[v["verdict"] for v in by_case[cid]] for cid in real if by_case.get(cid)]
    pattern = Counter()
    for u in units:
        counts = sorted(Counter(u).values(), reverse=True)
        pattern["-".join(map(str, counts))] += 1
    fk, fk_n = fleiss_kappa(units, VERDICTS)
    ac1, ac1_n = gwet_ac1(units, VERDICTS)
    report["fresh_five"] = {
        "raw_pattern_counts": dict(sorted(pattern.items())),
        "no_majority_units": sum(
            1 for u in units
            if max(Counter(u).values()) < args.min_majority
            or list(Counter(u).values()).count(max(Counter(u).values())) > 1),
        "krippendorff_alpha_nominal": krippendorff_alpha_nominal(units),
        "fleiss_kappa": fk, "fleiss_kappa_units": fk_n,
        "gwet_ac1": ac1, "gwet_ac1_units": ac1_n,
        "gwet_ac1_note": "secondary, non-gating: PLAUSIBLE dominates the natural mix",
    }
    pairwise = {}
    for a, b in combinations(sorted(by_family), 2):
        shared = [cid for cid in real if cid in by_family[a] and cid in by_family[b]]
        if not shared:
            continue
        k, po = cohens_kappa([by_family[a][cid]["verdict"] for cid in shared],
                             [by_family[b][cid]["verdict"] for cid in shared])
        pairwise[f"{a}|{b}"] = {"kappa": k, "raw": po, "n": len(shared)}
    report["fresh_five"]["pairwise_cohens_kappa"] = pairwise

    # ---- 5-6: canonical aggregate against the fresh consensus.
    def canon_vs(cids: list[str], fresh_of: dict[str, str], name: str) -> dict:
        pairs = [(cmap[c]["panel_verdict"], fresh_of[c]) for c in cids if c in fresh_of]
        cats = sorted({p[0] for p in pairs} | {p[1] for p in pairs})
        k, po = cohens_kappa([p[0] for p in pairs], [p[1] for p in pairs])
        return {"name": name, "n": len(pairs), "kappa": k, "raw": po,
                "confusion_canonical_rows": confusion(pairs, cats)}

    resolved = {c: agg[c]["fresh_verdict"] for c in real
                if c in agg and agg[c]["fresh_verdict"] != UNRESOLVED}
    report["canonical_vs_fresh"] = {
        "majority_resolved_only": canon_vs(real, resolved, "fresh five majority, resolved only"),
    }
    for variant, mapped in (
        ("neutral_unresolved_plausible", "PLAUSIBLE"),
        ("optimistic_unresolved_confirmed", "CONFIRMED"),
        ("pessimistic_unresolved_fabricated", "FABRICATED"),
    ):
        filled = {c: (agg[c]["fresh_verdict"] if agg[c]["fresh_verdict"] != UNRESOLVED
                      else mapped) for c in real if c in agg}
        report["canonical_vs_fresh"][variant] = canon_vs(real, filled, variant)

    # The three original families, re-run: their consensus is the closest thing to a
    # replication of the canonical panel, because the panel was drawn from them.
    trio = [f for f in ("claude", "gemini", "grok") if f in by_family]
    trio_consensus: dict[str, str] = {}
    for cid in real:
        votes = [by_family[f][cid]["verdict"] for f in trio if cid in by_family[f]]
        if not votes:
            continue
        tally = Counter(votes)
        best = max(tally.values())
        leaders = [v for v, c in tally.items() if c == best]
        if best > len(votes) / 2 and len(leaders) == 1:
            trio_consensus[cid] = leaders[0]
    report["canonical_vs_fresh"]["original_trio_consensus"] = canon_vs(
        real, trio_consensus, "fresh claude/gemini/grok consensus")

    # ---- 7-8: repeatability.
    canon_by_family: dict[tuple[str, str, str], str] = {}
    for j in canon_raw:
        canon_by_family[(str(j["run_id"]), str(j["claim_rid"]), j["judge_family"])] = j["verdict"]
    old_vs_fresh = {}
    for fam in trio:
        pairs = []
        for cid in real:
            m = cmap[cid]
            old = canon_by_family.get((m["run_id"], m["claim_rid"], fam))
            new = by_family[fam].get(cid)
            if old and new:
                pairs.append((old, new["verdict"]))
        if pairs:
            k, po = cohens_kappa([p[0] for p in pairs], [p[1] for p in pairs])
            old_vs_fresh[fam] = {"n": len(pairs), "kappa": k, "raw": po}
        else:
            old_vs_fresh[fam] = {"n": 0, "kappa": None, "raw": None,
                                 "note": "this family judged none of the sampled claims"}
    report["per_family_old_vs_fresh_kappa"] = old_vs_fresh

    retest = {}
    for fam in REPEAT_FAMILIES:
        r1 = {r["case_id"]: r["verdict"] for r in fresh
              if r["family"] == fam and int(r["rep"]) == 1}
        r2 = {r["case_id"]: r["verdict"] for r in fresh
              if r["family"] == fam and int(r["rep"]) == 2}
        shared = sorted(set(r1) & set(r2) & set(real))
        if shared:
            k, po = cohens_kappa([r1[c] for c in shared], [r2[c] for c in shared])
            retest[fam] = {"n": len(shared), "kappa": k, "raw": po}
        else:
            retest[fam] = {"n": 0, "kappa": None, "raw": None, "note": "no second pass ingested"}
    report["test_retest_kappa"] = retest

    # ---- 10: per stratum and per canonical verdict.
    def slice_agreement(pick) -> dict:
        out = {}
        for key in sorted({pick(cmap[c]) for c in real}):
            cids = [c for c in real if pick(cmap[c]) == key and c in resolved]
            if not cids:
                out[key] = {"n": 0, "raw": None, "kappa": None}
                continue
            pairs = [(cmap[c]["panel_verdict"], resolved[c]) for c in cids]
            k, po = cohens_kappa([p[0] for p in pairs], [p[1] for p in pairs])
            out[key] = {"n": len(cids), "raw": po, "kappa": k}
        return out
    report["by_stratum"] = slice_agreement(lambda m: m["stratum"])
    report["by_canonical_verdict"] = slice_agreement(lambda m: m["panel_verdict"])

    # ---- 11-12: label id, and the composite that makes CONFIRMED mean one defect.
    both_conf = [c for c in real
                 if cmap[c]["panel_verdict"] == "CONFIRMED"
                 and agg.get(c, {}).get("fresh_verdict") == "CONFIRMED"]
    if both_conf:
        lk, lpo = cohens_kappa([cmap[c]["panel_label_id"] for c in both_conf],
                               [agg[c]["fresh_label_id"] for c in both_conf])
        label_block = {"n": len(both_conf), "raw": lpo, "kappa": lk}
    else:
        label_block = {"n": 0, "raw": None, "kappa": None,
                       "note": "no case was CONFIRMED by both panels; nothing to compare"}
    report["label_id_agreement_given_confirmed"] = label_block

    comp = [c for c in real if c in resolved]
    if comp:
        ck, cpo = cohens_kappa(
            [_composite(cmap[c]["panel_verdict"], cmap[c]["panel_label_id"]) for c in comp],
            [_composite(resolved[c], agg[c]["fresh_label_id"]) for c in comp])
        report["composite_verdict_and_label_agreement"] = {
            "n": len(comp), "raw": cpo, "kappa": ck,
            "note": "a composite below the verdict figure means both panels said CONFIRMED "
                    "about different defects",
        }

    # ---- 13: does the consensus survive dropping any one juror, and does agreement?
    # Recomputing alpha without each family is what separates "this panel disagrees"
    # from "one lane disagrees with the panel". A single outlier drags a five-rater
    # alpha down hard, and the band would otherwise read as a verdict on all five.
    loo = {}
    for drop in sorted(by_family):
        changed, unresolved_now = 0, 0
        for cid in real:
            votes = [v["verdict"] for v in by_case[cid] if v["family"] != drop]
            if not votes:
                continue
            tally = Counter(votes)
            best = max(tally.values())
            leaders = [v for v, c in tally.items() if c == best]
            got = leaders[0] if (best > len(votes) / 2 and len(leaders) == 1) else UNRESOLVED
            was = agg.get(cid, {}).get("fresh_verdict", UNRESOLVED)
            if got != was:
                changed += 1
            if got == UNRESOLVED:
                unresolved_now += 1
        sub = [[v["verdict"] for v in by_case[cid] if v["family"] != drop]
               for cid in real if by_case.get(cid)]
        fk_sub, _ = fleiss_kappa(sub, VERDICTS)
        loo[drop] = {"consensus_changed": changed, "unresolved_without_it": unresolved_now,
                     "alpha_without_it": krippendorff_alpha_nominal(sub),
                     "fleiss_without_it": fk_sub}
    report["leave_one_juror_out"] = loo

    # ---- 14
    n_real = len([c for c in real if c in agg])
    n_unres = len([c for c in real if agg.get(c, {}).get("fresh_verdict") == UNRESOLVED])
    report["unresolved"] = {"n": n_unres, "of": n_real,
                            "rate": (n_unres / n_real) if n_real else None}

    # ---- 5.2: controls.
    # A control whose own expected answer is refuted after the freeze is retired, not
    # deleted and not silently kept. Keeping it scores the jurors who read the code
    # correctly as having failed; deleting it hides that the control set had an error
    # rate of its own. Accuracy is reported both ways.
    retired = dict(pair.split("=", 1) for pair in (args.retired_controls or []))
    all_ctrl = {r["case_id"]: r for r in read_jsonl(args.controls)} if args.controls else {}
    ctrl_rows = {k: v for k, v in all_ctrl.items() if k not in retired}
    per_family: dict[str, Counter] = defaultdict(Counter)
    per_category: dict[str, Counter] = defaultdict(Counter)
    ctrl_pairs: list[tuple[str, str]] = []
    failures = []
    for cid, meta in sorted(ctrl_rows.items()):
        want = meta["expected"]
        for v in by_case.get(cid, []):
            got = v["verdict"]
            ok = got == want and (want != "CONFIRMED"
                                  or v["label_id"] == (meta.get("expected_label_id") or ""))
            per_family[v["family"]]["n"] += 1
            per_family[v["family"]]["ok"] += int(ok)
            per_category[want]["n"] += 1
            per_category[want]["ok"] += int(ok)
            ctrl_pairs.append((want, got))
            if not ok:
                failures.append({"case_id": cid, "control_id": meta.get("control_id"),
                                 "expected": want, "family": v["family"], "got": got,
                                 "label_id": v["label_id"], "confidence": v["confidence"],
                                 "rationale": v["rationale"]})
    by_case_ctrl_fail = Counter(f["case_id"] for f in failures)
    report["controls"] = {
        "per_family": {f: {"n": c["n"], "correct": c["ok"],
                           "accuracy": c["ok"] / c["n"] if c["n"] else None}
                       for f, c in sorted(per_family.items())},
        "per_category": {k: {"n": c["n"], "correct": c["ok"],
                             "accuracy": c["ok"] / c["n"] if c["n"] else None,
                             "hard": k in ("CONFIRMED", "FABRICATED")}
                         for k, c in sorted(per_category.items())},
        "overall_accuracy": (sum(c["ok"] for c in per_category.values())
                             / max(sum(c["n"] for c in per_category.values()), 1)),
        "hard_overall_accuracy": (
            sum(c["ok"] for k, c in per_category.items() if k in ("CONFIRMED", "FABRICATED"))
            / max(sum(c["n"] for k, c in per_category.items()
                      if k in ("CONFIRMED", "FABRICATED")), 1)),
        "confusion_expected_rows": confusion(ctrl_pairs, list(VERDICTS)),
        "failures": failures,
        "cases_failed_by_three_or_more": sorted(
            c for c, n in by_case_ctrl_fail.items() if n >= 3),
        "retired": [{"case_id": cid, "control_id": all_ctrl[cid]["control_id"],
                     "expected_as_frozen": all_ctrl[cid]["expected"], "reason": why,
                     "votes": {v["family"]: v["verdict"] for v in by_case.get(cid, [])}}
                    for cid, why in sorted(retired.items()) if cid in all_ctrl],
        "accuracy_as_frozen": (
            sum(1 for cid, m in all_ctrl.items() for v in by_case.get(cid, [])
                if v["verdict"] == m["expected"]
                and (m["expected"] != "CONFIRMED"
                     or v["label_id"] == (m.get("expected_label_id") or "")))
            / max(sum(len(by_case.get(cid, [])) for cid in all_ctrl), 1)),
        "plausible_is_soft": "PLAUSIBLE control accuracy is reported, never a hard stop: "
                             "its gold is weaker without a human.",
        "confidence_calibration": _calibration([
            (v["confidence"], v["verdict"] == ctrl_rows[cid]["expected"])
            for cid in ctrl_rows for v in by_case.get(cid, [])]),
    }

    # ---- the eight canonical needs_human claims, recommended and never written back.
    report["canonical_needs_human"] = [
        {"case_id": cid, "canonical_status": "needs_human",
         "automated_consensus": agg.get(cid, {}).get("fresh_verdict", "NOT_JUDGED"),
         "automated_vote_split": dict(Counter(
             v["verdict"] for v in by_case.get(cid, []))),
         "canonical_mutated": False}
        for cid in sorted(real + supplement) if cmap[cid].get("panel_needs_human")
    ]

    # ---- band.
    alpha = report["fresh_five"]["krippendorff_alpha_nominal"]
    ck_res = report["canonical_vs_fresh"]["majority_resolved_only"]["kappa"]
    hard = report["controls"]["hard_overall_accuracy"]
    hard_min = min((c["accuracy"] for k, c in report["controls"]["per_category"].items()
                    if c["hard"] and c["accuracy"] is not None), default=None)
    unres_rate = report["unresolved"]["rate"]
    report["band"] = _band(alpha, ck_res, hard, hard_min, unres_rate)
    report["stage_b_triggers"] = _stage_b(report, cmap, agg, real)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(f"band {report['band']['band']}  "
          f"alpha {_fmt(alpha)}  canonical-vs-fresh kappa {_fmt(ck_res)}  "
          f"hard controls {_fmt(hard)}")
    for line in report["band"]["reasons"]:
        print(f"  {line}")
    print(f"stage B triggered: {report['stage_b_triggers']['triggered']}")
    for line in report["stage_b_triggers"]["reasons"]:
        print(f"  trigger {line}")
    print(f"wrote {args.out}")
    return 0


def _fmt(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.3f}"


def _calibration(pairs: list[tuple[float, bool]]) -> dict:
    """Mean asserted confidence against observed correctness, in four bins."""
    bins = {"0.00-0.50": [], "0.50-0.75": [], "0.75-0.90": [], "0.90-1.00": []}
    for conf, ok in pairs:
        key = ("0.00-0.50" if conf < 0.5 else "0.50-0.75" if conf < 0.75
               else "0.75-0.90" if conf < 0.90 else "0.90-1.00")
        bins[key].append((conf, ok))
    return {k: {"n": len(v),
                "mean_confidence": round(sum(c for c, _ in v) / len(v), 3) if v else None,
                "accuracy": round(sum(1 for _, o in v if o) / len(v), 3) if v else None}
            for k, v in bins.items()}


def _band(alpha, canon_kappa, hard_overall, hard_min, unresolved_rate) -> dict:
    """The engineering bands from the audit design. Not the preregistered gate."""
    reasons: list[str] = []
    def num(x):
        return x if isinstance(x, (int, float)) and not math.isnan(x) else None
    a, k = num(alpha), num(canon_kappa)
    red = []
    if a is not None and a < 0.55:
        red.append(f"fresh-five alpha {a:.3f} < 0.55")
    if k is not None and k < 0.55:
        red.append(f"canonical-vs-fresh kappa {k:.3f} < 0.55")
    if hard_overall is not None and hard_overall < 0.90:
        reasons.append(f"hard-control accuracy {hard_overall:.3f} below the 0.90 GREEN floor")
    if hard_min is not None and hard_min < 0.80:
        red.append(f"a hard control category is at {hard_min:.3f} < 0.80")
    if unresolved_rate is not None and unresolved_rate > 0.10:
        reasons.append(f"unresolved rate {unresolved_rate:.1%} over 10%")
    if red:
        return {"band": "RED", "reasons": red + reasons}
    green = (a is not None and a >= 0.70 and k is not None and k >= 0.70
             and hard_overall is not None and hard_overall >= 0.90
             and (hard_min is None or hard_min >= 0.80))
    if green and not reasons:
        return {"band": "GREEN", "reasons": ["every band threshold met"]}
    return {"band": "YELLOW", "reasons": reasons or ["agreement between 0.55 and 0.70"]}


def _stage_b(report: dict, cmap: dict, agg: dict, real: list[str]) -> dict:
    """Whichever of the conditional-expansion triggers actually fired."""
    reasons = []
    a = report["fresh_five"]["krippendorff_alpha_nominal"]
    k = report["canonical_vs_fresh"]["majority_resolved_only"]["kappa"]
    if isinstance(a, float) and not math.isnan(a) and a < 0.70:
        reasons.append(f"fresh-five alpha {a:.3f} < 0.70")
    if isinstance(k, float) and not math.isnan(k) and k < 0.70:
        reasons.append(f"canonical-vs-fresh kappa {k:.3f} < 0.70")
    lost = [c for c in real
            if cmap[c]["panel_verdict"] == "CONFIRMED"
            and agg.get(c, {}).get("fresh_verdict") not in ("CONFIRMED", None)]
    if lost:
        reasons.append(f"{len(lost)} canonical CONFIRMED claims lost confirmation: {lost[:6]}")
    relabelled = [c for c in real
                  if cmap[c]["panel_verdict"] == "CONFIRMED"
                  and agg.get(c, {}).get("fresh_verdict") == "CONFIRMED"
                  and agg[c]["fresh_label_id"] != cmap[c]["panel_label_id"]]
    if relabelled:
        reasons.append(f"{len(relabelled)} confirmed label ids changed: {relabelled[:6]}")
    flipped = [c for c in real
               if str(cmap[c].get("severity")) in ("0", "1")
               and c in agg and agg[c]["fresh_verdict"] != UNRESOLVED
               and agg[c]["fresh_verdict"] != cmap[c]["panel_verdict"]]
    if flipped:
        reasons.append(f"{len(flipped)} P0/P1 verdicts changed: {flipped[:6]}")
    hard_fail = [k2 for k2, c in report["controls"]["per_category"].items()
                 if c["hard"] and c["accuracy"] is not None and c["accuracy"] < 1.0]
    if hard_fail:
        reasons.append(f"hard control categories with a failure: {hard_fail}")
    ur = report["unresolved"]["rate"]
    if ur is not None and ur > 0.10:
        reasons.append(f"unresolved rate {ur:.1%} > 10%")
    return {"triggered": bool(reasons), "reasons": reasons,
            "expansion_set": "all canonical CONFIRMED + all canonical FABRICATED + all P0/P1 "
                             "+ the eight needs_human + every unique_contribution claim "
                             "+ anything implicated in an execution contradiction"}


# ----------------------------------------------------------------- budget

def project_cost(assignments: list[dict], prices: dict[str, dict[str, float]],
                 in_tokens: int, out_tokens: int) -> dict:
    """Projected list cost per family, before any call is made.

    A subscription route that reports $0 charged still consumes list-priced tokens,
    and the cap is a cap on exposure rather than on the invoice, so both figures are
    kept. `prices` is per million tokens.
    """
    per_family: dict[str, dict[str, float]] = {}
    for fam, n in Counter(a["family"] for a in assignments).items():
        p = prices.get(fam) or {}
        cost = n * (in_tokens * p.get("input", 0.0) + out_tokens * p.get("output", 0.0)) / 1e6
        per_family[fam] = {"calls": n, "projected_usd": round(cost, 2),
                           "metered": bool(p.get("metered", True))}
    total = round(sum(v["projected_usd"] for v in per_family.values()), 2)
    return {"per_family": per_family, "projected_total_usd": total,
            "assumed_input_tokens": in_tokens, "assumed_output_tokens": out_tokens}


def cmd_budget(args) -> int:
    assignments = read_jsonl(args.manifest)
    prices = json.loads(Path(args.prices).read_text())
    spent = 0.0
    if args.ledger and Path(args.ledger).exists():
        spent = sum(float(r.get("cost_usd_listed") or 0.0) for r in read_jsonl(args.ledger))
    proj = project_cost(assignments, prices, args.input_tokens, args.output_tokens)
    cumulative = round(spent + proj["projected_total_usd"], 2)
    proj["already_spent_usd"] = round(spent, 2)
    proj["projected_cumulative_usd"] = cumulative
    proj["hard_cap_usd"] = args.hard_cap_usd
    proj["soft_alert_usd"] = args.soft_alert_usd
    proj["stop_threshold_usd"] = args.stop_threshold_usd
    over = cumulative > args.hard_cap_usd
    at_stop = cumulative >= args.stop_threshold_usd
    proj["within_cap"] = not over
    proj["stop_condition_met"] = at_stop
    if args.out:
        Path(args.out).write_text(json.dumps(proj, indent=2, sort_keys=True) + "\n")

    for fam, v in sorted(proj["per_family"].items()):
        meter = "metered" if v["metered"] else "subscription (list estimate)"
        print(f"  {fam:<8} {v['calls']:>4} calls  ${v['projected_usd']:>7.2f}  {meter}")
    print(f"  {'spent':<8} {'':>4}        ${spent:>7.2f}")
    print(f"  {'TOTAL':<8} {len(assignments):>4} calls  ${cumulative:>7.2f}  "
          f"cap ${args.hard_cap_usd:.0f}")
    if over:
        print(f"REFUSED: projected cumulative ${cumulative:.2f} exceeds the hard cap "
              f"${args.hard_cap_usd:.2f}", file=sys.stderr)
        return 2
    if at_stop:
        print(f"STOP: projected cumulative ${cumulative:.2f} reaches the "
              f"${args.stop_threshold_usd:.2f} stop threshold before the next batch",
              file=sys.stderr)
        return 2
    if cumulative > args.soft_alert_usd:
        print(f"  soft alert: over ${args.soft_alert_usd:.0f}")
    return 0


# ----------------------------------------------------------------- verify

def cmd_verify(args) -> int:
    out = Path(args.dir)
    manifest_path = out / "MANIFEST.sha256"
    problems: list[str] = []

    recorded = {}
    for line in manifest_path.read_text().splitlines():
        if not line.strip():
            continue
        digest, name = line.split("  ", 1)
        recorded[name] = digest
    for name, digest in sorted(recorded.items()):
        p = out / name
        if not p.is_file():
            problems.append(f"{name} is in the manifest and missing from disk")
        elif sha256_file(p) != digest:
            problems.append(f"{name} does not match its frozen digest")

    build = json.loads((out / "build-manifest.json").read_text())
    frozen_at = build["frozen_at"]
    # A control whose answer was known before it was frozen is not a control. The
    # cheapest check that the freeze came first is that no response predates it.
    resp_dir = out / "responses"
    early = []
    if resp_dir.is_dir():
        for f in sorted(resp_dir.glob("*.jsonl")):
            for r in read_jsonl(f):
                started = str(r.get("started_at") or "")
                if started and started < frozen_at:
                    early.append(f"{f.name}:{r.get('assignment_id')} started {started}")
    if early:
        problems.append(f"{len(early)} response(s) predate the freeze at {frozen_at}")

    receipt = {}
    for spec in args.canonical or []:
        path, _, expect = spec.partition("=")
        got = sha256_file(Path(path))
        receipt[path] = got
        if expect and got != expect:
            problems.append(f"CANONICAL MUTATED: {path}")

    print(f"manifest entries {len(recorded)} | frozen_at {frozen_at}")
    print(f"responses predating the freeze: {len(early)}")
    print(f"canonical artifacts checked: {len(receipt)}")
    if problems:
        for line in problems[:12]:
            print(f"  FAIL {line}", file=sys.stderr)
        return 2
    print("  ok  every digest matches, no response predates the freeze")
    if args.receipt:
        Path(args.receipt).write_text(json.dumps(
            {"verified_at": now_utc(), "frozen_at": frozen_at,
             "manifest_entries": len(recorded),
             "responses_predating_freeze": len(early),
             "canonical_artifacts": receipt,
             "canonical_artifacts_mutated": False,
             "human_judge_reliability": {"status": "not_measured",
                                         "preregistered_gate_closed": False}},
            indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.receipt}")
    return 0


# -------------------------------------------------------------------- cli

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="freeze cases, controls, prompts and assignments")
    b.add_argument("--selection", type=Path, required=True,
                   help="the frozen sample; only its (run_id, claim_rid) keys are read")
    b.add_argument("--claims", type=Path, required=True)
    b.add_argument("--corpus", type=Path, required=True)
    b.add_argument("--judge", type=Path, required=True, help="canonical aggregate")
    b.add_argument("--controls", type=Path, default=None, help="control spec, yml or jsonl")
    b.add_argument("--supplement", type=Path, default=None,
                   help="extra claims to judge outside the frozen sample, keyed the same "
                        "way; reported separately and kept out of the headline denominators")
    b.add_argument("--families", nargs="*", default=None)
    b.add_argument("--reps", type=int, default=2,
                   help="passes for the two families with no old votes to compare against")
    b.add_argument("--seed", type=int, default=20260728)
    b.add_argument("--out", type=Path, required=True)
    b.set_defaults(fn=cmd_build)

    i = sub.add_parser("ingest", help="validate dispatcher responses into fresh judgements")
    i.add_argument("--manifest", type=Path, required=True)
    i.add_argument("--cases", type=Path, required=True)
    i.add_argument("--responses", type=Path, required=True)
    i.add_argument("--out", type=Path, required=True)
    i.set_defaults(fn=cmd_ingest)

    g = sub.add_parser("aggregate", help="fresh-panel consensus, 3 of 5, no tie-order")
    g.add_argument("--fresh", type=Path, required=True)
    g.add_argument("--case-map", type=Path, required=True)
    g.add_argument("--manifest", type=Path, required=True)
    g.add_argument("--panel-size", type=int, default=5)
    g.add_argument("--min-majority", type=int, default=3)
    g.add_argument("--allow-partial", action="store_true",
                   help="aggregate an incomplete panel; for diagnostics only")
    g.add_argument("--out", type=Path, required=True)
    g.set_defaults(fn=cmd_aggregate)

    a = sub.add_parser("analyze", help="agreement, controls, sensitivity, band")
    a.add_argument("--case-map", type=Path, required=True)
    a.add_argument("--retired-controls", nargs="*", default=None, metavar="CASE_ID=REASON",
                   help="controls whose own expected answer was refuted after the freeze; "
                        "excluded from accuracy and reported with their reason and votes")
    a.add_argument("--fresh", type=Path, required=True)
    a.add_argument("--aggregate", type=Path, required=True)
    a.add_argument("--controls", type=Path, default=None)
    a.add_argument("--canonical-raw", type=Path, default=None,
                   help="per-judge canonical rows, for old-vs-fresh repeatability")
    a.add_argument("--min-majority", type=int, default=3)
    a.add_argument("--out", type=Path, required=True)
    a.set_defaults(fn=cmd_analyze)

    u = sub.add_parser("budget", help="project spend and fail closed over the cap")
    u.add_argument("--manifest", type=Path, required=True)
    u.add_argument("--prices", type=Path, required=True)
    u.add_argument("--ledger", type=Path, default=None)
    u.add_argument("--input-tokens", type=int, default=20000)
    u.add_argument("--output-tokens", type=int, default=1200)
    u.add_argument("--hard-cap-usd", type=float, default=200.0)
    u.add_argument("--soft-alert-usd", type=float, default=100.0)
    u.add_argument("--stop-threshold-usd", type=float, default=175.0)
    u.add_argument("--out", type=Path, default=None)
    u.set_defaults(fn=cmd_budget)

    v = sub.add_parser("verify", help="digests, freeze precedence, canonical receipt")
    v.add_argument("--dir", type=Path, required=True)
    v.add_argument("--canonical", nargs="*", default=None, metavar="PATH[=SHA256]",
                   help="canonical artifacts that must not have moved")
    v.add_argument("--receipt", type=Path, default=None)
    v.set_defaults(fn=cmd_verify)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
