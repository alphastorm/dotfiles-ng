#!/usr/bin/env python3
"""Invariants that must hold before a single provider request is paid for.

    ./.venv/bin/pytest test_invariants.py -q

These are not unit tests of convenience. Each one guards a failure that is
*silent*: the harness keeps running, writes a plausible number, and the number is
wrong. That class of bug cannot be caught by reading the output, which is exactly
why it gets its own file.

What each test defends:

  determinism      a matrix that changes between processes is not a design
  stratification   a null that misses the traps measures nothing about traps
  replicates       three runs collapsed into one cell IS the missing control
  arm hygiene      arm T inside a per-family statistic rigs it for one family
  NaN keys         pandas silently drops group keys, and empty lens is a key
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).parent
PY = sys.executable

sys.path.insert(0, str(HERE))
import analyze_lrhe  # noqa: E402
import build_corpus  # noqa: E402


# --------------------------------------------------------------- helpers

def _run(args: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ)
    env.update(env_extra or {})
    p = subprocess.run([PY, *args], cwd=HERE, capture_output=True, text=True, env=env)
    assert p.returncode == 0, f"{args} failed:\n{p.stdout}\n{p.stderr}"
    return p


def _corpus(path: Path) -> list[dict]:
    """A corpus that spans every stratum and both control kinds.

    Two S4 traps and two label-free items (a known-passing S2 control and an S5
    null) so `_subset_key` has something to stratify on beyond the stratum name.
    """
    items = []
    spec = [
        ("S1_REVIEW_HUMAN", 6, "Type1_Direct", 3),
        ("S1_REVIEW_HUMAN", 4, "Type3_Latent", 3),
        ("S2_PATCH_VERDICT", 4, "Type2_Contextual", 3),
        ("S2_PATCH_VERDICT", 2, "Type2_Contextual", 0),   # known-passing control
        ("S3_VULN_POC", 4, "Type2_Contextual", 3),
        ("S4_FP_TRAP", 4, "Type2_Contextual", 3),
        ("S5_NULL", 2, "Type2_Contextual", 0),            # null: nothing to find
    ]
    n = 0
    for stratum, count, difficulty, n_lab in spec:
        for _ in range(count):
            n += 1
            iid = f"{stratum[:2]}-{n:04d}"
            labels = [
                {
                    "label_id": f"L{j+1}",
                    "severity": 1,
                    "kind": "correctness",
                    "sites": [{"path": f"src/{iid}/mod{j}.py", "lines": [100, 120]}],
                    "adjudication": "human_review_comment",
                }
                for j in range(n_lab)
            ]
            it = {
                "item_id": iid, "stratum": stratum, "difficulty": difficulty,
                "source": "test", "labels": labels,
                "repo_files": [f"src/{iid}/mod{j}.py" for j in range(max(1, n_lab))],
            }
            if stratum == "S4_FP_TRAP":
                it["trap"] = {"trap_id": "T1", "assertion": "seeded", "ground_truth": "invalid",
                              "sites": [{"path": f"src/{iid}/trap.c", "lines": [10, 20]}]}
            items.append(it)
    path.write_text("\n".join(json.dumps(i) for i in items) + "\n")
    return items


def _runs(items: list[dict], path: Path, judge_path: Path,
          families: list[str], replicates: int) -> None:
    """Arm C (floor, one run per family), arm D (the Latin square), arm T (the null).

    Detection is deterministic: a run misses label j when (j + skew) % 3 == 0, so
    each skew catches a different two of every three labels and pairwise Jaccard is
    defined rather than degenerate.

    A family's skew is its index and does NOT vary with lens. That is deliberate:
    it keeps the council's per-family caught-set from saturating, which is the only
    way arm T contamination is observable at all. This fixture exercises plumbing
    and arm hygiene -- it does not model a lens effect, and no test reads one.
    """
    runs, judge = [], []

    def emit(run_id: str, item: dict, arm: str, family: str, replicate: str,
             skew: int, lens: str = "floor") -> None:
        evidence = []
        for j, lab in enumerate(item.get("labels", [])):
            if (j + skew) % 3 == 0:
                continue                                  # this reviewer misses it
            rid = f"{j+1:02d}"
            s = lab["sites"][0]
            evidence.append(
                f"R{rid}|P{lab['severity']}|conf=0.80|claim=defect {lab['label_id']}"
                f"|evidence={s['path']}:{s['lines'][0]} observed|impact=t|verify=t"
            )
            judge.append({"run_id": run_id, "claim_rid": rid, "verdict": "CONFIRMED",
                          "label_id": lab["label_id"], "affinity": 0.9,
                          "panel": [f for f in families if f != family], "unanimous": True})
        runs.append({
            "run_id": run_id, "item_id": item["item_id"], "arm": arm, "family": family,
            "lens": lens, "replicate": replicate, "context_config": "retrieval",
            "model_selector_expected": f"{family}/pinned",
            "model_selector_reported": f"{family}/pinned",
            "schema_valid": True, "tool_violations": 0, "wrote_to_repo": False,
            "spawned_subagent": False, "evidence_cap": 12, "evidence": evidence,
            "latency_ms": 1000, "input_tokens": 10, "output_tokens": 10,
            "cost_usd": 0.01, "quota_pool": "test",
        })

    lenses = ["architecture", "whole_repo", "adversarial"]
    for it in items:
        for fi, fam in enumerate(families):
            emit(f"{it['item_id']}-{fam}-c", it, "C", fam, "", fi)
        # Arm D: the Latin square, so every family draws every lens exactly once.
        for si in range(len(lenses)):
            for fi, fam in enumerate(families):
                emit(f"{it['item_id']}-{fam}-s{si}", it, "D", fam, f"set{si+1}",
                     fi, lenses[(fi + si) % len(lenses)])
        # Skews outside the council's range, so the null catches labels its own
        # family's council runs miss -- otherwise contamination is invisible.
        for r in range(replicates):
            emit(f"{it['item_id']}-{families[0]}-t{r+1}", it, "T", families[0],
                 f"rep{r+1}", 10 + r)

    path.write_text("\n".join(json.dumps(r) for r in runs) + "\n")
    judge_path.write_text("\n".join(json.dumps(j) for j in judge) + "\n")


@pytest.fixture
def scored(tmp_path: Path):
    """corpus -> runs -> score -> analyse, with arm C and a 3-replicate arm T."""
    corpus = tmp_path / "corpus.jsonl"
    items = _corpus(corpus)
    runs, judge = tmp_path / "runs.jsonl", tmp_path / "judge.jsonl"
    _runs(items, runs, judge, ["claude", "gemini", "grok"], replicates=3)

    claims_csv, runs_csv = tmp_path / "claims.csv", tmp_path / "runs.csv"
    _run(["score_lrhe.py", "--corpus", str(corpus), "--runs", str(runs),
          "--judge", str(judge), "--out-claims", str(claims_csv),
          "--out-runs", str(runs_csv), "--out-report", str(tmp_path / "report.json")])

    analysis = tmp_path / "analysis.json"
    _run(["analyze_lrhe.py", "--claims", str(claims_csv), "--runs", str(runs_csv),
          "--corpus", str(corpus), "--boot", "40", "--perm", "40", "--out", str(analysis)])
    return {
        "claims": pd.read_csv(claims_csv),
        "runs": pd.read_csv(runs_csv),
        "analysis": json.loads(analysis.read_text()),
        "corpus": corpus,
        "tmp": tmp_path,
    }


# ------------------------------------------------------- 1. determinism

def test_assignments_identical_across_hash_seeds(tmp_path: Path):
    """PYTHONHASHSEED must not reach the matrix.

    The failure this guards is not a crash. `hash()` is salted per process, so the
    arm-B family rotation and the arm-D subset would differ between the run that
    produced the preregistration and the run that produced the results, with no
    diff anywhere to show it.
    """
    corpus = tmp_path / "corpus.jsonl"
    _corpus(corpus)
    digests = []
    for seed in ("0", "1", "12345"):
        out = tmp_path / f"a-{seed}.csv"
        man = tmp_path / f"m-{seed}.json"
        _run(["build_corpus.py", "assignments", "--corpus", str(corpus),
              "--out", str(out), "--manifest", str(man), "--d-items", "12"],
             {"PYTHONHASHSEED": seed})
        digests.append((out.read_text(), json.loads(man.read_text())["assignments_sha256"]))

    assert digests[0][0] == digests[1][0] == digests[2][0]
    assert digests[0][1] == digests[1][1] == digests[2][1]


def test_salt_is_recorded_and_actually_reshuffles(tmp_path: Path):
    """A salt must change the matrix AND be visible in the manifest.

    A salt that is recorded but ignored is worse than none: it documents a
    reshuffle that did not happen.
    """
    corpus = tmp_path / "corpus.jsonl"
    _corpus(corpus)
    plain, salted = tmp_path / "p.csv", tmp_path / "s.csv"
    mp, ms = tmp_path / "mp.json", tmp_path / "ms.json"
    _run(["build_corpus.py", "assignments", "--corpus", str(corpus),
          "--out", str(plain), "--manifest", str(mp), "--d-items", "12"])
    _run(["build_corpus.py", "assignments", "--corpus", str(corpus),
          "--out", str(salted), "--manifest", str(ms), "--d-items", "12",
          "--assignment-salt", "epoch-2"])

    assert plain.read_text() != salted.read_text()
    assert json.loads(mp.read_text())["assignment_salt"] == ""
    assert json.loads(ms.read_text())["assignment_salt"] == "epoch-2"
    # Empty salt must remain byte-identical to the pre-salt behaviour.
    assert build_corpus._stable_hash("SI-0001") == build_corpus._stable_hash("SI-0001", "")
    assert build_corpus._stable_hash("SI-0001") != build_corpus._stable_hash("SI-0001", "epoch-2")


# ---------------------------------------------------- 2. stratification

def test_subset_spans_every_stratum_and_control_type(tmp_path: Path):
    """The arm-D/T subset is where the null lives. It has to reach the controls.

    Selecting the first N items in file order concentrates the subset in whichever
    strata sort first, so the traps and the label-free items -- the only places a
    false positive can be observed at all -- may never enter arm T.
    """
    corpus = tmp_path / "corpus.jsonl"
    items = _corpus(corpus)
    by_id = {i["item_id"]: i for i in items}
    man = tmp_path / "m.json"
    _run(["build_corpus.py", "assignments", "--corpus", str(corpus),
          "--out", str(tmp_path / "a.csv"), "--manifest", str(man), "--d-items", "14"])

    subset = json.loads(man.read_text())["d_subset"]
    picked = [by_id[i] for i in subset]

    assert {i["stratum"] for i in picked} == {i["stratum"] for i in items}
    assert any(i.get("trap") for i in picked), "no S4 trap in the subset"
    assert any(not i.get("labels") for i in picked), "no label-free control in the subset"
    assert len({build_corpus._subset_key(i) for i in picked}) >= 5


def test_manifest_digest_tracks_the_csv(tmp_path: Path):
    corpus = tmp_path / "corpus.jsonl"
    _corpus(corpus)
    out, man = tmp_path / "a.csv", tmp_path / "m.json"
    _run(["build_corpus.py", "assignments", "--corpus", str(corpus),
          "--out", str(out), "--manifest", str(man), "--d-items", "12"])

    recorded = json.loads(man.read_text())["assignments_sha256"]
    assert recorded == build_corpus._sha256_path(out)
    out.write_text(out.read_text() + "SI-0001,S1_REVIEW_HUMAN,C,claude,floor,\n")
    assert recorded != build_corpus._sha256_path(out), "digest did not notice an edit"


# ------------------------------------------------------- 3. replicates

def test_replicates_survive_scoring(scored):
    """rep1/rep2/rep3 must still be three distinct cells after scoring."""
    claims = scored["claims"]
    assert "replicate" in claims.columns
    reps = set(claims.loc[claims["arm"] == "T", "replicate"].dropna())
    assert reps == {"rep1", "rep2", "rep3"}
    assert "replicate" in scored["runs"].columns


def test_replicates_survive_analysis(scored):
    """The load-bearing comparison must produce a number, not a polite refusal."""
    dvn = scored["analysis"]["diversity_vs_null"]
    assert dvn["same_family_jaccard"] is not None, dvn["verdict"]
    assert dvn["contrast"] is not None
    assert 0.0 <= dvn["same_family_jaccard"]["point"] <= 1.0


def test_collapsed_replicates_refuse_rather_than_report(scored):
    """Blank out `replicate` and the analysis must decline, not fall back.

    The old fallback keyed the null on `family`, which is constant within arm T:
    one column, no pairs, silent nan -- and the verdict then read that nan as
    "no evidence of diversification", reporting the absence of the control as a
    finding about the council.
    """
    claims = scored["claims"].copy()
    claims["replicate"] = ""
    corpus = analyze_lrhe.read_jsonl(scored["corpus"])
    defects = analyze_lrhe.build_defect_table(claims, corpus)

    out = analyze_lrhe.diversity_vs_null(defects, B=20)
    assert out["same_family_jaccard"] is None
    assert out["contrast"] is None
    assert "NOT MEASURABLE" in out["verdict"]


# ------------------------------------------------------ 4. arm hygiene

def test_null_arm_excluded_from_per_family_statistics(scored):
    """Arm T is one family repeated. It must not enter a per-family comparison."""
    result = scored["analysis"]
    assert result["council_arms"] == list(analyze_lrhe.COUNCIL_ARMS)

    claims = scored["claims"]
    corpus = analyze_lrhe.read_jsonl(scored["corpus"])
    defects = analyze_lrhe.build_defect_table(claims, corpus)
    council = defects[defects["arm"].isin(analyze_lrhe.COUNCIL_ARMS)]
    families = sorted(f for f in council["family"].unique() if f)

    uc_council = analyze_lrhe.unique_contribution(council, families)
    uc_polluted = analyze_lrhe.unique_contribution(defects, families)
    null_family = "claude"
    caught_c = int(uc_council.loc[uc_council["family"] == null_family, "caught"].iloc[0])
    caught_p = int(uc_polluted.loc[uc_polluted["family"] == null_family, "caught"].iloc[0])
    assert caught_p > caught_c, (
        "arm T did not inflate the null family's caught-set, so this test is no "
        "longer exercising the contamination it exists to catch"
    )


def test_lens_decomposition_sees_only_the_square(scored):
    """Arm C and arm T both run the floor lens; the square is arm D alone.

    Mixing them adds a `floor` column that only some families occupy, and the
    family x lens interaction is then computed over a grid with structurally empty
    cells -- which reports an interaction that is an artefact of the missing cells.
    """
    cells = scored["analysis"]["lens_family_decomposition"]["cell_means"]
    assert "floor" not in cells, "floor lens leaked into the Latin square"


# --------------------------------------------------------- 5. NaN keys

def test_empty_lens_is_not_dropped_by_groupby(scored):
    """Empty string, not NaN. `groupby` drops NaN keys and takes the arm with it.

    Arms A, B, C and probe all carry an empty lens and replicate. Read back from
    CSV those are NaN, and every one of their hits vanishes from the defect table
    -- scoring four arms at zero recall with no error and no warning.
    """
    claims = scored["claims"]
    corpus = analyze_lrhe.read_jsonl(scored["corpus"])

    raw_c = claims[claims["arm"] == "C"]
    assert raw_c["replicate"].isna().all(), "fixture no longer reproduces NaN keys"

    defects = analyze_lrhe.build_defect_table(claims, corpus)
    caught_c = defects[(defects["arm"] == "C")]["caught"].sum()
    assert caught_c > 0, "arm C hits were dropped by a NaN group key"

    norm = analyze_lrhe.normalize_conditions(claims)
    assert not norm["replicate"].isna().any()
    assert (norm.loc[norm["arm"] == "C", "replicate"] == "").all()
