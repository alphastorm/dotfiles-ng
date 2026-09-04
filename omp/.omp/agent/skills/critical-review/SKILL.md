---
name: critical-review
description: Run independent cross-family review for production-grade, materially unbounded, shared, or hard-to-reverse changes.
---

# Critical Review Council

Run automatic, evidence-driven review. The active main-session model remains the accountable lead and integrator; deterministic policy selects the assurance class and the resolver selects the lead-family profile and reviewer standing. Critics inspect independently; they do not vote, rewrite the solution, edit the repository, or see one another's first-round output.

This document is the whole admission decision. Most invocations end inside it with direct lead verification or one resolver-selected focused reviewer. Full-council mechanics are not here and are not needed to decide against a council. No review-class, provider, roster, or reviewer choice is a user authorization gate.

## Scope and authorization

Use this skill automatically when independent cross-family evidence is worth its cost because the change is production/customer/public-facing, materially unbounded, shared across operators or consumers, persistent or irreversible, supply-chain or public-compatibility sensitive, or has weak or costly rollback. Typical cases include broad authorization boundaries, production data or state, asset movement without a tight credible-loss cap, persistent migrations or deletion, public protocol compatibility, distributed coordination, releases, route promotion, and hard-to-reverse cross-system boundaries.

Do not select a full council merely because work is P0, mentions security, uses a credential, makes provider calls, spends a bounded amount, crosses a reversible internal boundary, or is a one-off experiment. Bounded experiments receive lead verification. Ordinary reusable internal paths receive lead verification plus one deterministic focused review of changed boundaries. An explicit user request for a full council selects the full-council path when hosted review is permitted.

### Automatic assurance selection — before ceremony

This is deterministic lead operation, not a new artifact, schema, service, user choice, or authorization gate. Perform it before `epoch.py scaffold`, record creation, freeze, receipts, or packet construction.

State concisely:

1. the current outcome or decision;
2. `bounded experiment`, `reusable internal path`, or `production/hard-to-reverse`;
3. the named assets and invariants;
4. the credible adversary and accidental-failure sources;
5. existing effect caps, containment, rollback/reconciliation, and residual consequence;
6. result-validity conditions; and
7. non-goals and the reasonable engineering budget.

Apply the first matching class without asking the user to choose:

1. explicit `NO_CLOUD` material: direct lead verification only; never dispatch hosted review;
2. `production/hard-to-reverse`: one full council;
3. `reusable internal path`: one focused review;
4. `bounded experiment`: direct lead verification only.

Use the lowest class consistent with the credible residual consequence. Priority, credentials, provider calls, security vocabulary, or invoking this skill do not independently raise it. A reviewer may challenge the class only with a concrete omitted consequence that survives current controls. An explicit full-council request overrides steps 2–4 but never overrides `NO_CLOUD`. Never invoke Ask to choose an assurance class, provider, reviewer, roster, or review depth.

When the deterministic result is not a full council, stop before review ceremony. Perform the selected lead-only or focused path. Do not mint a review sequence, frozen epoch, receipt set, panel manifest, or ledger solely to justify not running a council.

#### Focused review routing

Only GPT/ChatGPT and Claude are qualified accountable leads. A focused review always uses exactly one reciprocal cross-family strong critic: review-claude-opus under CVP for a GPT lead and review-daybreak-blue for a Claude lead. `qualification.yml` owns those profile entries, and `review_dispatch.py prepare --review-class focused` resolves the reviewer from the accountable lead family. The caller cannot name, replace, reorder, or add a reviewer.

Gemini and Grok remain full-council supplements, never focused alternatives. Review-claude-fable remains resolver-qualified architecture synthesis for full councils, not a focused shortcut. This routing does not modify the full council roster.

Routing selects the reviewer; it never states the reviewer's standing. The emitted Task payload is submitted verbatim. The resolver emits `selectionClass`, `role`, `independence_class`, and `authority` from the live authority; the lead never writes, derives, or hand-copies a standing field, and a reviewer reached any other way is not a review.

Review execution is a blocking Task boundary. Every `review-*` agent declares
`blocking: true`, so submit the resolver-emitted batch once and consume its
verdicts from that call. A focused review is a legitimate one-item batch, not a
general delegation wave; never pad it, detach it, poll `hub`/jobs, or run sleep
loops while the subject is frozen.

An enclosing eval call may auto-background while that protected Task is still
running. Its `bg_*` acknowledgement, an unavailable kernel result variable, or
failure to resolve that id through `output()` is orchestration state, not a Task
result and not failed reviewer delivery. Keep the original call as the sole
attempt and consume its eventual completion; never dispatch the envelope again
while that call is unresolved.

When a full council is justified, encode the same brief using the existing packet fields rather than adding keys: `goal` carries outcome and class; `requirements` carries named assets, caps, and result-validity requirements; `non_goals` carries excluded adversaries and reuse; `trust_boundaries` carries credible actors and boundaries; `rollback_contract` carries containment, recovery, and residual effects; and `rejected_alternatives_and_reasons` records disproportionate mitigations rejected before review.

### Hosted material policy

Both independent paths transmit reviewed material to hosted reviewers, so this floor applies before any dispatch, focused or full council:

1. Repository work and research are cloud-permitted by default. That default is standing unattended authorization for the exact live reviewers and access profiles selected by the resolver. Never invoke Ask merely to authorize a focused review, council, provider, access profile, or Oracle shadow.
2. Block hosted review only when the user, repository, or applicable customer policy explicitly marks the task or material `NO_CLOUD`; do not infer a separate confidential or local-only category. Never send `NO_CLOUD` material to a hosted reviewer.
3. An explicit provider list in the current request narrows the resolver to those providers for that review epoch. If the deterministic class cannot resolve its required roster under that restriction, do not ask, substitute, or shop fallbacks: use direct lead verification when the class permits it, otherwise report the explicit restriction as the blocker.
4. Populate `provider_data_allowlist` and `reviewer_access_profile_allowlist` mechanically from the deterministic class, lead-family profile, and live authority. These packet fields record the standing policy decision; they are not user prompts or caller-selected reviewer definitions.
5. Never include credential values, private keys, tokens, cookies, environment dumps, secret files, or generated credential stores in a packet. A source file containing secret-handling code may be reviewed under the standing cloud policy; redact actual values without changing the reviewed semantics.

Reviewer enablement, packet data grants, and access-profile matching for a full council are resolver-owned and live in the on-demand protocol below.

## Lead pre-dispatch obligations

These bind every dispatch, focused or full council. Reviewers render verdicts; they are never the primary defect-discovery loop for defects a checklist, grep, or meta-test could find.

### RepoPrompt context preparation

Before every design review and every full-council subject freeze, run exactly one
lead-only RepoPrompt Context Builder preparation for the current work package.
For focused review and implementation work, default to the same preparation when
the subject is technically complex, cross-cutting, multi-repository,
predecessor-state-sensitive, dynamically dispatched, or expensive to qualify.
Skip only for explicit `NO_CLOUD` material, an already-frozen subject,
diagnostic-red discovery, a narrow reproduced fix, mechanical work, or when the
same work package already has a successful preparation. This is context
discovery, not another review seat.

Use only the native RepoPromptCE MCP route. Bind the intended repository context;
if it is absent, bind its absolute `working_dirs` with `create_if_missing=true`.
Confirm its root with `get_file_tree`, then call `context_builder` once with
`response_type=plan` and `export_response=true`. Cite the returned `chat_id` or
`oracle_export_path` before mutation, packet construction, or freeze. Treat its
selection and plan as a preservation baseline for the accountable lead to check
against current evidence. RepoPrompt prose is never a reviewer verdict, standing,
authorization, frozen evidence, or acceptance proof, and reviewers do not receive
it as an independent finding. If the native route is unavailable, retry once after
checking health and binding, then continue without it; never rerun an unresolved
long request or invoke `rpce-cli`.

### Runtime discovery boundary

A council never owns the iterations that make a qualification, golden/E2E, or
managed lifecycle path green. Before freeze or dispatch, apply the global
**Runtime discovery before final acceptance** rule: exercise the smallest
state-faithful direct path, use a repository's bounded probe exception when it
exists, and close the first failing boundary with focused red → green evidence.
Only then freeze and review the complete candidate. Unless an explicit
repository contract orders otherwise, disposition the frozen review before one
final acceptance run. If that run fails, return to mutable diagnostic-red work;
if the nearest fix changes reviewed bytes or boundaries, use the normal
remediation rules after focused proof. Never turn council rounds, freeze
artifacts, or proof receipts into the runtime diagnosis harness.

1. Self-execute the review's scrutiny checklist against the diff before any dispatch and close what it finds. Any instruction you would hand a reviewer is one you must already have run against your own change.
2. Close classes, not instances. When a finding names one instance of a mechanical class — an invocation pattern, a predicate shape, a fixture convention — sweep the whole subject for the class in the same remediation and encode the class as an executable invariant (a meta-test that fails the suite). Policy prose alone never closes a class.
3. Stop-rule: if a round confirms the same defect class as the previous round, halt remediation and land the executable invariant before any re-dispatch.
4. Freeze the reviewed subject. Never edit the reviewed tree — including its policy and context files — while any reviewer is reading; queue such edits for the disposition commit. A verdict rendered over a moving target is discarded and the round is wasted.
5. Remediation-scoped verification defaults to the focused-reviewer lane; the full council renders only the sequence's budgeted general passes and the final verdict. Target at most two council rounds per subject.
6. Exit is by disposition, not zero findings: no in-scope residual P0/P1 and clean credential paths close the sequence, and the accountable lead records explicit residual dispositions for every remaining P2/P3 item without a user prompt. A supplemental seat's credential-path claim blocks only after lead verification against the committed bytes.

### Pragmatic full-council composition

The resolver uses three profile groups, not one role per model:

| Group | GPT lead | Claude lead | Standing |
|---|---|---|---|
| strongCritic | Opus 5 through the CVP-approved route | Daybreak Blue | reciprocal cross-family independent evidence |
| supplements | Gemini 3.7 Flash and Grok 4.6 | Gemini 3.7 Flash and Grok 4.6 | always-on cross-family supplemental evidence |
| architectureSpecialists | Fable 5 when eligible | Fable 5 when eligible | record-selected supplemental architecture synthesis |

The strong critic and supplements must be pairwise distinct by model family and
correlation group. Fable may intentionally share a lineage because its standing
is always supplemental.

The accountable lead already supplies its own family, so no same-family security
specialist runs by default. A future route-specific capability may justify one,
for example Opus CVP when a Claude lead lacks CVP access, but that requires an
explicit authority change rather than another permanent roster group.

ChatGPT Pro Web through pinned pi-oracle is an asynchronous shadow on every full
council, outside the resolver roster. It has no council standing, never blocks
closure or retries, receives no peer output, and never substitutes for a missing
qualified reviewer. The envelope selects it only when the packet separately grants
`openai` and the exact `chatgpt-pro-web-asxst0rm` access profile; otherwise it
records a nonblocking skip. A selected shadow uses `pro_extended` against the
frozen repository commit. Browser auth, submission, collection, or output-schema
failure is recorded for later evaluation and has no effect on the council.

## Full council at a glance

Every change admitted to the full council has one `review_sequence_id`. Every frozen epoch has one machine-readable `review-record.json`. `lrhe/review_sequence.py` is the sole dispatch-action selector; packet prose cannot override its result. Its modes are `design`, an optional pre-implementation council on the frozen design artifact; `initial`, the first general council for the sequence; `remediation`, a correction scoped to named findings, changed paths, and adjacent invariants; and `material-redesign`, a second general council only after the initial council's named P0/P1 findings are directly verified as resolved and the correction changes architecture, a trust boundary, public compatibility, persistent state, migration/rollback, or production effects.

One optional design pass, the initial pass, and at most one verified material redesign are the only general council passes. There is never a third implementation council, and one targeted refutation is the entire refutation budget for a sequence.

An admitted epoch runs in one order: triage the draft record, freeze the complete change, mint subject-bound proof receipts, pass the full gate on the frozen record, then invoke one `review_dispatch.py prepare` command. That command resolves the roster and panel manifest, rejects incompatible evidence delivery, freezes the manifest-bound subject, resolves standing, builds the envelope, and runs `verify-task` before returning the exact Task payload. Submit it verbatim in one gated wave, normalize every returned item into the ledger, then close on the lead's dispositions. Triage may instead close the epoch on direct lead verification and mint nothing. No triage result authorizes a provider call, and reviewers never see one another's first-round output.

Any selected reviewer with `evidence_delivery=repository` requires a clean repository subject bound to one full commit and every reviewed regular file. A packet-only subject is admissible only when every selected reviewer uses `inline` delivery and `design_or_diff` embeds a `critical-review-complete-inline-evidence-v1` bundle with every evidence artifact's complete UTF-8 content and matching SHA-256. A path, handle, summary, or excerpt supplied instead of that bundle never counts as inline evidence. Inside the bundle, `content` is the accountable producer's complete-byte declaration and the verifier treats it as opaque. The Task gate repeats this invariant from freshly read bytes.

Read `./lrhe/LIVE-PROTOCOL.md` only after admission selects a full council, and read it before any scaffold, record, freeze, receipt, manifest, or dispatch. It owns roster and provider authorization, the frozen record schema and subject binding, atomic preparation and internal Task verification, ledger scaffolding and outcome capture, targeted refutation, and close/report. Load it on demand; never expand it into a session that has not admitted a council. Its executable tools and the schemas beside them stay authoritative wherever prose could drift.

## Design-stage council

A design-stage council is selected only when the design itself establishes a production/hard-to-reverse boundary and correcting that boundary after implementation would be materially costlier than reviewing it now. Otherwise skip it. This rule is deterministic lead routing, not a user choice. Credentials or external effects alone do not select it. For bounded or reversible work, begin from the actual predecessor state and use the automatic lead-only or focused class above.

A supplied lifecycle state machine or failure matrix is review evidence, not a universal prerequisite. Require one only when the named outcome or explicit production policy needs it.

Skip a design council for small or reversible changes, bounded experiments, ordinary internal tooling, or when one review of the frozen implementation is sufficient, and never run one merely because the implementation may later receive a council.

`credentialed-external-lifecycle` is a descriptive risk domain. It records that a change combines live credentials with external effects and owns recovery, retry, concurrency, teardown, revocation, or uncertain-effect behavior. It never selects a design council, never requires a prior design full council, and never requires lifecycle artifacts. Supplied artifacts are optional evidence, and the executable gate validates them strictly when they are present.

For other consequential changes, prefer design review before code exists. The
shadow ledger's confirmed P0/P1 findings are dominantly design-level — ordering
that makes a commit unreachable, rollback that deletes its own recovery path,
single-check containment — and every one is cheaper to catch in the document
than in a remediation chain.

## Common reviewer assignment

Every dispatched reviewer receives this complete assignment. It is the canonical trusted assignment and the single owner of the shared review floor, including state fidelity; a private reviewer definition supplies the lens and output schema and never restates these requirements. Do not give reviewers caller-provided output schemas that weaken their agent schema.

The assignment is generated, never composed by hand. `review_dispatch.py prepare` resolves standing, builds the complete task text, and returns the only provider-ready payload; the lead submits it verbatim and writes none of it. The model's inputs are the frozen scope and packet, output paths, the accountable `lead_family`, the review class, and only the focused reviewer id when applicable. The resolver emits every standing field from the live authority, and the Task gate revalidates the whole payload before any reviewer runs. Never write, derive, copy, or edit a reviewer's `selectionClass`, `role`, `independence_class`, or `authority`.

This is the shape the generated assignment takes:

```text
CRITICAL_REVIEW_RESOLVER_RECEIPT_V1
`receipt_sha256`: <resolved>
`subject_digest`: <resolved>
`subject_kind`: <repository|packet-only>
`subject_commit`: <clean full 40-hex commit|none>
`lead_family`: <gpt|claude>
`review_class`: <focused|initial|targeted-refuter>
`reviewer_id`: <resolved>
`selectionClass`: <resolved>
`role`: <resolved>
`independence_class`: <resolved>
`authority`: <resolved>

# Target
For a repository subject: review only `repository_path` at the bound commit and exact regular-file list.
For a packet-only subject: inspect no path.
The verified assurance scope and packet bytes are reproduced in the generated task.
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

After an initial council, the lead directly verifies and dispositions every finding. Implement confirmed, in-scope P0/P1 rows whose final disposition is `mitigate`; implement a lower-severity item only when the lead verifies that it violates the current contract or is the smallest proportional closure of the same defect class. `accept`, `defer`, and `reject` rows create no remediation implementation. Batch the complete selected mitigation set into one remediation epoch; never create one epoch per comment or fix iteration. One still-disputed P0/P1 may reach one targeted refuter; otherwise the lead records ledger dispositions and closes the sequence without a user prompt.

There is no majority verdict. The accountable main-session lead owns the final evidence-based decision and coherent revision.

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
- A finding whose adversary, protected asset, or consequence falls outside the packet's declared adversary, assets, and non-goals gets the default disposition `reject: outside declared adversary` — one ledger row, no implementation. Widening the declared scope is an owner decision made before the review, never a review output.
- A review round never adds a persistent control, ADR, invariant, or lifecycle artifact by itself. A confirmed in-scope P0/P1 gets the smallest sufficient code change; a new persistent control requires the owning repository's threat model to name the residual consequence it prevents.
