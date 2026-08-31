# Critical Review Live Protocol

Full-council operating procedure for `skill://critical-review`. Read it only after
that skill's admission decision selected a full council — including an explicitly
user-requested council. Nothing here is an admission criterion, and nothing here
replaces the assurance selection, focused-review routing, hosted-material floor,
common reviewer assignment, or lead dispositions owned by `SKILL.md`.

Every command below runs from the critical-review skill root, the parent of this
file's `lrhe/` directory. Where prose and code could drift, the executable tools
in this directory and the JSON schemas beside them are authoritative.

## Live roster and unattended provider policy

Live reviewer membership and standing are configuration plus lead lineage, not
reviewer prose:

- `skill://critical-review/qualification.yml` `liveDispatch` is the sole
  authoritative live panel definition;
- `lrhe/qualification.py` is the sole executable resolver for that definition.
  The caller must supply the accountable main session's exact `model_family`;
  the resolver selects that `byLeadFamily` profile and emits the selection
  manifest that records the roster and the ledger's provenance;
- `lrhe/review_dispatch.py` is the sole live dispatch entry point, and its atomic
  `prepare` command is the only operator path to a reviewer. It resolves the
  roster, validates evidence delivery, freezes, resolves standing, builds the
  envelope, and invokes the Task verifier before returning any payload. It
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
- manifest leadFamily records the supplied accountable lead lineage. Only gpt
  and claude profiles exist; Gemini and Grok cannot drive a council;
- strongCritic selects exactly one reciprocal cross-family assurance anchor:
  Opus CVP for a GPT lead or Daybreak Blue for a Claude lead;
- supplements selects Gemini 3.7 Flash and Grok 4.6 on every full council.
  They are cross-family supplemental evidence: useful for fast sanity and cheap
  decorrelation, but unable to rescue the independent floor or break a dispute.
  The strong critic and supplements are pairwise distinct by model family and
  correlation group;
- architectureSpecialists is additive and record-selected. Eligible Fable is
  supplemental whether cross-family or same-lineage, and its absence never
  shrinks the unconditional council;
- no same-family security specialist runs by default. The accountable lead
  already supplies that lineage. A concrete route capability requires a future
  explicit authority change rather than another permanent role group;
- ChatGPT Pro Web through pi-oracle remains outside liveDispatch but is attempted
  asynchronously for every full council. The separate oracleShadow authority has
  no standing and no effect on closure, retries, or reviewer substitution. It is
  selected only when the packet grants both its OpenAI data key and exact access
  profile; every withheld grant records a nonblocking skip;
- standing reaches each reviewer only inside the generated
  CRITICAL_REVIEW_RESOLVER_RECEIPT_V1 block. evaluationOnly and disabled lanes,
  and every experiment in lrhe/panels.yaml, never authorize live review.

Before dispatch, in addition to the hosted-material floor in `SKILL.md`:

1. Read skill://critical-review/qualification.yml. A reviewer is enabled only when its reviewer_id is in the selected profile or required global group, dispatchEnabled is true, its canary and read-only gates pass, and its exact selector resolves. Fable additionally needs a passed scope receipt for the requested architecture domain.
2. For ordinary cloud-permitted work, populate `provider_data_allowlist` and `reviewer_access_profile_allowlist` from the complete deterministic candidate set for the selected class and lead-family profile. Match access_profile, not provider_route. An explicit provider restriction may only narrow those generated grants; it never triggers Ask or model substitution. A missing generated grant on the strong critic or an always-on supplement fails resolution; a record-selected architecture specialist is skipped with every reason recorded.

A missing, disabled, timed-out, schema-invalid, restricted, or unqualified reviewer is missing, never approved. Do not substitute another model or ask the user to pick one. An architecture specialist the resolver did not select is not_selected/ineligible. Any selected supplemental_evidence row remains additive: it cannot rescue the independent floor, break a disagreement, or earn a retry that the strong critic would not get.

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
- exit `20` (`ceremony-required`): a dispatching action is projected; materialize
  the deterministic packet grants, confirm panel resolvability, then freeze;
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

Reviewers must inspect one stable epoch. Prefer a no-effect digest checkpoint. When repository evidence requires a commit, use the workflow's already-authorized normal Conventional Commit after verification; never mutate history or invoke Ask solely to manufacture a review subject.

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

For ordinary cloud-permitted full councils, the generated grants include `openai` in `provider_data_allowlist` and `chatgpt-pro-web-asxst0rm` in `reviewer_access_profile_allowlist`, so the standard Oracle shadow is attempted without a user prompt. An explicit provider restriction may omit either grant; that records the Oracle lane as skipped and never fails or shrinks the qualified Task council.

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

## Round one: atomic preparation and dispatch

Live dispatch has exactly one entry point, `./lrhe/review_dispatch.py`, and one
operator command, `prepare`. The lead never assembles a reviewer assignment,
retypes a roster, writes a standing field into prose, or manually sequences
freeze, resolve, and dispatch. The command performs this fail-closed order before
any provider call is possible:

1. resolve the complete roster from the frozen record, immutable packet,
   accountable `lead_family`, review class, and fixed live authority;
2. validate every selected row's `evidence_delivery` against the proposed
   subject and packet bytes;
3. freeze the scope, packet, optional record, panel manifest, and repository
   identity;
4. resolve standing into the receipt;
5. build the canonical envelope; and
6. run `verify-task`, which repeats the evidence invariant from freshly read
   bytes before printing the approved Task input.

For an initial council, run exactly:

```bash
./lrhe/review_dispatch.py prepare \
  --scope scope.md --packet packet.md --record review-record.json \
  --manifest ~/.omp/agent/critical-review/<review-id>/panel-selection.json \
  --repo <repository-root> --commit <40-hex-commit-equal-to-HEAD> \
  --file <repository-relative-path> [--file <repository-relative-path> ...] \
  --lead-family <gpt|claude> --review-class initial \
  --subject ~/.omp/agent/critical-review/<review-id>/frozen-subject.json \
  --receipt ~/.omp/agent/critical-review/<review-id>/resolver-receipt.json \
  --out ~/.omp/agent/critical-review/<review-id>/review-dispatch-envelope.json
```

`prepare` resolves and writes the panel manifest itself. The manifest binds the
absolute record path and digest, proof-subject digest, packet path and digest,
lead family, qualification authority and resolver bytes, and the exact ordered
`selected` and `skipped` rows. The frozen subject binds the manifest path and
digest plus the digest of `lrhe/panel-selection.schema.json`. Freeze re-resolves
the manifest and requires byte-equivalent content; a stale, hand-edited,
filtered, reordered, or supplemented manifest is refused.

Evidence compatibility is executable policy:

- Any selected `evidence_delivery=repository` row requires
  `subject_kind=repository`, a caller-supplied lowercase 40-hex commit equal to
  clean `HEAD`, and a nonempty exact list of regular files bound in that commit.
  Directories, symlinks, submodule gitlinks, modified files, and untracked files
  are refused.
- A `packet-only` subject is admissible only when every selected row uses
  `inline` delivery and `design_or_diff` embeds the complete UTF-8 evidence bytes
  in this versioned form:

  ```yaml
  design_or_diff:
    format: critical-review-complete-inline-evidence-v1
    artifacts:
      - name: src/example.py
        sha256: <sha256 of the exact UTF-8 content below>
        content: |
          <complete reviewed bytes>
  ```

  A path, `agent://` handle, prose summary, or unstructured excerpt supplied in
  place of this bundle never counts as inline evidence. The versioned format is
  the accountable packet producer's declaration that each `content` value is a
  complete artifact, so the verifier treats those declared UTF-8 bytes as opaque
  and proves their digest rather than guessing their meaning from their text.
  Artifact names are labels only. A repository subject with any inline member
  must carry the same complete bundle for that member, while every repository
  member still requires the bound commit and files.

A focused preparation uses the same command with `--review-class focused`; the resolver infers the profile's reciprocal strong critic and rejects any caller-supplied reviewer choice. It has no panel manifest. A targeted-refuter
preparation uses `--review-class targeted-refuter`, requires the remediation
record, and infers the complete fixed pool; callers do not name or filter it.
Packet, scope, repository, subject, receipt, and envelope arguments retain the
same meanings.

The lower-level freeze, standing-resolution, and envelope builders remain
internal policy functions for `prepare` and focused tests. They are not CLI
subcommands and cannot mint a second provider-ready route. The only other CLI
surface is internal `verify-task`, invoked by the Task policy gate against an
existing `prepare` envelope.

The review class is a completeness contract, not a label. `initial` is exactly
the manifest's selected council; `focused` is exactly one configured initial
critic; `targeted-refuter` is the complete fixed refutation pool. Standing remains
resolver-owned: `selectionClass`, `role`, `independence_class`, and `authority`
come from the fixed live authority and cannot be caller-supplied.

`prepare` writes each strict, hash-bound, non-overwriting artifact atomically,
with the envelope last as the dispatchability commit marker. A stop before that
write leaves no provider-addressable dispatch; a caught failure removes every
artifact created by the invocation. Only after `verify-task` rehashes the
envelope and revalidates the current resolver and qualification authority,
subject and manifest state, evidence compatibility, receipt, assignments, and
exact canonical Task input does `prepare` print one JSON object whose only key
is `task_input`. Submit that object verbatim as the Task call. An edited
envelope, stale manifest, incompatible evidence mode, dirty tree, or path
supplied instead of an inline bundle is blocked rather than
warned.

The manifest remains the epoch's roster of record and ledger provenance. Join
rows only by `reviewer_id`; `model_family` is descriptive and two rows may share
it. Every `skipped` entry retains its sorted `reasonCodes` and is reported as
`not_selected/ineligible` in the close report.

### Canonical Task boundary
OMP uses one canonical batch Task shape for every review class: exactly `i`,
`context`, and `tasks`. A single `focused` reviewer or targeted refuter is a
one-item batch; never add a padding reviewer. `context` is:

```text
CRITICAL_REVIEW_DISPATCH_V1
envelope_path=<absolute path>
envelope_sha256=<64 lowercase hex>
```

The generated `i` is pinned as the envelope's `taskIntent` schema `const`.
Every batch item carries exactly `agent` and `task`. Never retype, reorder,
trim, merge, summarize, or reword one character of the canonical shape, and
never append an instruction of your own.

A multi-reviewer council runs in one gated Task wave.
Every `review-*` agent declares `blocking: true`. Submit the generated Task
call once; its inline completion is the review barrier for a focused review or
the whole concurrent council. Do not detach reviewers, inspect `hub` jobs, or
poll/sleep for verdicts. No reviewer payload may enter the lead's context or a
peer-visible file until every member has settled.

The policy gate records the envelope as in flight before execution, blocks
concurrent or completed replay in the same session, and opens the one
byte-identical transport retry only after the Task tool returns a terminal
error. This state follows the protected Task lifecycle rather than the enclosing
eval lifecycle, so an outer nonterminal transition cannot authorize redispatch.

After the same final verifier approves the canonical Task input, the policy reads
the envelope's resolver-derived `oracleShadow` state. A selected lane submits
once through pi-oracle from a hook-free detached worktree at `subject_commit`.
Before preflight or provider submission, it persists a launch-phase pointer and
starts a detached collector. Restarts retain the pointer's original six-hour
deadline; an interrupted or uncertain submission becomes finite evidence and is
never resubmitted. The immutable result artifact is canonical, and collectors or
later launches repair the run-keyed dataset row from it rather than contradicting
it. Request, dispatch, and result artifacts remain beside the review record;
every council keeps a separate row under `lrhe-data/oracle-shadow/`. Selected,
skipped, disabled, launch-failed, launch-outcome-unknown, collector-timeout,
schema-invalid, and terminal job outcomes are evidence for later evaluation only.
The launch is not awaited by Task, receives no reviewer output, cannot authorize
a retry, and an identical Task transport retry reuses the durable request or job.

The extension treats every Task item whose agent name starts with `review-` as
protected. A protected or mixed call without the exact
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
reuses the existing envelope: `prepare` never overwrites one, and preparing a
fresh envelope starts a new epoch rather than a retry. Never reword
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
for a readiness-complete `remediation` epoch. Dispatch it only through
`./lrhe/review_dispatch.py prepare --review-class targeted-refuter`. The command
requires the frozen remediation record and immutable packet, resolves the fixed
pool itself, validates each member's evidence mode, and refuses caller-supplied
reviewers. There is at most one targeted refutation for the entire review
sequence. Submit the emitted payload verbatim. The refuter's standing arrives
inside the generated `CRITICAL_REVIEW_RESOLVER_RECEIPT_V1` block; there is no
separate hand-written refutation launch. The refutation scope carried in the
frozen scope document is only:

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
