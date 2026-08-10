---
name: critical-review
description: Run independent cross-family review for consequential or hard-to-reverse changes.
---

# Critical Review Council

Run an opt-in, evidence-driven council. GPT remains the accountable lead and integrator. Critics inspect independently; they do not vote, rewrite the solution, edit the repository, or see one another's first-round output.

## Scope and authorization

Use this skill for architecture, authentication or authorization, secrets or cryptography, privacy, money or asset movement, persistent migrations or deletion, public protocol compatibility, concurrency or distributed coordination, release or supply-chain changes, cross-system boundaries, and weak or costly rollback.

Live reviewer membership and roles are configuration, not prose:

- `skill://critical-review/qualification.yml` `liveDispatch` is the sole
  authoritative live panel definition;
- `lrhe/qualification.py` is the sole executable resolver for that definition;
- `leadFamily` records the accountable GPT lead and MUST NOT appear in either
  `initialCritics` or `targetedRefuters`;
- `initialCritics` and `targetedRefuters` are distinct dispatch roles;
- `evaluationOnly` lanes and every experiment in `lrhe/panels.yaml` never
  authorize live review dispatch.

Before dispatch:

1. Treat repository work and research as cloud-permitted by default. Block hosted review only when the user, repository, or applicable customer policy explicitly marks the task or material `NO_CLOUD`; do not infer a separate confidential or local-only category.
2. Read `skill://critical-review/qualification.yml`. A provider is enabled only when its family is in the required `liveDispatch` role, its reviewer entry says `dispatchEnabled: true`, its canary and read-only gates pass, its exact model selector still resolves, and the packet authorizes that provider.
3. Treat an explicit provider list in the user's current `/skill:critical-review` request as authorization for only those providers and this review epoch.
4. If hosted-provider authorization is absent or unclear, invoke Ask before transmitting material. Recommend the safest qualified subset and include a no-effect/non-cloud option. Never send `NO_CLOUD` material to a hosted reviewer.
5. Never include credential values, private keys, tokens, cookies, environment dumps, secret files, or generated credential stores in a packet. A source file containing secret-handling code may be reviewed only when the chosen providers are authorized for it; redact actual values without changing the reviewed semantics.

A missing, disabled, timed-out, schema-invalid, or unauthorized reviewer is `missing`, never `approved`. Do not substitute another GPT model for an unavailable external family.

### Internal resource compatibility

When inspecting resources below `skill://critical-review/`:

- Enumerate fixture files by reading the directory URL, for example
  `read skill://critical-review/lrhe/fixtures`, then read the returned file URLs
  explicitly. Never pass an internal URL glob to `glob`.
- Read multiple `skill://` files with separate parallel `read` calls. Never
  semicolon-delimit internal URLs in one `read` path.
- If glob matching is still needed, use the resolved filesystem path displayed
  by the directory `read` as the glob root instead of the internal URL.

## Sequence and readiness gate

Every consequential change has one `review_sequence_id`. Every frozen epoch has
one machine-readable `review-record.json`. `lrhe/review_sequence.py` is the sole
dispatch-action selector; packet prose cannot override its result. Its modes are:

- `design`: an optional pre-implementation council on the frozen design
  artifact; at most one per sequence, always the first epoch, and it never
  counts toward the two general implementation passes;
- `initial`: the first general council for the sequence;
- `remediation`: a correction scoped to named findings, changed paths, and
  adjacent invariants;
- `material-redesign`: a second general council only after the initial council's
  named P0/P1 findings are directly verified as resolved and the correction
  changes architecture, a trust boundary, public compatibility, persistent state,
  migration/rollback, or production effects.

The frozen machine record must contain exactly these fields; any additional key fails closed:

1. `review_sequence_id`, a unique `review_id`, `review_mode`, `parent_review_id`,
   and ordered `sequence_history` whose rows bind each prior epoch record by
   path and SHA-256, plus history-derived `general_review_pass_count` (design
   epochs never count) and `targeted_refutation_used`;
2. `artifact_path` and SHA-256, every `changed_file` and its current SHA-256
   (`DELETED` for a deletion), and proof-receipt paths and SHA-256 values;
3. every touched risk domain and an invariant proof matrix whose rows collectively
   cover every changed path and every touched domain;
4. each proof class as `passed` with a subject-bound receipt or `not-applicable`
   with a concrete justification: fresh-process smoke, dependency-cycle,
   cache-invalidation, migration/rollback, authorization, and repository policy;
5. strict string lists for known deterministic failures, new risk classes,
   cross-subsystem omissions, and incomplete invariants;
6. mode-specific fields. Initial mode rejects remediation metadata. Remediation
   requires an exact finding/scope/verification disposition. Material redesign
   additionally requires one named material category and proof that all named
   parent findings are resolved.

Every proof receipt is JSON with `schemaVersion: 1`, `result: passed`,
`exit_code: 0`, and the `subject_digest` computed from the frozen artifact digest
and changed-file digest map. The proof runner must verify the live files against
that subject before executing. A receipt binds its subject digest, never the
wall clock: a receipt whose `subject_digest` equals the record's computed digest
stays valid across record revisions and later epochs. A receipt whose digest
does not match — stale, self-consistent, or hand-carried from another tree — is
invalid. Never re-run an identical check against an unchanged subject solely to
mint an equivalent receipt.

The selector runs twice, as two different gates:

```bash
python3 lrhe/review_sequence.py --triage draft-record.json   # before any ceremony
python3 lrhe/review_sequence.py review-record.json           # before any dispatch
```

Triage comes first, on a pre-freeze draft record that carries every field
except the subject: artifact and changed-file digests, proof receipts, proof
classes, touched risk domains, and the invariant matrix.
`python3 lrhe/epoch.py scaffold --mode <mode> --sequence-id <id>
--review-id <id> --out draft-record.json` emits that draft shape, and
`python3 lrhe/epoch.py bind --record <prior-record> --action <action>` prints
each bound `sequence_history` row. Triage projects the dispatch decision from
sequence identity, bound history, honesty flags, and mode-specific
dispositions:

- exit `0` (`lead-close`): the epoch cannot dispatch; take the lightweight
  lead-only close below and skip the freeze, receipts, matrix, and packet
  entirely;
- exit `20` (`ceremony-required`): a dispatching action is projected; confirm
  provider authorization and panel resolvability, then freeze;
- exit `10`: return to implementation audit/repair or human disposition.

Triage output carries `projected_action`, never `action`; no triage result
authorizes a provider call. Only the full gate on the frozen, subject-bound
record does, and only `action: full-council` or `action: targeted-refuter`
permits the matching provider call. Missing or malformed readiness evidence, a
digest mismatch, a known deterministic failure, a new risk class, multiple
cross-subsystem omissions, or incomplete invariant coverage fails closed to
`implementation-audit-repair`. Reviewers are never used to discover
deterministic failures that the lead can prove locally.

When modifying this critical-review skill itself, use its stable developer tiers:

```bash
./review_checks.py quick
./review_checks.py full --subject-record review-record.json --receipt full-proof.json
```

`quick` is the inner loop for `test_review_sequence.py`, `test_runner.py`, and
`test_consistency.py`. `full` is the pre-freeze and pre-push proof: it first
runs `quick` against the operator environment so private qualification authority
is exercised when present, then mirrors the public Actions contract by running
Ruff, the early consistency gate, the entire LRHE test suite under an isolated
`HOME`, and the no-live-transport assertion. The receipt form is mandatory when
this skill's full proof is cited in a review record. Never substitute the
narrower quick selection for `full`.

After an initial council, the lead fixes and directly verifies localized P0/P1
findings before any further reviewer call. A remediation epoch may cover only
named findings, its changed paths, and adjacent invariants. Honest direct
verification records each finding as `resolved` or `disputed`. Batch the
corrections: one remediation epoch covers the complete named finding-set, never
one epoch per fix iteration. One still-disputed
P0/P1 may reach one targeted refuter; otherwise the lead records ledger
dispositions and closes the sequence.

New risk classes, two or more cross-subsystem omissions, or incomplete invariant
proof are systemic evidence that the implementation audit was incomplete. Mark
the sequence `not-council-ready`, return to implementation audit/repair, and do
not automatically redispatch. One optional design pass, the initial pass, and
at most one verified material redesign are the only general council passes.
There is never a third implementation council.

## Lightweight lead-only close

When triage returns `lead-close`, the epoch closes on direct lead verification
alone and mints nothing:

1. Store the triage draft as the epoch's durable
   `~/.omp/agent/critical-review/<review-id>/review-record.json`. Its
   `remediation_scope` and `lead_verification` rows carry the changed paths and
   the narrowest decisive evidence for every named finding.
2. Run `python3 lrhe/review_sequence.py --triage review-record.json` against the
   stored record and require exit `0`.
3. Record the ledger dispositions for every named finding and close the
   sequence. Later epochs bind this record in `sequence_history` with action
   `none`, exactly like any other epoch record.

A lead-close record never cites proof receipts, and its dispositions rest on the
recorded direct verification. Any doubt — a new risk class, an unnamed
regression, a disputed P0/P1, verification you cannot state as evidence —
disqualifies the lightweight path: record it honestly and let triage route the
epoch.

## Design-stage council

Review the design before code exists whenever the change is consequential
enough to qualify for this skill at all. The shadow ledger's confirmed P0/P1
findings are dominantly design-level — ordering that makes a commit
unreachable, rollback that deletes its own recovery path, single-check
containment — and every one is cheaper to catch in the document than in a
remediation chain.

A design epoch is a full council at near-zero ceremony:

- the frozen subject is the design artifact itself: `artifact.diff` is the
  design document, `changed_files` names it, and because there is nothing to
  run, `proof_receipts` may be empty (`{}`) with every proof class
  `not-applicable` under a concrete justification. A `passed` class still
  requires a subject-bound receipt;
- `review_mode: design` requires an empty `sequence_history`: the design
  council is always the sequence's first epoch, and there is at most one;
- a design pass never consumes an implementation council: the later `initial`
  epoch reviews the implementation with the design ledger's dispositions in
  its packet context;
- design findings land in the ledger like any council's; resolve them in the
  revised design or carry them into the implementation packet's
  `known_open_questions`. They never spawn remediation epochs — remediation
  belongs to implementation reviews.

The panel resolves through the same `python3 lrhe/qualification.py initial`
call, and dispatch still requires the frozen full gate on the design record.

## Freeze one review epoch

Enter this section only when triage returned `ceremony-required`. Freeze last:
apply the complete change — for a remediation epoch, the complete fix set for
every named finding — and iterate with unbound fast or repository-default checks
until direct verification passes, then freeze once. A subject frozen before the
work settles only drifts stale and wastes the ceremony.

Reviewers must inspect one stable epoch. Prefer a no-effect digest checkpoint; a
temporary commit or any history mutation requires authorization already present
in the current request or an Ask gate.

1. Choose a review ID such as `CR-<UTC timestamp>-<short digest>` and create
   durable storage at `~/.omp/agent/critical-review/<review-id>/`. Never use
   session-scoped `local://` storage: future epochs must revalidate prior records.
   Future epochs cite that canonical `review-record.json` directly; only legacy
   session-local records are copied once into durable history.
2. Record repository root, branch, base commit, HEAD, changed paths,
   review-scoped untracked paths, and relevant design artifacts.
3. Materialize the complete review-scoped diff or design as `artifact.diff`.
   Exclude unrelated, ignored, secret-bearing, or unauthorized material.
4. Bind the frozen subject with the epoch tool — it resolves paths, records
   deletions explicitly (`DELETED`), keeps stable ordering, and refuses
   session-local or unreadable paths:

   ```bash
   python3 lrhe/epoch.py freeze --record review-record.json \
     --artifact artifact.diff --changed <files...> --deleted <files...>
   ```

5. Complete `review-record.json` with the remaining readiness fields: touched
   risk domains, the invariant proof matrix, proof classes, honesty flags, and
   the mode-specific dispositions.
6. Run each decisive check through the generic subject-bound producer and add
   the receipt path and digest to the record:

   ```bash
   python3 lrhe/make_receipt.py \
     --subject-record review-record.json --receipt <proof>.json \
     --cwd <repository> -- <exact command and arguments>
   ```

   It verifies the frozen subject both before and after the command and emits no
   receipt on failure or drift. The fixed skill-development `full` wrapper
   delegates to this same producer.

   Iteration MAY run a standalone fast or repository-default check before the
   subject is frozen. After the final subject freeze, run the final
   repository-default `just check` exactly once through `make_receipt.py` and
   use that subject-bound receipt as the proof; do not run the same unbound
   `just check` and then repeat it against unchanged frozen bytes solely to mint
   the receipt.

   When an earlier epoch or record revision already minted a receipt for this
   exact subject, verify and cite it unchanged instead of re-running the check:

   ```bash
   python3 lrhe/make_receipt.py \
     --subject-record review-record.json --receipt <proof>.json --reuse
   ```

   Exit `0` proves the receipt already binds this subject digest and the live
   tree.
7. Run `python3 lrhe/review_sequence.py review-record.json`. A nonzero result
   prohibits provider dispatch.
   `python3 lrhe/epoch.py recheck --record review-record.json` re-verifies the
   live tree against the frozen subject at any later point without re-reading
   digests by hand.
8. Create `packet.md` from the validated record. The packet records only the
   `review-record.json` path and digest plus the review context below; never
   restate pass counts, rosters, finding sets, test counts, or digests by hand.
9. Do not modify reviewed files from this point until the epoch closes.
10. Recompute the same artifact and file digests after round one and before
    synthesis. Any mismatch makes every result stale. Close the epoch and start a
    new review ID; never blend stale and fresh claims.

The packet context is:

```yaml
review_record_path:
review_record_sha256:
goal:
non_goals:
requirements:
invariants:
trust_boundaries:
data_or_state_transitions:
rollback_contract:
compatibility_contract:
design_or_diff:
known_open_questions:
rejected_alternatives_and_reasons:
provider_data_allowlist:
```

Use concise facts and primary anchors. Include the decision record, not hidden
reasoning, tentative confidence, or another reviewer's verdict. For every resolver
member whose `evidence_delivery` is `repository`, packet links must resolve. For an
`inline` member, the Task assignment itself must contain the complete packet,
review-scoped diff or design, and line-numbered source evidence needed to verify
every claim; a path or `agent://` handle is not evidence to a tool-less reviewer.
Anything omitted from that inline evidence must be reported as unresolved, never
reconstructed from naming conventions. Compute any displayed summary from the
record; never maintain a second evidence count.

## Round one: independent concurrent critics

Resolve the panel immediately before dispatch:

```bash
python3 lrhe/qualification.py initial
```

Use the eval kernel as the dispatcher, never as a review authority. Load the
resolver's exact JSON result and the already-complete immutable assignments into
eval state; do not retype, filter, reorder, or supplement the roster. Do not add a
live `lrhe/dispatch.py` or another workflow framework. `workflowz` is generic
guidance and does not replace this protocol.

Dispatch every returned member in one Python `parallel()` wave. Its bounded pool
follows the harness's `task.maxConcurrency`, and its return is the round-one
barrier: no reviewer payload may enter the lead's context or a peer-visible file
until every member has settled. Catch inside each branch so one failure cannot discard
the successful handles:

```python
def dispatch_member(member):
    family = member["family"]
    try:
        node = agent(
            assignments[family],
            agent=member["agent"],
            label=f"{review_id}:{family}",
            schema_mode="strict",
            handle=True,
        )
        return {"family": family, "state": "completed", "node": node}
    except Exception as exc:
        return {
            "family": family,
            "state": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }

round_one = parallel(
    [lambda member=member: dispatch_member(member) for member in members]
)
receipts = [
    {
        "family": result["family"],
        "state": result["state"],
        "handle": result.get("node", {}).get("handle"),
    }
    for result in round_one
]
display(receipts)
```

`handle=True` retains the `agent://` identity but also returns text and structured
data. Keep `round_one` in kernel state; display only the receipt projection above.
Immediately run `epoch.py recheck` outside the cell. Only after it passes may a
second eval cell write each completed `node["data"]` to `<family>.json`, after
which the lead reads the files separately. A failed recheck makes every node stale.

`parallel()` owns the critic barrier. `pipeline()` may make already-authorized
mechanical stages readable, but it never substitutes for triage, provider
authorization, freeze, receipts, the full gate, digest rechecks, or the ledger
scaffold. An explicit turn budget is defense in depth, not an exact cost or
cardinality bound; the resolver-owned roster remains the hard dispatch bound.
`completion()` must never decide Result or Disposition. The standard path uses the
deterministic `epoch.py ledger` scaffold rather than an LLM normalizer.

Use each resolver result's `agent`, `model`, and `evidence_delivery`; do not
maintain a second live panel list. Each `agent()` call must pass
`schema_mode="strict"` and omit `schema` so the agent's configured schema remains
authoritative. Use this complete assignment shape:

```text
# Target
For `repository` delivery: review the immutable packet at <packet path> and only the repository epoch it identifies.
For `inline` delivery: review only the complete immutable packet and numbered source evidence pasted below; do not inspect any path.
Do not modify files or inspect peer output.

# Change
Apply the common critical floor and the primary lens defined by your agent. Return falsifiable root-cause claims with exact evidence. This is review only; no implementation or competing rewrite.

For `inline` delivery, paste the complete packet, diff or design, and line-numbered source evidence here. Never substitute a path, summary, or source excerpt that omits reviewed behavior.

# Acceptance
Return one schema-valid summary/evidence/unresolved object, at most 12 evidence items, exact anchors present in the supplied evidence, and explicit missing evidence for unresolved claims.
```

Do not give reviewers caller-provided output schemas that weaken their agent
schema. Do not disclose round-one responses between reviewers through messages,
prompts, local files, follow-up calls, or eval display. After every selected branch
settles and the epoch digest recheck passes, persist and read each complete result
separately.

Distinguish three failure classes when a member returns nothing usable. A
dispatch-level failure — the eval bridge rejected the invocation, or the child died
or was interrupted before any terminal output — may use the protocol's single
recovery allowance. An explicit provider-policy refusal before a schema-valid
terminal review may use that same allowance: record the refusal and attempt metadata
in the close report, re-run `epoch.py recheck` and the full frozen-record gate
(`python3 lrhe/review_sequence.py review-record.json`), then redispatch the same
immutable assignment to the same resolved agent and model. The allowance is exactly
one total retry per member per epoch, not one retry per failure class. Never reword
or reframe the assignment, change the frozen subject or evidence, reduce scope,
change evidence delivery, or substitute a model or family. If that identical retry
also fails or refuses, the member is `missing` for the epoch. A terminal
schema-invalid result is not recoverable: it consumed that member's review, and the
member is `missing` for the epoch. Never redispatch to shop for a different answer.

## Finding ledger

After the epoch digest recheck passes, create `~/.omp/agent/critical-review/<review-id>/ledger.md`. Normalize every `evidence` and `unresolved` item into one row with:

| Field | Required content |
| --- | --- |
| Finding | Stable ID and normalized root-cause claim |
| Sources | Reviewer families that raised it; agreement is metadata only |
| Evidence | Verified source, log, test, or artifact anchors |
| Severity | P0, P1, P2, or P3 |
| Confidence | Confidence in evidence, not rhetoric |
| Verification | Concrete test or inspection that settles the claim |
| Result | `confirmed`, `falsified`, `unresolved`, `missing`, or `design decision` |
| Disposition | `accept`, `reject`, `defer`, or `mitigate` |
| Change | Exact resulting implementation or design change, or `none` |
| Rationale | Why evidence supports the disposition |

Scaffold the table mechanically; keep only the judgment manual:

```bash
python3 lrhe/epoch.py ledger --member claude=claude.json --member grok=grok.json \
  --review-id <review-id> --out ledger.md
```

Each saved reviewer yield becomes one row per evidence and unresolved item with
the mechanical columns filled and `unresolved` prefilled as the U-row Result;
Result, Disposition, Change, and Rationale stay empty because they are the
lead's verification, which the tool never performs. A row that fails the pinned
grammar refuses the whole scaffold rather than dropping feedback silently; an
existing `ledger.md` is never overwritten.

Merge duplicates only when they share a root cause. Preserve every source ID on the merged row. Verify each cited source location before promoting a claim. Resolve important claims with the narrowest decisive evidence: a reproducer, failing test, call graph, interface implementation inventory, policy counterexample, migration rehearsal, rollback simulation, demonstrated authorization path, or concrete race schedule.

Decision rules:

- A confirmed P0 or P1 blocks closure.
- A stated invariant violation blocks until corrected or explicitly waived by the human owner.
- An unresolved P0/P1 involving authorization, secrets, money, irreversible state, data loss, or release integrity blocks.
- One empirical falsification outweighs repeated unsupported concern.
- One reproducible exploit outweighs repeated approval.
- P2/P3 items receive explicit dispositions but do not trigger open-ended debate.
- Every returned item receives a ledger row and final disposition. Never silently omit inconvenient feedback.

### Capture outcomes; do not curate a benchmark

The ledger is also the evaluation. Append each closed review's rows to the shadow
ledger so lane value accumulates from work already being done, rather than from
hand-labeled historical examples:

```bash
L=~/.omp/agent/skills/critical-review/lrhe
python3 "$L/shadow_ledger.py" ingest   --runs runs.jsonl --dispositions ledger.jsonl
python3 "$L/shadow_ledger.py" outcomes --findings findings.jsonl --repo .
python3 "$L/shadow_ledger.py" queue    --findings findings.jsonl   # what you read
python3 "$L/shadow_ledger.py" metrics  --findings findings.jsonl   # after ~30 reviews
```

`queue` returns only unresolved P0/P1, irreversible tradeoffs with no empirical
answer, and proposed invariant waivers. Everything else disposes of itself.

One caveat the metrics print for themselves: the lead issues `Disposition`, and the
lead is one of the families being compared. That is the single-family-judge problem
this skill already rejects for review, applied to measurement. Run
`shadow_ledger.py audit` periodically against a cross-family panel and read its
kappa beside every per-family number.

The offline counterpart is `skill://critical-review/lrhe` — a 47-item public corpus
with executable and human-adjudicated labels, twelve seeded false-finding traps,
and the arm-T empirical null that says whether a lane diversifies at all or merely
adds another correlated draw. Its corpus and answer key are private
(`lrhe-data/`); never copy them into the public package.

## One targeted refutation round

Run a refutation only when the machine gate returns `action: targeted-refuter`
for a readiness-complete `remediation` epoch. Resolve eligible
reviewers with `python3 lrhe/qualification.py targeted-refuter`. There is at most
one targeted refutation for the entire review sequence. Select one returned
family that did not originate the claim when possible, then launch one reviewer
task with `schemaMode: strict`. Give it only:

```text
Claim and finding ID:
Supporting claim and exact repository anchors:
Counterevidence and exact repository anchors:
Lead verification already performed:
Repository packet path and immutable epoch:
Question that must be answered:
Permitted read-only verification methods:
```

For `repository` delivery, pass anchors and the packet path, not copied source.
Require the refuter to inspect the linked implementation with its read-only
repository tools; do not inline the diff, surrounding code, or a substitute source
excerpt. If a future resolver returns `inline` delivery, follow the general inline
packet rules above instead.

Do not provide the original reviews, reviewer identities, vote counts, or
rhetoric. Ask the refuter to falsify the single normalized claim, not to conduct
another general review. Record `confirmed`, `falsified`, or `unresolved/human
decision`, update the ledger, and stop. Never start another refutation or a third
general council pass.

## Close and report

Recheck epoch digests once more before final disposition. Mark the review stale instead of synthesizing if they changed. Then report:

- review ID and packet path;
- exact epoch and digest status;
- each selected reviewer's model family and `completed`, `missing`, or `invalid` state;
- ledger path;
- every blocking or unresolved P0/P1;
- accepted implementation/design changes and verification evidence;
- explicit residual risks and human waivers.

There is no majority verdict. The GPT lead owns the final evidence-based decision and coherent revision. Close the frozen epoch before modifying reviewed files. Then apply the sequence gate: close verified localized remediation directly; use the single targeted-refuter path only for a still-disputed P0/P1; return systemic omissions to implementation audit; and open another full council only for a readiness-complete material redesign within the two-pass limit.
