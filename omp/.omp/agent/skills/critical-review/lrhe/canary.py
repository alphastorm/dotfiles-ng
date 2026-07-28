#!/usr/bin/env python3
"""The three probes a lane must pass before councilEnabled may be flipped.

    ./.venv/bin/python canary.py selftest                    # graders vs known-bad
    ./.venv/bin/python canary.py run --family kimi --transport stub

`qualification.yml` records `schemaValid`, `readOnlyBoundary` and
`providerCanary` for every lane, and until now nothing produced any of them.
Three probes answer them, each built so the correct reply is known in advance:

  structured_output   does the reply validate against the reviewer's own output
                      schema? A reviewer whose schema fails returns free text,
                      and free text cannot be scored against a label.
  anchor_lookup       does every cited anchor exist in the packet? A reviewer
                      citing src/nowhere.py:12 has read nothing, and a review
                      made of plausible anchors is worse than no review, because
                      it survives a skim.
  empty_abstention    given a packet with nothing wrong in it, does the reviewer
                      return no evidence? A family that always finds something
                      scores zero precision on the trap stratum and reports it
                      as recall.

WHAT A NON-LIVE RUN PROVES. On `stub` the model is not exercised at all: the
reply comes from this repository. Such a run verifies the *graders* -- that a bad
reply is actually rejected -- which is worth doing first, because otherwise the
first paid request is also the first execution of the code that judges it. It is
not evidence about a provider, so `run` records `verdict: apparatus` and refuses
to emit a passed provider canary. Nothing here may be pasted into
qualification.yml as a lane's qualification.

WHY THIS DOES NOT BECOME A SIDE CHANNEL. `run_review.dispatch()` is the only
gated path to a provider, and this command deliberately does not reach it: the
probes are pre-qualification, and `prepare()` refuses a lane that is not yet
qualified -- which every lane needing a canary is. So the canary talks to the
transport table directly, and to stop that becoming an ungated egress path the
moment a live transport lands, it accepts only transports known not to leave the
machine. Pointing the canary at a provider has to be an edit to that set, made
deliberately and reviewed, not a flag someone passes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import run_review  # noqa: E402  -- needs the path above

SKILL = Path.home() / ".omp/agent/skills/critical-review"
AGENTS = Path.home() / ".omp/agent/agents"
DATA = SKILL / "lrhe-data"

EXIT_OK = 0
EXIT_FAILED = 10
EXIT_UNRESOLVED = 20

# Transports known not to leave the machine. A canary run on any of these is
# evidence about this repository, never about a provider.
NON_EGRESS = frozenset({"none", "stub"})

# `evidence=<path>:<line>` -- the anchor a reviewer claims to have read.
ANCHOR = re.compile(r"\|evidence=([^\s|:]+):")


@dataclass(frozen=True)
class Probe:
    probe_id: str
    question: str
    packet: dict[str, Any]
    grade: Callable[[str, dict[str, Any], dict[str, Any]], list[str]]
    known_bad: dict[str, Any]
    # Two kinds of probe. An apparatus probe judges the *shape* of a reply and
    # is meaningful against any reply, including a canned one -- which is how
    # this file caught the stub emitting evidence ids the schema forbids. A
    # judgement probe asks what the model chose to say, and a canned reply
    # cannot answer it: grading one on a stub measures the fixture's opinion,
    # not a reviewer's, and would report a permanent failure that means nothing.
    requires_judgement: bool = False


def _packet(item_id: str, goal: str, diff: str, files: list[str]) -> dict[str, Any]:
    """A packet in the shape the runner transmits, authored here.

    Synthetic on purpose: a probe whose right answer comes from the corpus would
    consume a labelled item to learn something that is not about the corpus, and
    would put the answer key on the wire to do it.
    """
    return {
        "item_id": item_id,
        "stratum": "CANARY",
        "goal": goal,
        "problem_statement": goal,
        "design_or_diff": diff,
        "repo_files": files,
        "license": "self-authored canary probe, not corpus content",
        "provider_data_allowlist": ["opencode", "anthropic", "google-antigravity", "xai"],
    }


# ------------------------------------------------------------------ graders

def _review_only(response: dict[str, Any]) -> dict[str, Any]:
    """The reviewer payload, without the transport's own accounting fields.

    The agent schema sets additionalProperties: false, so validating the whole
    transport envelope would fail on latency_ms and friends and report a schema
    violation the reviewer did not commit.
    """
    return {k: response.get(k) for k in ("summary", "evidence", "unresolved")}


def agent_output_schema(family: str) -> dict[str, Any] | None:
    """The reviewer's own declared output schema, or None when unreadable."""
    qual = SKILL / "qualification.yml"
    if not qual.is_file():
        return None
    entry = (yaml.safe_load(qual.read_text(encoding="utf-8")).get("reviewers") or {}).get(family)
    agent = AGENTS / f"{(entry or {}).get('agent', '')}.md"
    if not agent.is_file():
        return None
    text = agent.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    return (yaml.safe_load(text.split("---", 2)[1]) or {}).get("output")


def grade_structured_output(family: str, _packet_: dict, response: dict) -> list[str]:
    schema = agent_output_schema(family)
    if schema is None:
        return [f"no output schema found for {family}; cannot judge structure"]
    validator = Draft202012Validator(schema)
    return [f"schema: {e.json_path} {e.message}" for e in validator.iter_errors(_review_only(response))]


def grade_anchor_lookup(_family: str, packet: dict, response: dict) -> list[str]:
    known = set(packet.get("repo_files") or ())
    failures = []
    for item in response.get("evidence") or ():
        cited = ANCHOR.findall(str(item))
        if not cited:
            failures.append(f"anchor: no evidence=<path>:<line> in {str(item)[:60]!r}")
        failures += [f"anchor: {path!r} is not in the packet" for path in cited if path not in known]
    return failures


def grade_empty_abstention(_family: str, _packet_: dict, response: dict) -> list[str]:
    found = response.get("evidence") or []
    if found:
        return [f"abstention: {len(found)} finding(s) on a packet with nothing to find; "
                f"first is {str(found[0])[:80]!r}"]
    return []


PROBES: tuple[Probe, ...] = (
    Probe(
        probe_id="structured_output",
        question="does the reply validate against the reviewer's own output schema?",
        packet=_packet(
            "CANARY-structured",
            "Review a one-line change to a retry helper.",
            "@@ -1,3 +1,3 @@\n-    for attempt in range(3):\n+    for attempt in range(30):\n",
            ["src/canary/retry.py"],
        ),
        grade=grade_structured_output,
        known_bad={
            "summary": "looks fine",
            # R01 is the shape every synthetic generator in this repository used
            # until the canary rejected it: the schema requires ^R[1-9][0-9]*.
            "evidence": ["R01|P2|conf=0.50|claim=x|evidence=src/canary/retry.py:1|impact=x|verify=x"],
            "unresolved": [],
        },
    ),
    Probe(
        probe_id="anchor_lookup",
        question="does every cited anchor exist in the packet?",
        packet=_packet(
            "CANARY-anchor",
            "Review an authorization check for a missing negative case.",
            "@@ -10,6 +10,9 @@\n+def may_read(user, doc):\n+    return user.org_id == doc.org_id\n",
            ["src/canary/authz.py"],
        ),
        grade=grade_anchor_lookup,
        known_bad={
            "summary": "found a problem",
            "evidence": ["R1|P0|conf=0.90|claim=missing deny path"
                         "|evidence=src/canary/nowhere.py:42 observed|impact=authz|verify=test"],
            "unresolved": [],
        },
    ),
    Probe(
        probe_id="empty_abstention",
        question="given nothing to find, does the reviewer return nothing?",
        packet=_packet(
            "CANARY-abstain",
            "Review a documentation-only change.",
            "@@ -1,2 +1,2 @@\n-# Retry helper\n+# Retry helper. Retries are bounded by `attempts`.\n",
            ["docs/canary/retry.md"],
        ),
        grade=grade_empty_abstention,
        requires_judgement=True,
        known_bad={
            "summary": "several issues",
            "evidence": ["R1|P1|conf=0.70|claim=possible race in the retry loop"
                         "|evidence=docs/canary/retry.md:1 observed|impact=perf|verify=inspect"],
            "unresolved": [],
        },
    ),
)


# -------------------------------------------------------------------- runner

def _request(family: str, probe: Probe, entry: dict[str, Any]) -> run_review.AuthorizedRequest:
    return run_review.AuthorizedRequest(
        item_id=probe.packet["item_id"],
        family=family,
        lens=entry.get("lens", "floor"),
        arm="CANARY",
        experiment_id="canary",
        panel_id="canary",
        prompt_version="canary-v1",
        context_config="retrieval",
        requested_model=str(entry.get("model", "")),
        provider_route="canary",
        account_type="unknown",
        agent=str(entry.get("agent", "")),
        packet=probe.packet,
        data_rights={"decision": "not_applicable", "reason": "self-authored probe, no corpus content"},
        packet_digest=run_review._digest(probe.packet),
        assignment_manifest_digest="not-applicable",
        terms_snapshot_id="not-applicable",
    )


def _send(req: run_review.AuthorizedRequest, transport: str) -> dict[str, Any]:
    if transport not in NON_EGRESS:
        raise SystemExit(
            f"canary refuses transport {transport!r}: only {sorted(NON_EGRESS)} are known not "
            f"to leave this machine. Routing a probe to a provider is a change to the egress "
            f"boundary and belongs in run_review.py under review, not behind a flag here."
        )
    return run_review.TRANSPORTS[transport](req)


def cmd_run(args: argparse.Namespace) -> int:
    qual = SKILL / "qualification.yml"
    if not qual.is_file():
        print(f"{qual} not readable (private package not linked?)", file=sys.stderr)
        return EXIT_UNRESOLVED
    reviewers = yaml.safe_load(qual.read_text(encoding="utf-8")).get("reviewers") or {}
    families = [args.family] if args.family else sorted(reviewers)

    records, failed = [], False
    for family in families:
        entry = reviewers.get(family)
        if entry is None:
            print(f"{family}: no qualification.yml entry", file=sys.stderr)
            return EXIT_UNRESOLVED
        for probe in PROBES:
            skipped = probe.requires_judgement and args.transport in NON_EGRESS
            response = {} if skipped else _send(_request(family, probe, entry), args.transport)
            failures = [] if skipped else probe.grade(family, probe.packet, response)
            failed |= bool(failures)
            records.append({
                "schema": "lrhe-canary-v1",
                "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "family": family,
                "probe_id": probe.probe_id,
                "transport": args.transport,
                # A stub result is evidence about the graders and must never be
                # read as a passed lane, so the verdict says which it is.
                "verdict": "skipped" if skipped else
                           ("apparatus" if args.transport in NON_EGRESS else "provider"),
                "passed": None if skipped else not failures,
                "failures": failures,
                "requested_model": str(entry.get("model", "")),
            })
            mark = "--  " if skipped else ("ok  " if not failures else "FAIL")
            note = " (needs a real reply; a canned one cannot answer it)" if skipped else ""
            print(f"  {mark} {family:<9} {probe.probe_id:<18} {probe.question}{note}")
            for failure in failures:
                print(f"       {failure}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        print(f"\nappended {len(records)} record(s) to {args.out}")

    if args.transport in NON_EGRESS:
        print(f"\nverdict: apparatus. Transport {args.transport!r} did not reach a provider, so "
              f"this says the graders work and nothing about any lane. providerCanary stays "
              f"not-run.")
    return EXIT_FAILED if failed else EXIT_OK


def cmd_selftest(args: argparse.Namespace) -> int:
    """Every grader must reject its known-bad reply. A grader that cannot fail is decoration."""
    family = args.family
    broken = []
    for probe in PROBES:
        failures = probe.grade(family, probe.packet, probe.known_bad)
        if not failures:
            broken.append(f"{probe.probe_id}: accepted a reply built to fail it")
        print(f"  {'ok  ' if failures else 'FAIL'} {probe.probe_id:<18} "
              f"rejects known-bad: {failures[0] if failures else 'NO -- grader is blind'}")
    if broken:
        print("\n" + "\n".join(broken), file=sys.stderr)
        return EXIT_FAILED
    print(f"\n{len(PROBES)} graders reject the replies built to fail them.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="send each probe and grade the reply")
    run.add_argument("--family", help="one reviewer from qualification.yml (default: all)")
    run.add_argument("--transport", default="stub", help=f"default stub; non-egress: {sorted(NON_EGRESS)}")
    run.add_argument("--out", type=Path, default=DATA / "canary.jsonl",
                     help="JSONL to append results to (accumulated evidence, private package)")
    run.set_defaults(fn=cmd_run)

    selftest = sub.add_parser("selftest", help="prove each grader rejects a known-bad reply")
    selftest.add_argument("--family", default="claude",
                          help="whose output schema to judge structure against")
    selftest.set_defaults(fn=cmd_selftest)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
