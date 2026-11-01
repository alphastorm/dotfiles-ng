---
name: critical-review
description: Run independent cross-family review for production-grade, materially unbounded, shared, or hard-to-reverse changes.
---

# Critical Review Council

Run automatic, evidence-driven review. The active main-session model remains the accountable lead and integrator; deterministic policy selects the assurance class and the resolver selects the lead-family profile and reviewer standing. Critics inspect independently; they do not vote, rewrite the solution, edit the repository, or see one another's first-round output.

This document is the whole admission decision. Most invocations end inside it with direct lead verification or one resolver-selected focused review. Full-council mechanics are not here and are not needed to decide against a council. No review-class, provider, roster, or reviewer choice is a user authorization gate.

## Scope and authorization

Use this skill automatically when independent cross-family evidence is worth its cost because the change is production/customer/public-facing, materially unbounded, shared across operators or consumers, persistent or irreversible, supply-chain or public-compatibility sensitive, or has weak or costly rollback. Typical cases include broad authorization boundaries, production data or state, asset movement without a tight credible-loss cap, persistent migrations or deletion, public protocol compatibility, distributed coordination, releases, route promotion, and hard-to-reverse cross-system boundaries.

Do not select a full council merely because work is P0, mentions security, uses a credential, makes provider calls, spends a bounded amount, crosses a reversible internal boundary, or is a one-off experiment. Bounded experiments receive lead verification. Ordinary reusable internal paths receive lead verification plus one deterministic focused review of changed boundaries, except where a repository `REVIEW.md` floor classifies the changed paths as lead verification and no concrete residual consequence survives existing controls. An explicit user request for a full council selects the full-council path when hosted review is permitted.

### Automatic assurance selection — before ceremony

This is deterministic lead operation, not a new artifact, schema, service, user choice, or authorization gate. Perform it before `epoch.py scaffold`, record creation, freeze, receipts, or packet construction.

When the repository has a root `REVIEW.md`, read it before selection and apply
its deterministic changed-path risk floor; a model may raise that floor, never
lower it. Select the maximum of that minimum and the consequence-based class
below. The portable `lrhe/review_dispatch.py` exposes `risk_floor(paths)` for
Carrythrough's path rules. Use changed paths, not the supporting-context list.
`prepare` embeds the repository's committed `REVIEW.md` bytes and SHA-256 in
every task, and `verify-task` reconstructs them before dispatch. Packet-only
review carries applicable repository policy inside its immutable packet.
This policy supplements repository authority; it never authorizes an effect.
A repository's `REVIEW.md` or `AGENTS.md` round, successor, severity, and
disposition rules govern that repository and override this skill's review budget
and decision rules; this skill governs only what the repository leaves unstated.
`prepare` embeds only `REVIEW.md`, so a packet's `requirements` carry any
`AGENTS.md` rule a reviewer needs. Precedence never relaxes `NO_CLOUD`, the
hosted material policy, a required strong critic, or exact-head proof.

State concisely:

1. the current outcome or decision;
2. `bounded experiment`, `reusable internal path`, or `production/hard-to-reverse`;
3. the named assets and invariants;
4. the credible adversary and accidental-failure sources;
5. existing effect caps, containment, rollback/reconciliation, and residual consequence;
6. result-validity conditions; and
7. non-goals and the reasonable engineering budget.

Apply the first matching class without asking the user to choose:

1. explicit `NO_CLOUD` material: direct lead verification only; never dispatch hosted review, and record any unmet repository floor as unmet, never as independent review;
2. `production/hard-to-reverse`: one full council;
3. `reusable internal path`: one focused review;
4. `bounded experiment`: direct lead verification only.

Use the lowest class that both the repository floor and the credible residual consequence after existing controls permit; the `reusable internal path` label alone never raises a path that floor classifies as lead verification. Priority, credentials, provider calls, security vocabulary, or invoking this skill do not independently raise it. A reviewer may challenge the class only with a concrete omitted consequence that survives current controls. An explicit full-council request overrides steps 2–4 but never overrides `NO_CLOUD`. Never invoke Ask to choose an assurance class, provider, reviewer, roster, or review depth.

When the deterministic result is not a full council, stop before review ceremony. Perform the selected lead-only or focused path. Do not mint a review sequence, frozen epoch, receipt set, panel manifest, or ledger solely to justify not running a council.

#### Focused review routing

Only GPT/ChatGPT and Claude are qualified accountable leads. A focused review uses every strong critic — the reciprocal critic, review-claude-opus under CVP for a GPT lead or review-daybreak-blue for a Claude lead, and review-gemini-argon for both — plus the profile's `focusedSupplements`: review-gpt-astra for a Claude lead, none for a GPT lead. `qualification.yml` owns those profile entries, and `review_dispatch.py prepare --review-class focused` resolves every seat in profile order from the accountable lead family; the packet grants every seat's vendor and access profiles. The caller cannot name, replace, reorder, or add a reviewer.

Every strong critic is required cross-family independent evidence. Missing, refused, or invalid strong-critic results are missing, never approved. A configured focused supplement keeps its council supplement's standing: cross-family supplemental evidence that never replaces a strong critic or counts toward the independent floor. Its findings get the same lead verification and disposition as any other; a missing, refused, or invalid supplement result is recorded and never blocks closure or triggers a substitute. This routing does not modify the full council roster.

Routing selects the reviewer; it never states the reviewer's standing. The emitted Task payload is submitted verbatim. The resolver emits `selectionClass`, `role`, `independence_class`, and `authority` from the live authority; the lead never writes, derives, or hand-copies a standing field, and a reviewer reached any other way is not a review.

Review execution is a blocking Task boundary. Every `review-*` agent declares
`blocking: true`, so submit the resolver-emitted batch once and consume its
verdicts from that call. A focused review is a small resolver-emitted batch, not
a general delegation wave; never pad or trim it, detach it, poll `hub`/jobs, or
run sleep loops while the subject is frozen.

An enclosing eval call may auto-background while that protected Task is still
running. Its `bg_*` acknowledgement, an unavailable kernel result variable, or
failure to resolve that id through `output()` is orchestration state, not a Task
result and not failed reviewer delivery. Keep the original call as the sole
in-flight call and consume its eventual completion; never dispatch the envelope
again while that call is unresolved. The gate records every settled call beside
the envelope, so a lost kernel variable never loses the review; a failed seat
reruns alone from that record, once, and a seat that completed never runs again.

When a full council is justified, encode the same brief using the existing packet fields rather than adding keys: `goal` carries outcome and class; `requirements` carries named assets, caps, and result-validity requirements; `non_goals` carries excluded adversaries and reuse; `trust_boundaries` carries credible actors and boundaries; `rollback_contract` carries containment, recovery, and residual effects; and `rejected_alternatives_and_reasons` records disproportionate mitigations rejected before review.

### Hosted material policy

Both independent paths transmit reviewed material to hosted reviewers, so this floor applies before any dispatch, focused or full council:

1. Repository work and research are cloud-permitted by default. That default is standing unattended authorization for the exact live reviewers and access profiles selected by the resolver. Never invoke Ask merely to authorize a focused review, council, provider, or access profile.
2. Block hosted review only when the user, repository, or applicable customer policy explicitly marks the task or material `NO_CLOUD`; do not infer a separate confidential or local-only category. Never send `NO_CLOUD` material to a hosted reviewer.
3. An explicit provider list in the current request narrows the resolver to those providers for that review epoch. If the deterministic class cannot resolve its required roster under that restriction, do not ask, substitute, or shop fallbacks: use direct lead verification when the class permits it, otherwise report the explicit restriction as the blocker.
4. Populate `provider_data_allowlist` and `reviewer_access_profile_allowlist` mechanically from the deterministic class, lead-family profile, and live authority. These packet fields record the standing policy decision; they are not user prompts or caller-selected reviewer definitions.
5. Never include credential values, private keys, tokens, cookies, environment dumps, secret files, or generated credential stores in a packet. A source file containing secret-handling code may be reviewed under the standing cloud policy; redact actual values without changing the reviewed semantics.

Reviewer enablement, packet data grants, and access-profile matching for a full council are resolver-owned and live in the on-demand protocol below.

## Lead pre-dispatch obligations

These bind every dispatch, focused or full council. Reviewers render verdicts; they are never the primary defect-discovery loop for defects a checklist, grep, or meta-test could find.

### Review budget and pre-review checks

- Run one independent pass only when the selected class requires it — one focused review or the policy-required initial full council — then one batched remediation, then at most one round that examines only the fixes, and only when the batch mitigated a confirmed P0/P1 or the repository requires a closing round. Its spec carries non-empty `affected_invariants`. A clean first pass gets no second round. Never run a third round, including a separate refutation, unless a repository's recorded founder decision admits exactly one additional council in mode `founder-requested`; never a fourth. A new review ID, epoch, or day does not reset the budget.
- A P2/P3 finding never starts a round, an architecture review, or a general review; its dispositions follow the decision rules below.
- Escalation triggers: if a fix changes authority, acceptance criteria, spend or provider limits, irreversible effects, persistent schema or state semantics, or a locked architecture boundary, stop the loop and bring it to the founder as a design question, or to the repository's ADR route where one exists. A local helper, module, lock, regression test, or meta-test that changes none of these is ordinary remediation, not a founder decision.
- A fix-only round's packet carries the reviewer's meaning in existing fields: `goal` states that the round examines only the batched remediation, and `requirements` lists the `affected_invariants`, each prior finding's ID, anchors, and disposition, and that outside the remediation only a P0/P1 or a credential-path finding in the reviewed bytes is in scope. The generated assignment reproduces those fields; the reviewer template does not change.
- Before any review, run the smallest behavioral proof that exercises the changed contract; for stateful execution, retries, recovery, or publication, include the relevant failure and recovery transition, and name the boundaries left unproven. Bind the passing receipt to the exact head being reviewed; a unit suite or a receipt for another head is not this proof.
- About 800 changed lines is a scope warning, not a split order: split a change that spans unrelated responsibilities; a coherent change with generated output may be reviewed together.

The `lrhe/review_budget.py` CLI, run through the review venv, records each proof run with `e2e --lane-dir L --round N --head SHA --name NAME --cwd DIR -- argv...`; before dispatch, its `check --lane-dir L --round N --head SHA --spec PATH` enforces the budget. The JSON spec names `affected_invariants` for round 2. Round 3 additionally requires `--founder-decision PATH`: a `carrythrough.founder-decision.v1` record with `additional_full_councils` at least 1, `after_round` 2, and `after_head` equal to the head bound by `L/e2e-r2.json`; round 4 always refuses. The gate requires `L/e2e-rN.json` at that head, at least one run, and no failed runs. These admission limits also constrain the older epoch mechanics in the on-demand protocol; those mechanics never authorize an extra round.

### RepoPrompt context preparation

`skill://repoprompt-preparation` owns whether a work package gets the one
lead-run Context Builder preparation, its exceptions, and the submission and
recovery contract; never restate or vary it here. Review adds one deadline: a
preparation that applies has succeeded before every design review and every
full-council subject freeze. Its review evidence is the plan's `chat_id` and
`oracle_export_path`, cited before packet construction or freeze; treat the plan
as a preservation baseline for the accountable lead to check against current
evidence. RepoPrompt prose is never a reviewer verdict, standing, authorization,
frozen evidence, or acceptance proof, and reviewers do not receive it as an
independent finding. A failed, unusable, or unresolved preparation is never
rerun to unblock a review; continue without it.

### Runtime discovery boundary

A council never owns the iterations that make a qualification, golden/E2E, or
managed lifecycle path green. Before freeze or dispatch, apply the global
**Acceptance is never discovery** rule: exercise the smallest state-faithful
direct path, use a repository's bounded probe exception when it exists, and
close the first failing boundary with focused red → green evidence. Only then
freeze and review the complete candidate. Unless an explicit repository contract
orders otherwise, disposition the frozen review before one final acceptance run.
If that run fails, return to mutable diagnostic-red work; if the nearest fix
changes reviewed bytes or boundaries, use the normal remediation rules after
focused proof, without exceeding the review budget. Never turn council rounds,
freeze artifacts, or proof receipts into the runtime diagnosis harness.

1. Council is the last step, not the loop: dispatch only when the head is frozen, the known defects are fixed, and the rehearsal or smoke the live operation needs has passed.
2. Admissibility before dispatch. Before any round, run the repository's gate consumer on the changed paths' floor (Carrythrough: `scripts/review-verdict.py --repo . --base … --head …`) and name the receipt class it admits for the active row. A round whose receipt cannot admit is spend without payoff: for a focused-floor subject only a `focused`/`initial` receipt at the exact head admits, so `replay` rounds there are lead evidence, never the admitting row.
3. Rehearse before review. For an operator, migration, or restore path, the disposable-runtime rehearsal (real database, real schema, real binaries) precedes round 1; hermetic tests cannot see what the schema enforces (2026-10-19: four rounds missed stock immutability triggers that a nine-minute rehearsal found).
4. Self-execute the review's scrutiny checklist against the diff before any dispatch and close what it finds. Any instruction you would hand a reviewer is one you must already have run against your own change.
5. Close the class, not the instance. When a finding names one instance of a mechanical class — an invocation pattern, a predicate shape, a fixture convention — sweep the subject for the others in the same remediation and keep a regression for the reproduced one; add a class-wide invariant only when the class's recurrence or consequence justifies its maintenance.
6. Stop-rule: if the fix-only round confirms the same defect class again, halt the loop and record why it did not converge; then reduce scope, bring an escalation trigger to the founder or ADR route, use a recorded founder decision's one additional council, or continue in the repository's one successor PR under its own budget. Never re-dispatch the subject any other way.
7. Freeze the reviewed subject. Never edit the reviewed tree — including its policy and context files — while any reviewer is reading; queue such edits for the disposition commit. A verdict rendered over a moving target is discarded and the round is wasted.

## Full council at a glance

Every change admitted to the full council has one `review_sequence_id`. Every frozen epoch has one machine-readable `review-record.json`. `lrhe/review_sequence.py` remains the dispatch-action selector for admitted epochs; packet prose cannot override its result, and its legacy mode support cannot override the review budget check.

Preserve the full bound history: never relabel an epoch or reset a sequence. `material-redesign` and `founder-requested` obtain a general pass only where the governing round budget grants one: a repository round rule, or a recorded founder decision's one additional council. A correction that meets an escalation trigger ends the loop with a founder or ADR question, not another council. Any design-stage review also consumes a round; it is not an additional allowance.

An admitted epoch runs in one order: triage the draft record, freeze the complete change, mint subject-bound proof receipts, pass the full gate on the frozen record, then invoke one `review_dispatch.py prepare` command. That command resolves the roster and panel manifest, rejects incompatible evidence delivery, freezes the manifest-bound subject, resolves standing, builds the envelope, and runs `verify-task` before returning the exact Task payload. Submit it verbatim in one gated wave, normalize every returned item into the ledger, then close on the lead's dispositions. Triage may instead close the epoch on direct lead verification and mint nothing. No triage result authorizes a provider call, and reviewers never see one another's first-round output.

For repository subjects, `full_council.py prepare` automates preparation without selecting admission or submitting Tasks. Every implementation input (`initial`, `material-redesign`, or `founder-requested`) requires a `proofs` entry whose id starts with `e2e`; design inputs are exempt from that implementation-proof rule, not from the review budget.

Any selected reviewer with `evidence_delivery=repository` requires a clean repository subject bound to one full commit and every reviewed regular file. A packet-only subject is admissible only when every selected reviewer uses `inline` delivery and `design_or_diff` embeds a `critical-review-complete-inline-evidence-v1` bundle with every evidence artifact's complete UTF-8 content and matching SHA-256. A path, handle, summary, or excerpt supplied instead of that bundle never counts as inline evidence. Inside the bundle, `content` is the accountable producer's complete-byte declaration and the verifier treats it as opaque. The Task gate repeats this invariant from freshly read bytes.

Read `./lrhe/LIVE-PROTOCOL.md` only after admission selects a full council, and read it before any scaffold, record, freeze, receipt, manifest, or dispatch. It owns roster and provider authorization, the frozen record schema and subject binding, atomic preparation and internal Task verification, ledger scaffolding and outcome capture, targeted refutation, and close/report. Load it on demand; never expand it into a session that has not admitted a council. Its executable tools and schemas own those mechanical contracts; the governing round budget — a repository round rule, else the review budget above — controls admission and takes precedence over older round allowances.

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

The assignment is generated, never composed by hand. `review_dispatch.py prepare` resolves standing, builds the complete task text, and returns the only provider-ready payload; the lead submits it verbatim and writes none of it. The model's inputs are the frozen scope and packet, output paths, the accountable `lead_family`, the review class, and, for a canary probe only, the probed lane's reviewer id. The resolver emits every standing field from the live authority, and the Task gate revalidates the whole payload before any reviewer runs. Never write, derive, copy, or edit a reviewer's `selectionClass`, `role`, `independence_class`, or `authority`.

This is the shape the generated assignment takes:

```text
CRITICAL_REVIEW_RESOLVER_RECEIPT_V1
`receipt_sha256`: <resolved>
`subject_digest`: <resolved>
`subject_kind`: <repository|packet-only>
`subject_commit`: <clean full 40-hex commit|none>
`lead_family`: <gpt|claude>
`review_class`: <canary|focused|initial|targeted-refuter>
`reviewer_id`: <resolved>
`selectionClass`: <resolved>
`role`: <resolved>
`independence_class`: <resolved>
`authority`: <resolved>

# Target
For a repository subject: review only `repository_path` at the bound commit and exact regular-file list.
For a repository subject: retrieve the bound paths in batched turns — independent reads in the same turn, one whole-file or wide-range read over repeated narrow greps of the same path.
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

After the first review, the lead directly verifies and dispositions every finding. Implement confirmed, in-scope P0/P1 rows whose final disposition is `mitigate`, using existing mechanisms where they suffice, plus the small, obvious P2/P3 corrections the lead adds to the same batch; other P2/P3 rows get `accept` or `defer`. `accept`, `defer`, and `reject` rows create no remediation implementation. Batch the complete selected mitigation set into one remediation epoch; never create one epoch per comment or fix iteration. A targeted refutation consumes the remaining fix-only round rather than adding one, unless the repository round rule budgets it separately. If in-scope residual P0/P1 remains after that round, apply the stop-rule; otherwise record dispositions and close without a user prompt.

There is no majority verdict. The accountable main-session lead owns the final evidence-based decision and coherent revision.

Merge duplicates only when they share a root cause. Preserve every source ID on the merged row. Verify each cited source location before promoting a claim. Resolve important claims with the narrowest decisive evidence: a reproducer, failing test, call graph, interface implementation inventory, policy counterexample, migration rehearsal, rollback simulation, demonstrated authorization path, or concrete race schedule.

Decision rules:

- A confirmed P0 or P1 blocks closure only when it is **in-scope residual** severity. Assign severity after declared caps, containment, rollback, reconciliation, recovery, and credible-actor constraints—not before. Severity follows the finding's trigger and its consequence after those controls. Keep the reviewer's finding and severity unchanged in its ledger row and record the lead's evidence-backed re-rating separately in that row's `Rationale`, before the next round or any founder decision request. "The lead reads it" mitigates a truth defect only when the lead checks the claim against its source before handover.
- A stated invariant violation blocks only when the invariant is explicit, current, and within the declared scope; otherwise record a design decision or human tradeoff.
- An unresolved P0/P1 blocks only when the evidence supports a credible in-scope path to authorization, secret, money, irreversible-state, data-loss, or release-integrity impact after current controls.
- Findings are proposals, not implementation orders. `mitigate` means implement the smallest sufficient change; `accept`, `defer`, and `reject` are normal final dispositions.
- The lead records mitigation complexity, delivery delay, persistent state, maintenance, and newly introduced failure modes in `Rationale`. If mitigation cost is disproportionate to bounded residual impact, simplify, accept, defer, or reject it unless an explicit requirement mandates it.
- One empirical falsification outweighs repeated unsupported concern.
- One reproducible exploit outweighs repeated approval.
- P2/P3 findings do not block by default and receive a recorded `accept` or `defer`, except a small, obvious correction that joins the existing remediation batch as `mitigate`. A P2/P3 that defeats a mandatory acceptance requirement is an inconsistency to resolve under that requirement; its label never waives it.
- Every returned item receives a ledger row and final disposition. Not every item receives code.
- A finding whose adversary, protected asset, or consequence falls outside the packet's declared adversary, assets, and non-goals gets the default disposition `reject: outside declared adversary` — one ledger row, no implementation. Widening the declared scope is an owner decision made before the review, never a review output.
