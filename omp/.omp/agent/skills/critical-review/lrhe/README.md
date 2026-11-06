# LRHE — Lens-Rotation Historical Evaluation on public corpora

Evaluates critical-review reviewer families and lenses against a 47-item public
corpus without transmitting private repository history. Read
[`LRHE-PROTOCOL.md`](LRHE-PROTOCOL.md) first for the pre-registration and
[`PROVENANCE.md`](PROVENANCE.md) for the upstream source audit.

## Where this lives

Stowed into `~/.omp/agent/skills/critical-review/`, split across two repositories.
The cut is **replicable vs. accumulated**, not public vs. private:

```text
~/.omp/agent/skills/critical-review/
  SKILL.md            →  .dotfiles/omp            PUBLIC   alphastorm/dotfiles-ng
  lrhe/                  harness, schemas, PROVENANCE.md

  qualification.yml   →  .dotfiles-private        PRIVATE  (Git LFS)
  lrhe-data/             corpus, sweep, ledger, terms snapshots, handoff/
```

**Public because replicable.** Anyone reading [`LRHE-PROTOCOL.md`](LRHE-PROTOCOL.md)
can rebuild the harness. Publishing the methodology and verifier makes the
evaluation citable and auditable by a third party.

**Private because accumulated.** Three artifact sets in `lrhe-data/`, in
ascending order of reconstruction cost:

- `sweep.jsonl` — 184 paired ARVO container runs (~6 h and ~370 GB of image pulls).
- `corpus.jsonl` — the 47-item corpus *with its answer key*: every label plus
  `trap.ground_truth` for all twelve S4 traps. Publishing it exposes the trap
  baits to search and defeats the contamination controls in
  [`LRHE-PROTOCOL.md#3-contamination-control`](LRHE-PROTOCOL.md#3-contamination-control).
- `ledger/findings.jsonl` — production findings from `shadow_ledger.py` (beside
  `reviews.jsonl`, `runs.jsonl`, and `dispositions.jsonl`), accumulated across
  closed live reviews.

`qualification.yml` is also in the private package because it records exact
model selectors, account bindings, quota paths, live lane standing, and active
blockers. `skill://critical-review/qualification.yml` resolves either way:
`setup.sh` pre-creates `~/.omp/agent/skills/critical-review/` so GNU Stow merges
both packages into one unfolded directory.

One Stow gotcha: `lrhe/` is a symlink to the public checkout, so `..` from
inside `lrhe/` resolves to the **dotfiles** parent rather than the stowed
`critical-review/` directory. Address `lrhe-data/` by absolute path:

```bash
cd ~/.omp/agent/skills/critical-review/lrhe
D=~/.omp/agent/skills/critical-review/lrhe-data
python3 validate_corpus.py --corpus "$D/corpus.jsonl" --plan
```

All 47 corpus items (85 labels across S1–S5: 37 critical, 5 executable, 12
traps, 11 controls) are built, validated, and scrubbed into reviewer-safe
packets. See [`PROVENANCE.md#current-corpus-state`](PROVENANCE.md#current-corpus-state)
for the stratum table and
[`PROVENANCE.md#13-provider-authorization-was-a-two-way-switch-on-a-three-way-question`](PROVENANCE.md#13-provider-authorization-was-a-two-way-switch-on-a-three-way-question)
for the four S3 upstream license grants (`check_packet_gates.py grant` must be
re-run after any corpus rebuild because `_allowlist()` in `sources.py` defaults
unclassified upstream licenses to an empty allowlist).

## Quick start

```bash
# setup.sh creates this venv whenever uv is installed; by hand:
uv venv --python 3.13 .venv                        # pinned; see requirements.txt
VIRTUAL_ENV=.venv uv pip install -r requirements.txt
source .venv/bin/activate

python3 build_corpus.py plan                       # sampling plan, no network
python3 power_lrhe.py --sweep-items 16,24,32,40,56 --effect 0.8 --reps 300

# Skill-development check tiers (run from this directory):
./review_checks.py quick  # five-file tier against the current HOME
./review_checks.py full   # quick + Ruff + clean-HOME public CI contract

# Citable pre-freeze/pre-push proof, bound to exact artifact and file digests:
./review_checks.py full --subject-record review-record.json --receipt full-proof.json

# Bind any other exact verification command to a review record:
./make_receipt.py --subject-record review-record.json --receipt proof.json \
    --cwd /path/to/repository -- command arg1 arg2

# Offline synthetic fixtures and end-to-end simulation:
python3 make_fixtures.py                           # writes ./fixtures, never ./
python3 score_lrhe.py --corpus fixtures/corpus.jsonl --runs fixtures/runs.jsonl \
    --judge fixtures/judge.jsonl --exec fixtures/exec.jsonl \
    --out-claims claims.csv --out-runs runs.csv --out-report report.json
python3 simulate_experiment.py                     # full synthetic run, end to end

# Build the corpus (network; GitHub token via `gh auth`; S2 requires pyarrow):
python3 build_corpus.py fetch --stratum S1 --out raw/S1.jsonl
python3 build_corpus.py fetch --stratum S2 --out raw/S2.jsonl
python3 build_corpus.py fetch --stratum S5 --out raw/S5.jsonl --n 3

# S3 and S4 come from paired ARVO container execution:
curl -L --fail -o .cache/arvo.db \
    https://github.com/n132/ARVO-Meta/releases/download/v3.0.0/arvo.db
shasum -a 256 .cache/arvo.db   # 331184ca807c2f136f98dac9f1df94c893f4ee2fdf9329dca517ff88e72f97ce
python3 build_corpus.py fetch --stratum S3 --out raw/S3_candidates.jsonl
python3 build_corpus.py arvo-sweep --candidates raw/S3_candidates.jsonl --out sweep.jsonl --limit 120
python3 build_corpus.py arvo-build --candidates raw/S3_candidates.jsonl --sweep sweep.jsonl

# Assemble, validate, scrub, and generate probes and assignments:
cat raw/S*.jsonl > raw/all.jsonl                   # excluding S3_candidates
python3 validate_corpus.py --corpus raw/all.jsonl --plan
python3 build_corpus.py scrub --in raw/all.jsonl --out corpus.jsonl \
    --dispatch-out packets.jsonl --rename
python3 validate_corpus.py --corpus corpus.jsonl --plan
python3 build_corpus.py probes --corpus corpus.jsonl --out probes/
python3 build_corpus.py assignments --out assignments.csv --corpus corpus.jsonl --d-items 24

# Freeze the starting state before the first paid call; verify checks for drift:
python3 freeze_lock.py freeze
python3 freeze_lock.py verify

# Score and analyze collected runs (both require --experiment-id and --panel-id):
python3 score_lrhe.py --corpus corpus.jsonl --runs runs.jsonl --judge judge.jsonl \
    --experiment-id lrhe-core-v1 --panel-id core-cgg-v1 --manifest assignments.manifest.json
python3 analyze_lrhe.py --claims claims.csv --runs runs.csv --corpus corpus.jsonl \
    --experiment-id lrhe-core-v1 --panel-id core-cgg-v1 \
    --probe probe.csv --judge-calibration calib.csv --boot 10000
```

`--experiment-id` and `--panel-id` have no defaults so runs from different panels
cannot be pooled by accident. Experiment panels and lens prompts are defined in
`panels.yaml`. `corpus.jsonl` carries the answer key; `packets.jsonl` is the
scrubbed projection a reviewer may see (no `repo`, commits, `labels`,
`build_notes`, or trap `ground_truth`). Never dispatch `corpus.jsonl`.

## Authority and live dispatch

Live critical-review operation is separate from offline LRHE experiments and is
governed by [`../SKILL.md`](../SKILL.md) (admission and focused routing) and
[`LIVE-PROTOCOL.md`](LIVE-PROTOCOL.md) (on-demand full-council protocol):

- **`qualification.yml` `liveDispatch` owns live membership.** Its `byLeadFamily`
  profiles (`gpt` and `claude`), `disabled` list, reviewer entries, and optional
  per-lane `oauthAccount` bindings are the sole source of live standing.
  `panels.yaml` owns offline LRHE experiments only; this public directory never
  grants live membership. See
  [`LIVE-PROTOCOL.md#live-roster-and-unattended-provider-policy`](LIVE-PROTOCOL.md#live-roster-and-unattended-provider-policy)
  and
  [`LIVE-PROTOCOL.md#pragmatic-full-council-composition`](LIVE-PROTOCOL.md#pragmatic-full-council-composition).
- **`qualification.py` is the executable roster resolver.** It resolves
  `initial` (full council), `focused`, `replay`, and `targeted-refuter` rosters
  for `--lead-family`, enforces two-grant authorization
  (`provider_data_allowlist` and `reviewer_access_profile_allowlist`), and
  publishes a read-only `panel-selection.schema.json` manifest bound to the
  record, packet, resolver, and qualification digests.
- **`review_dispatch.py prepare` is the only live dispatch operator path.** It
  resolves the roster, freezes the subject, validates inline or repository
  evidence delivery, writes the dispatch envelope, and runs `verify-task` before
  emitting the canonical batch `Task` payload. For full-council epochs,
  `full_council.py prepare` runs record creation, freeze, and
  `review_dispatch.py prepare` in one step. See
  [`LIVE-PROTOCOL.md#one-command-council-preparation`](LIVE-PROTOCOL.md#one-command-council-preparation),
  [`LIVE-PROTOCOL.md#round-one-atomic-preparation-and-dispatch`](LIVE-PROTOCOL.md#round-one-atomic-preparation-and-dispatch),
  and [`LIVE-PROTOCOL.md#canonical-task-boundary`](LIVE-PROTOCOL.md#canonical-task-boundary).
- **`preflight.py` enforces trace receipts, cohorts, and standing amendments.**
  Reviewer charters, model selectors, and `canaryReceipt` / `charterAmendment`
  chains (`resolver-receipt-standing-source-v1`,
  `lrhe-chained-charter-standing-amendment-v1`,
  `lrhe-model-upgrade-standing-amendment-v1`) are verified before dispatch. See
  [`LIVE-PROTOCOL.md#reviewer-qualification-canary`](LIVE-PROTOCOL.md#reviewer-qualification-canary),
  [`LIVE-PROTOCOL.md#trace-preparation-and-standing-amendments`](LIVE-PROTOCOL.md#trace-preparation-and-standing-amendments),
  and [`LIVE-PROTOCOL.md#model-upgrades`](LIVE-PROTOCOL.md#model-upgrades).

## Evidence and adjudication

Every evaluation run record must validate against `run.schema.json` (v2), where
every boolean hard gate (`schema_valid`, `telemetry_complete`, `identity_verified`,
`wrote_to_repo`) is required rather than defaulted; failed gates mark a row
`gate_failed` in `runs.csv` and `analyze_lrhe.py` drops disqualified runs before
estimation. `reviewer.provider_fingerprint` is required on every run record so
`analyze_lrhe.py` refuses to pool two non-null checkpoints under one selector;
when a retained session transcript carries no checkpoint identifier,
`provider_fingerprint_observation` is `not_observed`, never `observed_absent`.
`item.schema.json` is closed (`additionalProperties: false`), and
`test_consistency.py` requires every item property to appear in either
`_DISPATCH_KEYS` or `_WITHHELD_KEYS` in `build_corpus.py`. `run_review.py`
(`plan`, `dispatch`, `prompts`, `ingest`) enforces qualification, packet gates,
and data-rights validation before rendering prompts from `panels.yaml`; its
`TRANSPORTS` table contains only `none` (default, raises on send) and `stub`
(canned apparatus reply), with no live socket in this repository. For the
pre-registered scoring stages, verdict lattice, and source-audit fixes, see
[`LRHE-PROTOCOL.md#5-scoring`](LRHE-PROTOCOL.md#5-scoring),
[`PROVENANCE.md#12-the-run-matrix-was-not-reproducible`](PROVENANCE.md#12-the-run-matrix-was-not-reproducible),
[`PROVENANCE.md#15-the-consensus-rule-recorded-a-bare-majority-as-settled`](PROVENANCE.md#15-the-consensus-rule-recorded-a-bare-majority-as-settled),
and
[`PROVENANCE.md#16-the-human-calibration-gate-could-not-be-scored-at-all`](PROVENANCE.md#16-the-human-calibration-gate-could-not-be-scored-at-all).
The dated 2026-07-28 seven-item smoke receipt (`lrhe-data/smoke/`) is preserved
in the baseline README at
[`ee7d76e4#the-seven-item-smoke-pass-2026-07-28`](https://github.com/alphastorm/dotfiles-ng/blob/ee7d76e4946bf0e1115b9c71d61f9c2ce592cd02/omp/.omp/agent/skills/critical-review/lrhe/README.md#the-seven-item-smoke-pass-2026-07-28).

`judge_lrhe.py` drives cross-family claim adjudication, cold refutation of
disputed P0/P1 claims, and the 60-claim human calibration (κ ≥ 0.70
gate in [`LRHE-PROTOCOL.md#8-promotion-gates`](LRHE-PROTOCOL.md#8-promotion-gates)):

```bash
# Adjudication: two non-authoring families per claim
python3 judge_lrhe.py prompts --corpus corpus.jsonl --runs runs.jsonl \
    --claims claims.csv --families gpt claude gemini grok kimi glm --out jp.jsonl
python3 judge_lrhe.py ingest --prompts jp.jsonl --responses jr.jsonl \
    --out judge.jsonl --out-judgments judgments.jsonl --human-queue human_queue.jsonl \
    --tiebreak-out tb.jsonl --families gpt claude gemini grok kimi glm

# Cold refutation: disputed P0/P1 claims only -> exec.jsonl -> REFUTED
python3 judge_lrhe.py refute --corpus corpus.jsonl --claims claims.csv \
    --judge judge.jsonl --refuter glm --out rp.jsonl
python3 judge_lrhe.py ingest-refutation --prompts rp.jsonl --responses rr.jsonl --out exec.jsonl

# Human calibration sample and Cohen's kappa
python3 judge_lrhe.py calibrate --claims claims.csv --judge judge.jsonl --n 60 --out calib.csv
python3 judge_lrhe.py kappa --calibration calib.csv --judge judge.jsonl
```

## Before spending

Run the offline gates in order before any paid evaluation or qualification call:

```bash
# 1. Snapshot or verify frozen provider terms in lrhe-data/terms/:
python3 snapshot_terms.py
python3 snapshot_terms.py --offline

# 2. Check per-item route data rights (exit 0 allow, 10 deny, 20 unresolved):
python3 check_data_rights.py --item-id S1-0001 \
    --classification public_corpus --route opencode-go \
    --policy-id opencode-go-2026-07-27

# 3. Exercise canary graders and grade a lane's prompts + repository trace:
python3 canary.py selftest
python3 canary.py run --transport stub --out canary-apparatus-vN.jsonl
python3 canary.py prompts --out cp.jsonl
python3 canary.py trace-receipt --trace session.jsonl --agent-definition agent.md \
    --agent review-X --selector provider/model:effort \
    --evidence-delivery repository --out trace-receipt.json
python3 canary.py grade --prompts cp.jsonl --responses cr.jsonl \
    --trace-receipt trace-receipt.json --out canary-vN.jsonl

# 4. Run every automatic preflight gate (exit 0 pass, 10 fail; spends nothing):
python3 preflight.py
python3 preflight.py --slow

# 5. Freeze runs/LOCK.json last, after qualification.yml and terms snapshots settle:
python3 freeze_lock.py freeze
```

- **Data rights (`check_data_rights.py`):** Exits `0` (allow), `10` (deny), or
  `20` (unresolved). Both `10` and `20` block egress; customer, third-party
  confidential, personal, secret, and unclassified inputs are denied on every
  route, and only `public_corpus` is self-authorizing.
- **Canaries (`canary.py`):** Grades three response probes (`structured_output`,
  `anchor_lookup`, `empty_abstention`) plus, for repository-delivery lanes, a
  validated `trace-receipt` (`4/4` required). `canary.py run --transport stub`
  records `verdict: apparatus` to test the graders and never qualifies a lane.
  The historical 2026-07-28 OpenCode floor-lane canary receipt and grader
  post-mortem are preserved at
  [`ee7d76e4#which-is-why-run-cannot-qualify-anything`](https://github.com/alphastorm/dotfiles-ng/blob/ee7d76e4946bf0e1115b9c71d61f9c2ce592cd02/omp/.omp/agent/skills/critical-review/lrhe/README.md#which-is-why-run-cannot-qualify-anything)
  (those evaluation-only floor lanes were later retired in `qualification.yml`,
  which remains the sole authority for enabled lanes).
- **Preflight and lock order (`preflight.py`, `freeze_lock.py`):** `preflight.py`
  verifies reviewer definitions, `thinkingLevel` keys, output schemas, Stow
  symlinks, OMP model-cache selectors, and canary/cohort digests without network
  calls. Freeze `runs/LOCK.json` last (`--allow-dirty` only when deliberately
  recording a dirty tree) because qualification and terms updates change the
  private repository commit hashed into the lock.

## Ledger and router

`shadow_ledger.py` records live council reviews into the committed private
ledger (`lrhe-data/ledger/`), and `router_dataset.py` builds derived, versioned,
gitignored training tables under `router/`:

```bash
D=~/.omp/agent/skills/critical-review/lrhe-data
python3 shadow_ledger.py review   --runs runs.jsonl --out "$D/ledger/reviews.jsonl" ...
python3 shadow_ledger.py ingest   --runs runs.jsonl --out "$D/ledger/findings.jsonl"
python3 shadow_ledger.py outcomes --findings "$D/ledger/findings.jsonl" --repo /path/to/repo

python3 router_dataset.py build   --reviews "$D/ledger/reviews.jsonl" \
    --findings "$D/ledger/findings.jsonl" --runs runs.jsonl
python3 router_dataset.py verify        # every lineage reference still resolves
python3 router_dataset.py delete-source --review-id R-0007
```

Three invariants are enforced by `review.schema.json`, `finding.schema.json`,
and `router-example.schema.json`:

1. **Features freeze before dispatch:** diff features at `epoch_commit` freeze
   at `features_frozen_at`; `router_dataset.py` refuses any example whose
   features were frozen after its reviewer started.
2. **Labels carry maturity:** `label_maturity_days` records how far forward
   history was inspected, and unobserved escapes remain `null` rather than
   `false`.
3. **No subtractive critic removal:** `decision_authority` permits shadow
   prediction, advisory ranking, additive reviewer selection, refuter selection,
   and cost forecasting, and refuses `subtractive` removal of a required critic.
   The dataset unit is the `(review, family, lens)` cell.

## Tests

```bash
./review_checks.py quick        # 5-file inner tier against the current HOME
./review_checks.py full         # quick + isolated clean-HOME Ruff, pytest, and transport check
.venv/bin/python -m pytest -q
.venv/bin/ruff check .          # rule set pinned in ruff.toml
```

In `review_checks.py full` and `.github/workflows/lrhe.yml` (both hosted and
fleet lanes), `test_consistency.py` runs first as the fail-fast cross-file
invariant gate and the subsequent full pytest pass runs with
`--ignore=test_consistency.py`, collecting `test_consistency.py` once per
environment. `review_checks.py full` intentionally runs `quick` in the caller's
real `HOME` first (so private-package contracts run when stowed locally) and
then runs the public CI contract in an isolated temporary `HOME`. Tests that
read the private corpus (`lrhe-data/`) or private reviewer/judge agent
definitions skip cleanly on public CI runners where the private repository is
absent.

| File | Coverage |
|---|---|
| `test_consistency.py` | Cross-file invariants: `SKILL.md` vs. `LIVE-PROTOCOL.md` ownership, `panels.yaml` vs. `provider-policies.yaml`, terms snapshots, item key classification, schema vocabularies, and lock paths. |
| `test_full_council.py` | End-to-end `full_council.py prepare` record, freeze, packet, and dispatch preparation in isolated repositories. |
| `test_review_sequence.py` | Epoch state machine, readiness gates, `qualification.py` roster resolution, and `review_checks.py` command contracts. |
| `test_review_dispatch.py` | Risk floors, `review_dispatch.py prepare` / `verify-task`, inline and repository evidence bundles, and `REVIEW.md` bindings. |
| `test_review_budget.py` | Two-pass review budget and remediation/refutation gating. |
| `test_selector_canary.py` | Selector parsing, trace receipts, and canary ledger validation. |
| `test_runner.py` | Pre-egress gates in `run_review.py`, zero-call transport spies on refusal, and prompt/ingest rights re-validation. |
| `test_invariants.py` | `score_lrhe.py` and `analyze_lrhe.py` statistical invariants, replicate survival, cross-family judge enforcement, and stratum apportionment. |
| `test_corpus_tools.py` | Corpus construction, scrubbing, and the six packet gates in `check_packet_gates.py`. |
| `test_ledger.py` | Shadow-ledger schema v2 validation, router dataset construction, and `freeze_lock.py` drift detection. |
| `test_auto_reliability.py` | Automated re-adjudication stability and fingerprint observation checks. |
| `test_fleet_workflow.py` | `.github/workflows/lrhe.yml` fleet dispatch-validation contract. |
| `critical-review-policy.test.ts` | TypeScript extension enforcement (`bun test ./omp/.omp/agent/skills/critical-review/lrhe/critical-review-policy.test.ts` from the repository root). |

## Statistics and known traps

- **Unit of analysis vs. resampling:** The unit of analysis is the labeled
  defect; the unit of resampling in `analyze_lrhe.py` is the **item** (defects
  within a PR are correlated, so defect-level binomial intervals overstate
  precision). Recall is never averaged across strata because S1 human review
  comments and S2/S3 executable failures have different label semantics. See
  [`LRHE-PROTOCOL.md#6-analysis`](LRHE-PROTOCOL.md#6-analysis) and
  [`LRHE-PROTOCOL.md#10-limits-stated-plainly`](LRHE-PROTOCOL.md#10-limits-stated-plainly).
- **Arm T empirical null:** Three identical reviewers each produce apparent
  "unique verified findings" by chance. Arm T runs a same-family triplicate so
  `analyze_lrhe.py` can compare cross-family union recall against an empirical
  null of equal size. See
  [`LRHE-PROTOCOL.md#4-arms-and-the-rotation-design`](LRHE-PROTOCOL.md#4-arms-and-the-rotation-design)
  and
  [`LRHE-PROTOCOL.md#7-the-finding-that-should-change-the-design`](LRHE-PROTOCOL.md#7-the-finding-that-should-change-the-design).
- **Upstream dataset traps and cross-family judging:** SWE-bench Verified leaks
  solution code and predates model cutoffs (use SWE-bench-Live for S2, noting
  its 2025-06 freeze in [`PROVENANCE.md#6-swe-bench-live-is-no-longer-live-and-the-date-gate-cannot-be-met`](PROVENANCE.md#6-swe-bench-live-is-no-longer-live-and-the-date-gate-cannot-be-met));
  SWE-PRBench contains bot comments that `sources.py` strips
  ([`PROVENANCE.md#1-swe-prbench-ground-truth-contains-ai-authored-review-comments`](PROVENANCE.md#1-swe-prbench-ground-truth-contains-ai-authored-review-comments));
  ARVO crash detection must key on sanitizer output rather than container exit
  code ([`PROVENANCE.md#9-sanitizer-output-not-exit-status-is-the-crash-oracle`](PROVENANCE.md#9-sanitizer-output-not-exit-status-is-the-crash-oracle));
  and every judged claim requires two non-authoring families calibrated to
  κ ≥ 0.70 against 60 blind human labels
  ([`LRHE-PROTOCOL.md#52-judge-panel--one-deviation-from-swe-prbench`](LRHE-PROTOCOL.md#52-judge-panel--one-deviation-from-swe-prbench),
  [`LRHE-PROTOCOL.md#8-promotion-gates`](LRHE-PROTOCOL.md#8-promotion-gates)).
