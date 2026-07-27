#!/usr/bin/env python3
"""
power_lrhe.py -- what effect size can this corpus actually resolve?

Answers the question that decides corpus size before any provider quota is spent:
"if a reviewer family really is pulling its weight, how often will the analysis
show it?" Simulates the defect-level generative model directly (no provider
calls, no scorer subprocess) and reports the hit rate of the two decisions that
matter:

  keep/drop     leave-one-family-out CI excludes 0
  lens pinning  family x lens interaction permutation test p < 0.05

Run this, pick a corpus size, then build the corpus. Not the other way round.

  power_lrhe.py --sweep-effect 0,0.4,0.8,1.2 --items 40 --reps 300
  power_lrhe.py --sweep-items 16,24,32,40,56 --effect 0.8 --reps 300
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

FAMILIES = ["claude", "gemini", "grok"]
LENSES = ["architecture", "whole_repo", "adversarial"]
LENS_SETS = [
    {"claude": "architecture", "gemini": "whole_repo", "grok": "adversarial"},
    {"claude": "adversarial", "gemini": "architecture", "grok": "whole_repo"},
    {"claude": "whole_repo", "gemini": "adversarial", "grok": "architecture"},
]


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def sim_defects(
    rng, n_items, defects_per_item, b0, fam_adv, lens_effect, interaction,
    item_sd, full_square,
):
    """Return long dataframe: item, label, family, lens, caught."""
    rows = []
    for i in range(n_items):
        item_re = rng.normal(0, item_sd)
        n_def = max(1, int(rng.poisson(defects_per_item)))
        sets = LENS_SETS if full_square else [LENS_SETS[i % 3]]
        for j in range(n_def):
            for si, lset in enumerate(sets):
                for f in FAMILIES:
                    lens = lset[f]
                    eta = b0 + item_re + lens_effect.get(lens, 0.0)
                    eta += fam_adv.get(f, 0.0)
                    if interaction and lens == LENS_SETS[0][f]:
                        eta += interaction
                    rows.append(
                        (f"I{i}", f"L{j}", f, lens, si, int(rng.random() < sigmoid(eta)))
                    )
    return pd.DataFrame(rows, columns=["item_id", "label_id", "family", "lens", "set", "caught"])


def union_recall(d, fams):
    s = d[d["family"].isin(fams)]
    if s.empty:
        return np.nan
    return float(s.groupby(["item_id", "label_id"], observed=True)["caught"].max().mean())


def loo_delta_ci(d, drop, B, rng):
    items = d["item_id"].unique()
    by = {k: v for k, v in d.groupby("item_id", observed=True)}
    keep = [f for f in FAMILIES if f != drop]
    draws = np.empty(B)
    for b in range(B):
        picks = rng.choice(items, size=len(items), replace=True)
        dd = pd.concat(
            [by[c].assign(item_id=f"{c}#{n}") for n, c in enumerate(picks)],
            ignore_index=True,
        )
        draws[b] = union_recall(dd, FAMILIES) - union_recall(dd, keep)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def interaction_p(d, n_perm, rng):
    def ss(dd):
        cell = dd.groupby(["family", "lens"], observed=True)["caught"].mean().unstack()
        g = float(dd["caught"].mean())
        fe = cell.mean(axis=1) - g
        le = cell.mean(axis=0) - g
        r = cell.sub(g).sub(fe, axis=0).sub(le, axis=1)
        return float(np.nansum(np.square(r.values)))

    obs = ss(d)
    by = {k: v for k, v in d.groupby("item_id", observed=True)}
    cnt = 0
    for _ in range(n_perm):
        frames = []
        for k, sub in by.items():
            s = sub.copy()
            ls = s["lens"].unique()
            s["lens"] = s["lens"].map(dict(zip(ls, rng.permutation(ls))))
            frames.append(s)
        if ss(pd.concat(frames, ignore_index=True)) >= obs:
            cnt += 1
    return (cnt + 1) / (n_perm + 1)


def one_rep(seed, n_items, defects_per_item, effect, b0, item_sd, B, n_perm,
            full_square, interaction):
    rng = np.random.default_rng(seed)
    fam_adv = {"claude": 0.0, "gemini": 0.0, "grok": effect}
    lens_effect = {"architecture": 0.15, "whole_repo": -0.10, "adversarial": 0.0}
    d = sim_defects(rng, n_items, defects_per_item, b0, fam_adv, lens_effect,
                    interaction, item_sd, full_square)
    lo, hi = loo_delta_ci(d, "grok", B, rng)
    p = interaction_p(d, n_perm, rng) if n_perm else np.nan
    return {"loo_detect": int(lo > 0.0), "inter_detect": int(p < 0.05) if n_perm else np.nan,
            "n_defects": int(d[["item_id", "label_id"]].drop_duplicates().shape[0])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", type=int, default=40)
    ap.add_argument("--defects-per-item", type=float, default=2.0)
    ap.add_argument("--effect", type=float, default=0.8,
                    help="logit advantage of the family under test")
    ap.add_argument("--interaction", type=float, default=0.0)
    ap.add_argument("--b0", type=float, default=-0.55,
                    help="baseline logit; -0.55 ~ 37%% per-family detection, in line with "
                         "published single-model review detection rates")
    ap.add_argument("--item-sd", type=float, default=0.7)
    ap.add_argument("--reps", type=int, default=200)
    ap.add_argument("--boot", type=int, default=400)
    ap.add_argument("--perm", type=int, default=0)
    ap.add_argument("--half-square", action="store_true",
                    help="one lens-set per item (between-item lens design) instead of all three")
    ap.add_argument("--sweep-effect", type=str, default=None)
    ap.add_argument("--sweep-items", type=str, default=None)
    a = ap.parse_args()

    def run(n_items, effect):
        res = [one_rep(9000 + r, n_items, a.defects_per_item, effect, a.b0, a.item_sd,
                       a.boot, a.perm, not a.half_square, a.interaction)
               for r in range(a.reps)]
        df = pd.DataFrame(res)
        return {
            "items": n_items,
            "effect_logit": effect,
            "mean_labeled_defects": round(float(df["n_defects"].mean()), 1),
            "power_keep_drop": round(float(df["loo_detect"].mean()), 3),
            "power_interaction": (round(float(df["inter_detect"].mean()), 3)
                                  if a.perm else None),
            "reps": a.reps,
        }

    rows = []
    if a.sweep_effect:
        for e in [float(x) for x in a.sweep_effect.split(",")]:
            rows.append(run(a.items, e))
    elif a.sweep_items:
        for n in [int(x) for x in a.sweep_items.split(",")]:
            rows.append(run(n, a.effect))
    else:
        rows.append(run(a.items, a.effect))

    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
