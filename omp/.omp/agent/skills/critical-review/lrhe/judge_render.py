#!/usr/bin/env python3
"""
judge_render.py -- the one place a judge's evidence surface is composed.

Two callers render adjudication prompts: `judge_lrhe.py`, which produced the
canonical panel, and `auto_reliability.py`, which re-judges a sample of that
panel's claims with fresh families. The audit's whole question is whether a
different panel decides the same cases the same way, so the two prompts must be
byte-identical for the same (item, claim). A second copy of the template would
make any divergence between them silent, and the divergence would be read as a
disagreement between panels.

Nothing here does I/O or takes a decision. It is a pure function and two strings
so that a test can assert the canonical and audit surfaces are the same object.
"""
from __future__ import annotations

# Verbatim from SWE-PRBench `dataset/rubric.md` section 1. Reused rather than
# reinvented: the protocol's comparison point is their published baseline, and a
# different rubric would silently make the numbers incomparable.
RUBRIC = """\
CONFIRMED -- all of the following hold:
  1. The comment identifies a specific issue in the code.
  2. A ground-truth label describes the SAME underlying issue (wording may differ).
  3. The issue concerns the same file or functional area.
  4. The concern would lead to the same kind of code change as the label.

PLAUSIBLE:
  1. The comment is grounded in code visible in the provided context.
  2. The observation is factually correct about the code.
  3. A reasonable engineer might raise the concern.
  4. NO ground-truth label describes this specific concern.

FABRICATED -- any of the following holds:
  1. References code, functions or behavior not present in the provided context.
  2. Makes factually incorrect claims about the code.
  3. Describes a bug that does not exist in the shown code.
  4. Invents method signatures, variable names or behavior.
"""

JUDGE_PROMPT = """\
You are adjudicating one code-review comment against the ground truth for a change.
You did not write this comment. Judge it; do not improve it.

{rubric}
Return JSON only:
  {{"verdict": "CONFIRMED"|"PLAUSIBLE"|"FABRICATED",
    "label_id": "<id of the matched ground-truth label, or null>",
    "confidence": 0.0-1.0,
    "rationale": "<one sentence>"}}

PLAUSIBLE is not a failure grade. Human review is not exhaustive, so a correct
observation that no label happens to cover is PLAUSIBLE, never FABRICATED.
Reserve FABRICATED for claims the code in front of you contradicts.

--- CHANGE UNDER REVIEW ---
{goal}

{problem}

--- FILES IN SCOPE ---
{files}

--- DIFF ---
{diff}

--- GROUND-TRUTH LABELS ---
{labels}

--- COMMENT TO ADJUDICATE ---
severity asserted: P{severity}   confidence asserted: {confidence}
claim:    {claim}
evidence: {evidence}
impact:   {impact}
"""

# Truncation is part of the evidence surface, not a display detail: two panels
# shown different amounts of the same diff are not judging the same case.
PROBLEM_CHARS = 4000
DIFF_CHARS = 60000
LABEL_DESC_CHARS = 400


def label_ids(item: dict) -> list[str]:
    """The label set this item's judgements may cite, in prompt order."""
    return [str(lab["label_id"]) for lab in (item.get("labels") or []) if lab.get("label_id")]


def render_labels(item: dict) -> str:
    labels = item.get("labels") or []
    return "\n".join(
        f"  [{lab['label_id']}] severity P{lab.get('severity')} "
        f"{'/'.join(s['path'] for s in lab.get('sites', []))}: "
        f"{(lab.get('description') or '')[:LABEL_DESC_CHARS]}"
        for lab in labels) or "  (none -- this item has no ground-truth defects)"


def render_judge(item: dict, claim: dict) -> str:
    """The adjudication prompt for one claim. Pure; the same inputs give the same bytes."""
    return JUDGE_PROMPT.format(
        rubric=RUBRIC, goal=item.get("goal", ""),
        problem=(item.get("problem_statement") or "")[:PROBLEM_CHARS],
        files="\n".join(f"  {p}" for p in item.get("repo_files", [])) or "  (unspecified)",
        diff=(item.get("design_or_diff") or "")[:DIFF_CHARS], labels=render_labels(item),
        severity=claim.get("severity", ""), confidence=claim.get("confidence", ""),
        claim=claim.get("claim_text", ""), evidence=claim.get("evidence_text", ""),
        impact=claim.get("impact_text", ""))


def evidence_surface(item: dict) -> str:
    """Everything the judge is shown ABOUT THE CODE, without the comment to adjudicate.

    A control asserting that the shown code contradicts a claim has to be checked
    against the code, not against the rendered prompt: the prompt embeds the claim,
    so an invented symbol always "appears" in it and every such check passes for the
    wrong reason. Same truncations as `render_judge`, because a token beyond the cut
    is not on the surface the judge actually saw.
    """
    return "\n".join((
        item.get("goal", ""),
        (item.get("problem_statement") or "")[:PROBLEM_CHARS],
        "\n".join(f"  {p}" for p in item.get("repo_files", [])),
        (item.get("design_or_diff") or "")[:DIFF_CHARS],
        render_labels(item),
    ))
