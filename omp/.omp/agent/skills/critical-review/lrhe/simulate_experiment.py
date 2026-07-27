#!/usr/bin/env python3
"""
simulate_experiment.py -- generate a synthetic LRHE run from a known truth.

Two jobs:
  1. End-to-end smoke test of score_lrhe.py + analyze_lrhe.py at real scale.
  2. Power measurement. Before spending provider quota, we can answer "what
     effect size would this corpus actually detect?" by simulating from known
     family/lens effects and counting how often the analysis recovers them.

Generative model, per (labeled defect, family, lens):
    logit(P(caught)) = b0 + fam[f] + lens[l] + inter[f,l] + item_re + kind_adj
Plus per-run false positives (fabrications, plausible-but-unlabeled claims) and
per-family trap susceptibility.

Usage:
  simulate_experiment.py --out-dir sim/           # one dataset
  simulate_experiment.py --power --reps 200       # power sweep
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
# The repository is flat; the protocol's Files section describes a scripts/ layout.
# Resolve either, so the simulation runs from a checkout of either shape.
SCRIPTS = HERE.parent / "scripts" if (HERE.parent / "scripts" / "score_lrhe.py").exists() else HERE

FAMILIES = ["claude", "gemini", "grok"]
LENSES = ["architecture", "whole_repo", "adversarial"]

# Latin square: item index mod 3 selects the rotation set.
LENS_SETS = [
    {"claude": "architecture", "gemini": "whole_repo", "grok": "adversarial"},
    {"claude": "adversarial", "gemini": "architecture", "grok": "whole_repo"},
    {"claude": "whole_repo", "gemini": "adversarial", "grok": "architecture"},
]

STRATA = [
    # (stratum, n_items, defects_per_item, crit_fraction, executable)
    ("S1_REVIEW_HUMAN", 14, (2, 6), 0.45, False),
    ("S2_PATCH_VERDICT", 10, (1, 2), 0.90, True),
    ("S3_VULN_POC", 8, (1, 2), 1.00, True),
    ("S4_FP_TRAP", 5, (0, 0), 0.00, True),
    ("S5_NULL", 3, (0, 0), 0.00, False),
]

DIFFICULTY_BY_KIND = {
    "Type1_Direct": 0.55,
    "Type2_Contextual": -0.15,
    "Type3_Latent": -0.85,
}


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def simulate(
    rng: np.random.Generator,
    b0: float = -0.55,
    fam_effect: dict | None = None,
    lens_effect: dict | None = None,
    interaction: float = 0.0,
    item_sd: float = 0.7,
    fabrication_rate: float = 0.14,
    plausible_rate: float = 0.30,
    trap_bait: dict | None = None,
    full_square: bool = True,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    fam_effect = fam_effect or {f: 0.0 for f in FAMILIES}
    lens_effect = lens_effect or {l: 0.0 for l in LENSES}
    trap_bait = trap_bait or {f: 0.35 for f in FAMILIES}

    corpus, runs, judge, execres = [], [], [], []
    n = 0
    for stratum, n_items, (dmin, dmax), crit_frac, executable in STRATA:
        for k in range(n_items):
            n += 1
            iid = f"{stratum[:2]}-{n:04d}"
            kind = rng.choice(
                list(DIFFICULTY_BY_KIND), p=[0.34, 0.40, 0.26]
            ) if stratum == "S1_REVIEW_HUMAN" else "Type2_Contextual"
            n_def = int(rng.integers(dmin, dmax + 1)) if dmax > 0 else 0
            labels = []
            for j in range(n_def):
                sev = 1 if rng.random() < crit_frac else 2
                lo = int(rng.integers(40, 900))
                labels.append(
                    {
                        "label_id": f"L{j+1}",
                        "severity": sev,
                        "kind": "correctness",
                        "sites": [{"path": f"src/{iid}/mod{j}.py", "lines": [lo, lo + 12]}],
                        "adjudication": "fail_to_pass_test" if executable else "human_review_comment",
                        **({"verify_cmd": f"pytest -k t_{iid}_{j}"} if executable else {}),
                    }
                )
            item = {
                "item_id": iid,
                "stratum": stratum,
                "difficulty": kind,
                "source": "sim",
                "repo_files": [f"src/{iid}/mod{j}.py" for j in range(max(1, n_def))],
                "labels": labels,
            }
            if stratum == "S4_FP_TRAP":
                item["trap"] = {
                    "trap_id": "T1",
                    "assertion": "seeded invalid finding",
                    "sites": [{"path": f"src/{iid}/trap.c", "lines": [100, 160]}],
                    "ground_truth": "invalid",
                }
                item["repo_files"].append(f"src/{iid}/trap.c")
            corpus.append(item)

            item_re = rng.normal(0, item_sd)
            sets = LENS_SETS if full_square else [LENS_SETS[n % 3]]
            for si, lset in enumerate(sets):
                for fam in FAMILIES:
                    lens = lset[fam]
                    rid_n = 0
                    evidence = []
                    run_id = f"{iid}-{fam}-s{si}"
                    # true positives
                    for lab in labels:
                        eta = (
                            b0
                            + fam_effect[fam]
                            + lens_effect[lens]
                            + item_re
                            + DIFFICULTY_BY_KIND.get(kind, 0.0)
                        )
                        # interaction: each family is boosted on one designated lens
                        if interaction and lens == LENS_SETS[0][fam]:
                            eta += interaction
                        if rng.random() < sigmoid(eta):
                            rid_n += 1
                            rid = f"{rid_n:02d}"
                            s = lab["sites"][0]
                            line = int(rng.integers(s["lines"][0], s["lines"][1] + 1))
                            evidence.append(
                                f"R{rid}|P{lab['severity']}|conf={rng.uniform(.4,.9):.2f}"
                                f"|claim=defect at {lab['label_id']}"
                                f"|evidence={s['path']}:{line} observed"
                                f"|impact=sim|verify=check {lab['label_id']}"
                            )
                            judge.append({"run_id": run_id, "claim_rid": rid,
                                          "verdict": "CONFIRMED", "label_id": lab["label_id"],
                                          "affinity": float(rng.uniform(.55, .95)),
                                          "panel": [x for x in FAMILIES if x != fam],
                                          "unanimous": True})
                            if lab.get("verify_cmd"):
                                execres.append({"run_id": run_id, "claim_rid": rid,
                                                "reproduced": True, "cmd": lab["verify_cmd"],
                                                "exit_code": 1})
                    # trap bait
                    if "trap" in item and rng.random() < trap_bait[fam]:
                        rid_n += 1
                        rid = f"{rid_n:02d}"
                        ts = item["trap"]["sites"][0]
                        evidence.append(
                            f"R{rid}|P0|conf={rng.uniform(.6,.95):.2f}"
                            f"|claim=exploitable condition"
                            f"|evidence={ts['path']}:{rng.integers(100,160)} timing differential"
                            f"|impact=sim|verify=timing harness"
                        )
                        judge.append({"run_id": run_id, "claim_rid": rid, "verdict": "PLAUSIBLE",
                                      "label_id": "", "affinity": 0.2,
                                      "panel": [x for x in FAMILIES if x != fam],
                                      "unanimous": False})
                        execres.append({"run_id": run_id, "claim_rid": rid, "reproduced": False,
                                        "cmd": "timing harness", "exit_code": 0})
                    # noise claims
                    for _ in range(int(rng.poisson(2.0))):
                        rid_n += 1
                        rid = f"{rid_n:02d}"
                        fab = rng.random() < fabrication_rate / max(1e-9, fabrication_rate + plausible_rate)
                        path = (f"src/{iid}/ghost.py" if fab
                                else item["repo_files"][0])
                        evidence.append(
                            f"R{rid}|P{rng.integers(1,4)}|conf={rng.uniform(.2,.7):.2f}"
                            f"|claim=noise|evidence={path}:{rng.integers(1,2000)}"
                            f"|impact=sim|verify=inspect"
                        )
                        judge.append({"run_id": run_id, "claim_rid": rid,
                                      "verdict": "FABRICATED" if fab else "PLAUSIBLE",
                                      "label_id": "", "affinity": 0.0 if fab else 0.25,
                                      "panel": [x for x in FAMILIES if x != fam],
                                      "unanimous": True})
                    runs.append({
                        "run_id": run_id, "item_id": iid, "arm": "D", "family": fam,
                        "lens": lens, "context_config": "retrieval",
                        "model_selector_expected": f"{fam}/pinned",
                        "model_selector_reported": f"{fam}/pinned",
                        "schema_valid": True, "tool_violations": 0, "wrote_to_repo": False,
                        "spawned_subagent": False, "evidence_cap": 12,
                        "latency_ms": int(rng.normal(40000, 9000)),
                        "input_tokens": int(rng.normal(20000, 5000)),
                        "output_tokens": int(rng.normal(1000, 250)),
                        "cost_usd": round(float(rng.uniform(.02, .25)), 4),
                        "quota_pool": f"{fam}-pool",
                        "evidence": evidence,
                    })
    return corpus, runs, judge, execres


def write_all(d: Path, corpus, runs, judge, execres):
    d.mkdir(parents=True, exist_ok=True)
    for name, rows in [("corpus.jsonl", corpus), ("runs.jsonl", runs),
                       ("judge.jsonl", judge), ("exec.jsonl", execres)]:
        (d / name).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return d


def run_pipeline(d: Path, boot=400, perm=400) -> dict:
    subprocess.run(
        [sys.executable, str(SCRIPTS / "score_lrhe.py"),
         "--corpus", str(d / "corpus.jsonl"), "--runs", str(d / "runs.jsonl"),
         "--judge", str(d / "judge.jsonl"), "--exec", str(d / "exec.jsonl"),
         "--out-claims", str(d / "claims.csv"), "--out-runs", str(d / "runs.csv"),
         "--out-report", str(d / "report.json")],
        check=True, capture_output=True,
    )
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "analyze_lrhe.py"),
         "--claims", str(d / "claims.csv"), "--runs", str(d / "runs.csv"),
         "--corpus", str(d / "corpus.jsonl"), "--boot", str(boot), "--perm", str(perm),
         "--out", str(d / "analysis.json")],
        check=True, capture_output=True, text=True,
    )
    return json.loads((d / "analysis.json").read_text())


def power_sweep(reps: int, deltas: list[float], boot: int) -> pd.DataFrame:
    """How often does the leave-one-out CI for a genuinely-weak family exclude 0?

    Setup: 'grok' contributes nothing beyond the other two (fam_effect lowered so
    its unique finds are rare); we ask how often the analysis correctly shows a
    non-trivial drop when a family IS carrying weight, at various effect sizes.
    """
    rows = []
    for delta in deltas:
        detected = 0
        for r in range(reps):
            rng = np.random.default_rng(1000 + r)
            corpus, runs, judge, execres = simulate(
                rng,
                fam_effect={"claude": 0.0, "gemini": 0.0, "grok": delta},
                interaction=0.0,
            )
            with tempfile.TemporaryDirectory() as td:
                d = write_all(Path(td), corpus, runs, judge, execres)
                res = run_pipeline(d, boot=boot, perm=1)
            loo = {x["configuration"]: x for x in res["leave_one_family_out"]}
            drop = loo.get("drop grok", {})
            lo = drop.get("delta_lo", float("nan"))
            if lo == lo and lo > 0.0:
                detected += 1
        rows.append({"family_logit_advantage": delta, "reps": reps,
                     "power_loo_ci_excludes_zero": detected / reps})
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=HERE / "sim")
    ap.add_argument("--power", action="store_true")
    ap.add_argument("--reps", type=int, default=40)
    ap.add_argument("--boot", type=int, default=400)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    if a.power:
        df = power_sweep(a.reps, [0.0, 0.4, 0.8, 1.2], a.boot)
        print(df.to_string(index=False))
        (HERE / "power.csv").write_text(df.to_csv(index=False))
        return 0

    rng = np.random.default_rng(a.seed)
    corpus, runs, judge, execres = simulate(
        rng,
        fam_effect={"claude": 0.30, "gemini": 0.00, "grok": -0.35},
        lens_effect={"architecture": 0.20, "whole_repo": -0.10, "adversarial": 0.00},
        interaction=0.45,
        trap_bait={"claude": 0.30, "gemini": 0.20, "grok": 0.55},
    )
    d = write_all(a.out_dir, corpus, runs, judge, execres)
    res = run_pipeline(d, boot=a.boot, perm=a.boot)
    print(json.dumps({k: res[k] for k in
                      ["n_items", "n_runs", "n_claims", "n_labeled_defects",
                       "n_critical_defects", "arm_critical_recall",
                       "leave_one_family_out", "unique_contribution", "overlap",
                       "lens_family_decomposition"]}, indent=2, default=str))
    print(f"\nartifacts in {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
