#!/usr/bin/env python3
"""Invariants that span files, which is why nothing else catches them.

    ./.venv/bin/pytest test_consistency.py -q

Every check here is about two files agreeing. No single module owns any of these
relationships, so no single module's tests can defend them, and the failure mode
is always the same: both files look fine on their own.

The first test is not hypothetical. All three enabled reviewers routed through
providers with no policy entry at all -- qualified, in use, and ungoverned --
and nothing surfaced it until a runner tried to assemble a request. This file
exists so the next one surfaces on push instead.

Deliberately public-only: nothing here reads the private corpus, so it runs
unchanged on a CI runner that has no access to it.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import canary  # noqa: E402  -- needs the path above
import freeze_lock  # noqa: E402  -- needs the path above
import preflight  # noqa: E402
import run_review  # noqa: E402  -- needs the path above
import snapshot_terms  # noqa: E402

PANELS = yaml.safe_load((HERE / "panels.yaml").read_text())
POLICIES = yaml.safe_load((HERE / "provider-policies.yaml").read_text())
SCHEMAS = sorted(HERE.glob("*.schema.json"))


def _routes_in_panels() -> set[str]:
    return {f["providerRoute"] for e in PANELS["experiments"] for f in e["families"]}


# ------------------------------------------------------- panels vs policies

def test_every_panel_route_has_a_policy():
    """A lane you can schedule but cannot get a rights decision for is a dead end.

    This is the bug that shipped: panels.yaml declared anthropic-subscription,
    google-antigravity and xai-oauth, provider-policies.yaml knew about neither,
    and the three lanes those routes serve were all councilEnabled: true.
    """
    have = {p["providerRoute"] for p in POLICIES["policies"]}
    missing = sorted(_routes_in_panels() - have)
    assert not missing, (
        f"panels.yaml schedules {missing} but provider-policies.yaml has no policy "
        f"for them; run_review.py will refuse every request on those routes")


def test_every_policy_names_a_snapshot_that_exists():
    """A termsSnapshotId pointing at nothing is a citation to a missing document.

    The record would still validate and still look like evidence.
    """
    known = set(snapshot_terms.SNAPSHOT_COMPONENTS)
    dangling = sorted(p["termsSnapshotId"] for p in POLICIES["policies"]
                      if p["termsSnapshotId"] not in known)
    assert not dangling, f"termsSnapshotId values with no snapshot definition: {dangling}"


def test_no_policy_still_carries_a_placeholder_snapshot():
    """UNSNAPSHOTTED is honest as a marker and unacceptable as a final state."""
    placeholders = sorted(p["policyId"] for p in POLICIES["policies"]
                          if "UNSNAPSHOT" in p["termsSnapshotId"].upper())
    assert not placeholders, (
        f"{placeholders} cite a placeholder snapshot id; fetch the terms with "
        f"snapshot_terms.py and cite the real id")


def test_snapshot_components_all_have_a_source_url():
    """A snapshot listing a component the source table cannot fetch never completes."""
    orphans = sorted(
        f"{sid}/{c}" for sid, comps in snapshot_terms.SNAPSHOT_COMPONENTS.items()
        for c in comps if c not in snapshot_terms.TERMS_SOURCES)
    assert not orphans, f"snapshot components with no URL: {orphans}"


def test_allowlist_keys_are_declared_and_distinct_per_vendor():
    """dataAllowlistKey is how a route matches an item's licence grant.

    Two routes sharing a key is legitimate -- the Claude CLI and OMP's Anthropic
    provider are the same vendor -- but an absent key silently denies everything,
    because no item's allowlist can contain nothing.
    """
    for policy in POLICIES["policies"]:
        key = policy.get("dataAllowlistKey")
        assert key, f"{policy['policyId']} has no dataAllowlistKey"
        assert key == key.lower(), f"{policy['policyId']} key {key!r} is not lowercase"


def test_risk_accepted_policies_name_a_principal_and_a_record():
    """A permissive policy with nobody's name on it is an assertion nobody owns."""
    for policy in POLICIES["policies"]:
        if policy.get("termsClearanceStatus") != "operator_risk_accepted_pending_written_clarification":
            continue
        auth = policy.get("operatorAuthorization")
        assert auth, f"{policy['policyId']} claims risk acceptance with no operatorAuthorization"
        assert auth.get("principal"), f"{policy['policyId']} names no principal"
        assert auth.get("effectiveDate"), f"{policy['policyId']} has no effective date"
        assert len(auth.get("recordSha256", "")) == 64, (
            f"{policy['policyId']} does not hash the authorization it rests on")


def test_no_policy_permits_training_a_competing_model():
    """The one prohibition no registry edit may grant."""
    offenders = sorted(p["policyId"] for p in POLICIES["policies"]
                       if p.get("modelTrainingAllowed") is not False)
    assert not offenders, f"{offenders} set modelTrainingAllowed true"


# ------------------------------------------------------------------ schemas

@pytest.mark.parametrize("path", SCHEMAS, ids=lambda p: p.name)
def test_schema_is_valid_draft_2020_12(path: Path):
    Draft202012Validator.check_schema(json.loads(path.read_text()))


def test_run_schema_reference_to_data_rights_resolves():
    """A $ref that cannot resolve throws at validation time, not at edit time.

    The run record and the egress guard must validate the same `data_rights`
    definition; two copies drift the first time one is edited.
    """
    docs = [json.loads((HERE / n).read_text())
            for n in ("run.schema.json", "data-rights.schema.json")]
    registry = Registry().with_resources(
        [(d["$id"], Resource.from_contents(d)) for d in docs])
    validator = Draft202012Validator(docs[0], registry=registry)
    # Exercising the ref is the point: an unresolvable one raises here.
    list(validator.iter_errors({"schema_version": 2, "data_rights": {}}))


# ----------------------------------------------------------- panels internals

def test_every_experiment_declares_at_least_one_lens_and_family():
    for exp in PANELS["experiments"]:
        assert exp["families"], f"{exp['experimentId']} has no families"
        assert exp["lenses"], f"{exp['experimentId']} has no lenses"


def test_null_family_is_actually_in_its_panel():
    """Arm T repeats one family. Naming one outside the panel yields an empty null,
    and the diversity contrast then reports NOT MEASURABLE for a reason nobody
    would look for in panels.yaml."""
    for exp in PANELS["experiments"]:
        null = exp.get("nullFamily")
        if not null:
            continue
        families = {f["family"] for f in exp["families"]}
        assert null in families, (
            f"{exp['experimentId']} nulls on {null!r}, which is not in its panel {sorted(families)}")


def test_requirements_are_all_pinned():
    """An unpinned dependency makes the analysis silently version-dependent."""
    unpinned = [
        line.strip() for line in (HERE / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#") and "==" not in line
    ]
    assert not unpinned, f"unpinned requirements: {unpinned}"


# ---------------------------------------------------------------- preflight

def test_every_preflight_gate_reports_instead_of_raising(monkeypatch):
    """A gate that crashes tells you nothing about the thing it guards.

    preflight runs where the private package may be absent -- on CI, on a fresh
    clone, before stow has linked anything. Every gate must degrade to a stated
    unknown, because "could not check" and "checked, fine" are different answers
    and only one of them is safe to act on.
    """
    monkeypatch.setattr(preflight, "SKILL", Path("/nonexistent/skill"))
    monkeypatch.setattr(preflight, "AGENTS", Path("/nonexistent/agents"))
    monkeypatch.setattr(preflight, "DATA", Path("/nonexistent/data"))
    monkeypatch.setattr(preflight, "LOCK", Path("/nonexistent/data/runs/LOCK.json"))

    for name, gate in preflight.GATES:
        result = gate()
        assert result.state in (preflight.PASS, preflight.FAIL,
                                preflight.UNKNOWN, preflight.SKIP), f"{name}: {result.state}"
        assert result.detail, f"{name} reported {result.state} with no detail"


def test_preflight_will_not_pass_a_lock_frozen_under_the_wrong_toolchain(tmp_path, monkeypatch):
    """The lock must name the version that actually runs.

    Freezing before the upgrade records a toolchain that produced nothing, and the
    lock's whole job is to be believed later. This is the one ordering mistake
    that cannot be corrected after the fact without discarding the result set.
    """
    lock = tmp_path / "runs/LOCK.json"
    lock.parent.mkdir(parents=True)
    monkeypatch.setattr(preflight, "LOCK", lock)
    assert preflight.check_lock_state().state == preflight.PASS, "absent is correct pre-upgrade"

    lock.write_text(json.dumps(
        {"lock_inputs": {"versions": {"omp": "0.0.0-stale"}}}), encoding="utf-8")
    stale = preflight.check_lock_state()
    assert stale.state == preflight.FAIL
    assert "0.0.0-stale" in stale.detail

    lock.write_text(json.dumps(
        {"lock_inputs": {"versions": {"omp": preflight.EXPECTED_OMP}}}), encoding="utf-8")
    assert preflight.check_lock_state().state == preflight.PASS


def test_preflight_inspects_the_lock_that_freeze_actually_writes(monkeypatch):
    """Two files naming the same artifact by hand is how a gate goes blind.

    freeze wrote `lrhe-data/runs/LOCK.json`; preflight looked for
    `lrhe-data/LOCK.json`. Both files were internally consistent, every test
    passed, and the gate that refuses a lock frozen under the wrong toolchain
    would have reported "no lock yet, which is correct" forever -- including
    after the lock existed. Asserting the constants match would be circular, so
    this drives the write path the command actually takes.
    """
    written: list[Path] = []
    clean = {"public_repo": {"dirty": False}, "private_repo": {"dirty": False}}
    monkeypatch.setattr(freeze_lock, "_build_record", lambda args: {"lock_inputs": clean})
    monkeypatch.setattr(freeze_lock, "_write_lock", lambda path, record: written.append(path))

    args = freeze_lock._build_parser().parse_args(["freeze"])
    assert args.lock is None, "a --lock default pinned at import cannot follow --data-dir"
    assert freeze_lock.cmd_freeze(args) == freeze_lock.EXIT_OK
    assert written == [preflight.LOCK], "preflight is watching a file freeze does not write"


def test_preflight_reports_the_tree_state_without_failing_on_it(tmp_path, monkeypatch):
    """Refusing a dirty freeze belongs at the point of effect, not here.

    The freeze is the last manual step, so the three steps before it -- credential,
    canaries, lane enablement -- all happen with the lock absent and the tree
    legitimately dirty. A gate that failed on that would print red through every
    ordinary edit for the whole qualification, and a gate that cries wolf during
    normal work is one the operator learns to skip. `freeze_lock.py freeze` does
    the refusing; preflight only has to say what it will find.
    """
    monkeypatch.setattr(preflight, "LOCK", tmp_path / "runs/LOCK.json")
    dirty = {freeze_lock.DEFAULT_PUBLIC_REPO: False, freeze_lock.DEFAULT_PRIVATE_REPO: False}
    monkeypatch.setattr(freeze_lock, "_git_state",
                        lambda repo: {"path": str(repo), "commit": "0" * 40, "dirty": dirty[repo]})

    clean = preflight.check_lock_state()
    assert clean.state == preflight.PASS
    assert "both repos committed" in clean.detail

    dirty[freeze_lock.DEFAULT_PRIVATE_REPO] = True
    noted = preflight.check_lock_state()
    assert noted.state == preflight.PASS, "a dirty tree mid-qualification is not a preflight failure"
    assert freeze_lock.DEFAULT_PRIVATE_REPO.name in noted.detail

    def unreadable(repo):
        raise RuntimeError(f"{repo}: git rev-parse HEAD failed")

    monkeypatch.setattr(freeze_lock, "_git_state", unreadable)
    assert "unreadable" in preflight.check_lock_state().detail, (
        '"could not check" and "checked, fine" must never print the same')


def test_the_freeze_is_ordered_after_the_steps_that_change_what_it_hashes():
    """Qualification mutates the lock's own inputs, so it cannot follow the lock.

    Canaries and lane enablement both edit `qualification.yml`, and snapshotting
    terms rewrites `lrhe-data/terms/` -- tracked files in the private repository
    whose commit the lock records. A lock frozen before them reports
    `drift: lock_inputs.private_repo.commit` before the first measured run, which
    is the one ordering mistake that cannot be corrected after the fact.
    """
    steps = [step for step, _why in preflight.MANUAL_STEPS]
    freeze_at = next(i for i, s in enumerate(steps) if "freeze" in s)
    for earlier in ("canaries", "enable a lane"):
        at = next(i for i, s in enumerate(steps) if earlier in s)
        assert at < freeze_at, f"{steps[at]!r} must precede the freeze, not follow it"
    assert freeze_at == len(steps) - 1, "the freeze is the last manual step"


def _fake_catalogue(path: Path, provider_id: str, models: list[dict]) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("create table model_cache (provider_id TEXT PRIMARY KEY, models TEXT NOT NULL)")
        con.execute("insert into model_cache values (?, ?)", (provider_id, json.dumps(models)))
        con.commit()
    finally:
        con.close()


def _qualification(path: Path, selector: str) -> None:
    (path / "qualification.yml").write_text(
        yaml.safe_dump({"reviewers": {"kimi": {"model": selector, "councilEnabled": False}}}),
        encoding="utf-8",
    )


def test_preflight_resolves_selectors_through_the_hashed_provider_key(tmp_path, monkeypatch):
    """qualification.yml cannot assert that its own selectors exist.

    The catalogue keys scoped providers as `<provider>:models-v1:<hash>` -- a
    cache discriminator, not part of the selector -- so a resolver comparing the
    raw key would find no `opencode-go` at all and report every OpenCode lane
    unresolvable. That is the shape the blocker "selector not discovered against
    the installed build" was standing in for.
    """
    db = tmp_path / "models.db"
    _fake_catalogue(db, "opencode-go:models-v1:1gswkvxt6z2u9",
                    [{"id": "kimi-k3", "thinking": {"efforts": ["low", "high", "max"]}}])
    monkeypatch.setattr(preflight, "MODELS_DB", db)
    monkeypatch.setattr(preflight, "SKILL", tmp_path)

    _qualification(tmp_path, "opencode-go/kimi-k3")
    assert preflight.check_model_selectors().state == preflight.PASS

    _qualification(tmp_path, "opencode-go/kimi-k3:high")
    assert preflight.check_model_selectors().state == preflight.PASS

    for selector, expected in (
        ("opencode-nope/kimi-k3", "no cached catalogue"),
        ("opencode-go/kimi-k9", "serves no model"),
        ("opencode-go/kimi-k3:medium", "not 'medium'"),
    ):
        _qualification(tmp_path, selector)
        result = preflight.check_model_selectors()
        assert result.state == preflight.FAIL, selector
        assert expected in result.detail, result.detail


def test_an_absent_catalogue_is_unknown_rather_than_resolved(tmp_path, monkeypatch):
    """On CI there is no OMP cache, and "nothing to check" is not "checks out"."""
    monkeypatch.setattr(preflight, "MODELS_DB", tmp_path / "absent.db")
    monkeypatch.setattr(preflight, "SKILL", tmp_path)
    _qualification(tmp_path, "opencode-go/kimi-k3")
    assert preflight.check_model_selectors().state == preflight.UNKNOWN


# ------------------------------------------------------------------- canary

_AGENTS_PRESENT = (canary.AGENTS / "review-claude.md").is_file()
needs_agents = pytest.mark.skipif(
    not _AGENTS_PRESENT, reason="reviewer agent definitions are not present in this checkout")


def test_every_canary_grader_rejects_the_reply_built_to_fail_it():
    """A grader that cannot fail is decoration, and this one guards spending.

    Each probe ships the reply that should fail it. Run them before trusting a
    green canary, because otherwise the first paid request is also the first
    execution of the code deciding whether the answer was any good.
    """
    if not _AGENTS_PRESENT:
        pytest.skip("structured_output needs the agent definitions")
    for probe in canary.PROBES:
        failures = probe.grade("claude", probe.packet, probe.known_bad)
        assert failures, f"{probe.probe_id} accepted a reply built to fail it"


@needs_agents
def test_the_stub_reply_satisfies_every_apparatus_probe():
    """The stub is the reviewer every local run and every fixture is shaped like.

    It emitted `R01|...` while every reviewer's output schema requires
    `^R[1-9][0-9]*`, so the canned reply exercising the whole path was one no
    real reviewer is allowed to return -- and `score_lrhe.py` parses evidence
    leniently, so nothing downstream noticed. Same for the simulator and the
    fixtures. This keeps synthetic evidence answerable to the schema that
    governs the real thing.
    """
    qual = yaml.safe_load((canary.SKILL / "qualification.yml").read_text())["reviewers"]
    for family, entry in sorted(qual.items()):
        for probe in canary.PROBES:
            if probe.requires_judgement:
                continue
            request = canary._request(family, probe, entry)
            reply = run_review.stub_transport(request)
            assert not probe.grade(family, probe.packet, reply), (
                f"{family}/{probe.probe_id}: the stub reply fails a probe the "
                f"apparatus is supposed to pass")


def test_the_canary_refuses_a_transport_that_could_leave_the_machine(monkeypatch):
    """The canary talks to transports directly, so it needs its own egress guard.

    `dispatch()` is the gated path, and the canary deliberately does not use it:
    probes are pre-qualification and `prepare()` refuses an unqualified lane, so
    every lane that needs a canary would be refused. The cost of that shortcut is
    that adding a live transport would otherwise make this command an ungated way
    to reach it.
    """
    sent = []
    monkeypatch.setitem(run_review.TRANSPORTS, "pretend_live", lambda req: sent.append(req) or {})
    with pytest.raises(SystemExit) as refused:
        canary._send(object(), "pretend_live")
    assert "refuses transport" in str(refused.value)
    assert sent == [], "the canary reached a transport it had just refused"
    assert "pretend_live" not in canary.NON_EGRESS
