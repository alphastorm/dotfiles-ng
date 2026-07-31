# Lens-Rotation Historical Evaluation — public-corpus protocol

**Status:** pre-registration draft, v1. Unblocks step 10 of the critical-review council plan
without transmitting any private repository history.

**The substitution:** the original design called for 24–40 of *your* consequential changes with
known outcomes. That is blocked on curation effort and data authorization. Public software-engineering
corpora supply the same thing and, for the strata that matter most, supply it *better* — the outcome
labels are executable, adjudicated by someone other than you, and already published, so the
comparison is auditable by a third party. You lose calibration to your own invariants. You gain
ground truth that cannot be talked out of its verdict.

Run this first. Run a small private confirmation set later, once the council is qualified and the
authorization question is worth reopening.

---

## 1. What this evaluation can and cannot qualify

Split the promotion decision in two, because a public corpus only speaks to one half.

| Transferable — settle it here | Non-transferable — needs your repo, later |
|---|---|
| Does adding cross-family critics raise verified critical recall? | Do critics respect *your* named invariants? |
| Which family contributes findings the others miss? | Does the packet's `rollback_contract` field carry enough signal? |
| Does the specialized lens beat the general floor prompt? | Are findings actionable in your codebase's idiom? |
| False-positive burden, fabrication rate, trap susceptibility | Whether your team's review taste matches the judge's |
| Contract compliance, anchoring discipline, schema validity | Coupling to your CI and deployment surfaces |
| Per-family cost, latency, quota path | |

Set the promotion gates from the left column only. The right column is what the private confirmation
set is for, and it needs about six items, not forty.

---

## 2. Corpus: five strata, 47 items

Each stratum pairs a lens with a *label semantics* and an *adjudication mechanism*. Mixing label
types is deliberate — the council's decision rules distinguish "a human flagged it" from "a test
proves it," and the corpus has to contain both or that distinction goes untested.

| # | Stratum | n | Source | Label means | Adjudicated by | Primary lens exercised |
|---|---|---|---|---|---|---|
| S1 | `REVIEW_HUMAN` | 14 | SWE-PRBench | a human expert flagged this in review | judge panel + localization | all three |
| S2 | `PATCH_VERDICT` | 10 | SWE-bench-Live + SWE-bench/experiments | hidden test suite fails | **execution** | architecture, adversarial |
| S3 | `VULN_POC` | 8 | ARVO | PoC still crashes | **execution** | adversarial |
| S4 | `FP_TRAP` | 12 | disclosed-invalid H1 reports; ARVO false-patch labels | the finding is *not real* | published maintainer verdict | adversarial |
| S5 | `NULL` | 3 | clean merged PRs, no revert/hotfix ≥90d | nothing to find | by construction | all three |

### S1 · `REVIEW_HUMAN` — the closest thing to your actual task

[SWE-PRBench](https://huggingface.co/datasets/foundry-ai/swe-prbench) is 350 merged pull requests
with ground truth consisting of review comments written by human engineers during the real review
process, collected after the fact from GitHub's review API — not generated, not synthesised. It also
ships a difficulty taxonomy that maps almost exactly onto your three lenses:

- **Type1 Direct** — issue visible in the changed lines. Your common floor.
- **Type2 Contextual** — issue requires relating changed code to surrounding *unchanged* code in the
  same file. This is your architecture/semantic-coherence lens.
- **Type3 Latent** — issue lives in files that import or depend on the changed files. This is your
  whole-repository lens, precisely as specified.

Sample **4 Type1 / 5 Type2 / 5 Type3**, over-weighting Type2 and Type3 relative to their 21% and 12%
dataset prevalence. Type1 discriminates poorly between lenses; it is there to keep the floor honest.

Its published baseline is also your comparison point: eight frontier models detected only 15–31% of
human-flagged issues, with fabrication rates from 0.19 to 0.42. Note the model set was Claude Haiku
4.5, Claude Sonnet 4.6, GPT-4o, GPT-4o-mini, DeepSeek V3, two Mistrals and Llama 3.3 — no Gemini, no
Grok, no current-generation GPT. Your run contributes genuinely new cells, which is a nice side
effect but also means you cannot lean on their numbers as a prior for your families.

Reuse their published `RUBRIC.md` and their CONFIRMED / PLAUSIBLE / FABRICATED classification rather
than inventing your own. One deviation, in §5.

### S2 · `PATCH_VERDICT` — reviewing model-authored work, with a test as the referee

This is the stratum that matches your deployment shape: GPT authors, the council reviews. Construct it
by pairing an issue instance with a *real agent-authored candidate patch* whose harness verdict is
already known.

The `SWE-bench/experiments` repository hosts leaderboard submissions including per-instance patches,
evaluation logs and trajectories; published analyses have pulled 9,374 trajectories from 19 agents
across the 500 Verified tasks as a near-complete agent × task matrix. The patch-verification framing
has precedent: one group assembled 1,340 patches from 335 issues at 49.9% resolved by deliberately
picking submissions with moderate accuracy to keep labels balanced, and dropped empty or
comment-only patches so models could not shortcut on surface features. Do the same.

**Do not build S2 on SWE-bench Verified.** Roughly a third of Verified issues have solution code
appearing near-verbatim in the issue description, and over 94% of the instances and their
ground-truth PRs predate the knowledge cutoffs of current frontier models. Verified is for validating
your harness plumbing, not for measuring anything. Build S2 on **SWE-bench-Live**, which adds 50
newly verified instances monthly and whose reported resolve rates run far below the static benchmark
(19.25% vs 43.20% for the same systems) — a gap that is itself the contamination signal.

Composition: **5 items with a known-broken candidate patch** (label = the specific `FAIL_TO_PASS`
test that fails) and **5 items with a known-passing candidate patch** as controls. Also be aware that
the SWE-bench harness runs only the test files touched by the PR, which has been estimated to
overstate pass rates by 4–7 points absolute; treat "passing" controls as *not-known-broken* rather
than clean, and do not score a claim as fabricated merely because it flags something on a control.

### S3 · `VULN_POC` — the security lens with an executable referee

[ARVO](https://github.com/n132/ARVO) reproduces over 6,100 real memory-safety vulnerabilities across
311 C/C++ projects from OSS-Fuzz, each with a triggering input, the canonical developer patch, and
the ability to rebuild and run at both vulnerable and patched revisions. Reproduction succeeds for
about 81% of entries with roughly 89% patch-location accuracy.

The high-value subset for your purposes is the **falsely patched** one: ARVO surfaced 300-plus cases
where a real developer patch was recorded as a fix but the PoC still crashes. That is an *incomplete
fix authored by a competent human*, with a machine-checkable verdict — exactly what your security
lens claims to catch and exactly the failure mode that a plausibility-optimized reviewer waves
through.

Composition: **5 incomplete-or-unfixed** items, **3 correctly-fixed** controls.

### S4 · `FP_TRAP` — the stratum your design exists for

The refute-or-promote work you are building on reports 80-plus agents, including dedicated
adversarial reviewers, unanimously endorsing a Bleichenbacher padding oracle in OpenSSL's CMS module
that did not exist. That is the failure your architecture is meant to survive, and no other stratum
tests it.

Build each trap item as a review packet that *asserts* a plausible-but-false finding — framed as a
prior reviewer's concern, or as a `known_open_questions` entry — over real source. Score whether the
reviewer promotes it to P0/P1.

Two label sources with published verdicts:

- **Disclosed-invalid bug bounty reports.** A public corpus of 9,942 disclosed HackerOne reports
  including 1,400 invalid ones, with a taxonomy of rejection reasons, is available from
  [arXiv:2511.18608](https://arxiv.org/abs/2511.18608). Its central finding is the one you need to
  design against: GPT-5, DeepSeek and a fine-tuned RoBERTa all achieved strong overall accuracy while
  consistently failing to detect the invalid cases, showing a clear tendency to *over-accept*.
- **ARVO false-patch labels, inverted.** Where ARVO shows a patch *did* close the crash, a seeded
  claim that it did not is a trap with a machine-checkable answer.

**This stratum is 12 items, not the 5 a proportional split would give it.** The simulation in §7
shows that 5 trap items × 3 lens-sets
gives about 15 runs per family, and at a base bait rate near 0.35 that yields a standard error around
0.12 — enough to establish that the council takes bait, nowhere near enough to rank families on it.
Trap packets need no build environment, so the marginal cost is close to zero.

### S5 · `NULL` — three items where the correct output is silence

Merged PRs with no revert and no follow-up fix commit within 90 days. Any P0/P1 here is a false
positive with no interpretive wiggle room. Keep them unlabeled and interleaved so they are
indistinguishable from real items at dispatch time. Note that the reviewer contract already permits
an empty `evidence` list; S5 is how you find out whether that permission is ever exercised.

### S6 · optional — reverted commits

Real `Revert "..."` commits with a stated reason, plus fix-of-fix pairs, are the only public proxy
for design-level defects that were shipped and hurt. Attractive, but the label is weak: it says
*that* a change was bad, not *which* named invariant the reviewer had to identify, so scoring needs
a manual reason-match rubric and inter-rater agreement of its own. Keep it out of the core 40 and add
it only if S1 Type3 turns out to under-represent architectural defects.

---

## 3. Contamination control

Contamination is the dominant threat here, and it is worse for your question than for a normal
benchmark: family knowledge cutoffs differ, so leakage is **asymmetric across families**, which
directly biases the per-family marginal-contribution estimate that the whole evaluation exists to
produce. Four controls, in order of value.

**1. Recall probe — do this, it is cheap and decisive.** For every item × family, run a `probe`
condition: issue or PR title and description only, no repository access, no diff, no tools. Ask for
the file and line of the defect. If a family names it, mark that `(item, family)` cell contaminated
and drop it from the primary analysis. Cost: 40 × 3 = 120 short single-turn calls. `analyze_lrhe.py`
takes the results as `--probe probe.csv` and reports a per-family contamination rate. Report that
rate in the results whatever it is; a family with 25% contamination and a family with 5% are not
comparable on recall.

**2. Date gate.** For any item you want clean, require the fix or merge date to be later than the
*latest* cutoff among all four participating families. Gating on each family's own cutoff
individually reintroduces the asymmetry you were trying to remove. Practically: SWE-bench-Live's
full split for S2, ARVO's newest OSS-Fuzz entries for S3, and a fresh six-month harvest for S1 if
SWE-PRBench's freeze date is too old by the time you run.

**3. Surface scrubbing.** Strip issue numbers, URLs, commit SHAs, CVE identifiers and `Revert `
prefixes; renumber items; paraphrase problem statements with local Qwen. This reduces string-level
recall, not conceptual recall, and the paraphrase can itself degrade the task — so keep 6 items in
scrubbed and unscrubbed form and measure the scrub's own effect before trusting it.

**4. Prefer the contamination-resistant tiers.** SWE-PRBench penalizes high-star repositories,
over-samples GPL-licensed ones and excludes PRs above a 0.85 embedding-similarity threshold against
known benchmark repos. Its Type3 tier is partially contamination-resistant by construction: cross-file
dependency reasoning cannot be resolved by retrieving a memorised patch. Weight toward Type3
accordingly — which the S1 sampling plan already does.

A note on data egress: this corpus is public open-source code, which is a far easier authorization
case than your repository, but it is not a null one. Populate the packet's existing
`provider_data_allowlist` field per item, and be aware SWE-PRBench and SWE-bench Pro deliberately
over-sample GPL repositories.

---

## 4. Arms and the rotation design

| Arm | Configuration | Runs on |
|---|---|---|
| **A** | GPT-only strong review (your current `task-strong` lane) | 40 items |
| **B** | GPT + one cross-family critic, critic rotated across items | 40 items |
| **C** | Three cross-family critics, **general floor prompt only, no lens** | 40 items |
| **D** | Three cross-family critics, **rotating specialized lenses** | 24-item subset |
| **T** | **One family run three times independently** | 24-item subset |

Arms A→C answer "do critics help." C→D answers "does the specialization help beyond the family."
**Arm T is the one that determines whether any of it means anything**, and it was not in the original
plan. §7 explains why.

### The Latin square

```
Set 1: Claude=architecture   Gemini=whole_repo    Grok=adversarial
Set 2: Claude=adversarial    Gemini=architecture  Grok=whole_repo
Set 3: Claude=whole_repo     Gemini=adversarial   Grok=architecture
```

**Run all three sets on every arm-D item.** The within-item, fully counterbalanced version is the
only one that can separate the family effect from the lens effect, which is the stated purpose. A
between-item variant — one set per item — costs a third as much and cannot: with 8 items per set the
lens contrast is not merely underpowered, it is uninterpretable.

### Optional cross-cut: context regime

Worth two extra cells on a 10-item subset, because a published result contradicts an assumption in
the council design. SWE-PRBench found that all eight evaluated models degraded *monotonically* as
context got richer, with contextual-issue detection **collapsing** — Sonnet 0.22 → 0.10, DeepSeek
0.20 → 0.10 — when execution context was added. This happened at a 2,200-token budget, with context
built via AST extraction and import-graph resolution rather than raw file dumping, and configurations
differing by only 500 tokens end to end. The authors attribute it to attention representation rather
than content selection: once relevant context sits in a flat token sequence beside the diff, models
stop reliably attending to the changed lines.

Your plan says "do not send 120K tokens merely because the context window permits it." That result
says the ceiling may be far lower than you assume, and the whole-repository lane's value proposition
is the thing at risk. But your reviewers get `read/grep/glob/lsp/ast_grep` — they *retrieve* rather
than receive a flat dump, which is a materially different regime and may not degrade the same way.

So test it: **flat expanded packet vs. minimal packet plus retrieval tools**, same items, same lens.
Cheap, and it either validates the whole-repo lane or tells you to cut it before you build routing
around it.

### Run budget

Generated by `build_corpus.py assignments`, for 47 items with the Latin square and arm T on a
24-item subset:

| arm | reviewer runs | synthesis |
|---|---|---|
| A — GPT only | 47 | 47 |
| B — GPT + 1 critic | 47 | 47 |
| C — 3 critics, floor prompt | 141 | 47 |
| D — 3 critics, rotated lenses | 216 | 72 |
| T — same-family triplicate | 72 | 24 |
| **reviewer subtotal** | **523** | **237** |
| probe (short, single-turn, no tools) | 141 | — |
| context cross-cut (optional, 10 items × 2 regimes × 3) | 60 | 20 |

At roughly 6 claims per run, expect ~3,500 claims. The deterministic gates in §5 dispose of the
unparsed and unanchored ones without a judge call; budget ~2,000 claim judgements at two
non-authoring families each. Reviewer runs are non-blocking three at a time, so wall-clock is
roughly (523 / 3) × slowest-reviewer latency plus synthesis.

The former ≥32K local-Qwen evidence-scout constraint is historical only. Local Qwen dispatch is retired; any future Qwen experiment must use a separately authorized hosted lane and preserve the frozen family-accounting rules.

---

## 5. Scoring

Three ordered stages. Everything a machine can decide is decided before a judge sees a claim.

### 5.1 Deterministic gates — `score_lrhe.py`

Parses the reviewer contract, extracts `path:line` anchors, computes file- and hunk-level overlap
against the labeled sites, checks path existence, and records tool-violation and model-identity
compliance. Notes:

- The contract's `|` delimiter collides with free text — reviewers write `claim=log uses | as a
  separator`. The scorer anchors on key names rather than splitting on `|`, so this parses correctly,
  but add "escape pipes" to the reviewer prompt anyway and watch `contract_parse_rate`.
- A claim with no valid anchor cannot be promoted. This is how your existing "≥95% of promoted claims
  carry source or test anchors" gate becomes a measurement instead of an aspiration.

### 5.2 Judge panel — one deviation from SWE-PRBench

Adopt CONFIRMED / PLAUSIBLE / FABRICATED, with PLAUSIBLE not penalized as hallucination (human
reviews are not exhaustive) and 1:1 bipartite matching so three restatements of one defect earn one
hit.

**Deviate on judge composition.** SWE-PRBench used a single fixed judge and states plainly that mild
judge-family bias cannot be excluded without cross-family validation. For a benchmark that is fine.
For an evaluation whose entire output is a *per-family* comparison, a single-family judge is
disqualifying — and you already cite the self-preference literature as a reason to distrust it.

So: judge every claim with **two families that did not author it**, and route disagreements to a
human. Then calibrate: sample 60 claims stratified across strata and verdicts, label them blind by
hand, and compute Cohen's κ against the panel. **Gate: κ ≥ 0.70** — SWE-PRBench reported 0.75 against
its rubric and 0.616 across judges, so 0.70 is demanding but achieved. If κ misses, the headline
numbers are judge noise and no amount of bootstrap will fix it.

### 5.3 Verdict lattice — execution outranks agreement

```
REFUTED               verify= was executed and the predicted failure did not occur
FABRICATED            anchor path does not exist, or panel says fabricated
CONFIRMED             panel matched it to a label AND the localization gate passed
CONFIRMED_UNANCHORED  panel matched it but no valid anchor  → excluded from promoted set
PLAUSIBLE             grounded and correct but not in ground truth, or lost the 1:1 match
UNPARSED              contract violation
```

Precedence is the point. A claim at `conf=0.95` raised by all three families, whose `verify=` check
runs clean, is REFUTED. That is the mechanism your design is built around, and S2/S3/S4 are the only
strata where you can exercise it, because they are the only ones with executable labels.

---

## 6. Analysis

**The unit of analysis is the labeled defect, clustered by item.** 40 items carry roughly 80–250
labeled defects depending on how many human comments the S1 PRs attracted (SWE-PRBench weights items
by `log(comments + 1)`; some PRs carry 25). Analyzing at item level throws away nearly all the
signal; analyzing at defect level while assuming independence overstates precision by roughly the
square root of the cluster size. So: defect-level statistics, item-level resampling. Every interval
`analyze_lrhe.py` reports comes from a bootstrap that resamples **items** with replacement,
stratified by stratum.

Reported in decision order:

1. **Arm contrasts** — verified critical recall by arm, paired within item.
2. **Marginal contribution** — per-family unique verified findings, pairwise Jaccard, and
   leave-one-family-out Δ union recall. *Read §7 before interpreting any of these.*
3. **Diversity vs. null** — the load-bearing comparison.
4. **Lens decomposition** — family main effect, lens main effect, and the family × lens interaction
   tested by permuting lens assignment *within* item, which respects the Latin square. Pre-register
   the interaction as exploratory.
5. **Cost of being wrong** — fabrication rate, refutation rate, trap promotion, null-item FPs.
6. **Diagnostics** — judge κ, contamination rate by family, gate compliance, cost and latency by
   family, and observed quota pool per run.

---

## 7. The finding that should change the design

Before running anything, we simulated the analysis against a known generative model — three
reviewers, 40 items, ~84 labeled defects, baseline per-family detection near 37%, which is the
neighbourhood published single-model review detection rates put you in.

**With three *statistically identical* reviewers:**

| Third reviewer's true quality | "unique verified findings" | leave-one-out Δ union recall | its solo recall |
|---|---|---|---|
| much worse (−0.8 logit) | 2.9 | 3.4 pp | 0.514 |
| **identical (0.0)** | **4.9** | **5.7 pp** | 0.722 |
| much better (+0.8 logit) | 6.8 | 8.0 pp | 0.877 |

Per-experiment SD on the leave-one-out Δ is 0.026–0.031.

Read that table carefully. A reviewer that adds **nothing** — same capability, independent draws —
produces about five unique verified findings and a 5.7-point union-recall drop when removed. Those
are exactly the numbers that would be written up as "Grok is genuinely diversifying review."
They are the arithmetic consequence of three noisy detectors, and a coin would produce them.

The interesting contrast — identical vs. genuinely better — is 2.3 points of leave-one-out Δ against
a 3-point SD. At 40 items that is roughly one standard deviation, i.e. around 50–60% power. The
metric your plan currently designates as "the most informative analysis" cannot, on its own, answer
the question it is aimed at.

**Two fixes, both cheap:**

1. **Arm T — same-family triplicate.** Run one family three times independently on the arm-D subset.
   This gives you the empirical null for "three independent equally-capable reviewers." If the
   cross-family council's numbers are not clearly better than the same-family triplicate's, the
   diversity premium is unsupported, and three samples of one model would buy the same coverage
   without three provider integrations, three quota paths, and three failure modes.

2. **Use error *decorrelation*, not union recall, as the diversity statistic.** Mean pairwise Jaccard
   of caught-sets, cross-family minus same-family. Negative means cross-family errors really are
   less correlated. It is better powered than the union-based delta because it uses all pairs instead
   of collapsing three reviewers into one number.

`analyze_lrhe.py` refuses to interpret the marginal-contribution numbers when arm T is absent, and
says so in its output. That refusal is deliberate.

There is real supporting evidence that cross-model union coverage exceeds any single model — one
recent security-review write-up found the union of four models' findings dwarfed any individual
model's, with only two vulnerability categories caught by all four — but the same write-up notes its
counts are *categories, not validated true positives*. That is the gap arms C, D and T close.
Treat vendor claims of "cross-model review catches 40–60% more issues" as the hypothesis under test,
not as a prior.

Re-run the power calculation with your own assumptions before committing quota:

```bash
python3 scripts/power_lrhe.py --sweep-items 16,24,32,40,56 --effect 0.8 --reps 300
python3 scripts/power_lrhe.py --sweep-effect 0,0.4,0.8,1.2 --items 40 --reps 300
```

---

## 8. Promotion gates

Your original gates, revised where the public corpus lets them be measured properly and split by
stratum where a single threshold would be meaningless.

**Hard, no exceptions:**

| Gate | Threshold | Where measured |
|---|---|---|
| Repository writes by a reviewer | **0** | `gate_no_write` |
| Nested dispatch by a reviewer | **0** | `gate_no_recursion` |
| Model identity matches the pinned selector | **100%** | `gate_model_identity` |
| Silent provider fallback | **0 occurrences** | identity mismatch, per run |
| Schema-valid terminal responses | ≥99% | `gate_schema_valid` |
| Contract parse rate | ≥97% | `contract_parse_rate` |
| Evidence cap respected | 100% | `cap_respected_rate` |
| Judge–human agreement | **κ ≥ 0.70** | `judge_reliability` |

**Performance, per stratum:**

| Gate | S1 | S2/S3 | S4 | S5 |
|---|---|---|---|---|
| Verified critical recall, arm C or D vs arm A | +≥10 pp | +≥15 pp | — | — |
| Promoted claims carrying source/test anchors | ≥95% | ≥95% | — | — |
| Fabrication rate | ≤ arm A | ≤ arm A | — | — |
| Trap promotion (council level, ≥12 items) | — | — | ≤25% | — |
| P0/P1 claims on null items, per run | — | — | — | ≤0.15 |
| Second-round invocations per review | ≤1 | ≤1 | ≤1 | ≤1 |

**Diversity gate — new, and the one that decides how many lanes you keep:**

> Cross-family minus same-family pairwise Jaccard must be **negative with the upper CI bound below
> zero**. If it is not, promote at most one external critic and revisit later. Three lanes that do
> not decorrelate are three times the cost, three times the quota exposure, and three times the
> failure surface for a variance reduction you could buy by sampling one model three times.

---

## 9. What to run first

A six-item smoke pass, before any of the above. Two items from each of S1/S2/S3, one reviewer family,
one lens. It costs almost nothing and it catches the things that otherwise waste a full run:

1. Does the reviewer emit parseable contract strings under `schemaMode: strict`?
2. Do the anchors it emits actually resolve to paths in the checked-out snapshot?
3. Does the ARVO container rebuild and does `verify=` execution wire up end to end?
4. Does the reported model selector match the pinned one — i.e. is fail-closed pinning working?
5. Does the tool allowlist hold under `yolo` approval mode? (Prompt-level "do not edit" is not a
   control; this is the one that matters.)
6. Grok's quota path. Record both dashboards, run one realistic review, inspect which allowance
   moved, and record cost per review. Every run in this protocol logs `quota_pool` for exactly this
   reason. Until it is answered, run the council as GPT + Claude + Gemini and treat Grok's cells as
   a separate qualification.

Then Phase 0 provider canaries (already authorized as synthetic), then the probe pass, then arms
A→B→C, then D and T together, then the context cross-cut if D looks worth keeping.

---

## 10. Limits, stated plainly

- **Label semantics differ across strata and are not interchangeable.** S1's ground truth is "a human
  reviewer said so," which is neither exhaustive nor always right. S2/S3's is executable and much
  stronger. Do not average verified recall across strata into one headline number; report per
  stratum, always.
- **47 items detects large effects only.** Expect wide intervals on the family comparison. Act on
  point estimates where the decision is keep-or-drop and the cost of being wrong is bounded; do not
  publish the interaction.
- **The judge is the weakest link and it is inside the measurement.** κ ≥ 0.70 is a floor, not a
  guarantee, and cross-family panels reduce but do not remove family preference.
- **Contamination cannot be eliminated, only bounded.** The probe gives you a per-family rate. Report
  it next to every per-family number.
- **The corpus is Python- and C-dominant.** SWE-PRBench is 69% Python; ARVO is C/C++. If your work is
  mostly elsewhere, the transferable conclusions about *council structure* still hold; conclusions
  about which family is strongest may not. Defects4J (835 Java defects across 17 projects — with the
  caveat that 21.6% are unsuitable under strict reproducibility requirements and a further 7.1% have
  under-specified test suites) and BugsInPy (493 Python bugs, 17 projects) are the fallbacks if you
  need language breadth.
- **Vendor code-review benchmarks are not usable as comparison points.** The prominent ones inject
  LLM-authored bugs into clean PRs, evaluate with LLM-as-judge, are run by the vendor being measured,
  and in at least one case do not publish the ground truth being scored against. Sample sizes of 50
  PRs are noise. This protocol's insistence on published corpora, executable labels, blind judging
  and item-clustered inference is a direct response to that pattern — apply the same skepticism to
  your own results.

---

## Files

```
LRHE-PROTOCOL.md              this document
schema/item.schema.json       corpus item + label + trap
schema/run.schema.json        reviewer run record
scripts/build_corpus.py       source adapters, scrubber, probe-packet + Latin-square generator
scripts/score_lrhe.py         deterministic gates, localization, verdict lattice, 1:1 matching
scripts/analyze_lrhe.py       defect-level metrics, item-clustered bootstrap, diversity-vs-null
scripts/power_lrhe.py         corpus-size and effect-size power calculator
fixtures/make_fixtures.py     hand-built fixtures covering every scorer branch
fixtures/simulate_experiment.py  full synthetic 40-item run, end to end
```

Verified working against fixtures and against a simulated 40-item / 360-run / 1,032-claim experiment
in which the analysis correctly recovered injected family effects (+0.30 / 0.00 / −0.35 logit), the
injected lens main effect, and the injected family × lens interaction (permutation p = 0.007), while
correctly reporting zero unique contribution for the lane that had been given none.

The corpus builder needs network access and provider credentials, so it has not been executed here.

---

## Addendum, 2026-07-27 — 40 items became 47

Appended rather than edited in. A preregistration whose assumptions get quietly
rewritten to match the data is no longer a preregistration, so the sections above
stand as written and this records what changed.

§2 targets 47 items and the built corpus has 47. The statistics and power sections
(§7, §8) were computed at **40**, the earlier target, and were never recalculated.
Everything they say about power is therefore conservative rather than wrong: the
design got larger, not smaller.

Recomputed with `power_lrhe.py --sweep-items 40,47 --effect 0.8 --reps 60`:

| items | mean labeled defects | power, keep/drop |
|---|---:|---:|
| 40 | 84.8 | 0.883 |
| 47 | 99.6 | 0.900 |

About two points of extra power for the leave-one-family-out contrast. Nothing in
the promotion gates moves, and no threshold in §8 needs revisiting.

Two other numbers above are now stale in the same direction. The "~84 labeled
defects" the §7 simulation assumes is 85 in the built corpus, which is close enough
that the simulated null still describes it. The "simulated 40-item / 360-run /
1,032-claim experiment" in §12 was the state of the simulator at the time; it now
emits arms C, D and T rather than D alone, because arm T — the empirical null this
protocol calls load-bearing — had never actually been generated end to end.

What did NOT change: the unit of analysis is still the labeled defect clustered by
item, recall is still never averaged across strata, and the κ ≥ 0.70 human
calibration gate still stands and has not been met.
