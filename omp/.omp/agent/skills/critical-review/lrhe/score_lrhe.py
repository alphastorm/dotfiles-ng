#!/usr/bin/env python3
"""
score_lrhe.py -- deterministic scorer for the Lens-Rotation Historical Evaluation.

Consumes:
  --corpus   corpus.jsonl   one record per review item (see schema/item.schema.json)
  --runs     runs.jsonl     one record per reviewer run (see schema/run.schema.json)
  --judge    judge.jsonl    optional: cross-family panel verdicts, one per claim
  --exec     exec.jsonl     optional: execution results for claim `verify=` checks

Emits:
  --out-claims claims.csv   one row per parsed claim, fully adjudicated
  --out-runs   runs.csv     one row per (item, arm, family, lens, context_config)
  --out-report report.json  parse/compliance/gate summary

Design notes
------------
1. Everything a machine can decide is decided here, before any judge sees a claim.
   That includes: contract parse, anchor extraction, localization overlap, the
   `verify=` execution verdict, tool-restriction compliance, and model identity.
2. The judge is only asked the one question a machine cannot answer: "is this
   claim about the same underlying defect as labeled defect L?" Judge verdicts
   are inputs, not authorities -- a judge CONFIRMED that fails the localization
   gate is recorded as CONFIRMED_UNANCHORED and excluded from the promoted set.
3. Execution outranks the judge. If a claim's `verify=` check was run and did
   not reproduce the predicted failure, the claim is REFUTED regardless of how
   many reviewers raised it or how confident the judge was. This is the whole
   point of the exercise.
4. Credit is assigned by 1:1 bipartite matching within (item, run), so three
   restatements of one defect earn one hit, not three.

No network access required. Pure stdlib + numpy/scipy/pandas.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

# --------------------------------------------------------------------------
# 1. Contract parsing
# --------------------------------------------------------------------------

# The reviewer contract is:
#   R<ID>|P<0-3>|conf=<0.00-1.00>|claim=<...>|evidence=<...>|impact=<...>|verify=<...>
#
# Free text in claim/evidence/impact/verify may itself contain '|', so we do NOT
# split on '|'. We anchor on the key names instead. Reviewers should still be
# told to avoid bare pipes; PARSE_PARTIAL counts how often they don't.
_KEYS = ("claim", "evidence", "impact", "verify")
_HEAD_RE = re.compile(
    r"^\s*R(?P<rid>[A-Za-z0-9_.\-]+)\s*\|\s*P(?P<sev>[0-3])\s*\|\s*conf\s*=\s*(?P<conf>[0-9]*\.?[0-9]+)\s*\|",
    re.IGNORECASE,
)
_KEY_RE = re.compile(r"\|?\s*(claim|evidence|impact|verify)\s*=", re.IGNORECASE)

# Anchor forms accepted in `evidence=`:
#   path/to/file.py:123
#   path/to/file.py:123-145
#   path/to/file.py:123:145
#   path/to/file.py L123-L145
#
# Two things this has to get right, because a missed anchor is not a visible
# failure -- it silently demotes a correct claim to CONFIRMED_UNANCHORED, drops it
# from the promoted set, and makes the section 8 anchor-rate gate measure this
# regex instead of the reviewer.
#
# 1. Longest alternative first. `ts|tsx` matches `.ts` inside `DeployProgress.tsx`,
#    leaving `x:240-254` unconsumed -- wrong path AND no line numbers. Same trap
#    for rs/rst, c/cc/cpp, h/hpp, js/jsx. The trailing boundary makes it explicit.
# 2. Docs are review targets. Three S1 label sites are `.md` files a human
#    reviewer actually commented on, and changesets carry release-note defects.
_ANCHOR_EXTS = sorted({
    "py", "pyi", "pyx", "c", "cc", "cpp", "cxx", "h", "hh", "hpp", "hxx",
    "go", "rs", "java", "kt", "kts", "scala", "clj", "ex", "exs", "erl",
    "ts", "tsx", "mts", "cts", "js", "jsx", "mjs", "cjs", "vue", "svelte",
    "rb", "php", "cs", "fs", "swift", "m", "mm", "lua", "pl", "pm", "r",
    "dart", "zig", "nim", "sh", "bash", "zsh", "ps1",
    "sql", "proto", "thrift", "graphql",
    "toml", "yaml", "yml", "json", "jsonc", "cfg", "ini", "conf", "env",
    "md", "mdx", "rst", "txt", "adoc",
    "css", "scss", "sass", "less", "html", "htm", "xml", "svg",
    "gradle", "bzl", "cmake", "mk", "tf", "tfvars", "snap", "feature",
}, key=len, reverse=True)

# `:123`, `#123`, `:L123`, and the bare `L123` form the header documents. The
# separatorless branch demands the `L` and a digit, so it cannot swallow prose.
_LINE_SEP = r"(?:\s*[:#]\s*L?|\s+L)"
_LINE_SPAN = r"(?P<start>\d+)(?:\s*[-:]\s*L?(?P<end>\d+))?"
_ANCHOR_RE = re.compile(
    r"(?P<path>(?:[\w.\-]+/)*[\w.\-]+\.(?:" + "|".join(_ANCHOR_EXTS) + r"))"
    r"(?![A-Za-z0-9])(?:" + _LINE_SEP + _LINE_SPAN + r")?"
)

# Fallback for extensions the allowlist does not know. Requires both a directory
# separator and an explicit line number, which is what keeps it from matching
# prose like "version 1.2.3" or "see foo.bar".
_ANCHOR_FALLBACK_RE = re.compile(
    r"(?P<path>(?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z][A-Za-z0-9]{0,7})"
    r"(?![A-Za-z0-9])" + _LINE_SEP + _LINE_SPAN
)

# Extensionless build and config files. `Makefile`, `configure` and `Dockerfile`
# are ordinary review targets -- one S4 trap is sited in a Makefile, and without
# this it could never register as promoted, silently scoring the bait as refused.
# The explicit line number is what stops this matching the word in prose.
_ANCHOR_BARENAMES = (
    "Makefile", "GNUmakefile", "Dockerfile", "Containerfile", "Jenkinsfile",
    "Vagrantfile", "Rakefile", "Gemfile", "Procfile", "Brewfile", "Justfile",
    "configure", "BUILD", "WORKSPACE", "LICENSE", "COPYING", "NOTICE", "OWNERS",
    "CODEOWNERS", "MANIFEST", "AUTHORS", "VERSION",
)
_ANCHOR_BARE_RE = re.compile(
    r"(?P<path>(?:[\w.\-]+/)*(?:" + "|".join(_ANCHOR_BARENAMES) + r"))"
    r"(?![\w.\-])" + _LINE_SEP + _LINE_SPAN
)

PARSE_OK = "ok"
PARSE_PARTIAL = "partial"
PARSE_FAIL = "fail"


@dataclass
class Anchor:
    path: str
    start: int | None = None
    end: int | None = None

    def lines(self) -> tuple[int, int] | None:
        if self.start is None:
            return None
        return (self.start, self.end if self.end is not None else self.start)


@dataclass
class Claim:
    run_id: str
    item_id: str
    arm: str
    family: str
    lens: str
    # Part of the condition key, not decoration. Arm T is |council| independent
    # runs of ONE family; drop this and all of them collapse into a single cell,
    # which is precisely the empirical null the diversity claim rests on.
    replicate: str
    context_config: str
    idx: int
    raw: str
    parse_status: str = PARSE_FAIL
    rid: str = ""
    severity: int | None = None
    confidence: float | None = None
    claim_text: str = ""
    evidence_text: str = ""
    impact_text: str = ""
    verify_text: str = ""
    anchors: list[Anchor] = field(default_factory=list)
    # adjudication
    has_anchor: bool = False
    anchor_paths_exist: bool | None = None
    loc_file_match: list[str] = field(default_factory=list)   # label ids
    loc_hunk_match: list[str] = field(default_factory=list)   # label ids
    judge_verdict: str = ""                                   # CONFIRMED|PLAUSIBLE|FABRICATED
    judge_label_id: str = ""
    judge_affinity: float = 0.0
    judge_panel: list[str] = field(default_factory=list)
    judge_unanimous: bool | None = None
    exec_ran: bool = False
    exec_reproduced: bool | None = None
    verdict: str = ""                                         # final
    matched_label_id: str = ""
    promoted: bool = False


def _parse_evidence_string(s: str) -> dict[str, Any]:
    """Parse one evidence string. Returns dict with parse_status and fields."""
    out: dict[str, Any] = {"parse_status": PARSE_FAIL}
    if not isinstance(s, str) or not s.strip():
        return out
    m = _HEAD_RE.match(s)
    if not m:
        return out
    out["rid"] = m.group("rid")
    out["severity"] = int(m.group("sev"))
    try:
        conf = float(m.group("conf"))
    except ValueError:
        conf = float("nan")
    out["confidence"] = conf if 0.0 <= conf <= 1.0 else float("nan")

    tail = s[m.end():]
    marks = [(mm.start(), mm.end(), mm.group(1).lower()) for mm in _KEY_RE.finditer(tail)]
    fields: dict[str, str] = {}
    for i, (ms, me, key) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(tail)
        val = tail[me:nxt].strip().rstrip("|").strip()
        # last key wins if a reviewer repeats one
        fields[key] = val
    for k in _KEYS:
        out[f"{k}_text"] = fields.get(k, "")
    present = sum(1 for k in _KEYS if fields.get(k))
    out["parse_status"] = PARSE_OK if present == len(_KEYS) else (
        PARSE_PARTIAL if present >= 2 else PARSE_FAIL
    )
    return out


def extract_anchors(text: str, max_anchors: int = 8) -> list[Anchor]:
    seen: set[tuple[str, int | None, int | None]] = set()
    paths_seen: set[str] = set()
    anchors: list[Anchor] = []
    text = text or ""
    for regex in (_ANCHOR_RE, _ANCHOR_BARE_RE, _ANCHOR_FALLBACK_RE):
        for m in regex.finditer(text):
            path = m.group("path")
            # Later passes exist only for what the allowlist missed. If an earlier
            # pass already produced this path, its match is authoritative.
            if regex is not _ANCHOR_RE and path in paths_seen:
                continue
            start = int(m.group("start")) if m.group("start") else None
            end = int(m.group("end")) if m.group("end") else None
            if start is not None and end is not None and end < start:
                start, end = end, start
            key = (path, start, end)
            if key in seen:
                continue
            seen.add(key)
            paths_seen.add(path)
            anchors.append(Anchor(path, start, end))
            if len(anchors) >= max_anchors:
                return anchors
    return anchors


# --------------------------------------------------------------------------
# 2. Localization gating
# --------------------------------------------------------------------------

def _norm_path(p: str) -> str:
    return p.strip().lstrip("./").replace("\\", "/")


def _path_matches(claim_path: str, label_path: str) -> bool:
    """Suffix match, so `src/foo/bar.py` matches a claim that said `foo/bar.py`."""
    a, b = _norm_path(claim_path), _norm_path(label_path)
    if a == b:
        return True
    return a.endswith("/" + b) or b.endswith("/" + a)


def _ranges_overlap(r1: tuple[int, int], r2: tuple[int, int], window: int) -> bool:
    return (r1[0] - window) <= r2[1] and (r2[0] - window) <= r1[1]


def localize(claim: Claim, labels: list[dict], window: int) -> None:
    """Populate loc_file_match / loc_hunk_match on the claim."""
    for lab in labels:
        lid = lab["label_id"]
        hit_file = False
        hit_hunk = False
        for a in claim.anchors:
            for site in lab.get("sites", []):
                if not _path_matches(a.path, site["path"]):
                    continue
                hit_file = True
                lr = site.get("lines")
                cr = a.lines()
                if lr and cr and _ranges_overlap(tuple(cr), tuple(lr), window):
                    hit_hunk = True
                elif not lr or not cr:
                    # file-level label or file-level claim: file match is all we get
                    pass
        if hit_file:
            claim.loc_file_match.append(lid)
        if hit_hunk:
            claim.loc_hunk_match.append(lid)


# --------------------------------------------------------------------------
# 3. Final verdict
# --------------------------------------------------------------------------

V_CONFIRMED = "CONFIRMED"
V_CONFIRMED_UNANCHORED = "CONFIRMED_UNANCHORED"
V_PLAUSIBLE = "PLAUSIBLE"
V_FABRICATED = "FABRICATED"
V_REFUTED = "REFUTED"
V_UNPARSED = "UNPARSED"

# Precedence, strongest evidence first. Execution beats judgement beats agreement.
def decide(claim: Claim, require_hunk: bool) -> None:
    if claim.parse_status == PARSE_FAIL:
        claim.verdict = V_UNPARSED
        return
    if claim.exec_ran and claim.exec_reproduced is False:
        claim.verdict = V_REFUTED
        return
    if claim.anchor_paths_exist is False:
        claim.verdict = V_FABRICATED
        return
    if claim.judge_verdict == V_FABRICATED:
        claim.verdict = V_FABRICATED
        return
    if claim.exec_ran and claim.exec_reproduced is True:
        gate = claim.judge_label_id in (claim.loc_hunk_match if require_hunk else claim.loc_file_match)
        claim.verdict = V_CONFIRMED if (claim.has_anchor and (gate or not claim.judge_label_id)) else V_CONFIRMED_UNANCHORED
        return
    if claim.judge_verdict == V_CONFIRMED:
        gate = claim.judge_label_id in (claim.loc_hunk_match if require_hunk else claim.loc_file_match)
        claim.verdict = V_CONFIRMED if (claim.has_anchor and gate) else V_CONFIRMED_UNANCHORED
        return
    claim.verdict = V_PLAUSIBLE


# --------------------------------------------------------------------------
# 4. 1:1 bipartite matching within (run, item)
# --------------------------------------------------------------------------

def match_claims_to_labels(claims: list[Claim], labels: list[dict]) -> None:
    """Assign at most one claim per label and one label per claim (Hungarian).

    Cost combines judge affinity and localization strength so that, when two
    claims both plausibly cover one label, the better-anchored one gets credit.
    """
    cands = [c for c in claims if c.verdict in (V_CONFIRMED, V_CONFIRMED_UNANCHORED)]
    if not cands or not labels:
        return
    lab_ids = [l["label_id"] for l in labels]
    score = np.zeros((len(cands), len(lab_ids)), dtype=float)
    for i, c in enumerate(cands):
        for j, lid in enumerate(lab_ids):
            s = 0.0
            if c.judge_label_id == lid:
                s += 1.0 + 0.5 * float(c.judge_affinity or 0.0)
            if lid in c.loc_hunk_match:
                s += 0.6
            elif lid in c.loc_file_match:
                s += 0.3
            if c.exec_ran and c.exec_reproduced is True and c.judge_label_id == lid:
                s += 1.0
            score[i, j] = s
    rows, cols = linear_sum_assignment(-score)
    for i, j in zip(rows, cols):
        if score[i, j] <= 0:
            continue
        cands[i].matched_label_id = lab_ids[j]
    # Anything that lost the assignment is a duplicate restatement, not a new hit.
    for c in cands:
        if not c.matched_label_id:
            c.verdict = V_PLAUSIBLE
        else:
            c.promoted = (
                c.verdict == V_CONFIRMED
                and c.severity is not None
                and c.severity <= 1
            )


# --------------------------------------------------------------------------
# 5. Per-run rollup
# --------------------------------------------------------------------------

def rollup_run(run: dict, item: dict, claims: list[Claim]) -> dict:
    labels = item.get("labels", [])
    n_lab = len(labels)
    n_lab_crit = sum(1 for l in labels if int(l.get("severity", 3)) <= 1)
    caught = {c.matched_label_id for c in claims if c.matched_label_id}
    caught_crit = {
        c.matched_label_id
        for c in claims
        if c.matched_label_id
        and any(int(l.get("severity", 3)) <= 1 for l in labels if l["label_id"] == c.matched_label_id)
    }
    n_claims = len(claims)
    counts = defaultdict(int)
    for c in claims:
        counts[c.verdict] += 1
    scored = [c for c in claims if c.verdict != V_UNPARSED]
    n_scored = len(scored)

    # FP-trap: did the reviewer promote the seeded false claim?
    # FP-trap: did the reviewer promote the seeded false claim to P0/P1?
    # Note: a later REFUTED verdict is the ground truth confirming the trap was
    # false -- it is NOT evidence that the reviewer abstained. Taking the bait
    # counts against the reviewer regardless of what refutation later showed.
    trap = item.get("trap")
    trap_promoted = None
    trap_severity = None
    if trap:
        bait = [
            c for c in claims
            if c.parse_status != PARSE_FAIL
            and c.severity is not None
            and any(_path_matches(a.path, s["path"])
                    for a in c.anchors for s in trap.get("sites", []))
        ]
        trap_promoted = any(c.severity <= 1 for c in bait)
        trap_severity = min((c.severity for c in bait), default=None)

    return {
        "run_id": run["run_id"],
        "item_id": item["item_id"],
        "stratum": item.get("stratum", ""),
        "difficulty": item.get("difficulty", ""),
        "arm": run.get("arm", ""),
        "family": run.get("family", ""),
        "lens": run.get("lens", ""),
        "replicate": run.get("replicate", ""),
        "context_config": run.get("context_config", ""),
        "model_selector_reported": run.get("model_selector_reported", ""),
        "model_selector_expected": run.get("model_selector_expected", ""),
        "model_identity_ok": (
            run.get("model_selector_reported", "") == run.get("model_selector_expected", "")
            if run.get("model_selector_expected")
            else None
        ),
        "schema_valid": bool(run.get("schema_valid", True)),
        "tool_violations": int(run.get("tool_violations", 0)),
        "wrote_to_repo": bool(run.get("wrote_to_repo", False)),
        "spawned_subagent": bool(run.get("spawned_subagent", False)),
        "n_labels": n_lab,
        "n_labels_crit": n_lab_crit,
        "n_claims": n_claims,
        "n_unparsed": counts[V_UNPARSED],
        "parse_rate": (n_scored / n_claims) if n_claims else float("nan"),
        "cap_respected": n_claims <= int(run.get("evidence_cap", 12)),
        "n_confirmed": counts[V_CONFIRMED],
        "n_confirmed_unanchored": counts[V_CONFIRMED_UNANCHORED],
        "n_plausible": counts[V_PLAUSIBLE],
        "n_fabricated": counts[V_FABRICATED],
        "n_refuted": counts[V_REFUTED],
        "n_promoted": sum(1 for c in claims if c.promoted),
        "anchor_rate": (
            sum(1 for c in scored if c.has_anchor) / n_scored if n_scored else float("nan")
        ),
        "promoted_anchor_rate": (
            sum(1 for c in claims if c.promoted and c.has_anchor)
            / max(1, sum(1 for c in claims if c.promoted))
            if any(c.promoted for c in claims)
            else float("nan")
        ),
        "recall": len(caught) / n_lab if n_lab else float("nan"),
        "recall_crit": len(caught_crit) / n_lab_crit if n_lab_crit else float("nan"),
        "precision": (counts[V_CONFIRMED] / n_scored) if n_scored else float("nan"),
        "fabrication_rate": (counts[V_FABRICATED] / n_scored) if n_scored else float("nan"),
        "refutation_rate": (counts[V_REFUTED] / n_scored) if n_scored else float("nan"),
        "null_item_fp": (
            sum(1 for c in claims if c.severity is not None and c.severity <= 1
                and c.verdict not in (V_REFUTED, V_UNPARSED))
            if item.get("stratum") == "S5_NULL"
            else None
        ),
        "trap_promoted": trap_promoted,
        "trap_severity": trap_severity,
        "caught_label_ids": "|".join(sorted(caught)),
        "latency_ms": run.get("latency_ms"),
        "input_tokens": run.get("input_tokens"),
        "output_tokens": run.get("output_tokens"),
        "cost_usd": run.get("cost_usd"),
        "quota_pool": run.get("quota_pool", ""),
    }


# --------------------------------------------------------------------------
# 6. Driver
# --------------------------------------------------------------------------

def read_jsonl(p: Path | None) -> list[dict]:
    if p is None:
        return []
    if not p.exists():
        raise FileNotFoundError(p)
    out = []
    for ln, line in enumerate(p.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise SystemExit(f"{p}:{ln}: bad JSON: {e}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--judge", type=Path, default=None)
    ap.add_argument("--exec", dest="execres", type=Path, default=None)
    ap.add_argument("--out-claims", type=Path, default=Path("claims.csv"))
    ap.add_argument("--out-runs", type=Path, default=Path("runs.csv"))
    ap.add_argument("--out-report", type=Path, default=Path("report.json"))
    ap.add_argument("--hunk-window", type=int, default=10,
                    help="lines of slack when matching a claim anchor to a labeled hunk")
    ap.add_argument("--require-hunk", action="store_true",
                    help="require hunk-level (not just file-level) overlap to promote a claim")
    args = ap.parse_args(argv)

    corpus = {it["item_id"]: it for it in read_jsonl(args.corpus)}
    runs = read_jsonl(args.runs)
    judge_idx: dict[tuple[str, str], dict] = {}
    for j in read_jsonl(args.judge):
        judge_idx[(j["run_id"], str(j["claim_rid"]))] = j
    exec_idx: dict[tuple[str, str], dict] = {}
    for e in read_jsonl(args.execres):
        exec_idx[(e["run_id"], str(e["claim_rid"]))] = e

    all_claims: list[Claim] = []
    run_rows: list[dict] = []
    unknown_items: set[str] = set()

    for run in runs:
        item = corpus.get(run["item_id"])
        if item is None:
            unknown_items.add(run["item_id"])
            continue
        labels = item.get("labels", [])
        repo_files = set(item.get("repo_files", []))  # optional allowlist for path existence
        claims: list[Claim] = []
        for i, raw in enumerate(run.get("evidence", [])):
            parsed = _parse_evidence_string(raw)
            c = Claim(
                run_id=run["run_id"], item_id=item["item_id"], arm=run.get("arm", ""),
                family=run.get("family", ""), lens=run.get("lens", ""),
                replicate=run.get("replicate", ""),
                context_config=run.get("context_config", ""), idx=i, raw=raw,
                **{k: v for k, v in parsed.items() if k in
                   {"parse_status", "rid", "severity", "confidence",
                    "claim_text", "evidence_text", "impact_text", "verify_text"}},
            )
            if not c.rid:
                c.rid = f"AUTO{i}"
            c.anchors = extract_anchors(c.evidence_text or c.claim_text)
            c.has_anchor = bool(c.anchors)
            if repo_files and c.anchors:
                c.anchor_paths_exist = any(
                    any(_path_matches(a.path, f) for f in repo_files) for a in c.anchors
                )
            localize(c, labels, args.hunk_window)

            jv = judge_idx.get((run["run_id"], c.rid))
            if jv:
                c.judge_verdict = jv.get("verdict", "")
                c.judge_label_id = jv.get("label_id", "") or ""
                c.judge_affinity = float(jv.get("affinity", 0.0) or 0.0)
                c.judge_panel = list(jv.get("panel", []))
                c.judge_unanimous = jv.get("unanimous")
            ev = exec_idx.get((run["run_id"], c.rid))
            if ev:
                c.exec_ran = True
                c.exec_reproduced = bool(ev.get("reproduced"))

            decide(c, args.require_hunk)
            claims.append(c)

        match_claims_to_labels(claims, labels)
        all_claims.extend(claims)
        run_rows.append(rollup_run(run, item, claims))

    claims_df = pd.DataFrame([
        {
            **{k: v for k, v in asdict(c).items() if k not in {"anchors", "judge_panel",
                                                               "loc_file_match", "loc_hunk_match"}},
            "anchors": ";".join(
                f"{a.path}:{a.start or ''}{'-' + str(a.end) if a.end else ''}" for a in c.anchors
            ),
            "loc_file_match": "|".join(c.loc_file_match),
            "loc_hunk_match": "|".join(c.loc_hunk_match),
            "judge_panel": "|".join(c.judge_panel),
        }
        for c in all_claims
    ])
    runs_df = pd.DataFrame(run_rows)

    claims_df.to_csv(args.out_claims, index=False)
    runs_df.to_csv(args.out_runs, index=False)

    n_claims = len(claims_df)
    report = {
        "n_items": len(corpus),
        "n_runs": len(runs_df),
        "n_claims": int(n_claims),
        "unknown_item_ids": sorted(unknown_items),
        "judge_coverage": (
            float((claims_df["judge_verdict"] != "").mean()) if n_claims else None
        ),
        "exec_coverage": float(claims_df["exec_ran"].mean()) if n_claims else None,
        "gates": {
            # These mirror the promotion gates already written into the council spec.
            "schema_valid_rate": float(runs_df["schema_valid"].mean()) if len(runs_df) else None,
            "contract_parse_rate": float(
                (claims_df["parse_status"] != PARSE_FAIL).mean()
            ) if n_claims else None,
            "no_write_compliance": float((~runs_df["wrote_to_repo"]).mean()) if len(runs_df) else None,
            "no_recursion_compliance": float((~runs_df["spawned_subagent"]).mean()) if len(runs_df) else None,
            "model_identity_ok_rate": (
                float(runs_df["model_identity_ok"].dropna().mean())
                if len(runs_df) and runs_df["model_identity_ok"].notna().any() else None
            ),
            "promoted_claim_anchor_rate": (
                float(claims_df.loc[claims_df["promoted"], "has_anchor"].mean())
                if n_claims and claims_df["promoted"].any() else None
            ),
            "cap_respected_rate": float(runs_df["cap_respected"].mean()) if len(runs_df) else None,
        },
        "verdict_mix": (
            {k: int(v) for k, v in claims_df["verdict"].value_counts().items()} if n_claims else {}
        ),
    }
    args.out_report.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
