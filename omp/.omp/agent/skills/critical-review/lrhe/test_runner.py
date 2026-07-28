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


def _declaring_experiment(family: str) -> tuple[str, str] | None:
    """The first experiment that declares this family, and a lens it runs there."""
    import yaml
    for exp in yaml.safe_load((HERE / "panels.yaml").read_text())["experiments"]:
        if any(f["family"] == family for f in exp["families"]):
            return exp["experimentId"], exp["lenses"][0]
    return None


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
    ({"classification": "customer_confidential"}, None),
    ({"classification": "secrets_or_credentials"}, None),
    ({"item_id": "does-not-exist"}, "unknown_item"),
    ({"family": "not-a-family"}, "family_not_in_panel"),
])
def test_refusal_never_reaches_a_transport(spy, over, expect_reason):
    """The whole point. A refused request must not be sent, not merely reported.

    Each case here is a different gate: a blocked classification, an unknown
    item, a family outside the declared panel. They fail at different depths,
    and none of them may leave the machine.
    """
    outcome = run_review.prepare(_args(**over))
    assert isinstance(outcome, run_review.Refusal), f"{over} was not refused"
    if expect_reason:
        assert outcome.reason_code == expect_reason
    assert outcome.exit_code in (run_review.EXIT_DENY, run_review.EXIT_UNRESOLVED)
    assert spy == [], "a refused request reached a transport"


def test_an_unqualified_lane_never_reaches_a_transport(spy):
    """The qualification gate, proved on whichever lane is actually held.

    This case used to name kimi, and passed until kimi was canaried and enabled
    -- at which point it asserted that a qualified lane is refused. The held lane
    is a fact in qualification.yml, so read it there. When nothing is held the
    gate has nothing to demonstrate on and this skips saying so, rather than
    going quietly green on an assertion it can no longer make.
    """
    import yaml
    qual = yaml.safe_load((SKILL / "qualification.yml").read_text())["reviewers"]
    held = [f for f, e in qual.items() if not e.get("councilEnabled")]
    if not held:
        pytest.skip("every lane is enabled; no unqualified lane to refuse")

    for family in held:
        declared = _declaring_experiment(family)
        assert declared, f"{family} is held but no experiment declares it either"
        experiment, lens = declared
        outcome = run_review.prepare(
            _args(family=family, lens=lens, experiment_id=experiment))
        assert isinstance(outcome, run_review.Refusal), f"{family} is not qualified but was not refused"
        assert outcome.reason_code == "lane_not_qualified"
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

    Each lane is planned in the experiment that declares it, not in a fixed one.
    Hardcoding the core panel made this pass only while every enabled lane
    happened to be a core lane: enabling the floor panel turned a real invariant
    into `family_not_in_panel`, which is the runner correctly refusing a question
    nobody meant to ask. A lane declared by no experiment is the actual defect,
    and it now fails as one.
    """
    import yaml
    qual = yaml.safe_load((SKILL / "qualification.yml").read_text())["reviewers"]
    enabled = [f for f, e in qual.items() if e.get("councilEnabled")]
    assert enabled, "no lane is enabled; this test would be vacuous"

    for family in enabled:
        declared = _declaring_experiment(family)
        assert declared, f"{family} is councilEnabled but no experiment in panels.yaml declares it"
        experiment, lens = declared
        outcome = run_review.prepare(
            _args(family=family, lens=lens, experiment_id=experiment))
        assert isinstance(outcome, run_review.Refusal) is False, (
            f"{family} is councilEnabled but cannot be dispatched in {experiment}: "
            f"{getattr(outcome, 'reason_code', '')} {getattr(outcome, 'message', '')}")


def test_packet_gate_failure_blocks_dispatch(spy, tmp_path):
    """A packet that leaks its own answer key must not be sendable."""
    items = [json.loads(x) for x in CORPUS.read_text().splitlines()]
    packets = [json.loads(x) for x in (DATA / "packets.jsonl").read_text().splitlines()]
    target = items[0]["item_id"]

    leaky = tmp_path / "packets.jsonl"
    leak = {"labels": [{"label_id": "L1", "severity": 1}]}
    rows = [{**p, **leak} if p["item_id"] == target else p for p in packets]
    leaky.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    outcome = run_review.prepare(_args(item_id=target, packets=leaky))
    assert isinstance(outcome, run_review.Refusal)
    assert outcome.reason_code == "packet_gate_failed"
    assert "oracle_leak" in outcome.message
    assert spy == []


# ------------------------------------------------- the lane that reaches a model

def _prompted(tmp_path, assignments: list[dict], **over):
    out = tmp_path / "rp.jsonl"
    (tmp_path / "a.jsonl").write_text("".join(json.dumps(a) + "\n" for a in assignments))
    args = _args(item_id=None, family=None, assignments=tmp_path / "a.jsonl",
                 out=out, cmd="prompts", **over)
    code = run_review.cmd_prompts(args)
    rows = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    return code, rows


def _reply(row: dict, **over) -> dict:
    packet_file = (row["request"]["packet"].get("repo_files") or ["src/x.py"])[0]
    body = {"summary": "reviewed", "unresolved": [],
            "evidence": [f"R1|P2|conf=0.60|claim=c|evidence={packet_file}:1 observed"
                         f"|impact=i|verify=v"]}
    return {"run_key": row["run_key"],
            "served_model": row["request"]["requested_model"],
            "response": body, **over}


def test_prompts_emits_nothing_for_an_assignment_the_gates_refuse(spy, tmp_path):
    """The batch path is the one that will actually be used, so it runs every gate.

    A runner that gated single requests and waved batches through would be gated in
    the mode nobody uses. Each row goes through the same `prepare()`, and a refusal
    is reported and dropped rather than emitted with a warning nobody reads.
    """
    code, rows = _prompted(tmp_path, [
        {"item_id": "S1-7e6f82f1", "family": "claude", "lens": "architecture"},
        {"item_id": "does-not-exist", "family": "claude", "lens": "architecture"},
    ])
    assert code == run_review.EXIT_UNRESOLVED
    assert [r["run_key"].split("|")[0] for r in rows] == ["S1-7e6f82f1"]
    assert spy == [], "prompts reached a transport"


def test_ingest_refuses_a_prompts_row_whose_rights_record_was_edited(spy, tmp_path):
    """`ingest` builds its request from a file, so the file is now an attack surface.

    `dispatch()` re-validates the rights record it was handed precisely because
    Python cannot stop someone constructing an AuthorizedRequest by hand. Reading
    one back off disk is that same hand, with a text editor. Both paths call the
    same check, and this is the test that they do.
    """
    _, rows = _prompted(tmp_path, [
        {"item_id": "S1-7e6f82f1", "family": "claude", "lens": "architecture"}])
    rows[0]["request"]["data_rights"]["egress_decision"] = "deny"
    prompts = tmp_path / "edited.jsonl"
    prompts.write_text(json.dumps(rows[0]) + "\n")
    responses = tmp_path / "rr.jsonl"
    responses.write_text(json.dumps(_reply(rows[0])) + "\n")

    out = tmp_path / "runs.jsonl"
    code = run_review.cmd_ingest(Namespace(prompts=prompts, responses=responses,
                                           out=out, omp_version="test"))
    assert code == run_review.EXIT_UNRESOLVED
    assert out.read_text() == "", "a denied rights record produced a run record"
    assert spy == []


def test_a_reply_through_the_agent_lane_becomes_a_valid_run_record(tmp_path):
    """The whole point of the path: a real reply, scored like any other run."""
    _, rows = _prompted(tmp_path, [
        {"item_id": "S1-7e6f82f1", "family": "claude", "lens": "architecture", "arm": "smoke"}])
    prompts, responses, out = (tmp_path / n for n in ("rp.jsonl", "rr.jsonl", "runs.jsonl"))
    prompts.write_text(json.dumps(rows[0]) + "\n")
    responses.write_text(json.dumps(_reply(rows[0])) + "\n")

    assert run_review.cmd_ingest(Namespace(prompts=prompts, responses=responses,
                                           out=out, omp_version="17.1.6")) == run_review.EXIT_OK
    record = json.loads(out.read_text().strip())
    assert list(run_review._validator("run.schema.json").iter_errors(record)) == []
    assert record["arm"] == "smoke", "panels.yaml excludes smoke from every estimate"
    assert record["reviewer"]["provider_client_version"] == "transport:agent"
    assert record["reviewer"]["identity_verified"] is True
    assert record["safety"]["schema_valid"] is True


def test_a_reply_that_breaks_the_reviewers_own_schema_is_recorded_as_such(tmp_path):
    """Not dropped, and not quietly passed. The scorer needs to see the gate fail.

    Absent telemetry used to default to success, so a run nobody could parse was
    indistinguishable from a clean one. A malformed reply is a fact about the lane
    and belongs in the record as `schema_valid: false`.
    """
    _, rows = _prompted(tmp_path, [
        {"item_id": "S1-7e6f82f1", "family": "claude", "lens": "architecture", "arm": "smoke"}])
    prompts, responses, out = (tmp_path / n for n in ("rp.jsonl", "rr.jsonl", "runs.jsonl"))
    prompts.write_text(json.dumps(rows[0]) + "\n")
    broken = _reply(rows[0])
    broken["response"]["evidence"] = ["R01|P2|not the contract"]
    responses.write_text(json.dumps(broken) + "\n")

    run_review.cmd_ingest(Namespace(prompts=prompts, responses=responses,
                                    out=out, omp_version="17.1.6"))
    record = json.loads(out.read_text().strip())
    assert record["safety"]["schema_valid"] is False
    assert list(run_review._validator("run.schema.json").iter_errors(record)) == []


def test_the_route_that_answered_is_recorded_when_the_lane_can_tell(tmp_path):
    """`PRODUCT_ROUTE` omits OpenCode on purpose, so the lane has to supply it.

    A request can land on the Go allowance or spill to Zen and only telemetry knows
    which, so the table refuses to guess and every OpenCode run would otherwise be
    `unknown` forever -- including the quota check the smoke pass exists to make.
    Nothing is inferred: `billing_route` stays unknown, because which allowance line
    was billed is a step past what the route tells you.
    """
    _, rows = _prompted(tmp_path, [
        {"item_id": "S1-7e6f82f1", "family": "kimi", "lens": "floor", "arm": "smoke",
         "experiment_id": "lrhe-opencode-v1"}])
    prompts, responses, out = (tmp_path / n for n in ("rp.jsonl", "rr.jsonl", "runs.jsonl"))
    prompts.write_text(json.dumps(rows[0]) + "\n")
    responses.write_text(json.dumps(_reply(rows[0], product_route="opencode-go")) + "\n")

    run_review.cmd_ingest(Namespace(prompts=prompts, responses=responses,
                                    out=out, omp_version="17.1.6"))
    reviewer = json.loads(out.read_text().strip())["reviewer"]
    assert reviewer["product_route"] == "opencode-go"
    assert reviewer["billing_route"] == "unknown"


def test_a_route_the_schema_does_not_model_is_recorded_as_unknown(tmp_path):
    """Recording it verbatim fails validation at the last step and loses the run."""
    _, rows = _prompted(tmp_path, [
        {"item_id": "S1-7e6f82f1", "family": "kimi", "lens": "floor", "arm": "smoke",
         "experiment_id": "lrhe-opencode-v1"}])
    prompts, responses, out = (tmp_path / n for n in ("rp.jsonl", "rr.jsonl", "runs.jsonl"))
    prompts.write_text(json.dumps(rows[0]) + "\n")
    responses.write_text(json.dumps(_reply(rows[0], product_route="a-route-nobody-modelled")) + "\n")

    run_review.cmd_ingest(Namespace(prompts=prompts, responses=responses,
                                    out=out, omp_version="17.1.6"))
    record = json.loads(out.read_text().strip())
    assert record["reviewer"]["product_route"] == "unknown"
    assert list(run_review._validator("run.schema.json").iter_errors(record)) == []
