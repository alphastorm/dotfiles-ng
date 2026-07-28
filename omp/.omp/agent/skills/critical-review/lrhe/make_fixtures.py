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
            ev("1", 1, 0.72,
               "route table mutated after cache key computed, so stale key persists",
               "src/pkg/router.py:144-149 observed key computed before mutation",
               "requests routed to retired backend after reload",
               "add test asserting key recomputed post-reload"),
            ev("2", 1, 0.55,
               "same stale-key defect, restated from the cache side",
               "src/pkg/router.py:146",
               "stale routing",
               "same test as R1"),
            ev("3", 0, 0.91,
               "unbounded recursion in nonexistent helper _resolve_chain",
               "src/pkg/nowhere.py:12-40",
               "stack exhaustion",
               "call it in a loop"),
            "this reviewer forgot the contract entirely and wrote prose",
            ev("5", 2, 0.40,
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
            ev("1", 0, 0.66,
               "patch converts scalar without honoring the unit scale factor",
               "astropy/units/quantity.py:1031",
               "silent numeric corruption on conversion",
               "pytest astropy/units/tests/test_quantity.py::test_scalar_conversion"),
            ev("2", 0, 0.95,
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
            ev("1", 0, 0.61,
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
            ev("1", 0, 0.83,
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
            ev("1", 1, 0.58,
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


# --------------------------------------------------------------- v2 envelope

EXPERIMENT_ID = "lrhe-fixture-v1"
PANEL_ID = "fixture-cgg-v1"
PROMPT_VERSION = "fixture-v1"
ASSIGNMENT_DIGEST = "sha256:fixture-assignment-manifest"


def _rights(item_id: str) -> dict:
    """A data-rights decision for a synthetic item.

    Fixtures carry a real one rather than a stub because run.schema.json $refs the
    same definition the egress guard emits. If the two shapes ever drift, the
    fixture run is what notices, and it notices before a provider request does.
    """
    return {
        "record_id": f"rights-{item_id}-fixture",
        "item_id": item_id,
        "classification": "public_corpus",
        "input_owner": "synthetic_fixture_no_upstream_owner",
        "rights_basis": ["synthetic fixture content; no third-party material"],
        "explicit_authorization": True,
        "policy_id": "fixture-no-egress",
        "terms_snapshot_id": "fixture-no-egress",
        "provider_route": "fixture",
        "provider_authorized": True,
        "customer_data_allowed": False,
        "third_party_confidential_allowed": False,
        "provider_training_use": "prohibited_by_provider_documentation",
        "provider_retention": "zero_retention_by_provider_documentation",
        "raw_output_capture_status": "allowed",
        "internal_evaluation_allowed": True,
        "router_training_allowed": False,
        "model_training_allowed": False,
        "egress_decision": "allow",
        "decision_reason": "Synthetic fixture; nothing leaves the machine.",
        "checked_at": "2026-07-27T00:00:00Z",
        "checked_by": "make_fixtures.py",
    }


def to_v2(run: dict, experiment_id: str = EXPERIMENT_ID, panel_id: str = PANEL_ID,
          prompt_version: str = PROMPT_VERSION) -> dict:
    """Wrap a flat fixture literal in the v2 run record.

    The literals above stay flat deliberately: a fixture earns its keep by putting
    the one interesting field on a line you can see, and burying `tool_violations`
    three levels down hides the only thing that run exists to test. This fills in
    the surrounding envelope so they still validate -- which they must, because the
    scorer now refuses to score anything that does not.
    """
    requested, served = run["model_selector_expected"], run["model_selector_reported"]
    digest = f"sha256:fixture-repo-{run['item_id']}"
    data_rights = _rights(run["item_id"])
    return {
        "schema_version": 2,
        "experiment_id": experiment_id,
        "panel_id": panel_id,
        "run_id": run["run_id"],
        "item_id": run["item_id"],
        "arm": run["arm"],
        "family": run["family"],
        "lens": run["lens"],
        "replicate": run.get("replicate", ""),
        "context_config": run["context_config"],
        "role": "critic",
        "prompt_version": prompt_version,
        "artifact_digest": f"sha256:fixture-packet-{run['item_id']}",
        "assignment_manifest_digest": ASSIGNMENT_DIGEST,
        "evidence_cap": run.get("evidence_cap", 12),
        "reviewer": {
            "provider_route": "fixture",
            "account_type": "fixture",
            "requested_model": requested,
            "served_model": served,
            "identity_verified": served == requested,
            "fallback_detected": served != requested,
            "omp_version": "fixture",
            "provider_client_version": "fixture",
            "product_route": run.get("product_route", "opencode-go"),
            "billing_route": run.get("billing_route", "unknown"),
            # A fixture has no checkpoint, and `null` is what a provider exposing no
            # fingerprint yields. Stated rather than omitted, because synthetic
            # evidence that cannot satisfy the schema governing the real thing is how
            # the stub came to emit evidence ids no reviewer is permitted to return.
            "provider_fingerprint": run.get("provider_fingerprint"),
        },
        "execution": {
            "started_at": "2026-07-27T00:00:00Z",
            "completed_at": "2026-07-27T00:00:01Z",
            "latency_ms": run["latency_ms"],
            "input_tokens": run["input_tokens"],
            "cached_input_tokens": 0,
            "output_tokens": run["output_tokens"],
            "list_cost_estimate_usd": run["cost_usd"],
            "provider_reported_cost_usd": run["cost_usd"],
            "quota_pool": run["quota_pool"],
            "allowance_before": None,
            "allowance_after": None,
            "zen_balance_before": None,
            "zen_balance_after": None,
            "raw_output_digest": run.get(
                "raw_output_digest", f"sha256:fixture-raw-output-{run['item_id']}"
            ),
            "tool_trace_digest": run.get(
                "tool_trace_digest", f"sha256:fixture-tool-trace-{run['item_id']}"
            ),
        },
        "safety": {
            "telemetry_complete": True,
            "schema_valid": run["schema_valid"],
            "tool_violations": run["tool_violations"],
            "wrote_to_repo": run["wrote_to_repo"],
            "spawned_subagent": run["spawned_subagent"],
            "consumed_peer_output": False,
            "repo_digest_before": digest,
            "repo_digest_after": digest,
            "timed_out": False,
            "provider_error": None,
        },
        "data_rights": data_rights,
        "summary": run.get("summary", ""),
        "unresolved": run.get("unresolved", []),
        "input_rights_record_id": data_rights["record_id"],
        "clarification_snapshot_id": run.get("clarification_snapshot_id"),
        "provider_documentation_snapshot_id": run.get("provider_documentation_snapshot_id"),
        "router_dataset_example_ids": run.get("router_dataset_example_ids", []),
        "evidence": run["evidence"],
    }


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
    write(args.out_dir, "runs.jsonl", [to_v2(r) for r in runs])
    write(args.out_dir, "judge.jsonl", judge)
    write(args.out_dir, "exec.jsonl", execres)
