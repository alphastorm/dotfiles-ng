#!/usr/bin/env python3
"""Verify what a reviewer packet would actually transmit, then grant a provider.

Authorization 2026-07-27 section 4 permits adding a provider to every item's
`provider_data_allowlist` *after* each generated packet passes six gates. This is
that check, run per item, machine-checked rather than asserted.

    ./.venv/bin/python check_packet_gates.py audit --corpus C --packets P
    ./.venv/bin/python check_packet_gates.py grant --corpus C --packets P --vendor opencode

The gates separate three failures that get conflated:

  * the packet contains something it must not          -- a leak, BLOCKING
  * the source carries an explicit incompatible term   -- a restriction, BLOCKING
  * the upstream licence never machine-resolved        -- unknown, REPORTED

Only the first two deny. The third is loud but not blocking: an unresolved detector
result is a fact about the detector, not about the licence, and section 4 is
explicit that a per-repository licensing adjudication is not to be started before
the smoke pass. It is equally explicit that such items are "reported rather than
silently changing the denominator", which is why they are named on every run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Exists to score a review; must never reach the reviewer doing it. `labels` is the
# answer key, `trap` carries the seeded finding's ground truth.
ORACLE_FIELDS = ("labels", "trap", "adjudication", "verify_cmd")

# Provenance the builder needs and the reviewer must not have. A reviewer who can
# identify the upstream repository can look up the real fix, which turns a blind
# review into a retrieval task and inflates every recall number without a trace.
PRIVATE_FIELDS = ("repo", "base_commit", "review_commit", "build_notes",
                  "merged_at", "source_item_id", "dataset_ref", "scrubbed")

# Provenance the SOURCE record must carry, so a packet can be re-derived and any
# transmission traced back to the revision it came from.
REQUIRED_PROVENANCE = ("source", "source_item_id", "dataset_ref")

# Deliberately narrow. A licence that failed to resolve is not a restriction; a
# licence that forbids redistribution or machine processing is.
EXPLICIT_RESTRICTIONS = ("no-redistribution", "noai", "no-ai-training",
                         "evaluation-only", "research-only", "non-commercial")
UNRESOLVED_LICENCES = ("NOASSERTION", "UNDECLARED", "", None)

# Prose the reviewer reads as instructions. The repository check runs against these
# and NOT against the diff, because a diff cannot be reviewed without its file
# paths and `livekit/agents` is a substring of `livekit-agents/livekit/agents/...`
# by construction. Naming the repository in prose is a different act: it hands the
# reviewer a search query.
PROSE_FIELDS = ("goal", "problem_statement", "known_open_questions")

# Private key headers, vendor token prefixes, and assigned secrets. A bare long hex
# run is NOT here: migration files carry schema hashes and diffs carry blob ids, so
# that rule fired on `_hash: 6fdbd1e8...` in a SQL fixture and on nothing else.
SECRET_PATTERNS = (
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"\b(?:bearer|authorization)\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{24,}", re.I)),
    ("provider_api_key", re.compile(r"\b(?:sk|pk|ghp|gho|xox[abpr])-[A-Za-z0-9_\-]{20,}\b")),
    ("assigned_secret", re.compile(r"\b(?:api[_-]?key|secret|passwd|password|token)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]", re.I)),
)


def gate_item(item: dict, packet: dict | None) -> tuple[list[str], list[str]]:
    """Return (blocking failures, non-blocking warnings) for one item."""
    if packet is None:
        # Cannot establish what would be transmitted. Section 4 lists this as its
        # own denial reason, distinct from finding a leak.
        return ["no_packet_generated"], []

    fails: list[str] = []
    warns: list[str] = []
    blob = json.dumps(packet, ensure_ascii=False)

    if leaked := [f for f in ORACLE_FIELDS if f in packet]:
        fails.append(f"oracle_leak:{'+'.join(leaked)}")

    # `ground_truth` is the constant enum "invalid" (item.schema.json), so testing
    # it as a substring asks whether the packet contains the word "invalid" -- which
    # a commit subject reading "output error for long/invalid names" satisfies.
    # `invalid_reason` is the specific taxonomy category and is the real leak.
    trap = item.get("trap") or {}
    if (reason := trap.get("invalid_reason")) and str(reason) in blob:
        fails.append("trap_invalid_reason_leak")

    if private := [f for f in PRIVATE_FIELDS if f in packet]:
        fails.append(f"private_metadata:{'+'.join(private)}")

    # Prose only. A diff cannot be reviewed without its paths, and an owner/repo
    # slug is a substring of its own source tree by construction. Naming the
    # repository in the instructions is the leak: "Review a candidate patch for
    # beetbox/beets issue 5495" is a search query, and the reviewer who runs it
    # finds the real fix.
    prose = " ".join(str(packet.get(f, "")) for f in PROSE_FIELDS)
    if (repo := item.get("repo")) and str(repo) in prose:
        fails.append("upstream_repo_named_in_prose")
    if re.search(r"\b(?:issue|pull request|PR)\s*#?\d{2,}", prose, re.I):
        fails.append("upstream_issue_number_in_prose")

    if hits := sorted({name for name, rx in SECRET_PATTERNS if rx.search(blob)}):
        fails.append(f"possible_secret:{'+'.join(hits)}")

    if missing := [f for f in REQUIRED_PROVENANCE if not item.get(f)]:
        fails.append(f"provenance_missing:{'+'.join(missing)}")
    if not (item.get("base_commit") or item.get("review_commit")):
        fails.append("no_source_revision")

    haystack = f"{item.get('license', '')} {item.get('license_url', '')}".lower()
    if restricted := [r for r in EXPLICIT_RESTRICTIONS if r in haystack]:
        fails.append(f"explicit_restriction:{'+'.join(restricted)}")

    if item.get("license") in UNRESOLVED_LICENCES:
        warns.append(f"licence_unresolved:{item.get('license')}")
    if not item.get("provider_data_allowlist"):
        warns.append("allowlist_empty")
    return fails, warns


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _as_list(value) -> list[str]:
    # The scrubber's projection has been seen to stringify this field. Accept both
    # rather than silently writing a list beside a string that says something else.
    if isinstance(value, str):
        return json.loads(value.replace("'", '"'))
    return list(value or [])


def run(args) -> int:
    items = _read(args.corpus)
    packets = {p["item_id"]: p for p in _read(args.packets)}
    results = {it["item_id"]: gate_item(it, packets.get(it["item_id"])) for it in items}
    blocked = {i: f for i, (f, _) in results.items() if f}
    flagged = {i: w for i, (_, w) in results.items() if w}

    print(f"{len(items)} items, {len(packets)} packets")
    print(f"  passed all six gates : {len(items) - len(blocked)}")
    print(f"  blocked              : {len(blocked)}")
    print(f"  passed with warnings : {len(flagged)}")
    for iid, reasons in sorted(blocked.items()):
        print(f"    BLOCKED {iid}  {'; '.join(reasons)}")
    for iid, reasons in sorted(flagged.items()):
        print(f"    warn    {iid}  {'; '.join(reasons)}")

    if args.cmd == "audit":
        return 1 if blocked else 0

    granted = 0
    for it in items:
        if results[it["item_id"]][0]:
            continue
        allow = _as_list(it.get("provider_data_allowlist"))
        if any(v not in allow for v in args.vendor):
            granted += 1
        it["provider_data_allowlist"] = sorted(set(allow) | set(args.vendor))
        # The packet carries its own copy. A reviewer-visible copy that disagrees
        # with the corpus is a rights record that cannot be trusted either way.
        if packet := packets.get(it["item_id"]):
            packet["provider_data_allowlist"] = it["provider_data_allowlist"]
    _write(args.corpus, items)
    _write(args.packets, [packets[it["item_id"]] for it in items if it["item_id"] in packets])

    print(f"\ngranted {', '.join(args.vendor)} to {granted} item(s)")
    if blocked:
        print(f"{len(blocked)} item(s) left denied; named above, not folded into the denominator")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("audit", "grant"):
        p = sub.add_parser(name)
        p.add_argument("--corpus", type=Path, required=True)
        p.add_argument("--packets", type=Path, required=True)
        if name == "grant":
            p.add_argument("--vendor", nargs="+", required=True,
                           help="allowlist tokens to add, e.g. opencode anthropic. Each "
                                "must match the dataAllowlistKey of a route it authorizes")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
