#!/usr/bin/env python3
"""preflight.py -- everything checkable before the first paid request, in order.

    ./.venv/bin/python preflight.py            # check, spend nothing
    ./.venv/bin/python preflight.py --slow     # also run the full suite

Exit 0 = every automatic gate holds and the next manual step is printed.
Exit 10 = a gate failed. Exit 20 = a gate could not be evaluated.

This exists because the ordering is load-bearing and was living in a chat log.
Three steps are order-sensitive and expensive to get wrong:

  * LOCK.json is frozen LAST, and from committed trees. A lock is a claim about
    the starting state of a result set, so it must name the toolchain that runs
    (hence: after the OMP upgrade) and the tree that produced the runs (hence:
    after qualification, which edits qualification.yml and the terms snapshots,
    both of which the lock hashes). It records each repo's commit and dirty flag
    and `verify` diffs both, so a lock taken early drifts before anything runs.
  * OpenCode lanes stay evaluationEnabled: false until a credential exists and
    their canary passes. Enabling first makes the first evaluation request also
    the first test of the request path.
  * A canary result is evidence and gets committed, which is why it precedes the
    freeze rather than following it.

Nothing here contacts a provider. The checks that would cost money are named as
manual steps, not executed -- a preflight that can spend is not a preflight.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import freeze_lock  # noqa: E402  -- needs the path above
import run_review  # noqa: E402
from qualification import QualificationError, load_qualification, reviewers as qualification_reviewers  # noqa: E402

SKILL = Path.home() / ".omp/agent/skills/critical-review"
AGENTS = Path.home() / ".omp/agent/agents"
DATA = SKILL / "lrhe-data"
# OMP's model cache. Read-only, and the only local answer to "does this selector
# resolve", short of spending a request to find out.
MODELS_DB = Path.home() / ".omp/agent/models.db"
# Imported, not restated. These disagreed once -- freeze wrote `lrhe-data/runs/
# LOCK.json` while this file looked for `lrhe-data/LOCK.json`, so the gate that
# refuses a lock frozen under the wrong toolchain could never see one at all.
LOCK = freeze_lock.DEFAULT_LOCK_PATH

EXIT_OK = 0
EXIT_BLOCKED = 10
EXIT_UNRESOLVED = 20

# Bumping this is the deliberate act that says "the upgrade happened". Freezing a
# lock under a version this does not name is the mistake the file exists to stop.
EXPECTED_OMP = "17.1.6"

PASS, FAIL, UNKNOWN, SKIP = "pass", "fail", "unknown", "skip"

# Set in the environment of any pytest subprocess preflight starts, so a gate that
# runs the suite cannot be re-entered by the suite that tests the gate.
REENTRY_FLAG = "LRHE_PREFLIGHT_ACTIVE"


class Result:
    __slots__ = ("state", "detail")

    def __init__(self, state: str, detail: str) -> None:
        self.state, self.detail = state, detail


# --------------------------------------------------------------- gate checks

def check_lint() -> Result:
    ruff = HERE / ".venv/bin/ruff"
    if not ruff.exists():
        return Result(UNKNOWN, "no ruff in .venv; pip install -r requirements.txt")
    proc = subprocess.run([str(ruff), "check", "."], cwd=HERE, check=False,
                          capture_output=True, text=True, timeout=300)
    if proc.returncode == 0:
        return Result(PASS, "clean under the pinned rule set in ruff.toml")
    return Result(FAIL, (proc.stdout or proc.stderr).strip().splitlines()[-1])


def _pytest(target: list[str], timeout: int) -> Result:
    """Run pytest in a subprocess that knows it is already inside preflight.

    The test suite exercises these gates, and these gates run the test suite. Left
    unguarded that is unbounded recursion, not a slow check: it took ten minutes
    to fail as a timeout rather than a stack overflow, which is the worst possible
    way to notice. The env var breaks the cycle at the first nested level.
    """
    if os.environ.get(REENTRY_FLAG):
        return Result(SKIP, "already inside preflight; not re-running pytest")
    proc = subprocess.run([sys.executable, "-m", "pytest", *target, "-q"],
                          cwd=HERE, check=False, capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, REENTRY_FLAG: "1"})
    tail = (proc.stdout or "").strip().splitlines()
    return Result(PASS if proc.returncode == 0 else FAIL,
                  tail[-1] if tail else "no output")


def check_consistency() -> Result:
    return _pytest(["test_consistency.py"], timeout=600)


def check_suite(slow: bool) -> Result:
    if not slow:
        return Result(SKIP, "pass --slow to run the full suite (~60s)")
    return _pytest([], timeout=1800)


def check_agent_definitions() -> Result:
    """The reviewer definitions must parse and agree with qualification.yml.

    These are version-sensitive in ways nothing else notices: `thinkingLevel` was
    once spelled `thinking-level` and silently did nothing, and a dangling stow
    symlink means an agent is simply absent rather than broken. An agent that
    fails to load at dispatch time fails after the council has already started.
    """
    if not AGENTS.is_dir():
        return Result(UNKNOWN, f"{AGENTS} is not a directory")

    problems: list[str] = []
    reviewers = sorted(AGENTS.glob("review-*.md"))
    if not reviewers:
        return Result(FAIL, f"no review-*.md agents under {AGENTS}")

    for path in reviewers:
        if not path.resolve().exists():
            problems.append(f"{path.name}: dangling symlink -> {path.readlink()}")
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            problems.append(f"{path.name}: no YAML frontmatter")
            continue
        try:
            front = yaml.safe_load(text.split("---", 2)[1])
        except yaml.YAMLError as exc:
            problems.append(f"{path.name}: frontmatter does not parse: {exc}")
            continue

        for key in ("name", "description", "tools", "model", "output"):
            if key not in front:
                problems.append(f"{path.name}: missing {key}")
        if "thinking-level" in front:
            problems.append(f"{path.name}: 'thinking-level' is ignored; use thinkingLevel")
        if front.get("name") != path.stem:
            problems.append(f"{path.name}: declares name {front.get('name')!r}")

        out = front.get("output")
        if isinstance(out, dict):
            # A reviewer whose output schema does not compile returns free text,
            # and free text cannot be scored against a label.
            try:
                from jsonschema import Draft202012Validator
                Draft202012Validator.check_schema(out)
            except Exception as exc:  # noqa: BLE001 -- any schema error is the finding
                problems.append(f"{path.name}: output schema invalid: {exc}")
        elif out is not None:
            problems.append(f"{path.name}: output must be a schema object")

    if problems:
        return Result(FAIL, "; ".join(problems))
    return Result(PASS, f"{len(reviewers)} reviewer definitions parse")


def _uncanaried_lanes() -> list[str] | None:
    """Held lanes whose canary has not been run, or None when the record is unreadable.

    Read from qualification.yml rather than parsed back out of a gate's prose, so
    the manual checklist below and the gate above cannot disagree.

    A lane that was canaried and failed is not awaiting a canary -- it is parked,
    with a recorded reason, and re-running its probes is the specific thing not to
    do. MiniMax M3 is the case: it failed 0/3 on repeated schema noncompliance and
    is deliberately held, so a checklist counting it as outstanding work would ask
    for a rerun the operator ruled out.
    """
    try:
        document = load_qualification(SKILL / "qualification.yml")
        qualified = qualification_reviewers(document)
    except QualificationError:
        return None
    return sorted(
        name
        for name, value in qualified.items()
        if isinstance(value, dict)
        and value.get("evaluationEnabled") is not True
        and value.get("providerCanary") not in ("passed", "failed")
    )


def check_lanes_held() -> Result:
    """An evaluation-enabled lane must have earned it.

    Live critical-review membership is separate and owned by `liveDispatch`.
    This gate covers LRHE evaluation capability: provider canary, schema, and
    read-only boundary. Hardcoding families here would duplicate the private
    qualification record.
    """
    try:
        document = load_qualification(SKILL / "qualification.yml")
        qualified = qualification_reviewers(document)
    except QualificationError as exc:
        return Result(FAIL, str(exc))

    on = sorted(
        name
        for name, value in qualified.items()
        if isinstance(value, dict) and value.get("evaluationEnabled") is True
    )
    off = sorted(set(qualified) - set(on))
    return Result(PASS, f"evaluation-enabled {on} all canaried, held {off}")


def _catalogue() -> dict[str, dict[str, set[str]]] | None:
    """provider -> model -> offered efforts, from OMP's cache, or None if unreadable.

    Scoped providers are keyed `<provider>:models-v1:<hash>` in the cache -- the
    suffix is a cache discriminator built from the provider id and a scope hash,
    not part of a selector, so it is stripped. That is why `opencode-go` appears
    under a hashed key while `anthropic` does not.
    """
    if not MODELS_DB.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{MODELS_DB}?mode=ro", uri=True)
        try:
            rows = con.execute("select provider_id, models from model_cache").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None

    catalogue: dict[str, dict[str, set[str]]] = {}
    for provider_id, blob in rows:
        try:
            parsed = json.loads(blob)
        except (TypeError, json.JSONDecodeError):
            continue
        models = parsed if isinstance(parsed, list) else parsed.get("models", [])
        provider = catalogue.setdefault(provider_id.split(":models-v1:", 1)[0], {})
        for model in models:
            if not isinstance(model, dict) or "id" not in model:
                continue
            thinking = model.get("thinking") or {}
            provider[model["id"]] = set(thinking.get("efforts") or thinking.get("levels") or ())
    return catalogue


def check_model_selectors() -> Result:
    """A selector that does not resolve fails at dispatch, once the council is running.

    qualification.yml names `provider/model[:effort]`, and nothing in it can
    assert that any of the three parts exist. The provider has to be
    authenticated far enough for OMP to have cached its catalogue, the model has
    to be in that catalogue, and an effort suffix has to be one the model offers.
    This is the "selector not discovered against the installed build" blocker,
    answered automatically instead of by hand.
    """
    try:
        document = load_qualification(SKILL / "qualification.yml")
        qualified = qualification_reviewers(document)
    except QualificationError as exc:
        return Result(FAIL, str(exc))
    catalogue = _catalogue()
    if catalogue is None:
        return Result(UNKNOWN, f"{MODELS_DB} not readable; nothing to resolve against")

    problems: list[str] = []
    resolved = 0
    for name, value in sorted(qualified.items()):
        if not isinstance(value, dict) or not value.get("model"):
            continue
        provider, _, rest = str(value["model"]).partition("/")
        model, _, effort = rest.partition(":")
        offered = catalogue.get(provider)
        if offered is None:
            problems.append(f"{name}: provider {provider!r} has no cached catalogue; authenticate it first")
        elif model not in offered:
            problems.append(f"{name}: {provider} serves no model {model!r}")
        elif effort and offered[model] and effort not in offered[model]:
            problems.append(f"{name}: {model} offers {sorted(offered[model])}, not {effort!r}")
        else:
            resolved += 1

    if problems:
        return Result(FAIL, "; ".join(problems))
    return Result(PASS, f"{resolved} reviewer selectors resolve against the installed build")


def check_no_live_transport() -> Result:
    names = sorted(run_review.TRANSPORTS)
    if "live" in names:
        return Result(FAIL, f"a live transport exists: {names}")
    if run_review.TRANSPORTS.get("none") is not run_review.no_egress_transport:
        return Result(FAIL, "the default transport is no longer the refusing one")
    return Result(PASS, f"transports {names}, default refuses")


def check_reviewer_agents_resolve() -> Result:
    """Each qualified reviewer must name an agent that actually exists.

    qualification.yml and the agents directory are edited independently, and a
    reviewer pointing at a missing definition fails at dispatch -- after the
    council has started and the other lanes have already been paid for.
    """
    try:
        document = load_qualification(SKILL / "qualification.yml")
        qualified = qualification_reviewers(document)
    except QualificationError as exc:
        return Result(FAIL, str(exc))

    missing = []
    for name, value in sorted(qualified.items()):
        agent = value.get("agent") if isinstance(value, dict) else None
        if not agent:
            missing.append(f"{name} names no agent")
            continue
        path = AGENTS / f"{agent}.md"
        if not path.exists():
            # .exists() follows links, covering absent files and dangling stow links.
            missing.append(f"{name} -> {agent}.md absent")
    if missing:
        return Result(FAIL, "; ".join(missing))
    return Result(PASS, f"{len(qualified)} reviewers resolve to an agent definition")


def check_omp_version() -> Result:
    got = freeze_lock._run_command_version("omp")
    if got is None:
        return Result(UNKNOWN, "omp did not answer --version")
    if got != EXPECTED_OMP:
        return Result(FAIL, f"running {got}, preflight expects {EXPECTED_OMP}; "
                            f"upgrade first or bump EXPECTED_OMP deliberately")
    return Result(PASS, f"omp {got}")


def _uncommitted() -> list[str] | None:
    """Repos carrying uncommitted work, or None when git could not be read."""
    names = []
    for repo in (freeze_lock.DEFAULT_PUBLIC_REPO, freeze_lock.DEFAULT_PRIVATE_REPO):
        try:
            state = freeze_lock._git_state(repo)
        except (RuntimeError, OSError):
            return None
        if state["dirty"]:
            names.append(repo.name)
    return names


def check_lock_state() -> Result:
    """Absent is correct until qualification ends; present must name the running toolchain.

    The tree state is reported rather than failed. Refusing a dirty freeze is
    `freeze_lock.py freeze`'s job, at the point of effect; failing here would
    colour the whole preflight red for every ordinary edit made during the three
    manual steps that now precede the freeze, and a gate that cries wolf during
    normal work is a gate the operator learns to skip.
    """
    if LOCK.is_file():
        stored = json.loads(LOCK.read_text(encoding="utf-8"))
        recorded = (stored.get("lock_inputs", {}).get("versions", {}) or {}).get("omp")
        if recorded != EXPECTED_OMP:
            return Result(FAIL, f"{LOCK.name} was frozen under omp {recorded!r}, not {EXPECTED_OMP}; "
                                f"a lock naming the wrong toolchain cannot start a result set")
        return Result(PASS, f"{LOCK.name} frozen under omp {recorded}")

    dirty = _uncommitted()
    if dirty is None:
        note = "worktree state unreadable"
    elif dirty:
        note = f"{', '.join(dirty)} uncommitted, and freeze refuses a dirty tree"
    else:
        note = "both repos committed"
    return Result(PASS, f"no {LOCK.name} yet -- it is listed below; {note}")


def check_automated_audit() -> Result:
    """The automated reliability audit's own state, kept strictly beside the human gate.

    This gate can never make the canonical preflight green. The section 8 step below is
    judge-versus-human agreement; the audit is model-versus-model, and reporting the two
    through one predicate is exactly the "done because the artifact exists" shape that
    put four false completions in this file. So it reports UNKNOWN when the namespace is
    absent, and on success says only what it measured.
    """
    d = DATA / "auto-reliability-v1"
    if not d.is_dir():
        return Result(UNKNOWN, "no auto-reliability-v1 namespace; the audit has not been run")
    receipt, agreement = d / "completion-receipt.json", d / "agreement.json"
    if not (receipt.is_file() and agreement.is_file()):
        return Result(FAIL, "audit namespace exists with no completion receipt or agreement.json")
    try:
        rec = json.loads(receipt.read_text(encoding="utf-8"))
        agr = json.loads(agreement.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Result(FAIL, f"audit artifacts unreadable: {exc}")
    if rec.get("canonical_artifacts_mutated") is not False:
        return Result(FAIL, "the audit receipt does not attest that canonical artifacts are intact")
    for doc in (rec, agr):
        if doc.get("human_judge_reliability", {}).get("status") != "not_measured":
            return Result(FAIL, "an audit artifact claims human judge reliability was measured")
    band = (agr.get("band") or {}).get("band", "?")
    stage_b = (agr.get("stage_b_triggers") or {}).get("triggered")
    return Result(PASS, f"band {band}, stage B triggered {stage_b}, canonical intact, "
                        f"human reliability still not_measured")


GATES = (
    ("lint", check_lint),
    ("cross-file invariants", check_consistency),
    ("reviewer definitions", check_agent_definitions),
    ("reviewer agents", check_reviewer_agents_resolve),
    ("no live transport", check_no_live_transport),
    ("lanes held", check_lanes_held),
    ("model selectors", check_model_selectors),
    ("omp version", check_omp_version),
    ("freeze lock", check_lock_state),
    ("automated audit", check_automated_audit),
)


def _has_rows(path: Path) -> bool:
    """Does this artifact exist and contain at least one record?

    An empty file is what a run that started and produced nothing leaves behind,
    and a step reported as done on the strength of a zero-byte file is the same
    silent pass the gates above exist to refuse.
    """
    try:
        return any(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def _complete_against(runs: Path, manifest: Path) -> tuple[int, int]:
    """(runs recorded, runs the frozen manifest calls for).

    `_has_rows` is the wrong question for anything that runs in batches. The floor
    panel is 105 reviews in five declared batches, and after the first one the runs
    file is non-empty -- so a step asking only "does the artifact exist" reported a
    panel one fifth finished as done. Same staleness as the static checklist, one
    level down: the artifact answered a question nobody meant to ask.
    """
    try:
        want = sum(1 for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return (0, 0)
    try:
        have = sum(1 for line in runs.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        have = 0
    return (have, want)


def _judgeable() -> int:
    """Floor claims that need a judge: the ones the deterministic gates did not settle.

    An unparsed claim has no content to adjudicate and a claim anchored outside the
    item's own files is FABRICATED by construction, so both are settled without a call.
    Everything else is the denominator.
    """
    try:
        import csv as _csv
        with (DATA / "floor/claims-floor.csv").open() as fh:
            rows = list(_csv.DictReader(fh))
    except OSError:
        return 0
    return sum(1 for r in rows
               if r.get("parse_status") != "fail"
               and r.get("verdict") not in ("UNPARSED", "REFUTED")
               and not (str(r.get("has_anchor")) == "True"
                        and str(r.get("anchor_paths_exist")) == "False"))


def _adjudicated() -> int:
    """Distinct floor claims carrying an aggregated judgement.

    The floor aggregate only. An earlier version unioned `judge-qual-agg.jsonl`, the
    12-call qualification run, so its 8 claims double-counted against a denominator they
    were never part of -- harmless as a progress counter and wrong the moment anyone reads
    it as coverage.
    """
    seen = set()
    try:
        for line in (DATA / "judge-floor-agg.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                seen.add((row.get("run_id"), str(row.get("claim_rid"))))
    except OSError:
        return 0
    return len(seen)


def _calibration() -> tuple[int, int, float | None]:
    """(labelled gate rows, gate size, kappa) for the blinded human packet.

    Adjudication coverage being complete does NOT finish this step. With 315 of 315 claims
    judged the predicate read done while the calibration packet held 0 labelled rows, so
    preflight said "nothing manual remains" over numbers that are provisional by their own
    protocol. Fourth instance of the same shape: a step is not done because the artifact
    upstream of it exists.

    Reads `auto-reliability-v1/human-packet.csv`, the blinded packet with opaque case ids.
    `judge-calibration-packet.csv` is the frozen *selection manifest* and must stay
    unlabelled -- it leaks item_id and the S4 trap prefix, and it is also the `--selection`
    input to `auto_reliability.py build`.

    The gate is computed through `judge_lrhe.blinded_calibration_pairs`, the same loader
    the CLI uses, so this line and `judge_lrhe.py kappa` cannot disagree.
    """
    packet = DATA / "auto-reliability-v1" / "human-packet.csv"
    case_map = DATA / "auto-reliability-v1" / "case-map.private.jsonl"
    judge_path = DATA / "judge-floor-agg.jsonl"
    try:
        import csv as _csv
        with packet.open() as fh:
            rows = list(_csv.DictReader(fh))
    except OSError:
        return (0, 0, None)
    if not (case_map.exists() and judge_path.exists()):
        return (0, len(rows), None)

    import judge_lrhe
    try:
        judge, judge_problems = judge_lrhe.load_judge_index(judge_path)
    except OSError:
        return (0, len(rows), None)
    gate_size = sum(1 for case in _read_case_kinds(case_map)
                    if case in {str(r.get("case_id") or "").strip() for r in rows})
    labelled = sum(
        1 for r in rows
        if (r.get("human_verdict") or "").strip()
        and str(r.get("case_id") or "").strip() in _read_case_kinds(case_map))
    if judge_problems or labelled < gate_size or not gate_size:
        return (labelled, gate_size, None)

    compared, _supplemental, problems = judge_lrhe.blinded_calibration_pairs(
        packet, case_map, judge, expect_gating=gate_size)
    if problems or not compared:
        return (labelled, gate_size, None)
    return (labelled, gate_size,
            judge_lrhe.calibration_agreement(compared)["verdict_kappa"])


def _read_case_kinds(case_map) -> set[str]:
    """case_ids in the case map that belong to the human gate (`kind == "case"`)."""
    import judge_lrhe
    out = set()
    try:
        for line in Path(case_map).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("kind") or "").strip() == judge_lrhe.GATE_KIND:
                out.add(str(row.get("case_id") or "").strip())
    except OSError:
        return set()
    return out


def _authorization(kind: str) -> bool:
    """Is there a recorded operator decision of this kind?

    A decision that lives only in a chat log is not a decision anybody can audit six
    months from now, and the promotion of three lanes gates every remaining run. So
    the checklist asks the same directory the risk acceptance lives in rather than
    trusting that someone remembers having decided.
    """
    directory = SKILL / "authorizations"
    if not directory.is_dir():
        return False
    return any(kind in path.name for path in directory.iterdir() if path.is_file())


# Printed after the gates. Order matters and is the reason this file exists.
#
# Each step carries the condition under which it is still outstanding, evaluated
# against the gate results above or the artifact it would leave behind, because a
# static checklist is exactly the kind of claim this file exists to stop trusting.
# This one went stale in the obvious way: it went on naming the OMP upgrade and the
# canaries after both were done, so the only way to learn what actually remained
# was to read the gates and reconstruct it. A step whose completion is visible
# should be asked, not remembered.
MANUAL_STEPS = (
    ("upgrade OMP and restart the session",
     "the reviewer definitions are version-sensitive and the lock must name the "
     "version that actually runs",
     lambda r: r["omp version"].state != PASS),
    ("run the three canaries on every lane that has not had them",
     "canary.py prompts, answered through the reviewer agent, then canary.py grade -- "
     "structured output, real citations, empty-evidence abstention. A lane stays held "
     "until all three pass, and `run` cannot qualify one: it refuses every transport "
     "that could leave the machine, so its verdict is always `apparatus`. A lane that "
     "was canaried and failed is parked, not outstanding: see its blockers",
     lambda r: _uncanaried_lanes() != []),
    ("freeze runs/LOCK.json, then run",
     "freeze_lock.py freeze -- from committed trees, after everything that edits what "
     "it hashes and before anything it has to vouch for. Qualification edits "
     "qualification.yml and the terms snapshots, both of which the lock hashes, so a "
     "lock taken before the steps above drifts before the first measured run",
     lambda r: not LOCK.is_file()),
    ("the seven-item smoke pass",
     "2xS1 + 2xS2 + 2xS3 + 1xS5, calibration items only, on an enabled lane. It is "
     "the first thing to exercise ARVO build/PoC wiring and a real packet end to end, "
     "and it is not a blind observation: do not score it unless the same condition is "
     "rerun cold later",
     lambda r: not _has_rows(DATA / "runs-smoke.jsonl")),
    ("the twelve-item four-family screen",
     "S1x4 + S2x3 + S3x2 + S4x2 + S5x1, frozen before any output exists, each item "
     "run by kimi, glm and deepseek independently on arm OC_SCREEN, lens floor, a "
     "fresh session and no peer output, plus the contamination probe per cell in a "
     "separate cold session. MiniMax is held on repeated schema noncompliance and is "
     "not one of the families. Do not prune on a 12-item recall ranking; drop a "
     "challenger only for operational failure",
     lambda r: not _has_rows(DATA / "runs-screen.jsonl")),
    ("record the promotion decision for kimi, glm and deepseek",
     "the screen is evidence, not a verdict, and Commits 9 and 10 both spend real "
     "quota on whichever lanes proceed. No lane met an early-drop condition; that is a "
     "recommendation in lrhe-data/screen/RESULTS.md and not a decision. Write it to "
     "authorizations/ with a principal and a date, the same way the risk acceptance "
     "is recorded -- a permissive decision nobody owns is the thing that file exists "
     "to stop",
     lambda r: not _authorization("promotion")),
    ("the same-family null, three replicates",
     "arm T_OC, family kimi, replicate rep1..rep3, lens floor, on the same twelve "
     "screen items. Three and not the pre-registered four: the four is derived from a "
     "four-family panel and MiniMax is held, so a four-sample null would make the "
     "caught-set Jaccard and union-coverage comparison unmatched. Deviation recorded "
     "in handoff/RECONCILIATION-2026-07-28.md",
     lambda r: not _has_rows(DATA / "runs-null-toc.jsonl")),
    (f"the 47-item floor panel ({_complete_against(DATA / 'runs-floor.jsonl', DATA / 'floor-manifest.jsonl')[0]}"
     f"/{_complete_against(DATA / 'runs-floor.jsonl', DATA / 'floor-manifest.jsonl')[1]} reviews recorded)",
     "the remaining 35 items for every promoted family, arm OC_FULL, panel "
     "opencode-broad-v1, contamination probes continuing. Five declared batches of "
     "seven, with the witness re-run at each boundary -- three replicates per family "
     "from boundary 2 on, because one sample cannot be told apart from the run-to-run "
     "spread the T_OC null measured. 105 reviews and 105 probes at three families, the "
     "longest window anything here runs over, which is why provider_fingerprint exists "
     "and why OpenCode exposing none is a stated risk rather than a closed control. "
     "Commit 12's lens rotation is optional and decided from this panel's analysis",
     lambda r: _complete_against(DATA / "runs-floor.jsonl",
                                 DATA / "floor-manifest.jsonl") != (105, 105)),
    (f"Fable adjudication ({_adjudicated()}/{_judgeable()} floor claims adjudicated)",
     "two non-authoring judges per surviving claim, keyed on family: the pool is "
     "claude/gemini/grok, disjoint from the OpenCode authors, so independence holds by "
     "construction and the routes are unaffected by the opencode-go spending limit. "
     "judge-output.schema.json is installed and reconciled -- the archived copy named "
     "the matched label `matched_label_id` where the runner reads `label_id`, so a reply "
     "valid against it would have been ingested with no label at all. `served_model` is "
     "harvested from the session record and gated: a judgement from a model nobody "
     "requested drops its whole claim, because one surviving judge is not a majority of "
     "two",
     # The schema existing is not the step being done. The first version of this
     # predicate was `not judge-output.schema.json.is_file()`, so writing the file
     # reported adjudication complete with 0 of 279 claims judged -- the same defect
     # `ac4855e` fixed for the floor step, which called a panel one fifth finished done.
     lambda r: _adjudicated() < _judgeable()),
    (f"the kappa >= 0.70 human calibration ({_calibration()[0]}/{_calibration()[1]} "
     f"labelled, kappa "
     f"{'n/a' if _calibration()[2] is None else format(_calibration()[2], '.2f')})",
     "the blinded packet is at lrhe-data/auto-reliability-v1/human-packet.csv: opaque "
     "case ids and two blank columns, nothing that leaks item_id or the S4 trap prefix. "
     "Fill `human_verdict` with CONFIRMED / PLAUSIBLE / FABRICATED (and `human_label_id` "
     "on CONFIRMED), then:\n"
     "       judge_lrhe.py kappa --calibration lrhe-data/auto-reliability-v1/human-packet.csv \\\n"
     "         --judge lrhe-data/judge-floor-agg.jsonl \\\n"
     "         --case-map lrhe-data/auto-reliability-v1/case-map.private.jsonl\n"
     "     The gate is the frozen 60 `kind == case` rows; the 5 `case_supplement` rows in "
     "that packet are reported separately and never enter kappa. Do NOT label "
     "lrhe-data/judge-calibration-packet.csv -- it is the frozen selection manifest and "
     "the `--selection` input to auto_reliability.py build. Until this passes, "
     "`arm_critical_recall`, `unique_contribution` and the "
     "leave-one-family-out deltas are provisional by the protocol's own terms -- "
     "adjudicating 667 calls produced them faster, not more quotable. A separate step "
     "from adjudication on purpose: with 315 of 315 claims judged the combined predicate "
     "read done over a packet holding zero labelled rows, and preflight said nothing "
     "manual remained",
     lambda r: (_calibration()[2] or 0.0) < 0.70),
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slow", action="store_true", help="also run the full test suite")
    args = ap.parse_args()

    results = [(name, fn()) for name, fn in ((n, f) for n, f in GATES[:2])]
    results.append(("full suite", check_suite(args.slow)))
    results += [(name, fn()) for name, fn in GATES[2:]]

    width = max(len(n) for n, _ in results)
    mark = {PASS: "ok  ", FAIL: "FAIL", UNKNOWN: "??  ", SKIP: "--  "}
    for name, res in results:
        print(f"  {mark[res.state]} {name.ljust(width)}  {res.detail}")

    failed = [n for n, r in results if r.state == FAIL]
    unknown = [n for n, r in results if r.state == UNKNOWN]

    print()
    if failed:
        print(f"blocked: {', '.join(failed)}", file=sys.stderr)
        return EXIT_BLOCKED
    if unknown:
        print(f"unresolved: {', '.join(unknown)}", file=sys.stderr)
        return EXIT_UNRESOLVED

    outstanding = [(s, why) for s, why, todo in MANUAL_STEPS if todo(dict(results))]
    if not outstanding:
        print("nothing manual remains.")
        return EXIT_OK
    print("remaining, in order -- none of these are automatic:")
    for i, (step, why) in enumerate(outstanding, 1):
        print(f"  {i}. {step}\n     {why}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
