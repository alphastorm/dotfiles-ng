#!/usr/bin/env python3
"""
build_corpus.py -- assemble the LRHE corpus from public sources.

Needs network access and, for some sources, a GitHub token. Nothing here touches
private repositories.

Sources, and what each contributes:

  S1 REVIEW_HUMAN   SWE-PRBench (HuggingFace: foundry-ai/swe-prbench)
                    350 merged PRs; ground truth = real human review comments.
                    Difficulty field Type1_Direct / Type2_Contextual /
                    Type3_Latent maps onto the three lenses.
                    Harness: github.com/FoundryHQ-AI/swe-prbench

  S2 PATCH_VERDICT  SWE-bench-Live (HF: SWE-bench-Live/SWE-bench-Live, split
                    "full" for the freshest instances) crossed with candidate
                    patches from github.com/SWE-bench/experiments.
                    Label = harness FAIL_TO_PASS verdict. EXECUTABLE.
                    Deliberately avoids SWE-bench Verified: ~1/3 of Verified
                    issues leak solution code in the issue text and >94% predate
                    current model cutoffs.

  S3 VULN_POC       ARVO (github.com/n132/ARVO-Meta, arvo.db on the release
                    page; docker images on Docker Hub).
                    Label = PoC reproduces. EXECUTABLE. Prefer the falsely-patched
                    subset: real developer patch, crash still reachable.

  S4 FP_TRAP        Disclosed-invalid HackerOne reports (arXiv:2511.18608 corpus)
                    and inverted ARVO true-fix labels. Label = finding is invalid.

  S5 NULL           Merged PRs with no revert and no follow-up fix within 90d.

Usage:
  build_corpus.py plan                     # print the sampling plan, no network
  build_corpus.py assignments --out a.csv  # Latin-square run matrix, no network
  build_corpus.py fetch --stratum S1 ...   # requires network
  build_corpus.py scrub --in raw/ --out corpus.jsonl
  build_corpus.py probes --corpus corpus.jsonl --out probes/
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# The council is not symmetric. Three roles, and conflating them is what makes a
# six-family setup look like six votes when it is four first-pass critics, one
# conditional refuter, and one accountable integrator.
#
#   author   runs arm A and wrote the work the others review; never judges itself
#   critic   first-pass parallel review under a rotating lens (arms B, C, D, probe)
#   refuter  cold falsification of disputed P0/P1 only -- see judge_lrhe.py refute
#
# Adding a critic scales arms C and D linearly. Adding a refuter costs nothing per
# item, because it is invoked per disputed claim rather than per review.
# These are read from panels.yaml, not declared here. A panel edited in source is
# a design change with no artifact and no digest; section 5.6 of the OpenCode
# handoff asks for exactly this move. There is deliberately no fallback constant:
# a second definition that only appears when the config is missing is how the two
# come to disagree, silently, in whichever direction nobody checked.
PANELS_PATH = Path(__file__).parent / "panels.yaml"
DEFAULT_EXPERIMENT = "lrhe-core-expanded-v1"


def load_panels(path: Path = PANELS_PATH) -> dict:
    import yaml
    return yaml.safe_load(path.read_text())


def panel(experiment_id: str, panels: dict | None = None) -> dict:
    panels = panels or load_panels()
    for exp in panels["experiments"]:
        if exp["experimentId"] == experiment_id:
            return exp
    known = ", ".join(e["experimentId"] for e in panels["experiments"])
    raise SystemExit(f"unknown experiment {experiment_id!r}; panels.yaml defines: {known}")


_DEFAULT = panel(DEFAULT_EXPERIMENT)
FAMILIES = [f["family"] for f in _DEFAULT["families"]]
AUTHOR_FAMILY = _DEFAULT["authorFamily"]
REFUTER_FAMILY = _DEFAULT["refuterFamily"]
LENSES = _DEFAULT["lenses"]


def lens_sets(families: list[str], lenses: list[str] | None = None) -> list[dict[str, str]]:
    """Counterbalanced lens assignment for any number of families.

    `lenses[(i - s) % L]` for family index i and set s. Every family draws every
    lens exactly once across the L sets, which is the property that separates the
    family effect from the lens effect -- the whole point of arm D, and the reason
    a model and its role never become permanently confounded.

    At three families and three lenses this reproduces the protocol's Latin square
    verbatim. At four of each it is a full 4x4 square. When F and L differ it
    degrades to a Youden-style rotation: every family still sees every lens, and
    each lens is used floor(F/L) or ceil(F/L) times per set instead of exactly once.

    What does NOT scale is the reading of the results. More reviewers inflate
    "unique verified findings" by pure arithmetic -- section 7's warning gets worse,
    not better -- so arm T grows with the council or the comparison stops meaning
    anything.
    """
    lenses = lenses or LENSES
    return [{f: lenses[(i - s) % len(lenses)] for i, f in enumerate(families)}
            for s in range(len(lenses))]


LENS_SETS = lens_sets(FAMILIES)

# ---------------------------------------------------------------- jsonl io

def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _write_jsonl(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(it, sort_keys=True) + "\n" for it in items))


# ---------------------------------------------------------------- sampling plan

@dataclass(frozen=True)
class StratumPlan:
    name: str
    n: int
    source: str
    dataset: str
    label_semantics: str
    executable: bool
    sampling: str


PLAN = [
    StratumPlan(
        "S1_REVIEW_HUMAN", 14, "swe-prbench", "hf://foundry-ai/swe-prbench",
        "human reviewer flagged it", False,
        "4 Type1_Direct / 5 Type2_Contextual / 5 Type3_Latent; over-weight Type2+Type3 "
        "(dataset prevalence 21% / 12%) because Type1 does not discriminate between lenses",
    ),
    StratumPlan(
        "S2_PATCH_VERDICT", 10, "swe-bench-live+experiments",
        "hf://SWE-bench-Live/SWE-bench-Live[full] x gh://SWE-bench/experiments",
        "hidden FAIL_TO_PASS test fails", True,
        "5 known-broken candidate patches + 5 known-passing controls; drop empty and "
        "comment-only patches; pick submissions with moderate resolve rates so labels balance",
    ),
    StratumPlan(
        "S3_VULN_POC", 8, "arvo", "gh://n132/ARVO-Meta (arvo.db)",
        "PoC still crashes at the reviewed revision", True,
        "5 incomplete-or-unfixed (prefer the falsely-patched subset) + 3 correctly-fixed controls",
    ),
    StratumPlan(
        "S4_FP_TRAP", 12, "h1-invalid+arvo-inverted",
        "arXiv:2511.18608 disclosed-invalid corpus; ARVO true-fix labels inverted",
        "the asserted finding is invalid", True,
        "12 not 5: at 5 items x 3 lens-sets you get ~15 runs/family, SE ~0.12 on a 0.35 "
        "bait rate -- enough to show the council takes bait, not enough to rank families. "
        "Trap packets need no build environment so the marginal cost is ~0",
    ),
    StratumPlan(
        "S5_NULL", 3, "clean-merged", "gh:// search",
        "nothing to find", False,
        "merged, no revert and no follow-up fix commit within 90 days; interleaved and unlabeled",
    ),
]


def cmd_plan(_args) -> int:
    total = sum(p.n for p in PLAN)
    print(f"LRHE corpus plan: {total} items\n")
    for p in PLAN:
        print(f"  {p.name:<18} n={p.n:<3} exec={'yes' if p.executable else 'no ':<3} {p.dataset}")
        print(f"  {'':<18} label: {p.label_semantics}")
        for line in _wrap(p.sampling, 88):
            print(f"  {'':<18} {line}")
        print()
    print("Date gate: require fix/merge date > the LATEST cutoff among ALL participating")
    print("families. Gating per-family reintroduces the asymmetry the gate exists to remove.")
    return 0


def _wrap(s: str, w: int) -> list[str]:
    out, cur = [], ""
    for word in s.split():
        if len(cur) + len(word) + 1 > w:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- assignments

def _stable_hash(s: str, salt: str = "") -> int:
    """Digest-derived index. Python's built-in hash() is salted per process, so the
    same corpus would otherwise produce a different matrix on every run.

    An empty salt hashes the value alone. That is the form every assignment frozen
    so far was generated with, so do NOT "simplify" this into an unconditional
    prefix: every previously recorded arm-B mapping would silently move. A non-empty
    salt is how you deliberately reshuffle, and it is written into the manifest so
    the reshuffle is a declared act rather than an unexplained difference.
    """
    payload = f"{salt}\0{s}" if salt else s
    return int(hashlib.sha256(payload.encode()).hexdigest()[:8], 16)


def _sha256_path(p: Path | None) -> str | None:
    if p is None or not Path(p).exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _subset_key(item: dict) -> str:
    """Stratification key for the arm-D / arm-T subset.

    Stratum alone is not enough. Arm D is the Latin square and arm T is the
    empirical null; if either lands only on defect-bearing items then the
    false-positive controls -- the S4 traps, and the label-free known-passing and
    null items -- are never exercised under the conditions the headline numbers come
    from. Finding nothing when there is nothing is a different measurement from
    missing something, and only one of the two is checked by recall.
    """
    stratum = item.get("stratum", "")
    if item.get("trap"):
        kind = "trap"
    elif not item.get("labels"):
        kind = "control"
    else:
        kind = item.get("difficulty") or "unspecified"
    return f"{stratum}|{kind}"


def _stratified_subset(items: list[tuple[str, str]], k: int, salt: str = "") -> set[str]:
    """Pick k item ids spread proportionally across the given keys, deterministically.

    Apportionment, not round-robin. Quotas of `max(1, round(...))` routinely sum to
    more than k, and draining them one pass at a time in sorted key order means
    whichever group sorts last absorbs the entire shortfall. That is invisible while
    the key is just the stratum and there are five groups; add difficulty and
    control status and it silently halved S4 trap representation in the subset that
    carries arm T -- the one arm where a false positive can be observed at all.

    Coverage first, proportionality second: every group takes one slot while slots
    remain, largest group first, and the rest go to whoever is furthest below their
    exact share. Ties break on the salted digest, so the outcome is reproducible
    without being alphabetical.
    """
    if k >= len(items):
        return {iid for iid, _ in items}

    groups: dict[str, list[str]] = {}
    for iid, key in items:
        groups.setdefault(key, []).append(iid)
    for ids in groups.values():
        ids.sort(key=lambda i: _stable_hash(i, salt))

    keys = sorted(groups)
    exact = {key: k * len(groups[key]) / len(items) for key in keys}
    alloc = {key: 0 for key in keys}

    for key in sorted(keys, key=lambda key: (-len(groups[key]), _stable_hash(key, salt))):
        if sum(alloc.values()) >= k:
            break
        alloc[key] = 1

    while sum(alloc.values()) < k:
        short = [key for key in keys if alloc[key] < len(groups[key])]
        if not short:
            break
        best = max(short, key=lambda key: (exact[key] - alloc[key], _stable_hash(key, salt)))
        alloc[best] += 1

    return {iid for key in keys for iid in groups[key][: alloc[key]]}


def cmd_assignments(args) -> int:
    """Emit the full run matrix. No network needed; do this before fetching anything
    so the budget is visible up front."""
    if args.corpus and Path(args.corpus).exists():
        corpus_items = _read_jsonl(args.corpus)
    else:
        corpus_items = [{"item_id": f"{p.name[:2]}-{i + 1:04d}", "stratum": p.name}
                        for p in PLAN for i in range(p.n)]
    items = [(it["item_id"], it.get("stratum", "")) for it in corpus_items]

    # The arm-D subset must be stratified. Taking the first 24 in file order gives
    # the Latin square and arm T to whichever strata happen to sort first, and arm
    # T is the empirical null the whole diversity claim rests on -- it has to span
    # the same strata, difficulties and control types the cross-family arms are
    # measured on.
    d_subset = _stratified_subset(
        [(it["item_id"], _subset_key(it)) for it in corpus_items],
        args.d_items, args.assignment_salt,
    )

    exp = panel(args.experiment_id)
    families = args.families or [f["family"] for f in exp["families"]]
    lenses = args.lenses or exp["lenses"]
    author_family = args.author_family or exp["authorFamily"]
    null_family = args.triplicate_family or exp.get("nullFamily") or families[0]
    sets = lens_sets(families, lenses)
    rows = []
    for iid, stratum in items:
        rows.append({"item_id": iid, "stratum": stratum, "arm": "A",
                     "family": author_family, "lens": "", "replicate": ""})
        # Arm B rotates the single critic so no family is confounded with item.
        # Built-in hash() is salted per process, so the same corpus would produce
        # a different matrix on every run and the design would not be replicable.
        rows.append({"item_id": iid, "stratum": stratum, "arm": "B",
                     "family": families[_stable_hash(iid, args.assignment_salt) % len(families)],
                     "lens": "", "replicate": ""})
        for f in families:
            rows.append({"item_id": iid, "stratum": stratum, "arm": "C",
                         "family": f, "lens": "floor", "replicate": ""})
        if iid in d_subset:
            for si, lset in enumerate(sets):
                for f in families:
                    rows.append({"item_id": iid, "stratum": stratum, "arm": "D",
                                 "family": f, "lens": lset[f], "replicate": f"set{si+1}"})
            # Arm T: the empirical null. One family, run independently as many times
            # as the council has members, so the comparison is like-for-like -- a
            # 3-run triplicate is not the null for a 6-family council.
            for r in range(args.triplicate_n or len(families)):
                rows.append({"item_id": iid, "stratum": stratum, "arm": "T",
                             "family": null_family, "lens": "floor",
                             "replicate": f"rep{r+1}"})
        # Contamination probe: no repository, no tools, title+description only.
        for f in families:
            rows.append({"item_id": iid, "stratum": stratum, "arm": "probe",
                         "family": f, "lens": "", "replicate": ""})

    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["item_id", "stratum", "arm", "family", "lens", "replicate"])
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    c = Counter(r["arm"] for r in rows)

    # The immutable half of the assignment. assignments.csv can be regenerated,
    # reordered or hand-edited and nothing would notice; these digests are what make
    # that detectable. The salt and the selected subset are what let someone without
    # this machine reproduce the matrix -- which is the entire point of freezing it
    # before any output exists.
    manifest = {
        "experiment_id": exp["experimentId"],
        "panel_id": exp["panelId"],
        "prompt_version": exp["promptVersion"],
        "subset_policy": "stratified",
        "assignment_salt": args.assignment_salt,
        "d_items_requested": args.d_items,
        "d_subset": sorted(d_subset),
        "families": families,
        "lenses": lenses,
        "author_family": author_family,
        "triplicate_family": null_family,
        "triplicate_n": args.triplicate_n or len(families),
        "n_items": len(items),
        "n_rows": len(rows),
        "arm_counts": {a: c[a] for a in sorted(c)},
        "corpus_sha256": _sha256_path(args.corpus),
        "assignments_sha256": _sha256_path(args.out),
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(f"wrote {args.out}: {len(rows)} scheduled runs")
    for arm in ["A", "B", "C", "D", "T", "probe"]:
        print(f"  arm {arm:<6} {c[arm]:>4}")
    print(f"\nreviewer runs (excl. probe): {sum(c[a] for a in 'ABCDT')}")
    print(f"probe runs (short, single-turn): {c['probe']}")
    print(f"wrote {args.manifest}: {len(d_subset)}-item subset, "
          f"assignments sha256 {manifest['assignments_sha256'][:12]}")
    if c["T"] == 0:
        print("\nWARNING: arm T empty. Marginal-contribution numbers will be uninterpretable.")
    return 0


# ---------------------------------------------------------------- scrubber

_SCRUB = [
    (re.compile(r"https?://\S+"), "[url]"),
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "[sha]"),
    (re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I), "[cve]"),
    (re.compile(r"\bGHSA-[\w-]{11,}\b", re.I), "[advisory]"),
    (re.compile(r"#\d{2,7}\b"), "[issue]"),
    (re.compile(r"\bOSS-Fuzz\s*(issue|bug)?\s*#?\d+\b", re.I), "[oss-fuzz-id]"),
    (re.compile(r"^\s*Revert\s+\"", re.M), '"'),
    (re.compile(r"\b(fixes|closes|resolves)\s+\[issue\]", re.I), "addresses [issue]"),
]


def scrub_text(s: str) -> str:
    for pat, rep in _SCRUB:
        s = pat.sub(rep, s)
    return s


# A diff is the artifact under review, not prose about it. Running the prose
# scrubber over it rewrites code: a bare hex token in a fixture becomes `[sha]`,
# a URL in a comment vanishes, and the reviewer is now reading something the
# maintainers never wrote. Strip only what carries provenance and no review
# signal -- the `index <sha>..<sha>` metadata line git regenerates anyway.
_DIFF_INDEX_RE = re.compile(r"^index [0-9a-f]{7,40}\.\.[0-9a-f]{7,40}( \d{6})?$", re.M)
_DIFF_SCRUB = [
    (_DIFF_INDEX_RE, "index [sha]..[sha]"),
    (re.compile(r"https?://\S*(github\.com|gitlab|bitbucket|issues?|bugs?)\S*"), "[url]"),
    (re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I), "[cve]"),
    (re.compile(r"\bGHSA-[\w-]{11,}\b", re.I), "[advisory]"),
]


def scrub_diff(s: str) -> str:
    for pat, rep in _DIFF_SCRUB:
        s = pat.sub(rep, s)
    return s


PROSE_FIELDS = ("goal", "problem_statement", "known_open_questions")


def cmd_scrub(args) -> int:
    """Strip surface identifiers that enable string-level recall.

    Reduces string recall, not conceptual recall. Keep 6 items in both scrubbed
    and unscrubbed form and measure the scrub's own effect before trusting it --
    paraphrase can degrade the task as well as the leakage.
    """
    n = 0
    out = []
    for line in Path(args.inp).read_text().splitlines():
        if not line.strip():
            continue
        it = json.loads(line)
        for field in PROSE_FIELDS:
            if isinstance(it.get(field), str):
                it[field] = scrub_text(it[field])
        if isinstance(it.get("design_or_diff"), str):
            it["design_or_diff"] = scrub_diff(it["design_or_diff"])
        # Provenance the reviewer must never see. `repo` plus `review_commit` is a
        # one-line search away from the upstream fix, which defeats the scrub
        # entirely; `build_notes` literally records the harness verdict; `labels`
        # and `trap.ground_truth` are the answer key. Kept in the scored corpus,
        # stripped from the dispatched projection.
        if args.rename:
            h = hashlib.sha256((args.salt + it["item_id"]).encode()).hexdigest()[:8]
            it["source_item_id"] = it["item_id"]
            it["item_id"] = f"{it.get('stratum','X')[:2]}-{h}"
        it["scrubbed"] = True
        out.append(it)
        n += 1
    Path(args.out).write_text("".join(json.dumps(it, sort_keys=True) + "\n" for it in out))
    print(f"scrubbed {n} items -> {args.out}")
    if args.dispatch_out:
        packets = [_dispatch_view(it) for it in out]
        Path(args.dispatch_out).write_text(
            "".join(json.dumps(p, sort_keys=True) + "\n" for p in packets))
        print(f"wrote {len(packets)} reviewer-safe packets -> {args.dispatch_out}")
    print("Reminder: this reduces string-level recall only. The probe pass in section 3")
    print("is the control that actually measures leakage; run it per (item, family).")
    return 0


# Everything a reviewer legitimately needs, and nothing that answers the question
# for them. The projection below is an allowlist, so a field nobody classified is
# withheld by default -- but "withheld because nobody thought about it" and
# "withheld on purpose" look identical from here, and only one of them is a
# decision. `_WITHHELD_KEYS` names the second so a cross-file test can insist that
# every property in `item.schema.json` appears in exactly one of these two sets.
# Add a field to the item schema and the suite fails until you have said which it
# is; that is the only moment anyone is thinking about it.
_DISPATCH_KEYS = (
    "item_id", "stratum", "artifact_digest", "repo_files", "goal", "problem_statement",
    "design_or_diff", "known_open_questions", "tests_already_run",
    "provider_data_allowlist", "license",
)

# Withheld, with the reason. `repo` plus `review_commit` or `base_commit` is one
# search away from the upstream fix, which defeats the scrub entirely. `labels` and
# `trap` are the answer key -- the trap's assertion is projected into
# `known_open_questions` as bait, the rest of it is the answer. `build_notes` records
# the harness's own verdict. The remainder is provenance and sampling bookkeeping: it
# tells a reviewer which dataset an item came from and when, which is a retrieval
# hint and never evidence.
_WITHHELD_KEYS = (
    "repo", "base_commit", "review_commit", "changed_file_digests",
    "labels", "trap", "build_notes", "difficulty",
    "dataset_ref", "source", "source_item_id", "date_gate_cutoff", "merged_at",
    "license_url", "scrubbed",
)


def _dispatch_view(it: dict) -> dict:
    p = {k: it[k] for k in _DISPATCH_KEYS if k in it}
    trap = it.get("trap")
    if trap:
        # The trap's assertion is the bait and belongs in the packet. Its
        # ground_truth, invalid_reason and verify_cmd are the answer.
        p["known_open_questions"] = (
            (p.get("known_open_questions", "") + "\n\n" + trap["assertion"]).strip())
    return p


# ---------------------------------------------------------------- probe packets

PROBE_TEMPLATE = """\
You are given only the title and description of a reported problem. You have no
repository access and no tools.

Title: {title}

Description:
{description}

State the single file path and line range where the defect is located, and the
one-line change required to fix it. If you do not know, reply exactly: UNKNOWN.

Respond as JSON: {{"path": ..., "lines": ..., "fix": ...}} or {{"unknown": true}}
"""


def cmd_probes(args) -> int:
    """Emit the contamination-probe prompts.

    The probe is the only control here that measures leakage directly rather than
    hoping a date filter handled it. Cost is ~120 short single-turn calls for a
    40-item corpus. Score `probe_localized = 1` when the named path matches a
    labeled site; feed the result to analyze_lrhe.py --probe.
    """
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for line in Path(args.corpus).read_text().splitlines():
        if not line.strip():
            continue
        it = json.loads(line)
        title = it.get("goal") or it.get("title") or it["item_id"]
        desc = it.get("problem_statement", "")[: args.max_chars]
        (outdir / f"{it['item_id']}.probe.txt").write_text(
            PROBE_TEMPLATE.format(title=title, description=desc)
        )
        n += 1
    (outdir / "SCORING.md").write_text(
        "probe_localized = 1 if the returned path matches any labeled site path for the\n"
        "item (suffix match), AND the item's own description does not already contain\n"
        "that path, AND the probe answered with no tools. UNKNOWN or a non-matching\n"
        "path scores 0. `build_corpus.py probe-score` is the implementation; do not\n"
        "score these by hand.\n\n"
        "The two extra conditions are not pedantry. A path the description names is\n"
        "read, not recalled, and a probe that can search a filesystem is not measuring\n"
        "training-data recall at all -- which is the only thing this control exists to\n"
        "measure. The first probe run scored 11 of 36 answered and 359 tool calls\n"
        "against a prompt whose first line says there are no tools.\n\n"
        "Emit probe.csv with columns: item_id,family,probe_localized\n"
        "Any (item, family) cell scoring 1 is dropped from the primary analysis.\n"
        "Report the per-family contamination rate alongside every per-family result:\n"
        "cutoffs differ across families, so leakage is asymmetric and biases exactly\n"
        "the per-family comparison this evaluation exists to produce.\n"
    )
    print(f"wrote {n} probe prompts + SCORING.md -> {outdir}")
    return 0


def _labeled_site_paths(item: dict) -> set[str]:
    """Every path a defect is actually at, labels and trap sites alike."""
    paths = {s["path"] for lab in (item.get("labels") or []) for s in (lab.get("sites") or [])}
    return paths | {s["path"] for s in ((item.get("trap") or {}).get("sites") or [])}


def cmd_probe_score(args) -> int:
    """Score probe replies into the probe.csv `analyze_lrhe.py --probe` consumes.

    There was no implementation of this: `SCORING.md` described the rule in prose
    and every scoring of it was therefore by hand, against a rule that turned out
    to be wrong in two ways. A localized hit means the model knew where the defect
    was without being shown, so a path the description already contains is not a
    hit, and neither is anything returned by a probe that went and looked.
    """
    corpus = {it["item_id"]: it for it in _read_jsonl(args.corpus)}
    rows, skipped, tooled = [], 0, []
    for reply in _read_jsonl(args.probes):
        item = corpus.get(reply.get("item_id"))
        if item is None:
            skipped += 1
            continue
        body = reply.get("response") or {}
        path = str(body.get("path") or "").strip()
        sites = _labeled_site_paths(item)
        description = (item.get("problem_statement") or "")[: args.max_chars]
        # Absent means unrecorded, not zero: a probe whose tool use nobody captured
        # cannot be shown to have answered without looking.
        used_tools = reply.get("tool_calls")
        clean = used_tools == 0
        if not clean:
            tooled.append(reply.get("probe_key") or reply.get("item_id"))
        localized = int(bool(path) and not body.get("unknown") and clean
                        and any(path == s or path.endswith("/" + s) or s.endswith("/" + path)
                                for s in sites)
                        and path not in description)
        rows.append({"item_id": item["item_id"], "family": reply.get("family", ""),
                     "probe_localized": localized})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["item_id", "family", "probe_localized"])
        writer.writeheader()
        writer.writerows(rows)

    by_family: dict[str, list[int]] = {}
    for row in rows:
        by_family.setdefault(row["family"], []).append(row["probe_localized"])
    print(f"scored {len(rows)} probe cell(s) -> {args.out}")
    for family in sorted(by_family):
        hits = sum(by_family[family])
        print(f"  {family:<10} localized {hits}/{len(by_family[family])}")
    if skipped:
        print(f"  WARNING: {skipped} reply/replies name an item not in the corpus", file=sys.stderr)
    if tooled:
        print(f"  {len(tooled)} probe(s) used tools or did not record whether they did, and "
              f"score 0 by rule: {', '.join(str(t) for t in tooled[:4])}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- fetch

# The protocol names `Type3_Latent`. SWE-PRBench ships zero rows with that value;
# the real string is `Type3_Latent_Candidate` (43 rows). Using the protocol's name
# would silently yield an empty stratum, so the plan is expressed in the values the
# data actually carries.
S1_DIFFICULTY_PLAN = {
    "Type1_Direct": 4,
    "Type2_Contextual": 5,
    "Type3_Latent_Candidate": 5,
}

# Drawn from the same repository population as S1 so S5 is indistinguishable from a
# real item at dispatch time.
S5_REPOS = [
    "pallets/flask", "psf/requests", "pallets/click", "encode/httpx",
    "tiangolo/typer", "pydantic/pydantic", "sqlalchemy/alembic", "python-attrs/attrs",
]


def cmd_fetch(args) -> int:
    import sources

    key = args.stratum.upper()[:2]
    http = sources.Http(args.cache)
    st = sources.BuildStats()

    if key == "S1":
        items = sources.fetch_s1(http, want=S1_DIFFICULTY_PLAN, date_gate=args.date_gate,
                                 allow_copyleft=args.allow_copyleft, stats=st)
    elif key == "S2":
        items = sources.fetch_s2(http, n_broken=args.n_broken, n_control=args.n_control,
                                 date_gate=args.date_gate,
                                 allow_copyleft=args.allow_copyleft, stats=st)
    elif key == "S3":
        db = args.arvo_db or (args.cache / "arvo.db")
        if not db.exists():
            print(f"arvo.db not found at {db}. Fetch and verify it first:\n"
                  f"  curl -L --fail -o {db} {sources.ARVO_DB_URL}\n"
                  f"  shasum -a 256 {db}   # expect {sources.ARVO_DB_SHA256}",
                  file=sys.stderr)
            return 2
        cands = sources.fetch_s3_candidates(
            db, pool=args.pool, prefer_corrected=True,
            older_dbs=[args.cache / "arvo_v1.0.0.db", args.cache / "arvo_v2.0.0.db"],
            stats=st)
        _write_jsonl(args.out, cands)
        print(f"wrote {len(cands)} ARVO CANDIDATES -> {args.out}")
        print("These are not corpus items. Every one of the 6,138 rows in arvo.db carries")
        print("the identical (reproduced, patch_located, verified) = (1, 1, 0), so no")
        print("released column identifies the falsely-patched subset. Run the paired")
        print("containers to establish which cases reproduce their own recorded crash:")
        print(f"  build_corpus.py arvo-sweep --candidates {args.out} --out sweep.jsonl --prune")
        for n in st.notes:
            print(f"  note: {n}")
        return 0
    elif key == "S4":
        print(FETCH_S4_BLOCKED, file=sys.stderr)
        return 2
    elif key == "S5":
        items = sources.fetch_s5(http, repos=args.repos or S5_REPOS, n=args.n,
                                 window_days=args.window_days, date_gate=args.date_gate,
                                 allow_copyleft=args.allow_copyleft, stats=st)
    else:
        print(f"unknown stratum {args.stratum}", file=sys.stderr)
        return 2

    _write_jsonl(args.out, items)
    print(f"wrote {len(items)} items -> {args.out}  (considered {st.considered})")
    for n in st.notes:
        print(f"  note: {n}")
    return 0


FETCH_S4_BLOCKED = """\
S4 cannot be built from the sources the protocol names. Both are unavailable:

  (a) arXiv:2511.18608 describes a 9,942-report corpus with 1,400 invalid cases but
      publishes no artifact: no repository, no DOI, no data-availability statement.
      The cited Hacker0x01/hacktivity project is a GitHub issue-mirror search CLI
      whose records carry title/url/repository/participants/labels and no HackerOne
      outcome. HackerOne's own API requires an authenticated customer token.
      -> Ask the authors for the corpus, or replace this source.

  (b) ARVO inversion works in principle -- take a case whose patch DID close the
      crash and assert in the packet that it did not -- but the assertion is only
      false if the fixed image is observed not to crash. That is the same paired
      container run S3 needs, and it must be calibrated on one real case first:
      an image pull failure also produces no sanitizer output, and scoring that as
      "trap correctly refused" would invert the result.
      -> build_corpus.py arvo-sweep, then build S4 from confirmed clean fixes.

Refusing rather than emitting placeholder traps is deliberate. S4 is the stratum
that decides whether the council takes bait; a fabricated trap measures nothing.
"""


# ---------------------------------------------------------------- arvo sweep

# Reproduction fidelity. The single most dangerous failure in this sweep is a
# vulnerable image that crashes for a reason unrelated to the recorded bug: the
# fixed image then crashes the same way, the pair scores "incomplete fix," and an
# S3 item ships whose executable label asserts a defect that does not exist.
#
# It is not hypothetical. Under x86_64 emulation on arm64, five cases in the first
# 53 produced `AddressSanitizer: BUS on unknown address` at BOTH revisions -- and
# those five, and only those five, classified as incomplete fixes. Zero of the
# 6,138 rows in arvo.db record a BUS crash type, so none of them was reproducing
# its own bug. The gate below is what makes that visible instead of authoritative.
_CRASH_PATTERNS = [
    ("heap-buffer-overflow", r"heap[- ]buffer[- ]overflow"),
    ("stack-buffer-overflow", r"stack[- ]buffer[- ](overflow|underflow)"),
    ("global-buffer-overflow", r"global[- ]buffer[- ]overflow"),
    ("dynamic-stack-buffer-overflow", r"dynamic[- ]stack[- ]buffer[- ]overflow"),
    ("heap-use-after-free", r"heap[- ]use[- ]after[- ]free|use[- ]after[- ]free"),
    ("stack-use-after-return", r"stack[- ]use[- ]after[- ]return"),
    ("stack-use-after-scope", r"stack[- ]use[- ]after[- ]scope"),
    ("use-after-poison", r"use[- ]after[- ]poison"),
    ("use-of-uninitialized-value", r"use[- ]of[- ]uninitialized[- ]value"),
    ("double-free", r"double[- ]free|attempting double-free"),
    ("invalid-free", r"invalid[- ]free|attempting free on address"),
    ("bad-free", r"bad[- ]free"),
    ("container-overflow", r"container[- ]overflow"),
    ("memcpy-param-overlap", r"memcpy[- ]param[- ]overlap"),
    ("negative-size-param", r"negative[- ]size[- ]param"),
    ("index-out-of-bounds", r"index[- ]out[- ]of[- ]bounds|index \d+ out of bounds"),
    ("bad-cast", r"bad[- ]cast|downcast of address"),
    ("object-size", r"object[- ]size"),
    ("null-dereference", r"null[- ]dereference|null pointer passed"),
    ("segv", r"\bsegv\b"),
    # Emulation tells. Never a recorded ARVO crash type; never a usable verdict.
    ("bus", r"\bbus on unknown address\b|\bsigbus\b"),
    ("unknown-crash", r"unknown[- ]crash"),
]
_EMULATION_ARTIFACTS = {"bus", "unknown-crash"}
# arvo.db uses these when the tracker itself did not classify the crash. They
# cannot be matched exactly, so any real sanitizer class satisfies them.
_GENERIC_RECORDED = {"unknown", "segv", ""}


def crash_class(text: str | None) -> str:
    """Canonical crash class for an arvo.db `crash_type` or a sanitizer report."""
    low = (text or "").lower()
    for name, pat in _CRASH_PATTERNS:
        if re.search(pat, low):
            return name
    return "unknown"


def _reproduces(recorded: str, observed: str) -> bool:
    """Did the vulnerable run actually reproduce the recorded bug?"""
    if observed in _EMULATION_ARTIFACTS or observed == "unknown":
        return False
    return recorded == observed or recorded in _GENERIC_RECORDED


def cmd_arvo_sweep(args) -> int:
    """Run each ARVO candidate's PoC in the vulnerable and fixed images.

    This is the only way to separate the three populations the corpus needs:
      vul crashes, fix clean    -> a correct fix. S3 control, and the S4 trap pool.
      vul crashes, fix crashes  -> an incomplete fix by a competent human. S3 gold.
      vul does not crash        -> unusable; ARVO reproduces about 81% of entries.

    A container that fails to start is NOT a clean fix, and conflating the two is
    the single mistake that would invert every S3 and S4 label.
    """
    import sources

    cands = _read_jsonl(args.candidates)
    if not _docker_ready():
        print("docker daemon is not reachable. Start it and retry:\n"
              "  open -a Docker            # macOS\n"
              "  docker info               # confirm", file=sys.stderr)
        return 2

    done = {r["localId"]: r for r in (_read_jsonl(args.out) if Path(args.out).exists() else [])}
    if done:
        print(f"resuming: {len(done)} cases already swept in {args.out}")
    out = list(done.values())
    todo = [c for c in cands if c["localId"] not in done][: args.limit]

    for i, c in enumerate(todo, 1):
        vul_cmd, fix_cmd = sources.arvo_pair_commands(c)
        vul = _run_probe(vul_cmd, args.timeout)
        recorded = crash_class(c["crash_type"])
        vul_class = crash_class(vul["signature"])
        faithful = vul["crashed"] and _reproduces(recorded, vul_class)
        rec = {"localId": c["localId"], "project": c["project"],
               "vul_ok": vul["ran"], "vul_crashed": vul["crashed"],
               "signature": vul["signature"], "recorded_class": recorded,
               "vul_class": vul_class, "faithful": faithful,
               "fix_ok": None, "fix_crashed": None, "fix_signature": "", "fix_class": ""}
        if faithful:
            fix = _run_probe(fix_cmd, args.timeout)
            rec["fix_ok"] = fix["ran"]
            rec["fix_signature"] = fix["signature"]
            rec["fix_class"] = crash_class(fix["signature"])
            rec["fix_crashed"] = fix["crashed"] and rec["fix_class"] == vul_class
        out.append(rec)
        role = ("unfaithful" if not faithful
                else "incomplete_fix" if rec["fix_crashed"]
                else "correct_fix" if rec["fix_ok"] else "fix_run_failed")
        note = "" if faithful else f"  ({vul_class} != recorded {recorded})"
        # Each pair is roughly 2 GB, so a sweep long enough to find five incomplete
        # fixes would pull far more than a laptop has free. Nothing downstream needs
        # the image again: the verdict is recorded and verify_cmd re-pulls on demand.
        if args.prune:
            _prune_images(c["localId"])
        print(f"[{i}/{len(todo)}] {c['localId']:>10} {c['project'][:24]:<24} {role}{note}")
        _write_jsonl(args.out, out)
    n_inc = sum(1 for r in out if r.get("fix_crashed"))
    n_ok = sum(1 for r in out if r.get("fix_ok") and not r.get("fix_crashed"))
    n_bad = sum(1 for r in out if not r.get("faithful", r.get("vul_crashed")))
    print(f"\nswept {len(out)}: {n_inc} incomplete fixes (S3 gold), {n_ok} correct fixes "
          f"(S3 controls + S4 trap pool), {n_bad} unfaithful -> {args.out}")
    return 0


def _docker_ready() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True,
                              timeout=20, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _prune_images(local_id: int) -> None:
    subprocess.run(["docker", "image", "rm", "-f",
                    f"n132/arvo:{local_id}-vul", f"n132/arvo:{local_id}-fix"],
                   capture_output=True, timeout=180, check=False)


# A sanitizer report, not a nonzero exit code. A pull failure, an OOM kill and a
# harness that never started all exit nonzero without any crash having occurred.
_SANITIZER_RE = re.compile(
    r"(ERROR: (?:Address|Memory|Leak|UndefinedBehavior)Sanitizer[^\n]*"
    r"|SUMMARY: \w+Sanitizer:[^\n]*"
    r"|WARNING: MemorySanitizer:[^\n]*"
    r"|runtime error:[^\n]*)")


def _run_probe(cmd: str, timeout: int) -> dict:
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, errors="replace", check=False)
    except subprocess.TimeoutExpired:
        return {"ran": False, "crashed": False, "signature": "", "why": "timeout"}
    blob = (p.stdout or "") + (p.stderr or "")
    if re.search(r"(manifest unknown|pull access denied|Cannot connect to the Docker daemon"
                 r"|no such image|Unable to find image.*\n.*Error)", blob, re.I):
        return {"ran": False, "crashed": False, "signature": "", "why": "image unavailable"}
    m = _SANITIZER_RE.search(blob)
    if m:
        # Normalize the report line to a comparable signature: the crash class plus
        # the faulting frame, with addresses and pids stripped.
        sig = re.sub(r"0x[0-9a-f]+|\bpid=\d+|\b\d{4,}\b", "", m.group(0)).strip()
        return {"ran": True, "crashed": True, "signature": sig, "why": ""}
    return {"ran": True, "crashed": False, "signature": "", "why": ""}


# ---------------------------------------------------------------- arvo -> items

def _rank_arvo(http, cands: dict, clean: list[dict], allow_copyleft: bool) -> list[dict]:
    """Order swept clean cases: dispatchable licenses first, then round-robin by
    project. Deterministic; within a project, newest case id first."""
    import sources

    by_project: dict[str, list[dict]] = {}
    for s in clean:
        c = cands[s["localId"]]
        lic, _ = sources.upstream_license(http, c["repo_addr"])
        s["_dispatchable"] = bool(sources._allowlist(lic, allow_copyleft=allow_copyleft))
        s["_license"] = lic or "UNDECLARED"
        by_project.setdefault(c["project"], []).append(s)
    for group in by_project.values():
        group.sort(key=lambda s: (not s["_dispatchable"], -int(s["localId"])))

    ordered: list[dict] = []
    for tier in (True, False):
        cursors = {p: 0 for p in by_project}
        while True:
            took = False
            for project in sorted(by_project):
                group = by_project[project]
                while cursors[project] < len(group):
                    s = group[cursors[project]]
                    cursors[project] += 1
                    if s["_dispatchable"] is tier:
                        ordered.append(s)
                        took = True
                        break
            if not took:
                break
    return ordered


def cmd_arvo_build(args) -> int:
    """Turn sweep results into S3 items and S4 traps.

    Three populations come out of one sweep, and each is worth something different:
      fix still crashes -> S3 gold. An incomplete fix by a competent human, which
                           is the failure a plausibility-optimized reviewer waves
                           through, with an executable verdict.
      fix runs clean    -> S3 control, and the substrate for an S4 trap: assert in
                           the packet that the crash survived, and the same command
                           that proved it did not is the trap's verify_cmd.
      unfaithful       -> discarded. The vulnerable run did not reproduce the
                          recorded crash class, so neither verdict means anything.
    """
    import sources

    http = sources.Http(args.cache)
    cands = {c["localId"]: c for c in _read_jsonl(args.candidates)}
    sweeps = _read_jsonl(args.sweep)

    # Recomputed here, not merely read, so a sweep file written before the fidelity
    # gate existed is re-judged rather than trusted.
    for s in sweeps:
        recorded = s.get("recorded_class") or crash_class(cands[s["localId"]]["crash_type"])
        observed = s.get("vul_class") or crash_class(s.get("signature"))
        s["faithful"] = bool(s.get("vul_crashed")) and _reproduces(recorded, observed)
        s["recorded_class"], s["vul_class"] = recorded, observed

    usable = [s for s in sweeps if s["faithful"]]
    clean = [s for s in usable if s.get("fix_ok") and not s.get("fix_crashed")]
    # Gold is now sourced from ARVO's own correction history, not from a fixed image
    # that still crashes. 124 faithful paired runs produced zero of the latter, and
    # v3's release notes say prior false positives were fixed -- so the population
    # the protocol wants survives only as metadata. A candidate carrying a
    # `superseded_fix_commit` is a patch an earlier release published as the fix and
    # a later one replaced.
    gold = [s for s in clean if cands[s["localId"]].get("superseded_fix_commit")]
    controls = [s for s in clean if not cands[s["localId"]].get("superseded_fix_commit")]
    print(f"sweep: {len(sweeps)} cases -> {len(usable)} faithful "
          f"({len(sweeps) - len(usable)} discarded: vulnerable run did not reproduce "
          f"the recorded crash class) -> {len(gold)} superseded-fix, {len(controls)} clean")

    # Order before slicing: dispatchable licenses first, then spread across projects.
    # Three ImageMagick items in a row would let a reviewer that over-flags one
    # codebase's idiom fail them all, and the item-clustered bootstrap treats those
    # as independent draws when they are not.
    gold = _rank_arvo(http, cands, gold, args.allow_copyleft)
    controls = _rank_arvo(http, cands, controls, args.allow_copyleft)

    s3, s4, skipped = [], [], []
    n_gold = n_ctrl = 0
    for s in gold + controls:
        c = cands[s["localId"]]
        is_gold = bool(c.get("superseded_fix_commit"))
        if (is_gold and n_gold >= args.n_s3_gold) or (not is_gold and n_ctrl >= args.n_s3_control):
            continue
        # The reviewed artifact for a gold item is the SUPERSEDED patch -- the one
        # that was published as the fix and was not. Reviewing the current patch
        # would present a correct fix and label it broken.
        url = c.get("superseded_patch_url") or c["patch_url"]
        patch = sources.fetch_arvo_patch(http, url)
        touched = sources.diff_touched(patch) if patch else {}
        # A patch that parses to zero files is a mirror serving an HTML page, or a
        # malformed upstream URL -- v1 recorded several as `<repo>.git<sha>` with no
        # separator. Reconstruct from the commit before giving up; without real
        # paths the label sites fall back to the project name, every correct claim
        # scores CONFIRMED_UNANCHORED, and the item silently measures nothing.
        if not touched and c.get("superseded_fix_commit"):
            patch = sources.fetch_commit_patch(http, c["repo_addr"], c["superseded_fix_commit"])
            touched = sources.diff_touched(patch) if patch else {}
        if not touched:
            skipped.append((s["localId"], f"no parseable patch for {url}"))
            continue
        s["sites"] = [{"path": p, "lines": [a, b]} for p, rs in sorted(touched.items()) for a, b in rs]
        s["patch_text"] = patch
        lic, lic_url = sources.upstream_license(http, c["repo_addr"])
        s3.append(sources.s3_item_from_sweep(
            c, s, date_gate=args.date_gate, license_id=lic, license_url=lic_url,
            allow_copyleft=args.allow_copyleft))
        n_gold += is_gold
        n_ctrl += not is_gold
    # Traps come from the control pool only: a trap asserts the fix is incomplete,
    # so it must sit on a fix that demonstrably is not.
    for s in controls[args.n_s3_control: args.n_s3_control + args.n_s4]:
        c = cands[s["localId"]]
        patch = sources.fetch_arvo_patch(http, c["patch_url"])
        if not patch:
            skipped.append((s["localId"], "patch unavailable"))
            continue
        lic, lic_url = sources.upstream_license(http, c["repo_addr"])
        s4.append(sources.s4_trap_from_sweep(
            c, s, patch, date_gate=args.date_gate, license_id=lic, license_url=lic_url,
            allow_copyleft=args.allow_copyleft))

    if s3:
        _write_jsonl(args.out_s3, s3)
        print(f"wrote {len(s3)} S3 items -> {args.out_s3}")
    if s4:
        _write_jsonl(args.out_s4, s4)
        print(f"wrote {len(s4)} S4 traps -> {args.out_s4}")
    for lid, why in skipped:
        print(f"  skipped {lid}: {why}")
    if len(gold) < args.n_s3_gold:
        print(f"\nWARNING: only {len(gold)} superseded-fix cases have been swept and confirmed "
              f"faithful; S3 wants {args.n_s3_gold}. 152 exist in the release history -- sweep "
              f"more of them (they sort first in the candidate pool).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("plan").set_defaults(fn=cmd_plan)

    a = sub.add_parser("assignments")
    a.add_argument("--out", type=Path, default=Path("assignments.csv"))
    a.add_argument("--d-items", type=int, default=24,
                   help="items receiving the full Latin square and arm T")
    a.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT,
                   help=f"panel definition from panels.yaml (default: {DEFAULT_EXPERIMENT}). "
                        f"Use lrhe-core-v1 to reproduce the pre-registered 3x3 exactly, or "
                        f"lrhe-opencode-v1 for the four-family floor panel")
    a.add_argument("--panel-config", type=Path, default=PANELS_PATH,
                   help="frozen panel definitions (default: %(default)s)")
    a.add_argument("--triplicate-family", default=None,
                   help="family repeated for the arm-T empirical null; defaults to the "
                        "experiment's nullFamily")
    a.add_argument("--corpus", type=Path, default=Path("corpus.jsonl"),
                   help="use real item ids from this corpus; falls back to the "
                        "pre-registered plan's placeholder ids when absent")
    a.add_argument("--families", nargs="*", default=None, metavar="NAME",
                   help=f"council members (default: {' '.join(FAMILIES)}). Any names work "
                        f"-- kimi, glm, qwen, deepseek. Arm C and arm D scale linearly, "
                        f"and arm T scales with them so the empirical null stays comparable")
    a.add_argument("--author-family", default=None,
                   help="family that runs arm A and authored the work under review")
    a.add_argument("--triplicate-n", type=int, default=None,
                   help="arm-T replicate count; defaults to the council size")
    a.add_argument("--lenses", nargs="*", default=None, metavar="LENS",
                   help=f"lens rotation (default: {' '.join(LENSES)}). Pin the original "
                        f"three to reproduce the pre-registered 523-run budget exactly; "
                        f"arm D is |families| x |lenses| runs per subset item")
    a.add_argument("--assignment-salt", default="",
                   help="freeze a deliberate reshuffle of arm B and the arm-D/T subset. "
                        "Empty reproduces every assignment frozen so far; whatever is "
                        "used is recorded in the manifest")
    a.add_argument("--manifest", type=Path, default=Path("assignments.manifest.json"),
                   help="immutable record: salt, selected subset, and digests of both "
                        "the corpus and the emitted CSV")
    a.set_defaults(fn=cmd_assignments)

    s = sub.add_parser("scrub")
    s.add_argument("--in", dest="inp", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--rename", action="store_true")
    s.add_argument("--salt", default="lrhe-v1")
    s.add_argument("--dispatch-out", type=Path, default=None,
                   help="also write the reviewer-safe projection: no repo, no commits, "
                        "no labels, no build_notes, no trap answer")
    s.set_defaults(fn=cmd_scrub)

    p = sub.add_parser("probes")
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("probes"))
    p.add_argument("--max-chars", type=int, default=1800)
    p.set_defaults(fn=cmd_probes)

    ps = sub.add_parser("probe-score", help="score probe replies into probe.csv")
    ps.add_argument("--probes", type=Path, required=True,
                    help="JSONL of {item_id, family, tool_calls, response:{path,unknown}}")
    ps.add_argument("--corpus", type=Path, required=True)
    ps.add_argument("--out", type=Path, default=Path("probe.csv"))
    ps.add_argument("--max-chars", type=int, default=1800,
                    help="must match the value `probes` was generated with")
    ps.set_defaults(fn=cmd_probe_score)

    f = sub.add_parser("fetch", help="build one stratum from its public source")
    f.add_argument("--stratum", required=True, help="S1 | S2 | S3 | S4 | S5")
    f.add_argument("--out", type=Path, default=Path("raw.jsonl"))
    f.add_argument("--cache", type=Path, default=Path(".cache"),
                   help="downloaded upstream artifacts; makes a rebuild free and reproducible")
    f.add_argument("--date-gate", default=None, metavar="YYYY-MM-DD",
                   help="require merge/fix date strictly after this. Use the LATEST cutoff "
                        "across ALL participating families; per-family gating reintroduces "
                        "the asymmetry the gate exists to remove")
    f.add_argument("--n", type=int, default=3, help="S5: item count")
    f.add_argument("--repos", nargs="*", default=None, help="S5: repositories to harvest")
    f.add_argument("--window-days", type=int, default=90, help="S5: clean window")
    f.add_argument("--n-broken", type=int, default=5, help="S2: known-broken candidate patches")
    f.add_argument("--n-control", type=int, default=5, help="S2: known-passing controls")
    f.add_argument("--arvo-db", type=Path, default=None, help="S3: path to the pinned arvo.db")
    f.add_argument("--pool", type=int, default=400, help="S3: candidate pool size for the sweep")
    f.add_argument("--allow-copyleft", action="store_true",
                   help="authorize copyleft-licensed items (GPL/LGPL/MPL/...) for provider "
                        "transmission. Off by default: protocol section 3 warns these corpora "
                        "over-sample GPL, and the decision is a policy call, not a build detail")
    f.set_defaults(fn=cmd_fetch)

    v = sub.add_parser("arvo-sweep",
                       help="run paired vulnerable/fixed containers to classify ARVO cases")
    v.add_argument("--candidates", type=Path, required=True)
    v.add_argument("--out", type=Path, default=Path("sweep.jsonl"))
    v.add_argument("--limit", type=int, default=120,
                   help="cases to sweep THIS run; already-swept cases in --out are skipped")
    v.add_argument("--timeout", type=int, default=900)
    v.add_argument("--prune", action="store_true",
                   help="delete each pair's images after use. ~2 GB per pair, so a "
                        "sweep long enough to find five incomplete fixes needs this")
    v.set_defaults(fn=cmd_arvo_sweep)

    b = sub.add_parser("arvo-build",
                       help="turn sweep results into S3 items and S4 traps")
    b.add_argument("--candidates", type=Path, required=True)
    b.add_argument("--sweep", type=Path, required=True)
    b.add_argument("--cache", type=Path, default=Path(".cache"))
    b.add_argument("--out-s3", type=Path, default=Path("raw/S3.jsonl"))
    b.add_argument("--out-s4", type=Path, default=Path("raw/S4.jsonl"))
    b.add_argument("--n-s3-gold", type=int, default=5, help="incomplete-fix items")
    b.add_argument("--n-s3-control", type=int, default=3, help="correct-fix controls")
    b.add_argument("--n-s4", type=int, default=12, help="inverted traps")
    b.add_argument("--date-gate", default=None)
    b.add_argument("--allow-copyleft", action="store_true",
                   help="authorize copyleft-licensed items for provider transmission")
    b.set_defaults(fn=cmd_arvo_build)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
