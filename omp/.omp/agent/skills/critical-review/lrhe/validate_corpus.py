#!/usr/bin/env python3
"""
validate_corpus.py -- check a corpus JSONL against item.schema.json and against the
invariants the scorer relies on but JSON Schema cannot express.

JSON Schema catches shape errors. It does not catch the errors that actually corrupt
an evaluation: a label site anchored outside `repo_files` (which score_lrhe.py would
mark FABRICATED for a *correct* claim), an executable adjudication with no verify_cmd,
a trap item that leaks its own answer into the packet text, or a stratum whose item
count does not match the pre-registered sampling plan.

Usage:
  validate_corpus.py --corpus corpus.jsonl [--schema item.schema.json]
                     [--plan] [--strict] [--json report.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

EXECUTABLE_ADJUDICATIONS = {"fail_to_pass_test", "poc_reproduces"}

# Difficulty values that mark an item as a control: the harness or the PoC already
# says there is nothing to find, so a label would be a contradiction.
CONTROL_DIFFICULTIES = {
    "S2_PATCH_VERDICT": {"resolved_agent_patch"},
    "S3_VULN_POC": {"correct_fix_control"},
}

# Mirrors sources.license_class. Duplicated deliberately: the validator has to be
# able to catch a corpus built by an older or patched builder, which it cannot do
# if it imports the same classifier that produced the file.
_COPYLEFT = re.compile(r"^(A?GPL|LGPL|MPL|EPL|CDDL|OSL|EUPL|CECILL|SLEEPYCAT)", re.I)
_UNRESOLVED_LICENSES = {"NOASSERTION", "NONE", "UNKNOWN", "UNDECLARED", "OTHER", "SEE-REPO", ""}


def _license_class(license_id: str | None) -> str:
    lid = (license_id or "").strip().upper()
    if lid in _UNRESOLVED_LICENSES:
        return "unresolved"
    return "copyleft" if _COPYLEFT.match(lid) else "permissive"


# Pre-registered counts, LRHE-PROTOCOL.md section 2.
PLAN_COUNTS = {
    "S1_REVIEW_HUMAN": 14,
    "S2_PATCH_VERDICT": 10,
    "S3_VULN_POC": 8,
    "S4_FP_TRAP": 12,
    "S5_NULL": 3,
}
# S1 difficulty split, same section. The protocol names Type3_Latent; SWE-PRBench
# ships zero rows of it and 43 of Type3_Latent_Candidate, which is what the tier
# actually is. See PROVENANCE.md.
PLAN_S1_DIFFICULTY = {"Type1_Direct": 4, "Type2_Contextual": 5, "Type3_Latent_Candidate": 5}

# Surface identifiers the scrubber is supposed to have removed. Checked only when
# the item claims `scrubbed: true`, so an unscrubbed staging corpus stays quiet.
_PROSE_LEAKS = [
    ("url", re.compile(r"https?://\S+")),
    ("sha", re.compile(r"\b[0-9a-f]{7,40}\b")),
    ("cve", re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)),
    ("advisory", re.compile(r"\bGHSA-[\w-]{11,}\b", re.I)),
    ("issue_ref", re.compile(r"#\d{2,7}\b")),
]
# Only what `scrub_diff` claims to remove: git-regenerated provenance and
# identifiers that name the upstream fix. Never code content.
_DIFF_LEAKS = [
    ("index line", re.compile(r"^index [0-9a-f]{7,40}\.\.[0-9a-f]{7,40}", re.M)),
    ("forge url", re.compile(r"https?://\S*(github\.com|gitlab|bitbucket)\S*")),
    ("cve", re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)),
    ("advisory", re.compile(r"\bGHSA-[\w-]{11,}\b", re.I)),
]
_PROSE_FIELDS = ("goal", "problem_statement", "known_open_questions")
_PACKET_TEXT_FIELDS = ("goal", "problem_statement", "design_or_diff", "known_open_questions")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, item_id: str, msg: str) -> None:
        self.errors.append(f"{item_id}: {msg}")

    def warn(self, item_id: str, msg: str) -> None:
        self.warnings.append(f"{item_id}: {msg}")


def _norm(p: str) -> str:
    return p.strip().lstrip("./").replace("\\", "/")


def check_item(it: dict, rep: Report) -> None:
    iid = it.get("item_id", "<no id>")
    stratum = it.get("stratum", "")
    labels = it.get("labels", [])
    repo_files = {_norm(p) for p in it.get("repo_files", [])}

    # -- labels ------------------------------------------------------------
    seen_label_ids: set[str] = set()
    for lab in labels:
        lid = lab.get("label_id", "<no label_id>")
        if lid in seen_label_ids:
            rep.err(iid, f"duplicate label_id {lid!r}")
        seen_label_ids.add(lid)

        adj = lab.get("adjudication", "")
        has_verify = bool(lab.get("verify_cmd"))
        # The verdict lattice puts REFUTED above every judge verdict, but only an
        # executable label can ever reach that branch. An executable adjudication
        # with no verify_cmd silently demotes the item to judge-only.
        if adj in EXECUTABLE_ADJUDICATIONS and not has_verify:
            rep.err(iid, f"label {lid}: adjudication={adj} is executable but verify_cmd is missing")
        if has_verify and adj not in EXECUTABLE_ADJUDICATIONS:
            rep.warn(iid, f"label {lid}: verify_cmd present but adjudication={adj} is not executable")

        for site in lab.get("sites", []):
            path = _norm(site.get("path", ""))
            if not path:
                rep.err(iid, f"label {lid}: site with empty path")
                continue
            # score_lrhe.py auto-FABRICATEs any claim anchored outside repo_files.
            # A label site outside it makes the correct answer unscoreable.
            if repo_files and path not in repo_files:
                rep.err(iid, f"label {lid}: site {path!r} is not in repo_files")
            lines = site.get("lines")
            if lines is not None:
                if len(lines) != 2 or not all(isinstance(n, int) for n in lines):
                    rep.err(iid, f"label {lid}: site {path!r} lines must be [start, end] integers")
                elif lines[0] > lines[1]:
                    rep.err(iid, f"label {lid}: site {path!r} has inverted range {lines}")
                elif lines[0] < 1:
                    rep.err(iid, f"label {lid}: site {path!r} start line {lines[0]} < 1")

    # -- stratum invariants -------------------------------------------------
    if stratum == "S5_NULL":
        if labels:
            rep.err(iid, f"S5_NULL must carry zero labels, found {len(labels)}")
        if it.get("trap"):
            rep.err(iid, "S5_NULL must not carry a trap")
    elif stratum == "S4_FP_TRAP":
        trap = it.get("trap")
        if not trap:
            rep.err(iid, "S4_FP_TRAP requires a trap object")
        else:
            if trap.get("ground_truth") != "invalid":
                rep.err(iid, f"trap ground_truth must be 'invalid', got {trap.get('ground_truth')!r}")
            if not trap.get("assertion"):
                rep.err(iid, "trap has no assertion to plant in the packet")
            for site in trap.get("sites", []):
                path = _norm(site.get("path", ""))
                if repo_files and path and path not in repo_files:
                    rep.err(iid, f"trap site {path!r} is not in repo_files")
            # The trap must be plantable as a prior concern without the packet
            # revealing that it is false. Scope matters as much as the phrases:
            # scan only text the BUILDER authored. `problem_statement` embeds a
            # verbatim sanitizer report, and every ASan stack-overflow report ends
            # with "HINT: this may be a false positive if your program uses some
            # custom stack unwind mechanism" -- flagging tool boilerplate trains you
            # to ignore the check that catches a real mistake.
            authored = [it.get("goal", ""), it.get("known_open_questions", ""),
                        trap.get("assertion", ""), trap.get("framing", "")]
            blob = " ".join(str(x) for x in authored).lower()
            for tell in ("false positive", "not a real", "does not exist",
                         "is not actually", "no such vulnerability", "this is a trap",
                         "seeded", "ground truth", "the concern is invalid",
                         "finding is invalid", "not exploitable in practice"):
                if tell in blob:
                    rep.err(iid, f"packet text leaks the trap answer (contains {tell!r})")
    elif stratum in ("S1_REVIEW_HUMAN", "S2_PATCH_VERDICT", "S3_VULN_POC"):
        # S2 and S3 each ship deliberate controls -- a candidate patch the harness
        # passed, an ARVO fix whose PoC no longer reproduces. Like S5, their correct
        # output is silence, so zero labels is the right shape, not a build error.
        if stratum in CONTROL_DIFFICULTIES and it.get("difficulty") in CONTROL_DIFFICULTIES[stratum]:
            if labels:
                rep.err(iid, f"{it.get('difficulty')} is a control and must carry zero labels, "
                             f"found {len(labels)}")
        elif not labels:
            rep.err(iid, f"{stratum} requires at least one label")
        elif stratum == "S2_PATCH_VERDICT" and not any(
                lab.get("adjudication") in EXECUTABLE_ADJUDICATIONS for lab in labels):
            rep.err(iid, "S2_PATCH_VERDICT exists for its executable label; none is executable")
        elif stratum == "S3_VULN_POC" and not any(
                lab.get("adjudication") in EXECUTABLE_ADJUDICATIONS for lab in labels):
            # S3 was specified as execution-adjudicated, and it is not, because the
            # population it needed no longer exists in executable form: 116 faithful
            # paired runs found zero fixed images still crashing. Its positive labels
            # are ARVO's own corrections instead, which is a maintainer verdict --
            # a real published adjudication, but weaker than a container. Warn, do
            # not fail, and never average its recall with S2's.
            rep.warn(iid, "S3 label is maintainer_verdict, not executable; the REFUTED "
                          "branch of the verdict lattice is exercised only by S2")

    # -- provenance and the date gate --------------------------------------
    if not it.get("dataset_ref"):
        rep.warn(iid, "no dataset_ref; the corpus is not reproducible without a revision pin")
    # The egress rule, enforced rather than trusted. An allowlist that does not
    # follow from the recorded license means either the license was resolved after
    # the allowlist was written, or someone widened it by hand -- and the direction
    # that matters is the one where copyleft or unclassified source silently
    # acquires four provider destinations.
    allow = it.get("provider_data_allowlist") or []
    cls = _license_class(it.get("license"))
    if allow and cls == "unresolved":
        rep.err(iid, f"license {it.get('license')!r} is unresolved but the item is "
                     f"authorized for {allow}")
    elif allow and cls == "copyleft":
        rep.warn(iid, f"copyleft ({it.get('license')}) authorized for {len(allow)} providers; "
                      f"valid only if that policy decision was made deliberately")
    elif not allow:
        rep.warn(iid, f"not dispatchable: license {it.get('license')!r} ({cls})"
                      + (f" -- terms at {it['license_url']}" if it.get("license_url") else ""))
    gate = it.get("date_gate_cutoff")
    merged = it.get("merged_at")
    if gate and merged and merged[:10] <= gate:
        rep.err(iid, f"date gate violated: merged_at {merged[:10]} is not after cutoff {gate}")

    # -- scrub verification --------------------------------------------------
    if it.get("scrubbed"):
        for field in _PROSE_FIELDS:
            text = it.get(field)
            if not isinstance(text, str):
                continue
            for name, pat in _PROSE_LEAKS:
                m = pat.search(text)
                if m:
                    rep.err(iid, f"scrubbed=true but {field} still contains a {name}: "
                                 f"{m.group(0)[:60]!r}")
        # The diff is code, not prose about code. The prose patterns produce pure
        # noise on it -- an integer literal `2147483648` is not a commit sha and a
        # documentation URL inside a source comment is what the maintainers wrote.
        # Only provenance that git itself regenerates is checked here.
        diff = it.get("design_or_diff")
        if isinstance(diff, str):
            for name, pat in _DIFF_LEAKS:
                m = pat.search(diff)
                if m:
                    rep.err(iid, f"scrubbed=true but design_or_diff still contains a {name}: "
                                 f"{m.group(0)[:60]!r}")


def check_plan(corpus: list[dict], rep: Report) -> None:
    counts = Counter(it.get("stratum", "") for it in corpus)
    for stratum, want in PLAN_COUNTS.items():
        got = counts.get(stratum, 0)
        if got != want:
            rep.warn("<plan>", f"{stratum}: {got} items, pre-registered {want}")
    s1 = Counter(it.get("difficulty", "") for it in corpus if it.get("stratum") == "S1_REVIEW_HUMAN")
    for diff, want in PLAN_S1_DIFFICULTY.items():
        got = s1.get(diff, 0)
        if got != want:
            rep.warn("<plan>", f"S1 {diff}: {got} items, pre-registered {want}")

    ids = [it.get("item_id") for it in corpus]
    for iid, n in Counter(ids).items():
        if n > 1:
            rep.err("<plan>", f"duplicate item_id {iid!r} appears {n} times")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--schema", type=Path, default=Path(__file__).with_name("item.schema.json"))
    ap.add_argument("--plan", action="store_true",
                    help="also check counts against the pre-registered sampling plan")
    ap.add_argument("--strict", action="store_true", help="exit non-zero on warnings too")
    ap.add_argument("--json", type=Path, help="write the full report here")
    args = ap.parse_args(argv)

    corpus = []
    for n, line in enumerate(args.corpus.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            corpus.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"{args.corpus}:{n}: not valid JSON: {e}", file=sys.stderr)
            return 2

    rep = Report()

    try:
        import jsonschema
    except ImportError:
        rep.warn("<schema>", "jsonschema not installed; shape validation skipped")
    else:
        schema = json.loads(args.schema.read_text())
        validator = jsonschema.Draft202012Validator(schema)
        for it in corpus:
            iid = it.get("item_id", "<no id>")
            for e in validator.iter_errors(it):
                loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
                rep.err(iid, f"schema: {loc}: {e.message}")

    for it in corpus:
        check_item(it, rep)
    if args.plan:
        check_plan(corpus, rep)

    for w in rep.warnings:
        print(f"WARN  {w}")
    for e in rep.errors:
        print(f"ERROR {e}")
    print(f"\n{len(corpus)} items, {len(rep.errors)} errors, {len(rep.warnings)} warnings")

    if args.json:
        args.json.write_text(json.dumps(
            {"n_items": len(corpus), "errors": rep.errors, "warnings": rep.warnings}, indent=2) + "\n")

    if rep.errors:
        return 1
    if args.strict and rep.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
