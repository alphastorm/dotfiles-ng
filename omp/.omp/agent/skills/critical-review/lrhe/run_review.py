#!/usr/bin/env python3
"""Assemble one reviewer request, or refuse to.

    ./.venv/bin/python run_review.py plan     --item-id S1-... --family claude
    ./.venv/bin/python run_review.py dispatch --item-id S1-... --family claude --transport stub

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
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
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
            f"R01|P2|conf=0.50|claim=stub finding {seed[:8]}"
            f"|evidence={(req.packet.get('repo_files') or ['src/unknown.py'])[0]}:1 observed"
            f"|impact=stub|verify=stub"
        ],
        "unresolved": [],
        "latency_ms": 1000 + int(seed[:4], 16) % 1000,
        "input_tokens": 1000,
        "output_tokens": 100,
        "cost_usd": 0.0,
        "tool_violations": 0,
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
        capture_output=True, text=True, cwd=HERE)
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

def dispatch(req: AuthorizedRequest, transport: str, *,
             omp_version: str = "unknown") -> dict[str, Any]:
    """Send, and build the run record. Takes an AuthorizedRequest and nothing else.

    The rights record is re-validated here even though `prepare()` produced it.
    That is not redundancy for its own sake: it is the check that survives someone
    constructing an AuthorizedRequest by hand, which Python will happily let them
    do. A request whose evidence does not validate is refused at the last step.
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

    send = TRANSPORTS.get(transport)
    if send is None:
        raise EgressRefused(
            f"transport {transport!r} is not implemented. 'live' is deliberately "
            f"absent: no provider credential is configured and provider calls are "
            f"held pending the OMP upgrade")

    started = datetime.now(timezone.utc)
    response = send(req)
    completed = datetime.now(timezone.utc)
    record = _run_record(req, response, started, completed, transport, omp_version)

    # The scorer refuses a record that does not validate, and it is better to find
    # that out here -- with the response still in hand -- than after a paid run has
    # been written to disk in a shape nothing will read.
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
            "telemetry_complete": True,
            "schema_valid": True,
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


# ------------------------------------------------------------------- driver

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "dispatch"):
        p = sub.add_parser(name)
        p.add_argument("--item-id", required=True)
        p.add_argument("--family", required=True)
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
    args = ap.parse_args(argv)

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
