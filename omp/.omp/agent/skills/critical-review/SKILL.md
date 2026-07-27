---
name: critical-review
description: Run independent cross-family review for consequential or hard-to-reverse changes.
---

# Critical Review Council

Run an opt-in, evidence-driven council. GPT remains the accountable lead and integrator. Critics inspect independently; they do not vote, rewrite the solution, edit the repository, or see one another's first-round output.

## Scope and authorization

Use this skill for architecture, authentication or authorization, secrets or cryptography, privacy, money or asset movement, persistent migrations or deletion, public protocol compatibility, concurrency or distributed coordination, release or supply-chain changes, cross-system boundaries, and weak or costly rollback.

Map reviewer names to external processors:

- `review-claude` -> Anthropic;
- `review-gemini` -> Google Antigravity;
- `review-grok` -> xAI Grok Build.

Before dispatch:

1. Classify the repository and review material as public, cloud-eligible, or local-only from existing context.
2. Read `skill://critical-review/qualification.yml`. A provider is enabled only when its entry says `councilEnabled: true`, its exact model selector still resolves, and the packet authorizes that provider.
3. Treat an explicit provider list in the user's current `/skill:critical-review` request as authorization for only those providers and this review epoch.
4. If hosted-provider authorization is absent or unclear, invoke Ask before transmitting material. Recommend the safest qualified subset. Include a no-effect/local-only option. Never send local-only material to a hosted reviewer.
5. Never include credential values, private keys, tokens, cookies, environment dumps, secret files, or generated credential stores in a packet. A source file containing secret-handling code may be reviewed only when the chosen providers are authorized for it; redact actual values without changing the reviewed semantics.

A missing, disabled, timed-out, schema-invalid, or unauthorized reviewer is `missing`, never `approved`. Do not substitute another GPT model for an unavailable external family.

## Freeze one review epoch

Reviewers must inspect one stable epoch. Prefer a no-effect digest checkpoint; a temporary commit or any history mutation requires the authorization already present in the current request or an Ask gate.

1. Choose a review ID such as `CR-<UTC timestamp>-<short digest>` and create `local://critical-review/<review-id>/`.
2. Record the repository root, current branch, base commit, HEAD, staged and unstaged changed paths, review-scoped untracked paths, and relevant design artifacts.
3. Materialize the complete review-scoped diff or design as an artifact in that directory. Exclude ignored files and any secret-bearing material not authorized for the selected providers.
4. Compute a SHA-256 digest for the review artifact and a SHA-256 digest for every reviewed changed file. Record deleted files explicitly. Use stable path ordering.
5. Do not modify reviewed files from this point until the epoch closes. Other unrelated work must not touch them.
6. Recompute the same artifact and file digests after round one and before synthesis. Any mismatch makes every result for that epoch stale. Close it and start a new review ID; never blend stale and fresh claims.

Create `local://critical-review/<review-id>/packet.md` with these fields:

```yaml
review_id:
risk_class: critical
repository:
base_commit:
review_commit_or_checkpoint:
artifact_digest:
changed_file_digests:

goal:
non_goals:
requirements:
invariants:
trust_boundaries:
data_or_state_transitions:
rollback_contract:
compatibility_contract:

changed_files:
design_or_diff:
tests_already_run:
test_results:
known_open_questions:
rejected_alternatives_and_reasons:
provider_data_allowlist:
```

Use concise facts and primary anchors. Include the decision record, not hidden reasoning, tentative confidence, or another reviewer's verdict. Packet links must resolve for every selected reviewer.

## Round one: independent concurrent critics

Launch every selected and enabled critic in one `task` batch. Do not launch separate calls that serialize independent reviews. Shared context names only the immutable packet, review ID, epoch, and the independence rule. Do not put a reviewer output or `agent://` handle in shared context.

Use these stable items for the qualified subset:

- name `ClaudeCritical`, agent `review-claude`;
- name `GeminiCritical`, agent `review-gemini`;
- name `GrokCritical`, agent `review-grok`.

Each item must set `schemaMode: strict` and use this complete assignment shape:

```text
# Target
Review the immutable packet at <packet path> and only the repository epoch it identifies. Do not modify files or inspect peer output.

# Change
Apply the common critical floor and the primary lens defined by your agent. Return falsifiable root-cause claims with exact evidence. This is review only; no implementation or competing rewrite.

# Acceptance
Return one schema-valid summary/evidence/unresolved object, at most 12 evidence items, exact source anchors where available, and explicit missing evidence for unresolved claims.
```

Do not give reviewers caller-provided output schemas that weaken their agent schema. Do not disclose round-one responses between reviewers through messages, prompts, local files, or follow-up calls. Wait for every selected job to settle, then read each complete `agent://` result separately.

## Finding ledger

After the epoch digest recheck passes, create `local://critical-review/<review-id>/ledger.md`. Normalize every `evidence` and `unresolved` item into one row with:

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

Run a second round only when a P0/P1 remains disputed or unresolved after direct verification. There is at most one targeted refutation round for the entire review ID.

Choose one qualified family that did not originate the claim when possible. Launch one reviewer task with `schemaMode: strict`. Give it only:

```text
Claim:
Exact supporting evidence:
Exact counterevidence:
Repository epoch and packet:
Question that must be answered:
Permitted read-only verification methods:
```

Do not provide the original reviews, reviewer identities, vote counts, or rhetoric. The assignment must ask the refuter to falsify the single normalized claim, not to conduct another general review. Record the result as `confirmed`, `falsified`, or `unresolved/human decision`, update the ledger, and stop. Never start a third round.

## Close and report

Recheck epoch digests once more before final disposition. Mark the review stale instead of synthesizing if they changed. Then report:

- review ID and packet path;
- exact epoch and digest status;
- each selected reviewer's model family and `completed`, `missing`, or `invalid` state;
- ledger path;
- every blocking or unresolved P0/P1;
- accepted implementation/design changes and verification evidence;
- explicit residual risks and human waivers.

There is no majority verdict. The GPT lead owns the final evidence-based decision and coherent revision. Close the epoch before modifying reviewed files. If revision changes the reviewed contract or resolves a blocking finding, run a new review epoch rather than treating the old approval as current.
