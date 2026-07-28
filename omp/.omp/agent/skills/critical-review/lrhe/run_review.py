#!/usr/bin/env python3
"""Assemble one reviewer request, or refuse to.

    ./.venv/bin/python run_review.py plan     --item-id S1-... --family claude
    ./.venv/bin/python run_review.py dispatch --item-id S1-... --family claude --transport stub
    ./.venv/bin/python run_review.py prompts  --assignments smoke.jsonl --out rp.jsonl
    ./.venv/bin/python run_review.py ingest   --prompts rp.jsonl --responses rr.jsonl

This is the pre-egress gate that authorization section 8 step 2 asks for. Until
now `check_data_rights.py` and `check_packet_gates.py` were CLIs nobody called:
correct, tested, and advisory. A gate nothing invokes is documentation.

THE SHAPE IS THE POINT. `prepare()` returns either a `Refusal` or an
`AuthorizedRequest`, and `dispatch()` accepts nothing but an `AuthorizedRequest`.
There is no argument you can pass `dispatch()` to make it skip a check, and no
ordering of calls that reaches a provider without a rights record in hand. Python
cannot enforce that at the type level, so `dispatch()` re-validates the record it
was handed against `data-rights.schema.json` before touching a transport: a
hand-built request with a plausible-looking record still dies at the last step.

Transports are explicit and default to refusing:

    none    raises on any send. What `plan` uses, so a dry run cannot leak by
            accident -- and what the tests assert was never called.
    stub    deterministic canned response. Exercises assembly, run-record
            emission and provenance without a network socket existing.
    live    not implemented. No OpenCode credential is configured, the operator
            has held provider calls pending an OMP upgrade, and a half-written
            live path is exactly the thing that gets called by mistake.

A reviewer still has to reach a model, and it does -- through the OMP agent named
in qualification.yml, the one the council dispatches. `prompts` runs every gate
and emits the packet as text, and `ingest` turns the replies into run records,
re-running the rights check on each request it rebuilt from the file. Nothing in
that path opens a connection either, so the boundary above is unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from argparse import Namespace
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import check_packet_gates  # noqa: E402  -- needs the path above

SKILL = Path.home() / ".omp/agent/skills/critical-review"
DATA = SKILL / "lrhe-data"

EXIT_OK = 0
EXIT_DENY = 10
EXIT_UNRESOLVED = 20


@dataclass(frozen=True)
class Refusal:
    """Why nothing will be sent. `deny` is a decision; `unresolved` is homework."""
    decision: str
    reason_code: str
    message: str

    @property
    def exit_code(self) -> int:
        return EXIT_DENY if self.decision == "deny" else EXIT_UNRESOLVED


@dataclass(frozen=True)
class AuthorizedRequest:
    """A request that has passed every gate, carrying the evidence that it did.

    Constructed only by `prepare()`. Holding one is the proof; `dispatch()` asks
    for nothing else and re-checks the proof anyway.
    """
    item_id: str
    family: str
    lens: str
    arm: str
    experiment_id: str
    panel_id: str
    prompt_version: str
    context_config: str
    requested_model: str
    provider_route: str
    account_type: str
    agent: str
    packet: dict[str, Any]
    data_rights: dict[str, Any]
    packet_digest: str
    assignment_manifest_digest: str
    terms_snapshot_id: str


def lens_text(panels: dict[str, Any], lens: str) -> str:
    """The assignment for a lens, from panels.yaml. Raises on one nobody declared.

    Silently rendering an unknown lens as nothing is how the whole defect worked:
    the field was recorded on every run and transmitted on none.
    """
    declared = panels.get("lenses")
    if not isinstance(declared, dict) or lens not in declared:
        raise SystemExit(
            f"lens {lens!r} has no text in panels.yaml. A lens that is assigned but "
            f"not transmitted is recorded on the run and applied to nothing.")
    return str(declared[lens] or "").strip()


def render_packet(packet: dict[str, Any], lens: str = "floor",
                  panels: dict[str, Any] | None = None) -> str:
    """The packet as a reviewer receives it, under its assigned lens.

    A packet is data; a provider takes text. Something has to turn one into the
    other, and if each caller does it, two lanes reviewing the same item read two
    different documents and the comparison between them measures the renderer.
    So it lives here, beside the transports, and the canary imports it rather
    than authoring a second one.

    The lens arrives here for the same reason. It used to be a hardcoded line in
    three agent definitions, so a family could only ever run its own lens while
    every assignment recorded one freely -- the rotation the experiment is named
    for could not be delivered. It is text in `panels.yaml` now and the runner
    transmits it, which is also what lets one agent serve any lens it is given.

    `repo_files` is stated as the closed set of citable anchors because it is:
    the reviewer is answering from this document, not from a working tree, and a
    citation outside the set is a fabricated anchor whether or not the path
    happens to exist on the machine that reads it.
    """
    if panels is None:
        panels = yaml.safe_load((HERE / "panels.yaml").read_text())
    assignment = lens_text(panels, lens)
    files = packet.get("repo_files") or []
    return "\n".join((
        f"item_id: {packet.get('item_id', '')}",
        f"stratum: {packet.get('stratum', '')}",
        f"lens: {lens}",
        "",
        *((assignment, "") if assignment else ()),
        "## Goal",
        str(packet.get("goal", "")).strip() or "(unstated)",
        "",
        "## Problem statement",
        str(packet.get("problem_statement", "")).strip() or "(none)",
        "",
        "## Files in scope -- the complete set of anchors you may cite",
        *(tuple(f"  {p}" for p in files) or ("  (none)",)),
        "",
        "## Design or diff under review",
        str(packet.get("design_or_diff", "")).strip() or "(none)",
        "",
        "Review the change above. This document is the whole of the evidence: do "
        "not read the working tree, and cite only paths listed under files in "
        "scope. Return your structured response and nothing else.",
    ))


class EgressRefused(RuntimeError):
    """A transport was asked to send when sending was not permitted."""


def no_egress_transport(_req: AuthorizedRequest) -> dict[str, Any]:
    """The default. Proves a dry run cannot reach a provider even by mistake."""
    raise EgressRefused(
        "transport 'none' was asked to send. This is the default so that planning "
        "cannot leak; pass --transport stub to exercise the path locally."
    )


def stub_transport(req: AuthorizedRequest) -> dict[str, Any]:
    """Deterministic canned response. No socket is opened anywhere in this path.

    Derived from the request so two cells do not collide, and stable so a test can
    assert on it. It reports the requested model as served: a stub that faked a
    fallback would make every run gate-fail and hide whatever else broke.
    """
    seed = hashlib.sha256(f"{req.item_id}|{req.family}|{req.lens}".encode()).hexdigest()
    return {
        "served_model": req.requested_model,
        "summary": f"stub review of {req.item_id} by {req.family}",
        "evidence": [
            f"R1|P2|conf=0.50|claim=stub finding {seed[:8]}"
            f"|evidence={(req.packet.get('repo_files') or ['src/unknown.py'])[0]}:1 observed"
            f"|impact=stub|verify=stub"
        ],
        "unresolved": [],
        "latency_ms": 1000 + int(seed[:4], 16) % 1000,
        "input_tokens": 1000,
        "output_tokens": 100,
        "cost_usd": 0.0,
        "tool_violations": 0,
        # Stated, not defaulted. The stub's reply is schema-valid by construction
        # and a cross-file test holds it to that; saying so here is what lets
        # `_run_record` refuse a transport that stays silent on the question.
        "schema_valid": True,
        "telemetry_complete": True,
        "raw": f"stub:{seed[:16]}",
    }


TRANSPORTS: dict[str, Callable[[AuthorizedRequest], dict[str, Any]]] = {
    "none": no_egress_transport,
    "stub": stub_transport,
}


# ------------------------------------------------------------------ helpers

def _digest(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _validator(name: str) -> Draft202012Validator:
    docs = [json.loads((HERE / n).read_text())
            for n in (name, "data-rights.schema.json")]
    registry = Registry().with_resources(
        [(d["$id"], Resource.from_contents(d)) for d in docs])
    return Draft202012Validator(docs[0], registry=registry, format_checker=FormatChecker())


def _panel(panels: dict, experiment_id: str) -> dict:
    for exp in panels["experiments"]:
        if exp["experimentId"] == experiment_id:
            return exp
    known = ", ".join(e["experimentId"] for e in panels["experiments"])
    raise SystemExit(f"unknown experiment {experiment_id!r}; panels.yaml has: {known}")


# ------------------------------------------------------------------- gates

def prepare(args) -> AuthorizedRequest | Refusal:
    """Every gate, in the order that fails cheapest first. Any one refuses."""
    panels = yaml.safe_load(args.panels.read_text())
    exp = _panel(panels, args.experiment_id)
    lane = next((f for f in exp["families"] if f["family"] == args.family), None)
    if lane is None:
        return Refusal("unresolved", "family_not_in_panel",
                       f"{args.family!r} is not in panel {exp['panelId']!r}")

    # 1. Is this lane allowed to run at all? qualification.yml is the dispatch
    #    gate the SKILL reads, and it is deliberately the only place that answers.
    qual = yaml.safe_load(args.qualification.read_text())["reviewers"]
    entry = qual.get(args.family)
    if entry is None:
        return Refusal("unresolved", "lane_unknown",
                       f"{args.family!r} has no qualification.yml entry")
    if entry.get("councilEnabled") is not True:
        blockers = "; ".join(entry.get("blockers") or ["no reason recorded"])
        return Refusal("deny", "lane_not_qualified",
                       f"{args.family} is councilEnabled: false -- {blockers}")

    # 2. Does the item exist, and is there a scrubbed packet for it? The packet is
    #    what would actually be transmitted; the corpus row carries the answer key
    #    and must never be the thing we send.
    items = {i["item_id"]: i for i in _read_jsonl(args.corpus)}
    packets = {p["item_id"]: p for p in _read_jsonl(args.packets)}
    item, packet = items.get(args.item_id), packets.get(args.item_id)
    if item is None:
        return Refusal("unresolved", "unknown_item", f"{args.item_id!r} is not in the corpus")
    if packet is None:
        return Refusal("unresolved", "no_packet",
                       f"{args.item_id!r} has no reviewer packet; nothing establishes "
                       f"what would be transmitted")

    # 3. What would be transmitted, checked rather than assumed.
    fails, _ = check_packet_gates.gate_item(item, packet)
    if fails:
        return Refusal("deny", "packet_gate_failed", "; ".join(fails))

    # 4. Rights. Invoked as the CLI it already is, so there is exactly one
    #    implementation of this decision and the runner cannot reach around it.
    policy_id = args.policy_id or _policy_for_route(args.policies, lane["providerRoute"])
    if policy_id is None:
        return Refusal("unresolved", "no_policy_for_route",
                       f"no policy in provider-policies.yaml governs "
                       f"{lane['providerRoute']!r}")
    proc = subprocess.run(
        [sys.executable, str(HERE / "check_data_rights.py"),
         "--item-id", args.item_id,
         "--classification", args.classification,
         "--route", lane["providerRoute"],
         "--policy-id", policy_id,
         "--item-provider-allowlist", *item.get("provider_data_allowlist", []),
         *(["--item-authorized"] if args.item_authorized else [])],
        capture_output=True, text=True, cwd=HERE, check=False)
    if proc.returncode != EXIT_OK:
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return Refusal("unresolved", "rights_guard_unreadable",
                           (proc.stdout + proc.stderr).strip()[:300])
        return Refusal(payload.get("decision", "unresolved"),
                       payload.get("reason_code", "rights_refused"),
                       payload.get("message", ""))
    rights = json.loads(proc.stdout)

    manifest = json.loads(args.manifest.read_text()) if args.manifest.exists() else {}
    return AuthorizedRequest(
        item_id=args.item_id,
        family=args.family,
        lens=args.lens or (exp["lenses"][0] if exp["lenses"] else "floor"),
        arm=args.arm,
        experiment_id=exp["experimentId"],
        panel_id=exp["panelId"],
        prompt_version=exp["promptVersion"],
        context_config=exp.get("contextConfig", "retrieval"),
        requested_model=lane["requestedSelector"],
        provider_route=lane["providerRoute"],
        account_type=rights.get("account_type", "unknown"),
        agent=(entry.get("agent") or ""),
        packet=packet,
        data_rights=rights,
        packet_digest=_digest(packet),
        assignment_manifest_digest=manifest.get("assignments_sha256", "unset"),
        terms_snapshot_id=rights["terms_snapshot_id"],
    )


def _policy_for_route(path: Path, route: str) -> str | None:
    for policy in yaml.safe_load(path.read_text())["policies"]:
        if policy["providerRoute"] == route:
            return policy["policyId"]
    return None


# ---------------------------------------------------------------- dispatch

def _require_allowed_rights(req: AuthorizedRequest) -> None:
    """Re-validate the evidence the request carries. Raises rather than returns.

    `prepare()` produced this record, so re-checking it looks redundant. It is the
    check that survives someone constructing an AuthorizedRequest by hand, which
    Python will happily let them do -- and now also the one that survives a
    hand-edited prompts row, since `ingest` builds its request from a file. Both
    last steps run this, so there is one implementation of the decision.
    """
    rights_schema = json.loads((HERE / "data-rights.schema.json").read_text())
    errors = list(Draft202012Validator(
        rights_schema, format_checker=FormatChecker()).iter_errors(req.data_rights))
    if errors:
        raise EgressRefused(
            "the rights record attached to this request does not validate: "
            + "; ".join(e.message for e in errors[:3]))
    if req.data_rights.get("egress_decision") != "allow":
        raise EgressRefused(
            f"rights record says egress_decision="
            f"{req.data_rights.get('egress_decision')!r}, not 'allow'")


def dispatch(req: AuthorizedRequest, transport: str, *,
             omp_version: str = "unknown") -> dict[str, Any]:
    """Send, and build the run record. Takes an AuthorizedRequest and nothing else."""
    _require_allowed_rights(req)

    send = TRANSPORTS.get(transport)
    if send is None:
        raise EgressRefused(
            f"transport {transport!r} is not implemented. 'live' is deliberately "
            f"absent: no provider credential is configured and provider calls are "
            f"held pending the OMP upgrade")

    started = datetime.now(timezone.utc)
    response = send(req)
    completed = datetime.now(timezone.utc)
    return _validated_record(req, response, started, completed, transport, omp_version)


def _validated_record(req: AuthorizedRequest, response: dict, started, completed,
                      transport: str, omp_version: str) -> dict[str, Any]:
    """Build the run record and refuse to return one the scorer would reject.

    Better to find that out with the response still in hand than after a paid run
    has been written to disk in a shape nothing will read.
    """
    record = _run_record(req, response, started, completed, transport, omp_version)
    bad = list(_validator("run.schema.json").iter_errors(record))
    if bad:
        raise EgressRefused(
            "the run record this runner produced does not validate against "
            "run.schema.json: " + "; ".join(f"{e.json_path} {e.message}" for e in bad[:3]))
    return record


# The product tier each configured route serves. Only the OpenCode route is
# genuinely ambiguous -- a request can land on the Go allowance or spill to Zen,
# and only the provider's own telemetry knows which -- so it is absent here and
# must come from the transport or stay `unknown`.
PRODUCT_ROUTE: dict[str, str] = {
    "anthropic-subscription": "anthropic-subscription",
    "claude-code-subscription": "anthropic-subscription",
    "google-antigravity": "google-antigravity",
    "xai-oauth": "supergrok-subscription",
}


def _enum_or_unknown(value: Any, field: str) -> str:
    """Accept a reported value only if the schema already allows it.

    A transport reporting something the enum does not know is telling us about a
    route we have not modelled. Recording it verbatim fails validation at the last
    step and loses the run; recording `unknown` keeps the run and is honest about
    what could be established.
    """
    schema = json.loads((HERE / "run.schema.json").read_text())
    allowed = schema["properties"]["reviewer"]["properties"][field]["enum"]
    return value if value in allowed else "unknown"


def _run_record(req: AuthorizedRequest, response: dict, started, completed,
                transport: str, omp_version: str) -> dict[str, Any]:
    served = response.get("served_model")
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    return {
        "schema_version": 2,
        "experiment_id": req.experiment_id,
        "panel_id": req.panel_id,
        "run_id": f"{req.item_id}-{req.family}-{req.lens}-{int(started.timestamp())}",
        "item_id": req.item_id,
        "arm": req.arm,
        "family": req.family,
        "lens": req.lens,
        "replicate": "",
        "context_config": req.context_config,
        "role": "critic",
        "prompt_version": req.prompt_version,
        "artifact_digest": req.packet_digest,
        "assignment_manifest_digest": req.assignment_manifest_digest,
        "evidence_cap": 12,
        "input_rights_record_id": req.data_rights["record_id"],
        "clarification_snapshot_id": None,
        "provider_documentation_snapshot_id": req.terms_snapshot_id,
        "router_dataset_example_ids": [],
        "reviewer": {
            "provider_route": req.provider_route,
            # Reported by the transport when it can tell, `unknown` when it cannot.
            # Section 7 permits `unknown` and forbids inference: a fabricated
            # billing route reconciles against the dashboard and nobody looks
            # again. The previous version stamped every non-OpenCode run
            # `opencode-zen` because the enum offered nothing else -- a Claude run
            # carrying an OpenCode product route is not a rounding error, it is a
            # provenance field that is simply false.
            "product_route": _enum_or_unknown(
                response.get("product_route") or PRODUCT_ROUTE.get(req.provider_route),
                "product_route"),
            "billing_route": _enum_or_unknown(response.get("billing_route"), "billing_route"),
            "account_type": req.account_type,
            "requested_model": req.requested_model,
            "served_model": served,
            "identity_verified": bool(served) and served == req.requested_model,
            "fallback_detected": bool(served) and served != req.requested_model,
            "omp_version": omp_version,
            "provider_client_version": f"transport:{transport}",
        },
        "execution": {
            "started_at": started.strftime(stamp),
            "completed_at": completed.strftime(stamp),
            "latency_ms": int(response.get("latency_ms") or 0),
            "input_tokens": response.get("input_tokens"),
            "cached_input_tokens": 0,
            "output_tokens": response.get("output_tokens"),
            "list_cost_estimate_usd": response.get("cost_usd"),
            "provider_reported_cost_usd": response.get("cost_usd"),
            "quota_pool": None,
            "allowance_before": None,
            "allowance_after": None,
            "zen_balance_before": None,
            "zen_balance_after": None,
            "raw_output_digest": _digest(response.get("raw", "")),
            "tool_trace_digest": _digest(response.get("tool_trace", [])),
        },
        "safety": {
            # No `.get` default on these two. Section 5.5 is about absent telemetry
            # reading as success, and a default here is exactly that: a transport
            # that cannot say whether the reply validated would produce a record
            # indistinguishable from one that did. A KeyError at build time is the
            # loud version of the same fact.
            "telemetry_complete": bool(response["telemetry_complete"]),
            "schema_valid": bool(response["schema_valid"]),
            "tool_violations": int(response.get("tool_violations") or 0),
            "wrote_to_repo": False,
            "spawned_subagent": False,
            "consumed_peer_output": False,
            "repo_digest_before": req.packet_digest,
            "repo_digest_after": req.packet_digest,
            "timed_out": False,
            "provider_error": response.get("provider_error"),
        },
        "data_rights": req.data_rights,
        "summary": response.get("summary", ""),
        "evidence": list(response.get("evidence") or []),
        "unresolved": list(response.get("unresolved") or []),
    }


# --------------------------------------------------- the lane that reaches a model
#
# `dispatch()` is the gated path to a transport, and the transports are `none` and
# `stub`. Neither reaches a model, and `live` is deliberately absent: a half-written
# live path is the one that gets called by mistake, and no provider credential
# belongs in this repository anyway. But the reviewers do reach models -- through
# the OMP agent named by `agent:` in qualification.yml, which is what the council
# dispatches and what the canary already qualified the floor lanes with.
#
# So the same split, one layer up. `prompts` runs every gate and emits the packet
# as text; the reviewer agent answers; `ingest` builds the run record. The egress
# boundary does not move -- nothing here opens a connection -- and `ingest` re-runs
# the rights check on a request it rebuilt from a file, so a hand-edited prompts row
# dies at the same last step a hand-built AuthorizedRequest does.

AGENTS = Path.home() / ".omp/agent/agents"


def agent_output_schema(agent: str) -> dict[str, Any] | None:
    """A reviewer agent's declared output schema, or None when unreadable."""
    definition = AGENTS / f"{agent}.md"
    if not agent or not definition.is_file():
        return None
    text = definition.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    return (yaml.safe_load(text.split("---", 2)[1]) or {}).get("output")


def _reply_is_schema_valid(agent: str, reply: dict[str, Any]) -> bool | None:
    """Did the reviewer answer in the shape it declared? None when unanswerable."""
    schema = agent_output_schema(agent)
    if schema is None:
        return None
    body = {k: reply.get(k) for k in ("summary", "evidence", "unresolved")}
    return not list(Draft202012Validator(schema).iter_errors(body))


def cmd_prompts(args) -> int:
    """Gate every assignment, and emit the ones that survive as dispatchable text."""
    assignments = _read_jsonl(args.assignments)
    panels = yaml.safe_load(args.panels.read_text())
    rows, refused = [], []
    for a in assignments:
        one = Namespace(**{**vars(args), **a})
        outcome = prepare(one)
        if isinstance(outcome, Refusal):
            refused.append((a, outcome))
            continue
        rows.append({
            "run_key": f"{outcome.item_id}|{outcome.family}|{outcome.lens}|{outcome.arm}",
            "agent": outcome.agent,
            "request": asdict(outcome),
            "prompt": render_packet(outcome.packet, outcome.lens, panels),
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                        encoding="utf-8")
    print(f"{len(rows)} of {len(assignments)} assignment(s) authorized -> {args.out}")
    for a, refusal in refused:
        print(f"  refused {a.get('item_id')}/{a.get('family')}: "
              f"{refusal.reason_code} {refusal.message}", file=sys.stderr)
    print("\nDispatch each `prompt` to its `agent`, one invocation per row, in a fresh")
    print("session with no peer output. Write one JSON object per reply carrying")
    print("  {run_key, served_model, response: {summary, evidence, unresolved}}")
    print("then run `run_review.py ingest`.")
    return EXIT_OK if not refused else EXIT_UNRESOLVED


def cmd_ingest(args) -> int:
    """Build run records from replies obtained through the agent lane."""
    prompts = {p["run_key"]: p for p in _read_jsonl(args.prompts)}
    replies = _read_jsonl(args.responses)

    records, unmatched, failed = [], [], []
    for reply in replies:
        prompt = prompts.get(str(reply.get("run_key", "")))
        if prompt is None:
            unmatched.append(str(reply.get("run_key", "")))
            continue
        req = AuthorizedRequest(**prompt["request"])
        body = reply.get("response") or {}
        valid = _reply_is_schema_valid(prompt["agent"], body)
        started = datetime.now(timezone.utc)
        response = {
            "served_model": reply.get("served_model"),
            "summary": body.get("summary") if isinstance(body.get("summary"), str) else "",
            "evidence": body.get("evidence") if isinstance(body.get("evidence"), list) else [],
            "unresolved": body.get("unresolved") if isinstance(body.get("unresolved"), list) else [],
            "latency_ms": reply.get("latency_ms"),
            "input_tokens": reply.get("input_tokens"),
            "output_tokens": reply.get("output_tokens"),
            "cost_usd": reply.get("cost_usd"),
            "tool_violations": reply.get("tool_violations"),
            "provider_error": reply.get("provider_error"),
            "raw": json.dumps(body, sort_keys=True),
            # A reviewer whose agent definition cannot be read has not been shown
            # to answer in shape, and `telemetry_complete: false` is how the record
            # says the question went unanswered rather than answering it favourably.
            "schema_valid": bool(valid),
            "telemetry_complete": valid is not None and reply.get("served_model") is not None,
        }
        try:
            _require_allowed_rights(req)
            records.append(_validated_record(req, response, started,
                                             datetime.now(timezone.utc), "agent",
                                             args.omp_version))
        except EgressRefused as exc:
            failed.append((prompt["run_key"], str(exc)))
            continue
        mark = "ok  " if response["schema_valid"] else "SHAPE"
        print(f"  {mark} {prompt['run_key']}  served {response['served_model']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"\nappended {len(records)} run record(s) to {args.out}")

    missing = sorted(set(prompts) - {str(r.get("run_key", "")) for r in replies})
    for key, why in failed:
        print(f"  refused at ingest: {key}: {why}", file=sys.stderr)
    if unmatched:
        print(f"  {len(unmatched)} reply/replies match no prompt: "
              f"{', '.join(sorted(set(unmatched))[:5])}", file=sys.stderr)
    if missing:
        print(f"  {len(missing)} prompt(s) unanswered: {', '.join(missing[:5])}", file=sys.stderr)
    return EXIT_OK if not (failed or unmatched or missing) else EXIT_UNRESOLVED


# ------------------------------------------------------------------- driver

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "dispatch", "prompts"):
        p = sub.add_parser(name)
        # `prompts` reads item/family/lens/arm per row from --assignments; the flags
        # below become its defaults, so one file can leave them out and inherit.
        p.add_argument("--item-id", required=name != "prompts")
        p.add_argument("--family", required=name != "prompts")
        p.add_argument("--lens", default="")
        p.add_argument("--arm", default="C")
        p.add_argument("--experiment-id", default="lrhe-core-v1")
        p.add_argument("--classification", default="public_corpus")
        p.add_argument("--item-authorized", action="store_true")
        p.add_argument("--policy-id", default=None)
        p.add_argument("--corpus", type=Path, default=DATA / "corpus.jsonl")
        p.add_argument("--packets", type=Path, default=DATA / "packets.jsonl")
        p.add_argument("--manifest", type=Path, default=DATA / "assignments.manifest.json")
        p.add_argument("--panels", type=Path, default=HERE / "panels.yaml")
        p.add_argument("--policies", type=Path, default=HERE / "provider-policies.yaml")
        p.add_argument("--qualification", type=Path, default=SKILL / "qualification.yml")
        if name == "dispatch":
            p.add_argument("--transport", default="none", choices=sorted(TRANSPORTS))
            p.add_argument("--out", type=Path, default=None,
                           help="append the run record here as JSONL")
        if name == "prompts":
            p.add_argument("--assignments", type=Path, required=True,
                           help="JSONL of {item_id, family, lens, arm, experiment_id}")
            p.add_argument("--out", type=Path, default=Path("run-prompts.jsonl"))

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--prompts", type=Path, required=True)
    ingest.add_argument("--responses", type=Path, required=True)
    ingest.add_argument("--out", type=Path, default=Path("runs.jsonl"))
    ingest.add_argument("--omp-version", default="unknown")

    args = ap.parse_args(argv)
    if args.cmd == "prompts":
        return cmd_prompts(args)
    if args.cmd == "ingest":
        return cmd_ingest(args)

    outcome = prepare(args)
    if isinstance(outcome, Refusal):
        json.dump({"decision": outcome.decision, "reason_code": outcome.reason_code,
                   "message": outcome.message, "item_id": args.item_id,
                   "family": args.family}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return outcome.exit_code

    if args.cmd == "plan":
        json.dump({"decision": "would_dispatch", "item_id": outcome.item_id,
                   "family": outcome.family, "lens": outcome.lens,
                   "requested_model": outcome.requested_model,
                   "provider_route": outcome.provider_route,
                   "agent": outcome.agent,
                   "packet_digest": outcome.packet_digest,
                   "rights_record_id": outcome.data_rights["record_id"],
                   "terms_snapshot_id": outcome.terms_snapshot_id,
                   "packet_bytes": len(json.dumps(outcome.packet))},
                  sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return EXIT_OK

    try:
        record = dispatch(outcome, args.transport)
    except EgressRefused as exc:
        json.dump({"decision": "deny", "reason_code": "egress_refused",
                   "message": str(exc)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return EXIT_DENY
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    json.dump(record, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
