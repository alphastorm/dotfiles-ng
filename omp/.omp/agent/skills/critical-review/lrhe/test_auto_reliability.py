#!/usr/bin/env python3
"""Pre-dispatch invariants for the automated judge-reliability audit."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
PY = sys.executable
DATA = Path.home() / ".omp/agent/skills/critical-review/lrhe-data"
CALIBRATION = DATA / "judge-calibration-packet.csv"
CLAIMS = DATA / "floor/claims-floor.csv"
CORPUS = DATA / "corpus.jsonl"
JUDGE_AGG = DATA / "judge-floor-agg.jsonl"
JUDGE_RAW = DATA / "judge-floor.jsonl"

sys.path.insert(0, str(HERE))
import auto_reliability  # noqa: E402
import judge_render  # noqa: E402


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PY, *args], cwd=HERE, capture_output=True, text=True,
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_keys(path: Path) -> list[tuple[str, str]]:
    with path.open() as fh:
        return [(row["run_id"], row["claim_rid"]) for row in csv.DictReader(fh)]


def _require_real_data(*paths: Path) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        pytest.skip("private LRHE data are not linked: " + ", ".join(missing))


def _synthetic_inputs(base: Path) -> dict:
    item = {
        "item_id": "S1-abc12345",
        "goal": "Keep the parser deterministic",
        "problem_statement": "The parser must retain stable behavior.",
        "repo_files": ["src/parser.py"],
        "design_or_diff": "def parse(value):\n    return VISIBLE_EVIDENCE_TOKEN\n",
        "labels": [{
            "label_id": "L1",
            "severity": 1,
            "sites": [{"path": "src/parser.py"}],
            "description": "The parser drops the final token.",
        }],
    }
    claim = {
        "run_id": "S1-abc12345-deepseek-floor-deadbeef",
        "rid": "1",
        "item_id": item["item_id"],
        "severity": "1",
        "confidence": "0.83",
        "claim_text": "The parser drops the final token.",
        "evidence_text": "src/parser.py returns before consuming it.",
        "impact_text": "A valid token is lost.",
    }
    panel = {
        "run_id": claim["run_id"],
        "claim_rid": claim["rid"],
        # Sentinels detect interpolation. Real verdict and label tokens necessarily occur
        # in the canonical rubric and ground-truth label block.
        "verdict": "PANEL_VERDICT_SECRET",
        "label_id": "PANEL_LABEL_SECRET",
        "rationale": "PANEL_RATIONALE_SECRET",
        "needs_human": False,
        "unanimous": True,
        "panel": ["claude", "gemini"],
        "votes": {"claude": "PANEL_VOTE_SECRET"},
    }
    corpus = base / "corpus.jsonl"
    claims = base / "claims.csv"
    judge = base / "judge.jsonl"
    selection = base / "selection.csv"
    _write_jsonl(corpus, [item])
    _write_csv(claims, list(claim), [claim])
    _write_jsonl(judge, [panel])
    _write_csv(selection, ["run_id", "claim_rid"], [{
        "run_id": claim["run_id"], "claim_rid": claim["rid"],
    }])
    return {
        "item": item,
        "claim": claim,
        "panel": panel,
        "corpus": corpus,
        "claims": claims,
        "judge": judge,
        "selection": selection,
    }


def _build(inputs: dict, out: Path, controls: Path | None = None, *, all_families: bool = False):
    args = [
        "auto_reliability.py", "build",
        "--selection", str(inputs["selection"]),
        "--claims", str(inputs["claims"]),
        "--corpus", str(inputs["corpus"]),
        "--judge", str(inputs["judge"]),
        "--out", str(out),
    ]
    if controls is not None:
        args.extend(["--controls", str(controls)])
    if not all_families:
        args.extend(["--families", "claude"])
    return _run(args)


@pytest.fixture
def built(tmp_path: Path) -> dict:
    inputs = _synthetic_inputs(tmp_path / "inputs")
    out = tmp_path / "build"
    result = _build(inputs, out)
    assert result.returncode == 0, result.stdout + result.stderr
    return {**inputs, "out": out}


def _assignment() -> dict:
    family = "claude"
    return {
        "assignment_id": "AR-0001|claude|1",
        "case_id": "AR-0001",
        "family": family,
        "rep": 1,
        "agent": auto_reliability.FAMILIES[family]["agent"],
        "requested_selector": auto_reliability.FAMILIES[family]["selector"],
        "prompt_sha256": "a" * 64,
    }


def _response(assignment: dict | None = None) -> dict:
    assignment = assignment or _assignment()
    return {
        "assignment_id": assignment["assignment_id"],
        "case_id": assignment["case_id"],
        "verdict": "CONFIRMED",
        "label_id": "L1",
        "confidence": 0.8,
        "rationale": "The claim matches L1.",
        "family": assignment["family"],
        "requested_selector": assignment["requested_selector"],
        "served_model": assignment["requested_selector"],
        "identity_verified": True,
        "fallback_used": False,
        "named_tools": [],
        "prompt_sha256": assignment["prompt_sha256"],
        "started_at": "2026-07-28T00:00:00Z",
        "finished_at": "2026-07-28T00:00:01Z",
    }


def _ingest_files(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    assignment = _assignment()
    manifest = tmp_path / "assignments.jsonl"
    cases = tmp_path / "cases.jsonl"
    responses = tmp_path / "responses.jsonl"
    _write_jsonl(manifest, [assignment])
    _write_jsonl(cases, [{
        "case_id": assignment["case_id"],
        "prompt": "prompt",
        "prompt_sha256": assignment["prompt_sha256"],
        "label_ids": ["L1"],
    }])
    _write_jsonl(responses, [_response(assignment)])
    return manifest, cases, responses, assignment


def test_01_selection_keys_are_preserved_exactly(tmp_path: Path):
    _require_real_data(CALIBRATION, CLAIMS, CORPUS, JUDGE_AGG)
    out = tmp_path / "real-build"
    result = _run([
        "auto_reliability.py", "build",
        "--selection", str(CALIBRATION),
        "--claims", str(CLAIMS),
        "--corpus", str(CORPUS),
        "--judge", str(JUDGE_AGG),
        "--families", "claude",
        "--out", str(out),
    ])
    assert result.returncode == 0, result.stdout + result.stderr

    expected = _read_keys(CALIBRATION)
    actual = _read_keys(out / "selection-60.csv")
    assert len(expected) == 60
    assert actual == expected
    assert all(run_id.endswith("-1785281118") for run_id, _ in actual)


def test_02_case_ids_leak_nothing(built: dict):
    cases = _read_jsonl(built["out"] / "cases.jsonl")
    case_map = _read_jsonl(built["out"] / "case-map.private.jsonl")
    assert len(cases) == len(case_map) == 1
    allowed = {"case_id", "prompt", "prompt_sha256", "label_ids"}
    forbidden_tokens = {
        built["item"]["item_id"], "S1", "S2", "S3", "S4", "S5",
        *auto_reliability.FAMILIES, "OC_FULL", "OC_NEAR", "OC_BLIND", "floor", "trap",
    }
    for row in cases:
        assert re.fullmatch(r"AR-\d{4}", row["case_id"])
        assert not any(token.lower() in row["case_id"].lower() for token in forbidden_tokens)
        assert set(row) == allowed
        assert not ({
            "item_id", "stratum", "panel_verdict", "panel_label_id",
            "author_family", "trap", "arm",
        } & set(row))


def test_03_prompt_is_the_canonical_evidence_surface(built: dict):
    case = _read_jsonl(built["out"] / "cases.jsonl")[0]
    case_map = _read_jsonl(built["out"] / "case-map.private.jsonl")[0]
    prompt = (built["out"] / "prompts" / f"{case['case_id']}.txt").read_text()
    expected = judge_render.render_judge(built["item"], built["claim"])

    assert case_map["run_id"] == built["claim"]["run_id"]
    assert case_map["claim_rid"] == built["claim"]["rid"]
    assert prompt == expected
    assert case["prompt"] == expected
    assert case["prompt_sha256"] == hashlib.sha256(expected.encode()).hexdigest()
    assert auto_reliability.render_judge is judge_render.render_judge


def test_04_prompt_carries_no_canonical_votes(built: dict):
    case = _read_jsonl(built["out"] / "cases.jsonl")[0]
    case_map = _read_jsonl(built["out"] / "case-map.private.jsonl")[0]
    prompt = (built["out"] / "prompts" / f"{case['case_id']}.txt").read_text()

    for field in ("panel_verdict", "panel_label_id"):
        assert case_map[field]
        assert case_map[field] not in prompt
    assert built["panel"]["rationale"] not in prompt
    assert built["panel"]["votes"]["claude"] not in prompt
    assert re.search(r"\b(?:panel|votes?)\b", prompt, flags=re.IGNORECASE) is None
    # Dispatchable bytes are reproduced from corpus + claim, never from the private map.
    assert prompt == judge_render.render_judge(built["item"], built["claim"])
    assert set(case) == {"case_id", "prompt", "prompt_sha256", "label_ids"}


def test_05_every_family_is_tool_less():
    agents = Path.home() / ".omp/agent/agents"
    if not agents.is_dir():
        pytest.skip("judge agent definitions are not present in this checkout")
    assert len(auto_reliability.FAMILIES) == 5
    for family, definition in auto_reliability.FAMILIES.items():
        path = agents / f"{definition['agent']}.md"
        assert path.is_file(), f"missing {family} definition {path}"
        parts = path.read_text().split("---", 2)
        assert len(parts) == 3, f"{path.name} has no YAML front matter"
        front = yaml.safe_load(parts[1])
        assert front["tools"] == [], f"{path.name} declares {front.get('tools')}"


def test_06_missing_served_model_telemetry_fails_closed():
    assignment = _assignment()
    valid = _response(assignment)
    assert auto_reliability.validate_response(valid, assignment, {"L1"}) == []

    missing_model = dict(valid)
    del missing_model["served_model"]
    reasons = auto_reliability.validate_response(missing_model, assignment, {"L1"})
    assert reasons and any("served_model absent" in reason for reason in reasons)

    missing_tools = dict(valid)
    del missing_tools["named_tools"]
    reasons = auto_reliability.validate_response(missing_tools, assignment, {"L1"})
    assert reasons and any("named_tools absent" in reason for reason in reasons)


def test_07_fallback_fails_closed():
    assignment = _assignment()
    fallback = _response(assignment)
    fallback["fallback_used"] = True
    reasons = auto_reliability.validate_response(fallback, assignment, {"L1"})
    assert any("fallback model" in reason for reason in reasons)

    mismatched = _response(assignment)
    mismatched["requested_selector"] = "other/provider-model"
    reasons = auto_reliability.validate_response(mismatched, assignment, {"L1"})
    assert any("requested_selector does not match" in reason for reason in reasons)


def test_08_partial_response_sets_refuse_aggregation(tmp_path: Path):
    fresh = tmp_path / "fresh.jsonl"
    case_map = tmp_path / "case-map.jsonl"
    manifest = tmp_path / "assignments.jsonl"
    out = tmp_path / "aggregate.jsonl"
    families = list(auto_reliability.FAMILIES)
    _write_jsonl(manifest, [{
        "assignment_id": f"AR-0001|{family}|1", "case_id": "AR-0001",
        "family": family, "rep": 1,
    } for family in families])
    _write_jsonl(case_map, [{"case_id": "AR-0001", "kind": "case"}])
    _write_jsonl(fresh, [{
        "case_id": "AR-0001", "family": family, "rep": 1,
        "verdict": "PLAUSIBLE", "label_id": "", "confidence": 0.8,
    } for family in families[:-1]])

    result = _run([
        "auto_reliability.py", "aggregate",
        "--fresh", str(fresh), "--case-map", str(case_map),
        "--manifest", str(manifest), "--out", str(out),
    ])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "incomplete panel" in result.stderr
    assert not out.exists()


def test_09_two_two_one_stays_unresolved():
    split = [
        {"verdict": verdict, "label_id": "L1" if verdict == "CONFIRMED" else ""}
        for verdict in ("CONFIRMED", "CONFIRMED", "PLAUSIBLE", "PLAUSIBLE", "FABRICATED")
    ]
    result = auto_reliability.aggregate_case(split, 3)
    assert result["verdict"] == auto_reliability.UNRESOLVED
    assert result["needs_resolution"] is True
    assert result["n_top"] == 2

    majority = split[:2] + [{"verdict": "CONFIRMED", "label_id": "L1"}] + split[3:]
    resolved = auto_reliability.aggregate_case(majority, 3)
    assert resolved["verdict"] == "CONFIRMED"
    assert resolved["label_id"] == "L1"


def test_10_invalid_label_ids_are_rejected():
    assignment = _assignment()
    invalid = _response(assignment)
    invalid["label_id"] = "L99"
    reasons = auto_reliability.validate_response(invalid, assignment, {"L1"})
    assert any("not in this case's label set" in reason for reason in reasons)

    absent = _response(assignment)
    absent["label_id"] = None
    reasons = auto_reliability.validate_response(absent, assignment, {"L1"})
    assert any("CONFIRMED requires a label_id" in reason for reason in reasons)


def test_11_plausible_and_fabricated_with_label_ids_are_rejected():
    assignment = _assignment()
    for verdict in ("PLAUSIBLE", "FABRICATED"):
        response = _response(assignment)
        response["verdict"] = verdict
        response["label_id"] = "L1"
        reasons = auto_reliability.validate_response(response, assignment, {"L1"})
        assert any(f"{verdict} must carry label_id null" in reason for reason in reasons)


def test_12_no_model_response_can_create_execution_evidence(tmp_path: Path):
    assignment = _assignment()
    for field, value in (("ran", True), ("exit_code", 0), ("reproduced", False)):
        response = _response(assignment)
        response[field] = value
        reasons = auto_reliability.validate_response(response, assignment, {"L1"})
        assert reasons and any(field in reason and "runner-only" in reason for reason in reasons)

    manifest, cases, responses, _ = _ingest_files(tmp_path)
    out = tmp_path / "judgments-fresh.jsonl"
    result = _run([
        "auto_reliability.py", "ingest", "--manifest", str(manifest),
        "--cases", str(cases), "--responses", str(responses), "--out", str(out),
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    judgement = _read_jsonl(out)[0]
    assert not ({"ran", "exit_code", "reproduced"} & set(judgement))

    schema = json.loads((HERE / "exec-evidence.schema.json").read_text())
    runner_only = {
        "ran", "command", "exit_code", "reproduced", "stdout_sha256", "stderr_sha256",
        "repo_digest_before", "repo_digest_after", "runner_version", "started_at", "finished_at",
    }
    assert runner_only <= set(schema["required"])
    assert schema["properties"]["ran"]["const"] is True
    assert schema["additionalProperties"] is False


def test_13_cost_cap_projection_fails_before_dispatch(tmp_path: Path):
    manifest = tmp_path / "assignments.jsonl"
    prices = tmp_path / "prices.json"
    ledger = tmp_path / "dispatch-ledger.jsonl"
    _write_jsonl(manifest, [{"family": "claude"}])
    prices.write_text(json.dumps({
        "claude": {"input": 1.0, "output": 0.0, "metered": True},
    }))
    common = [
        "auto_reliability.py", "budget", "--manifest", str(manifest),
        "--prices", str(prices), "--ledger", str(ledger),
        "--input-tokens", "1000000", "--output-tokens", "0",
        "--soft-alert-usd", "999", "--stop-threshold-usd", "999",
    ]

    refused = _run([*common, "--hard-cap-usd", "0.50"])
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "exceeds the hard cap" in refused.stderr
    assert not ledger.exists()

    allowed = _run([*common, "--hard-cap-usd", "2.00"])
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert not ledger.exists()


def test_14_resume_and_reingest_are_idempotent(tmp_path: Path):
    manifest, cases, responses, assignment = _ingest_files(tmp_path)
    out = tmp_path / "judgments-fresh.jsonl"
    command = [
        "auto_reliability.py", "ingest", "--manifest", str(manifest),
        "--cases", str(cases), "--responses", str(responses), "--out", str(out),
    ]
    first = _run(command)
    assert first.returncode == 0, first.stdout + first.stderr
    first_bytes = out.read_bytes()
    second = _run(command)
    assert second.returncode == 0, second.stdout + second.stderr
    assert out.read_bytes() == first_bytes

    original = _response(assignment)
    original["confidence"] = 0.4
    duplicate = dict(original)
    duplicate["confidence"] = 0.9
    duplicate["rationale"] = "A later answer must not overwrite the first."
    duplicate_responses = tmp_path / "duplicates.jsonl"
    duplicate_out = tmp_path / "duplicate-judgments.jsonl"
    _write_jsonl(duplicate_responses, [original, duplicate])
    rejected = _run([
        "auto_reliability.py", "ingest", "--manifest", str(manifest),
        "--cases", str(cases), "--responses", str(duplicate_responses),
        "--out", str(duplicate_out),
    ])
    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    assert "duplicate response" in rejected.stdout
    assert _read_jsonl(duplicate_out)[0]["confidence"] == 0.4
    rejection_rows = _read_jsonl(tmp_path / "rejected-fresh.jsonl")
    assert rejection_rows[0]["reasons"] == ["duplicate response"]


def test_15_canonical_files_remain_byte_identical(tmp_path: Path):
    canonical = [CALIBRATION, JUDGE_AGG, JUDGE_RAW, CORPUS]
    _require_real_data(*canonical, CLAIMS)
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in canonical}
    result = _run([
        "auto_reliability.py", "build",
        "--selection", str(CALIBRATION),
        "--claims", str(CLAIMS),
        "--corpus", str(CORPUS),
        "--judge", str(JUDGE_AGG),
        "--out", str(tmp_path / "full-build"),
    ])
    assert result.returncode == 0, result.stdout + result.stderr
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in canonical}
    assert after == before


def test_16_human_kappa_refuses_partial_or_invalid_packets(tmp_path: Path):
    judge = [{
        "run_id": "r1", "claim_rid": "1", "verdict": "PLAUSIBLE", "label_id": "",
    }]
    corpus = [{"item_id": "S1-item", "labels": [{"label_id": "L1"}]}]

    def run_packet(directory: Path, human_verdict: str, expect_rows: int):
        packet = directory / "packet.csv"
        judge_path = directory / "judge.jsonl"
        corpus_path = directory / "corpus.jsonl"
        _write_csv(packet, [
            "run_id", "claim_rid", "item_id", "human_verdict", "human_label_id",
        ], [{
            "run_id": "r1", "claim_rid": "1", "item_id": "S1-item",
            "human_verdict": human_verdict, "human_label_id": "",
        }])
        _write_jsonl(judge_path, judge)
        _write_jsonl(corpus_path, corpus)
        return _run([
            "judge_lrhe.py", "kappa", "--calibration", str(packet),
            "--judge", str(judge_path), "--corpus", str(corpus_path),
            "--expect-rows", str(expect_rows),
        ])

    partial = run_packet(tmp_path / "partial", "", 1)
    assert partial.returncode == 2, partial.stdout + partial.stderr
    assert "human_verdict is empty" in partial.stderr
    assert "Cohen's kappa" not in partial.stdout + partial.stderr

    wrong_size = run_packet(tmp_path / "wrong-size", "PLAUSIBLE", 2)
    assert wrong_size.returncode == 2, wrong_size.stdout + wrong_size.stderr
    assert "expected exactly 2 rows, found 1" in wrong_size.stderr
    assert "Cohen's kappa" not in wrong_size.stdout + wrong_size.stderr


def test_17_readme_fingerprint_language_matches_not_observed():
    text = (HERE / "README.md").read_text()
    assert "OpenCode exposes no fingerprint" not in text
    assert "not_observed" in text
    assert "provider_fingerprint_observation` is `not_observed`, never `observed_absent`" in text
    assert "provider_fingerprint_observation` is `observed_absent`" not in text


def test_18_controls_are_mechanically_falsifiable(tmp_path: Path):
    inputs = _synthetic_inputs(tmp_path / "inputs")
    invalid_specs = [
        ({
            "control_id": "CTRL-FAB", "item_id": inputs["item"]["item_id"],
            "expected": "FABRICATED", "claim_text": "The visible token is absent.",
            "contradicted_by": "VISIBLE_EVIDENCE_TOKEN",
        }, "DOES appear"),
        ({
            "control_id": "CTRL-PL", "item_id": inputs["item"]["item_id"],
            "expected": "PLAUSIBLE", "claim_text": "A missing token is present.",
            "grounded_in": "MISSING_EVIDENCE_TOKEN",
            "no_label_rationale": "No label concerns this observation.",
        }, "is absent"),
        ({
            "control_id": "CTRL-CONF", "item_id": inputs["item"]["item_id"],
            "expected": "CONFIRMED", "claim_text": "This matches another label.",
            "label_id": "L99",
        }, "does not carry"),
    ]
    for index, (spec, message) in enumerate(invalid_specs, 1):
        controls = tmp_path / f"invalid-{index}.jsonl"
        out = tmp_path / f"invalid-build-{index}"
        _write_jsonl(controls, [spec])
        result = _build(inputs, out, controls)
        assert result.returncode == 2, result.stdout + result.stderr
        assert message in result.stderr
        assert not out.exists()

    invented = "INVENTED_CONTROL_TOKEN"
    valid_spec = {
        "control_id": "CTRL-VALID", "item_id": inputs["item"]["item_id"],
        "expected": "FABRICATED",
        "claim_text": f"The implementation calls {invented}.",
        "evidence_text": "The invented call would occur in parse().",
        "impact_text": "It would change parsing.",
        "contradicted_by": invented,
    }
    synthetic_claim = {
        "severity": valid_spec.get("severity", 2),
        "confidence": valid_spec.get("confidence", 0.7),
        "claim_text": valid_spec["claim_text"],
        "evidence_text": valid_spec["evidence_text"],
        "impact_text": valid_spec["impact_text"],
    }
    assert invented in judge_render.render_judge(inputs["item"], synthetic_claim)
    assert invented not in judge_render.evidence_surface(inputs["item"])
    assert auto_reliability.evidence_surface is judge_render.evidence_surface
    assert auto_reliability._validate_control(valid_spec, inputs["item"]) == []

    controls = tmp_path / "valid.jsonl"
    out = tmp_path / "valid-build"
    _write_jsonl(controls, [valid_spec])
    result = _build(inputs, out, controls)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(_read_jsonl(out / "controls.jsonl")) == 1


def _margin_votes(*spec):
    """('CONFIRMED','L1'), ('PLAUSIBLE','') -> vote dicts."""
    return [{"verdict": v, "label_id": lid} for v, lid in spec]


def test_19_bare_majority_is_never_recorded_as_settled():
    """3-of-5 clears the threshold by exactly one vote, so it is one flip from a tie.

    Measured across two independent 5-voter panels on auto-reliability-v1: consensus
    accuracy is 0.72 at a bare majority against 0.96 at 4-1 and 0.99 at 5-0, and the
    bare bucket holds 13 of 16 observed consensus errors.
    """
    bare = _margin_votes(
        ("FABRICATED", ""), ("FABRICATED", ""), ("FABRICATED", ""),
        ("PLAUSIBLE", ""), ("PLAUSIBLE", ""),
    )
    result = auto_reliability.aggregate_case(bare, 3)
    assert result["verdict"] == "FABRICATED"
    assert result["n_top"] == 3
    assert result["needs_resolution"] is True, "a bare majority must escalate"
    # the verdict itself is unchanged -- this flags, it does not withhold
    assert result["label_id"] == ""


def test_20_a_clear_majority_still_settles():
    """4-1 and 5-0 must not be dragged into the queue by the margin rule."""
    for n_top in (4, 5):
        votes = _margin_votes(
            *([("FABRICATED", "")] * n_top + [("PLAUSIBLE", "")] * (5 - n_top))
        )
        result = auto_reliability.aggregate_case(votes, 3)
        assert result["verdict"] == "FABRICATED"
        assert result["n_top"] == n_top
        assert result["needs_resolution"] is False, f"{n_top}-{5 - n_top} should settle"


def test_21_label_split_stays_keyed_to_label_disagreement():
    """`label_split` reports which defects a CONFIRMED majority disagreed about.

    It was previously gated on `needs_resolution`. Broadening that flag to cover margin
    would silently start populating `label_split` on every bare majority, changing the
    meaning of a field the label-recall figures read.
    """
    # bare majority, but the winners agree on the label -> escalate, no label split
    agreed = _margin_votes(
        ("CONFIRMED", "L1"), ("CONFIRMED", "L1"), ("CONFIRMED", "L1"),
        ("PLAUSIBLE", ""), ("FABRICATED", ""),
    )
    result = auto_reliability.aggregate_case(agreed, 3)
    assert result["needs_resolution"] is True   # because the margin is bare
    assert result["label_split"] == []          # but the labels did not disagree
    assert result["label_id"] == "L1"

    # comfortable majority whose winners named different defects -> escalate on labels
    disagreed = _margin_votes(
        ("CONFIRMED", "L1"), ("CONFIRMED", "L2"), ("CONFIRMED", "L3"),
        ("CONFIRMED", "L1"), ("PLAUSIBLE", ""),
    )
    result = auto_reliability.aggregate_case(disagreed, 3)
    assert result["n_top"] == 4
    assert result["needs_resolution"] is True
    assert result["label_split"] == ["L1", "L2", "L3"]
    assert result["label_id"] == ""


def test_22_aggregate_rows_declare_the_policy_that_produced_them():
    """A derivative aggregate must say which producer policy it came from.

    `aggregate-fresh.jsonl` was produced under v1, where a bare majority was recorded as
    settled. Any recomputation is a versioned derivative, so the rows carry the version
    rather than relying on a filename to date them.
    """
    assert auto_reliability.AGGREGATION_POLICY_VERSION == 2

    bare = _margin_votes(
        ("FABRICATED", ""), ("FABRICATED", ""), ("FABRICATED", ""),
        ("PLAUSIBLE", ""), ("PLAUSIBLE", ""),
    )
    # v1 recorded this as settled; v2 escalates it. That difference is the whole reason
    # the version exists, so pin both halves here.
    result = auto_reliability.aggregate_case(bare, 3)
    assert result["n_top"] == 3
    assert result["needs_resolution"] is True
