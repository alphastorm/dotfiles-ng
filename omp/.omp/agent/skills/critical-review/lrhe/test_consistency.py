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

from argparse import Namespace
import hashlib
import json
import re
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
import auto_reliability  # noqa: E402

import freeze_lock  # noqa: E402  -- needs the path above
import preflight  # noqa: E402
import qualification  # noqa: E402
import run_review  # noqa: E402  -- needs the path above
import snapshot_terms  # noqa: E402

PANELS = yaml.safe_load((HERE / "panels.yaml").read_text())
POLICIES = yaml.safe_load((HERE / "provider-policies.yaml").read_text())
SCHEMAS = sorted(HERE.glob("*.schema.json"))


SKILL_DOC = HERE.parent / "SKILL.md"
LIVE_PROTOCOL_DOC = HERE / "LIVE-PROTOCOL.md"
CRITICAL_REVIEW_DOCS = (SKILL_DOC, LIVE_PROTOCOL_DOC)

LIVE_PROTOCOL_POINTER = "read `./lrhe/live-protocol.md` only after admission selects a full council"
STATE_FIDELITY_REQUIREMENT = "assumed starting state with the bound predecessor evidence"

# Admission, focused routing, state fidelity, and the lead's disposition authority.
# A session that reads only SKILL.md must be able to decide against a council and
# act on that decision, so these are stated there and nowhere else.
SKILL_OWNED_CONTROLS = (
    "assurance selection — before ceremony",
    "perform it before `epoch.py scaffold`",
    "do not independently raise it",
    "stop before review ceremony",
    "#### focused review routing",
    "only gpt/chatgpt and claude are qualified accountable leads",
    "always uses exactly one reciprocal cross-family strong critic",
    "the caller cannot name, replace, reorder, or add a reviewer",
    "this routing does not modify the full council roster",
    "never invoke ask to choose an assurance class",
    "### repoprompt context preparation",
    "before every design review and every full-council subject freeze",
    "repoprompt prose is never a reviewer verdict",
    "create_if_missing=true",
    "a design-stage council is selected only when",
    "an enclosing eval call may auto-background",
    "# state fidelity",
    STATE_FIDELITY_REQUIREMENT,
    "does not prove a successor transition",
    "every change admitted to the full council has one `review_sequence_id`",
    "decision rules:",
    "a confirmed p0 or p1 blocks closure",
    "findings are proposals, not implementation orders",
    "p2/p3 items receive explicit dispositions",
    "every returned item receives a ledger row and final disposition",
    "there is no majority verdict",
    "merge duplicates only when they share a root cause",
    "zero findings is valid",
)

# Full-council mechanics: roster and provider authorization, the frozen record,
# freeze and receipts, dispatch, the single bounded retry, ledger provenance,
# refutation, and close. An invocation that never admits a council must never pay
# to read these, and a control stated in both documents is one that will drift.
LIVE_PROTOCOL_OWNED_MECHANICS = (
    "`livedispatch` is the sole authoritative live panel definition",
    "strongcritic selects exactly one reciprocal cross-family assurance anchor",
    "match access_profile, not provider_route",
    "`provider_data_allowlist` and `reviewer_access_profile_allowlist` from the complete deterministic candidate set",
    "is missing, never approved",
    "not_selected/ineligible",
    "never pass an internal url glob to `glob`",
    "the frozen machine record must contain exactly these fields",
    "`general_review_pass_count`",
    "`targeted_refutation_used`",
    "`lifecycle_design_artifacts`",
    "`subject_digest` computed from the frozen artifact digest",
    "`schemaversion: 1`",
    "never re-run an identical check against an unchanged subject",
    "receipt on failure or drift",
    "a nonzero result prohibits provider dispatch",
    "recompute the same artifact and file digests",
    "the packet context is:",
    "the manifest binds the absolute record path",
    "until every member has settled",
    "one gated task wave",
    "records the envelope as in flight",
    "submit that object verbatim as the task call",
    "critical_review_dispatch_v1",
    "the sole live dispatch entry point",
    "do not disclose round-one responses between reviewers",
    "one total retry per member per epoch",
    "exactly one byte-identical retry",
    "missing/transport_failure",
    "missing/provider_policy_refusal",
    "invalid/schema_invalid",
    "invalid/model_mismatch",
    "stable id and normalized root-cause claim",
    "the command refuses any member key absent from that immutable manifest",
    "### capture outcomes",
    "shadow_ledger.py",
    "one targeted refutation for the entire review sequence",
    "close the frozen epoch before modifying reviewed files",
    "mark the review stale instead of synthesizing",
)

# Both documents are read from the skill root, so every relocated command names
# its tool through `./lrhe/`. The pre-split spellings resolved from two different
# directories, which is how `./review_checks.py` came to name nothing.
MOVED_COMMANDS = (
    "./lrhe/review_sequence.py --triage",
    "./lrhe/review_sequence.py review-record.json",
    "./lrhe/epoch.py scaffold",
    "./lrhe/epoch.py bind",
    "./lrhe/epoch.py freeze",
    "./lrhe/epoch.py recheck",
    "./lrhe/epoch.py ledger",
    "./lrhe/make_receipt.py",
    "./lrhe/review_dispatch.py prepare",
    "./lrhe/shadow_ledger.py",
    "./lrhe/review_checks.py quick",
    "./lrhe/review_checks.py full",
)

_FENCE = re.compile(r"^```(?P<language>[\w-]*)[ \t]*\n(?P<body>.*?)^```", re.DOTALL | re.MULTILINE)
_INVOKED_TOOL = re.compile(r"\./((?:[\w.-]+/)*[\w.-]+\.py)\b")
_LEGACY_INVOCATION = re.compile(r"python3?\s+[\"']?\$?[\w.${}/-]*\.py")


def _raw(path: Path) -> str:
    assert path.is_file(), (
        f"{path.name} is missing: admission and full-council mechanics are two documents"
    )
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Lowercased and whitespace-collapsed. Relocating a paragraph rewraps it, and
    a control that survived the move must not fail on where the lines now break."""
    return re.sub(r"\s+", " ", text.lower())


def _document(path: Path) -> str:
    return _flat(_raw(path))


def _owners(marker: str) -> set[str]:
    """Which of the two canonical documents states this control. Exactly one must:
    absent means the split dropped it, both means the next edit only fixes one."""
    return {path.name for path in CRITICAL_REVIEW_DOCS if marker in _document(path)}


def _section(flat: str, anchor: str) -> str:
    return flat.split(anchor, 1)[1].split(" ## ", 1)[0]


def test_proportional_assurance_policy_contract():
    system_append = _flat((HERE.parents[2] / "APPEND_SYSTEM.md").read_text(encoding="utf-8"))
    skill = _document(SKILL_DOC)
    classes = ("bounded experiment", "reusable internal path", "production/hard-to-reverse")

    assert all(name in skill for name in classes)
    for document in (system_append, skill):
        assert all(
            marker in document for marker in ("p0", "credential", "provider call", "security")
        )
    assert (
        "credible residual consequence after caps, containment, rollback, and recovery"
        in system_append
    )
    assert "not from p0 labels, security vocabulary, credentials, provider calls" in system_append
    assert "do not independently raise it" in skill

    assert "assurance selection — before ceremony" in skill
    assert "perform it before `epoch.py scaffold`" in skill
    assert "every change admitted to the full council has one `review_sequence_id`" in skill
    assert "a design-stage council is selected only when" in skill

    focused_routing = skill.split("#### focused review routing", 1)[1]
    focused_routing = focused_routing.split("when a full council is justified", 1)[0]
    assert "review-daybreak-blue for a claude lead" in focused_routing
    assert "review-claude-opus under cvp for" in focused_routing
    assert "gemini and grok remain full-council supplements" in focused_routing
    assert "review-claude-fable remains resolver-qualified architecture synthesis" in focused_routing
    assert "always uses exactly one reciprocal cross-family strong critic" in focused_routing
    assert "the caller cannot name, replace, reorder, or add a reviewer" in focused_routing
    assert "this routing does not modify the full council roster" in focused_routing

    hosted_policy = skill.split("### hosted material policy", 1)[1]
    hosted_policy = hosted_policy.split("## lead pre-dispatch obligations", 1)[0]
    assert "standing unattended authorization" in hosted_policy
    assert "never invoke ask merely to authorize" in hosted_policy
    assert "invoke ask before transmitting" not in hosted_policy
    assert "if hosted-provider authorization is absent" not in hosted_policy

    assignment = skill.split("this is the shape the generated assignment takes:", 1)[1]
    assignment = assignment.split("```text", 1)[1]
    assignment = assignment.split("```", 1)[0]
    assert "critical_review_resolver_receipt_v1" in assignment, (
        "the generated assignment no longer opens with the trusted receipt marker"
    )
    for marker in (
        "class",
        "assets and invariants",
        "credible adversary",
        "caps",
        "recovery contract",
        "result-validity conditions",
        "non-goals",
        "`subject_commit`",
        "`lead_family`",
        "`selectionclass`",
        "`role`",
        "`independence_class`",
        "`authority`",
        "zero findings is valid",
        "# state fidelity",
        STATE_FIDELITY_REQUIREMENT,
    ):
        assert marker in assignment

    decisions = _section(skill, "decision rules:")
    assert "in-scope residual" in decisions
    assert "assign severity after declared caps" in decisions
    assert "findings are proposals, not implementation orders" in decisions
    assert all(
        f"`{disposition}`" in decisions for disposition in ("accept", "defer", "reject", "mitigate")
    )

    for forbidden_machine_extension in (
        "assurance_class:",
        "assurance_mode:",
        "review_mode: bounded",
        "review_mode: reusable",
        "review_mode: production",
        "proportional-assurance.schema",
        "pragmatic reviewer",
    ):
        assert forbidden_machine_extension not in skill
        assert forbidden_machine_extension not in _document(LIVE_PROTOCOL_DOC)


def test_admission_controls_are_owned_once_by_the_eager_document():
    """Two canonical documents, no copies. A duplicated control is invisible in
    both files and only half-corrected by the next edit to either one."""
    misowned = {}
    for control in SKILL_OWNED_CONTROLS:
        owners = _owners(control)
        if owners != {SKILL_DOC.name}:
            misowned[control] = sorted(owners) or ["nowhere"]
    assert not misowned, f"admission controls not owned once by SKILL.md: {misowned}"


def test_full_council_mechanics_are_owned_once_by_the_on_demand_protocol():
    """The relocation is the whole point: these are the paragraphs an erroneously
    invoked skill used to read before it could decide it needed no council."""
    misowned = {}
    for mechanic in LIVE_PROTOCOL_OWNED_MECHANICS:
        owners = _owners(mechanic)
        if owners != {LIVE_PROTOCOL_DOC.name}:
            misowned[mechanic] = sorted(owners) or ["nowhere"]
    assert not misowned, f"mechanics missing from or restated outside the protocol: {misowned}"


def test_a_bounded_case_stops_before_the_live_protocol():
    """The regression the split prevents: paying for roster, record, freeze,
    dispatch, and ledger mechanics in order to conclude no council was needed."""
    skill = _document(SKILL_DOC)
    assert LIVE_PROTOCOL_DOC.is_file(), "there is no protocol to stop before"
    assert LIVE_PROTOCOL_POINTER in skill, "the pointer does not condition the read on admission"

    admission = skill.split(LIVE_PROTOCOL_POINTER, 1)[0]
    for control in (
        "bounded experiment",
        "stop before review ceremony",
        "always uses exactly one reciprocal cross-family strong critic",
        "review-claude-opus under cvp",
        "no_cloud",
    ):
        assert control in admission, f"{control!r} is only reachable after the protocol pointer"
    eager = [mechanic for mechanic in LIVE_PROTOCOL_OWNED_MECHANICS if mechanic in admission]
    assert not eager, f"the bounded path already pays for council mechanics: {eager}"


def test_the_protocol_is_read_on_demand_and_never_expanded_into_a_session():
    """A link is a decision; an include is a cost every session pays."""
    skill = _document(SKILL_DOC)
    named = skill.count("live-protocol.md")
    assert named == 1, f"SKILL.md names the protocol {named} times, not once"
    assert LIVE_PROTOCOL_POINTER in skill, "the read is not conditioned on full-council admission"
    for mechanism in ("@import", "{{", "!include", "<!-- include"):
        assert mechanism not in skill, f"SKILL.md expands the protocol through {mechanism!r}"


def test_the_admission_document_carries_no_dispatch_command_or_code():
    raw = _raw(SKILL_DOC)
    fences = [(match.group("language"), match.group("body")) for match in _FENCE.finditer(raw)]
    assert fences, "the common reviewer assignment is a fenced block"
    languages = {language for language, _ in fences}
    assert languages <= {"text"}, f"SKILL.md carries executable fences: {sorted(languages)}"
    assert not _INVOKED_TOOL.findall(raw), f"SKILL.md invokes {_INVOKED_TOOL.findall(raw)}"
    assert not _LEGACY_INVOCATION.search(raw), "SKILL.md still spells a python3 invocation"


def test_every_documented_command_resolves_from_the_skill_root():
    """Both documents are read from the skill root, so `./lrhe/<tool>.py` is the
    only spelling that names a real executable from either of them."""
    for path in CRITICAL_REVIEW_DOCS:
        raw = _raw(path)
        assert not _LEGACY_INVOCATION.search(raw), f"{path.name} still spells a python3 invocation"
        for relative in sorted(set(_INVOKED_TOOL.findall(raw))):
            tool = HERE.parent / relative
            assert tool.is_file(), f"{path.name} documents ./{relative}, which does not exist"
            assert tool.stat().st_mode & 0o111, f"./{relative} is documented but is not executable"

    protocol = _document(LIVE_PROTOCOL_DOC)
    missing = [command for command in MOVED_COMMANDS if command not in protocol]
    assert not missing, f"moved commands absent from the protocol: {missing}"


def test_the_moved_packet_context_still_matches_the_resolver_field_set():
    """The block is prose; `qualification.PACKET_FIELDS` is what refuses a packet.
    Relocating the block must not let the closed set drift in either direction."""
    body = _raw(LIVE_PROTOCOL_DOC).split("The packet context is:", 1)[1]
    fence = _FENCE.search(body)
    assert fence is not None, "the packet context is no longer a fenced block"
    assert fence.group("language") in {"yaml", "yml"}
    documented = yaml.safe_load(fence.group("body")) or {}
    assert set(documented) == set(qualification.PACKET_FIELDS), (
        f"documented packet context {sorted(documented)} is not the resolver's "
        f"closed set {sorted(qualification.PACKET_FIELDS)}"
    )


def test_state_fidelity_is_owned_once_by_the_trusted_assignment():
    """One canonical owner: the assignment the dispatcher transmits. Copying it
    into reviewer definitions is how a shared floor becomes several floors."""
    assert _owners("# state fidelity") == {SKILL_DOC.name}
    assert _owners(STATE_FIDELITY_REQUIREMENT) == {SKILL_DOC.name}

    definitions = sorted(canary.AGENTS.glob("review-*.md"))
    if not definitions:
        pytest.skip("reviewer agent definitions are not present in this checkout")
    restated = [
        definition.name
        for definition in definitions
        if STATE_FIDELITY_REQUIREMENT in _flat(definition.read_text(encoding="utf-8"))
    ]
    assert not restated, f"private reviewer definitions restate the trusted assignment: {restated}"


def _routes_in_panels() -> set[str]:
    return {f["providerRoute"] for e in PANELS["experiments"] for f in e["families"]}


def test_auto_reliability_excludes_the_retired_local_qwen_lane():
    assert "qwen" not in auto_reliability.FAMILIES
    assert auto_reliability.REPEAT_FAMILIES == ("gpt",)
    assert all(
        not row["selector"].startswith("nyc-pc/") and row["agent"] != "judge-qwen-auto"
        for row in auto_reliability.FAMILIES.values()
    )


# ------------------------------------------------------- panels vs policies


def test_every_panel_route_has_a_policy():
    """A lane you can schedule but cannot get a rights decision for is a dead end.

    This is the bug that shipped: panels.yaml declared provider routes that had
    no rights policy while their lanes were evaluation-enabled.
    """
    have = {p["providerRoute"] for p in POLICIES["policies"]}
    missing = sorted(_routes_in_panels() - have)
    assert not missing, (
        f"panels.yaml schedules {missing} but provider-policies.yaml has no policy "
        f"for them; run_review.py will refuse every request on those routes"
    )


def test_every_policy_names_a_snapshot_that_exists():
    """A termsSnapshotId pointing at nothing is a citation to a missing document.

    The record would still validate and still look like evidence.
    """
    known = set(snapshot_terms.SNAPSHOT_COMPONENTS)
    dangling = sorted(
        p["termsSnapshotId"] for p in POLICIES["policies"] if p["termsSnapshotId"] not in known
    )
    assert not dangling, f"termsSnapshotId values with no snapshot definition: {dangling}"


def test_no_policy_still_carries_a_placeholder_snapshot():
    """UNSNAPSHOTTED is honest as a marker and unacceptable as a final state."""
    placeholders = sorted(
        p["policyId"] for p in POLICIES["policies"] if "UNSNAPSHOT" in p["termsSnapshotId"].upper()
    )
    assert not placeholders, (
        f"{placeholders} cite a placeholder snapshot id; fetch the terms with "
        f"snapshot_terms.py and cite the real id"
    )


def test_snapshot_components_all_have_a_source_url():
    """A snapshot listing a component the source table cannot fetch never completes."""
    orphans = sorted(
        f"{sid}/{c}"
        for sid, comps in snapshot_terms.SNAPSHOT_COMPONENTS.items()
        for c in comps
        if c not in snapshot_terms.TERMS_SOURCES
    )
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
        if (
            policy.get("termsClearanceStatus")
            != "operator_risk_accepted_pending_written_clarification"
        ):
            continue
        auth = policy.get("operatorAuthorization")
        assert auth, f"{policy['policyId']} claims risk acceptance with no operatorAuthorization"
        assert auth.get("principal"), f"{policy['policyId']} names no principal"
        assert auth.get("effectiveDate"), f"{policy['policyId']} has no effective date"
        assert len(auth.get("recordSha256", "")) == 64, (
            f"{policy['policyId']} does not hash the authorization it rests on"
        )


def test_no_policy_permits_training_a_competing_model():
    """The one prohibition no registry edit may grant."""
    offenders = sorted(
        p["policyId"] for p in POLICIES["policies"] if p.get("modelTrainingAllowed") is not False
    )
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
    docs = [
        json.loads((HERE / n).read_text()) for n in ("run.schema.json", "data-rights.schema.json")
    ]
    registry = Registry().with_resources([(d["$id"], Resource.from_contents(d)) for d in docs])
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
            f"{exp['experimentId']} nulls on {null!r}, which is not in its panel {sorted(families)}"
        )


def test_requirements_are_all_pinned():
    """An unpinned dependency makes the analysis silently version-dependent."""
    unpinned = [
        line.strip()
        for line in (HERE / "requirements.txt").read_text().splitlines()
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
        assert result.state in (
            preflight.PASS,
            preflight.FAIL,
            preflight.UNKNOWN,
            preflight.SKIP,
        ), f"{name}: {result.state}"
        assert result.detail, f"{name} reported {result.state} with no detail"


def test_evaluation_lane_gate_does_not_call_live_specialists_held(monkeypatch):
    """Dispatch and evaluation are separate authorities in operator output."""
    monkeypatch.setattr(preflight, "load_qualification", lambda _path: {})
    monkeypatch.setattr(
        preflight,
        "qualification_reviewers",
        lambda _document: {
            "daybreak-blue": {"dispatchEnabled": True, "evaluationEnabled": False},
            "grok": {"dispatchEnabled": True, "evaluationEnabled": True},
        },
    )

    result = preflight.check_lanes_held()

    assert result.state == preflight.PASS
    assert result.detail == (
        "evaluation-enabled ['grok'] all canaried; evaluation-disabled ['daybreak-blue']"
    )
    assert "held" not in result.detail


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

    lock.write_text(
        json.dumps({"lock_inputs": {"versions": {"omp": "0.0.0-stale"}}}), encoding="utf-8"
    )
    stale = preflight.check_lock_state()
    assert stale.state == preflight.FAIL
    assert "0.0.0-stale" in stale.detail

    lock.write_text(
        json.dumps({"lock_inputs": {"versions": {"omp": preflight.EXPECTED_OMP}}}), encoding="utf-8"
    )
    assert preflight.check_lock_state().state == preflight.PASS


def test_preflight_inspects_the_lock_that_freeze_actually_writes(monkeypatch):
    """Two files naming the same artifact by hand is how a gate goes blind.

    freeze wrote `lrhe-data/runs/LOCK.json`; preflight looked for
    `lrhe-data/LOCK.json`. Both files were internally consistent, every test
    passed, and the gate that refuses a lock frozen under the wrong toolchain
    would have reported "no lock yet, which is correct" forever -- including
    after the lock existed. Asserting the constants match would be circular, so
    this drives the write path the command actually takes.

    Through `main`, not `cmd_freeze`. Calling the subcommand directly skipped the
    one line between them, `args.lock = Path(args.lock)`, which raised TypeError
    on the documented default of not passing --lock at all. `cmd_freeze` already
    defaults it from --data-dir, so the coercion was redundant as well as fatal,
    and the only invocation that never reached it was the tested one.
    """
    written: list[Path] = []
    clean = {"public_repo": {"dirty": False}, "private_repo": {"dirty": False}}
    monkeypatch.setattr(freeze_lock, "_build_record", lambda args: {"lock_inputs": clean})
    monkeypatch.setattr(freeze_lock, "_write_lock", lambda path, record: written.append(path))

    assert freeze_lock._build_parser().parse_args(["freeze"]).lock is None, (
        "a --lock default pinned at import cannot follow --data-dir"
    )
    assert freeze_lock.main(["freeze"]) == freeze_lock.EXIT_OK
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
    monkeypatch.setattr(
        freeze_lock,
        "_git_state",
        lambda repo: {"path": str(repo), "commit": "0" * 40, "dirty": dirty[repo]},
    )

    clean = preflight.check_lock_state()
    assert clean.state == preflight.PASS
    assert "both repos committed" in clean.detail

    dirty[freeze_lock.DEFAULT_PRIVATE_REPO] = True
    noted = preflight.check_lock_state()
    assert noted.state == preflight.PASS, (
        "a dirty tree mid-qualification is not a preflight failure"
    )
    assert freeze_lock.DEFAULT_PRIVATE_REPO.name in noted.detail

    def unreadable(repo):
        raise RuntimeError(f"{repo}: git rev-parse HEAD failed")

    monkeypatch.setattr(freeze_lock, "_git_state", unreadable)
    assert "unreadable" in preflight.check_lock_state().detail, (
        '"could not check" and "checked, fine" must never print the same'
    )


def test_the_freeze_is_ordered_after_the_steps_that_change_what_it_hashes():
    """Qualification mutates the lock's own inputs, so it cannot follow the lock.

    Canaries and lane enablement both edit `qualification.yml`, and snapshotting
    terms rewrites `lrhe-data/terms/` -- tracked files in the private repository
    whose commit the lock records. A lock frozen before them reports
    `drift: lock_inputs.private_repo.commit` before the first measured run, which
    is the one ordering mistake that cannot be corrected after the fact.

    It is squeezed from the other side too: the smoke pass is the first thing to
    spend a measured run, and a run made before the freeze is a run the lock
    cannot vouch for. So the freeze is not last -- it is exactly between them.
    """
    steps = [step for step, _why, _todo in preflight.MANUAL_STEPS]
    freeze_at = next(i for i, s in enumerate(steps) if "freeze" in s)
    canaries_at = next(i for i, s in enumerate(steps) if "canaries" in s)
    smoke_at = next(i for i, s in enumerate(steps) if "smoke" in s)
    assert canaries_at < freeze_at, "the canaries edit qualification.yml, which the lock hashes"
    assert freeze_at < smoke_at, "the smoke pass is a measured run and needs a lock to name"


def test_a_finished_step_stops_being_printed():
    """The checklist went stale in the obvious way, so it is derived now.

    It named the OMP upgrade and the canaries for a session after both were done,
    and the only way to learn what actually remained was to read the gates and
    reconstruct it by hand. Every step whose completion a gate can see now
    answers for itself.
    """
    done = {"omp version": preflight.Result(preflight.PASS, f"omp {preflight.EXPECTED_OMP}")}
    upgrade = next(todo for step, _why, todo in preflight.MANUAL_STEPS if "upgrade" in step)
    assert upgrade(done) is False
    assert upgrade({"omp version": preflight.Result(preflight.FAIL, "older omp")}) is True


def _fake_catalogue(path: Path, provider_id: str, models: list[dict]) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("create table model_cache (provider_id TEXT PRIMARY KEY, models TEXT NOT NULL)")
        con.execute("insert into model_cache values (?, ?)", (provider_id, json.dumps(models)))
        con.commit()
    finally:
        con.close()


def _qualification(path: Path, selector: str) -> None:
    data = path / "lrhe-data"
    data.mkdir(exist_ok=True)
    for name, probe in (
        ("canary.jsonl", "historical"),
        ("canary-v2.jsonl", "enforced"),
        ("canary-v3.jsonl", "live"),
    ):
        (data / name).write_text(
            json.dumps({"schema": "lrhe-canary-v1", "probe_id": probe}) + "\n",
            encoding="utf-8",
        )
    document = {
        "schemaVersion": qualification.SCHEMA_VERSION,
        "canaryLedgers": {
            "historical": {
                "path": "lrhe-data/canary.jsonl",
                "mode": "sealed",
                "sha256": freeze_lock._sha256_file(data / "canary.jsonl"),
                "authority": "historical-non-scoring",
            },
            "enforced": {
                "path": "lrhe-data/canary-v2.jsonl",
                "mode": "sealed",
                "sha256": freeze_lock._sha256_file(data / "canary-v2.jsonl"),
                "authority": "evaluation",
            },
            "live": {
                "path": "lrhe-data/canary-v3.jsonl",
                "mode": "append-only",
                "prefixRows": 1,
                "prefixSha256": freeze_lock._sha256_file(data / "canary-v3.jsonl"),
                "authority": "live-qualification",
            },
        },
        "oracleShadow": {
            "enabled": True,
            "shadow_id": "oracle-chatgpt-pro-web",
            "model_family": "gpt",
            "correlation_group": "openai-chatgpt-pro-web",
            "provider_route": "openai-chatgpt-web",
            "access_profile": "chatgpt-pro-web-asxst0rm",
            "data_allowlist_key": "openai",
            "preset": "pro_extended",
            "execution_mode": "pi_oracle_async",
            "evidence_delivery": "repository",
            "dataset_path": "lrhe-data/oracle-shadow",
        },
        "liveDispatch": {
            "panelId": qualification.LIVE_PANEL_ID,
            "byLeadFamily": {
                family: {
                    "strongCritic": [],
                    "supplements": [],
                    "architectureSpecialists": [],
                }
                for family in ("gpt", "claude")
            },
            "targetedRefuters": [],
            "evaluationOnly": ["kimi"],
            "disabled": [],
        },
        "reviewers": {
            "kimi": {
                "agent": "review-kimi-floor",
                "model": selector,
                "dispatchEnabled": False,
                "evaluationEnabled": True,
                "model_family": "kimi",
                "correlation_group": "kimi-k3",
                "provider_route": "opencode-go",
                "access_profile": "opencode-go-default",
                "data_allowlist_key": "opencode",
                "execution_mode": "task_agent",
                "providerCanary": "passed",
                "schemaValid": True,
                "readOnlyBoundary": "passed",
            }
        },
    }
    (path / "qualification.yml").write_text(yaml.safe_dump(document), encoding="utf-8")


def test_canary_ledger_integrity_allows_append_but_rejects_prefix_drift(tmp_path, monkeypatch):
    _qualification(tmp_path, "opencode-go/kimi-k3")
    monkeypatch.setattr(preflight, "SKILL", tmp_path)

    clean = preflight.check_canary_ledger_integrity()
    assert clean.state == preflight.PASS, clean.detail
    pins = freeze_lock._canary_ledger_pins(Namespace(qualification=tmp_path / "qualification.yml"))
    assert pins["live"]["path"] == "lrhe-data/canary-v3.jsonl"

    active = tmp_path / "lrhe-data/canary-v3.jsonl"
    prefix = active.read_text(encoding="utf-8")
    active.write_text(
        prefix
        + json.dumps(
            {
                "schema": "lrhe-canary-v1",
                "probe_id": "later",
                "verdict": "provider",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    appended = preflight.check_canary_ledger_integrity()
    assert appended.state == preflight.PASS, appended.detail

    active.write_text(
        json.dumps({"schema": "lrhe-canary-v1", "probe_id": "rewritten"}) + "\n",
        encoding="utf-8",
    )
    drifted = preflight.check_canary_ledger_integrity()
    assert drifted.state == preflight.FAIL
    assert "drift" in drifted.detail


def test_canary_writer_refuses_sealed_and_non_provider_protected_ledgers(tmp_path, monkeypatch):
    _qualification(tmp_path, "opencode-go/kimi-k3")
    monkeypatch.setattr(canary, "SKILL", tmp_path)
    monkeypatch.setattr(canary, "DATA", tmp_path / "lrhe-data")

    sealed = canary.DATA / "canary.jsonl"
    sealed_before = sealed.read_bytes()
    provider = {
        "schema": "lrhe-canary-v1",
        "probe_id": "later",
        "verdict": "provider",
    }
    with pytest.raises(canary.OutputRefusal, match="sealed"):
        canary._append_canary_records(sealed, [provider])
    assert sealed.read_bytes() == sealed_before

    active = canary.DATA / "canary-v3.jsonl"
    canary._append_canary_records(active, [provider])
    active_before = active.read_bytes()
    apparatus = {**provider, "probe_id": "stub", "verdict": "apparatus"}
    with pytest.raises(canary.OutputRefusal, match="provider verdicts only"):
        canary._append_canary_records(active, [apparatus])
    assert active.read_bytes() == active_before


def test_qualification_rejects_unknown_canary_authority(tmp_path):
    _qualification(tmp_path, "opencode-go/kimi-k3")
    path = tmp_path / "qualification.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["canaryLedgers"]["live"]["authority"] = "live_qualification"
    with pytest.raises(qualification.QualificationError, match="authority must be one of"):
        qualification.validate_qualification(document)


def test_preflight_resolves_selectors_through_the_hashed_provider_key(tmp_path, monkeypatch):
    """qualification.yml cannot assert that its own selectors exist.

    The catalogue keys scoped providers as `<provider>:models-v1:<hash>` -- a
    cache discriminator, not part of the selector -- so a resolver comparing the
    raw key would find no `opencode-go` at all and report every OpenCode lane
    unresolvable. That is the shape the blocker "selector not discovered against
    the installed build" was standing in for.
    """
    db = tmp_path / "models.db"
    _fake_catalogue(
        db,
        "opencode-go:models-v1:1gswkvxt6z2u9",
        [
            {"id": "kimi-k3", "thinking": {"efforts": ["low", "high", "max"]}},
            {"id": "no-effort-metadata"},
        ],
    )
    monkeypatch.setattr(preflight, "MODELS_DB", db)
    monkeypatch.setattr(preflight, "SKILL", tmp_path)
    monkeypatch.setattr(preflight, "MODELS_CONFIG", tmp_path / "absent-models.yml")

    _qualification(tmp_path, "opencode-go/kimi-k3")
    assert preflight.check_model_selectors().state == preflight.PASS

    _qualification(tmp_path, "opencode-go/kimi-k3:high")
    assert preflight.check_model_selectors().state == preflight.PASS

    for selector, expected in (
        ("opencode-nope/kimi-k3", "no cached catalogue"),
        ("opencode-go/kimi-k9", "serves no model"),
        ("opencode-go/kimi-k3:medium", "not 'medium'"),
        ("opencode-go/no-effort-metadata:max", "offers [], not 'max'"),
    ):
        _qualification(tmp_path, selector)
        result = preflight.check_model_selectors()
        assert result.state == preflight.FAIL, selector
        assert expected in result.detail, result.detail


def test_preflight_applies_effective_model_effort_overrides(tmp_path, monkeypatch):
    db = tmp_path / "models.db"
    models_config = tmp_path / "models.yml"
    _fake_catalogue(
        db,
        "openai-codex",
        [
            {
                "id": "gpt-daybreak-blue-latest",
                "thinking": {"efforts": ["minimal", "low", "medium", "high", "xhigh"]},
            }
        ],
    )
    monkeypatch.setattr(preflight, "MODELS_DB", db)
    monkeypatch.setattr(preflight, "MODELS_CONFIG", models_config)
    monkeypatch.setattr(preflight, "SKILL", tmp_path)
    _qualification(tmp_path, "openai-codex/gpt-daybreak-blue-latest:max")

    unresolved = preflight.check_model_selectors()
    assert unresolved.state == preflight.FAIL
    assert "not 'max'" in unresolved.detail

    models_config.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "openai-codex": {
                        "modelOverrides": {
                            "gpt-daybreak-blue-latest": {
                                "thinking": {
                                    "mode": "effort",
                                    "efforts": ["low", "medium", "high", "xhigh", "max"],
                                    "defaultLevel": "low",
                                }
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    resolved = preflight.check_model_selectors()
    assert resolved.state == preflight.PASS, resolved.detail

    configured = yaml.safe_load(models_config.read_text(encoding="utf-8"))
    configured["providers"]["openai-codex"]["modelOverrides"]["gpt-daybreak-blue-latest"][
        "thinking"
    ]["efforts"] = []
    models_config.write_text(yaml.safe_dump(configured), encoding="utf-8")
    empty_ladder = preflight.check_model_selectors()
    assert empty_ladder.state == preflight.FAIL
    assert "offers [], not 'max'" in empty_ladder.detail


def test_an_absent_catalogue_is_unknown_rather_than_resolved(tmp_path, monkeypatch):
    """On CI there is no OMP cache, and "nothing to check" is not "checks out"."""
    monkeypatch.setattr(preflight, "MODELS_DB", tmp_path / "absent.db")
    monkeypatch.setattr(preflight, "MODELS_CONFIG", tmp_path / "absent-models.yml")
    monkeypatch.setattr(preflight, "SKILL", tmp_path)
    _qualification(tmp_path, "opencode-go/kimi-k3")
    assert preflight.check_model_selectors().state == preflight.UNKNOWN


_AGENTS_PRESENT = (canary.AGENTS / "review-claude-fable.md").is_file()
needs_agents = pytest.mark.skipif(
    not _AGENTS_PRESENT, reason="reviewer agent definitions are not present in this checkout"
)


@needs_agents
def test_live_evidence_contract_matches_active_config_and_receipt():
    result = preflight.check_reviewer_evidence_contracts()
    assert result.state == preflight.PASS, result.detail


def test_probe_pins_bind_version_fixture_and_role(tmp_path):
    """qualification.yml's probe pins are assertions until preflight re-checks them.

    The cited prompt version must be versioned in repository-probes.yml, the
    fixture must hash to its pin, the probe text must name that fixture, and
    the probe's role must match the lane it qualifies -- exactly the drift
    classes a hand-run requalification can produce.
    """
    data = tmp_path / "lrhe-data"
    data.mkdir()
    fixture = data / "repository-canary-parse.py"
    fixture.write_text("def paginate():\n    return []\n", encoding="utf-8")
    twin = data / "repository-canary-auth.py"
    twin.write_bytes(fixture.read_bytes())
    (data / "repository-probes.yml").write_text(
        yaml.safe_dump(
            {
                "schemaVersion": 1,
                "probes": {
                    "live-repository-v6": {
                        "role": "primary_critic",
                        "assignment": "Review lrhe-data/repository-canary-parse.py only.",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    measured = {
        "repositoryPromptVersion": "live-repository-v6",
        "repositoryFixture": "lrhe-data/repository-canary-parse.py",
        "repositoryFixtureSha256": freeze_lock._sha256_file(fixture),
    }

    assert preflight._probe_pin_problems("claude", measured, ("primary_critic",), tmp_path) == []

    unversioned = {**measured, "repositoryPromptVersion": "live-repository-v2"}
    assert any(
        "not versioned" in problem
        for problem in preflight._probe_pin_problems(
            "claude", unversioned, ("primary_critic",), tmp_path
        )
    )

    assert any(
        "selected roles" in problem
        for problem in preflight._probe_pin_problems(
            "glm", measured, ("targeted_refuter",), tmp_path
        )
    )

    unnamed = {**measured, "repositoryFixture": "lrhe-data/repository-canary-auth.py"}
    assert any(
        "never names" in problem
        for problem in preflight._probe_pin_problems(
            "claude", unnamed, ("primary_critic",), tmp_path
        )
    )

    absent = preflight._probe_pin_problems(
        "claude", measured, ("primary_critic",), tmp_path / "empty"
    )
    assert any("unreadable" in problem for problem in absent)

    fixture.write_text("drifted\n", encoding="utf-8")
    assert any(
        "hashes" in problem
        for problem in preflight._probe_pin_problems(
            "claude", measured, ("primary_critic",), tmp_path
        )
    )


@needs_agents
def test_evidence_contract_rejects_an_active_model_override_drift(tmp_path, monkeypatch):
    config = yaml.safe_load(preflight.CONFIG.read_text(encoding="utf-8"))
    config["task"]["agentModelOverrides"]["review-grok"] = "xai-oauth/grok-build"
    wrong = tmp_path / "config.yml"
    wrong.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setattr(preflight, "CONFIG", wrong)
    result = preflight.check_reviewer_evidence_contracts()
    assert result.state == preflight.FAIL
    assert "active override" in result.detail


def test_receiptless_evaluation_lane_model_drift_fails_preflight(tmp_path, monkeypatch):
    """kimi and deepseek carry no trace receipt, so nothing byte-pins their
    definitions; the corpus still attributes every row to the lane's recorded
    model. The contract check therefore verifies the definition file and its
    pinned model even without a receipt, and ignores fully disabled lanes."""
    _qualification(tmp_path, "opencode-go/kimi-k3")
    agents = tmp_path / "agents"
    agents.mkdir()
    _write_trace_agent(agents / "review-kimi-floor.md", "opencode-go/kimi-k3")
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump({"task": {"agentModelOverrides": {}}}), encoding="utf-8")
    monkeypatch.setattr(preflight, "SKILL", tmp_path)
    monkeypatch.setattr(preflight, "AGENTS", agents)
    monkeypatch.setattr(preflight, "CONFIG", config)

    clean = preflight.check_reviewer_evidence_contracts()
    assert clean.state == preflight.PASS, clean.detail

    _write_trace_agent(agents / "review-kimi-floor.md", "opencode-go/kimi-k3-next")
    drifted = preflight.check_reviewer_evidence_contracts()
    assert drifted.state == preflight.FAIL
    assert "kimi: agent model" in drifted.detail

    (agents / "review-kimi-floor.md").unlink()
    missing = preflight.check_reviewer_evidence_contracts()
    assert missing.state == preflight.FAIL

    document = yaml.safe_load((tmp_path / "qualification.yml").read_text(encoding="utf-8"))
    document["reviewers"]["kimi"]["evaluationEnabled"] = False
    document["liveDispatch"]["evaluationOnly"] = []
    document["liveDispatch"]["disabled"] = ["kimi"]
    (tmp_path / "qualification.yml").write_text(yaml.safe_dump(document), encoding="utf-8")
    disabled = preflight.check_reviewer_evidence_contracts()
    assert disabled.state == preflight.PASS, disabled.detail


# ------------------------------------------------- conditional critic scope

FABLE_POLICY = qualification.FABLE_POLICY
FABLE_SELECTOR = "anthropic/claude-fable-5:max"


def _write_fable_agent(path: Path, selector: str = FABLE_SELECTOR) -> None:
    """The production Fable charter shape: Max, read-only, and its own marker.

    It deliberately does not carry `CRITICAL_REVIEWER_READ_ONLY_V1`. The lane
    exists because the security-vocabulary boilerplate was removed from its
    charter, so requiring the old marker would require the prose the lane was
    created to drop.
    """
    tools = "".join(f"  - {tool}\n" for tool in qualification.READ_ONLY_REPOSITORY_TOOLS)
    path.write_text(
        "---\n"
        "name: review-claude-fable\n"
        f"tools:\n{tools}"
        f"model: [{selector}]\n"
        f"thinkingLevel: {FABLE_POLICY.thinking_level}\n"
        "output:\n"
        "  type: object\n"
        "  additionalProperties: false\n"
        "  required: [summary, evidence, unresolved]\n"
        "  properties:\n"
        "    summary: {type: string}\n"
        "    evidence: {type: array, items: {type: string}}\n"
        "    unresolved: {type: array, items: {type: string}}\n"
        "---\n"
        f"{FABLE_POLICY.read_only_marker}\n",
        encoding="utf-8",
    )


def _trace_receipt(definition: Path, agent: str, selector: str, delivery: str) -> dict:
    model, effort = canary._selector_parts(selector)
    return {
        "schema": canary.TRACE_RECEIPT_SCHEMA,
        "result": "passed",
        "agent": agent,
        "requested_selector": selector,
        "requested_model": model,
        "thinking_level": effort,
        "evidence_delivery": delivery,
        "agent_tools": canary._contract_tools(delivery),
        "served_models": [model],
        "declared_tools": canary._declared_contract_tools(delivery),
        "tool_attempts": ["read", "yield"],
        "tool_executions": ["read", "yield"],
        "forbidden_tool_attempts": 0,
        "forbidden_tool_executions": 0,
        "fallback_used": False,
        "output_schema_valid": True,
        "session_file": "session.jsonl",
        "session_sha256": "0" * 64,
        "agent_definition_sha256": hashlib.sha256(definition.read_bytes()).hexdigest(),
        "observed_at": "2026-08-10T00:00:00Z",
    }


def _fable_cohort(skill: Path, definition: Path, attempts: int = 20) -> Path:
    """Mint one cohort receipt whose completed attempts cite real v2 receipts."""
    data = skill / "lrhe-data"
    cohort_dir = data / "fable-max-architecture"
    cohort_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index in range(1, attempts + 1):
        receipt_path = cohort_dir / f"attempt-{index:02d}.json"
        receipt_path.write_text(
            json.dumps(
                _trace_receipt(definition, "review-claude-fable", FABLE_SELECTOR, "repository"),
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        rows.append(
            {
                "attempt": index,
                "outcome": "completed",
                "receipt": str(receipt_path.relative_to(skill)),
                "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            }
        )
    cohort = {
        "schema": FABLE_POLICY.cohort_schema,
        "result": "passed",
        "cohort_id": "fable-max-architecture-20260810",
        "policy": FABLE_POLICY.policy,
        "scope": FABLE_POLICY.required_scope,
        "agent": "review-claude-fable",
        "requested_selector": FABLE_SELECTOR,
        "requested_model": qualification.selector_model(FABLE_SELECTOR),
        "thinking_level": FABLE_POLICY.thinking_level,
        "evidence_delivery": "repository",
        "read_only_marker": FABLE_POLICY.read_only_marker,
        "agent_definition_sha256": hashlib.sha256(definition.read_bytes()).hexdigest(),
        "risk_domain_scope": list(FABLE_POLICY.qualified_risk_domains),
        "security_misroutes": 0,
        "forbidden_tool_attempts": 0,
        "forbidden_tool_executions": 0,
        "seeded_defects_found": 8,
        "seeded_defects_total": 18,
        "negative_control_false_positives": 0,
        "negative_control_attempts": 6,
        "provider_policy_refusals": 0,
        "fallback_used": False,
        "attempts": rows,
        "observed_at": "2026-08-10T00:00:00Z",
    }
    path = data / "fable-max-architecture-cohort.json"
    path.write_text(json.dumps(cohort, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _conditional_scope_fixture(tmp_path, monkeypatch, attempts: int = 20) -> tuple[Path, Path]:
    _qualification(tmp_path, "opencode-go/kimi-k3")
    document = yaml.safe_load((tmp_path / "qualification.yml").read_text(encoding="utf-8"))
    document["liveDispatch"]["byLeadFamily"]["gpt"]["architectureSpecialists"] = ["claude"]
    document["reviewers"]["claude"] = {
        "dispatchEnabled": True,
        "evaluationEnabled": True,
        "model_family": "claude",
        "correlation_group": "claude-fable-5",
        "provider_route": "anthropic",
        "access_profile": "anthropic-subscription",
        "data_allowlist_key": "anthropic",
        "execution_mode": "task_agent",
        "lens": "architecture",
        "agent": "review-claude-fable",
        "model": FABLE_SELECTOR,
        "fallbackAllowed": False,
        "qualification": {
            "common": {
                "schemaValid": True,
                "readOnlyBoundary": "passed",
                "exactServedModelRequired": qualification.selector_model(FABLE_SELECTOR),
            },
            "scopes": {
                "non-security-architecture": {
                    "status": "passed",
                    "canaryReceipt": "lrhe-data/fable-max-architecture-cohort.json",
                },
                "security": {
                    "status": "ineligible",
                    "boundaryEvidence": ["lrhe-data/fable-max-refusals.json"],
                },
            },
        },
        "eligibility": {
            "policy": FABLE_POLICY.policy,
            "allowedReviewModes": list(FABLE_POLICY.allowed_review_modes),
            "activationRiskDomainsAny": list(FABLE_POLICY.activation_risk_domains),
            "deniedRiskDomains": list(FABLE_POLICY.denied_risk_domains),
            "requiredProofClassStatuses": {"authorization": "not-applicable"},
            "denyPathComponentRegex": FABLE_POLICY.deny_path_pattern,
            "onUnknown": "skip",
        },
    }
    (tmp_path / "qualification.yml").write_text(yaml.safe_dump(document), encoding="utf-8")

    agents = tmp_path / "agents"
    agents.mkdir(exist_ok=True)
    definition = agents / "review-claude-fable.md"
    _write_fable_agent(definition)
    _write_trace_agent(agents / "review-kimi-floor.md", "opencode-go/kimi-k3")
    config = tmp_path / "config.yml"
    config.write_text(
        yaml.safe_dump(
            {
                "task": {
                    "maxConcurrency": 4,
                    "agentModelOverrides": {"review-claude-fable": FABLE_SELECTOR},
                }
            }
        ),
        encoding="utf-8",
    )
    cohort = _fable_cohort(tmp_path, definition, attempts)
    monkeypatch.setattr(preflight, "SKILL", tmp_path)
    monkeypatch.setattr(preflight, "AGENTS", agents)
    monkeypatch.setattr(preflight, "CONFIG", config)
    return definition, cohort


def test_a_complete_max_cohort_binds_the_conditional_critic(tmp_path, monkeypatch):
    """The gate passes only on a cohort that meets every promotion threshold."""
    _conditional_scope_fixture(tmp_path, monkeypatch)
    result = preflight.check_conditional_critic_scope()
    assert result.state == preflight.PASS, result.detail


def test_a_missing_scope_receipt_fails_the_conditional_gate(tmp_path, monkeypatch):
    _, cohort = _conditional_scope_fixture(tmp_path, monkeypatch)
    cohort.unlink()
    result = preflight.check_conditional_critic_scope()
    assert result.state == preflight.FAIL
    assert "unreadable" in result.detail


def test_an_agent_edited_after_its_cohort_makes_the_receipt_stale(tmp_path, monkeypatch):
    """Re-qualification is a fresh cohort, never a prose edit to the charter."""
    definition, _ = _conditional_scope_fixture(tmp_path, monkeypatch)
    definition.write_text(
        definition.read_text(encoding="utf-8") + "\nOne more instruction.\n", encoding="utf-8"
    )
    result = preflight.check_conditional_critic_scope()
    assert result.state == preflight.FAIL
    assert "agent_definition_sha256" in result.detail


def test_a_short_cohort_cannot_promote_the_conditional_critic(tmp_path, monkeypatch):
    _conditional_scope_fixture(tmp_path, monkeypatch, attempts=3)
    result = preflight.check_conditional_critic_scope()
    assert result.state == preflight.FAIL
    assert f"fewer than the {FABLE_POLICY.cohort_min_eligible_attempts}" in result.detail


def test_a_refused_attempt_cannot_cite_a_passed_trace(tmp_path, monkeypatch):
    """A refusal is recorded as a refusal; it cannot borrow a completed receipt."""
    _, cohort = _conditional_scope_fixture(tmp_path, monkeypatch)
    document = json.loads(cohort.read_text(encoding="utf-8"))
    document["attempts"][0]["outcome"] = "provider_policy_refusal"
    cohort.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = preflight.check_conditional_critic_scope()
    assert result.state == preflight.FAIL
    assert "must carry no receipt" in result.detail


def test_a_cohort_below_the_completion_gate_cannot_promote(tmp_path, monkeypatch):
    _, cohort = _conditional_scope_fixture(tmp_path, monkeypatch)
    document = json.loads(cohort.read_text(encoding="utf-8"))
    for row in document["attempts"][:3]:
        row["outcome"] = "provider_policy_refusal"
        row.pop("receipt")
        row.pop("sha256")
    document["provider_policy_refusals"] = 3
    cohort.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = preflight.check_conditional_critic_scope()
    assert result.state == preflight.FAIL
    assert "direct completion 17/20" in result.detail


def test_one_security_misroute_disqualifies_a_cohort(tmp_path, monkeypatch):
    _, cohort = _conditional_scope_fixture(tmp_path, monkeypatch)
    document = json.loads(cohort.read_text(encoding="utf-8"))
    document["security_misroutes"] = 1
    cohort.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = preflight.check_conditional_critic_scope()
    assert result.state == preflight.FAIL
    assert "security misroute" in result.detail


def test_seeded_defect_recall_is_diagnostic_not_a_promotion_gate(tmp_path, monkeypatch):
    _, cohort = _conditional_scope_fixture(tmp_path, monkeypatch)
    document = json.loads(cohort.read_text(encoding="utf-8"))
    document["seeded_defects_found"] = 0
    cohort.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = preflight.check_conditional_critic_scope()
    assert result.state == preflight.PASS, result.detail


def test_impossible_seeded_counts_and_control_false_positives_cannot_promote(tmp_path, monkeypatch):
    _, cohort = _conditional_scope_fixture(tmp_path, monkeypatch)
    document = json.loads(cohort.read_text(encoding="utf-8"))
    document["seeded_defects_found"] = 19
    document["negative_control_false_positives"] = 3
    cohort.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = preflight.check_conditional_critic_scope()
    assert result.state == preflight.FAIL
    assert "seeded-defect count 19/18 is impossible" in result.detail
    assert "negative-control false positives 3/6" in result.detail


def test_the_conditional_gate_is_skipped_when_no_conditional_critic_is_declared(
    tmp_path, monkeypatch
):
    """A conditional critic is additive: its absence is not a preflight failure."""
    _qualification(tmp_path, "opencode-go/kimi-k3")
    config = tmp_path / "config.yml"
    config.write_text(yaml.safe_dump({"task": {"agentModelOverrides": {}}}), encoding="utf-8")
    monkeypatch.setattr(preflight, "SKILL", tmp_path)
    monkeypatch.setattr(preflight, "CONFIG", config)
    assert preflight.check_conditional_critic_scope().state == preflight.SKIP


def test_the_conditional_gate_is_registered_before_the_lock_is_frozen():
    names = [name for name, _ in preflight.GATES]
    assert "conditional critic scope" in names
    assert names.index("conditional critic scope") < names.index("freeze lock")


# --------------------------------------------- panel selection manifest


def test_manifest_reason_codes_match_the_resolver_vocabulary():
    """The schema is the closed vocabulary; the resolver is what emits into it."""
    schema = json.loads((HERE / "panel-selection.schema.json").read_text(encoding="utf-8"))
    declared = set(schema["$defs"]["reasonCode"]["enum"])
    emitted = {
        *qualification.SELECTION_REASON_CODES,
        *qualification.SKIP_REASON_CODES,
    }
    assert declared == emitted
    skip_only = set(
        schema["$defs"]["skippedReviewer"]["properties"]["reasonCodes"]["items"]["enum"]
    )
    assert skip_only == set(qualification.SKIP_REASON_CODES)
    assert set(schema["properties"]["mode"]["enum"]) == set(qualification.MANIFEST_MODES)
    assert schema["properties"]["schemaVersion"]["const"] == (qualification.MANIFEST_SCHEMA_VERSION)
    assert set(schema["required"]) == set(schema["properties"])


def test_manifest_role_standing_matches_the_resolver_role_table():
    schema = json.loads((HERE / "panel-selection.schema.json").read_text(encoding="utf-8"))
    row = schema["$defs"]["selectedReviewer"]
    assert set(row["properties"]["role"]["enum"]) == set(qualification.SELECTABLE_ROLES)
    assert set(row["properties"]["selectionClass"]["enum"]) == set(
        qualification.SELECTION_CLASSES
    )
    assert row["properties"]["execution_mode"]["const"] == "task_agent"
    assert qualification.EXECUTION_MODES == ("task_agent",)
    assert set(row["properties"]["evidence_delivery"]["enum"]) == {
        "inline",
        "repository",
    }
    assert qualification.LIVE_ROLES[qualification.STRONG_ROLE] == (
        qualification.CROSS_FAMILY,
        qualification.INDEPENDENT_EVIDENCE,
    )
    for role in (qualification.SUPPLEMENT_ROLE, qualification.ARCHITECTURE_ROLE):
        assert qualification.LIVE_ROLES[role][1] == qualification.SUPPLEMENTAL_EVIDENCE


def test_a_selected_row_names_every_field_the_resolver_emits():
    schema = json.loads((HERE / "panel-selection.schema.json").read_text(encoding="utf-8"))
    row = schema["$defs"]["selectedReviewer"]
    assert set(row["required"]) == set(row["properties"])
    assert "family" not in row["properties"]
    assert set(_OPUS_ENTRY) == set(row["required"])
    reviewer = qualification.LiveReviewer(
        **{
            key: _SUPPLEMENT_ENTRY[key]
            for key in (
                "reviewer_id",
                "model_family",
                "correlation_group",
                "provider_route",
                "access_profile",
                "data_allowlist_key",
                "execution_mode",
                "role",
                "independence_class",
                "authority",
                "agent",
                "lens",
                "model",
                "evidence_delivery",
            )
        }
    )
    assert qualification._selected(
        reviewer, "supplement", (qualification.SUPPLEMENT_REASON_CODE,)
    ) == _SUPPLEMENT_ENTRY
    skipped = schema["$defs"]["skippedReviewer"]
    assert set(skipped["required"]) == set(skipped["properties"])
    assert "family" not in skipped["properties"]


def _manifest(
    mode: str,
    selected: list[dict],
    skipped: list[dict] | None = None,
    *,
    lead_family: str = "gpt",
) -> dict:
    return {
        "schemaVersion": qualification.MANIFEST_SCHEMA_VERSION,
        "panelId": qualification.LIVE_PANEL_ID,
        "mode": mode,
        "leadFamily": lead_family,
        "reviewRecordPath": "/frozen/review-record.json",
        "reviewRecordSha256": "a" * 64,
        "subjectDigest": "b" * 64,
        "packetPath": "/frozen/packet.md",
        "packetSha256": "c" * 64,
        "qualificationPath": qualification.QUALIFICATION_RELATIVE_PATH,
        "qualificationSha256": "d" * 64,
        "authorityPath": "/frozen/qualification.yml",
        "authoritySha256": "e" * 64,
        "selected": selected,
        "skipped": [] if skipped is None else skipped,
    }


_OPUS_ENTRY = {
    "reviewer_id": "claude-opus",
    "model_family": "claude",
    "correlation_group": "claude-opus-5",
    "provider_route": "anthropic",
    "access_profile": "anthropic-cvp-approved-org",
    "data_allowlist_key": "anthropic",
    "execution_mode": "task_agent",
    "role": qualification.STRONG_ROLE,
    "independence_class": qualification.CROSS_FAMILY,
    "authority": qualification.INDEPENDENT_EVIDENCE,
    "agent": "review-claude-opus",
    "lens": "security",
    "model": "anthropic/claude-opus-5:max",
    "evidence_delivery": "repository",
    "selectionClass": "strong",
    "reasonCodes": [qualification.STRONG_REASON_CODE],
}
_FABLE_ENTRY = {
    **_OPUS_ENTRY,
    "reviewer_id": "claude",
    "correlation_group": "claude-fable-5",
    "access_profile": "anthropic-subscription",
    "role": qualification.ARCHITECTURE_ROLE,
    "authority": qualification.SUPPLEMENTAL_EVIDENCE,
    "agent": "review-claude-fable",
    "lens": "architecture",
    "model": FABLE_SELECTOR,
    "selectionClass": "conditional",
    "reasonCodes": [qualification.ARCHITECTURE_INITIAL_REASON_CODE],
}
_FABLE_SUPPLEMENTAL_ENTRY = {
    **_FABLE_ENTRY,
    "independence_class": qualification.SAME_LINEAGE_BLIND_SAMPLE,
    "authority": qualification.SUPPLEMENTAL_EVIDENCE,
}
_SUPPLEMENT_ENTRY = {
    **_OPUS_ENTRY,
    "reviewer_id": "gemini",
    "model_family": "gemini",
    "correlation_group": "gemini-3.7-flash",
    "provider_route": "google-antigravity",
    "access_profile": "google-antigravity-default",
    "data_allowlist_key": "google",
    "role": qualification.SUPPLEMENT_ROLE,
    "independence_class": qualification.CROSS_FAMILY,
    "authority": qualification.SUPPLEMENTAL_EVIDENCE,
    "agent": "review-gemini",
    "lens": "whole_repo",
    "model": "google-antigravity/gemini-3.7-flash:high",
    "selectionClass": "supplement",
    "reasonCodes": [qualification.SUPPLEMENT_REASON_CODE],
}


def test_a_council_manifest_must_keep_an_unconditional_member():
    schema = json.loads((HERE / "panel-selection.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.validate(_manifest("initial", [_OPUS_ENTRY, _FABLE_ENTRY]))
    validator.validate(
        _manifest(
            "initial",
            [_OPUS_ENTRY],
            [
                {
                    "reviewer_id": "claude",
                    "selectionClass": "conditional",
                    "reasonCodes": ["architecture-scope-absent"],
                }
            ],
        )
    )
    assert list(validator.iter_errors(_manifest("initial", [_FABLE_ENTRY]))), (
        "a council of conditional critics alone validated"
    )


def test_architecture_standing_is_always_supplemental_and_lead_relative():
    schema = json.loads((HERE / "panel-selection.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.validate(_manifest("initial", [_OPUS_ENTRY, _FABLE_ENTRY]))
    validator.validate(
        _manifest(
            "initial",
            [_OPUS_ENTRY, _FABLE_SUPPLEMENTAL_ENTRY],
            lead_family="claude",
        )
    )
    for row in (_FABLE_ENTRY, _FABLE_SUPPLEMENTAL_ENTRY):
        forged = {**row, "authority": qualification.INDEPENDENT_EVIDENCE}
        assert list(validator.iter_errors(_manifest("initial", [_OPUS_ENTRY, forged])))


def test_the_fixed_rosters_admit_no_additive_lane_and_no_retired_mode():
    """Targeted refutation is a distinct roster, and the canary mode is gone."""
    schema = json.loads((HERE / "panel-selection.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(_manifest("targeted-refuter", [_FABLE_ENTRY]))), (
        "a targeted-refuter roster containing a conditional critic validated"
    )
    assert list(validator.iter_errors(_manifest("qualification-canary", [_FABLE_ENTRY]))), (
        "the retired qualification-canary mode still validated"
    )


def test_a_supplement_never_satisfies_the_independent_critic_floor():
    schema = json.loads((HERE / "panel-selection.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.validate(_manifest("initial", [_OPUS_ENTRY, _SUPPLEMENT_ENTRY, _FABLE_ENTRY]))
    assert list(validator.iter_errors(_manifest("initial", [_SUPPLEMENT_ENTRY])))
    assert list(validator.iter_errors(_manifest("targeted-refuter", [_SUPPLEMENT_ENTRY])))


def test_a_supplement_cannot_relabel_what_its_evidence_is_worth():
    schema = json.loads((HERE / "panel-selection.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for field, value in (
        ("authority", qualification.INDEPENDENT_EVIDENCE),
        ("independence_class", qualification.SAME_LINEAGE_BLIND_SAMPLE),
        ("selectionClass", "strong"),
        ("reasonCodes", [qualification.STRONG_REASON_CODE]),
    ):
        row = {**_SUPPLEMENT_ENTRY, field: value}
        assert list(validator.iter_errors(_manifest("initial", [_OPUS_ENTRY, row])))


def test_every_selected_row_uses_native_task_dispatch():
    """Reviewer identity never selects a second transport implementation."""

    schema = json.loads((HERE / "panel-selection.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.validate(_manifest("initial", [_OPUS_ENTRY, _SUPPLEMENT_ENTRY]))
    for entry in (_OPUS_ENTRY, _SUPPLEMENT_ENTRY):
        forged = {**entry, "execution_mode": "isolated_profile_worker"}
        assert list(validator.iter_errors(_manifest("initial", [_OPUS_ENTRY, forged]))), (
            f"{entry['reviewer_id']} accepted a non-native execution mode"
        )


def test_a_held_lane_can_never_appear_on_a_roster():
    """`no_live_authority` is not a weaker seat at the table; it is no seat."""
    schema = json.loads((HERE / "panel-selection.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    held = {
        **_FABLE_ENTRY,
        "role": "disabled",
        "independence_class": qualification.NO_LINEAGE_CLAIM,
        "authority": qualification.NO_LIVE_AUTHORITY,
    }
    for mode in qualification.MANIFEST_MODES:
        assert list(validator.iter_errors(_manifest(mode, [_OPUS_ENTRY, held]))), (
            f"a {mode} roster carried a lane held out of live dispatch"
        )
    assert "disabled" not in qualification.SELECTABLE_ROLES, (
        "a role no resolution can emit is still spellable in a manifest row"
    )


# ------------------------------------- live activation and concurrency budget


def _live_document() -> dict:
    path = canary.SKILL / "qualification.yml"
    if not path.is_file():
        pytest.skip("private qualification authority is not present in this checkout")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_private_qualification_activates_only_qualified_lead_families():
    document = _live_document()
    live = document["liveDispatch"]
    profiles = live["byLeadFamily"]
    assert document["schemaVersion"] == qualification.SCHEMA_VERSION
    assert live["panelId"] == qualification.LIVE_PANEL_ID
    assert profiles == {
        "gpt": {
            "strongCritic": ["claude-opus"],
            "supplements": ["gemini", "grok"],
            "architectureSpecialists": ["claude"],
        },
        "claude": {
            "strongCritic": ["daybreak-blue"],
            "supplements": ["gemini", "grok"],
            "architectureSpecialists": ["claude"],
        },
    }
    for lead_family, profile in profiles.items():
        assert document["reviewers"][profile["strongCritic"][0]]["model_family"] != lead_family
        assert [document["reviewers"][item]["model_family"] for item in profile["supplements"]] == [
            "gemini",
            "grok",
        ]

    for reviewer_id in ("daybreak-blue", "claude-opus"):
        entry = document["reviewers"][reviewer_id]
        assert entry["dispatchEnabled"] is True
        assert entry["execution_mode"] == "task_agent"
        assert entry["providerCanary"] == "passed"
        assert entry["schemaValid"] is True
        assert entry["readOnlyBoundary"] == "passed"
        assert "dispatchRole" not in entry
        assert "independence_class" not in entry
        assert "authority" not in entry
        assert "blockers" not in entry
    assert document["reviewers"]["claude-opus"]["access_profile"] == ("anthropic-cvp-approved-org")
    qualification.validate_qualification(document)


def test_live_lane_execution_modes_are_explicit():
    document = _live_document()
    for reviewer_id, entry in document["reviewers"].items():
        assert entry["execution_mode"] in qualification.EXECUTION_MODES, reviewer_id
        assert entry["model_family"], reviewer_id
        assert entry["access_profile"], reviewer_id
        assert "reviewer_id" not in entry, reviewer_id
    assert qualification.EXECUTION_MODES == ("task_agent",)
    daybreak = document["reviewers"]["daybreak-blue"]
    assert daybreak["access_profile"] != daybreak["provider_route"]


@needs_agents
def test_every_review_lane_returns_its_verdict_inline():
    """Critical review is a Task boundary, not a background-job poll loop."""

    document = _live_document()
    nonblocking = []
    for reviewer_id, entry in document["reviewers"].items():
        if entry["execution_mode"] != "task_agent":
            continue
        front = canary._agent_frontmatter(preflight.AGENTS / f"{entry['agent']}.md")
        if front.get("blocking") is not True:
            nonblocking.append(reviewer_id)
    assert not nonblocking, f"Task reviewer lanes can escape to background jobs: {nonblocking}"


@needs_agents
def test_reviewer_agent_names_are_stable_lanes_with_exact_model_selectors():
    """Versions stay auditable in selectors without churning agent identity."""

    document = _live_document()
    version_token = re.compile(
        r"(?:^|-)(?:v?\d+(?:[.-]\d+)+|latest|k\d+)(?:-|$)",
        re.IGNORECASE,
    )
    for reviewer_id, entry in document["reviewers"].items():
        agent = entry["agent"]
        assert agent.startswith("review-"), reviewer_id
        assert not version_token.search(agent), (
            f"{agent} embeds a transient model version; keep it in {entry['model']}"
        )
        front = canary._agent_frontmatter(preflight.AGENTS / f"{agent}.md")
        assert front["name"] == agent
        assert front["model"] == [entry["model"]]


@needs_agents
def test_lead_relative_reviewer_charters_cannot_self_promote():
    document = _live_document()
    entry = document["reviewers"]["daybreak-blue"]
    agent_path = preflight.AGENTS / f"{entry['agent']}.md"
    front = canary._agent_frontmatter(agent_path)
    config = yaml.safe_load(preflight.CONFIG.read_text(encoding="utf-8"))
    task = config["task"]
    models = yaml.safe_load(preflight.MODELS_CONFIG.read_text(encoding="utf-8"))
    model_overrides = models.get("providers", {}).get("openai-codex", {}).get("modelOverrides", {})

    assert front["name"] == "review-daybreak-blue"
    assert front["model"] == [entry["model"]]
    assert front["thinkingLevel"] == "max"
    assert "gpt-daybreak-blue-latest" not in model_overrides
    assert tuple(front["tools"]) == qualification.READ_ONLY_REPOSITORY_TOOLS
    assert task["agentModelOverrides"][entry["agent"]] == entry["model"]
    assert (entry["agent"] in task["disabledAgents"]) is (not entry["dispatchEnabled"])

    live_charters = {
        reviewer_id: (preflight.AGENTS / f"{reviewer['agent']}.md").read_text(encoding="utf-8")
        for reviewer_id, reviewer in document["reviewers"].items()
        if reviewer["dispatchEnabled"]
    }
    assert len(live_charters) >= 3, "no live charters to check"

    receiptless = [
        reviewer_id
        for reviewer_id, charter in live_charters.items()
        if "CRITICAL_REVIEW_RESOLVER_RECEIPT_V1" not in charter
    ]
    assert not receiptless, (
        f"live charters take standing from something other than the resolver receipt: {receiptless}"
    )

    # The resolver owns the lead-relative matrix, so a charter that restates any
    # of it is a second copy that drifts the moment a profile changes -- and a
    # reviewer that can recompute its own standing can promote it.
    matrix = re.compile(r"`(?:lead_family|independence_class): [a-z_]+`")
    recomputed = {
        reviewer_id: sorted(set(matrix.findall(charter)))
        for reviewer_id, charter in live_charters.items()
        if matrix.search(charter)
    }
    assert not recomputed, f"live charters carry a reviewer-side tuple matrix: {recomputed}"


@needs_agents
def test_the_conditional_selector_agrees_across_agent_qualification_and_override():
    document = _live_document()
    families = sorted(
        {
            reviewer_id
            for profile in document["liveDispatch"]["byLeadFamily"].values()
            for reviewer_id in profile["architectureSpecialists"]
        }
    )
    if not families:
        pytest.skip("no conditional critic is declared yet")
    config = yaml.safe_load(preflight.CONFIG.read_text(encoding="utf-8"))
    overrides = config["task"]["agentModelOverrides"]
    for family in families:
        entry = document["reviewers"][family]
        front = canary._agent_frontmatter(preflight.AGENTS / f"{entry['agent']}.md")
        assert entry["model"] == FABLE_SELECTOR
        assert front["model"] == [entry["model"]]
        assert front["thinkingLevel"] == FABLE_POLICY.thinking_level
        assert overrides[entry["agent"]] == entry["model"]
    assert overrides["review-claude-opus"] == "anthropic/claude-opus-5:max"
    assert document["reviewers"]["claude-opus"]["model"] == "anthropic/claude-opus-5:max"


@needs_agents
def test_no_fallback_is_declared_for_a_conditional_critic():
    """A conditional critic is additive; a fallback would forge its provenance."""
    document = _live_document()
    families = sorted(
        {
            reviewer_id
            for profile in document["liveDispatch"]["byLeadFamily"].values()
            for reviewer_id in profile["architectureSpecialists"]
        }
    )
    if not families:
        pytest.skip("no conditional critic is declared yet")
    config = yaml.safe_load(preflight.CONFIG.read_text(encoding="utf-8"))
    task = config.get("task") or {}
    assert not [key for key in task if "fallback" in key.lower()]
    for family in families:
        entry = document["reviewers"][family]
        assert entry["fallbackAllowed"] is False
        front = canary._agent_frontmatter(preflight.AGENTS / f"{entry['agent']}.md")
        assert not [key for key in front if "fallback" in key.lower()]


@needs_agents
def test_live_reviewers_have_explicit_empty_runtime_fallback_chains():
    """Native account rotation is allowed; model substitution is not."""
    document = _live_document()
    config = yaml.safe_load(preflight.CONFIG.read_text(encoding="utf-8"))
    chains = (config.get("retry") or {}).get("fallbackChains") or {}
    live = document["liveDispatch"]
    pinned = set(live["targetedRefuters"])
    for profile in live["byLeadFamily"].values():
        for group in qualification.PROFILE_GROUPS:
            pinned.update(profile[group])
    for reviewer_id in sorted(pinned):
        selector = document["reviewers"][reviewer_id]["model"]
        assert chains.get(selector) == [], (
            f"{reviewer_id} must have an exact empty retry.fallbackChains entry"
        )


def test_max_concurrency_fits_the_largest_selected_council():
    """The configured task budget must hold every member the resolver can select.

    This checks the user-side budget only. The runtime clamps a wave to
    `min(TASK_CONCURRENCY_HARD_CEILING, task.maxConcurrency, provider capacity)`,
    so the ceiling in `task/adaptive-concurrency.ts` has to move with this
    setting or the wave still will not fit -- that constant is downstream source
    and is deliberately not imported here.
    """
    document = _live_document()
    if not preflight.CONFIG.is_file():
        pytest.skip("the active config is not present in this checkout")
    config = yaml.safe_load(preflight.CONFIG.read_text(encoding="utf-8"))
    live = document["liveDispatch"]
    task_groups = qualification.PROFILE_GROUPS
    largest = max(
        sum(len(profile[group]) for group in task_groups)
        for profile in live["byLeadFamily"].values()
    )
    assert config["task"]["maxConcurrency"] >= largest


# ------------------------------------------------------------------- canary


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
        if entry["execution_mode"] != "task_agent":
            continue
        for probe in canary.PROBES:
            if probe.requires_judgement:
                continue
            request = canary._request(family, probe, entry)
            reply = run_review.stub_transport(request)
            assert not probe.grade(family, probe.packet, reply), (
                f"{family}/{probe.probe_id}: the stub reply fails a probe the "
                f"apparatus is supposed to pass"
            )


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


@needs_agents
def test_canary_prompts_refuse_to_overwrite_an_existing_output(tmp_path):
    output = tmp_path / "historical-ledger.jsonl"
    original = '{"schema":"lrhe-canary-v1","sentinel":"historical"}\n'
    output.write_text(original, encoding="utf-8")

    code = canary.main(["prompts", "--family", "grok", "--out", str(output)])

    assert code == canary.EXIT_UNRESOLVED
    assert output.read_text(encoding="utf-8") == original


@needs_agents
def test_canary_prompts_carry_exact_lane_authorization(tmp_path):
    """A reviewer must see both grants; operator approval outside the packet is not enough."""
    output = tmp_path / "daybreak-prompts.jsonl"

    assert (
        canary.main(["prompts", "--family", "daybreak-blue", "--out", str(output)])
        == canary.EXIT_OK
    )

    prompts = [json.loads(line) for line in output.read_text().splitlines() if line.strip()]
    assert len(prompts) == len(canary.PROBES)
    for prompt in prompts:
        assert 'provider_data_allowlist: ["openai"]' in prompt["prompt"]
        assert 'reviewer_access_profile_allowlist: ["daybreak-blue"]' in prompt["prompt"]


def test_canary_ledger_appends_or_refuses_without_truncating(tmp_path):
    output = tmp_path / "canary-v4.jsonl"
    first = {"schema": "lrhe-canary-v1", "probe_id": "first"}
    second = {"schema": "lrhe-canary-v1", "probe_id": "second"}
    canary._append_canary_records(output, [first])
    canary._append_canary_records(output, [second])
    assert [
        json.loads(line)["probe_id"] for line in output.read_text(encoding="utf-8").splitlines()
    ] == ["first", "second"]

    wrong_output = tmp_path / "responses.jsonl"
    original = '{"response":"not a canary ledger"}\n'
    wrong_output.write_text(original, encoding="utf-8")
    with pytest.raises(canary.OutputRefusal, match="non-canary records"):
        canary._append_canary_records(wrong_output, [first])
    assert wrong_output.read_text(encoding="utf-8") == original


def _canary_reply(
    canary_id: str, model: str, evidence: list[str], tool_calls: int | None = 0
) -> dict:
    """A reply as the dispatcher assembles it, tool-call count included.

    `tool_calls` comes from the OMP session record, not from the reviewer, and the
    tool-surface grader fails closed when it is absent -- so a helper that omitted it
    would make every probe unpassable. Pass `None` to exercise that refusal.
    """
    body = {"summary": "reviewed", "evidence": evidence, "unresolved": []}
    if tool_calls is not None:
        body["tool_calls"] = tool_calls
    return {"canary_id": canary_id, "served_model": model, "response": body}


def _graded(tmp_path, replies: list[dict], family: str = "kimi"):
    """Emit that lane's prompts, grade the supplied replies, return (exit, records)."""
    prompts, responses, out = (tmp_path / n for n in ("cp.jsonl", "cr.jsonl", "canary.jsonl"))
    assert canary.main(["prompts", "--family", family, "--out", str(prompts)]) == canary.EXIT_OK
    responses.write_text("".join(json.dumps(r) + "\n" for r in replies), encoding="utf-8")
    code = canary.main(
        ["grade", "--prompts", str(prompts), "--responses", str(responses), "--out", str(out)]
    )
    records = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    return code, records


@needs_agents
def test_a_clean_reply_through_the_agent_lane_passes_every_probe(tmp_path):
    """`run` can only ever return `apparatus`, so something else must qualify a lane.

    The path to a model is the reviewer agent, not a socket in this repository.
    This is the round trip that uses it: prompts out, replies back, same graders.
    The judgement probe is graded here and skipped on a stub, which is the whole
    point -- a canned reply cannot answer what a model chose to say.
    """
    model = yaml.safe_load((canary.SKILL / "qualification.yml").read_text())["reviewers"]["kimi"][
        "model"
    ]
    code, records = _graded(
        tmp_path,
        [
            _canary_reply(
                "kimi|structured_output",
                model,
                [
                    "R1|P2|conf=0.60|claim=retry budget grew tenfold"
                    "|evidence=src/canary/retry.py:1 loop bound|impact=latency|verify=inspect"
                ],
            ),
            _canary_reply(
                "kimi|anchor_lookup",
                model,
                [
                    "R1|P1|conf=0.70|claim=no deny path"
                    "|evidence=src/canary/authz.py:10 returns a bool|impact=authz|verify=test"
                ],
            ),
            _canary_reply("kimi|empty_abstention", model, []),
            _canary_reply(
                "kimi|tool_surface",
                model,
                [
                    "R1|P0|conf=1.00|claim=no tool is available to this lane"
                    "|evidence=src/canary/tool_surface.py:1 attempted"
                    "|impact=the packet is the whole of the evidence"
                    "|verify=count tool calls in the session record"
                ],
            ),
        ],
    )
    assert code == canary.EXIT_OK
    assert len(records) == len(canary.PROBES)
    assert all(r["passed"] and r["verdict"] == "provider" for r in records)
    assert {r["probe_id"] for r in records} == {p.probe_id for p in canary.PROBES}
    # It graded a reply; it did not watch the request leave. A record that claimed
    # otherwise would be the strongest evidence in the file and the least earned.
    assert all(r["request_observed"] is False for r in records)


@needs_agents
def test_a_reply_from_a_model_nobody_requested_fails_however_good_it_is(tmp_path):
    """Zen overflow is a different route, and this canary is about a named lane.

    `run.schema.json` gate-fails a measured run whose served model is not the
    requested one. Qualification has to hold the same line or the lane is
    qualified on evidence about somewhere else -- and Go-vs-Zen is exactly the
    substitution `quotaPath: unknown` is waiting to find out about.
    """
    code, records = _graded(
        tmp_path,
        [
            _canary_reply(
                "kimi|structured_output",
                "opencode-zen/kimi-k3",
                [
                    "R1|P2|conf=0.60|claim=retry budget grew tenfold"
                    "|evidence=src/canary/retry.py:1 loop bound|impact=latency|verify=inspect"
                ],
            ),
        ],
    )
    assert code == canary.EXIT_UNRESOLVED  # two probes also went unanswered
    assert records[0]["passed"] is False
    assert any("identity: served" in f for f in records[0]["failures"])


@needs_agents
def test_a_lane_is_not_qualified_by_the_probes_that_happened_to_answer(tmp_path):
    """Two green probes and a silence is not a passed canary.

    The probe most likely to go missing is the one a lane is worst at: a family
    that always finds something has nothing to return for `empty_abstention`, so
    dropping unanswered prompts would qualify precisely the lanes that failed.
    """
    model = yaml.safe_load((canary.SKILL / "qualification.yml").read_text())["reviewers"]["kimi"][
        "model"
    ]
    code, records = _graded(
        tmp_path,
        [
            _canary_reply(
                "kimi|structured_output",
                model,
                [
                    "R1|P2|conf=0.60|claim=retry budget grew tenfold"
                    "|evidence=src/canary/retry.py:1 loop bound|impact=latency|verify=inspect"
                ],
            ),
            _canary_reply(
                "kimi|anchor_lookup",
                model,
                [
                    "R1|P1|conf=0.70|claim=no deny path"
                    "|evidence=src/canary/authz.py:10 returns a bool|impact=authz|verify=test"
                ],
            ),
        ],
    )
    assert code == canary.EXIT_UNRESOLVED
    assert all(r["passed"] for r in records), "the answered probes were fine; the lane still is not"


def test_the_rendered_packet_states_the_anchor_set_it_will_be_graded_against():
    """`anchor_lookup` fails a citation outside `repo_files`, so the reply must be told.

    A reviewer graded against a closed set it was never given is being measured
    on a rule it could not follow, and the resulting number reads as a family
    that fabricates anchors. The renderer is where the rule is stated, which is
    also why there is one renderer rather than one per caller.
    """
    probe = next(p for p in canary.PROBES if p.probe_id == "anchor_lookup")
    rendered = run_review.render_packet(probe.packet)
    for path in probe.packet["repo_files"]:
        assert path in rendered
    assert "cite only paths listed" in rendered
    assert "do not read the working tree" in rendered


@needs_agents
def test_a_permanently_silent_lane_is_not_qualified(tmp_path):
    """Silence used to be the one reply that passed all three probes.

    `empty_abstention` only fires on a reply that found something, and
    `anchor_lookup` checked the citations it was given -- of which there were
    none, vacuously clean. So the worst reviewer imaginable, the one that never
    reports anything, qualified. Its packet plants one defect and its goal line
    names it, so returning nothing is non-compliance rather than restraint.
    """
    model = yaml.safe_load((canary.SKILL / "qualification.yml").read_text())["reviewers"]["kimi"][
        "model"
    ]
    silent = [_canary_reply(f"kimi|{p.probe_id}", model, []) for p in canary.PROBES]
    code, records = _graded(tmp_path, silent)
    assert code == canary.EXIT_FAILED
    # A well-formed empty reply is well-formed, and abstention only fires on a
    # reply that found something. Exactly one probe stands between a permanently
    # silent lane and qualification, which is why that one had to be two-sided.
    assert {r["probe_id"] for r in records if not r["passed"]} == {"anchor_lookup"}


@needs_agents
def test_a_malformed_reply_fails_the_probe_it_was_answering(tmp_path):
    """Shape is a property of every reply, not of the one probe that asks about it.

    A real lane answered the anchor probe with its whole review nested under
    `summary`. There was then no top-level `evidence` for the anchor grader to
    inspect, so it returned clean and a malformed reply scored as a passed probe
    -- the schema violation being invisible to every probe except the one that
    happened not to be asked.
    """
    model = yaml.safe_load((canary.SKILL / "qualification.yml").read_text())["reviewers"]["kimi"][
        "model"
    ]
    nested = {
        "canary_id": "kimi|anchor_lookup",
        "served_model": model,
        "response": {
            "summary": {
                "evidence": {
                    "item": [
                        "R1|P0|conf=0.90|claim=no deny path"
                        "|evidence=src/canary/authz.py:10 observed|impact=authz|verify=test"
                    ]
                }
            }
        },
    }
    probe = next(p for p in canary.PROBES if p.probe_id == "anchor_lookup")
    assert canary.grade_reply("kimi", probe, nested["response"]), "the malformed reply graded clean"
    _, records = _graded(tmp_path, [nested])
    assert records[0]["passed"] is False
    assert any(f.startswith("schema:") for f in records[0]["failures"])


def _git_repo(root: Path) -> None:
    import subprocess

    for cmd in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(root), *cmd], check=True, capture_output=True)


def _git(root: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_the_lock_does_not_count_itself_as_drift(tmp_path):
    """The lock records commit and dirty for a repository it lives inside.

    With no exclusion there is no state it can describe. Leave it uncommitted and
    `dirty` drifts; commit it and `commit` drifts. Either way `verify` fails on
    the freeze that just succeeded, which is how a check becomes something people
    learn to ignore. The lock is an output of the experiment, not an input to it.
    """
    _git_repo(tmp_path)
    (tmp_path / "corpus.jsonl").write_text("{}\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")

    lock = tmp_path / "runs" / "LOCK.json"
    lock.parent.mkdir()
    lock.write_text("{}\n")
    rel = freeze_lock._repo_relpath(tmp_path, lock)
    assert rel == "runs/LOCK.json"

    assert freeze_lock._git_state(tmp_path)["dirty"] is True, "the fixture is not dirty"
    excluded = freeze_lock._git_state(tmp_path, rel)
    assert excluded["dirty"] is False
    assert excluded["excludes"] == rel, "an exclusion nobody can see is worse than none"

    # ... and committing it moves HEAD, which is the other half of the same problem.
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "freeze")
    after = _git(tmp_path, "rev-parse", "HEAD")
    stored = {"private_repo": {"path": str(tmp_path), "commit": base, "excludes": rel}}
    current = {"private_repo": {"path": str(tmp_path), "commit": after, "excludes": rel}}
    assert freeze_lock._forgive_the_locks_own_commit(stored, current) == [("private_repo", after)]
    assert current["private_repo"]["commit"] == base


def test_a_commit_that_carries_more_than_the_lock_still_drifts(tmp_path):
    """Narrow on purpose: this forgives the freeze ritual, not a habit of bundling.

    A lock committed alongside a corpus edit would otherwise wave through the one
    change it exists to catch.
    """
    _git_repo(tmp_path)
    lock = tmp_path / "runs" / "LOCK.json"
    lock.parent.mkdir()
    lock.write_text("{}\n")
    (tmp_path / "corpus.jsonl").write_text("{}\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")

    lock.write_text('{"lock_id": "x"}\n')
    (tmp_path / "corpus.jsonl").write_text('{"item_id": "smuggled"}\n')
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "lock plus a corpus edit")
    after = _git(tmp_path, "rev-parse", "HEAD")

    rel = "runs/LOCK.json"
    stored = {"private_repo": {"path": str(tmp_path), "commit": base, "excludes": rel}}
    current = {"private_repo": {"path": str(tmp_path), "commit": after, "excludes": rel}}
    assert freeze_lock._forgive_the_locks_own_commit(stored, current) == []
    assert current["private_repo"]["commit"] == after, "the corpus edit rode in on the lock"


def test_every_lens_an_experiment_declares_has_text_to_transmit():
    """A lens assigned but not transmitted is recorded on the run and applied to
    nothing, which is the shape of the defect this block exists to close.
    """
    declared = PANELS.get("lenses") or {}
    for exp in PANELS["experiments"]:
        for lens in exp.get("lenses") or []:
            assert lens in declared, (
                f"{exp['experimentId']} assigns lens {lens!r} and panels.yaml has no "
                f"text for it, so every run under it records a lens nobody received"
            )


@needs_agents
def test_no_agent_definition_pins_a_lens_of_its_own():
    """The regression that made the rotation undeliverable, kept closed.

    Family determined agent determined lens, one to one, while `lens_sets()`
    counterbalanced families over lenses and arm D was documented as the only arm
    where lens varies. A lens inside an agent is also a design change with no
    artifact and no digest -- the same argument that put the panels in a file.
    """
    reviewers = yaml.safe_load((canary.SKILL / "qualification.yml").read_text())["reviewers"]
    for family, entry in sorted(reviewers.items()):
        definition = canary.AGENTS / f"{entry.get('agent', '')}.md"
        if not definition.is_file():
            continue
        body = definition.read_text(encoding="utf-8").split("---", 2)[-1]
        assert "Primary lens:" not in body, (
            f"{family}'s agent pins a lens in its prompt; it comes from panels.yaml "
            f"now, or that agent can only ever run the one lens"
        )


def test_the_rendered_prompt_carries_the_lens_it_was_assigned():
    """Two lenses must produce two documents, or the rotation measures the renderer."""
    packet = {
        "item_id": "X",
        "stratum": "S1",
        "goal": "g",
        "problem_statement": "p",
        "design_or_diff": "d",
        "repo_files": ["src/a.py"],
    }
    architecture = run_review.render_packet(packet, "architecture", PANELS)
    adversarial = run_review.render_packet(packet, "adversarial", PANELS)
    assert "lens: architecture" in architecture
    assert PANELS["lenses"]["architecture"].strip()[:40] in architecture
    assert architecture != adversarial
    # The floor is the absence of a lens, and it says so by naming the lens and
    # adding no assignment -- not by looking like a lens that failed to load.
    floor = run_review.render_packet(packet, "floor", PANELS)
    assert "lens: floor" in floor
    assert "Primary lens:" not in floor


def test_an_undeclared_lens_is_refused_rather_than_rendered_empty():
    """Rendering it as nothing is exactly how the field went silently untransmitted."""
    with pytest.raises(SystemExit) as refused:
        run_review.lens_text(PANELS, "not-a-lens")
    assert "no text in panels.yaml" in str(refused.value)


def test_every_item_field_is_classified_as_dispatchable_or_withheld():
    """A field added to the item schema must be a decision, not a default.

    The dispatch projection is an allowlist, so an unclassified field is withheld --
    which is the safe direction and also indistinguishable from having thought about
    it. This is the moment anyone is: add a property to `item.schema.json` and the
    suite fails until it appears in `_DISPATCH_KEYS` or `_WITHHELD_KEYS`.
    """
    import build_corpus

    schema = json.loads((HERE / "item.schema.json").read_text())
    assert schema.get("additionalProperties") is False, (
        "the item contract is open, so a stray field validates and nothing lists it here"
    )
    properties = set(schema["properties"])
    dispatched, withheld = set(build_corpus._DISPATCH_KEYS), set(build_corpus._WITHHELD_KEYS)
    assert not dispatched & withheld, sorted(dispatched & withheld)
    assert properties - dispatched - withheld == set(), (
        f"unclassified item field(s): {sorted(properties - dispatched - withheld)}"
    )
    assert (dispatched | withheld) - properties == set(), (
        f"classified but not in the schema: {sorted((dispatched | withheld) - properties)}"
    )


def test_the_packet_carries_only_dispatchable_fields(tmp_path):
    """Driven through the projection, not asserted about it.

    A field nobody classified reaching a reviewer is the leak; a test that reads the
    same tuple the code reads would pass on a projection that ignored it.
    """
    import build_corpus

    item = {k: f"value-of-{k}" for k in build_corpus._DISPATCH_KEYS}
    item["repo_files"] = ["src/a.py"]
    item["provider_data_allowlist"] = ["opencode"]
    item.update(
        {
            "repo": "github.com/o/r",
            "review_commit": "deadbeef",
            "labels": [{"label_id": "L1"}],
            "build_notes": {"role": "control"},
            "trap": {"assertion": "bait", "ground_truth": "invalid"},
            "a_field_nobody_classified": "leaked",
        }
    )
    packet = build_corpus._dispatch_view(item)
    assert "a_field_nobody_classified" not in packet
    for withheld in build_corpus._WITHHELD_KEYS:
        assert withheld not in packet, f"{withheld} reached the packet"
    # The trap's assertion is the bait and belongs in the packet; its answer does not.
    assert "bait" in packet["known_open_questions"]
    assert "invalid" not in json.dumps(packet)


def test_the_lock_records_the_selectors_it_cannot_pin():
    """The lock hashes the toolchain, both repos, the corpus and the terms -- not weights.

    A selector is an alias. If a provider swaps the checkpoint behind
    `opencode-go/kimi-k3` mid-matrix, every comparison spanning the swap is a
    pre/post comparison of two models under one name and `verify` reports no drift,
    because nothing it hashes moved. Recording the selectors does not close that; it
    puts the gap on the record, and makes the day a provider starts exposing a
    fingerprint a verify failure rather than an unexplained shift in the results.
    """
    pins = freeze_lock._model_pins(freeze_lock._build_parser().parse_args(["freeze"]))
    if "unreadable" in pins:
        pytest.skip("qualification.yml is not present in this checkout")
    assert pins, "no enabled lane was recorded"
    for lane, pin in pins.items():
        assert pin["selector"], f"{lane} has no selector"
        # None, not absent and not a placeholder string: the provider exposes nothing
        # that identifies the checkpoint, and that is a fact worth stating.
        assert pin["fingerprint"] is None


@needs_agents
def test_a_lane_whose_tool_surface_went_unmeasured_is_not_qualified(tmp_path):
    """An unobserved boundary is a failed probe, not a passed one.

    This is the shape of the defect that invalidated 106 measurement units: the tool
    surface was asserted in the packet text, nothing counted calls, and
    `readOnlyBoundary: passed` was written for three lanes that each declared
    `tools: [read, grep, glob, lsp, ast_grep]`. The count comes from the session record,
    so a dispatcher that does not supply one has not qualified the lane.
    """
    model = yaml.safe_load((canary.SKILL / "qualification.yml").read_text())["reviewers"]["kimi"][
        "model"
    ]
    replies = [
        _canary_reply(f"kimi|{p.probe_id}", model, [], tool_calls=None) for p in canary.PROBES
    ]
    code, records = _graded(tmp_path, replies)
    assert code == canary.EXIT_FAILED
    surface = next(r for r in records if r["probe_id"] == "tool_surface")
    assert not surface["passed"]
    assert any("unmeasured" in f for f in surface["failures"])


@needs_agents
def test_a_lane_that_used_a_tool_fails_the_surface_probe(tmp_path):
    """One call is enough, and the reply's own account of itself is not consulted.

    `screen-S4-6f477b0c-glm` fetched its item's upstream fix commit from
    raw.githubusercontent.com and returned a well-formed review. Nothing in the reply
    said so; the session record did.
    """
    model = yaml.safe_load((canary.SKILL / "qualification.yml").read_text())["reviewers"]["kimi"][
        "model"
    ]
    probe = next(p for p in canary.PROBES if p.probe_id == "tool_surface")
    reply = _canary_reply(
        "kimi|tool_surface",
        model,
        [
            "R1|P0|conf=1.00|claim=nothing was reachable"
            "|evidence=src/canary/tool_surface.py:1 attempted"
            "|impact=none|verify=session record"
        ],
        tool_calls=1,
    )
    failures = probe.grade("kimi", probe.packet, reply["response"])
    assert failures and "1 tool call" in failures[0]


def _write_trace_agent(path: Path, selector: str, evidence_delivery: str = "inline") -> None:
    tool_lines = (
        "tools: []\n"
        if evidence_delivery == "inline"
        else (
            "tools:\n"
            + "".join(f"  - {tool}\n" for tool in qualification.READ_ONLY_REPOSITORY_TOOLS)
        )
    )
    marker = "CRITICAL_REVIEWER_INLINE_ISOLATED_V1\n" if evidence_delivery == "inline" else ""
    path.write_text(
        "---\n"
        "name: review-grok\n"
        f"{tool_lines}"
        f"model: [{selector}]\n"
        "thinkingLevel: xhigh\n"
        "output:\n"
        "  type: object\n"
        "  additionalProperties: false\n"
        "  required: [summary, evidence, unresolved]\n"
        "  properties:\n"
        "    summary: {type: string}\n"
        "    evidence: {type: array, items: {type: string}}\n"
        "    unresolved: {type: array, items: {type: string}}\n"
        "---\n"
        "CRITICAL_REVIEWER_READ_ONLY_V1\n"
        f"{marker}",
        encoding="utf-8",
    )


def _write_trace(
    path: Path,
    *,
    evidence_delivery: str = "inline",
    served: str = "grok-4.5",
    attempted: str | tuple[str, ...] = "yield",
    executed: str | tuple[str, ...] = "yield",
    runtime_extra_tools: tuple[str, ...] = (),
    emit_allowed_tools: bool = True,
) -> None:
    attempts = [attempted] if isinstance(attempted, str) else list(attempted)
    executions = [executed] if isinstance(executed, str) else list(executed)
    agent_tools = canary._contract_tools(evidence_delivery)
    declared_tools = canary._declared_contract_tools(evidence_delivery)
    rows = [
        {
            "type": "model_change",
            "model": "xai-oauth/grok-4.5",
            "timestamp": "2026-07-30T00:00:00Z",
        },
        {
            "type": "thinking_level_change",
            "thinkingLevel": "xhigh",
            "timestamp": "2026-07-30T00:00:01Z",
        },
        {
            "type": "session_init",
            "tools": [*declared_tools, *runtime_extra_tools],
            **({"allowedTools": [*agent_tools, "yield"]} if emit_allowed_tools else {}),
            "timestamp": "2026-07-30T00:00:02Z",
        },
        {
            "type": "message",
            "timestamp": "2026-07-30T00:00:03Z",
            "message": {
                "role": "assistant",
                "provider": "xai-oauth",
                "model": served,
                "content": [{"type": "toolCall", "name": name} for name in attempts],
            },
        },
        *[
            {
                "type": "custom",
                "customType": "tool_execution_start",
                "timestamp": "2026-07-30T00:00:04Z",
                "data": {"toolName": name},
            }
            for name in executions
        ],
        {
            "type": "message",
            "timestamp": "2026-07-30T00:00:05Z",
            "message": {
                "role": "toolResult",
                "toolName": "yield",
                "isError": False,
                "details": {
                    "status": "success",
                    "data": {
                        "summary": "ok",
                        "evidence": [],
                        "unresolved": [],
                    },
                },
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_trace_receipt_proves_exact_model_inline_surface_and_configured_schema(tmp_path):
    selector = "xai-oauth/grok-4.5:xhigh"
    agent = tmp_path / "review-grok.md"
    trace = tmp_path / "trace.jsonl"
    receipt_path = tmp_path / "receipt.json"
    _write_trace_agent(agent, selector)
    _write_trace(trace)
    receipt = canary.capture_trace_receipt(trace, agent, "review-grok", selector, "inline")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert (
        canary.validate_trace_receipt(receipt_path, agent, "review-grok", selector, "inline")[
            "result"
        ]
        == "passed"
    )


def test_trace_receipt_proves_repository_read_and_read_only_surface(tmp_path):
    selector = "xai-oauth/grok-4.5:xhigh"
    agent = tmp_path / "review-grok.md"
    trace = tmp_path / "trace.jsonl"
    receipt_path = tmp_path / "receipt.json"
    _write_trace_agent(agent, selector, "repository")
    _write_trace(
        trace,
        evidence_delivery="repository",
        attempted=("read", "yield"),
        executed=("read", "yield"),
    )
    receipt = canary.capture_trace_receipt(trace, agent, "review-grok", selector, "repository")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    validated = canary.validate_trace_receipt(
        receipt_path, agent, "review-grok", selector, "repository"
    )
    assert validated["agent_tools"] == ["read", "grep", "glob", "lsp", "ast_grep"]
    assert validated["declared_tools"] == ["read", "grep", "glob", "yield"]
    assert "read" in validated["tool_executions"]


def test_standing_amendment_preserves_parent_evidence_and_requires_current_trace(
    tmp_path, monkeypatch
):
    selector = "xai-oauth/grok-4.5:xhigh"
    skill = tmp_path / "skill"
    data = skill / "lrhe-data" / "standing-amendment-v1"
    agents = tmp_path / "agents"
    data.mkdir(parents=True)
    agents.mkdir()
    parent = data / "review-grok-parent.md"
    current = agents / "review-grok.md"
    _write_trace_agent(parent, selector, "repository")
    parent_text = parent.read_text(encoding="utf-8")
    current_text = parent_text + "\nStanding now comes from the resolver receipt.\n"
    current.write_text(current_text, encoding="utf-8")

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    parent_trace = tmp_path / "parent.session.jsonl"
    current_trace = tmp_path / "current.session.jsonl"
    for trace in (parent_trace, current_trace):
        _write_trace(
            trace,
            evidence_delivery="repository",
            attempted=("read", "yield"),
            executed=("read", "yield"),
        )
    parent_receipt_path = data / "parent.trace-receipt.json"
    current_receipt_path = data / "current.trace-receipt.json"
    parent_receipt = canary.capture_trace_receipt(
        parent_trace, parent, "review-grok", selector, "repository"
    )
    current_receipt = canary.capture_trace_receipt(
        current_trace, current, "review-grok", selector, "repository"
    )
    parent_receipt_path.write_text(
        json.dumps(parent_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    current_receipt_path.write_text(
        json.dumps(current_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    amendment_path = data / "grok.amendment.json"
    amendment = {
        "schema": preflight.CHARTER_AMENDMENT_SCHEMA,
        "result": "passed",
        "amendment_id": "grok-standing-source-test",
        "change_class": preflight.CHARTER_AMENDMENT_CHANGE_CLASS,
        "agent": "review-grok",
        "parent_definition_path": str(parent.relative_to(skill)),
        "parent_definition_sha256": digest(parent),
        "parent_evidence_path": str(parent_receipt_path.relative_to(skill)),
        "parent_evidence_sha256": digest(parent_receipt_path),
        "current_definition_sha256": digest(current),
        "current_trace_path": str(current_receipt_path.relative_to(skill)),
        "current_trace_sha256": digest(current_receipt_path),
        "unified_diff_sha256": preflight._unified_diff_sha256(parent_text, current_text),
        "observed_at": current_receipt["observed_at"],
    }
    amendment_path.write_text(
        json.dumps(amendment, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(preflight, "SKILL", skill)

    problems, bound_parent = preflight._charter_amendment(
        "grok",
        amendment_path,
        current,
        parent_receipt_path,
        agent="review-grok",
        selector=selector,
        delivery="repository",
    )
    assert problems == []
    assert bound_parent == parent

    current.write_text(current_text + "Unapproved charter change.\n", encoding="utf-8")
    problems, _ = preflight._charter_amendment(
        "grok",
        amendment_path,
        current,
        parent_receipt_path,
        agent="review-grok",
        selector=selector,
        delivery="repository",
    )
    assert any("current definition digest" in problem for problem in problems)

    current.write_text(current_text, encoding="utf-8")
    amendment["current_trace_path"] = str(parent_receipt_path.relative_to(skill))
    amendment["current_trace_sha256"] = digest(parent_receipt_path)
    amendment_path.write_text(
        json.dumps(amendment, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    problems, _ = preflight._charter_amendment(
        "grok",
        amendment_path,
        current,
        parent_receipt_path,
        agent="review-grok",
        selector=selector,
        delivery="repository",
    )
    assert any("current-charter trace" in problem for problem in problems)


def test_repository_trace_completes_three_response_probe_gate(tmp_path, monkeypatch):
    selector = "xai-oauth/grok-4.5:xhigh"
    agent = tmp_path / "review-grok.md"
    trace = tmp_path / "trace.jsonl"
    receipt_path = tmp_path / "receipt.json"
    prompts = tmp_path / "prompts.jsonl"
    responses = tmp_path / "responses.jsonl"
    ledger = tmp_path / "canary.jsonl"
    entry = {
        "agent": "review-grok",
        "model": selector,
        "access_profile": "xai-oauth-default",
        "data_allowlist_key": "xai",
    }
    monkeypatch.setattr(canary, "AGENTS", tmp_path)
    monkeypatch.setattr(run_review, "AGENTS", tmp_path)
    monkeypatch.setattr(canary, "_reviewers", lambda: {"grok": entry})
    _write_trace_agent(agent, selector, "repository")
    _write_trace(
        trace,
        evidence_delivery="repository",
        attempted=("read", "yield"),
        executed=("read", "yield"),
    )
    receipt_path.write_text(
        json.dumps(
            canary.capture_trace_receipt(trace, agent, "review-grok", selector, "repository")
        ),
        encoding="utf-8",
    )
    assert canary.main(["prompts", "--family", "grok", "--out", str(prompts)]) == canary.EXIT_OK
    replies = [
        _canary_reply(
            "grok|structured_output",
            selector,
            [
                "R1|P2|conf=0.80|claim=retry budget grew"
                "|evidence=src/canary/retry.py:1 loop bound"
                "|impact=latency|verify=inspect"
            ],
        ),
        _canary_reply(
            "grok|anchor_lookup",
            selector,
            [
                "R1|P1|conf=0.90|claim=missing deny path"
                "|evidence=src/canary/authz.py:10 comparison"
                "|impact=authz|verify=test"
            ],
        ),
        _canary_reply("grok|empty_abstention", selector, []),
    ]
    responses.write_text(
        "".join(json.dumps(reply) + "\n" for reply in replies),
        encoding="utf-8",
    )

    code = canary.main(
        [
            "grade",
            "--prompts",
            str(prompts),
            "--responses",
            str(responses),
            "--trace-receipt",
            str(receipt_path),
            "--out",
            str(ledger),
        ]
    )

    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert code == canary.EXIT_OK
    assert len(records) == len(canary.RESPONSE_PROBE_IDS)
    assert all(record["passed"] for record in records)
    assert len({record["trace_receipt_sha256"] for record in records}) == 1


def test_repository_trace_receipt_records_current_task_runtime_tools(tmp_path):
    selector = "xai-oauth/grok-4.5:xhigh"
    agent = tmp_path / "review-grok.md"
    trace = tmp_path / "trace.jsonl"
    receipt_path = tmp_path / "receipt.json"
    _write_trace_agent(agent, selector, "repository")
    _write_trace(
        trace,
        evidence_delivery="repository",
        attempted=("read", "yield"),
        executed=("read", "yield"),
        runtime_extra_tools=("hub", "mcp__node_repl_js"),
        emit_allowed_tools=False,
    )
    receipt = canary.capture_trace_receipt(trace, agent, "review-grok", selector, "repository")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    validated = canary.validate_trace_receipt(
        receipt_path, agent, "review-grok", selector, "repository"
    )
    assert validated["declared_tools"] == [
        "read",
        "grep",
        "glob",
        "yield",
        "hub",
        "mcp__node_repl_js",
    ]
    assert validated["forbidden_tool_attempts"] == 0


def test_repository_trace_receipt_rejects_runtime_extra_tool_use(tmp_path):
    selector = "xai-oauth/grok-4.5:xhigh"
    agent = tmp_path / "review-grok.md"
    trace = tmp_path / "trace.jsonl"
    _write_trace_agent(agent, selector, "repository")
    _write_trace(
        trace,
        evidence_delivery="repository",
        attempted=("read", "mcp__node_repl_js", "yield"),
        executed=("read", "mcp__node_repl_js", "yield"),
        runtime_extra_tools=("mcp__node_repl_js",),
        emit_allowed_tools=False,
    )
    with pytest.raises(canary.TraceCanaryError, match="forbidden_tool_attempts"):
        canary.capture_trace_receipt(trace, agent, "review-grok", selector, "repository")


def test_repository_trace_receipt_requires_an_observed_read(tmp_path):
    selector = "xai-oauth/grok-4.5:xhigh"
    agent = tmp_path / "review-grok.md"
    trace = tmp_path / "trace.jsonl"
    _write_trace_agent(agent, selector, "repository")
    _write_trace(trace, evidence_delivery="repository")
    with pytest.raises(canary.TraceCanaryError, match="read"):
        canary.capture_trace_receipt(trace, agent, "review-grok", selector, "repository")


@pytest.mark.parametrize(
    ("served", "attempted", "executed", "failure"),
    (
        ("grok-build", "yield", "yield", "fallback_used must be false"),
        ("grok-4.5", "read", "yield", "forbidden_tool_attempts"),
        ("grok-4.5", "yield", "read", "tool call(s) reached a tool"),
    ),
)
def test_trace_receipt_rejects_fallback_and_forbidden_tool_activity(
    tmp_path, served, attempted, executed, failure
):
    selector = "xai-oauth/grok-4.5:xhigh"
    agent = tmp_path / "review-grok.md"
    trace = tmp_path / "trace.jsonl"
    _write_trace_agent(agent, selector)
    _write_trace(trace, served=served, attempted=attempted, executed=executed)
    with pytest.raises(canary.TraceCanaryError, match=re.escape(failure)):
        canary.capture_trace_receipt(trace, agent, "review-grok", selector, "inline")
