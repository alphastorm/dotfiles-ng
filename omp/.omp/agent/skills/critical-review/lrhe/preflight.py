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
  * The OpenCode lanes stay councilEnabled: false until a credential exists AND a
    canary has run. Enabling first means the first live request is also the first
    test of the request path.
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


def check_lanes_held() -> Result:
    """An enabled lane must have earned it, and the record must say how.

    Hardcoding which three families are allowed would just move the claim into
    this file. The qualification record already states what each lane proved --
    a passed provider canary, a schema that validated, a read-only boundary that
    held -- so enabling is checked against that evidence instead.
    """
    qual = SKILL / "qualification.yml"
    if not qual.is_file():
        return Result(UNKNOWN, f"{qual} not readable (private package not linked?)")
    doc = yaml.safe_load(qual.read_text(encoding="utf-8"))
    reviewers = doc.get("reviewers", {}) if isinstance(doc, dict) else {}

    on, off, unearned = [], [], []
    for name, entry in sorted(reviewers.items()):
        if not isinstance(entry, dict):
            continue
        if not entry.get("councilEnabled"):
            off.append(name)
            continue
        on.append(name)
        missing = [
            field for field, want in (("providerCanary", "passed"),
                                      ("readOnlyBoundary", "passed"),
                                      ("schemaValid", True))
            if entry.get(field) != want
        ]
        if missing:
            unearned.append(f"{name} enabled but {missing} not proven")

    if unearned:
        return Result(FAIL, "; ".join(unearned))
    return Result(PASS, f"enabled {on} all canaried, held {off}")


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
    qual = SKILL / "qualification.yml"
    if not qual.is_file():
        return Result(UNKNOWN, f"{qual} not readable (private package not linked?)")
    catalogue = _catalogue()
    if catalogue is None:
        return Result(UNKNOWN, f"{MODELS_DB} not readable; nothing to resolve against")

    doc = yaml.safe_load(qual.read_text(encoding="utf-8"))
    reviewers = doc.get("reviewers", {}) if isinstance(doc, dict) else {}
    problems: list[str] = []
    resolved = 0
    for name, entry in sorted(reviewers.items()):
        if not isinstance(entry, dict) or not entry.get("model"):
            continue
        provider, _, rest = str(entry["model"]).partition("/")
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
    qual = SKILL / "qualification.yml"
    if not qual.is_file():
        return Result(UNKNOWN, f"{qual} not readable (private package not linked?)")
    reviewers = yaml.safe_load(qual.read_text(encoding="utf-8")).get("reviewers", {})

    missing = []
    for name, entry in sorted(reviewers.items()):
        agent = entry.get("agent") if isinstance(entry, dict) else None
        if not agent:
            missing.append(f"{name} names no agent")
            continue
        path = AGENTS / f"{agent}.md"
        if not path.exists():
            # .exists() already follows the link, so this covers both a missing
            # file and the dangling stow symlink that reads as one.
            missing.append(f"{name} -> {agent}.md absent")
    if missing:
        return Result(FAIL, "; ".join(missing))
    return Result(PASS, f"{len(reviewers)} reviewers resolve to an agent definition")


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
    return Result(PASS, f"no {LOCK.name} yet -- it is the last manual step below; {note}")


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
)

# Printed after the gates. Order matters and is the reason this file exists.
MANUAL_STEPS = (
    ("upgrade OMP and restart the session",
     "the reviewer definitions are version-sensitive and the lock must name the "
     "version that actually runs"),
    ("run the three canaries",
     "one cheap request per lane -- structured output, read-only anchor lookup, "
     "empty-evidence abstention -- to prove the request path before the council runs. "
     "The credential itself is no longer a step: the `model selectors` gate above "
     "fails with 'authenticate it first' when a provider has no cached catalogue"),
    ("enable a lane and smoke it",
     "flip councilEnabled only for a lane whose canary passed"),
    ("freeze runs/LOCK.json, then run",
     "freeze_lock.py freeze -- last, and from committed trees. Qualification edits "
     "qualification.yml and the terms snapshots, both of which the lock hashes, so a "
     "lock taken before the steps above drifts before the first measured run"),
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

    print("remaining, in order -- none of these are automatic:")
    for i, (step, why) in enumerate(MANUAL_STEPS, 1):
        print(f"  {i}. {step}\n     {why}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
