import hashlib
import json
from pathlib import Path

import pytest

import selector_canary as sc


ARMS = [
    {"agent": "codexExec", "id": "gpt-5.6-sol-low", "model": "gpt-5.6-sol-low"},
    {"agent": "openCode", "id": "kimi-k3", "model": "opencode-go/kimi-k3"},
]
CASE_IDS = ["case-one", "case-two", "case-three", "case-four"]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "selector-canary-v1"
    workspace = root / "workspace"
    files = []
    cases = []
    for case_id in CASE_IDS:
        design = f"designs/{case_id}.design.md"
        cases.append({"design": design, "id": case_id})
        definitions = [
            (design, "required", f"# {case_id}\n\nVerify the named operation.\n"),
            (f"src/{case_id}/main.py", "required", "def operation():\n    return 'ok'\n"),
            (f"src/{case_id}/support.py", "allowed_support", "VALUE = 'support'\n"),
            (f"src/{case_id}/decoy.py", "decoy", "VALUE = 'decoy'\n"),
        ]
        for relative, role, content in definitions:
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            raw = content.encode()
            files.append({
                "bytes": len(raw),
                "caseId": case_id,
                "path": relative,
                "role": role,
                "sha256": _sha(raw),
            })
    files.sort(key=lambda row: row["path"])
    digest_rows = [
        {"bytes": row["bytes"], "path": row["path"], "sha256": row["sha256"]}
        for row in files
    ]
    manifest = {
        "arms": ARMS,
        "cases": cases,
        "corpusId": "selector-canary-v1",
        "files": files,
        "replicates": 3,
        "schema": sc.CORPUS_SCHEMA,
        "workspace": "workspace",
        "workspaceSha256": sc._workspace_digest(digest_rows),
    }
    control = root / "control"
    control.mkdir(parents=True)
    (control / "corpus-manifest.v1.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return root


def _roles(corpus: sc.Corpus, case_id: str, role: str) -> list[str]:
    return sorted(
        path for path, fact in corpus.files.items()
        if fact.case_id == case_id and fact.role == role
    )


def _run_row(
    corpus: sc.Corpus,
    arm_id: str,
    case_id: str,
    replicate: int,
    selected: list[str],
) -> dict:
    selected = sorted(selected)
    tokens = {path: index + 7 for index, path in enumerate(selected)}
    selection_sha = sc._selection_sha(tuple(selected))
    case = next(item for item in corpus.cases if item.id == case_id)
    return {
        "armId": arm_id,
        "caseId": case_id,
        "corpusId": corpus.corpus_id,
        "durationMs": 1000 + replicate,
        "exportResponse": False,
        "finalSelectionSha256": selection_sha,
        "oracleDisposition": "run-discarded",
        "preOracleSelectionSha256": selection_sha,
        "promptSha256": sc.prompt_sha256(case),
        "replicate": replicate,
        "runId": f"{arm_id}:{case_id}:r{replicate}",
        "schema": sc.RUN_SCHEMA,
        "selectedByteTotal": sum(corpus.files[path].bytes for path in selected),
        "selectedPathTokens": [
            {"path": path, "tokens": tokens[path]} for path in selected
        ],
        "selectedPaths": selected,
        "selectedTokenTotal": sum(tokens.values()),
        "workspaceSha256": corpus.workspace_sha256,
    }


def _complete_rows(corpus: sc.Corpus) -> list[dict]:
    rows = []
    for arm in corpus.arms:
        for case in corpus.cases:
            required = _roles(corpus, case.id, "required")
            for replicate in range(1, 4):
                rows.append(_run_row(corpus, arm.id, case.id, replicate, required))
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def test_complete_matrix_scores_deterministically(tmp_path):
    root = _build_corpus(tmp_path)
    corpus = sc.load_corpus(root)
    rows = _complete_rows(corpus)
    for index, row in enumerate(rows):
        if row["armId"] == "kimi-k3":
            selected = _roles(corpus, row["caseId"], "required") + _roles(
                corpus, row["caseId"], "allowed_support"
            )
            rows[index] = _run_row(
                corpus, row["armId"], row["caseId"], row["replicate"], selected
            )
    runs = root / "runs/complete.jsonl"
    _write_jsonl(runs, rows)

    matrix, runs_sha = sc.load_runs(corpus, runs)
    first = sc.score(corpus, matrix, runs_sha)
    second = sc.score(corpus, matrix, runs_sha)

    assert first == second
    assert first["matrix"] == {"expectedCells": 24, "scoredCells": 24}
    assert [row["microCriticalRecall"] for row in first["armAggregates"]] == [1.0, 1.0]
    assert [row["meanCaseStability"] for row in first["armAggregates"]] == [1.0, 1.0]
    assert first["armAggregates"][1]["pooledAllowedSupportInclusionRate"] == 1.0
    assert first["armAggregates"][1]["pooledFalseInclusionRate"] == 0.0
    assert all(not cell["falseInclusionPaths"] for cell in first["cells"])


def test_prompt_names_design_but_never_hidden_answer_paths(tmp_path):
    corpus = sc.load_corpus(_build_corpus(tmp_path))
    for case in corpus.cases:
        prompt = sc.prompt_text(case)
        assert case.design in prompt
        hidden = [
            path for path, fact in corpus.files.items()
            if fact.case_id == case.id and path != case.design
        ]
        assert all(path not in prompt for path in hidden)


def test_workspace_byte_change_invalidates_manifest(tmp_path):
    root = _build_corpus(tmp_path)
    (root / "workspace/src/case-one/main.py").write_text("def operation():\n    return 'changed'\n")
    with pytest.raises(sc.SelectorCanaryError, match="binding mismatch"):
        sc.load_corpus(root)


def test_workspace_rejects_unmanifested_file_and_symlink(tmp_path):
    root = _build_corpus(tmp_path)
    extra = root / "workspace/src/extra.py"
    extra.write_text("EXTRA = True\n")
    with pytest.raises(sc.SelectorCanaryError, match="workspace file set differs"):
        sc.load_corpus(root)

    extra.unlink()
    link = root / "workspace/src/link.py"
    try:
        link.symlink_to(root / "workspace/src/case-one/main.py")
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(sc.SelectorCanaryError, match="symlink"):
        sc.load_corpus(root)


def test_incomplete_and_duplicate_run_matrix_fail_closed(tmp_path):
    corpus = sc.load_corpus(_build_corpus(tmp_path))
    rows = _complete_rows(corpus)
    runs = corpus.root / "runs/incomplete.jsonl"
    _write_jsonl(runs, rows[:-1])
    with pytest.raises(sc.SelectorCanaryError, match="matrix incomplete"):
        sc.load_runs(corpus, runs)

    _write_jsonl(runs, rows + [rows[0]])
    with pytest.raises(sc.SelectorCanaryError, match="duplicate selector matrix cell"):
        sc.load_runs(corpus, runs)


def test_run_rejects_oracle_content_and_bad_pre_oracle_digest(tmp_path):
    corpus = sc.load_corpus(_build_corpus(tmp_path))
    rows = _complete_rows(corpus)
    rows[0]["oracleResponse"] = "must never enter a run record"
    runs = corpus.root / "runs/oracle.jsonl"
    _write_jsonl(runs, rows)
    with pytest.raises(sc.SelectorCanaryError, match="keys differ"):
        sc.load_runs(corpus, runs)

    rows[0].pop("oracleResponse")
    rows[0]["preOracleSelectionSha256"] = "0" * 64
    _write_jsonl(runs, rows)
    with pytest.raises(sc.SelectorCanaryError, match="pre-Oracle selection digest mismatch"):
        sc.load_runs(corpus, runs)


def test_oracle_selection_drift_is_reported_not_scored_as_selector_output(tmp_path):
    corpus = sc.load_corpus(_build_corpus(tmp_path))
    rows = _complete_rows(corpus)
    rows[0]["finalSelectionSha256"] = "0" * 64
    runs = corpus.root / "runs/drift.jsonl"
    _write_jsonl(runs, rows)

    matrix, runs_sha = sc.load_runs(corpus, runs)
    report = sc.score(corpus, matrix, runs_sha)

    assert report["cells"][0]["oracleSelectionChanged"] is True
    assert report["cells"][0]["criticalPathRecall"] == 1.0


def test_scores_decoy_cross_case_and_unstable_selection(tmp_path):
    corpus = sc.load_corpus(_build_corpus(tmp_path))
    rows = _complete_rows(corpus)
    required = _roles(corpus, "case-one", "required")
    decoy = _roles(corpus, "case-one", "decoy")[0]
    foreign = _roles(corpus, "case-two", "required")[0]
    replacements = {
        1: required,
        2: required + [decoy],
        3: required + [foreign],
    }
    for index, row in enumerate(rows):
        if row["armId"] == "gpt-5.6-sol-low" and row["caseId"] == "case-one":
            rows[index] = _run_row(
                corpus, row["armId"], row["caseId"], row["replicate"],
                replacements[row["replicate"]],
            )
    runs = corpus.root / "runs/varied.jsonl"
    _write_jsonl(runs, rows)
    matrix, runs_sha = sc.load_runs(corpus, runs)
    report = sc.score(corpus, matrix, runs_sha)

    cells = [
        cell for cell in report["cells"]
        if cell["armId"] == "gpt-5.6-sol-low" and cell["caseId"] == "case-one"
    ]
    assert cells[1]["selectedDecoyPaths"] == [decoy]
    assert cells[2]["crossCaseLeakagePaths"] == [foreign]
    stability = next(
        row for row in report["stability"]
        if row["armId"] == "gpt-5.6-sol-low" and row["caseId"] == "case-one"
    )
    assert stability["meanJaccard"] < 1.0


def test_jsonl_requires_final_newline_and_finite_numbers(tmp_path):
    corpus = sc.load_corpus(_build_corpus(tmp_path))
    row = _complete_rows(corpus)[0]
    runs = corpus.root / "runs/bad.jsonl"
    runs.parent.mkdir()
    runs.write_text(json.dumps(row))
    with pytest.raises(sc.SelectorCanaryError, match="end with a newline"):
        sc.load_runs(corpus, runs)

    runs.write_text('{"value": NaN}\n')
    with pytest.raises(sc.SelectorCanaryError, match="non-finite"):
        sc.load_runs(corpus, runs)


def test_score_output_is_write_once_and_outside_hidden_roots(tmp_path):
    corpus = sc.load_corpus(_build_corpus(tmp_path))
    rows = _complete_rows(corpus)
    runs = corpus.root / "runs/complete.jsonl"
    _write_jsonl(runs, rows)
    out = corpus.root / "scores/report.json"

    assert sc.main([
        "score", "--corpus-root", str(corpus.root), "--runs", str(runs), "--out", str(out)
    ]) == sc.EXIT_OK
    first = out.read_bytes()
    assert sc.main([
        "score", "--corpus-root", str(corpus.root), "--runs", str(runs), "--out", str(out)
    ]) == sc.EXIT_OUTPUT_REFUSED
    assert out.read_bytes() == first

    hidden_out = corpus.root / "control/report.json"
    assert sc.main([
        "score", "--corpus-root", str(corpus.root), "--runs", str(runs),
        "--out", str(hidden_out)
    ]) == sc.EXIT_OUTPUT_REFUSED
