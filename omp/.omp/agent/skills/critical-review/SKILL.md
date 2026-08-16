---
name: critical-review
description: Run independent cross-family review for production-grade, materially unbounded, shared, or hard-to-reverse changes.
---

# Critical Review Council

Run an opt-in, evidence-driven council. GPT remains the accountable lead and integrator. Critics inspect independently; they do not vote, rewrite the solution, edit the repository, or see one another's first-round output.

This document is the whole admission decision. Most invocations end inside it: choose the assurance depth, take direct lead verification or one focused reviewer, and stop. Full-council mechanics are not here and are not needed to decide against a council.

## Scope and authorization

Use this skill when independent cross-family evidence is worth its cost because the change is production/customer/public-facing, materially unbounded, shared across operators or consumers, persistent or irreversible, supply-chain or public-compatibility sensitive, or has weak or costly rollback. Typical cases include broad authorization boundaries, production data or state, asset movement without a tight credible-loss cap, persistent migrations or deletion, public protocol compatibility, distributed coordination, releases, route promotion, and hard-to-reverse cross-system boundaries.

Do not invoke a full council merely because work is P0, mentions security, uses a credential, makes provider calls, spends a bounded amount, crosses a reversible internal boundary, or is a one-off experiment. Bounded experiments default to lead verification and, when independent evidence is useful, may add one focused read-only reviewer outside this council. Ordinary reusable internal paths default to lead verification plus one focused independent review of changed boundaries. An explicit user request for a full council still applies.

### Assurance selection — before ceremony

This is lead judgment, not a new artifact, schema, service, or authorization gate. Perform it before `epoch.py scaffold`, record creation, provider authorization, freeze, receipts, or packet construction.

State concisely:

1. the current outcome or decision;
2. `bounded experiment`, `reusable internal path`, or `production/hard-to-reverse`;
3. the named assets and invariants;
4. the credible adversary and accidental-failure sources;
5. existing effect caps, containment, rollback/reconciliation, and residual consequence;
6. result-validity conditions; and
7. non-goals and the reasonable engineering budget.

Use the lowest class consistent with the credible residual consequence. Priority, credentials, provider calls, security vocabulary, or invoking this skill do not independently raise it. A reviewer may challenge the class only with a concrete omitted consequence that survives current controls.

If the full-council criteria are not met, stop before review ceremony. Complete direct lead verification and optionally use one ordinary focused read-only reviewer. Do not mint a review sequence, frozen epoch, receipt set, packet, panel manifest, or ledger solely to justify not running a council.

#### Focused review routing

For the single focused reviewer outside this council:

- security, credentials, authorization, money or assets, and trust boundaries default to `review-claude-opus` under CVP;
- non-security architecture or operational correctness defaults to `review-claude`;
- migration, production, compatibility, or cross-system behavior uses `review-gemini` or `review-grok` when its lens better matches the concrete question or the default lane is unavailable or unauthorized.

Use exactly one focused reviewer; do not escalate to multiple reviewers by fallback shopping. `review-daybreak-blue` remains supplemental only. This routing neither selects nor modifies a full council; full-council membership remains resolver-owned.

When a full council is justified, encode the same brief using the existing packet fields rather than adding keys: `goal` carries outcome and class; `requirements` carries named assets, caps, and result-validity requirements; `non_goals` carries excluded adversaries and reuse; `trust_boundaries` carries credible actors and boundaries; `rollback_contract` carries containment, recovery, and residual effects; and `rejected_alternatives_and_reasons` records disproportionate mitigations rejected before review.

### Hosted material authorization

Both paths transmit reviewed material to a hosted reviewer, so this floor applies before any dispatch, focused or full council:

1. Treat repository work and research as cloud-permitted by default. Block hosted review only when the user, repository, or applicable customer policy explicitly marks the task or material `NO_CLOUD`; do not infer a separate confidential or local-only category.
2. Treat an explicit provider list in the user's current `/skill:critical-review` request as authorization for only those providers and this review epoch.
3. If hosted-provider authorization is absent or unclear, invoke Ask before transmitting material. Recommend the safest qualified subset and include a no-effect/non-cloud option. Never send `NO_CLOUD` material to a hosted reviewer.
4. Never include credential values, private keys, tokens, cookies, environment dumps, secret files, or generated credential stores in a packet. A source file containing secret-handling code may be reviewed only when the chosen providers are authorized for it; redact actual values without changing the reviewed semantics.

Reviewer enablement, packet data grants, and access-profile matching for a full council are resolver-owned and live in the on-demand protocol below.

## Full council at a glance

Every change admitted to the full council has one `review_sequence_id`. Every frozen epoch has one machine-readable `review-record.json`. `lrhe/review_sequence.py` is the sole dispatch-action selector; packet prose cannot override its result. Its modes are `design`, an optional pre-implementation council on the frozen design artifact; `initial`, the first general council for the sequence; `remediation`, a correction scoped to named findings, changed paths, and adjacent invariants; and `material-redesign`, a second general council only after the initial council's named P0/P1 findings are directly verified as resolved and the correction changes architecture, a trust boundary, public compatibility, persistent state, migration/rollback, or production effects.

One optional design pass, the initial pass, and at most one verified material redesign are the only general council passes. There is never a third implementation council, and one targeted refutation is the entire refutation budget for a sequence.

An admitted epoch runs in one order: triage the draft record, freeze the complete change, mint subject-bound proof receipts, pass the full gate on the frozen record, resolve the panel manifest, dispatch every selected reviewer in one concurrent wave, normalize every returned item into the ledger, then close on the lead's dispositions. Triage may instead close the epoch on direct lead verification and mint nothing. No triage result authorizes a provider call, and reviewers never see one another's first-round output.

Read `./lrhe/LIVE-PROTOCOL.md` only after admission selects a full council, and read it before any scaffold, record, freeze, receipt, manifest, or dispatch. It owns roster and provider authorization, the frozen record schema and subject binding, freeze and receipt commands, panel resolution, dispatch and the single bounded retry, ledger scaffolding and outcome capture, targeted refutation, and close/report. Load it on demand; never expand it into a session that has not admitted a council. Its executable tools and the schemas beside them stay authoritative wherever prose could drift.

## Design-stage council

A design-stage council is optional. Use it when the design itself establishes a
production/hard-to-reverse boundary and early cross-family review is likely
cheaper than correcting implementation. Credentials or external effects alone
do not require it. For bounded or reversible work, begin from the actual
predecessor state and use direct lead verification or one focused reviewer.

A supplied lifecycle state machine or failure matrix is review evidence, not a
universal prerequisite. Require one only when the named outcome or explicit
production policy needs it.

Skip a design council for small or reversible changes, bounded experiments, ordinary internal tooling, or when one review of the frozen implementation is sufficient, and never run one merely because the implementation may later receive a council.

`credentialed-external-lifecycle` is a descriptive risk domain. It records that a change combines live credentials with external effects and owns recovery, retry, concurrency, teardown, revocation, or uncertain-effect behavior. It never selects a design council, never requires a prior design full council, and never requires lifecycle artifacts. Supplied artifacts are optional evidence, and the executable gate validates them strictly when they are present.

For other consequential changes, prefer design review before code exists. The
shadow ledger's confirmed P0/P1 findings are dominantly design-level — ordering
that makes a commit unreachable, rollback that deletes its own recovery path,
single-check containment — and every one is cheaper to catch in the document
than in a remediation chain.

## Common reviewer assignment

Every dispatched reviewer receives this complete assignment. It is the canonical trusted assignment and the single owner of the shared review floor, including state fidelity; a private reviewer definition supplies the lens and output schema and never restates these requirements. Do not give reviewers caller-provided output schemas that weaken their agent schema.

Use this complete assignment shape:

```text
# Target
For `repository` delivery: review the immutable packet at <packet path> and only the repository epoch it identifies.
For `inline` delivery: review only the complete immutable packet and numbered source evidence pasted below; do not inspect any path.
Do not modify files or inspect peer output.

# Assurance scope
The packet's class, outcome, named assets and invariants, credible adversary, caps, recovery contract, result-validity conditions, and non-goals are binding. Do not promote the class, invent future consumers, expand the adversary model, or turn defense in depth into a requirement. Assess impact after current controls. Complexity, delivery delay, persistent state, maintenance, and newly introduced failure modes are adverse effects. Return no finding unless a falsifiable in-scope failure leaves meaningful residual impact; zero findings is valid.

# Change
Apply the common critical floor and the primary lens defined by your agent. Return falsifiable root-cause claims with exact evidence. This is review only; no implementation or competing rewrite.

For `inline` delivery, paste the complete packet, diff or design, and line-numbered source evidence here. Never substitute a path, summary, or source excerpt that omits reviewed behavior.

# State fidelity
Before accepting readiness or lifecycle claims, compare the implementation's assumed starting state with the bound predecessor evidence. A fresh-state fixture, status command, schema, review, or generated packet does not prove a successor transition. Report the smallest concrete mismatch; do not respond by designing a generalized recovery system unless the declared consequence requires one.

# Acceptance
Return one schema-valid summary/evidence/unresolved object, at most 12 evidence items, exact anchors present in the supplied evidence, and explicit missing evidence for unresolved claims.
Every evidence item must identify the protected asset or invariant and the residual consequence after declared controls. Do not report general hardening or speculative future-proofing as a defect.
```

## Lead verification and dispositions

After an initial council, the lead directly verifies and dispositions every finding. Implement only confirmed, in-scope P0/P1 rows whose final disposition is `mitigate`, plus any lower-severity item explicitly selected by the human owner. `accept`, `defer`, and `reject` rows create no remediation implementation. Batch the complete selected mitigation set into one remediation epoch; never create one epoch per comment or fix iteration. One still-disputed P0/P1 may reach one targeted refuter; otherwise the lead records ledger dispositions and closes the sequence.

There is no majority verdict. The GPT lead owns the final evidence-based decision and coherent revision.

Merge duplicates only when they share a root cause. Preserve every source ID on the merged row. Verify each cited source location before promoting a claim. Resolve important claims with the narrowest decisive evidence: a reproducer, failing test, call graph, interface implementation inventory, policy counterexample, migration rehearsal, rollback simulation, demonstrated authorization path, or concrete race schedule.

Decision rules:

- A confirmed P0 or P1 blocks closure only when it is **in-scope residual** severity. Assign severity after declared caps, containment, rollback, reconciliation, recovery, and credible-actor constraints—not before.
- A stated invariant violation blocks only when the invariant is explicit, current, and within the declared scope; otherwise record a design decision or human tradeoff.
- An unresolved P0/P1 blocks only when the evidence supports a credible in-scope path to authorization, secret, money, irreversible-state, data-loss, or release-integrity impact after current controls.
- Findings are proposals, not implementation orders. `mitigate` means implement the smallest sufficient change; `accept`, `defer`, and `reject` are normal final dispositions.
- The lead records mitigation complexity, delivery delay, persistent state, maintenance, and newly introduced failure modes in `Rationale`. If mitigation cost is disproportionate to bounded residual impact, simplify, accept, defer, or reject it unless an explicit requirement mandates it.
- One empirical falsification outweighs repeated unsupported concern.
- One reproducible exploit outweighs repeated approval.
- P2/P3 items receive explicit dispositions but do not trigger open-ended debate or implementation by default.
- Every returned item receives a ledger row and final disposition. Not every item receives code.
