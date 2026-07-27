# LRHE — Lens-Rotation Historical Evaluation on public corpora

Unblocks step 10 of the critical-review council plan without transmitting private repository
history. Read `LRHE-PROTOCOL.md` first; it is the pre-registration.


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

**Public because replicable.** Anyone reading `LRHE-PROTOCOL.md` could rebuild the
harness. Methodology is worth more published: it is citable, it invites correction,
and this design's central claim is that it is auditable by a third party. Hiding it
weakens the argument it makes.

**Private because accumulated.** Three artifacts, in ascending order of how
impossible they are to reconstruct:

- `sweep.jsonl` — 184 paired container runs, ~6 h and ~370 GB of pulls.
- `corpus.jsonl` — the corpus *with its answer key*: every label, plus
  `trap.ground_truth` for all twelve S4 traps. Publishing it destroys the trap
  stratum (baits become searchable) and defeats the §3 contamination apparatus.
  `lrhe/.gitignore` guards against that by accident; the guard is not the policy.
- `ledger/findings.jsonl` — production findings from `shadow_ledger.py`. Empty
  today; after thirty-odd reviews it is a routing dataset nobody outside can
  reconstruct at any price.

`qualification.yml` is private too — it names exact model selectors, quota paths,
which lanes are live, and a `blockers:` list that reads as an inventory of gaps.
`skill://critical-review/qualification.yml` still resolves: both packages stow into
one unfolded directory, so the path does not change with the repository.

`setup.sh` pre-creates `critical-review/` to keep stow from folding it. One gotcha:
`lrhe/` is a symlink, so `..` from inside resolves to the **dotfiles** parent, not
the stowed one. Address data absolutely:

```bash
cd ~/.omp/agent/skills/critical-review/lrhe
D=~/.omp/agent/skills/critical-review/lrhe-data
python3 validate_corpus.py --corpus "$D/corpus.jsonl" --plan
```

## Corpus status

43 of 47 items are built, validated, scrubbed and dispatched as reviewer-safe
packets. `PROVENANCE.md` is the source audit — read it before trusting any count,
because several things `LRHE-PROTOCOL.md` §2 asserts about these datasets are not
true of the data as shipped, and three of them would have corrupted the result
silently rather than failing loudly.

| Stratum | Built | Target | Blocker |
|---|---|---|---|
| S1 `REVIEW_HUMAN` | 14 | 14 | — |
| S2 `PATCH_VERDICT` | 10 | 10 | — |
| S3 `VULN_POC` | 4 | 8 | needs 4 more incomplete fixes: ~76 more swept cases (~2.5 h) at the observed 5.3% rate |
| S4 `FP_TRAP` | 12 | 12 | — (6 of the 12 await one license decision; see `license_url`) |
| S5 `NULL` | 3 | 3 | — |

81 labels, 33 critical, 6 executable, 12 traps, 11 controls.

## Quick start

```bash
uv venv --python 3.13 .venv                        # pinned; see requirements.txt
VIRTUAL_ENV=.venv uv pip install -r requirements.txt

python3 build_corpus.py plan                       # sampling plan, no network
python3 power_lrhe.py --sweep-items 16,24,32,40,56 --effect 0.8 --reps 300

# prove the harness before spending quota
python3 -m pytest test_invariants.py               # the silent-failure guards
python3 make_fixtures.py                           # writes ./fixtures, never ./
python3 score_lrhe.py --corpus fixtures/corpus.jsonl --runs fixtures/runs.jsonl \
    --judge fixtures/judge.jsonl --exec fixtures/exec.jsonl \
    --out-claims claims.csv --out-runs runs.csv --out-report report.json
python3 simulate_experiment.py                     # full synthetic run, end to end

# build the corpus (network; GitHub token via `gh auth`; S2 needs pyarrow)
python3 build_corpus.py fetch --stratum S1 --out raw/S1.jsonl
python3 build_corpus.py fetch --stratum S2 --out raw/S2.jsonl
python3 build_corpus.py fetch --stratum S5 --out raw/S5.jsonl --n 3

# S3 and S4 are execution results, not queries: no released ARVO column
# identifies an incomplete fix, so the containers have to run.
curl -L --fail -o .cache/arvo.db \
    https://github.com/n132/ARVO-Meta/releases/download/v3.0.0/arvo.db
shasum -a 256 .cache/arvo.db   # 331184ca807c2f136f98dac9f1df94c893f4ee2fdf9329dca517ff88e72f97ce
python3 build_corpus.py fetch --stratum S3 --out raw/S3_candidates.jsonl
python3 build_corpus.py arvo-sweep --candidates raw/S3_candidates.jsonl --out sweep.jsonl --limit 120
python3 build_corpus.py arvo-build --candidates raw/S3_candidates.jsonl --sweep sweep.jsonl

# assemble, check, scrub, dispatch
cat raw/S*.jsonl > raw/all.jsonl                   # excluding S3_candidates
python3 validate_corpus.py --corpus raw/all.jsonl --plan
python3 build_corpus.py scrub --in raw/all.jsonl --out corpus.jsonl \
    --dispatch-out packets.jsonl --rename
python3 validate_corpus.py --corpus corpus.jsonl --plan
python3 build_corpus.py probes --corpus corpus.jsonl --out probes/
python3 build_corpus.py assignments --out assignments.csv --corpus corpus.jsonl --d-items 24

# freeze the starting state before the first call; verify re-checks and reports drift
python3 freeze_lock.py freeze
python3 freeze_lock.py verify

# ... run the council, collect runs.jsonl per run.schema.json ...
python3 score_lrhe.py --corpus corpus.jsonl --runs runs.jsonl --judge judge.jsonl \
    --experiment-id lrhe-core-v1 --panel-id core-cgg-v1 --manifest assignments.manifest.json
python3 analyze_lrhe.py --claims claims.csv --runs runs.csv --corpus corpus.jsonl \
    --experiment-id lrhe-core-v1 --panel-id core-cgg-v1 \
    --probe probe.csv --judge-calibration calib.csv --boot 10000
```

`--experiment-id` and `--panel-id` are required on both, with no default. A mean
taken over the core lens experiment and the OpenCode floor panel describes neither,
and that failure has no symptom: every statistic still returns a number. The panels
themselves live in `panels.yaml`, which is also where `build_corpus.py` reads its
families and lenses from — a panel edited in Python is a design change with no
artifact and no digest.

```bash
python3 build_corpus.py assignments --corpus corpus.jsonl   # default: 4x4, 762 runs
python3 build_corpus.py assignments --corpus corpus.jsonl --experiment-id lrhe-core-v1
python3 build_corpus.py assignments --corpus corpus.jsonl --experiment-id lrhe-opencode-v1
```

`corpus.jsonl` is the scored corpus and carries the answer key. `packets.jsonl` is
what a reviewer may see: no `repo`, no commits, no `labels`, no `build_notes`, and
for a trap the assertion without its `ground_truth`. Never dispatch `corpus.jsonl`.

## A run that cannot prove itself is not evidence

`run.schema.json` is v2: panel-aware, nested, and every boolean hard gate is
**required**. Absent telemetry used to default to success — `schema_valid` to
`True`, `wrote_to_repo` to `False` — so a runner that failed to capture anything
produced a record indistinguishable from a clean one. It now fails validation, and
`score_lrhe.py` aborts rather than scoring the subset that happens to parse.

A record that validates can still disqualify itself. Identity unverified, a served
model that is not the requested one, a tool violation, a repository digest that
moved, a timeout, a provider error, or an assignment-manifest digest that no longer
matches — each marks the run `gate_failed` with its reason. Those runs stay in
`runs.csv` so they remain auditable; `analyze_lrhe.py` is what drops them, once,
where the estimates are actually made.

`judgments.jsonl` holds one row per judge invocation, and `judge.jsonl` the
deterministic aggregate over them. Ingest refuses a response whose judge shares the
author's family: the pool already excludes it when prompts are generated, but
nothing re-checked it on the way back in, so a hand-edited or mis-routed response
file could seat a family as judge of its own claim — the single-family-judge
problem `LRHE-PROTOCOL.md` §5.2 calls disqualifying.


## The council is asymmetric

Six families, three roles. Conflating them is what makes this look like six votes
when it is four first-pass critics, one conditional refuter, and one accountable
integrator.

| Role | Family | Runs on | Cost driver |
|---|---|---|---|
| author / integrator | `gpt` | arm A, synthesis | per review |
| critic | `claude` `gemini` `grok` `kimi` | arms B, C, D, probe | per review, linear in council size |
| refuter | `glm` | arm R | **per disputed P0/P1 claim**, not per review |

`independent` is a real fourth lens, not a relabelling of the other three:
reconstruct the system and its invariants from primary evidence instead of
checking the author's framing, so it can surface defects the hand-designed
taxonomy itself omits. `lens_sets()` counterbalances any F families over any L
lenses — at 3×3 it reproduces the protocol's Latin square verbatim, at 4×4 it is a
full square, and every family draws every lens exactly once either way. That is
what stops a model and its role becoming permanently confounded.

```bash
# default council: 4 critics x 4 lenses -> 762 reviewer runs
python3 build_corpus.py assignments --corpus corpus.jsonl --d-items 24

# the pre-registered design, reproduced exactly: 523 reviewer + 141 probe
python3 build_corpus.py assignments --corpus corpus.jsonl --d-items 24 \
    --families claude gemini grok --lenses architecture whole_repo adversarial \
    --triplicate-n 3
```

Both write `assignments.manifest.json` beside the CSV: the salt, the 24 selected
subset ids, and SHA-256 of the corpus and of the CSV itself. The CSV can be
regenerated, reordered or hand-edited and nothing would notice; the manifest is what
makes that detectable. `--assignment-salt` reshuffles arm B and the subset on
purpose and is recorded; empty reproduces every mapping frozen so far byte-for-byte.

The subset is apportioned by largest remainder over stratum × trap/control/
difficulty, not by stratum alone. Arm T is the only place a false positive can be
observed, so a subset that misses the traps or the label-free items measures nothing
about either — and finding nothing when there is nothing is a different measurement
from missing something, which recall cannot tell you.

Arm D is `|families| × |lenses|` runs per subset item, so both knobs cost. The
refuter costs nothing per item — on the 261-claim run below it fired on 16 claims,
about 6%. Adding critics also inflates "unique verified findings" by pure
arithmetic, so arm T grows with the council by default: §7's warning gets *worse*
with more reviewers, not better.

## Adjudication without hand-labeling

`score_lrhe.py` consumes `judge.jsonl` and `exec.jsonl`. Nothing produced either,
so in practice every claim got hand-labeled and the REFUTED branch was reachable
only from container execution. `judge_lrhe.py` closes both with the same plumbing.

```bash
# adjudication: two non-authoring families per claim
python3 judge_lrhe.py prompts --corpus corpus.jsonl --runs runs.jsonl \
    --claims claims.csv --families gpt claude gemini grok kimi glm --out jp.jsonl
python3 judge_lrhe.py ingest --prompts jp.jsonl --responses jr.jsonl \
    --out judge.jsonl --out-judgments judgments.jsonl --human-queue human_queue.jsonl \
    --tiebreak-out tb.jsonl --families gpt claude gemini grok kimi glm

# cold refutation: one cheap family, disputed P0/P1 only -> exec.jsonl -> REFUTED
python3 judge_lrhe.py refute --corpus corpus.jsonl --claims claims.csv \
    --judge judge.jsonl --refuter glm --out rp.jsonl
python3 judge_lrhe.py ingest-refutation --prompts rp.jsonl --responses rr.jsonl --out exec.jsonl

# the part that stays human, and only this part
python3 judge_lrhe.py calibrate --claims claims.csv --judge judge.jsonl --n 60 --out calib.csv
python3 judge_lrhe.py kappa --calibration calib.csv --judge judge.jsonl
```

Measured on a 261-claim run over this corpus:

| stage | claims reaching it |
|---|---|
| all claims | 261 |
| settled by the deterministic gates (§5.1) — no judge call | 188 (72%) |
| adjudicated by a 2-family panel | 73 |
| still split after a 3rd-family tiebreak | 4 |
| unresolved P0/P1 after cold refutation | 2 |
| **reaching a person** | **6, plus the fixed 60-claim calibration** |

The human is not removable — κ ≥ 0.70 against blind hand labels is the §8 gate, and
a panel calibrated against nothing measures nothing. What is removable is the other
~1,950 claims. Note that raw agreement flatters: 80% agreement on the run above is
κ = 0.649, which **fails** the gate.

Majority settles a *judging label* here, never a review finding. Nothing in
`judge_lrhe.py` promotes a claim because reviewers agreed — agreement is metadata,
not proof, and `unresolved` deliberately writes no exec record in either direction.

## Nothing leaves the machine without a rights decision

```bash
python3 snapshot_terms.py                     # freeze the provider terms, hash them
python3 snapshot_terms.py --offline           # re-verify without a network call

python3 check_data_rights.py --item-id S1-0001 \
    --classification public_corpus --route opencode-go \
    --policy-id opencode-go-2026-07-27        # stdout is the data_rights record
```

Snapshots land in `lrhe-data/terms/`, not here: a re-fetch returns a different
document, so the frozen bodies are accumulated evidence rather than replicable
output — and the harness has no business republishing two vendors' legal text.

`check_data_rights.py` runs before a provider request is assembled and exits 0
allow, 10 deny, 20 unresolved. Deny and unresolved both stop egress; they stay
distinct because a prohibition and missing evidence need different remediation.

Two distinctions carry the design. **Gating facts are not downstream-use facts:**
whether this classification may travel this route is a gate, while whether the raw
response may later be exported or used to train a router is a restriction recorded
on the record. Conflate them and `raw_output_capture_status: contract_pending`
blocks a public benchmark item, which pressures whoever maintains the registry into
writing `allowed` instead — asserting a permission nobody granted. **Demanded
controls are not observed controls:** a policy's `requiredControls` says what must
be true of the account, so re-reading that same file to confirm it checks the policy
against itself. Claude's model-improvement setting comes from
`--model-improvement-enabled`, and its absence on that route is `unresolved`.

Customer, third-party confidential, personal, secret and unclassified inputs are
denied on every route. Carrythrough-owned internal material needs explicit per-item
authorization; only the public corpus is self-authorizing.

## Three things that are easy to get wrong

1. **Arm T is not optional.** Three statistically identical reviewers produce ~5 "unique verified
   findings" each and a ~6pp union-recall drop when removed. Without a same-family triplicate as
   the empirical null, the marginal-contribution numbers cannot distinguish real diversity from
   three coin flips. `analyze_lrhe.py` refuses to interpret them and says so.

2. **Do not build on SWE-bench Verified.** Roughly a third of its issues leak solution code into
   the issue text and >94% predate current model cutoffs. Use SWE-bench-Live. Verified is for
   validating harness plumbing only.

3. **The judge must be cross-family.** The output of this evaluation is a per-family comparison, so
   a single-family judge is disqualifying. Two non-authoring families per claim, disagreements to a
   human, and κ ≥ 0.70 against 60 hand-labeled claims before any headline number is trusted.

## Statistics contract

Unit of analysis is the labeled defect. Unit of resampling is the item. Never the other way round —
defects inside one PR are correlated, and defect-level binomial intervals overstate precision by
roughly √(cluster size). Recall is never averaged across strata: `human_review_comment` and
`fail_to_pass_test` labels do not mean the same thing.
