#!/usr/bin/env python3
"""
analyze_lrhe.py -- pre-registered analysis for the Lens-Rotation Historical Evaluation.

Key choice: the unit of analysis is the *labeled defect*, clustered by item.
40 review items carry a few hundred labeled defects, so defect-level analysis is
the difference between "no detectable effect" and a usable estimate -- but the
defects inside one item are not independent, so every interval here comes from a
bootstrap that resamples ITEMS, not defects. Reporting a defect-level binomial CI
would overstate precision by roughly the square root of the cluster size.

Outputs, in decision order:
  1. Arm contrasts        does adding critics raise verified critical recall?
  2. Marginal contribution   what does each family add that the others don't?
  3. Lens decomposition   is the gain from the family, the role, or the prompt?
  4. Cost of being wrong  false-positive burden, trap promotion, null-item FPs
  5. Diagnostics          judge reliability, contamination, gate compliance

Usage:
  analyze_lrhe.py --claims claims.csv --runs runs.csv --corpus corpus.jsonl \
      [--probe probe.csv] [--judge-calibration calib.csv] [--boot 10000]
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RNG = np.random.default_rng(20260726)
CRITICAL = {0, 1}


# ----------------------------------------------------------------- helpers

def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def cluster_bootstrap(
    df: pd.DataFrame,
    stat_fn,
    cluster_col: str = "item_id",
    strat_col: str | None = "stratum",
    B: int = 10000,
    alpha: float = 0.05,
) -> dict:
    """Percentile CI from resampling clusters with replacement, stratified."""
    point = stat_fn(df)
    if point is None or (isinstance(point, float) and np.isnan(point)):
        return {"point": float("nan"), "lo": float("nan"), "hi": float("nan"), "B": 0}

    if strat_col and strat_col in df.columns:
        groups = {
            s: sub[cluster_col].unique() for s, sub in df.groupby(strat_col, observed=True)
        }
    else:
        groups = {"_all": df[cluster_col].unique()}

    by_cluster = {k: v for k, v in df.groupby(cluster_col, observed=True)}
    draws = np.empty(B, dtype=float)
    for b in range(B):
        picks = []
        for _, ids in groups.items():
            if len(ids) == 0:
                continue
            picks.extend(RNG.choice(ids, size=len(ids), replace=True))
        # Re-key duplicated clusters so within-cluster correlation is preserved.
        frames = []
        for n, cid in enumerate(picks):
            f = by_cluster[cid].copy()
            f[cluster_col] = f"{cid}#{n}"
            frames.append(f)
        val = stat_fn(pd.concat(frames, ignore_index=True)) if frames else np.nan
        draws[b] = val if val is not None else np.nan
    draws = draws[~np.isnan(draws)]
    if draws.size == 0:
        return {"point": float(point), "lo": float("nan"), "hi": float("nan"), "B": 0}
    return {
        "point": float(point),
        "lo": float(np.quantile(draws, alpha / 2)),
        "hi": float(np.quantile(draws, 1 - alpha / 2)),
        "B": int(draws.size),
    }


def cohens_kappa(a: pd.Series, b: pd.Series) -> dict:
    cats = sorted(set(a.dropna()) | set(b.dropna()))
    idx = {c: i for i, c in enumerate(cats)}
    n = len(a)
    if n == 0 or len(cats) < 2:
        return {"kappa": float("nan"), "agreement": float("nan"), "n": int(n)}
    m = np.zeros((len(cats), len(cats)))
    for x, y in zip(a, b):
        m[idx[x], idx[y]] += 1
    po = np.trace(m) / n
    pe = float((m.sum(axis=0) / n) @ (m.sum(axis=1) / n))
    kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    return {"kappa": float(kappa), "agreement": float(po), "n": int(n)}


# ----------------------------------------------- build the defect-level table

# The condition key, in one place because it is repeated at every join and a
# mismatch between two copies of it is invisible in the output.
#
# `replicate` is load-bearing. Arm T is |council| independent runs of ONE family on
# ONE lens: without it those runs share a key, collapse into a single cell, and
# their union is reported as if a single reviewer had found everything. The
# same-family null is the only thing that distinguishes real diversification from
# resampling one model, so collapsing it does not weaken the analysis -- it removes
# the control entirely while still printing a number.
CONDITION_KEYS = ["item_id", "arm", "family", "lens", "replicate", "context_config"]


# Which arms may enter which statistic. This is not bookkeeping. Arm T is ONE family
# repeated, so letting it into a per-family statistic unions a family's caught-set
# with its own repeats -- inflating that family's `caught`, depressing every other
# family's `unique_share`, and quietly rigging the leave-one-out delta in favour of
# whichever family happened to be chosen as the null.
COUNCIL_ARMS = ("C", "D")   # cross-family review
FLOOR_ARM = "C"             # one floor-lens run per family
SQUARE_ARM = "D"            # the Latin square: the only arm where lens varies by design
NULL_ARM = "T"              # same-family replicates: the empirical null


def normalize_conditions(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce the condition columns to strings before any grouping.

    Empty CSV cells read back as NaN and `groupby` drops NaN keys by default. Arms
    A, B, C and probe all carry an empty `lens` and `replicate`, so grouping the raw
    frame deletes every one of their hits and scores those arms at zero recall --
    quietly, with no error and no empty-frame warning.
    """
    out = df.copy()
    for col in CONDITION_KEYS:
        out[col] = out[col].fillna("").astype(str) if col in out.columns else ""
    return out


def build_defect_table(claims: pd.DataFrame, corpus: list[dict]) -> pd.DataFrame:
    """One row per (item, label, condition) -> caught 0/1.

    Rows are generated from the full label inventory, so defects that nobody
    caught are present as zeros. A recall computed only over claims would be
    silently conditioned on detection.
    """
    labels = []
    for it in corpus:
        for lab in it.get("labels", []):
            labels.append(
                {
                    "item_id": it["item_id"],
                    "stratum": it.get("stratum", ""),
                    "difficulty": it.get("difficulty", ""),
                    "label_id": lab["label_id"],
                    "label_severity": int(lab.get("severity", 3)),
                    "label_kind": lab.get("kind", ""),
                    "adjudication": lab.get("adjudication", ""),
                    "executable": bool(lab.get("verify_cmd")),
                }
            )
    lab_df = pd.DataFrame(labels)
    if lab_df.empty:
        return lab_df

    claims = normalize_conditions(claims)
    conds = claims[CONDITION_KEYS].drop_duplicates().reset_index(drop=True)
    grid = lab_df.merge(conds, on="item_id", how="inner")

    hits = claims[claims["verdict"] == "CONFIRMED"].copy()
    hits = hits[hits["matched_label_id"].notna() & (hits["matched_label_id"] != "")]
    hits["caught"] = 1
    agg = (
        hits.groupby(CONDITION_KEYS + ["matched_label_id"], observed=True)
        .agg(caught=("caught", "max"), promoted=("promoted", "max"))
        .reset_index()
        .rename(columns={"matched_label_id": "label_id"})
    )
    out = grid.merge(agg, on=CONDITION_KEYS + ["label_id"], how="left")
    out["caught"] = out["caught"].fillna(0).astype(int)
    out["promoted"] = out["promoted"].fillna(0).astype(int)
    return out


# ---------------------------------------------------------------- statistics

def recall_crit(df: pd.DataFrame) -> float:
    sub = df[df["label_severity"].isin(CRITICAL)]
    return float(sub["caught"].mean()) if len(sub) else float("nan")


def union_recall(df: pd.DataFrame, families: list[str]) -> float:
    """Council recall: a defect counts as caught if ANY listed family caught it."""
    sub = df[df["family"].isin(families) & df["label_severity"].isin(CRITICAL)]
    if sub.empty:
        return float("nan")
    g = sub.groupby(["item_id", "label_id"], observed=True)["caught"].max()
    return float(g.mean())


def leave_one_out(defects: pd.DataFrame, families: list[str], B: int) -> pd.DataFrame:
    rows = []
    full = lambda d: union_recall(d, families)  # noqa: E731
    base = cluster_bootstrap(defects, full, B=B)
    rows.append({"configuration": "all families", **base, "delta": 0.0})
    for f in families:
        keep = [x for x in families if x != f]
        stat = lambda d, k=keep: union_recall(d, k)  # noqa: E731
        r = cluster_bootstrap(defects, stat, B=B)
        # Paired delta: recompute on the same resamples would be tighter, but the
        # simple difference of point estimates is what the keep/drop decision uses.
        d_stat = lambda d, k=keep: full(d) - union_recall(d, k)  # noqa: E731
        dd = cluster_bootstrap(defects, d_stat, B=B)
        rows.append(
            {
                "configuration": f"drop {f}",
                **r,
                "delta": dd["point"],
                "delta_lo": dd["lo"],
                "delta_hi": dd["hi"],
            }
        )
    return pd.DataFrame(rows)


def unique_contribution(defects: pd.DataFrame, families: list[str]) -> pd.DataFrame:
    sub = defects[defects["label_severity"].isin(CRITICAL)]
    piv = (
        sub.pivot_table(
            index=["item_id", "label_id"], columns="family", values="caught",
            aggfunc="max", observed=True,
        )
        .reindex(columns=families)
        .fillna(0)
        .astype(int)
    )
    rows = []
    for f in families:
        others = [x for x in families if x != f]
        only_f = int(((piv[f] == 1) & (piv[others].sum(axis=1) == 0)).sum())
        rows.append(
            {
                "family": f,
                "caught": int(piv[f].sum()),
                "unique_to_family": only_f,
                "unique_share": (only_f / piv[f].sum()) if piv[f].sum() else float("nan"),
            }
        )
    out = pd.DataFrame(rows)
    caught_by_any = int((piv.sum(axis=1) > 0).sum())
    caught_by_all = int((piv.sum(axis=1) == len(families)).sum())
    out.attrs["n_critical_defects"] = int(len(piv))
    out.attrs["caught_by_any"] = caught_by_any
    out.attrs["caught_by_all"] = caught_by_all
    out.attrs["jaccard"] = {
        f"{a}~{b}": float(
            ((piv[a] == 1) & (piv[b] == 1)).sum()
            / max(1, ((piv[a] == 1) | (piv[b] == 1)).sum())
        )
        for a, b in itertools.combinations(families, 2)
    }
    return out


def diversity_vs_null(defects: pd.DataFrame, B: int) -> dict:
    """Is cross-family review actually diversifying, or just three coin flips?

    THIS IS THE LOAD-BEARING COMPARISON. Simulation with three *statistically
    identical* reviewers (baseline detection 37%, 40 items, ~84 labeled defects)
    produces, on average, 4.9 "unique verified findings" for the third reviewer
    and a 5.7pp union-recall drop when it is removed. A genuinely stronger family
    produced 6.8 and 8.0pp; a genuinely weaker one still produced 2.9 and 3.4pp.

    So a raw unique-finding count or leave-one-out delta cannot distinguish
    diversity from independent noise -- both are positive by construction. The
    decision requires an empirical null: arm T runs ONE family as many times as the
    council has members, independently, on the same items. If the cross-family
    council's error overlap is no lower than the same-family replicates', the
    diversity premium is unsupported and resampling one model would buy the same
    coverage more cheaply.

    Reported statistic: mean pairwise Jaccard of caught-sets, cross-family minus
    same-family. Negative = real diversification. Jaccard over all pairs is better
    powered than the union-based delta because it does not collapse the council into
    one number.

    Both sides must be ONE run per column. Pooling arms C and D for the cross-family
    side -- which this did until the null was first actually exercised -- pivots on
    `family` with aggfunc=max and so unions each family's floor run with its |lenses|
    square runs, then compares that union against a single arm-T run. Larger sets
    overlap more, so the contrast is biased toward "no diversification": a real
    effect can be masked, and the section 7 promotion gate would refuse a council
    that actually works. Arm C is the like-for-like counterpart -- one floor-lens run
    per family, same lens and same cardinality as arm T.
    """
    triplicate = defects[defects["arm"] == NULL_ARM]
    null_items = set(triplicate["item_id"])
    council = defects[(defects["arm"] == FLOOR_ARM) & defects["item_id"].isin(null_items)]

    def mean_pair_jaccard(d: pd.DataFrame, key: str) -> float:
        sub = d[d["label_severity"].isin(CRITICAL)]
        if sub.empty or sub[key].nunique() < 2:
            return float("nan")
        piv = sub.pivot_table(index=["item_id", "label_id"], columns=key,
                              values="caught", aggfunc="max", observed=True).fillna(0)
        vals = []
        for a, b in itertools.combinations(piv.columns, 2):
            inter = ((piv[a] == 1) & (piv[b] == 1)).sum()
            union = ((piv[a] == 1) | (piv[b] == 1)).sum()
            if union:
                vals.append(inter / union)
        return float(np.mean(vals)) if vals else float("nan")

    # Guards first. The cross-family side is defined only on the items the null ran
    # on, so with no null there is no cross-family number to report either -- the
    # statistic is a contrast, not two independent measurements.
    out: dict = {"n_null_items": len(null_items), "cross_family_jaccard": None,
                 "same_family_jaccard": None, "contrast": None}
    if triplicate.empty:
        out["verdict"] = (
            f"NOT MEASURABLE -- arm {NULL_ARM} (same-family replicates) was not run. "
            "Without it, unique-finding counts and leave-one-out deltas cannot be "
            "interpreted: identical reviewers generate both. Run the null arm before "
            "promoting any lane."
        )
        return out
    if council.empty:
        out["verdict"] = (
            f"NOT MEASURABLE -- arm {NULL_ARM} ran on {len(null_items)} item(s) but "
            f"arm {FLOOR_ARM} did not run on any of them. The contrast needs one "
            "floor-lens run per family against one floor-lens run per replicate, on "
            "the same items. Arm D is not a substitute: its lens rotation gives each "
            "family several runs, and unioning them inflates cross-family overlap."
        )
        return out

    out["cross_family_jaccard"] = cluster_bootstrap(
        council, lambda d: mean_pair_jaccard(d, "family"), B=B
    )

    # No fallback to `family`. Every arm-T row is the same family by construction,
    # so keying on it gives one column, no pairs, and a silent nan -- which the
    # verdict below then reads as "no evidence of diversification". That reports the
    # absence of the control as a finding about the council. Refuse instead.
    reps = sorted(set(triplicate["replicate"]) - {""})
    if len(reps) < 2:
        out["same_family_jaccard"] = None
        out["contrast"] = None
        out["verdict"] = (
            f"NOT MEASURABLE -- arm T ran but carries {len(reps)} distinct replicate "
            "id(s). Independent repeats of one family ARE the empirical null; without "
            "distinct `replicate` values they collapse into one cell and their union "
            "is scored as though a single reviewer had found everything. Check that "
            "runs.jsonl sets `replicate` and that scoring preserved it."
        )
        return out

    out["same_family_jaccard"] = cluster_bootstrap(
        triplicate, lambda d: mean_pair_jaccard(d, "replicate"), B=B
    )

    both = pd.concat([council, triplicate], ignore_index=True)
    out["contrast"] = cluster_bootstrap(both, lambda d: (
        mean_pair_jaccard(d[d["arm"] == FLOOR_ARM], "family")
        - mean_pair_jaccard(d[d["arm"] == NULL_ARM], "replicate")
    ), B=B)
    hi = out["contrast"]["hi"]
    out["verdict"] = (
        "cross-family errors are less correlated than same-family: diversification supported"
        if hi == hi and hi < 0
        else "no evidence that cross-family review decorrelates errors more than "
             "resampling one family; the extra provider lanes are not yet justified "
             "on coverage grounds"
    )
    return out


def lens_family_decomposition(defects: pd.DataFrame, n_perm: int = 5000) -> dict:
    """Separate the family main effect, the lens main effect, and the interaction.

    This is the question the rotation exists to answer: is a lane earning its keep
    because of the model family, because of the role prompt, or only in a specific
    family x role pairing? With ~24 items the interaction is badly underpowered --
    it is reported as exploratory and tested by permuting the lens assignment
    WITHIN each item, which respects the Latin-square structure.
    """
    d = defects[defects["label_severity"].isin(CRITICAL)].copy()
    d = d[(d["family"] != "") & (d["lens"] != "")]
    if d.empty or d["lens"].nunique() < 2 or d["family"].nunique() < 2:
        return {"note": "insufficient family x lens coverage for decomposition"}

    cell = d.groupby(["family", "lens"], observed=True)["caught"].mean().unstack()
    grand = float(d["caught"].mean())
    fam_eff = (cell.mean(axis=1) - grand).to_dict()
    lens_eff = (cell.mean(axis=0) - grand).to_dict()
    resid = cell.copy()
    for f in cell.index:
        for l in cell.columns:
            resid.loc[f, l] = cell.loc[f, l] - grand - fam_eff[f] - lens_eff[l]
    obs = float(np.nansum(np.square(resid.values)))

    # Permute lens labels within item (and within family block) to build the null.
    perm = np.empty(n_perm, dtype=float)
    items = d["item_id"].unique()
    by_item = {i: sub for i, sub in d.groupby("item_id", observed=True)}
    for k in range(n_perm):
        frames = []
        for i in items:
            sub = by_item[i].copy()
            lenses = sub["lens"].unique()
            mapping = dict(zip(lenses, RNG.permutation(lenses)))
            sub["lens"] = sub["lens"].map(mapping)
            frames.append(sub)
        dd = pd.concat(frames, ignore_index=True)
        c2 = dd.groupby(["family", "lens"], observed=True)["caught"].mean().unstack()
        g2 = float(dd["caught"].mean())
        fe = (c2.mean(axis=1) - g2)
        le = (c2.mean(axis=0) - g2)
        r2 = c2.sub(g2).sub(fe, axis=0).sub(le, axis=1)
        perm[k] = float(np.nansum(np.square(r2.values)))
    p = float((np.sum(perm >= obs) + 1) / (n_perm + 1))

    return {
        "grand_mean_critical_recall": grand,
        "cell_means": cell.round(4).to_dict(),
        "family_main_effect": {k: round(v, 4) for k, v in fam_eff.items()},
        "lens_main_effect": {k: round(v, 4) for k, v in lens_eff.items()},
        "interaction_ss": round(obs, 6),
        "interaction_p_permutation": p,
        "n_perm": n_perm,
        "warning": (
            "Interaction is exploratory. Do not pin lens assignments on this test alone; "
            "with fewer than ~60 critical defects per cell it cannot distinguish a real "
            "family x role interaction from noise."
        ),
    }


def fp_burden(runs: pd.DataFrame, claims: pd.DataFrame, B: int) -> dict:
    out = {}
    for arm, sub in runs.groupby("arm", observed=True):
        sub = sub.copy()
        out[str(arm)] = {
            "fabrication_rate": cluster_bootstrap(sub, lambda d: float(d["fabrication_rate"].mean()), B=B),
            "refutation_rate": cluster_bootstrap(sub, lambda d: float(d["refutation_rate"].mean()), B=B),
            "claims_per_run": float(sub["n_claims"].mean()),
            "promoted_per_run": float(sub["n_promoted"].mean()),
        }
    traps = runs[runs["trap_promoted"].notna()]
    if len(traps):
        out["trap_promotion_by_family"] = (
            traps.groupby("family", observed=True)["trap_promoted"]
            .agg(["mean", "count"])
            .round(4)
            .to_dict("index")
        )
    nulls = runs[runs["null_item_fp"].notna()]
    if len(nulls):
        out["null_item_fp_by_family"] = (
            nulls.groupby("family", observed=True)["null_item_fp"]
            .agg(["mean", "sum", "count"])
            .round(4)
            .to_dict("index")
        )
    return out


def apply_contamination_mask(defects: pd.DataFrame, probe: pd.DataFrame) -> pd.DataFrame:
    """Drop (item, family) cells where the no-repository probe recalled the fix.

    Contamination is asymmetric across families -- cutoffs differ -- so it biases
    exactly the per-family comparison this evaluation exists to make. Dropping the
    affected cells is crude but honest; the alternative is a per-family correction
    nobody can defend.
    """
    if probe is None or probe.empty:
        return defects
    bad = set(
        zip(
            probe.loc[probe["probe_localized"].astype(bool), "item_id"],
            probe.loc[probe["probe_localized"].astype(bool), "family"],
        )
    )
    if not bad:
        return defects
    mask = [
        (i, f) not in bad for i, f in zip(defects["item_id"], defects["family"])
    ]
    return defects[pd.Series(mask, index=defects.index)]


def select_panel(claims: pd.DataFrame, runs: pd.DataFrame, args) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Narrow to one experiment and panel, and drop runs that failed a hard gate.

    Refusing rather than inferring is the point. A file holding one experiment
    today holds two the first time someone appends to it, and every statistic
    below would go on producing numbers -- pooling the three-family core lens
    experiment with the OpenCode floor panel into a mean that describes neither.
    """
    for name, df in (("claims", claims), ("runs", runs)):
        missing = {"experiment_id", "panel_id"} - set(df.columns)
        if missing:
            raise SystemExit(
                f"{name} is missing {sorted(missing)}; it predates panel-aware scoring. "
                f"Re-run score_lrhe.py rather than analysing it as though it belonged "
                f"to one panel."
            )

    def narrow(df: pd.DataFrame) -> pd.DataFrame:
        return df[(df["experiment_id"] == args.experiment_id)
                  & (df["panel_id"] == args.panel_id)]

    present = sorted(set(map(tuple, runs[["experiment_id", "panel_id"]].dropna().values)))
    claims, runs = narrow(claims), narrow(runs)
    if runs.empty:
        raise SystemExit(
            f"no runs for experiment_id={args.experiment_id!r} panel_id={args.panel_id!r}; "
            f"the file contains {present}"
        )

    selection = {"experiment_id": args.experiment_id, "panel_id": args.panel_id,
                 "other_panels_in_file": [p for p in present
                                          if p != (args.experiment_id, args.panel_id)]}

    if args.arms:
        claims = claims[claims["arm"].isin(args.arms)]
        runs = runs[runs["arm"].isin(args.arms)]
        selection["arms"] = list(args.arms)

    # A run that mutated the repository, ran the wrong model, or lost its telemetry
    # is not evidence about its family. score_lrhe records it rather than deleting
    # it, so the exclusion happens here, once, where the estimates are made.
    if "gate_failed" in runs.columns:
        failed = set(runs.loc[runs["gate_failed"].fillna(False).astype(bool), "run_id"])
        selection["gate_failed_runs_dropped"] = len(failed)
        if failed and not args.keep_gate_failed:
            runs = runs[~runs["run_id"].isin(failed)]
            claims = claims[~claims["run_id"].isin(failed)]
        elif failed:
            selection["gate_failed_runs_dropped"] = 0
            selection["gate_failed_runs_kept"] = len(failed)
    if runs.empty:
        raise SystemExit("every run in the selected panel failed a hard gate; nothing to analyse")
    return claims, runs, selection


# ----------------------------------------------------------------- driver

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--claims", required=True, type=Path)
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--probe", type=Path, default=None,
                    help="CSV: item_id,family,probe_localized -- contamination probe results")
    ap.add_argument("--judge-calibration", type=Path, default=None,
                    help="CSV: run_id,claim_rid,judge_verdict,human_verdict")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--perm", type=int, default=2000)
    ap.add_argument("--out", type=Path, default=Path("analysis.json"))
    ap.add_argument("--experiment-id", required=True,
                    help="required, not inferred. Two experiments pooled into one mean "
                         "is a number with no referent")
    ap.add_argument("--panel-id", required=True)
    ap.add_argument("--arms", nargs="*", default=None, metavar="ARM",
                    help=f"restrict to these arms (default: every arm present). Per-family "
                         f"statistics always use {' '.join(COUNCIL_ARMS)} regardless")
    ap.add_argument("--keep-gate-failed", action="store_true",
                    help="score runs that failed a hard gate. For diagnosing the gate "
                         "itself; never for a headline number")
    args = ap.parse_args(argv)

    claims = pd.read_csv(args.claims)
    runs = pd.read_csv(args.runs)
    corpus = read_jsonl(args.corpus)
    probe = pd.read_csv(args.probe) if args.probe and args.probe.exists() else None

    claims, runs, selection = select_panel(claims, runs, args)

    defects = build_defect_table(claims, corpus)
    if defects.empty:
        print("no labeled defects in corpus; nothing to analyse", file=sys.stderr)
        return 1

    n_before = len(defects)
    defects = apply_contamination_mask(defects, probe)
    # Per-family statistics run on the council arms ONLY. `families` derived from all
    # claims would include the author (arm A), the refuter (arm R) and probe rows,
    # none of which are first-pass critics, and would then be compared against each
    # other as though they were.
    council = defects[defects["arm"].isin(COUNCIL_ARMS)]
    families = sorted(f for f in council["family"].unique() if f)

    result: dict = {
        "selection": selection,
        "n_items": len(corpus),
        "n_runs": int(len(runs)),
        "n_claims": int(len(claims)),
        "n_labeled_defects": int(defects[["item_id", "label_id"]].drop_duplicates().shape[0]),
        "n_critical_defects": int(
            defects[defects["label_severity"].isin(CRITICAL)][["item_id", "label_id"]]
            .drop_duplicates().shape[0]
        ),
        "families": families,
        "council_arms": list(COUNCIL_ARMS),
        "contamination_cells_dropped": int(n_before - len(defects)),
        "bootstrap_B": args.boot,
    }

    # 1. arm contrasts
    arms = sorted(a for a in defects["arm"].dropna().unique() if a)
    result["arm_critical_recall"] = {
        str(a): cluster_bootstrap(defects[defects["arm"] == a], recall_crit, B=args.boot)
        for a in arms
    }

    # 2. marginal contribution
    if len(families) >= 2:
        loo = leave_one_out(council, families, B=args.boot)
        result["leave_one_family_out"] = loo.to_dict("records")
        uc = unique_contribution(council, families)
        result["unique_contribution"] = uc.to_dict("records")
        result["overlap"] = {
            "n_critical_defects": uc.attrs["n_critical_defects"],
            "caught_by_any": uc.attrs["caught_by_any"],
            "caught_by_all": uc.attrs["caught_by_all"],
            "pairwise_jaccard": uc.attrs["jaccard"],
        }

    # 2b. is the diversity real, or three coin flips?
    result["diversity_vs_null"] = diversity_vs_null(defects, B=args.boot)

    # 3. lens decomposition -- the Latin square only. Arm C and arm T both run the
    # floor lens, so including them adds a "floor" column that only some families
    # occupy, and the family x lens interaction is then computed over a grid with
    # structurally empty cells.
    result["lens_family_decomposition"] = lens_family_decomposition(
        defects[defects["arm"] == SQUARE_ARM], n_perm=args.perm
    )

    # 4. cost of being wrong
    result["false_positive_burden"] = fp_burden(runs, claims, B=args.boot)

    # 5. diagnostics
    diag: dict = {
        "gate_schema_valid": float(runs["schema_valid"].mean()) if "schema_valid" in runs else None,
        "gate_no_write": float((~runs["wrote_to_repo"].astype(bool)).mean()) if "wrote_to_repo" in runs else None,
        "gate_no_recursion": float((~runs["spawned_subagent"].astype(bool)).mean()) if "spawned_subagent" in runs else None,
        "gate_model_identity": (
            float(runs["model_identity_ok"].dropna().mean())
            if "model_identity_ok" in runs and runs["model_identity_ok"].notna().any() else None
        ),
        "gate_promoted_anchor_rate": (
            float(claims.loc[claims["promoted"].astype(bool), "has_anchor"].mean())
            if claims["promoted"].any() else None
        ),
        "median_latency_ms_by_family": (
            runs.groupby("family", observed=True)["latency_ms"].median().to_dict()
            if "latency_ms" in runs else None
        ),
        "cost_usd_by_family": (
            runs.groupby("family", observed=True)["cost_usd"].sum().round(4).to_dict()
            if "cost_usd" in runs else None
        ),
        "quota_pools_observed": (
            sorted(set(runs["quota_pool"].dropna().astype(str))) if "quota_pool" in runs else None
        ),
    }
    if probe is not None and not probe.empty:
        diag["contamination_rate_by_family"] = (
            probe.groupby("family", observed=True)["probe_localized"].mean().round(4).to_dict()
        )
    if args.judge_calibration and args.judge_calibration.exists():
        cal = pd.read_csv(args.judge_calibration)
        diag["judge_reliability"] = cohens_kappa(cal["judge_verdict"], cal["human_verdict"])
        diag["judge_reliability"]["gate"] = "promote only if kappa >= 0.70"
    result["diagnostics"] = diag

    args.out.write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
