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
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import preflight  # noqa: E402
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
    monkeypatch.setattr(preflight, "DATA", tmp_path)
    assert preflight.check_lock_state().state == preflight.PASS, "absent is correct pre-upgrade"

    (tmp_path / "LOCK.json").write_text(json.dumps(
        {"lock_inputs": {"versions": {"omp": "0.0.0-stale"}}}), encoding="utf-8")
    stale = preflight.check_lock_state()
    assert stale.state == preflight.FAIL
    assert "0.0.0-stale" in stale.detail

    (tmp_path / "LOCK.json").write_text(json.dumps(
        {"lock_inputs": {"versions": {"omp": preflight.EXPECTED_OMP}}}), encoding="utf-8")
    assert preflight.check_lock_state().state == preflight.PASS
