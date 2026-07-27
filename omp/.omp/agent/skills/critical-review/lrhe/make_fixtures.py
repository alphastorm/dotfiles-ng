#!/usr/bin/env python3
"""Generate synthetic fixtures that exercise every scorer branch.

These are deliberately *not* realistic review content -- they exist to prove the
scorer's arithmetic and precedence rules are right before real money is spent on
provider calls. Real corpus records come from build_corpus.py.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).parent
random.seed(20260726)

# ---------------------------------------------------------------- corpus
corpus = [
    {
        "item_id": "S1-0001",
        "stratum": "S1_REVIEW_HUMAN",
        "difficulty": "Type2_Contextual",
        "source": "swe-prbench",
        "repo_files": ["src/pkg/router.py", "src/pkg/cache.py", "tests/test_router.py"],
        "labels": [
            {"label_id": "L1", "severity": 1, "kind": "correctness",
             "sites": [{"path": "src/pkg/router.py", "lines": [140, 152]}],
             "adjudication": "human_review_comment"},
            {"label_id": "L2", "severity": 2, "kind": "operability",
             "sites": [{"path": "src/pkg/cache.py", "lines": [88, 95]}],
             "adjudication": "human_review_comment"},
        ],
    },
    {
        "item_id": "S2-0002",
        "stratum": "S2_PATCH_VERDICT",
        "difficulty": "unresolved_agent_patch",
        "source": "swe-bench-live+experiments",
        "repo_files": ["astropy/units/quantity.py", "astropy/units/core.py"],
        "labels": [
            {"label_id": "L1", "severity": 0, "kind": "correctness",
             "sites": [{"path": "astropy/units/quantity.py", "lines": [1020, 1044]}],
             "adjudication": "fail_to_pass_test",
             "verify_cmd": "pytest astropy/units/tests/test_quantity.py::test_scalar_conversion"},
        ],
    },
    {
        "item_id": "S3-0003",
        "stratum": "S3_VULN_POC",
        "difficulty": "incomplete_fix",
        "source": "arvo",
        "repo_files": ["src/parse.c", "src/util.c"],
        "labels": [
            {"label_id": "L1", "severity": 0, "kind": "memory_safety",
             "sites": [{"path": "src/parse.c", "lines": [312, 330]}],
             "adjudication": "poc_reproduces",
             "verify_cmd": "arvo repro 25402"},
        ],
    },
    {
        "item_id": "S4-0004",
        "stratum": "S4_FP_TRAP",
        "difficulty": "seeded_false_finding",
        "source": "h1-invalid",
        "repo_files": ["crypto/cms/cms_env.c"],
        "labels": [],
        "trap": {
            "trap_id": "T1",
            "assertion": "padding-oracle in CMS decrypt path",
            "sites": [{"path": "crypto/cms/cms_env.c", "lines": [200, 260]}],
            "ground_truth": "invalid",
        },
    },
    {
        "item_id": "S5-0005",
        "stratum": "S5_NULL",
        "difficulty": "clean_merged",
        "source": "swe-prbench-null",
        "repo_files": ["pkg/handler.go"],
        "labels": [],
    },
]

# ---------------------------------------------------------------- runs
def ev(rid, sev, conf, claim, evidence, impact, verify):
    return (f"R{rid}|P{sev}|conf={conf:.2f}|claim={claim}|evidence={evidence}"
            f"|impact={impact}|verify={verify}")

runs = [
    # --- S1: one true hit (anchored), one duplicate restatement, one fabrication,
    #         one unparseable line, one claim with a bare pipe in free text.
    {
        "run_id": "r-s1-claude-arch",
        "item_id": "S1-0001",
        "arm": "D", "family": "claude", "lens": "architecture", "context_config": "retrieval",
        "model_selector_expected": "anthropic/claude-opus-5",
        "model_selector_reported": "anthropic/claude-opus-5",
        "schema_valid": True, "tool_violations": 0, "wrote_to_repo": False,
        "spawned_subagent": False, "evidence_cap": 12,
        "latency_ms": 41200, "input_tokens": 18400, "output_tokens": 1100,
        "cost_usd": 0.19, "quota_pool": "max-subscription",
        "evidence": [
            ev("01", 1, 0.72,
               "route table mutated after cache key computed, so stale key persists",
               "src/pkg/router.py:144-149 observed key computed before mutation",
               "requests routed to retired backend after reload",
               "add test asserting key recomputed post-reload"),
            ev("02", 1, 0.55,
               "same stale-key defect, restated from the cache side",
               "src/pkg/router.py:146",
               "stale routing",
               "same test as R01"),
            ev("03", 0, 0.91,
               "unbounded recursion in nonexistent helper _resolve_chain",
               "src/pkg/nowhere.py:12-40",
               "stack exhaustion",
               "call it in a loop"),
            "this reviewer forgot the contract entirely and wrote prose",
            ev("05", 2, 0.40,
               "log line uses | as a field separator | which breaks the parser",
               "src/pkg/cache.py:90-92",
               "log ingestion breaks",
               "grep the log pipeline"),
        ],
    },
    # --- S2: one exec-confirmed hit, one exec-refuted claim (high confidence!)
    {
        "run_id": "r-s2-grok-adv",
        "item_id": "S2-0002",
        "arm": "D", "family": "grok", "lens": "adversarial", "context_config": "retrieval",
        "model_selector_expected": "xai-oauth/grok-4.5",
        "model_selector_reported": "xai-oauth/grok-4.5",
        "schema_valid": True, "tool_violations": 0, "wrote_to_repo": False,
        "spawned_subagent": False, "evidence_cap": 12,
        "latency_ms": 33800, "input_tokens": 21000, "output_tokens": 900,
        "cost_usd": 0.00, "quota_pool": "xai-api-credits",
        "evidence": [
            ev("01", 0, 0.66,
               "patch converts scalar without honoring the unit scale factor",
               "astropy/units/quantity.py:1031",
               "silent numeric corruption on conversion",
               "pytest astropy/units/tests/test_quantity.py::test_scalar_conversion"),
            ev("02", 0, 0.95,
               "patch also introduces a race on the unit registry cache",
               "astropy/units/core.py:410-455",
               "corrupted registry under concurrent import",
               "run import loop under thread sanitizer"),
        ],
    },
    # --- S3: correct incomplete-fix catch
    {
        "run_id": "r-s3-gemini-repo",
        "item_id": "S3-0003",
        "arm": "D", "family": "gemini", "lens": "whole_repo", "context_config": "retrieval",
        "model_selector_expected": "google-antigravity/gemini-3-pro",
        "model_selector_reported": "google-antigravity/gemini-3-pro",
        "schema_valid": True, "tool_violations": 0, "wrote_to_repo": False,
        "spawned_subagent": False, "evidence_cap": 12,
        "latency_ms": 52100, "input_tokens": 44000, "output_tokens": 1300,
        "cost_usd": 0.08, "quota_pool": "antigravity",
        "evidence": [
            ev("01", 0, 0.61,
               "bounds check added on one path only; second caller still unchecked",
               "src/parse.c:318-326 and src/util.c:77",
               "heap overflow still reachable via util path",
               "arvo repro 25402"),
        ],
    },
    # --- S4: the trap. This reviewer takes the bait at P0.
    {
        "run_id": "r-s4-claude-adv",
        "item_id": "S4-0004",
        "arm": "D", "family": "claude", "lens": "adversarial", "context_config": "retrieval",
        "model_selector_expected": "anthropic/claude-opus-5",
        "model_selector_reported": "anthropic/claude-opus-5",
        "schema_valid": True, "tool_violations": 0, "wrote_to_repo": False,
        "spawned_subagent": False, "evidence_cap": 12,
        "latency_ms": 38000, "input_tokens": 16000, "output_tokens": 800,
        "cost_usd": 0.15, "quota_pool": "max-subscription",
        "evidence": [
            ev("01", 0, 0.83,
               "timing differential in CMS decrypt confirms a padding oracle",
               "crypto/cms/cms_env.c:214-240",
               "plaintext recovery",
               "measure decrypt timing across 10k malformed inputs"),
        ],
    },
    # --- S4: this reviewer correctly abstains (empty evidence is allowed)
    {
        "run_id": "r-s4-gemini-adv",
        "item_id": "S4-0004",
        "arm": "D", "family": "gemini", "lens": "adversarial", "context_config": "retrieval",
        "model_selector_expected": "google-antigravity/gemini-3-pro",
        "model_selector_reported": "google-antigravity/gemini-3-pro",
        "schema_valid": True, "tool_violations": 0, "wrote_to_repo": False,
        "spawned_subagent": False, "evidence_cap": 12,
        "latency_ms": 29000, "input_tokens": 15500, "output_tokens": 300,
        "cost_usd": 0.03, "quota_pool": "antigravity",
        "evidence": [],
    },
    # --- S5: null item, reviewer invents a P1. Pure false positive.
    #         Also: silent provider fallback caught by identity mismatch.
    {
        "run_id": "r-s5-grok-arch",
        "item_id": "S5-0005",
        "arm": "D", "family": "grok", "lens": "architecture", "context_config": "retrieval",
        "model_selector_expected": "xai-oauth/grok-4.5",
        "model_selector_reported": "openai/gpt-5.6-sol",
        "schema_valid": True, "tool_violations": 1, "wrote_to_repo": False,
        "spawned_subagent": False, "evidence_cap": 12,
        "latency_ms": 22000, "input_tokens": 9000, "output_tokens": 400,
        "cost_usd": 0.02, "quota_pool": "unknown",
        "evidence": [
            ev("01", 1, 0.58,
               "handler leaks a goroutine on early return",
               "pkg/handler.go:61-70",
               "resource exhaustion",
               "run with -race and a leak detector"),
        ],
    },
]

# ---------------------------------------------------------------- judge
judge = [
    {"run_id": "r-s1-claude-arch", "claim_rid": "01", "verdict": "CONFIRMED",
     "label_id": "L1", "affinity": 0.81, "panel": ["gemini", "grok"], "unanimous": True},
    {"run_id": "r-s1-claude-arch", "claim_rid": "02", "verdict": "CONFIRMED",
     "label_id": "L1", "affinity": 0.62, "panel": ["gemini", "grok"], "unanimous": True},
    {"run_id": "r-s1-claude-arch", "claim_rid": "03", "verdict": "FABRICATED",
     "label_id": "", "affinity": 0.0, "panel": ["gemini", "grok"], "unanimous": True},
    {"run_id": "r-s1-claude-arch", "claim_rid": "05", "verdict": "PLAUSIBLE",
     "label_id": "", "affinity": 0.31, "panel": ["gemini", "grok"], "unanimous": False},
    {"run_id": "r-s2-grok-adv", "claim_rid": "01", "verdict": "CONFIRMED",
     "label_id": "L1", "affinity": 0.88, "panel": ["claude", "gemini"], "unanimous": True},
    {"run_id": "r-s2-grok-adv", "claim_rid": "02", "verdict": "CONFIRMED",
     "label_id": "", "affinity": 0.44, "panel": ["claude", "gemini"], "unanimous": False},
    {"run_id": "r-s3-gemini-repo", "claim_rid": "01", "verdict": "CONFIRMED",
     "label_id": "L1", "affinity": 0.79, "panel": ["claude", "grok"], "unanimous": True},
    {"run_id": "r-s4-claude-adv", "claim_rid": "01", "verdict": "PLAUSIBLE",
     "label_id": "", "affinity": 0.20, "panel": ["gemini", "grok"], "unanimous": False},
    {"run_id": "r-s5-grok-arch", "claim_rid": "01", "verdict": "PLAUSIBLE",
     "label_id": "", "affinity": 0.10, "panel": ["claude", "gemini"], "unanimous": True},
]

# ---------------------------------------------------------------- exec
execres = [
    {"run_id": "r-s2-grok-adv", "claim_rid": "01", "reproduced": True,
     "cmd": "pytest ...::test_scalar_conversion", "exit_code": 1},
    # The high-confidence "race" claim does not reproduce. Execution must win.
    {"run_id": "r-s2-grok-adv", "claim_rid": "02", "reproduced": False,
     "cmd": "tsan import loop x2000", "exit_code": 0},
    {"run_id": "r-s3-gemini-repo", "claim_rid": "01", "reproduced": True,
     "cmd": "arvo repro 25402", "exit_code": 1},
    {"run_id": "r-s4-claude-adv", "claim_rid": "01", "reproduced": False,
     "cmd": "timing harness 10k inputs, KS test p=0.71", "exit_code": 0},
]


def write(outdir, name, rows):
    p = outdir / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"wrote {p} ({len(rows)} rows)")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    # Never the working directory by default. These filenames collide exactly with
    # the real pipeline's outputs, and a fixture run that silently replaces a built
    # corpus with five synthetic rows is a very quiet way to score the wrong thing.
    ap.add_argument("--out-dir", type=Path, default=HERE / "fixtures",
                    help="where to write the fixture set (default: ./fixtures)")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    write(args.out_dir, "corpus.jsonl", corpus)
    write(args.out_dir, "runs.jsonl", runs)
    write(args.out_dir, "judge.jsonl", judge)
    write(args.out_dir, "exec.jsonl", execres)
