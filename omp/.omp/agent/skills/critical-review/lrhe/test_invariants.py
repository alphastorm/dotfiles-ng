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
import re
import subprocess
import datetime
import csv
import sys
from datetime import timedelta
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

import pandas as pd
import pytest

HERE = Path(__file__).parent
PY = sys.executable
DATA_RIGHTS_SCHEMA = json.loads((HERE / "data-rights.schema.json").read_text(encoding="utf-8"))

sys.path.insert(0, str(HERE))
import analyze_lrhe  # noqa: E402
import build_corpus  # noqa: E402
import make_fixtures  # noqa: E402

EXPERIMENT_ID = "lrhe-test-v1"
PANEL_ID = "test-panel-v1"


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
        runs.append(make_fixtures.to_v2({
            "run_id": run_id, "item_id": item["item_id"], "arm": arm, "family": family,
            "lens": lens, "replicate": replicate, "context_config": "retrieval",
            "model_selector_expected": f"{family}/pinned",
            "model_selector_reported": f"{family}/pinned",
            "schema_valid": True, "tool_violations": 0, "wrote_to_repo": False,
            "spawned_subagent": False, "evidence_cap": 12, "evidence": evidence,
            "latency_ms": 1000, "input_tokens": 10, "output_tokens": 10,
            "cost_usd": 0.01, "quota_pool": "test",
        }, experiment_id=EXPERIMENT_ID, panel_id=PANEL_ID))

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
          "--judge", str(judge), "--experiment-id", EXPERIMENT_ID, "--panel-id", PANEL_ID,
          "--out-claims", str(claims_csv),
          "--out-runs", str(runs_csv), "--out-report", str(tmp_path / "report.json")])

    analysis = tmp_path / "analysis.json"
    _run(["analyze_lrhe.py", "--claims", str(claims_csv), "--runs", str(runs_csv),
          "--corpus", str(corpus), "--boot", "40", "--perm", "40",
          "--experiment-id", EXPERIMENT_ID, "--panel-id", PANEL_ID, "--out", str(analysis)])
    return {
        "claims": pd.read_csv(claims_csv),
        "runs": pd.read_csv(runs_csv),
        "analysis": json.loads(analysis.read_text()),
        "corpus": corpus,
        "tmp": tmp_path,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _base_packet_item(item_id: str, goal: str = "Refactor module behavior", *,
                     repo: str = "acme/widget",
                     problem_statement: str = "Fix issue in the existing module.",
                     known_open_questions: str = "",
                     license_: str = "MIT",
                     license_url: str = "https://spdx.org/licenses/MIT.html",
                     provider_data_allowlist: list[str] = ("opencode",),
                     base_commit: str = "abc123",
                     source: str = "test-corpus") -> dict:
    return {
        "item_id": item_id,
        "stratum": "S1_REVIEW_HUMAN",
        "source": source,
        "labels": [],
        "repo_files": ["src/widget.py"],
        "repo": repo,
        "source_item_id": f"{item_id}-src",
        "dataset_ref": f"{item_id}-dataset",
        "base_commit": base_commit,
        "goal": goal,
        "problem_statement": problem_statement,
        "known_open_questions": known_open_questions,
        "provider_data_allowlist": list(provider_data_allowlist),
        "license": license_,
        "license_url": license_url,
    }


def _build_policy(provider_route: str, policy_id: str, **overrides) -> dict:
    policy = {
        "policyId": policy_id,
        "providerRoute": provider_route,
        "accountType": "test",
        "dataAllowlistKey": "opencode" if provider_route == "opencode-go" else "anthropic",
        "termsSnapshotId": "test-terms-2026-07-27",
        "providerTrainingUse": "prohibited_by_provider_documentation",
        "providerRetention": "zero_retention_by_provider_documentation",
        "rawOutputCaptureStatus": "allowed",
        "internalEvaluationAllowed": True,
        "routerTrainingAllowed": True,
        "modelTrainingAllowed": False,
        "publicCorpusAllowed": True,
        "carrythroughOwnedInternalAllowed": True,
        "customerDataAllowed": False,
        "thirdPartyConfidentialAllowed": False,
    }
    if provider_route == "claude-code-subscription":
        policy["requiredControls"] = {"modelImprovementEnabled": False}
    policy.update(overrides)
    return policy

def _write_policy_registry(tmp_path: Path, policy: dict) -> Path:
    path = tmp_path / "provider-policies.yaml"
    path.write_text(
        yaml.safe_dump(
            {"schemaVersion": 1, "policies": [policy]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _run_check_data_rights(tmp_path: Path, policy: dict, args: list[str]) -> subprocess.CompletedProcess:
    registry = _write_policy_registry(tmp_path, policy)
    return subprocess.run(
        [
            PY,
            "check_data_rights.py",
            *args,
            "--policies", str(registry),
            "--policy-schema", str(HERE / "provider-policy.schema.json"),
            "--data-rights-schema", str(HERE / "data-rights.schema.json"),
        ],
        cwd=HERE,
        capture_output=True,
        text=True,
    )


def _run_packet_gates(tmp_path: Path, cmd: str, corpus: list[dict], packets: list[dict],
                      *extra: str):
    corpus_path = tmp_path / "corpus.jsonl"
    packets_path = tmp_path / "packets.jsonl"
    _write_jsonl(corpus_path, corpus)
    _write_jsonl(packets_path, packets)
    return subprocess.run(
        [PY, "check_packet_gates.py", cmd, "--corpus", str(corpus_path),
         "--packets", str(packets_path), *extra],
        cwd=HERE, capture_output=True, text=True,
    )


def _assert_data_rights_schema(record: dict) -> None:
    errors = sorted(
        Draft202012Validator(DATA_RIGHTS_SCHEMA, format_checker=FormatChecker())
        .iter_errors(record),
        key=lambda e: (list(e.absolute_path), e.message),
    )
    assert not errors, "data-rights schema check failed: " + "; ".join(
        "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path)
        + f": {e.message}" for e in errors
    )


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


# ------------------------------------------------------- 6. panel hygiene

def _one_run(**over) -> dict:
    """A single valid v2 run record, with overrides applied after the envelope."""
    base = make_fixtures.to_v2({
        "run_id": "r1", "item_id": "S1-0001", "arm": "C", "family": "claude",
        "lens": "floor", "replicate": "", "context_config": "retrieval",
        "model_selector_expected": "claude/pinned", "model_selector_reported": "claude/pinned",
        "schema_valid": True, "tool_violations": 0, "wrote_to_repo": False,
        "spawned_subagent": False, "evidence_cap": 12, "evidence": [],
        "latency_ms": 1, "input_tokens": 1, "output_tokens": 1,
        "cost_usd": 0.0, "quota_pool": "test",
    }, experiment_id=EXPERIMENT_ID, panel_id=PANEL_ID)
    for path, value in over.items():
        head, _, tail = path.partition(".")
        if tail:
            base[head][tail] = value
        else:
            base[head] = value
    return base


def _score(tmp_path: Path, runs: list[dict], *extra: str, corpus=None):
    corpus_path = tmp_path / "corpus.jsonl"
    if corpus is None:
        _corpus(corpus_path)
    else:
        corpus_path.write_text("\n".join(json.dumps(i) for i in corpus) + "\n")
    runs_path = tmp_path / "r.jsonl"
    runs_path.write_text("\n".join(json.dumps(r) for r in runs) + "\n")
    import os
    return subprocess.run(
        [PY, "score_lrhe.py", "--corpus", str(corpus_path), "--runs", str(runs_path),
         "--experiment-id", EXPERIMENT_ID, "--panel-id", PANEL_ID,
         "--out-claims", str(tmp_path / "c.csv"), "--out-runs", str(tmp_path / "r.csv"),
         "--out-report", str(tmp_path / "rep.json"), *extra],
        cwd=HERE, capture_output=True, text=True, env=dict(os.environ))


# ------------------------------------------------- 6. execution evidence hygiene

def test_model_refuter_opinion_cannot_enter_execution_precedence(tmp_path: Path):
    """A proposed command is not proof that it ran, even when the opinion says it did."""
    opinion_path = tmp_path / "refuter-opinions.jsonl"
    _write_jsonl(opinion_path, [{
        "run_id": "r1",
        "claim_rid": "1",
        "ran": True,
        "reproduced": False,
        "cmd": "pytest tests/test_widget.py::test_failure",
        "refuter_family": "glm",
        "exit_code": 0,
    }])
    run = _one_run(evidence=[
        "R1|P1|conf=0.90|claim=widget fails|evidence=src/widget.py:1 "
        "observed|impact=incorrect result|verify=run the regression test",
    ])

    p = _score(tmp_path, [run], "--exec", str(opinion_path))

    assert p.returncode != 0
    assert f"{opinion_path}:1:" in p.stderr
    assert "invalid execution-evidence requirement(s)" in p.stderr
    assert "refusing to score" in p.stderr
    assert not (tmp_path / "c.csv").exists(), "opinion reached claim scoring"
    assert not (tmp_path / "r.csv").exists(), "opinion reached run scoring"
    assert not (tmp_path / "rep.json").exists(), "opinion produced a score report"


def test_ingest_refutation_writes_diagnostic_opinions_without_execution_fields(
    tmp_path: Path,
):
    """Every cold-refuter outcome stays diagnostic, including unresolved answers."""
    prompts_path = tmp_path / "prompts.jsonl"
    responses_path = tmp_path / "responses.jsonl"
    opinions_path = tmp_path / "opinions.jsonl"
    outcomes = ("confirmed", "falsified", "unresolved")
    _write_jsonl(prompts_path, [
        {
            "refute_id": f"rf-{outcome}",
            "run_id": "r1",
            "claim_rid": str(i),
            "family": "glm",
        }
        for i, outcome in enumerate(outcomes, 1)
    ])
    _write_jsonl(responses_path, [
        {
            "refute_id": f"rf-{outcome}",
            "outcome": outcome,
            "primary_evidence": "e" * 450,
            "verification_procedure": "v" * 450,
            "rationale": "r" * 450,
        }
        for outcome in outcomes
    ])

    p = _run([
        "judge_lrhe.py",
        "ingest-refutation",
        "--prompts",
        str(prompts_path),
        "--responses",
        str(responses_path),
        "--out",
        str(opinions_path),
    ])

    rows = _read_jsonl(opinions_path)
    expected_keys = {
        "run_id",
        "claim_rid",
        "kind",
        "outcome",
        "refuter_family",
        "primary_evidence",
        "proposed_verification",
        "rationale",
    }
    assert len(rows) == 3
    assert {row["outcome"] for row in rows} == set(outcomes)
    assert all(set(row) == expected_keys for row in rows)
    assert all(row["kind"] == "model_opinion" for row in rows)
    assert all(len(row["primary_evidence"]) == 400 for row in rows)
    assert all(len(row["proposed_verification"]) == 400 for row in rows)
    assert all(len(row["rationale"]) == 400 for row in rows)
    assert "not execution evidence" in p.stdout


def test_analysis_refuses_mixed_panels(scored, tmp_path: Path):
    """Two experiments in one file must stop the analysis, not be averaged.

    A mean over the core lens experiment and the OpenCode floor panel describes
    neither. This is the failure that has no symptom: every statistic still
    produces a number.
    """
    claims, runs = scored["claims"].copy(), scored["runs"].copy()
    other_c, other_r = claims.copy(), runs.copy()
    other_c["experiment_id"] = other_r["experiment_id"] = "lrhe-other-v1"
    other_c["run_id"] = other_c["run_id"] + "-x"
    other_r["run_id"] = other_r["run_id"] + "-x"

    cpath, rpath = tmp_path / "mixed-claims.csv", tmp_path / "mixed-runs.csv"
    pd.concat([claims, other_c]).to_csv(cpath, index=False)
    pd.concat([runs, other_r]).to_csv(rpath, index=False)

    import os
    p = subprocess.run(
        [PY, "analyze_lrhe.py", "--claims", str(cpath), "--runs", str(rpath),
         "--corpus", str(scored["corpus"]), "--boot", "20", "--perm", "20",
         "--experiment-id", "lrhe-nonexistent-v1", "--panel-id", PANEL_ID,
         "--out", str(tmp_path / "a.json")],
        cwd=HERE, capture_output=True, text=True, env=dict(os.environ))
    assert p.returncode != 0
    assert "no runs for experiment_id" in (p.stdout + p.stderr)

    # And the legitimate selection must take only its own half.
    ok = subprocess.run(
        [PY, "analyze_lrhe.py", "--claims", str(cpath), "--runs", str(rpath),
         "--corpus", str(scored["corpus"]), "--boot", "20", "--perm", "20",
         "--experiment-id", EXPERIMENT_ID, "--panel-id", PANEL_ID,
         "--out", str(tmp_path / "b.json")],
        cwd=HERE, capture_output=True, text=True, env=dict(os.environ))
    assert ok.returncode == 0, ok.stderr
    sel = json.loads((tmp_path / "b.json").read_text())["selection"]
    assert sel["other_panels_in_file"], "the discarded panel was not even noticed"


# --------------------------------------------------------- 7. fail closed

def test_missing_hard_gate_field_is_refused(tmp_path: Path):
    """Absent telemetry must not validate. It used to default to success."""
    run = _one_run()
    del run["safety"]["wrote_to_repo"]
    p = _score(tmp_path, [run])
    assert p.returncode != 0
    assert "wrote_to_repo" in (p.stdout + p.stderr)


def test_missing_new_provenance_field_is_refused(tmp_path: Path):
    """Section 7 requires explicit provenance fields; absence is a schema failure."""
    run = _one_run()
    del run["reviewer"]["product_route"]
    p = _score(tmp_path, [run])
    assert p.returncode != 0
    assert "product_route" in (p.stdout + p.stderr)


def test_model_mismatch_fails_identity(tmp_path: Path):
    """A Fable request answered by Opus is an Opus result, never a Fable one."""
    ok = _score(tmp_path, [_one_run()])
    assert ok.returncode == 0, ok.stderr
    assert json.loads((tmp_path / "rep.json").read_text())["gate_failed_runs"] == 0

    bad = _score(tmp_path, [_one_run(**{
        "reviewer.served_model": "someone-else/model",
        "reviewer.fallback_detected": True,
        "reviewer.identity_verified": False,
    })])
    assert bad.returncode == 0, bad.stderr
    report = json.loads((tmp_path / "rep.json").read_text())
    assert report["gate_failed_runs"] == 1
    assert {"model_mismatch", "fallback_detected", "identity_unverified"} <= set(
        report["gate_failure_reasons"])


def test_duplicate_run_id_is_refused(tmp_path: Path):
    """Two runs sharing an id overwrite each other in every join downstream, and
    the totals still look right."""
    p = _score(tmp_path, [_one_run(), _one_run(item_id="S2-0011")])
    assert p.returncode != 0
    assert "duplicate run_id" in (p.stdout + p.stderr)


def test_stale_assignment_manifest_is_flagged(tmp_path: Path):
    """A run scheduled by a superseded matrix is not comparable to one that was not."""
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"assignments_sha256": "sha256:the-real-one"}))
    p = _score(tmp_path, [_one_run()], "--manifest", str(manifest))
    assert p.returncode == 0, p.stderr
    report = json.loads((tmp_path / "rep.json").read_text())
    assert report["gate_failure_reasons"].get("stale_assignment_manifest") == 1


# ------------------------------------------------ 8. legitimate emptiness

def test_s5_empty_evidence_is_valid_not_a_failure(tmp_path: Path):
    """Finding nothing on a null item is the CORRECT answer, not a broken run.

    An S5 item has no defect to find. A reviewer that returns an empty evidence
    list has passed, and anything that converts that into a gate failure or a
    parse failure inverts the one stratum built to measure false positives.
    """
    null_item = [{"item_id": "S5-9001", "stratum": "S5_NULL", "difficulty": "control",
                  "source": "test", "labels": [], "repo_files": ["src/x.py"]}]
    p = _score(tmp_path, [_one_run(item_id="S5-9001", evidence=[])], corpus=null_item)
    assert p.returncode == 0, p.stderr
    report = json.loads((tmp_path / "rep.json").read_text())
    assert report["gate_failed_runs"] == 0, report["gate_failure_reasons"]
    assert report["n_claims"] == 0

    runs = pd.read_csv(tmp_path / "r.csv")
    assert bool(runs.loc[0, "cap_respected"])
    assert runs.loc[0, "null_item_fp"] == 0


def test_unanchored_claims_are_never_promoted(scored):
    """Promotion requires an anchor. The gate is section 8's anchor-rate metric,
    and a promoted claim without one would make that metric measure the regex
    rather than the reviewer."""
    claims = scored["claims"]
    promoted = claims[claims["promoted"].fillna(False).astype(bool)]
    assert len(promoted) > 0, "fixture promotes nothing, so this proves nothing"
    assert promoted["has_anchor"].all()
    assert (promoted["verdict"] != "CONFIRMED_UNANCHORED").all()


# ----------------------------------------------------- 9. data-rights gate


def test_check_data_rights_public_corpus_on_opencode_go_allows_and_validates_schema(tmp_path: Path):
    """Public corpus over OpenCode must pass the gate and emit a schema-valid record."""
    policy = _build_policy("opencode-go", "opencode-go-public-corpus")
    p = _run_check_data_rights(
        tmp_path,
        policy,
        [
            "--item-id", "S1-0001",
            "--classification", "public_corpus",
            "--provider-route", "opencode-go",
            "--policy-id", policy["policyId"],
            "--item-provider-allowlist", policy["dataAllowlistKey"],
        ],
    )
    assert p.returncode == 0
    record = json.loads(p.stdout)
    assert record["egress_decision"] == "allow"
    _assert_data_rights_schema(record)


@pytest.mark.parametrize("provider_route", ["opencode-go", "claude-code-subscription"])
@pytest.mark.parametrize("classification", [
    "customer_confidential",
    "third_party_confidential",
    "secrets_or_credentials",
    "unknown",
])
def test_check_data_rights_sensitive_classifications_are_denied_on_all_routes(
        tmp_path: Path, provider_route: str, classification: str):
    """Sensitive item classes must be blocked before any packet can be assembled."""
    policy = _build_policy(provider_route, f"{provider_route}-sensitivity-policy")
    args = [
        "--item-id", "S1-0001",
        "--classification", classification,
        "--provider-route", provider_route,
        "--policy-id", policy["policyId"],
        "--item-provider-allowlist", policy["dataAllowlistKey"],
    ]
    if provider_route == "claude-code-subscription":
        args += ["--model-improvement-enabled", "false"]
    p = _run_check_data_rights(tmp_path, policy, args)
    assert p.returncode == 10
    reason = json.loads(p.stdout)["reason_code"]
    assert reason in {"classification_not_permitted", "classification_blocked"}


def test_check_data_rights_unrecognized_classification_is_unresolved(tmp_path: Path):
    """Unrecognized classes must stay unresolved, because they are missing legal facts."""
    policy = _build_policy("opencode-go", "opencode-go-unknown")
    p = _run_check_data_rights(
        tmp_path,
        policy,
        [
            "--item-id", "S1-0001",
            "--classification", "not_a_real_class",
            "--provider-route", "opencode-go",
            "--policy-id", policy["policyId"],
            "--item-provider-allowlist", policy["dataAllowlistKey"],
        ],
    )
    assert p.returncode == 20
    assert json.loads(p.stdout)["reason_code"] == "unknown_classification"


def test_check_data_rights_carrythrough_owned_internal_requires_item_authorization(tmp_path: Path):
    """Carrythrough-internal items need explicit per-item authorization; otherwise they are denied."""
    policy = _build_policy("opencode-go", "opencode-go-carrythrough")
    common = [
        "--item-id", "S1-0001",
        "--classification", "carrythrough_owned_internal",
        "--provider-route", "opencode-go",
        "--policy-id", policy["policyId"],
        "--item-provider-allowlist", policy["dataAllowlistKey"],
    ]

    denied = _run_check_data_rights(tmp_path, policy, common)
    assert denied.returncode == 10
    assert json.loads(denied.stdout)["reason_code"] == "item_authorization_required"

    passed = _run_check_data_rights(
        tmp_path,
        policy,
        common + ["--item-authorized"],
    )
    assert passed.returncode == 0
    record = json.loads(passed.stdout)
    assert record["classification"] == "carrythrough_owned_internal"
    _assert_data_rights_schema(record)


def test_check_data_rights_claude_route_respects_model_improvement_gate(tmp_path: Path):
    """A stale or permissive model-improvement signal must block Claude usage."""
    policy = _build_policy("claude-code-subscription", "claude-model-improvement")
    denied = _run_check_data_rights(
        tmp_path,
        policy,
        [
            "--item-id", "S1-0001",
            "--classification", "public_corpus",
            "--provider-route", "claude-code-subscription",
            "--policy-id", policy["policyId"],
            "--item-provider-allowlist", policy["dataAllowlistKey"],
            "--model-improvement-enabled", "true",
        ],
    )
    assert denied.returncode == 10
    assert json.loads(denied.stdout)["reason_code"] == "control_violated"

    allowed = _run_check_data_rights(
        tmp_path,
        policy,
        [
            "--item-id", "S1-0001",
            "--classification", "public_corpus",
            "--provider-route", "claude-code-subscription",
            "--policy-id", policy["policyId"],
            "--item-provider-allowlist", policy["dataAllowlistKey"],
            "--model-improvement-enabled", "false",
        ],
    )
    assert allowed.returncode == 0


def test_check_data_rights_stale_observation_records_are_unresolved(tmp_path: Path):
    """Recorded controls that exceed the freshness window must not be trusted as current."""
    policy = _build_policy("claude-code-subscription", "claude-stale-observation")
    observed_path = tmp_path / "observed-controls.yml"
    stale = (datetime.datetime.now(datetime.timezone.utc).date() - timedelta(days=2)).isoformat()
    observed_path.write_text(
        yaml.safe_dump({
            "observations": [{
                "providerRoute": "claude-code-subscription",
                "observedAt": stale,
                "controls": {"modelImprovementEnabled": False},
            }]
        }),
        encoding="utf-8",
    )
    p = _run_check_data_rights(
        tmp_path,
        policy,
        [
            "--item-id", "S1-0001",
            "--classification", "public_corpus",
            "--provider-route", "claude-code-subscription",
            "--policy-id", policy["policyId"],
            "--item-provider-allowlist", policy["dataAllowlistKey"],
            "--observed-controls", str(observed_path),
            "--max-observation-age-days", "1",
        ],
    )
    assert p.returncode == 20
    assert json.loads(p.stdout)["reason_code"] == "stale_or_missing_observation"


@pytest.mark.parametrize("item_allowlist", [[], ["anthropic"]])
def test_check_data_rights_item_allowlist_denies_routes_without_opt_in(tmp_path: Path, item_allowlist: list[str]):
    """Only routes named in an item allowlist may proceed; no implicit fallback is allowed."""
    policy = _build_policy("opencode-go", "opencode-go-allowlist")
    args = [
        "--item-id", "S1-0001",
        "--classification", "public_corpus",
        "--provider-route", "opencode-go",
        "--policy-id", policy["policyId"],
        "--item-provider-allowlist",
    ] + item_allowlist
    p = _run_check_data_rights(tmp_path, policy, args)
    assert p.returncode == 10
    assert json.loads(p.stdout)["reason_code"] == "provider_not_in_item_allowlist"


def test_check_data_rights_model_training_flag_denies_regardless_of_classification(tmp_path: Path):
    """A policy cannot permit model-training even when another classification would otherwise pass."""
    policy = _build_policy(
        "opencode-go", "opencode-go-training-allowed", modelTrainingAllowed=True
    )
    p = _run_check_data_rights(
        tmp_path,
        policy,
        [
            "--item-id", "S1-0001",
            "--classification", "public_corpus",
            "--provider-route", "opencode-go",
            "--policy-id", policy["policyId"],
            "--item-provider-allowlist", policy["dataAllowlistKey"],
        ],
    )
    assert p.returncode == 10
    assert json.loads(p.stdout)["reason_code"] == "forbidden_use_enabled"


# ----------------------------------------------------- 10. packet gates


def test_check_packet_gates_clean_corpus_and_packet_pair_passes_all_six_gates(tmp_path: Path):
    """A valid pair with no leaks, no repo-in-prose clues, and resolved metadata should pass."""
    items = [
        _base_packet_item("S1-9001", goal="Refactor parser entrypoint handling."),
        _base_packet_item("S1-9002", goal="Align behavior with upstream style."),
    ]
    packets = [
        {"item_id": "S1-9001"},
        {"item_id": "S1-9002"},
    ]
    p = _run_packet_gates(tmp_path, "audit", items, packets)
    assert p.returncode == 0
    assert "passed all six gates : 2" in p.stdout
    assert "blocked              : 0" in p.stdout


@pytest.mark.parametrize("oracle_payload", [
    {"labels": []},
    {"trap": {"trap_id": "T1", "assertion": "seeded"}},
])
def test_check_packet_gates_blocks_oracle_leaks(tmp_path: Path, oracle_payload: dict):
    """Packet fields that expose oracle answers must fail hard before grant or assignment."""
    item = _base_packet_item("S1-9003", goal="Refactor the packet builder.")
    p = _run_packet_gates(
        tmp_path,
        "audit",
        [item],
        [{"item_id": "S1-9003", **oracle_payload}],
    )
    assert p.returncode == 1
    assert "BLOCKED S1-9003" in p.stdout
    assert "oracle_leak:" in p.stdout


def test_check_packet_gates_repo_named_in_prose_is_blocked_but_diff_path_is_allowed(tmp_path: Path):
    """Naming a repository in packet prose must block, while the same token in diff text does not."""
    repo_slug = "acme/widget-core"
    prose_item = _base_packet_item(
        "S1-9004",
        goal="Diff-safe cleanup of tokenizer callsites.",
    )
    diff_item = _base_packet_item(
        "S1-9005",
        repo=repo_slug,
        goal="Fix the tokenizer bug without upstream context.",
    )
    p = _run_packet_gates(
        tmp_path,
        "audit",
        [prose_item, diff_item],
        [
            {"item_id": "S1-9004", "goal": f"Review and fix upstream issue in {repo_slug}."},
            {"item_id": "S1-9005", "design_or_diff": f"diff --git a/{repo_slug}/x.py b/{repo_slug}/x.py", "goal": "Fix tokenizer bug"},
        ],
    )
    assert p.returncode == 1
    assert "BLOCKED S1-9004  upstream_repo_named_in_prose" in p.stdout
    assert "BLOCKED S1-9005" not in p.stdout


def test_check_packet_gates_blocks_issue_number_in_prose(tmp_path: Path):
    """Mentioning upstream issue IDs in prose is a retrieval cue and must be blocked."""
    item = _base_packet_item(
        "S1-9006",
        goal="Fix issue 9876: transient NPE during startup.",
    )
    p = _run_packet_gates(
        tmp_path,
        "audit",
        [item],
        [{"item_id": "S1-9006", "goal": "Fix issue 9876: transient NPE during startup."}],
    )
    assert p.returncode == 1
    assert "BLOCKED S1-9006  upstream_issue_number_in_prose" in p.stdout


def test_check_packet_gates_unresolved_license_warning_is_reported_not_blocking(tmp_path: Path):
    """Unresolved licence fields are a compliance report, not an automatic block."""
    item = _base_packet_item(
        "S1-9007",
        license_="NOASSERTION",
    )
    p = _run_packet_gates(
        tmp_path,
        "audit",
        [item],
        [{"item_id": "S1-9007"}],
    )
    assert p.returncode == 0
    assert "passed all six gates : 1" in p.stdout
    assert "warn    S1-9007  licence_unresolved:NOASSERTION" in p.stdout


def test_check_packet_gates_grant_only_pass_items_and_keeps_allowlists_in_sync(tmp_path: Path):
    """Grant should only mutate pass items and keep corpus and packet allowlists identical."""
    passed_item = _base_packet_item(
        "S1-9008",
        goal="Normalize packet fields and strip private metadata.",
    )
    blocked_item = _base_packet_item(
        "S1-9009",
        goal="A known false-positive gate regression.",
    )
    p = _run_packet_gates(
        tmp_path,
        "grant",
        [passed_item, blocked_item],
        [
            {"item_id": "S1-9008"},
            {
                "item_id": "S1-9009",
                "labels": [{"label_id": "L1", "severity": "high"}],
                "provider_data_allowlist": ["opencode"],
            },
        ],
        "--vendor", "opencode-judge",
    )
    assert p.returncode == 0
    corpus_rows = _read_jsonl(tmp_path / "corpus.jsonl")
    packet_rows = _read_jsonl(tmp_path / "packets.jsonl")
    corpus_by_id = {row["item_id"]: row for row in corpus_rows}
    packets_by_id = {row["item_id"]: row for row in packet_rows}
    assert corpus_by_id["S1-9008"]["provider_data_allowlist"] == ["opencode", "opencode-judge"]
    assert packets_by_id["S1-9008"]["provider_data_allowlist"] == ["opencode", "opencode-judge"]
    assert corpus_by_id["S1-9009"]["provider_data_allowlist"] == ["opencode"]
    assert packets_by_id["S1-9009"]["provider_data_allowlist"] == ["opencode"]


def test_real_s2_corpus_goals_do_not_name_repository_or_issue_number():
    """The shipped S2 packets must not hand the reviewer a search query.

    Every S2 goal once read "Review a candidate patch for beetbox/beets issue
    5495". One lookup returns the merged resolution, so the stratum whose job is
    measuring whether a reviewer can tell a good patch from a bad one was
    measuring retrieval instead, at a ceiling of 100%. Nothing about that looks
    wrong in the output -- the recall number simply comes back excellent.
    """
    corpus_path = Path.home() / ".omp/agent/skills/critical-review/lrhe-data/corpus.jsonl"
    if not corpus_path.exists():
        pytest.skip("private corpus is not present in this checkout")
    issue_re = re.compile(r"\b(?:issue|pull request|PR)\s*#?\d{2,}", re.I)
    offenders: list[str] = []
    for item in _read_jsonl(corpus_path):
        if item.get("stratum") != "S2_PATCH_VERDICT":
            continue
        goal = str(item.get("goal", ""))
        repo = str(item.get("repo", ""))
        if repo and repo.lower() in goal.lower():
            offenders.append(f"{item['item_id']} has repo in goal: {repo}")
        if issue_re.search(goal):
            offenders.append(f"{item['item_id']} has issue number in goal: {goal}")
    assert not offenders, "found S2 goals with repository/issue leakage: " + "; ".join(offenders)


def test_simulator_honours_the_per_family_trap_bait_rate():
    """Trap susceptibility must stay a tunable rate, not a constant.

    A lint pass once rewrote `if "trap" in item and rng.random() < trap_bait[fam]`
    to `if "trap" in item`, which makes every family take every trap. The full
    suite still passed: the simulator produced a complete, plausible dataset in
    which false-positive rates were identical across families, which is precisely
    the comparison the traps exist to make. Nothing downstream could notice,
    because nothing downstream knows what the rate was supposed to be.

    Bracketing the rate is what makes this detectable at all -- a fixed-seed count
    would only pin today's number, and the failure mode is the rate ceasing to be
    read.
    """
    import numpy as np

    import simulate_experiment as sim

    def trap_claims(bait: float) -> int:
        outputs = sim.simulate(np.random.default_rng(7),
                               trap_bait={f: bait for f in sim.FAMILIES})
        judge = next(o for o in outputs if isinstance(o, list) and o
                     and isinstance(o[0], dict) and "affinity" in o[0])
        # The bait claim is the one judged plausible against no label at all.
        return sum(1 for j in judge if j.get("affinity") == 0.2 and j.get("label_id") == "")

    never, always = trap_claims(0.0), trap_claims(1.0)
    assert never == 0, f"trap_bait=0.0 still produced {never} trap claims"
    assert always > 0, "trap_bait=1.0 produced no trap claims; the bait path is dead"


def test_two_checkpoints_behind_one_selector_are_never_pooled(scored, tmp_path: Path):
    """A selector is an alias, not a model, and the lock cannot tell the difference.

    `freeze_lock.py` pins the toolchain, both repositories, the corpus and its answer
    key, the manifest and the terms snapshots -- and nothing about the weights. If a
    provider swaps the checkpoint behind a selector partway through a 105-review
    matrix, every family comparison spanning the swap compares two models under one
    name and `verify` reports no drift, because nothing it hashes moved. Where a
    fingerprint exists this is the detector, and it fails closed like a mixed panel.
    """
    import os
    runs = scored["runs"].copy()
    # The real column is empty, so pandas typed it float64. Cast before assigning,
    # which is the same NaN-shaped trap the guard itself had to be fixed for.
    runs["provider_fingerprint"] = runs["provider_fingerprint"].astype("object")
    half = len(runs) // 2
    runs.loc[: half - 1, "provider_fingerprint"] = "cp-2026-06-01"
    runs.loc[half:, "provider_fingerprint"] = "cp-2026-07-15"
    rpath, cpath = tmp_path / "r.csv", tmp_path / "c.csv"
    runs.to_csv(rpath, index=False)
    scored["claims"].to_csv(cpath, index=False)

    p = subprocess.run(
        [PY, "analyze_lrhe.py", "--claims", str(cpath), "--runs", str(rpath),
         "--corpus", str(scored["corpus"]), "--boot", "20", "--perm", "20",
         "--experiment-id", EXPERIMENT_ID, "--panel-id", PANEL_ID,
         "--out", str(tmp_path / "a.json")],
        cwd=HERE, capture_output=True, text=True, env=dict(os.environ))
    assert p.returncode != 0
    assert "more than one checkpoint behind one selector" in (p.stdout + p.stderr)


def test_a_provider_that_exposes_no_fingerprint_still_analyses(scored, tmp_path: Path):
    """Absence is one state, not one per run, and the first version got that wrong.

    An unpopulated column reads back as NaN, NaN never equals itself, and a set of
    them has one member per row -- so the guard above refused every analysis of every
    provider that exposes no fingerprint, which today is all of them. A detector that
    fires on absence is worse than the gap it closes, and the unmeasured risk belongs
    in the selection record where the operator report will find it.
    """
    import os
    runs = scored["runs"].copy()
    runs["provider_fingerprint"] = ""
    rpath, cpath = tmp_path / "r.csv", tmp_path / "c.csv"
    runs.to_csv(rpath, index=False)
    scored["claims"].to_csv(cpath, index=False)

    out = tmp_path / "a.json"
    p = subprocess.run(
        [PY, "analyze_lrhe.py", "--claims", str(cpath), "--runs", str(rpath),
         "--corpus", str(scored["corpus"]), "--boot", "20", "--perm", "20",
         "--experiment-id", EXPERIMENT_ID, "--panel-id", PANEL_ID, "--out", str(out)],
        cwd=HERE, capture_output=True, text=True, env=dict(os.environ))
    assert p.returncode == 0, p.stdout + p.stderr
    selection = json.loads(out.read_text())["selection"]
    assert selection["selectors_without_a_fingerprint"], (
        "the unmeasured checkpoint risk is absent from the record rather than named")


def test_scoring_excludes_an_invalidated_run(tmp_path: Path):
    """A run kept as apparatus evidence must not reach a statistic.

    The pre-2026-07-28 cohort is preserved verbatim and stamped
    `eligible_for_primary_scoring: false`. Preservation is only safe if scoring refuses
    it; otherwise the quarantine is a naming convention.
    """
    good = _one_run()
    bad = _one_run(run_id=good["run_id"] + "-old")
    bad["measurement_status"] = {
        "status": "invalidated",
        "invalidation_reason": "unenforced_reviewer_tool_surface",
        "eligible_for_primary_scoring": False, "eligible_for_pooling": False,
        "exploratory_use_only": ["tool_boundary_diagnostics"],
        "replaces_run_id": None, "dispatch_policy_digest": "sha256:pre-enforcement"}

    res = _score(tmp_path, [good, bad])
    assert res.returncode == 0, res.stderr
    report = json.loads((tmp_path / "rep.json").read_text())
    assert report["n_runs"] == 1, "the invalidated run was scored"
    assert "unenforced_reviewer_tool_surface" in res.stderr, (
        "the exclusion happened silently")


def test_scoring_refuses_two_dispatch_policies(tmp_path: Path):
    """Two conditions under one panel id do not pool, and the digest is what says so.

    The invalidated cohort and its replacements share `experiment_id`, `panel_id`, item,
    family and lens. Everything that distinguishes them lives in the policy digest, so
    that is the field the refusal has to read.
    """
    a = _one_run()
    b = _one_run(run_id=a["run_id"] + "-b")
    a["measurement_status"] = dict(a["measurement_status"],
                                   dispatch_policy_digest="sha256:enforced-v1")
    b["measurement_status"] = dict(b["measurement_status"],
                                   dispatch_policy_digest="sha256:enforced-v2")

    res = _score(tmp_path, [a, b])
    assert res.returncode != 0, "two dispatch policies were pooled into one estimate"
    assert "more than one dispatch policy" in (res.stdout + res.stderr)


def test_every_experiment_resolves_its_arm_roles_to_declared_arms():
    """An undeclared arm role selects nothing and every statistic keyed on it reads clean.

    `councilArms: [C, D]` and `nullArm: T` sat in `panels.yaml` unread while
    `analyze_lrhe.py` hardcoded the same four values, so the OpenCode panel inherited the
    core experiment's arms. `families` came back `[]` on a three-family panel and the
    matched null reported "was not run" with 36 runs on disk.
    """
    sys.path.insert(0, str(HERE))
    import analyze_lrhe

    panels = yaml.safe_load((HERE / "panels.yaml").read_text())
    declared = set(panels["arms"])
    for exp in panels["experiments"]:
        roles = analyze_lrhe.resolve_arm_roles(exp["experimentId"])
        named = [*roles["councilArms"], roles["nullArm"], roles["squareArm"], roles["floorArm"]]
        assert all(a in declared for a in named), (exp["experimentId"], named)

    oc = analyze_lrhe.resolve_arm_roles("lrhe-opencode-v1")
    assert oc["councilArms"] == ("OC_FULL",), oc
    assert oc["nullArm"] == "T_OC", oc
    # The core experiment must be untouched by the new resolution path.
    core = analyze_lrhe.resolve_arm_roles("lrhe-core-v1")
    assert core["councilArms"] == ("C", "D") and core["nullArm"] == "T", core


def test_a_trap_whose_sites_cover_every_file_in_scope_reports_no_promotion(tmp_path: Path):
    """A flag that fires on any severe claim is not a measure of taking the bait.

    `trap.sites` is drawn from the fix under review, so on all nine trap items in this
    corpus it covers every file in scope. `trap_promoted` there reduces to "made a P0/P1
    claim", which tracks claim volume -- and GLM's flagged P0 on `S4-44c75ca0` explicitly
    said the patch *eliminated* the overflow, refuting the trap it was scored as promoting.
    """
    item = {
        "item_id": "S4-trap", "stratum": "S4_TRAP", "goal": "g", "problem_statement": "p",
        "design_or_diff": "d", "repo_files": ["src/only.c"], "license": "x",
        "provider_data_allowlist": ["opencode"],
        "labels": [{"label_id": "L1", "severity": 0, "kind": "memory_safety",
                    "sites": [{"path": "src/only.c", "line_start": 1, "line_end": 2}],
                    "rationale": "r"}],
        "trap": {"assertion": "the patch does not close the crash",
                 "sites": [{"path": "src/only.c", "line_start": 1, "line_end": 2}]},
    }
    run = _one_run(item_id="S4-trap")
    run["evidence"] = ["R1|P0|conf=0.90|claim=the patch does close it"
                       "|evidence=src/only.c:1 observed|impact=none|verify=test"]
    res = _score(tmp_path, [run], corpus=[item])
    assert res.returncode == 0, res.stderr + res.stdout

    rows = list(csv.DictReader((tmp_path / "r.csv").open()))
    assert len(rows) == 1
    assert rows[0]["trap_sites_discriminate"] in ("False", "false"), rows[0]
    assert rows[0]["trap_promoted"] == "", (
        "trap_promoted was reported on an item where the flag cannot discriminate")
    assert rows[0]["trap_site_severe_claim"] in ("True", "true"), (
        "the observable upper bound was lost along with the misleading measurement")


def test_an_uncomputable_decorrelation_contrast_is_not_a_negative_result():
    """NaN must not become "the extra provider lanes are not yet justified".

    `hi < 0` is false when `hi` is NaN, so the two-branch guard sent every uncomputable
    contrast to the negative branch. The caught-set Jaccard is undefined until claims are
    matched to labels, so before adjudication this fires on every panel -- and the
    conclusion it produced was about provider diversity, which is a promotion criterion.
    """
    sys.path.insert(0, str(HERE))
    import analyze_lrhe
    import pandas as pd

    # Two arms on one shared item, nothing caught: the pivot has no overlap to divide.
    rows = []
    for arm, key in (("C", "fam-a"), ("C", "fam-b"), ("T", "r1"), ("T", "r2")):
        rows.append({"item_id": "S1-x", "label_id": "L1", "label_severity": 0,
                     "arm": arm, "family": key if arm == "C" else "kimi",
                     "replicate": key if arm == "T" else "", "caught": 0})
    out = analyze_lrhe.diversity_vs_null(pd.DataFrame(rows), B=32)
    assert "NOT MEASURABLE" in out["verdict"], out["verdict"]
    assert "not yet justified" not in out["verdict"], (
        "an uncomputable contrast still produced a verdict against provider diversity")


def test_a_judge_panel_that_cannot_be_filled_is_refused_not_shrunk():
    """One judge is not two, and two of the same family is one judge.

    `panel_for` used to `return []` when the author was the only family and
    `min(size, len(eligible))` otherwise, so a mis-specified pool produced claims with
    one judge, or none, and said nothing -- the claim simply never appeared in the output.
    `cmd_ingest` already refuses a judgement whose family authored the claim; the
    assignment side had no equivalent.
    """
    sys.path.insert(0, str(HERE))
    import judge_lrhe

    ok = judge_lrhe.panel_for("k1", "kimi", ["claude", "gemini", "grok"], 2)
    assert len(ok) == 2 and len(set(ok)) == 2 and "kimi" not in ok, ok

    with pytest.raises(judge_lrhe.PanelUnfillable):
        judge_lrhe.panel_for("k1", "kimi", ["kimi", "glm"], 2)
    with pytest.raises(judge_lrhe.PanelUnfillable):
        judge_lrhe.panel_for("k1", "kimi", ["kimi"], 1)


def test_judge_prompts_writes_nothing_when_a_claim_cannot_be_assigned(tmp_path: Path):
    """A partial assignment file is worse than none: the gap reads as consensus.

    Claims missing from the judge file are not disputed by anybody, so downstream they
    look like claims nobody challenged rather than claims nobody was allowed to judge.
    """
    corpus, runs, claims, out = (tmp_path / n for n in
                                 ("c.jsonl", "r.jsonl", "cl.csv", "jp.jsonl"))
    _corpus(corpus)
    item_id = json.loads(corpus.read_text().splitlines()[0])["item_id"]
    run = _one_run(item_id=item_id, family="kimi")
    runs.write_text(json.dumps(run) + "\n")
    claims.write_text(
        "run_id,item_id,rid,parse_status,verdict,has_anchor,anchor_paths_exist,severity,"
        "confidence,claim_text,evidence_text,impact_text,family\n"
        f"{run['run_id']},{item_id},R1,ok,PLAUSIBLE,True,True,1,0.7,c,e,i,kimi\n")

    res = subprocess.run(
        [PY, "judge_lrhe.py", "prompts", "--corpus", str(corpus), "--runs", str(runs),
         "--claims", str(claims), "--families", "kimi", "glm", "--panel-size", "2",
         "--out", str(out)],
        cwd=HERE, capture_output=True, text=True)
    assert res.returncode != 0, res.stdout
    assert "REFUSED" in res.stderr, res.stderr
    assert not out.exists(), "a partial judge assignment was written anyway"


def test_the_judge_schema_and_the_runner_agree_on_the_label_field():
    """The archived schema said `matched_label_id`; the runner reads `label_id`.

    A reply valid against the published schema would have been ingested with no label,
    so every CONFIRMED verdict would have lost the 1:1 defect matching it exists to
    establish -- and nothing would have failed.
    """
    schema = json.loads((HERE / "judge-output.schema.json").read_text())
    assert "label_id" in schema["properties"], sorted(schema["properties"])
    assert "matched_label_id" not in schema["properties"], (
        "both spellings are present, which is how the divergence survives")
    assert schema["additionalProperties"] is False
    # The three verdicts the runner counts. REFUTED comes from the refutation pass and
    # UNRESOLVED had no consumer: ingest dropped such replies as unmatched, silently.
    sys.path.insert(0, str(HERE))
    import judge_lrhe
    assert set(schema["properties"]["verdict"]["enum"]) == set(judge_lrhe.VERDICTS)


def _run_kappa_packet(
        tmp_path: Path, rows: list[dict], judge: list[dict], *,
        case_map: list[dict] | None = None,
        corpus: list[dict] | None = None) -> subprocess.CompletedProcess:
    packet_path = tmp_path / "calibration.csv"
    judge_path = tmp_path / "judge.jsonl"
    fields = [
        "case_id", "run_id", "claim_rid", "item_id",
        "human_verdict", "human_label_id",
    ]
    with packet_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    _write_jsonl(judge_path, judge)

    args = [
        PY, "judge_lrhe.py", "kappa",
        "--calibration", str(packet_path),
        "--judge", str(judge_path),
        "--expect-rows", str(len(rows)),
    ]
    if case_map is not None:
        case_map_path = tmp_path / "case-map.jsonl"
        _write_jsonl(case_map_path, case_map)
        args.extend(["--case-map", str(case_map_path)])
    if corpus is not None:
        corpus_path = tmp_path / "corpus.jsonl"
        _write_jsonl(corpus_path, corpus)
        args.extend(["--corpus", str(corpus_path)])
    return subprocess.run(args, cwd=HERE, capture_output=True, text=True)


def test_kappa_refuses_a_partially_labelled_packet(tmp_path: Path):
    """Leaving inconvenient rows blank cannot turn a partial calibration into a gate."""
    rows = [
        {"run_id": "r1", "claim_rid": "1", "item_id": "S1",
         "human_verdict": "CONFIRMED", "human_label_id": "L1"},
        {"run_id": "r1", "claim_rid": "2", "item_id": "S2",
         "human_verdict": "", "human_label_id": ""},
    ]
    judge = [
        {"run_id": "r1", "claim_rid": "1", "verdict": "CONFIRMED", "label_id": "L1"},
        {"run_id": "r1", "claim_rid": "2", "verdict": "PLAUSIBLE", "label_id": ""},
    ]
    corpus = [
        {"item_id": "S1", "labels": [{"label_id": "L1"}]},
        {"item_id": "S2", "labels": [{"label_id": "L2"}]},
    ]

    res = _run_kappa_packet(tmp_path, rows, judge, corpus=corpus)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "human_verdict is empty" in res.stderr
    assert "Cohen's kappa" not in res.stdout + res.stderr


def test_kappa_refuses_confirmed_label_outside_the_item_label_set(tmp_path: Path):
    """Verdict agreement cannot hide that the human matched a different item defect."""
    rows = [
        {"case_id": "AR-0001", "human_verdict": "confirmed",
         "human_label_id": "L99"},
    ]
    judge = [
        {"run_id": "r1", "claim_rid": "1", "verdict": "CONFIRMED", "label_id": "L1"},
    ]
    case_map = [
        {"case_id": "AR-0001", "run_id": "r1", "claim_rid": "1",
         "item_id": "S1", "label_ids": ["L1"], "kind": "case"},
    ]

    res = _run_kappa_packet(tmp_path, rows, judge, case_map=case_map)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "human_label_id 'L99' is not valid for item 'S1'" in res.stderr
    assert "Cohen's kappa" not in res.stdout + res.stderr


def test_kappa_composite_is_below_verdict_when_confirmed_labels_differ(
        tmp_path: Path):
    """Same verdict but a different defect is disagreement in the composite figure."""
    rows = [
        {"case_id": "AR-0001", "human_verdict": "CONFIRMED",
         "human_label_id": "L1"},
        {"case_id": "AR-0002", "human_verdict": "CONFIRMED",
         "human_label_id": "L2"},
        {"case_id": "AR-0003", "human_verdict": "PLAUSIBLE",
         "human_label_id": ""},
    ]
    judge = [
        {"run_id": "r1", "claim_rid": "1", "verdict": "CONFIRMED", "label_id": "L2"},
        {"run_id": "r1", "claim_rid": "2", "verdict": "CONFIRMED", "label_id": "L1"},
        {"run_id": "r1", "claim_rid": "3", "verdict": "PLAUSIBLE", "label_id": ""},
    ]
    case_map = [
        {"case_id": f"AR-000{i}", "run_id": "r1", "claim_rid": str(i),
         "item_id": f"S{i}", "label_ids": ["L1", "L2"], "kind": "case"}
        for i in range(1, 4)
    ]

    res = _run_kappa_packet(tmp_path, rows, judge, case_map=case_map)
    assert res.returncode == 0, res.stdout + res.stderr
    verdict_section = res.stdout.split(
        "verdict agreement:", 1)[1].split("exact matched-label", 1)[0]
    verdict_kappa = float(re.search(
        r"Cohen's kappa\s*:\s*(-?\d+\.\d+)", verdict_section).group(1))
    composite_kappa = float(re.search(
        r"composite kappa:\s*(-?\d+\.\d+)", res.stdout).group(1))
    assert composite_kappa < verdict_kappa, res.stdout


# The agent definitions live in the PRIVATE package; this repository is public and its CI
# runner has no `~/.omp/agent/agents`. `test_runner.py` gets this for free from a
# module-level skip on the private corpus, which is why its floor-reviewer equivalent
# skipped there while this one failed the build.
@pytest.mark.skipif(not (Path.home() / ".omp/agent/agents/judge-claude.md").is_file(),
                    reason="judge agent definitions are not present in this checkout")
def test_every_judge_agent_declares_an_empty_tool_surface():
    """A judge is shown the ground-truth labels, so it needs no repository at all.

    Worse than a reviewer reaching for the tree: the corpus answer key for every OTHER
    item is exactly what a judge with `read` would be reaching for.
    """
    agents = Path.home() / ".omp/agent/agents"
    defs = sorted(agents.glob("judge-*.md"))
    assert len(defs) >= 3, f"expected the judge definitions in {agents}"
    for path in defs:
        front = yaml.safe_load(path.read_text().split("---")[1])
        assert front["tools"] == [], f"{path.name} declares {front['tools']}"
        assert front["output"]["additionalProperties"] is False, path.name


def test_a_judgement_from_an_unrequested_model_drops_its_claim(tmp_path: Path):
    """Reviewer runs carry identity_verified; judgements carried nothing.

    `judge_family` is copied from the PROMPT, which is the request. A silent provider
    fallback would have left every judgement attributed to a family that never answered,
    with nothing in the record able to detect it afterwards -- the session's defect in a
    new place. The whole claim drops, not half its panel: one surviving judge cannot be a
    majority of two.
    """
    sys.path.insert(0, str(HERE))
    import judge_lrhe

    assert judge_lrhe.identity_of("grok", "xai-oauth/grok-build",
                                  {"grok": "xai-oauth/grok-build"}) is True
    # The definition pins a thinking level; the session record does not report one.
    assert judge_lrhe.identity_of("claude", "anthropic/claude-opus-5",
                                  {"claude": "anthropic/claude-opus-5:max"}) is True
    assert judge_lrhe.identity_of("grok", "opencode-go/kimi-k3",
                                  {"grok": "xai-oauth/grok-build"}) is False
    # Unanswerable is not a pass: no expectation, or nothing served.
    assert judge_lrhe.identity_of("nobody", "x/y", {}) is None
    assert judge_lrhe.identity_of("grok", None, {"grok": "xai-oauth/grok-build"}) is None

    prompts, responses, out, judgments = (tmp_path / n for n in
                                          ("jp.jsonl", "jr.jsonl", "agg.jsonl", "raw.jsonl"))
    prompts.write_text(json.dumps({
        "judge_id": "r1|01|grok", "run_id": "r1", "claim_rid": "01", "item_id": "S1-0001",
        "author_family": "kimi", "judge_family": "grok", "role": "judge", "round": 1}) + "\n")
    responses.write_text(json.dumps({
        "judge_id": "r1|01|grok", "verdict": "CONFIRMED", "label_id": "L1",
        "confidence": 0.9, "judge_family": "grok",
        "served_model": "opencode-go/kimi-k3"}) + "\n")

    res = subprocess.run(
        [PY, "judge_lrhe.py", "ingest", "--prompts", str(prompts),
         "--responses", str(responses), "--out", str(out),
         "--out-judgments", str(judgments),
         "--human-queue", str(tmp_path / "hq.jsonl"),
         "--expect", "grok=xai-oauth/grok-build"],
        cwd=HERE, capture_output=True, text=True)
    assert res.returncode != 0, res.stdout
    assert "unverified judge identity" in res.stdout, res.stdout
    # Kept as raw evidence, excluded from the aggregate that scoring reads.
    raw = [json.loads(x) for x in judgments.read_text().splitlines() if x.strip()]
    assert len(raw) == 1 and raw[0]["identity_verified"] is False
    assert raw[0]["served_model"] == "opencode-go/kimi-k3"
    assert not [x for x in out.read_text().splitlines() if x.strip()], (
        "an unverified judgement reached the aggregate scoring reads")


def test_false_positive_burden_is_reported_per_family_with_an_interval(tmp_path: Path):
    """Promote/drop is a per-lane decision, and a raw count is not a rate.

    This section had only per-arm numbers, so the burden of an individual family could only
    be read off raw FABRICATED counts -- 19 DeepSeek, 11 GLM, 2 Kimi -- with no interval
    and no correction for DeepSeek also emitting the most claims per run. That is a volume
    measurement wearing a quality label, the same error `trap_promoted` made. With
    intervals the ordering turns out not to be established at this n, which is the finding.
    """
    sys.path.insert(0, str(HERE))
    import analyze_lrhe
    import pandas as pd

    runs = pd.DataFrame([
        {"run_id": f"r{i}", "item_id": f"S1-{i//2}", "arm": "OC_FULL", "family": fam,
         "lens": "floor", "replicate": "", "fabrication_rate": rate, "refutation_rate": 0.0,
         "n_claims": 3, "n_promoted": 0, "trap_promoted": None,
         "trap_site_severe_claim": None, "trap_sites_discriminate": None, "null_item_fp": None}
        for i, (fam, rate) in enumerate(
            [("kimi", 0.0), ("kimi", 0.1), ("glm", 0.2), ("glm", 0.1),
             ("deepseek", 0.3), ("deepseek", 0.2)])
    ])
    out = analyze_lrhe.fp_burden(runs, pd.DataFrame(), B=64, council_arms=("OC_FULL",))
    assert "by_family" in out, sorted(out)
    assert set(out["by_family"]) == {"kimi", "glm", "deepseek"}
    for fam, v in out["by_family"].items():
        fr = v["fabrication_rate"]
        assert fr["lo"] <= fr["point"] <= fr["hi"], (fam, fr)
        assert v["n_runs"] == 2, (fam, v)
    # An arm outside the council pool contributes nothing to a per-family lane statistic.
    other = runs.assign(arm="OC_SCREEN")
    assert "by_family" not in analyze_lrhe.fp_burden(
        other, pd.DataFrame(), B=64, council_arms=("OC_FULL",))
