# Critical Review Live Protocol

Full-council operating procedure for `skill://critical-review`. Read it only after
that skill's admission decision selected a full council — including an explicitly
user-requested council. Nothing here is an admission criterion, and nothing here
replaces the assurance selection, focused-review routing, hosted-material floor,
common reviewer assignment, or lead dispositions owned by `SKILL.md`.

Every command below runs from the critical-review skill root, the parent of this
file's `lrhe/` directory. Where prose and code could drift, the executable tools
in this directory and the JSON schemas beside them are authoritative.

## Live roster and provider authorization

Live reviewer membership and standing are configuration plus lead lineage, not
reviewer prose:

- `skill://critical-review/qualification.yml` `liveDispatch` is the sole
  authoritative live panel definition;
- `lrhe/qualification.py` is the sole executable resolver for that definition.
  The caller must supply the accountable main session's exact `model_family`;
  the resolver selects that `byLeadFamily` profile and emits the selection
  manifest that records the roster and the ledger's provenance;
- `lrhe/review_dispatch.py` is the sole live dispatch entry point, and its
  `freeze` → `resolve` → `dispatch` order is the only path to a reviewer. It
  reads the fixed live authority at
  `~/.omp/agent/skills/critical-review/qualification.yml` — never a
  caller-supplied authority path — and emits every standing field itself. There
  is no hand-assembled assignment, no hand-copied standing, and no direct
  reviewer launch;
- a reviewer's identity is its `reviewers` key, its `reviewer_id`. That is the
  only join key for manifests, dispatch, results, receipts, and ledger rows.
  `model_family` and `correlation_group` describe which model answers for a lane,
  and two reviewer ids may deliberately share one lineage — so never join, dedupe,
  or substitute on the family;
- manifest `leadFamily` records the supplied accountable lead lineage. Every
  `initialCritic` and targeted refuter is cross-family, and no two
  `initialCritics` in one profile may share a `model_family`;
- `initialCritics` are unconditional for the selected profile and are the three
  members that satisfy the independent critic floor;
- `initialSpecialists` are additive blind samples of the lead's own lineage.
  Their standing is `same_lineage_blind_sample` / `supplemental_evidence`: they
  resolve after every critic, never replace or fall back to another member, and
  never satisfy the independent floor;
- `conditionalCritics` are additive and record-selected. Eligibility and
  standing are separate: an eligible cross-family conditional carries
  `independent_evidence`; an eligible same-family conditional carries
  `supplemental_evidence`. Its absence never shrinks the unconditional council;
- role, `independence_class`, and `authority` are derived from lead family,
  reviewer family, and the selected profile group. Reviewer entries and prompts
  cannot declare or promote their own standing, and these fields reach a reviewer
  only inside the generated `CRITICAL_REVIEW_RESOLVER_RECEIPT_V1` block;
- `initialCritics`, `initialSpecialists`, `conditionalCritics`, and
  `targetedRefuters` are distinct dispatch roles;
- `evaluationOnly` and `disabled` lanes, and every experiment in
  `lrhe/panels.yaml`, never authorize live review dispatch. A lane in `disabled`
  may be fully described by lineage, transport, lens, agent, and model and is
  still not on the council. Being representable is not being selected.

Before dispatch, in addition to the hosted-material floor in `SKILL.md`:

1. Read `skill://critical-review/qualification.yml`. A reviewer is enabled only when its `reviewer_id` is in the selected `byLeadFamily` profile or required global group, its reviewer entry says `dispatchEnabled: true`, its canary and read-only gates pass, its exact model selector still resolves, and the packet authorizes its provider. A conditional critic additionally needs a fresh passed receipt for the exact scope being requested; a scope recorded `ineligible` is an explicit supported boundary, not a failure to work around.
2. Authorize by `access_profile`, not by `provider_route`. Several lanes with different entitlements share one route, so route-level permission proves nothing about a given lane. A member whose `access_profile` is not authorized is `missing`. The packet carries the two grants separately and the resolver matches both exactly: `provider_data_allowlist` must contain the reviewer's `data_allowlist_key`, the vendor-rights token (`anthropic`, `google`, `xai`, `opencode`, `openai`), and `reviewer_access_profile_allowlist` must contain its exact `access_profile`. A missing grant on an unconditional critic or an always-on specialist fails the whole resolution — neither has a not-selected state, so dropping one would silently shrink a council; a conditional critic is skipped with `provider-data-rights-not-authorized` or `access-profile-not-authorized` recorded. In particular, `daybreak-blue` shares the native `openai-codex` route with ordinary GPT lanes and requires both `openai` vendor authorization and explicit `daybreak-blue` access-profile authorization. OMP may rotate among sibling Codex credentials when one account returns a TAC policy denial; credential selection is transport behavior, not a substitute for either packet grant, and authorization for `daybreak-blue` never implies authorization for another lane.

A missing, disabled, timed-out, schema-invalid, or unauthorized reviewer is `missing`, never `approved`. Do not substitute another model for an unavailable lane. A conditional critic the resolver did not select is `not_selected/ineligible` — neither `missing` nor a failed member. Any selected row with `supplemental_evidence` remains additive: it cannot rescue the independent floor, break a disagreement, or earn a retry that an independent critic would not get.

## Internal resource compatibility

When inspecting resources below `skill://critical-review/`:

- Enumerate fixture files by reading the directory URL, for example
  `read skill://critical-review/lrhe/fixtures`, then read the returned file URLs
  explicitly. Never pass an internal URL glob to `glob`.
- Read multiple `skill://` files with separate parallel `read` calls. Never
  semicolon-delimit internal URLs in one `read` path.
- If glob matching is still needed, use the resolved filesystem path displayed
  by the directory `read` as the glob root instead of the internal URL.

## Sequence and readiness gate

The selector's modes are:

- `design`: an optional pre-implementation council on the frozen design artifact;
  at most one per sequence, always the first epoch, and never counted toward the
  two general implementation passes;
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
   parent findings are resolved;
7. `lifecycle_design_artifacts` only when the epoch intentionally supplies them:
   distinct, digest-bound state-machine and failure-matrix JSON artifacts.
   Absent, `null`, or `{}` is valid and raises no lifecycle error, and no touched
   risk domain — `credentialed-external-lifecycle` included — requires the
   artifacts or a prior design council. Once supplied, validation is strict: the
   executable gate checks their schemas, transition/failure-state coverage, and
   inclusion in the reviewed `changed_files`, and when `sequence_history` already
   carries a design epoch it additionally requires that epoch's exact binding.
   Historical records holding valid artifacts stay readable and verifiable.

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
./lrhe/review_sequence.py --triage draft-record.json   # before any ceremony
./lrhe/review_sequence.py review-record.json           # before any dispatch
```

Triage comes first, on a pre-freeze draft record that carries every field
except the subject: artifact and changed-file digests, proof receipts, proof
classes, touched risk domains, and the invariant matrix.
`./lrhe/epoch.py scaffold --mode <mode> --sequence-id <id>
--review-id <id> --out draft-record.json` emits that draft shape, and
`./lrhe/epoch.py bind --record <prior-record> --action <action>` prints
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

New risk classes, two or more cross-subsystem omissions, or incomplete invariant
proof are systemic evidence that the implementation audit was incomplete. Mark
the sequence `not-council-ready`, return to implementation audit/repair, and do
not automatically redispatch.

## Lightweight lead-only close

When triage returns `lead-close`, the epoch closes on direct lead verification
alone and mints nothing:

1. Store the triage draft as the epoch's durable
   `~/.omp/agent/critical-review/<review-id>/review-record.json`. Its
   `remediation_scope` and `lead_verification` rows carry the changed paths and
   the narrowest decisive evidence for every named finding.
2. Run `./lrhe/review_sequence.py --triage review-record.json` against the
   stored record and require exit `0`.
3. Record the ledger dispositions for every named finding and close the
   sequence. Later epochs bind this record in `sequence_history` with action
   `none`, exactly like any other epoch record.

A lead-close record never cites proof receipts, and its dispositions rest on the
recorded direct verification. Any doubt — a new risk class, an unnamed
regression, a disputed P0/P1, verification you cannot state as evidence —
disqualifies the lightweight path: record it honestly and let triage route the
epoch.

## Design epoch mechanics

Enter this section only when admission chose the optional design council
described in `SKILL.md`. A design epoch is a full council at near-zero ceremony:

- the frozen subject is the design artifact itself: `artifact.diff` is the
  design document, `changed_files` names it, and because there is nothing to
  run, `proof_receipts` may be empty (`{}`) with every proof class
  `not-applicable` under a concrete justification. A `passed` class still
  requires a subject-bound receipt;
- when the design intentionally supplies lifecycle artifacts, they are distinct
  `lifecycle-state-machine.json` and `lifecycle-failure-matrix.json` files.
  Supplying them is optional; once supplied, `lrhe/review_sequence.py` requires
  the state machine to name every state, guarded transition, terminal state, and
  failure state; the failure matrix must bind each failure state to trigger,
  durable state, recovery, retry, cleanup, and decisive verification. Both files
  must be in `changed_files`;
- `review_mode: design` requires an empty `sequence_history`: the design
  council is always the sequence's first epoch, and there is at most one;
- a design pass never consumes an implementation council: the later `initial`
  epoch reviews the implementation with the design ledger's dispositions and any
  supplied `lifecycle_design_artifacts` bindings in its packet context;
- design findings land in the ledger like any council's; resolve them in the
  revised design or carry them into the implementation packet's
  `known_open_questions`. They never spawn remediation epochs — remediation
  belongs to implementation reviews;
- after implementation, review is verification-only unless the correction
  introduces a new risk class or materially changes architecture, trust
  boundary, compatibility, persistent state, migration/rollback, or production
  effects. Named-finding remediation does not reopen design or create another
  full council merely because implementation changed.

The panel resolves through the same record-aware resolver call below, and
dispatch still requires the frozen full gate on the design record. A `design`
record resolves through `qualification.py initial`: `initial` names the
full-council resolution, not the record's review mode.

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
   ./lrhe/epoch.py freeze --record review-record.json \
     --artifact artifact.diff --changed <files...> --deleted <files...>
   ```

5. Complete `review-record.json` with the remaining readiness fields: touched
   risk domains, the invariant proof matrix, proof classes, honesty flags, and
   the mode-specific dispositions.
6. Run each decisive check through the generic subject-bound producer and add
   the receipt path and digest to the record:

   ```bash
   ./lrhe/make_receipt.py \
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
   ./lrhe/make_receipt.py \
     --subject-record review-record.json --receipt <proof>.json --reuse
   ```

   Exit `0` proves the receipt already binds this subject digest and the live
   tree.
7. Run `./lrhe/review_sequence.py review-record.json`. A nonzero result
   prohibits provider dispatch.
   `./lrhe/epoch.py recheck --record review-record.json` re-verifies the
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
reviewer_access_profile_allowlist:
```

The declared class, named assets and invariants, credible adversary, caps, recovery contract, result-validity conditions, and non-goals are binding review scope. Review invocation does not promote the artifact class or create a future consumer. Critics may report an omitted risk only when they show a concrete in-scope consequence after current controls. General hardening and speculative future-proofing are not findings.

Use concise facts and primary anchors. Include the decision record, not hidden
reasoning, tentative confidence, or another reviewer's verdict. For every resolver
member whose `evidence_delivery` is `repository`, packet links must resolve. For an
`inline` member, the Task assignment itself must contain the complete packet,
review-scoped diff or design, and line-numbered source evidence needed to verify
every claim; a path or `agent://` handle is not evidence to a tool-less reviewer.
Anything omitted from that inline evidence must be reported as unresolved, never
reconstructed from naming conventions. Compute any displayed summary from the
record; never maintain a second evidence count.

## Round one: freeze, resolve, dispatch

Live dispatch has exactly one entry point, `./lrhe/review_dispatch.py`, and one
order: freeze the subject, resolve standing, dispatch the payload. The lead never
assembles a reviewer assignment by hand, never writes a standing field into
prose, and never launches a reviewer through the eval kernel, an `agent()` call,
or a Task call it composed itself.

The division of labor is fixed. The model chooses what is reviewed and by whom:
the frozen scope and packet, the repository commit and the exact file list, the
accountable `lead_family`, the reviewer ids, and the review class — `focused`,
`initial`, or `targeted-refuter`. Executable resolution owns standing: it reads
the fixed live authority and emits `selectionClass`, `role`,
`independence_class`, and `authority` itself, so a caller-supplied standing field
is refused rather than trusted. The Task gate injects the trusted receipt:
reviewer task text is generated, opens with
`CRITICAL_REVIEW_RESOLVER_RECEIPT_V1`, and is submitted verbatim.

Resolve the roster of record before freezing the dispatch subject. The panel
resolver reads the frozen record and the immutable packet, verifies that the
packet names exactly that record path and digest, and writes one durable
selection manifest:

```bash
./lrhe/qualification.py initial \
  --lead-family <gpt|claude|gemini|grok> \
  --record review-record.json \
  --packet packet.md \
  --out ~/.omp/agent/critical-review/<review-id>/panel-selection.json
```

It prints exactly the bytes it wrote, validates against
`lrhe/panel-selection.schema.json`, refuses to overwrite an existing manifest,
refuses a session-local output path, and refuses to answer at all unless
`lrhe/review_sequence.py` returns `full-council` for that record. The manifest
binds the absolute record path, the record SHA-256, the proof-subject digest,
the packet path and SHA-256, the exact qualification authority path and digest,
and the resolver's own `qualificationPath` and `qualificationSha256`. It also
binds `leadFamily`, the profile input that derived every row's standing. A
changed lead family, record, packet, authority, or resolver no longer matches
the roster it produced.
The manifest is file- and directory-synced, appears atomically, and lands
read-only; nothing downstream needs to `chmod` it or accept partial bytes.

The manifest is the epoch's roster of record and the ledger's provenance, not a
dispatch authority. Read its complete `selected` array and hand those
`reviewer_id` values to `resolve`; do not retype, filter, reorder, or supplement
the roster, and never re-derive membership from prose or from `qualification.yml`
by hand. Join every row by `reviewer_id`; `model_family` is descriptive and two
rows may share it. Every `skipped` entry carries its sorted `reasonCodes` and is
reported as `not_selected/ineligible` in the close report. Do not add a live
`lrhe/dispatch.py` or another workflow framework. `workflowz` is generic guidance
and does not replace this protocol.

### Freeze the dispatch subject

```bash
./lrhe/review_dispatch.py freeze \
  --scope scope.md --packet packet.md --record review-record.json \
  --repo <repository root> --commit <40-hex commit equal to HEAD> \
  --file <repository-relative path> [--file <repository-relative path> ...] \
  --out ~/.omp/agent/critical-review/<review-id>/frozen-subject.json
```

A repository subject requires a clean HEAD, a caller-supplied lowercase 40-hex
commit that equals that HEAD, and a nonempty list of exact repository-relative
files. Every bound path must be a regular blob at that commit — git mode `100644`
or `100755`. A directory, a symlink (`120000`), or a submodule gitlink (`160000`)
is refused, because none of them fixes the bytes a reviewer would read: a
committed symlink is immutable as a tree entry while its target is not. `--repo`
without `--commit` and at least one `--file` is a working-tree subject and is
refused by name: there is no freeze of a mutable tree, and no reviewer ever reads
one. Omit `--repo` for a packet-only subject — `--commit` and `--file` are then
refused, and the subject binds the scope and packet content hashes instead.
`--scope` and `--packet` are required in both shapes and are hash-bound;
`--record` is optional. The frozen subject validates against
`lrhe/frozen-subject.schema.json`.

### Resolve standing

```bash
./lrhe/review_dispatch.py resolve \
  --subject frozen-subject.json \
  --lead-family <gpt|claude|gemini|grok> \
  --review-class <focused|initial|targeted-refuter> \
  --reviewer <reviewer_id> [--reviewer <reviewer_id> ...] \
  --out ~/.omp/agent/critical-review/<review-id>/resolver-receipt.json
```

The review class is a completeness contract, not a label. `initial` must name the
complete selected council; `focused` names exactly one configured initial critic;
`targeted-refuter` names the complete fixed refutation pool. A short, padded,
substituted, or reordered set fails closed rather than quietly dispatching a
smaller council than the authority selected. `resolve` writes exactly one
artifact, the resolver receipt, validated against the generated
`resolver-receipt.schema.json` whose `const` branches admit only the valid
lead-family × review-class × reviewer tuples. It does not replace the selection
manifest, and `epoch.py ledger --manifest` still takes that manifest.

Each resolved row's `selectionClass` says whether the member is `unconditional`,
`specialist`, or `conditional`, and its `authority` says what its result is worth
— `supplemental_evidence` never counts toward the independent critic floor.
Conditional selection does not imply independent standing, and no class changes
how a result is weighed in debate, because there is no vote.

### Dispatch

```bash
./lrhe/review_dispatch.py dispatch \
  --receipt resolver-receipt.json \
  --out ~/.omp/agent/critical-review/<review-id>/review-dispatch-envelope.json
```

`dispatch` writes the envelope, hashes the bytes it wrote, and prints one strict
JSON object whose only key is `task_input`. Submit that object verbatim as the
Task call; `verify-task` independently regenerates the same object immediately
before execution.

OMP requires two canonical Task shapes. A single `focused` reviewer or the
single targeted refuter uses the flat shape with exactly `i`, `agent`, and
`task`; the generated task begins with the receipt marker followed by the
envelope path and SHA-256. A council with multiple reviewers uses the batch
shape with exactly `i`, `context`, and `tasks`; `context` is:

```text
CRITICAL_REVIEW_DISPATCH_V1
envelope_path=<absolute path>
envelope_sha256=<64 lowercase hex>
```

The generated `i` is pinned as the envelope's `taskIntent` schema `const`.
Every batch item carries exactly `agent` and `task`. Never retype, reorder,
trim, merge, summarize, or reword one character of either canonical shape, and
never append an instruction of your own. A multi-reviewer council runs in one
gated Task wave; that call is the round-one barrier, and no reviewer payload may
enter the lead's context or a peer-visible file until every member has settled.

The extension treats every Task item or flat Task whose agent name starts with
`review-` as protected. A protected or mixed call without the exact
verifier-approved canonical input is blocked; a Task call with no protected
agent is unaffected. The gate invokes `review_dispatch.py verify-task
--envelope <path> --sha256 <hex>` at a path fixed relative to the extension and
never caller-selectable. It rehashes the envelope and revalidates the envelope
schema, current resolver and qualification authority, subject state, receipt,
assignments, and exact canonical Task input. An edited envelope binding, intent,
task, or batch is blocked, not warned; hand-writing a reviewer Task call is the
bypass the gate exists to refuse.

Every generated artifact — frozen subject, resolver receipt, dispatch envelope —
is strict, hash-bound, atomic, non-overwriting, and read-only. Exit `0` is
success; exit `1` is a domain refusal carrying one plain reason line on stderr
and nothing on stdout; exit `2` is an argv usage error. A refusal is a stop, not
an invitation to retry with different flags.

Every selected member runs through the same native Task path. Evidence delivery,
agent, model, and execution mode come from the resolved row; never maintain a
second live panel list. Every live row uses `task_agent`. The resolved agent
definition supplies its exact model, thinking level, tool surface, charter, and
output schema, and the generated payload carries no schema of its own, so the
agent's configured schema stays authoritative. OMP owns credential selection
inside the provider route, including sibling-account rotation on account-scoped
TAC denials; dispatch neither pins an OAuth account nor implements a second retry
policy. A served model that differs from the manifest's exact selector is still
invalid.
Every live reviewer selector, plus a held lane being qualified, must also have an
exact empty entry in OMP's `retry.fallbackChains`. Native credential rotation may
preserve the selector; model fallback may not change it.

Immediately after the Task call returns, run `epoch.py recheck`. Only after it
passes may each completed member's structured output be written to
`<reviewer_id>.json`, after which the lead reads the files separately. A failed
recheck makes every result stale.

An explicit turn budget is defense in depth, not an exact cost or cardinality
bound; the resolver-owned roster remains the hard dispatch bound. `completion()`
must never decide Result or Disposition, and no eval helper substitutes for
triage, provider authorization, freeze, receipts, the full gate, digest rechecks,
or the ledger scaffold. The standard path uses the deterministic `epoch.py
ledger` scaffold rather than an LLM normalizer.

The generated task text is the common reviewer assignment that `SKILL.md`
defines — unchanged, complete, and identical for every member. `SKILL.md` is the
single owner of the shared review floor, including state fidelity; this protocol
never restates, narrows, or supplements it.

Do not disclose round-one responses between reviewers through messages, prompts,
local files, or follow-up calls. After every selected item settles and the epoch
digest recheck passes, persist and read each complete result separately.

Distinguish these outcomes when a member returns nothing usable. Only a
dispatch-level failure may be retried:

| Outcome | Retry | Final state |
|---|---|---|
| Resolver did not select a conditional critic | no dispatch | `not_selected/ineligible` |
| The Task gate refused the canonical call, or a child died or was interrupted before any terminal output | exactly one byte-identical retry, after `epoch.py recheck` and the full frozen-record gate both pass again | `completed`, or `missing/transport_failure` |
| Explicit provider-policy refusal | none | `missing/provider_policy_refusal` |
| Terminal output violates the strict schema | none | `invalid/schema_invalid` |
| Served model differs from the exact requested selector | none, and never accepted | `invalid/model_mismatch` |
| Epoch digests changed | no synthesis | every result stale |

A provider-policy refusal is terminal for that member in this epoch. Retrying the
same refused request on the same model reproduces the refusal, and the protocol's
recovery allowance exists for transport failures, not for provider policy.
Record the refusal category and attempt metadata in the close report and continue
with the council that is already running.

The transport allowance is exactly one total retry per member per epoch. Re-run
`epoch.py recheck` and the full frozen-record gate
(`./lrhe/review_sequence.py review-record.json`), then resubmit the exact
`task_input` that `verify-task` regenerates from the same envelope. A retry
reuses the existing envelope: `dispatch` never overwrites one, and re-freezing
or re-resolving to obtain a fresh envelope is a new epoch, not a retry. Never reword
or reframe a task, change the frozen subject or evidence, reduce scope, change
evidence delivery, lower thinking level, or substitute a model or family.
If that identical retry also fails, the member is `missing` for the epoch. Never
redispatch to shop for a different answer, and never let one member's refusal
cause another family to run twice.

## Finding ledger

After the epoch digest recheck passes, create `~/.omp/agent/critical-review/<review-id>/ledger.md`. Normalize every `evidence` and `unresolved` item into one row with:

| Field | Required content |
| --- | --- |
| Finding | Stable ID and normalized root-cause claim |
| Sources | Reviewer ids that raised it; agreement is metadata only, and two ids on one `model_family` are one lineage agreeing with itself |
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
./lrhe/epoch.py ledger --manifest panel-selection.json \
  --member claude=claude.json --member grok=grok.json \
  --review-id <review-id> --out ledger.md
```

Each `--member` key is a `reviewer_id` exactly as the selection manifest names it,
and it becomes that row's `Sources` cell — so a finding traces back to the lane
that raised it rather than to a lineage two lanes may share.
The command refuses any member key absent from that immutable manifest; a typo
cannot mint finding provenance for a lane that was never selected.

Each saved reviewer yield becomes one row per evidence and unresolved item with
the mechanical columns filled and `unresolved` prefilled as the U-row Result;
Result, Disposition, Change, and Rationale stay empty because they are the
lead's verification, which the tool never performs. A row that fails the pinned
grammar refuses the whole scaffold rather than dropping feedback silently; an
existing `ledger.md` is never overwritten.

Filling Verification, Result, Disposition, Change, and Rationale is lead
judgment. Apply `SKILL.md` § Lead verification and dispositions, which owns
duplicate merging, source verification, severity after declared controls, and the
decision rules; zero findings closes a valid council.

### Capture outcomes; do not curate a benchmark

The ledger is also the evaluation. Append each closed review's rows to the shadow
ledger so lane value accumulates from work already being done, rather than from
hand-labeled historical examples:

```bash
./lrhe/shadow_ledger.py ingest   --runs runs.jsonl --dispositions ledger.jsonl
./lrhe/shadow_ledger.py outcomes --findings findings.jsonl --repo .
./lrhe/shadow_ledger.py queue    --findings findings.jsonl   # what you read
./lrhe/shadow_ledger.py metrics  --findings findings.jsonl   # after ~30 reviews
```

`queue` returns only unresolved P0/P1, irreversible tradeoffs with no empirical
answer, and proposed invariant waivers. Everything else disposes of itself.

One caveat the metrics print for themselves: the lead issues `Disposition`, and the
lead is one of the families being compared. That is the single-family-judge problem
this skill already rejects for review, applied to measurement. Run
`./lrhe/shadow_ledger.py audit` periodically against a cross-family panel and read
its kappa beside every per-family number.

The offline counterpart is `skill://critical-review/lrhe` — a 47-item public corpus
with executable and human-adjudicated labels, twelve seeded false-finding traps,
and the arm-T empirical null that says whether a lane diversifies at all or merely
adds another correlated draw. Its corpus and answer key are private
(`lrhe-data/`); never copy them into the public package.

## One targeted refutation round

Run a refutation only when the machine gate returns `action: targeted-refuter`
for a readiness-complete `remediation` epoch. Resolve eligible reviewers with
`./lrhe/qualification.py targeted-refuter --lead-family
<gpt|claude|gemini|grok>`. That roster is fixed and separate: it never contains a
conditional critic and takes no record or packet. There is at most
one targeted refutation for the entire review sequence. Dispatch it through the
same three commands as round one — `freeze`, then `resolve --review-class
targeted-refuter` naming the complete fixed pool, then `dispatch` — and submit
the emitted payload verbatim. The refuter's standing arrives the same way every
reviewer's does, inside the generated
`CRITICAL_REVIEW_RESOLVER_RECEIPT_V1` block; there is no separate hand-written
refutation launch. The refutation scope carried in the frozen scope document is
only:

```text
Claim and finding ID:
Supporting claim and exact repository anchors:
Counterevidence and exact repository anchors:
Lead verification already performed:
Repository packet path and immutable epoch:
Question that must be answered:
Permitted read-only verification methods:
```

For `repository` delivery, the scope names anchors and the packet path, not copied
source. Require the refuter to inspect the linked implementation with its
read-only repository tools; do not inline the diff, surrounding code, or a
substitute source excerpt. If a future resolver returns `inline` delivery, follow
the general inline packet rules above instead. Selecting a refuter that did not
originate the claim is a lead judgment expressed in the frozen scope, never a
reason to trim the resolved pool.

Do not provide the original reviews, reviewer identities, vote counts, or
rhetoric. Ask the refuter to falsify the single normalized claim, not to conduct
another general review. Record `confirmed`, `falsified`, or `unresolved/human
decision`, update the ledger, and stop. Never start another refutation or a third
general council pass.

## Close and report

Recheck epoch digests once more before final disposition. Mark the review stale instead of synthesizing if they changed. Then report:

- review ID and packet path;
- exact epoch and digest status;
- each selected reviewer's `reviewer_id`, `model_family`, `authority`, and `completed`, `missing`, or `invalid` state;
- the selection manifest path and digest, the resolver receipt and dispatch envelope paths and digests, plus every `not_selected/ineligible` lane with its reason codes;
- ledger path;
- every blocking or unresolved P0/P1;
- accepted implementation/design changes and verification evidence;
- explicit residual risks and human waivers.

Close the frozen epoch before modifying reviewed files. Then apply the sequence gate: close verified localized remediation directly; use the single targeted-refuter path only for a still-disputed P0/P1; return systemic omissions to implementation audit; and open another full council only for a readiness-complete material redesign within the two-pass limit.

## Proving changes to this skill

When modifying this critical-review skill itself, use its stable developer tiers:

```bash
./lrhe/review_checks.py quick
./lrhe/review_checks.py full --subject-record review-record.json --receipt full-proof.json
```

`quick` is the inner loop for `test_review_sequence.py`, `test_runner.py`, and
`test_consistency.py`. `full` is the pre-freeze and pre-push proof: it first
runs `quick` against the operator environment so private qualification authority
is exercised when present, then mirrors the public Actions contract by running
Ruff, the early consistency gate, the entire LRHE test suite under an isolated
`HOME`, and the no-live-transport assertion. The receipt form is mandatory when
this skill's full proof is cited in a review record. Never substitute the
narrower quick selection for `full`.
