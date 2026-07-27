#!/usr/bin/env python3
"""The pre-egress gate: does anything actually stop a request leaving?

    ./.venv/bin/pytest test_runner.py -q

`check_data_rights.py` and `check_packet_gates.py` were correct and well tested
for a while and still protected nothing, because no code path invoked them. These
tests are about the property that fixes: not "the guard returns deny" but
"a denied request never reaches a transport".

Every test that asserts a refusal spies on the transport table and asserts the
call count is zero. A gate that refuses AFTER sending is not a gate.
"""
from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import run_review  # noqa: E402

SKILL = Path.home() / ".omp/agent/skills/critical-review"
DATA = SKILL / "lrhe-data"
CORPUS = DATA / "corpus.jsonl"

pytestmark = pytest.mark.skipif(
    not CORPUS.exists(), reason="private corpus is not present in this checkout")


def _args(**over) -> Namespace:
    base = dict(
        item_id="S1-7e6f82f1", family="claude", lens="architecture", arm="C",
        experiment_id="lrhe-core-v1", classification="public_corpus",
        item_authorized=False, policy_id=None,
        corpus=CORPUS, packets=DATA / "packets.jsonl",
        manifest=DATA / "assignments.manifest.json",
        panels=HERE / "panels.yaml", policies=HERE / "provider-policies.yaml",
        qualification=SKILL / "qualification.yml",
    )
    base.update(over)
    return Namespace(**base)


@pytest.fixture
def spy(monkeypatch):
    """Replace every transport with a counter. Nothing can send without incrementing it."""
    calls: list[run_review.AuthorizedRequest] = []

    def counting(req):
        calls.append(req)
        return run_review.stub_transport(req)

    monkeypatch.setattr(run_review, "TRANSPORTS",
                        {"none": run_review.no_egress_transport, "stub": counting})
    return calls


# ------------------------------------------------------- refusals never send

@pytest.mark.parametrize("over,expect_reason", [
    ({"family": "kimi", "experiment_id": "lrhe-opencode-v1"}, "lane_not_qualified"),
    ({"classification": "customer_confidential"}, None),
    ({"classification": "secrets_or_credentials"}, None),
    ({"item_id": "does-not-exist"}, "unknown_item"),
    ({"family": "not-a-family"}, "family_not_in_panel"),
])
def test_refusal_never_reaches_a_transport(spy, over, expect_reason):
    """The whole point. A refused request must not be sent, not merely reported.

    Each case here is a different gate: an unqualified lane, a blocked
    classification, an unknown item, a family outside the declared panel. They
    fail at different depths, and none of them may leave the machine.
    """
    outcome = run_review.prepare(_args(**over))
    assert isinstance(outcome, run_review.Refusal), f"{over} was not refused"
    if expect_reason:
        assert outcome.reason_code == expect_reason
    assert outcome.exit_code in (run_review.EXIT_DENY, run_review.EXIT_UNRESOLVED)
    assert spy == [], "a refused request reached a transport"


def test_default_transport_refuses_to_send(spy):
    """`none` is the default so that a dry run cannot leak by forgetting a flag."""
    outcome = run_review.prepare(_args())
    assert isinstance(outcome, run_review.AuthorizedRequest)
    with pytest.raises(run_review.EgressRefused):
        run_review.dispatch(outcome, "none")
    assert spy == []


def test_no_live_transport_exists(spy):
    """A half-written live path is the one that gets called by mistake."""
    assert "live" not in run_review.TRANSPORTS
    outcome = run_review.prepare(_args())
    with pytest.raises(run_review.EgressRefused):
        run_review.dispatch(outcome, "live")
    assert spy == []


# --------------------------------------------- dispatch re-checks its evidence

def test_hand_built_request_with_bad_rights_is_refused(spy):
    """Python will let anyone construct an AuthorizedRequest. Dispatch re-checks.

    `prepare()` returning the only valid instance is a convention, not an
    enforcement. The last-step revalidation is what survives someone assembling
    the dataclass directly, which is exactly what a hurried caller would do.
    """
    good = run_review.prepare(_args())
    assert isinstance(good, run_review.AuthorizedRequest)

    forged = run_review.AuthorizedRequest(
        **{**good.__dict__, "data_rights": {"record_id": "made-up", "egress_decision": "allow"}})
    with pytest.raises(run_review.EgressRefused, match="does not validate"):
        run_review.dispatch(forged, "stub")
    assert spy == []


def test_rights_record_that_denies_is_refused_at_dispatch(spy):
    """A record can validate and still say no. Dispatch reads the decision."""
    good = run_review.prepare(_args())
    denied = dict(good.data_rights)
    denied["egress_decision"] = "deny"
    forged = run_review.AuthorizedRequest(**{**good.__dict__, "data_rights": denied})
    with pytest.raises(run_review.EgressRefused, match="egress_decision"):
        run_review.dispatch(forged, "stub")
    assert spy == []


# --------------------------------------------------------- the allowed path

def test_allowed_path_sends_once_and_records_its_authority(spy):
    """The happy path still has to carry its evidence into the run record."""
    outcome = run_review.prepare(_args())
    assert isinstance(outcome, run_review.AuthorizedRequest)
    record = run_review.dispatch(outcome, "stub")

    assert len(spy) == 1, "expected exactly one send"
    assert record["input_rights_record_id"] == outcome.data_rights["record_id"]
    assert record["data_rights"]["egress_decision"] == "allow"
    assert record["reviewer"]["requested_model"] == outcome.requested_model
    assert record["reviewer"]["identity_verified"] is True
    # Never inferred: section 7 permits `unknown` and forbids guessing.
    assert record["reviewer"]["billing_route"] == "unknown"
    assert record["artifact_digest"] == outcome.packet_digest


def test_emitted_run_record_validates_against_the_run_schema():
    """dispatch refuses to return a record the scorer would reject.

    Finding that out here, with the response still in hand, beats finding it after
    a paid run has been written to disk in a shape nothing will read.
    """
    outcome = run_review.prepare(_args())
    record = run_review.dispatch(outcome, "stub")
    errors = list(run_review._validator("run.schema.json").iter_errors(record))
    assert errors == [], [f"{e.json_path} {e.message}" for e in errors[:3]]


def test_every_enabled_lane_can_be_planned():
    """A lane marked councilEnabled must actually be dispatchable.

    The three enabled reviewers routed through providers with no policy entry at
    all for a while: qualified, in use, and ungoverned. Nothing surfaced it until
    a runner tried to assemble a request, because no other code path asked.
    """
    import yaml
    qual = yaml.safe_load((SKILL / "qualification.yml").read_text())["reviewers"]
    enabled = [f for f, e in qual.items() if e.get("councilEnabled")]
    assert enabled, "no lane is enabled; this test would be vacuous"

    for family in enabled:
        outcome = run_review.prepare(_args(family=family, lens="floor"))
        assert isinstance(outcome, run_review.Refusal) is False, (
            f"{family} is councilEnabled but cannot be dispatched: "
            f"{getattr(outcome, 'reason_code', '')} {getattr(outcome, 'message', '')}")


def test_packet_gate_failure_blocks_dispatch(spy, tmp_path):
    """A packet that leaks its own answer key must not be sendable."""
    items = [json.loads(x) for x in CORPUS.read_text().splitlines()]
    packets = [json.loads(x) for x in (DATA / "packets.jsonl").read_text().splitlines()]
    target = items[0]["item_id"]

    leaky = tmp_path / "packets.jsonl"
    rows = []
    for packet in packets:
        if packet["item_id"] == target:
            packet = {**packet, "labels": [{"label_id": "L1", "severity": 1}]}
        rows.append(packet)
    leaky.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    outcome = run_review.prepare(_args(item_id=target, packets=leaky))
    assert isinstance(outcome, run_review.Refusal)
    assert outcome.reason_code == "packet_gate_failed"
    assert "oracle_leak" in outcome.message
    assert spy == []
