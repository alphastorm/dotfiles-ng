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

All 47 items are built, validated, scrubbed and dispatched as reviewer-safe
packets. `PROVENANCE.md` is the source audit — read it before trusting any count,
because several things `LRHE-PROTOCOL.md` §2 asserts about these datasets are not
true of the data as shipped, and three of them would have corrupted the result
silently rather than failing loudly.

| Stratum | Built | Target | Note |
|---|---|---|---|
| S1 `REVIEW_HUMAN` | 14 | 14 | — |
| S2 `PATCH_VERDICT` | 10 | 10 | — |
| S3 `VULN_POC` | 8 | 8 | 4 carry an upstream licence the detector could not classify; see below |
| S4 `FP_TRAP` | 12 | 12 | — |
| S5 `NULL` | 3 | 3 | — |

85 labels, 37 critical, 5 executable, 12 traps, 11 controls.

All 47 items authorize all five provider vendors, so every family can review every
item and no stratum has a coverage hole.

Four S3 items — ImageMagick, KDE kimageformats, libheif, freetype2 — reached that
state by explicit operator decision rather than automatically. Their upstream
licence is real but the host's detector could not classify it (`NOASSERTION` or
`UNDECLARED`), and `_allowlist()` in `sources.py` withholds every provider in that
case rather than quietly defaulting to open. Two of the four are copyleft, which
governs distribution of derivative works and not reading source for review; none
carries a no-machine-processing term, and `check_packet_gates.py` finds no explicit
restriction on any of them. They are still named as warnings on every audit run,
because an unresolved licence is a fact worth seeing even once it has been decided.

**A rebuild narrows them again.** `_allowlist()` is deliberately conservative, so
regenerating the corpus returns those four to an empty allowlist. Re-run
`check_packet_gates.py grant` afterwards; the audit will tell you if you forgot.

## Quick start

```bash
uv venv --python 3.13 .venv                        # pinned; see requirements.txt
VIRTUAL_ENV=.venv uv pip install -r requirements.txt

python3 build_corpus.py plan                       # sampling plan, no network
python3 power_lrhe.py --sweep-items 16,24,32,40,56 --effect 0.8 --reps 300

# Stable skill-development tiers resolve tools and tests from this directory.
./review_checks.py quick  # three-file inner loop
./review_checks.py full   # quick + Ruff + clean-HOME public CI contract

# Citable pre-freeze/pre-push proof, bound to exact artifact and file digests.
./review_checks.py full --subject-record review-record.json --receipt full-proof.json

# Any other exact proof command uses the same before/after subject binding.
./make_receipt.py --subject-record review-record.json --receipt proof.json \
    --cwd /path/to/repository -- command arg1 arg2
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

### A selector is an alias, not a model

The lock pins the toolchain, both repository commits, the corpus and its answer key,
the assignment manifest and every terms snapshot. It pins **nothing about the
weights**. If OpenCode swaps the checkpoint behind `opencode-go/kimi-k3` partway
through the 105-review floor matrix, every family comparison spanning the swap
compares two models under one name — and `freeze_lock.py verify` reports no drift,
because none of the things it hashes moved. That is the drifted-trial failure the
lock exists to prevent, in the one dimension it does not cover.

`reviewer.provider_fingerprint` is required on every run record and carries whatever
the provider exposes that identifies the checkpoint. `analyze_lrhe.py` refuses to
pool two fingerprints under one selector, failing closed exactly as it does on a
mixed panel. `freeze_lock.py` records each enabled lane's selector under
`model_pins`, with `fingerprint: null`.

**No fingerprint has been observed in the retained session record.** That record keeps
`responseId`, `runtimeRequestId`, `logicalTurnId`, `provider`, `model` and usage, and
carries no `system_fingerprint` field and no raw headers — so this is `null` on every
OpenCode run today, and the control is a detector for later plus an unmeasured risk on
the record. What that does **not** establish is that the provider sends nothing:
`provider_fingerprint_observation` is `not_observed`, never `observed_absent`, because
nothing here can distinguish a provider that emits no checkpoint identifier from a
persistence path that drops one. Earlier prose in this file read "OpenCode exposes no
fingerprint"; that was a conclusion about the provider drawn from a gap in our own
retention. Settling it needs authenticated raw headers compared against the record,
which needs a live transport this harness deliberately does not have. It is
deliberately not a solved problem: a field that reads `null` is a stated gap, where an
absent field is one nobody has noticed.

The guard's own first version refused every analysis it touched. An unpopulated
column reads back as `NaN`, `NaN` never equals itself, and a set of them has one
member per row — so "no fingerprint anywhere" looked like one checkpoint per run.
A detector that fires on absence is worse than the gap it closes.

### Every item field is classified, or the suite fails

`item.schema.json` is closed (`additionalProperties: false`) and every property in it
appears in exactly one of `_DISPATCH_KEYS` or `_WITHHELD_KEYS`. The projection was
already an allowlist, so an unclassified field was withheld by default — the safe
direction, and indistinguishable from having thought about it. Adding a property now
fails `test_consistency.py` until someone says which it is, which is the only moment
anyone is thinking about it. `_WITHHELD_KEYS` carries the reason per group: provenance
one search from the upstream fix, the answer key, the harness's own verdict, sampling
bookkeeping that is a retrieval hint and never evidence.


## Live dispatch and evaluation are separate

`qualification.yml` `liveDispatch` is the sole authoritative live panel. Its
`leadFamily` records the accountable GPT lead and is disjoint from both live reviewer
roles. Resolve the current initial or targeted-refuter roster with `qualification.py`;
do not duplicate family names or counts in public tests, packets, or policy prose. This
public package never grants live membership.

LRHE experiments remain asymmetric evaluation designs:

| Evaluation role | Family | Runs on | Cost driver |
|---|---|---|---|
| author / integrator | `gpt` | arm A, synthesis | per review |
| critic | experiment-defined | arms B, C, D, probe, floor arms | per evaluation item |
| refuter | experiment-defined | arm R | per disputed claim, not per review |

Conflating experiment membership with live dispatch turns evaluation lanes into
unapproved reviewers and makes independent roles look like votes. `panels.yaml`
therefore owns experiments only; `qualification.yml` owns live dispatch.

`qualification.py` fails closed unless schema version 3, role membership,
dispatch/evaluation flags, canary results, read-only proof, agents, and selectors
are internally consistent. `initial` returns only configured primary critics;
`targeted-refuter` returns only the separately configured refutation pool.

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

## The runner is the gate, not a caller of one

```bash
python3 run_review.py plan     --item-id S1-... --family claude --lens architecture
python3 run_review.py dispatch --item-id S1-... --family claude --transport stub --out runs.jsonl
```

`prepare()` returns either a `Refusal` or an `AuthorizedRequest`; `dispatch()`
accepts nothing but an `AuthorizedRequest`. There is no argument that makes
`dispatch()` skip a check and no call order that reaches a provider without a
rights record in hand. Python cannot enforce that at the type level, so
`dispatch()` re-validates the record it was handed against
`data-rights.schema.json` before touching a transport — a hand-built request with
a plausible-looking record still dies at the last step, and there is a test that
builds exactly that.

Gates run cheapest-first: lane qualified in `qualification.yml` → item and packet
exist → `check_packet_gates` on what would actually be transmitted →
`check_data_rights` for the route. The rights guard is invoked as the CLI it
already is, so there is one implementation of that decision and the runner cannot
reach around it.

Transports are explicit and default to refusing:

| | |
|---|---|
| `none` | raises on any send. The default, so a dry run cannot leak by forgetting a flag |
| `stub` | deterministic canned response; exercises assembly, run-record emission and provenance with no socket in the path |
| `live` | **not implemented.** No credential is configured and provider calls are held. A half-written live path is the one that gets called by mistake |

Every test that asserts a refusal spies on the transport table and asserts zero
calls. A gate that refuses *after* sending is not a gate.

### So how does a reviewer reach a model

```bash
python3 run_review.py prompts --assignments smoke-manifest.jsonl --out rp.jsonl
# ... one agent invocation per row, fresh session, no peer output ...
python3 run_review.py ingest --prompts rp.jsonl --responses rr.jsonl --out runs.jsonl
```

Not through a socket in this repository. Through the OMP agent named by `agent:`
in `qualification.yml` — the one the council dispatches, and the one the canary
qualified the floor lanes with. `prompts` runs every gate above and emits the
packet as text; `ingest` turns the replies into run records. Nothing in that path
opens a connection, so the table stays as it is.

`ingest` builds its request from a file, which makes the file an attack surface,
so it re-runs `_require_allowed_rights` — the same check `dispatch()` makes on a
hand-built `AuthorizedRequest`, now shared by both. A prompts row whose rights
record has been edited to `deny` produces no run record, and there is a test that
edits one.

`schema_valid` and `telemetry_complete` have no defaults. Section 5.5 is about
absent telemetry reading as success, and `response.get("schema_valid", True)` is
precisely that: a transport unable to say whether the reply validated would emit a
record indistinguishable from a clean one. Both are now required keys, so silence
is a `KeyError` at build time rather than a green field.

### The lens was recorded on every run and transmitted on none

It lived as one hardcoded `Primary lens:` line inside `review-claude`,
`review-gemini` and `review-grok`, and not at all in the four floor agents. Family
determined agent determined lens, one to one — while `lens_sets()` counterbalances
families over lenses, arm D is documented as the only arm where lens varies, and
`power_lrhe.py` tests a family × lens interaction. None of that could be
delivered. Arm D's 216 rows would have carried four lens labels over reviewers
that each received exactly one, and nothing would have failed: the field was
written to every record and applied to nothing.

The text is data in `panels.yaml` now, verbatim from the three agents it came
from, and `render_packet()` transmits it. That is also what lets one agent serve
any lens it is given, which is the premise the whole rotation rests on. A lens an
experiment declares but `panels.yaml` has no text for is refused rather than
rendered as nothing — rendering it as nothing is how this went unnoticed. `floor`
is declared with empty text on purpose: the floor is the *absence* of a lens, and
a missing key would read as an oversight.

### The seven-item smoke pass, 2026-07-28

2×S1 + 2×S2 + 2×S3 from the frozen calibration subset plus the 1×S5 null, Kimi K3,
lens `architecture` for the six and `floor` for the null, arm `smoke`. Scored as
two experiments because it is two panels, and a mean over both describes neither.
All seven checks pass; the record is in the private package under `lrhe-data/smoke/`.

| | |
|---|---|
| contract parse | 17/17 claims, rate 1.0 |
| anchor resolution | **fired** — 1 of 17 FABRICATED, no judge, no person |
| model pinning | `identity_verified` 7/7, no fallback |
| tool allowlist | 0 violations, no write, no subagent |
| ARVO build/PoC | 4 containers, both pairs reproduce as recorded |
| S5 empty evidence | 0 claims |
| quota / Zen | `product_route: opencode-go` 7/7, no overflow |

On `S3-0019a556` Kimi cited `pdf-font.c:299` against a packet whose `repo_files` is
`["thirdparty/freetype"]`. The claim parses cleanly and reads well. §5.1 refused it
deterministically, which is the gate doing on real output exactly what it exists
for — and the reason "PLAUSIBLE" in that report means parsed and anchored, not
correct. No judge ran; `judge_coverage` is 0.

The quota check needed a fix to be answerable at all. `PRODUCT_ROUTE` omits the
OpenCode route deliberately, because a request can land on the Go allowance or
spill to Zen and only telemetry knows which — so every OpenCode run recorded
`unknown`, permanently, including the check that was supposed to establish it. The
agent lane does know: the session record names the provider that answered. It is
passed through and validated against the enum. `billing_route` stays `unknown`,
because which allowance line was billed is a step past what the route tells you.

One expectation of ours was wrong and the harness was right. A `superseded_fix`
item whose fixed image comes back clean looks like an inverted label; it is not.
`cmd_arvo_build` draws gold from the *clean* sweeps and establishes incompleteness
from ARVO's own correction history, because 124 faithful paired runs produced zero
fixed images that still crash. The incomplete patch is `review_commit`, which is
what the reviewer reviews; the `-fix` image is the later replacement. At the
container level `superseded_fix` and `correct_fix_control` are indistinguishable,
by design.

Writing this runner is what surfaced that **all three enabled reviewers routed
through providers with no data-rights policy at all** — qualified, in use, and
ungoverned, because `provider-policies.yaml` had only been written for the two
new routes. Nothing else asked the question, so nothing else could notice.

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

## The ledger is the asset; the router is a build product

LRHE measures the council against a fixed public corpus. `shadow_ledger.py`
measures the same council against real work, and after thirty-odd reviews it is a
routing dataset nobody outside can reconstruct at any price. It is empty today,
which is exactly why the schemas exist now: a field not captured at the time of the
first live review is not recoverable later.

```bash
python3 shadow_ledger.py review   --runs runs.jsonl --out "$D/ledger/reviews.jsonl" ...
python3 shadow_ledger.py ingest   --runs runs.jsonl --out "$D/ledger/findings.jsonl"
python3 shadow_ledger.py outcomes --findings "$D/ledger/findings.jsonl" --repo /path/to/repo

python3 router_dataset.py build   --reviews "$D/ledger/reviews.jsonl" \
    --findings "$D/ledger/findings.jsonl" --runs runs.jsonl
python3 router_dataset.py verify        # every lineage reference still resolves
python3 router_dataset.py delete-source --review-id R-0007
```

`ledger/` is raw, immutable and committed. `router/` is derived, versioned and
gitignored — rebuilt whenever the feature set or the label definition changes.
Pooling two `dataset_version`s trains on labels that mean different things, and
nothing in the file would say so.

Three invariants are in the schemas rather than in anyone's memory:

**Features freeze before dispatch.** `review.schema.json` splits `features`
(computable from the diff at `epoch_commit`, frozen at `features_frozen_at`) from
`outcomes` (accrues afterwards). Anything knowable only after the review is a
label. Put it in `features` and you train the router to predict the present from
the future — which looks superb offline and is worthless live, so the builder
refuses any example whose features were frozen after its reviewer started.

**Labels have an age.** `label_maturity_days` records how far forward history was
actually examined. Escapes and rollbacks only appear with time, so a review
labelled the same day reports zero escapes by construction. Train across mixed
maturity without conditioning on it and the router learns that recent reviews are
safer than old ones — an artefact of when they were labelled and nothing else. An
unobserved escape is recorded as null, never as false.

**The router may not fire a critic.** `decision_authority` is a field, not a
comment, because the constraint has to survive whoever remembers it. Shadow
prediction, advisory recommendations, additive reviewer selection, refuter
selection and cost forecasting are permitted immediately. `subtractive` —
dropping a required critic from a critical review on a prediction — is not
emitted and must be refused, until LRHE and live outcomes show it does not
materially reduce critical recall.

The unit is the (review, family, lens) cell. Aggregating to the review loses the
per-family signal that is the whole point; splitting to the finding conditions
every example on detection, which is the same bug that makes a claims-only recall
meaningless.

## Before the first paid request

```bash
python3 preflight.py            # every checkable gate, spends nothing
python3 preflight.py --slow     # same, plus the full suite
```

Exit 0 means every automatic gate holds, and the remaining manual steps print in
order. Exit 10 means one failed. Nothing in it contacts a provider: the steps that
would cost money are *named*, not run, because a preflight that can spend is not a
preflight.

It exists because three steps are order-sensitive and expensive to get wrong, and
that ordering was living in a chat log. `runs/LOCK.json` is frozen **last**. A lock
is a claim about the starting state of a result set, so it has to name the toolchain
that runs — hence after the OMP upgrade, since one frozen under the old version
records a toolchain that never produced anything — and the tree that produced the
runs. Qualification edits `qualification.yml` and rewrites the terms snapshots,
both tracked and hashed into the lock, so a lock taken before the canaries reports
`drift: lock_inputs.private_repo.commit` before the first measured run. A lane
stays `evaluationEnabled: false` until its credential exists and its canary passes;
live critical-review membership remains separately owned by `liveDispatch`.

`freeze_lock.py freeze` refuses a dirty tree outright, with `--allow-dirty` to
record the dirty state deliberately. That refusal lives at the point of effect
rather than in preflight: the freeze is the last step, so the three before it run
with the tree legitimately dirty, and a gate that printed red through all of them
is one you would learn to skip. Preflight reports the tree state instead.

The gates it owns that no test covers: reviewer definitions parse and declare
`thinkingLevel` rather than the `thinking-level` that is silently ignored; their
output schemas compile, since a reviewer whose schema fails returns free text and
free text cannot be scored against a label; every reviewer in `qualification.yml`
resolves to an agent file that is present and not a dangling stow symlink; and an
evaluation-enabled lane is checked against the evidence recorded for it rather
than a hardcoded list of names.

It also resolves every selector against OMP's model cache — provider, model, and
any `:effort` suffix. `qualification.yml` cannot assert that its own selectors
exist, and the alternative to checking is spending a request to find out. Note
the cache keys scoped providers as `<provider>:models-v1:<hash>`, a discriminator
rather than part of the selector, so `opencode-go` appears hashed while
`anthropic` does not; a resolver comparing raw keys finds no OpenCode lane at all.
That is why "add the credential" is no longer a manual step — an unauthenticated
provider has no cached catalogue, and the gate says so by name.

## The canaries decide whether a lane may be dispatched

```bash
python3 canary.py selftest                     # graders vs replies built to fail them
python3 canary.py run --transport stub         # every lane, no egress
python3 canary.py prompts --out cp.jsonl       # ... answered through the agent lane ...
python3 canary.py grade --prompts cp.jsonl --responses cr.jsonl
```

`qualification.yml` records `schemaValid`, `readOnlyBoundary` and `providerCanary`
per lane, and nothing produced any of them. Three probes do, each built so the
right answer is known before the reply arrives:

| probe | asks | needs a real reply |
|---|---|---|
| `structured_output` | does the reply validate against the reviewer's own output schema? | no |
| `anchor_lookup` | are the citations real, and are there any? | no |
| `empty_abstention` | given nothing to find, does it return nothing? | yes |

The split matters. The first two judge the *shape* of a reply and are meaningful
against any reply, canned included. The third asks what the model chose to say,
and grading that against a fixture measures the fixture's opinion — so on a
non-egress transport it is skipped with that stated, not failed into a permanent
red that teaches you to ignore the exit code.

**A stub run is evidence about the graders, not about a lane.** It records
`verdict: apparatus` and refuses to emit a passed provider canary; nothing from it
belongs in `qualification.yml`. Running it first is still worth it, because
otherwise the first paid request is also the first execution of the code deciding
whether the answer was any good. `selftest` is the other half: every grader must
reject the reply shipped to fail it.

It earned itself immediately. `stub_transport` emitted `R01|P2|...` while every
reviewer's output schema requires `^R[1-9][0-9]*` — so the canned reply that
exercises the whole path locally, and the fixtures and simulator built the same
way, were shaped like a reply no reviewer is permitted to return. `score_lrhe.py`
parses evidence leniently, so nothing downstream ever objected. Fixed in all
three, and `test_consistency.py` now holds synthetic evidence answerable to the
schema that governs the real thing.

The canary talks to the transport table directly, because `prepare()` refuses an
unqualified lane and every lane needing a canary is one. The cost of that
shortcut is that a live transport would otherwise make `canary.py` an ungated way
to reach it, so it accepts only transports known not to leave the machine and
refuses anything else by name. Pointing a probe at a provider is an edit to that
set, made deliberately.

### Which is why `run` cannot qualify anything

Its verdict is always `apparatus`. The path to a model is not a socket in this
repository — it is the OMP reviewer agent named by `agent:`, the same one the
council dispatches. So the split is the one `judge_lrhe.py` already uses: emit
the prompts, answer them by the means that exists, grade what comes back. The
boundary is unmoved, no command here opens a connection, and a real reply can
finally answer the probe a canned one cannot. What `grade` cannot do is witness
the request, so every record says `request_observed: false` and carries the
digest of the reply file it read.

`render_packet()` lives in `run_review.py` rather than here. Two lanes reviewing
one item must read one document; if each caller renders its own, the comparison
between them measures the renderer. It states `repo_files` as the closed set of
citable anchors, because that is the rule `anchor_lookup` grades against and a
reviewer held to a rule it was never given reads as a family that fabricates.

**The four OpenCode floor lanes, 2026-07-28.** Kimi K3, GLM 5.2 and DeepSeek
V4 Pro passed 3/3 and are enabled. MiniMax M3 passed 0/3 and stays held, on
`failureClass: repeated_schema_noncompliance`: every reply wraps its JSON arrays
as `{"item": [...]}` where the schema requires an array, so nothing it returns
parses as the evidence contract. Served identity was read out of the session
transcript rather than taken from the reply, so `quotaPath` records the route
that answered: `opencode-go` for all four, no Zen overflow.

One non-scoring diagnostic located that wrapper, without a provider call and
without touching the parser — `lrhe-data/diagnostics/`. The session records keep
`partialArgs`, the raw streamed tool-call text from before the harness parses it,
and MiniMax's very first payload already reads `"evidence": {"item": [...]}`.
There is no XML in the wire text for an extraction layer to have converted, which
also disposes of the model's own explanation — its thinking blames the harness for
nesting `<item>` tags it never emitted. The other three lanes ran the same agent
definition, schema, prompt and extraction path and returned a conformant call on
the first attempt. The model is the only variable that differs.

Two things that result does *not* say. It is not a review-quality finding: MiniMax
found the same `None == None` authorization bypass the passing lanes found, at the
same severity, citing real packet paths. And the fix is not a parser that accepts
the wrapper — that would relax a contract the other lanes meet unaided, and the
floor comparison would then be measuring the parser. Worth knowing for later: OMP
rejected and re-prompted three times per probe, and MiniMax *degraded* under
retry, moving the malformation up into `summary`; the fourth attempt is the one
permissive mode accepted and `grade` scored.

Two graders were wrong, and the four lanes found both:

- `anchor_lookup` checked the citations it was given and passed vacuously when
  there were none. With `empty_abstention` firing only on a reply that *found*
  something, a lane that returned nothing to everything passed all three probes —
  the worst reviewer imaginable, qualified. Its packet plants one defect and the
  goal line names it, so silence is now non-compliance.
- Shape was judged only on the probe that asks about it. MiniMax's malformed
  anchor reply had no top-level `evidence` for the anchor grader to inspect, so
  it graded clean: a schema violation invisible to every probe except the one it
  was not asked. Every reply is now judged for shape, whichever probe it answers.

## The test suite

```bash
python3 -m pytest -q            # 161 tests, ~75s
ruff check .                    # rule set pinned in ruff.toml, not inherited
```

Every test here defends a failure that produced *plausible output*. None of them
check that a function returns what it returns; the harness is only worth running
if it fails loudly, so the suite is a list of the ways it has been caught failing
quietly.

| file | what it defends |
|---|---|
| `test_invariants.py` | The scoring and analysis path. Replicates surviving both stages, refusal on mixed panels or collapsed replicates, cross-family judge enforcement, apportionment across strata. Most of these exist because the thing they check was once broken: the simulator emitted only arm D, and `diversity_vs_null` compared a union of k council runs against a single arm-T run and reported +0.266 CI[0.099, 0.413] for what was actually −0.097 CI[−0.270, 0.055]. |
| `test_corpus_tools.py` | Corpus construction and the six packet gates. The gates caught all ten `S2_PATCH_VERDICT` goals sharing one string — a retrieval test sitting at a 100% ceiling, which would have read as a strong result. |
| `test_ledger.py` | Shadow-ledger reads against schema v2's nested shape, freeze-lock round-trip and drift detection. |
| `test_runner.py` | The pre-egress gate. Every refusal test spies the transport table and asserts *zero* calls, because a gate that refuses after dispatching has not refused. |
| `test_consistency.py` | Cross-file agreement — see below. |

`test_consistency.py` holds the invariants no single module owns, which is why no
module's tests caught them. It asserts that every `providerRoute` in `panels.yaml`
has a policy behind it in `provider-policies.yaml`; that every `termsSnapshotId`
resolves to a real snapshot and none is still a placeholder; that a risk-accepted
policy names a principal and hashes the record it rests on; that no policy permits
training a competing model; that an arm-T `nullFamily` is actually in its own
panel; that the lock file `preflight.py` inspects is the one `freeze_lock.py`
actually writes; and that every requirement is pinned.

The first of those is not hypothetical. All three enabled reviewers routed through
`anthropic-subscription`, `google-antigravity` and `xai-oauth` while
`provider-policies.yaml` knew about none of them — qualified, in use, and
ungoverned. Both files were internally consistent. Nothing surfaced it until a
runner tried to assemble a request.

**Sixteen tests skip without the private package.** Fourteen read the corpus and
two read the reviewer agent definitions; both live outside this repository and
both skip cleanly when absent — which is what happens in CI, because the corpus
carries the answer key and must never reach a public runner. The remaining 113
need nothing but this repository. `.github/workflows/lrhe.yml` runs lint, the
cross-file invariants, the suite, and a final assertion that no `live` transport
has appeared. The skips there are correct; do not "fix" them by checking the
corpus out.

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
