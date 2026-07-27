#!/usr/bin/env python3
"""Regression coverage for validate_corpus.py and snapshot_terms.py.

Every assertion names a silent failure mode that should stop a run before scoring.
"""
from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path


HERE = Path(__file__).parent
PY = sys.executable


# ---------------------------------------------------------------------------
# helpers


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    payload = "\n".join(json.dumps(row) for row in rows) + "\n"
    path.write_text(payload, encoding="utf-8")


def _run_validate(corpus: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PY, "validate_corpus.py", "--corpus", str(corpus), *extra],
        cwd=HERE,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_snapshot(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PY, "snapshot_terms.py", *args],
        cwd=HERE,
        capture_output=True,
        text=True,
        check=False,
    )


def _base_label(*, label_id: str = "L1", adjudication: str = "human_review_comment",
                verify_cmd: str | None = None, path: str = "src/item.py", lines=(10, 20)) -> dict:
    lab: dict = {
        "label_id": label_id,
        "severity": 1,
        "kind": "correctness",
        "sites": [{"path": path, "lines": list(lines)}],
        "adjudication": adjudication,
    }
    if verify_cmd:
        lab["verify_cmd"] = verify_cmd
    return lab


def _base_item(
    *,
    item_id: str = "S1-0001",
    stratum: str = "S1_REVIEW_HUMAN",
    difficulty: str = "Type1_Direct",
    labels: list[dict] | None = None,
    trap: dict | None = None,
    repo_files: list[str] | None = None,
    dataset_ref: str = "test://dataset",
    **extra: object,
) -> dict:
    if labels is None:
        labels = [_base_label()]
    if repo_files is None:
        repo_files = ["src/item.py"]
    item = {
        "item_id": item_id,
        "stratum": stratum,
        "difficulty": difficulty,
        "source": "synthetic",
        "labels": labels,
        "repo_files": repo_files,
        "dataset_ref": dataset_ref,
        "provider_data_allowlist": ["opencode"],
        "license": "MIT",
        "license_url": "https://spdx.org/licenses/MIT.html",
        "goal": "Test corpus item",
        "problem_statement": "Validate this synthetic item.",
        "known_open_questions": "",
    }
    item.update(extra)
    if trap is not None:
        item["trap"] = trap
    return item


def _base_plan_corpus() -> list[dict]:
    items: list[dict] = []
    idx = 0

    def mk() -> str:
        nonlocal idx
        idx += 1
        return f"S{idx:04d}"

    for i in range(4):
        items.append(_base_item(item_id=f"S1-{mk()}", stratum="S1_REVIEW_HUMAN", difficulty="Type1_Direct"))
    for i in range(5):
        items.append(_base_item(item_id=f"S1-{mk()}", stratum="S1_REVIEW_HUMAN", difficulty="Type2_Contextual"))
    for i in range(5):
        items.append(_base_item(item_id=f"S1-{mk()}", stratum="S1_REVIEW_HUMAN", difficulty="Type3_Latent_Candidate"))

    for i in range(10):
        items.append(_base_item(
            item_id=f"S2-{mk()}",
            stratum="S2_PATCH_VERDICT",
            difficulty="resolved_agent_patch",
            labels=[],
        ))

    for i in range(8):
        items.append(_base_item(
            item_id=f"S3-{mk()}",
            stratum="S3_VULN_POC",
            difficulty="incomplete_fix",
            labels=[_base_label(
                label_id=f"L{i+1}",
                adjudication="fail_to_pass_test",
                verify_cmd="true",
                path=f"src/patch-{i}.py",
            )],
            repo_files=[f"src/patch-{i}.py"],
        ))

    for i in range(12):
        items.append(_base_item(
            item_id=f"S4-{mk()}",
            stratum="S4_FP_TRAP",
            difficulty="seeded_false_finding",
            trap={
                "trap_id": "T-1",
                "assertion": "Potentially interesting concern",
                "ground_truth": "invalid",
                "sites": [{"path": "src/item.py", "lines": [10, 15]}],
            },
            repo_files=["src/item.py"],
        ))

    for i in range(3):
        items.append(_base_item(
            item_id=f"S5-{mk()}",
            stratum="S5_NULL",
            difficulty="clean_merged",
            labels=[],
        ))

    return items


def _snapshot_tree(tmp_path: Path, snapshot_id: str) -> tuple[Path, list[str], list[Path]]:
    # Build a valid frozen tree using the module's own canonical URLs.
    import snapshot_terms

    root = tmp_path / "terms"
    entries: list[str] = []
    paths: list[Path] = []
    for component in snapshot_terms.SNAPSHOT_COMPONENTS[snapshot_id]:
        body_path = root / snapshot_id / f"{component}.body"
        body = f"lrhe offline fixture for {component}".encode("utf-8")
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)

        metadata_path = root / snapshot_id / f"{component}.metadata.json"
        metadata = {
            "url": snapshot_terms.TERMS_SOURCES[component],
            "final_url": snapshot_terms.TERMS_SOURCES[component],
            "byte_length": len(body),
            "fetched_at": "2026-07-27T00:00:00Z",
            "http_status": 200,
            "content_type": "text/plain",
            "redirects": [],
            "user_agent": snapshot_terms.USER_AGENT,
        }
        metadata["sha256"] = sha256(body).hexdigest()
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_text = json.dumps(metadata, indent=2) + "\n"
        metadata_sha = sha256(metadata_text.encode("utf-8")).hexdigest()
        metadata_path.write_text(metadata_text, encoding="utf-8")

        entries.append(f"{sha256(body).hexdigest()}  {snapshot_id}/{component}.body")
        entries.append(f"{metadata_sha}  {snapshot_id}/{component}.metadata.json")
        paths.extend([body_path, metadata_path])

    lines = sorted(entries, key=lambda row: row.split(maxsplit=1)[1])
    (root / snapshot_terms.MANIFEST_NAME).write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    return root, lines, paths


# ---------------------------------------------------------------------------
# validate_corpus.py


def test_validate_corpus_accepts_well_formed_synthetic_corpus(tmp_path: Path):
    """A synthetically balanced corpus can still pass if every gate is satisfied."""
    corpus = tmp_path / "corpus.jsonl"
    item = [
        _base_item(),
        _base_item(item_id="S2-0002", stratum="S2_PATCH_VERDICT", difficulty="resolved_agent_patch", labels=[]),
        _base_item(item_id="S3-0003", stratum="S3_VULN_POC", difficulty="incomplete_fix",
                   labels=[_base_label(label_id="L2", adjudication="fail_to_pass_test", verify_cmd="false")]),
        _base_item(item_id="S4-0004", stratum="S4_FP_TRAP", difficulty="seeded_false_finding",
                   trap={
                       "trap_id": "T1",
                       "assertion": "Prior reviewer concern for synthetic packet",
                       "ground_truth": "invalid",
                       "sites": [{"path": "src/item.py", "lines": [10, 15]}],
                   }),
        _base_item(item_id="S5-0005", stratum="S5_NULL", difficulty="clean_merged", labels=[]),
    ]
    _write_jsonl(corpus, item)

    p = _run_validate(corpus)
    assert p.returncode == 0, p.stdout + p.stderr


def test_validate_corpus_rejects_schema_missing_required_field(tmp_path: Path):
    """Missing required schema fields must reject a corpus before scoring starts."""
    corpus = tmp_path / "corpus.jsonl"
    row = _base_item()
    row.pop("source")
    _write_jsonl(corpus, [row])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "schema:" in p.stdout
    assert "required property" in p.stdout


def test_validate_corpus_rejects_duplicate_label_id(tmp_path: Path):
    """Duplicate label IDs make recall bookkeeping ambiguous and silently corrupt metrics."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(labels=[
        _base_label(label_id="L1"),
        _base_label(label_id="L1"),
    ])])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "duplicate label_id 'L1'" in p.stdout


def test_validate_corpus_rejects_executable_label_without_verify_cmd(tmp_path: Path):
    """Executable adjudication without verify_cmd quietly demotes to judge-only evidence."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(labels=[
        _base_label(adjudication="fail_to_pass_test", verify_cmd=None)
    ])])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "is executable but verify_cmd is missing" in p.stdout


def test_validate_corpus_rejects_label_path_outside_repo_files(tmp_path: Path):
    """A label anchored outside repo_files becomes FABRICATED in runtime scoring."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(labels=[
        _base_label(path="outside/file.py"),
    ], repo_files=["src/inside.py"])])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "site 'outside/file.py' is not in repo_files" in p.stdout


def test_validate_corpus_rejects_site_without_path(tmp_path: Path):
    """A label with an empty path cannot be anchored and must be rejected."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(labels=[
        _base_label(path=""),
    ])])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "site with empty path" in p.stdout


def test_validate_corpus_rejects_non_positive_site_start_line(tmp_path: Path):
    """Line numbers starting at 0 cannot be mapped during adjudication."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(labels=[
        _base_label(lines=(0, 5)),
    ])])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "start line 0 < 1" in p.stdout


def test_validate_corpus_rejects_inverted_site_range(tmp_path: Path):
    """Inverted label ranges cannot be resolved into a file span and must be caught."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(labels=[
        _base_label(lines=(20, 10)),
    ])])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "has inverted range" in p.stdout

def test_validate_corpus_rejects_s5_null_with_labels(tmp_path: Path):
    """S5 null items are no-fault items; labels would count false positives as hits."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(stratum="S5_NULL", difficulty="clean_merged", labels=[
        _base_label()
    ])])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "S5_NULL must carry zero labels" in p.stdout


def test_validate_corpus_rejects_stratum_without_labels(tmp_path: Path):
    """A non-control stratum with no labels cannot define recall on anything."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(stratum="S1_REVIEW_HUMAN", labels=[])])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "S1_REVIEW_HUMAN requires at least one label" in p.stdout


def test_validate_corpus_rejects_s5_null_with_trap(tmp_path: Path):
    """S5 null must stay null and trap-less; a trap there collapses null to adversarial mode."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(stratum="S5_NULL", difficulty="clean_merged", labels=[],
                                     trap={"trap_id": "T1", "assertion": "x", "ground_truth": "invalid"})])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "S5_NULL must not carry a trap" in p.stdout


def test_validate_corpus_rejects_s4_trap_without_ground_truth(tmp_path: Path):
    """A false-finding trap without ground_truth invalid makes every report unusable as evidence."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(stratum="S4_FP_TRAP", difficulty="seeded_false_finding", labels=[],
                                     trap={"trap_id": "T1", "assertion": "prior concern", "sites": [{"path": "src/item.py", "lines": [10, 15]}]})])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "trap ground_truth must be 'invalid'" in p.stdout


def test_validate_corpus_rejects_s4_trap_missing_assertion(tmp_path: Path):
    """A trap with no assertion cannot be planted in a packet as prior concern."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(stratum="S4_FP_TRAP", difficulty="seeded_false_finding", labels=[],
                                     trap={"trap_id": "T1", "assertion": "", "ground_truth": "invalid",
                                           "sites": [{"path": "src/item.py", "lines": [10, 15]}]})])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "trap has no assertion" in p.stdout


def test_validate_corpus_rejects_s4_trap_site_outside_repo_files(tmp_path: Path):
    """A trap that cannot be anchored to repo files leaks a false-negative anchor."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(
        stratum="S4_FP_TRAP",
        difficulty="seeded_false_finding",
        labels=[],
        trap={"trap_id": "T1", "assertion": "Potential concern", "ground_truth": "invalid",
              "sites": [{"path": "outside/file.py", "lines": [10, 15]}]},
        repo_files=["src/item.py"],
    )])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "trap site 'outside/file.py' is not in repo_files" in p.stdout


def test_validate_corpus_rejects_s4_trap_with_oracle_leak(tmp_path: Path):
    """A trap assertion that says it is seeded can only leak answer text to the packet."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(
        stratum="S4_FP_TRAP",
        difficulty="seeded_false_finding",
        labels=[],
        trap={"trap_id": "T1", "assertion": "This is a known invalid seeded trap.", "ground_truth": "invalid",
              "sites": [{"path": "src/item.py", "lines": [10, 15]}]},
    )])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "packet text leaks the trap answer" in p.stdout


def test_validate_corpus_rejects_s2_control_with_labels(tmp_path: Path):
    """S2 controls must remain label-free to remain true negative controls."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(
        item_id="S2-0001",
        stratum="S2_PATCH_VERDICT",
        difficulty="resolved_agent_patch",
        labels=[_base_label()],
    )])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "is a control and must carry zero labels" in p.stdout


def test_validate_corpus_rejects_non_executable_s2(tmp_path: Path):
    """S2 patch-verdict items require executable adjudication to prevent silent rubric drift."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(
        item_id="S2-0001",
        stratum="S2_PATCH_VERDICT",
        difficulty="Type2_Contextual",
        labels=[_base_label(adjudication="human_review_comment")],
    )])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "exists for its executable label; none is executable" in p.stdout


def test_validate_corpus_rejects_duplicate_item_id_with_plan_check(tmp_path: Path):
    """Plan verification must reject duplicated IDs before assignment can be reproduced."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [
        _base_item(item_id="S1-dup", labels=[_base_label()]),
        _base_item(item_id="S1-dup", labels=[_base_label(label_id="L2")]),
    ])

    p = _run_validate(corpus, "--plan")
    assert p.returncode == 1
    assert "duplicate item_id 'S1-dup' appears 2 times" in p.stdout


def test_validate_corpus_reports_stratum_counts_with_plan(tmp_path: Path):
    """Plan mode must fail noisily when count assumptions no longer match the corpus."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(item_id="S1-0001")])

    p = _run_validate(corpus, "--plan")
    assert p.returncode == 0
    assert "S1_REVIEW_HUMAN: 1 items, pre-registered 14" in p.stdout
    assert "S2_PATCH_VERDICT: 0 items, pre-registered 10" in p.stdout
    assert "WARN  <plan>:" in p.stdout


def test_validate_corpus_returns_non_zero_on_plan_warning_when_strict(tmp_path: Path):
    """Strict mode must make plan mismatch an explicit hard failure for runners."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(item_id="S1-0001")])

    p = _run_validate(corpus, "--plan", "--strict")
    assert p.returncode == 1
    assert "WARN  <plan>:" in p.stdout


def test_validate_corpus_rejects_unresolved_license_with_allowlist(tmp_path: Path):
    """Unresolved license plus explicit provider allowlist would silently widen rights."""
    corpus = tmp_path / "corpus.jsonl"
    item = _base_item(license="NOASSERTION", license_url="https://example.org/license")
    _write_jsonl(corpus, [item])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "license 'NOASSERTION' is unresolved but the item is authorized" in p.stdout


def test_validate_corpus_rejects_stratum_date_gate_violation(tmp_path: Path):
    """A merged commit before the gate date silently violates the egress freeze window."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, [_base_item(
        item_id="S1-0001",
        merged_at="2026-07-27T00:00:00Z",
        date_gate_cutoff="2026-08-01",
    )])

    p = _run_validate(corpus)
    assert p.returncode == 1
    assert "date gate violated" in p.stdout


# ---------------------------------------------------------------------------
# snapshot_terms.py


def test_snapshot_terms_offline_self_test_runs_without_network():
    """The self-test exercises the same verifier and proves the module starts offline-safe."""
    p = _run_snapshot("--offline", "--self-test")
    assert p.returncode == 0
    assert "offline self-test verified" in p.stdout


def test_snapshot_terms_offline_verifies_locally_constructed_snapshot(tmp_path: Path):
    """Locally built snapshot trees must verify to be treated as frozen evidence."""
    snapshot_id = "opencode-terms-2026-03-06__go-docs-2026-07-27"
    terms_root, manifest, _ = _snapshot_tree(tmp_path, snapshot_id)

    p = _run_snapshot("--offline", "--terms-dir", str(terms_root), "--snapshot-id", snapshot_id)
    assert p.returncode == 0
    assert f"offline verification passed: {len(manifest)} manifest entries" in p.stdout


def test_snapshot_terms_offline_detects_one_byte_manifested_mutation(tmp_path: Path):
    """One-byte edits after freezing must invalidate the manifest immediately."""
    snapshot_id = "opencode-terms-2026-03-06__go-docs-2026-07-27"
    terms_root, _, paths = _snapshot_tree(tmp_path, snapshot_id)
    body = paths[0]
    data = bytearray(body.read_bytes())
    data[0] = (data[0] + 1) % 256
    body.write_bytes(bytes(data))

    p = _run_snapshot("--offline", "--terms-dir", str(terms_root), "--snapshot-id", snapshot_id)
    assert p.returncode == 4
    assert "SHA-256 mismatch" in p.stderr


def test_snapshot_terms_offline_detects_manifested_missing_file(tmp_path: Path):
    """A manifest entry pointing at missing content cannot remain silently acceptable."""
    snapshot_id = "opencode-terms-2026-03-06__go-docs-2026-07-27"
    terms_root, _, paths = _snapshot_tree(tmp_path, snapshot_id)
    paths[0].unlink()

    p = _run_snapshot("--offline", "--terms-dir", str(terms_root), "--snapshot-id", snapshot_id)
    assert p.returncode == 4
    assert "manifest path is missing" in p.stderr


def test_snapshot_terms_retains_expected_canonical_terms_urls():
    """Canonical URL constants must preserve the legal sources in evidence snapshots."""
    import snapshot_terms

    assert snapshot_terms.TERMS_SOURCES["opencode-terms-of-service"] == (
        "https://opencode.ai/legal/terms-of-service"
    )
    assert snapshot_terms.TERMS_SOURCES["anthropic-consumer-model-training"] == (
        "https://privacy.claude.com/en/articles/10023580-is-my-data-used-for-model-training"
    )


def test_validate_corpus_plan_aligned_counts_round_trip(tmp_path: Path):
    """When plan counts are exact, --plan must emit no plan warnings."""
    corpus = tmp_path / "corpus.jsonl"
    _write_jsonl(corpus, _base_plan_corpus())

    p = _run_validate(corpus, "--plan")
    assert p.returncode == 0
    assert p.stdout == "\n47 items, 0 errors, 0 warnings\n"
    assert "WARN  <plan>:" not in p.stdout
