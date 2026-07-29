# Corpus provenance and source audit

What the upstream sources actually contain, measured on 2026-07-26 against pinned
revisions. Read this next to `LRHE-PROTOCOL.md` section 2: several things the
protocol asserts about these datasets are not true of the data as shipped, and
three of them would have silently corrupted the result rather than failing loudly.

Every claim below was checked by fetching the artifact, not by reading its
documentation.

---

## Pins

| Source | Revision | Verified |
|---|---|---|
| SWE-PRBench | `hf://foundry-ai/swe-prbench@b87f5797aef3ed2c3153bb1304ea4d801d36ba6e` | 350 PRs, 1,674 ground-truth comments |
| SWE-bench-Live | `hf://SWE-bench-Live/SWE-bench-Live@a637bd46829f3132e12938c8a0ca93173a977b8e` split `lite` | 300 instances |
| Live submissions | `gh://SWE-bench-Live/submission@main/submissions/lite` | 6 submissions, 4 usable |
| ARVO | `gh://n132/ARVO-Meta@v3.0.0/arvo.db` | 6,138 rows, sha256 `331184ca…f97ce` confirmed |

---

## Findings that change the design

### 1. SWE-PRBench ground truth contains AI-authored review comments

The dataset's own inclusion rubric (`dataset/rubric.md` §2) requires ground-truth
comments to be "Written by a human (not a bot or AI tool)" and excludes any author
matching "known bot patterns." The filter leaked: **85 of the 1,674 ground-truth
comments are authored by `gemini-code-assist`** (a further 102 of 3,093 in the
unfiltered `human_review_comments`, including `cursor`).

For a normal benchmark this is a minor labelling defect. For LRHE it is
disqualifying if unhandled — the entire deliverable is a per-family comparison, and
Gemini would be scored on recall against Gemini's own prior review output. That is
a self-preference channel the protocol's contamination section does not anticipate,
and it is not detectable by the recall probe, because it is not contamination: the
label is genuinely there in the corpus.

**Handled:** `sources.BOT_AUTHOR` drops bot-authored comments before labels are
built, and `build_notes.bot_comments_dropped` records the count per item. Zero were
dropped from the 14 selected items, because the freshest slice happens to be clean —
the filter still matters for any reselection.

### 2. No severity annotation exists anywhere in SWE-PRBench

`severity` is `null` on all 1,674 comments, `is_blocking` is `null` on all 1,674,
and `has_severity_annotations` is `false` on all 350 PRs.

The protocol's plan said to derive severity "from the comment's own language (map
must/blocker → 1, else 2)." That would be a language heuristic over a field the
dataset already answers structurally: `requires_change` is true on 510 of 1,674
comments (30.5%).

**Handled:** severity 1 when `requires_change`, else 2, with
`severity_confirmed_by_human: false` on every label. This is load-bearing — section
8's performance gates are stated in *verified critical recall*, i.e. severity ≤ 1 —
so the protocol's instruction to hand-confirm the mapping on a sample stands, and
now applies to `requires_change` rather than to a regex.

### 3. `Type3_Latent` does not exist; the tier is `Type3_Latent_Candidate`

Observed difficulty counts: `Type1_Direct` 232, `Type2_Contextual` 75,
`Type3_Latent_Candidate` 43, `Type3_Latent` **0**.

Sampling on the protocol's string would have produced an empty Type3 tier — the
tier that carries the whole-repository lens and, per section 3, the only tier that
is contamination-resistant by construction. The rubric defines the candidate tier
as "at least one ground-truth comment references a file not in the diff," which is
the property the protocol actually wants; "Candidate" flags that the upstream
authors did not hand-confirm each one.

**Handled:** `build_corpus.S1_DIFFICULTY_PLAN` uses the real value.

### 4. Only 37% of ground-truth comments carry a line number

`line` is present on 619 of 1,674 comments; `diff_hunk` is present on 1,621 (96.8%).

Localization gating (section 5.1) is what turns "≥95% of promoted claims carry
anchors" from an aspiration into a measurement, so a label with no line range is a
label the scorer cannot use.

**Handled:** when `line` is absent the label range comes from the last hunk header
in `diff_hunk` (`@@ -a,b +c,d @@` → `[c, c+d-1]`), which is where GitHub anchors a
review comment. Comments with neither are dropped.

### 5. `SWE-bench/experiments` has no SWE-bench-Live data

`evaluation/` contains `bash-only`, `lite`, `multilingual`, `multimodal`, `test`,
`verified` — and no `live`. The join the protocol specifies for S2 (Live instances
crossed with `SWE-bench/experiments` patches) cannot be made.

**Handled:** candidate patches come from `SWE-bench-Live/submission` instead, which
carries `submissions/lite/<name>/{preds.json, results.json, logs/}`. Four
submissions have full id overlap with the pinned `lite` split and mid-range resolve
rates (22.1%, 20.9%, 17.2%, 12.3%); `20250725-openhands-Qwen3-Coder-480B-A35B` is
excluded because only 122 of its 291 completed ids survive in the pinned split,
which would bias selection toward instances that outlived a split revision.

Two `results.json` shapes exist and both are handled: 2025 submissions use
`resolved_ids`/`unresolved_ids`, the 2026 one uses `success_ids`/`failure_ids`.

Items are drawn round-robin across the four submissions so the corpus is not one
agent's output.

### 6. SWE-bench-Live is no longer live, and the date gate cannot be met

The protocol builds S2 on Live because it "adds 50 newly verified instances
monthly." Monthly parquet files stop at `202506`; the dataset was last modified
2025-09-18. The pinned `lite` split spans `created_at` **2024-10-01 → 2025-03-30**.

Section 3's date gate requires a fix date later than the latest cutoff among all
participating families. No S2 item can satisfy that against 2026-era models.

**Not fixable at the source.** S2 ships with `date_gate_cutoff` unset and relies on
control #1, the recall probe, which the protocol itself ranks first and calls "cheap
and decisive." Report the per-family probe rate next to every S2 number; do not
present S2 as date-gated.

S1 is unaffected: its selected items merge between 2026-01-28 and 2026-02-17.

### 7. ARVO cannot identify its own falsely-patched subset

The high-value S3 population is the ~329 cases where a real developer patch was
recorded as a fix but the PoC still crashes. In the released `arvo.db`, **all 6,138
rows carry the identical triple `(reproduced, patch_located, verified) = (1, 1, 0)`**.
Those columns are constant and therefore carry no information. There is no
`false_patch` field. The paper's 329 come from a separate, unreleased
`upstream_false_positives.db`, and the v3 release notes say prior false positives
were fixed, so the historical ids are not recoverable from v3 by any single flag.

`arvo.db` also has no date column and no vulnerable/base commit column. The newest
observable case material is dated 2025-08, so S3 cannot meet the date gate either.

**Handled:** selection is an execution result, not a query. `build_corpus.py
arvo-sweep` runs the PoC in both the `-vul` and `-fix` images and partitions cases
into incomplete-fix / correct-fix / unusable. See the cost note below.

### 8. arXiv:2511.18608 publishes no corpus — S4 source (a) is dead

The paper describes 9,942 disclosed HackerOne reports including 1,400 invalid, with
a rejection-reason taxonomy. The arXiv HTML, PDF and TeX source expose no
repository, no DOI and no data-availability statement. The cited
`Hacker0x01/hacktivity` project is a GitHub issue-mirror search CLI whose records
are `{title, url, repository, participants, labels}` — no HackerOne outcome.
HackerOne's own API requires an authenticated customer token.

**Handled by substitution, not by faking it.** S4 is built entirely from source (b),
ARVO inversion: take a case whose fix the sweep observed to be *clean*, and assert
in the packet — framed as an unclosed concern from a previous reviewer — that the
crash is still reachable. Ground truth `invalid`; `verify_cmd` is the same fixed-image
run that proved it. `build_corpus.py fetch --stratum S4` refuses with an explanation
rather than emitting placeholder traps.

### 9. Sanitizer output, not exit status, is the crash oracle

A container that fails to pull, a harness that never starts and an OOM kill all
exit non-zero with no crash having occurred. Scoring any of them as "no crash"
would invert every S3 and S4 label at once — the fixed image would look clean
because it never ran.

**Handled:** `_run_probe` classifies on a sanitizer report (`ERROR: *Sanitizer`,
`SUMMARY: *Sanitizer:`, `runtime error:`), records `ran` separately from `crashed`,
and requires the fixed-image signature to match the vulnerable-image signature
before calling a fix incomplete. Traps additionally require `fix_ok`.

### 10. ARVO-Meta declares no license; the upstream project's does

`n132/ARVO-Meta` reports `license: null` with no LICENSE file. What actually travels
to a provider, though, is the upstream project's patch and its sanitizer output, so
the upstream repository's terms govern. 332 of 400 candidates are GitHub-hosted and
resolvable; the rest (cgit, googlesource, gitlab.gnome.org, hg) are not.

**Handled:** `provider_data_allowlist` is populated from the resolved upstream
license and is left **empty** when it cannot be resolved, so an unlicensed item is
inert rather than quietly authorized.

### 11. The scrubber must not be run over a diff

`scrub_text` replaces `\b[0-9a-f]{7,40}\b` with `[sha]` and `https?://\S+` with
`[url]`. Over prose that is correct. Over `design_or_diff` it rewrites the artifact
under review: the integer literal `2147483648`, a documentation URL inside a source
comment, and a `#245` in a CSS fixture all get mangled, and the reviewer is asked to
review something the maintainers never wrote.

**Handled:** `scrub_diff` removes only git-regenerated provenance (`index <sha>..<sha>`)
and forge/CVE identifiers, leaving code bytes untouched. `validate_corpus.py` applies
the matching narrower check so it stops reporting code as a leak.

### 12. The run matrix was not reproducible

`cmd_assignments` chose arm B's rotating critic with `FAMILIES[hash(iid) % 3]`.
Python salts `hash()` per process, so the pre-registered design changed on every
invocation. It also took the arm-D/T subset as the first 24 items in file order,
which hands the Latin square and the empirical null to whichever strata sort first.

**Handled:** `_stable_hash` (sha256) and `_stratified_subset`. Verified byte-identical
across three runs under `PYTHONHASHSEED=random`.

---

## Current corpus state

| Stratum | Built | Target | Status |
|---|---|---|---|
| S1 `REVIEW_HUMAN` | **14** | 14 | complete — 4/5/5 across Type1/Type2/Type3_Latent_Candidate, 75 labels (27 critical), 10 repos, merged 2026-01-28 → 2026-02-17 |
| S2 `PATCH_VERDICT` | **10** | 10 | complete — 5 unresolved + 5 resolved controls, 4 submissions, executable `verify_cmd` |
| S3 `VULN_POC` | **8** | 8 | complete — 4 of the 8 carry an upstream licence the host's detector could not classify (`NOASSERTION`/`UNDECLARED`); see below |
| S4 `FP_TRAP` | **12** | 12 | complete — every trap sits on a fix whose PoC was observed running clean |
| S5 `NULL` | **3** | 3 | complete — 4–6 files, 5.7–8.7 KB diffs, no same-file follow-up in 90d |

All 47 items built, validated, scrubbed, and dispatched as reviewer-safe packets.
85 labels, 37 critical, 5 executable, 12 traps, 11 controls.

All 47 items authorize all five provider vendors; no family has a coverage hole in
any stratum.

The four S3 items with an unclassifiable licence — ImageMagick, KDE
kimageformats, libheif, freetype2 — got there by explicit operator decision, not
automatically. `_allowlist()` withholds every provider when the detector returns
`NOASSERTION` or `UNDECLARED`, which is the right default and also means a rebuild
silently narrows them again: re-run `check_packet_gates.py grant` after any
corpus regeneration. The audit names them on every run regardless, because an
unresolved licence stays worth seeing after it has been decided.

### Sweep results

19 ARVO cases swept, `--prune` on: **1 incomplete fix, 18 correct fixes, 0 unusable.**
The observed incomplete-fix rate of 1/19 ≈ 5.3% matches the ARVO paper's
329/6138 ≈ 5.4%, which is the first independent confirmation that v3 did not
silently remove that population.

| case | project | vulnerable | fixed | classification |
|---|---|---|---|---|
| 434978682 | libavc | ASan | **still crashes** | incomplete fix → S3 gold |
| 438309779 | gpac | ASan heap-use-after-free | clean | correct fix |
| 437162340 | ndpi | MSan use-of-uninitialized-value | clean | correct fix |
| *16 others* | libdwarf, libxml2, harfbuzz, libjxl, imagemagick, upx, gpac, ndpi | — | clean | correct fix |

### Cost, measured

**~2 min wall and ~2 GB of image pulls per pair** under x86_64 emulation on arm64.
`arvo-sweep` writes after every case and skips cases already in its output, so it
resumes; `--prune` deletes each pair's images after use, which is mandatory beyond
about 50 cases — 113 GB is free and an unpruned 120-case run would pull ~240 GB.

S3 is complete. The sweep that finished it ran at the observed 5.3% incomplete-fix
rate; budget roughly 76 cases and 2.5 h per additional item if the stratum is ever
extended.

### 13. Provider authorization was a two-way switch on a three-way question

The first cut was "recognized SPDX id → send it to all four families." That is
wrong in both directions and it hid the question protocol §3 actually raises.

- **Copyleft is the real decision.** §3 warns that these corpora over-sample GPL.
  The observed ARVO clean pool is 8 of 13 LGPL, and S2's original control set
  contained a **GPL-2.0** item (`beancount/beancount`) that the old rule
  authorized for all four providers without anyone deciding anything — masked
  further by a hardcoded S2 allowlist that never consulted the repository at all.
- **`NOASSERTION` is not "unlicensed."** GitHub returns it for libdwarf, harfbuzz
  and ImageMagick, all genuinely licensed; its matcher just cannot name a
  multi-license COPYING, an "Old MIT" variant, or the ImageMagick License.
  Collapsing that into the same bucket as a repository with no terms means the
  loud case and the quiet case get the same treatment.

**Handled:** `license_class` returns `permissive | copyleft | unresolved`.
Permissive dispatches; copyleft requires `--allow-copyleft`; unresolved never
dispatches and carries a `license_url` pointing straight at the terms. Selection
in both S2 and the ARVO trap pool now *prefers* dispatchable items, so the policy
question mostly evaporates instead of needing adjudication: S1, S2 and S5 are now
**100% permissive and fully dispatchable**, and the GPL-2.0 control was replaced
by an equally valid BSD-3-Clause one.

`validate_corpus.py` enforces the rule rather than trusting it — an allowlist on
an unresolved license is an error, and copyleft with an allowlist is a warning
that names the decision. Verified against a hand-widened item.

### 14. Anchors could not name extensionless files

Found by the corpus itself: one S4 trap is sited in a `Makefile`, and
`score_lrhe.py` required a dotted extension, so the trap could never register as
promoted — the bait would have scored as correctly refused every time. With the
barename branch added, all 12 traps register. `Makefile`, `Dockerfile`,
`configure`, `BUILD` and friends now anchor when a line number is present, and
still do not match the same words in prose.

### 15. The consensus rule recorded a bare majority as settled

`aggregate_case` flagged `needs_resolution` only when a CONFIRMED majority's voters named
different `label_id`s. It had no dependency on the vote margin, so all 83 automated-audit
cases carried `needs_resolution: false` and `human_queue.jsonl` stayed empty while 22 of
them rested on a bare 3–2 split.

Measured across two independent five-voter panels (rep1, and a rep2 built by running the
missing second replicate for claude/gemini/grok): consensus accuracy is **0.72** at a bare
majority against **0.96** at 4–1 and **0.99** at 5–0, and the bare bucket holds **13 of 16**
observed consensus errors. On rep1 alone the separation looked perfect — 6 of 6 errors in
that bucket — but that was small-sample plus reference coupling: `panel_verdict` is built
from claude/gemini/grok, who voted only in rep1, so a rep1 consensus shares three of its
five voters with the reference it is scored against (0.928 agreement on rep1, 0.887 on rep2).

`needs_resolution` is now `label_ambiguous or best == min_majority`. **Triage, not a filter:**
3 of the 15 errors carried a wider margin, so the flag is not exhaustive and the docstring
says so. `label_split` was re-keyed to `label_ambiguous` at the same time — leaving it gated
on `needs_resolution` would have started populating it on every bare majority, changing the
meaning of a field the label-recall figures read.

This is a new producer policy, so `AGGREGATION_POLICY_VERSION = 2` is declared and emitted on
every aggregate row — a derivative says which policy produced it instead of being dated by its
filename. v1 is the behaviour above; v2 is margin-aware with `label_split` keyed to label
ambiguity.

`lrhe-data/auto-reliability-v1/aggregate-fresh.jsonl` was **not** regenerated. It was produced
under v1 and is preserved byte-identical, so its `needs_resolution` column is all-false by
construction rather than by measurement. Any v2 recomputation is a versioned derivative
(`aggregate-fresh-v2.jsonl`), never an overwrite.

### 16. The human calibration gate could not be scored at all

Found by smoke-testing the one step preflight still lists as outstanding, before any row was
labelled. Three defects stacked:

**Two packets.** `judge-calibration-packet.csv` is the frozen *selection manifest* — the
`--selection` input to `auto_reliability.py build`, pinned by
`test_01_selection_keys_are_preserved_exactly`, and leaky (it carries `item_id` and the `S4`
trap prefix). The blinded packet that replaced it for human use is
`auto-reliability-v1/human-packet.csv`: opaque `AR-####` ids and two blank columns.
`preflight._calibration()` read the former and its guidance told the operator to fill it.

**Two id namespaces.** `run_id` became a digest of the reply after the 60-claim selection was
frozen, so the selection's keys match nothing in a current judge file — `_run_stem` already
documents this and `resolve_selection` already handles it soundly, but neither `cmd_kappa` nor
preflight used it. Both joined on exact `(run_id, claim_rid)`: 0 of 60 matched, so preflight
would have reported `kappa n/a` even after a human labelled everything.

**A silent collapse.** `judge-floor.jsonl` holds 667 rows over 315 claims. Indexing it by
claim kept whichever row came last, scoring the rater against an arbitrary panel member — a
number rather than an error, which is the worse failure.

One loader, `blinded_calibration_pairs`, now serves both `judge_lrhe.py kappa --case-map` and
`preflight._calibration()`, resolving opaque `case_id` through `case-map.private.jsonl`.
`load_judge_index` refuses a per-judge file or one not keyed by claim; `calibration_agreement`
produces every section-8 figure from one pair list so the CLI and the status line cannot
disagree. The gate is exactly the 60 `kind == "case"` rows and all 60 must be labelled; the 5
`case_supplement` rows stay in the frozen packet, are excluded from κ and reported separately;
`control` is refused. Under `--case-map`, `--expect-rows` counts gate rows, so the 65-row
packet satisfies `--expect-rows 60`.

Blinded-path violations name `case_id` and never echo `item_id`: reporting `S1` back would leak
the stratum prefix the packet exists to withhold, and a test asserts its absence.

`human-packet.csv`, `FROZEN_INPUTS` and `MANIFEST.sha256` are unchanged and the gate stays
n=60. Both packets remain 0-labelled — the gate compares a *human* against the panel, and the
operator authorization forbids model output in `human_verdict` / `human_label_id`.

### 17. A "byte-identical" revert that was not, and the rule that catches it

Near-miss, caught by `git status` rather than by any check of mine. While investigating the
calibration path I briefly rewrote `judge-calibration-packet.csv`, then reverted it with
`path.write_text(path.read_text())`. That file is CRLF in git; universal-newlines on read plus
`write_text` on write silently converted all 61 lines to LF, so the working tree differed from
`HEAD` in every line while the content was unchanged.

The verification was the actual defect. I compared the file against a scratch backup written
through the *same* text-mode round trip, so both agreed with each other while both differed
from the repository. `git diff --ignore-cr-at-eol` and `cmp` after `tr -d '\r'` proved the
content identical, and `git checkout --` restored the exact bytes.

The rule, which finding 11 already implies for diffs and this generalises:

> Compare protected artifacts with Git or raw bytes. Never verify a text-mode rewrite against
> a backup produced by the same text-mode path.

Line endings are deliberately **not** normalised here. `aggregate-fresh.jsonl` is git-LFS
tracked, so `git show HEAD:<path>` prints a 130-byte pointer rather than content: verify that
one by comparing the pointer's `oid sha256` against `shasum -a 256` of the working file, or with
`git hash-object` against `git rev-parse HEAD:<path>`. A plain `cmp` against `git show` output
reports a false difference.
