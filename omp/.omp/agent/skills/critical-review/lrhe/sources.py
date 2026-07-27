#!/usr/bin/env python3
"""
sources.py -- real adapters from public corpora to LRHE items (item.schema.json).

One function per stratum. Every one is deterministic given its pinned revision, so
two builds of the same corpus produce byte-identical items.

What the upstream sources actually provide, as measured rather than as documented
(see PROVENANCE.md for the full audit):

  S1  foundry-ai/swe-prbench @ b87f579 -- 350 PRs, 1,674 ground-truth comments.
      `severity` is null on every comment and `has_severity_annotations` is false on
      every PR, so severity is derived from the published `requires_change` flag.
      85 of the 1,674 comments are authored by `gemini-code-assist` despite the
      dataset's own rubric excluding bots; those are dropped (see BOT_AUTHOR).
      Only 37% of comments carry `line`; the rest are located from `diff_hunk`.
      The difficulty value is `Type3_Latent_Candidate`, not the `Type3_Latent`
      named in the protocol.

  S2  SWE-bench-Live @ a637bd4 `lite` split (300 instances) crossed with
      SWE-bench-Live/submission. NOT SWE-bench/experiments -- that repository has
      no `live` evaluation directory, so its patches cannot be joined to Live.

  S3  n132/ARVO-Meta v3.0.0 arvo.db (sha256 331184ca...f97ce). All 6,138 rows carry
      the identical flag triple (reproduced, patch_located, verified) = (1, 1, 0),
      so no released column identifies the falsely-patched subset. Selecting it
      requires paired container execution; see `arvo_pair_commands`.

  S5  GitHub REST. Conservative filter: reject if ANY file the PR touched received
      a commit on the base branch within 90 days of merge.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------- pins
# Every revision here is content-addressed. Bump deliberately, never implicitly:
# a floating ref makes the corpus unreproducible and silently changes the labels.

PRBENCH_REPO = "foundry-ai/swe-prbench"
PRBENCH_REV = "b87f5797aef3ed2c3153bb1304ea4d801d36ba6e"

LIVE_REPO = "SWE-bench-Live/SWE-bench-Live"
LIVE_REV = "a637bd46829f3132e12938c8a0ca93173a977b8e"
LIVE_SPLIT = "lite"

SUBMISSION_REPO = "SWE-bench-Live/submission"
SUBMISSION_REV = "main"
# Submissions whose ids fully intersect the pinned `lite` split, with mid-range
# resolve rates so the resolved/unresolved labels balance. openhands-Qwen3 is
# excluded: only 122 of its 291 completed ids survive in the pinned split, which
# would silently bias selection toward instances that outlived a split revision.
SUBMISSIONS = [
    ("20251221-MITIBM-agent-seedoss36b", 0.221),
    ("20250501-sweagent-claude37", 0.209),
    ("20250501-sweagent-gpt41", 0.172),
    ("20260629-sapient-slingshot-claude-4.5", 0.123),
]

ARVO_RELEASE = "v3.0.0"
ARVO_DB_URL = f"https://github.com/n132/ARVO-Meta/releases/download/{ARVO_RELEASE}/arvo.db"
ARVO_DB_SHA256 = "331184ca807c2f136f98dac9f1df94c893f4ee2fdf9329dca517ff88e72f97ce"

# The dataset's own rubric (dataset/rubric.md section 2) excludes bot authors from
# ground truth. It leaked: `gemini-code-assist` accounts for 85 comments. Leaving
# them in would score Gemini against Gemini's own review output in an evaluation
# whose entire deliverable is a per-family comparison.
BOT_AUTHOR = re.compile(
    r"(\[bot\]$|^bot-|-bot$|code-assist|copilot|coderabbit|sourcery|codium|qodo"
    r"|greptile|ellipsis|sweep-ai|deepsource|codacy|sonarcloud|renovate|dependabot"
    r"|gemini|claude|chatgpt|openai|cursor|devin|codex)",
    re.I,
)


# ---------------------------------------------------------------- http

class Http:
    """Caching fetcher. The cache is what makes a rebuild cheap and a rerun honest:
    the same bytes produce the same corpus without re-hitting rate-limited APIs."""

    def __init__(self, cache_dir: Path, token: str | None = None) -> None:
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.token = token or os.environ.get("GITHUB_TOKEN") or _gh_cli_token()

    def get(self, url: str, *, key: str | None = None, auth: bool = False,
            accept: str | None = None) -> bytes:
        path = self.cache / (key or _slug(url))
        if path.exists():
            return path.read_bytes()
        headers = {"User-Agent": "lrhe-build-corpus/1"}
        if accept:
            headers["Accept"] = accept
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = _get_with_retry(url, headers)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return body

    def json(self, url: str, **kw) -> Any:
        return json.loads(self.get(url, **kw))


def _gh_cli_token() -> str | None:
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _get_with_retry(url: str, headers: dict[str, str], tries: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            # 403/429 from GitHub is nearly always the secondary rate limit; the
            # reset header is authoritative and guessing a backoff wastes quota.
            if e.code in (403, 429) and attempt < tries - 1:
                reset = e.headers.get("x-ratelimit-reset")
                wait = 60.0
                if reset and reset.isdigit():
                    wait = max(1.0, min(300.0, int(reset) - time.time() + 2))
                time.sleep(wait)
                last = e
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET {url} failed after {tries} tries: {last}")


def _slug(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", url)[-180:]


# ---------------------------------------------------------------- diff parsing

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def hunk_new_range(diff_hunk: str) -> tuple[int, int] | None:
    """New-file line range of the LAST hunk header in a review comment's diff hunk.

    GitHub anchors a review comment at the end of the hunk it ships, so when the
    structured `line` is absent (63% of SWE-PRBench comments) this is the only
    localization available.
    """
    last = None
    for line in diff_hunk.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            last = m
    if not last:
        return None
    start = int(last.group(3))
    length = int(last.group(4) or 1)
    return start, start + max(length, 1) - 1


def diff_touched(patch: str) -> dict[str, list[tuple[int, int]]]:
    """Map new-file path -> merged new-file line ranges touched by a unified diff.

    Deleted files are skipped: a claim cannot be anchored in a file that no longer
    exists at the reviewed revision, and score_lrhe.py would mark it FABRICATED.
    """
    out: dict[str, list[tuple[int, int]]] = defaultdict(list)
    path: str | None = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            path = None if p == "/dev/null" else re.sub(r"^[ab]/", "", p)
            continue
        if line.startswith("@@") and path:
            m = _HUNK_RE.match(line)
            if m:
                start = int(m.group(3))
                length = int(m.group(4) or 1)
                out[path].append((start, start + max(length, 1) - 1))
    return {p: _merge_ranges(r) for p, r in out.items()}


def _merge_ranges(ranges: list[tuple[int, int]], gap: int = 8) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ranges = sorted(ranges)
    merged = [list(ranges[0])]
    for s, e in ranges[1:]:
        if s <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(a, b) for a, b in merged]


def patch_is_substantive(patch: str) -> bool:
    """Reject empty, comment-only and test-only patches.

    The patch-verification literature drops these because a reviewer can shortcut
    on surface features instead of reasoning about the change.
    """
    if not patch or not patch.strip():
        return False
    touched = diff_touched(patch)
    if not touched:
        return False
    if all(_is_test_path(p) for p in touched):
        return False
    for line in patch.splitlines():
        if not (line.startswith("+") or line.startswith("-")):
            continue
        if line.startswith(("+++", "---")):
            continue
        body = line[1:].strip()
        if not body:
            continue
        if body.startswith(("#", "//", "/*", "*", '"""', "'''")):
            continue
        return True   # at least one non-comment code line changed
    return False


def _is_test_path(p: str) -> bool:
    low = p.lower()
    return (
        low.startswith(("test/", "tests/", "testing/"))
        or "/tests/" in low or "/test/" in low
        or re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.[a-z]+$", low) is not None
        or low.endswith((".spec.js", ".spec.ts", ".test.js", ".test.ts"))
    )


# ---------------------------------------------------------------- shared helpers

ALL_PROVIDERS = ["anthropic", "google", "openai", "xai"]

_COPYLEFT = re.compile(r"^(A?GPL|LGPL|MPL|EPL|CDDL|OSL|EUPL|CECILL|SLEEPYCAT)", re.I)
_UNRESOLVED = {"NOASSERTION", "NONE", "UNKNOWN", "UNDECLARED", "OTHER", ""}


def license_class(license_id: str | None) -> str:
    """`permissive` | `copyleft` | `unresolved`.

    The binary "recognized SPDX id -> send it anywhere" is the wrong cut. Protocol
    section 3 warns specifically that SWE-PRBench and SWE-bench Pro over-sample GPL,
    and the observed ARVO pool is 8/13 LGPL. Copyleft egress to a model provider is
    a policy call an organization may well have made already; `NOASSERTION` is a
    different problem entirely -- a real license the host's matcher could not name.
    Collapsing the two hides the question that actually needs answering.
    """
    lid = (license_id or "").strip().upper()
    if lid in _UNRESOLVED:
        return "unresolved"
    return "copyleft" if _COPYLEFT.match(lid) else "permissive"


def _allowlist(license_id: str | None, *, allow_copyleft: bool = False) -> list[str]:
    """Which providers may receive this item.

    Permissive public source goes to every participating family. Copyleft is
    withheld pending an explicit policy decision (`--allow-copyleft`), and an
    unresolved license is withheld outright rather than quietly defaulting to open.
    """
    cls = license_class(license_id)
    if cls == "permissive" or (cls == "copyleft" and allow_copyleft):
        return list(ALL_PROVIDERS)
    return []


def _repo_license(http: Http, repo: str) -> str | None:
    try:
        d = http.json(f"https://api.github.com/repos/{repo}/license", auth=True,
                      key=f"license_{repo.replace('/', '__')}.json")
    except urllib.error.HTTPError:
        return None
    lic = (d or {}).get("license") or {}
    return lic.get("spdx_id") or lic.get("key")


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clean_console(text: str | None) -> str:
    """Strip terminal control sequences and CRs from captured tool output.

    ARVO stores sanitizer reports exactly as the console emitted them, colour codes
    and all. Those bytes reach the reviewer's packet, where they are pure noise:
    they carry no review signal, they fragment tokenization around the very lines
    that matter, and they make an anchored quote impossible to match.
    """
    if not text:
        return ""
    return _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def _cap(text: str | None, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... truncated at {limit} chars by build_corpus ...]"


@dataclass
class BuildStats:
    considered: int = 0
    selected: int = 0
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []


# ================================================================ S1

def fetch_s1(http: Http, *, want: dict[str, int], date_gate: str | None,
             max_per_repo: int = 2, allow_copyleft: bool = False,
             stats: BuildStats | None = None) -> list[dict]:
    """SWE-PRBench -> S1_REVIEW_HUMAN items.

    `want` maps the dataset's own difficulty strings to counts. The protocol asks
    for `Type3_Latent`; the dataset ships `Type3_Latent_Candidate` and zero rows of
    the former, so the caller must use the real value.
    """
    st = stats or BuildStats()
    base = f"https://huggingface.co/datasets/{PRBENCH_REPO}/resolve/{PRBENCH_REV}"
    rows = [json.loads(line) for line in
            http.get(f"{base}/dataset/prs.jsonl", key="prbench_prs.jsonl").decode().splitlines()
            if line.strip()]
    st.considered = len(rows)

    by_diff: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_diff[r.get("difficulty", "")].append(r)

    selected: list[dict] = []
    # The repo cap is global, not per-bucket: the same repository turning up in
    # three difficulty tiers concentrates the corpus on one codebase's idiom and
    # correlates errors across items that the bootstrap treats as independent.
    per_repo: dict[str, int] = defaultdict(int)
    # Scarcest tier picks first. Type3_Latent_Candidate has 43 rows against
    # Type1_Direct's 232, so letting Type1 claim the freshest PRs and the repo
    # budget first would starve the tier the whole-repository lens exists to test.
    for difficulty, n in sorted(want.items(), key=lambda kv: len(by_diff.get(kv[0], []))):
        pool = by_diff.get(difficulty, [])
        if not pool:
            raise ValueError(
                f"difficulty {difficulty!r} has no rows; observed values: "
                f"{sorted(by_diff)}")
        # Freshest first: merge date is the only contamination lever available at
        # selection time. rvs_score breaks ties toward PRs with substantive review.
        pool = sorted(pool, key=lambda r: (r.get("merged_at", ""), r.get("rvs_score") or 0),
                      reverse=True)
        taken = 0
        for r in pool:
            if taken >= n:
                break
            if date_gate and (r.get("merged_at") or "")[:10] <= date_gate:
                continue
            if per_repo[r["repo"]] >= max_per_repo:
                continue
            ann = http.json(f"{base}/dataset/annotations/{r['task_id']}_human.json",
                            key=f"prbench_ann_{r['task_id']}.json")
            item = _s1_item(http, r, ann, difficulty, date_gate, allow_copyleft)
            if item is None:
                continue
            per_repo[r["repo"]] += 1
            taken += 1
            selected.append(item)
        if taken < n:
            st.notes.append(f"S1 {difficulty}: wanted {n}, found {taken} eligible")
    st.selected = len(selected)
    return selected


def _s1_item(http: Http, pr: dict, ann: dict, difficulty: str,
             date_gate: str | None, allow_copyleft: bool) -> dict | None:
    labels: list[dict] = []
    dropped_bot = 0
    label_paths: set[str] = set()

    for c in ann.get("comments") or []:
        reviewer = c.get("reviewer") or ""
        if BOT_AUTHOR.search(reviewer):
            dropped_bot += 1
            continue
        path = c.get("file")
        if not path:
            continue
        line = c.get("line")
        if isinstance(line, int) and line > 0:
            lines = [max(1, line - 3), line + 3]
        else:
            rng = hunk_new_range(c.get("diff_hunk") or "")
            if not rng:
                continue          # unlocalizable: it could never be scored
            lines = [rng[0], rng[1]]
        labels.append({
            "label_id": c.get("comment_id") or f"c_{len(labels) + 1}",
            # No human severity exists anywhere in SWE-PRBench: `severity` is null
            # on all 1,674 comments. `requires_change` is the dataset's own
            # structured blocking signal and is the honest proxy. Section 5 gates
            # on critical recall (severity <= 1), so this mapping is load-bearing
            # and must be confirmed by hand on a sample before results are trusted.
            "severity": 1 if c.get("requires_change") else 2,
            "description": _cap(c.get("body"), 2000),
            "sites": [{"path": path, "lines": lines}],
            "adjudication": "human_review_comment",
            "severity_confirmed_by_human": False,
        })
        label_paths.add(path)

    if not labels:
        return None

    changed = list(pr.get("changed_files") or [])
    # Type3 comments point at files the diff never touched. Those paths must be in
    # the allowlist or score_lrhe.py auto-FABRICATEs the correct answer.
    repo_files = sorted(set(changed) | label_paths)
    lic = _repo_license(http, pr["repo"])

    return {
        "item_id": f"S1-{pr['task_id'].replace('__', '-')}",
        "source_item_id": pr["task_id"],
        "stratum": "S1_REVIEW_HUMAN",
        "difficulty": difficulty,
        "source": "swe-prbench",
        "dataset_ref": f"hf://{PRBENCH_REPO}@{PRBENCH_REV}",
        "repo": pr["repo"],
        "base_commit": pr.get("base_commit", ""),
        "review_commit": pr.get("head_commit", ""),
        "repo_files": repo_files,
        "merged_at": pr.get("merged_at", ""),
        **({"date_gate_cutoff": date_gate} if date_gate else {}),
        "scrubbed": False,
        "license": lic or "UNKNOWN",
        "provider_data_allowlist": _allowlist(lic, allow_copyleft=allow_copyleft),
        "goal": pr.get("title", ""),
        "problem_statement": _cap(pr.get("description"), 12000),
        "design_or_diff": pr.get("diff_patch", ""),
        "labels": labels,
        "build_notes": {
            "bot_comments_dropped": dropped_bot,
            "ground_truth_comments_total": len(ann.get("comments") or []),
            "severity_source": "requires_change flag; upstream severity is null everywhere",
            "language": pr.get("language", ""),
            "num_unique_reviewers": pr.get("num_unique_reviewers"),
        },
    }


# ================================================================ S2

def fetch_s2(http: Http, *, n_broken: int, n_control: int, date_gate: str | None,
             allow_copyleft: bool = False, stats: BuildStats | None = None) -> list[dict]:
    """SWE-bench-Live `lite` x SWE-bench-Live/submission -> S2_PATCH_VERDICT items.

    The reviewed artifact is a real agent-authored candidate patch whose harness
    verdict is already known, which is the deployment shape the council is being
    qualified for: a model authors, the council reviews.
    """
    st = stats or BuildStats()
    instances = {r["instance_id"]: r for r in _live_instances(http)}
    st.considered = len(instances)

    verdicts = _submission_verdicts(http)

    def _ok(repo: str) -> bool:
        return bool(_allowlist(_repo_license(http, repo), allow_copyleft=allow_copyleft))

    broken = _s2_pick(instances, verdicts, resolved=False, n=n_broken, dispatchable=_ok)
    control = _s2_pick(instances, verdicts, resolved=True, n=n_control,
                       exclude={i for i, _, _ in broken}, dispatchable=_ok)
    if len(broken) < n_broken:
        st.notes.append(f"S2 broken: wanted {n_broken}, found {len(broken)}")
    if len(control) < n_control:
        st.notes.append(f"S2 control: wanted {n_control}, found {len(control)}")

    out = []
    for iid, submission, patch in broken + control:
        out.append(_s2_item(instances[iid], submission, patch,
                            lic=_repo_license(http, instances[iid].get("repo", "")),
                            resolved=(iid, submission, patch) in control,
                            date_gate=date_gate, allow_copyleft=allow_copyleft))
    st.selected = len(out)
    return out


def _live_instances(http: Http) -> list[dict]:
    try:
        import pyarrow.parquet as pq
    except ImportError as e:                       # pragma: no cover
        raise SystemExit(
            "S2 needs pyarrow to read the SWE-bench-Live parquet split.\n"
            "  uv pip install pyarrow      (or: pip install pyarrow)") from e
    url = (f"https://huggingface.co/datasets/{LIVE_REPO}/resolve/{LIVE_REV}"
           f"/data/{LIVE_SPLIT}-00000-of-00001.parquet")
    http.get(url, key=f"live_{LIVE_SPLIT}.parquet")
    path = http.cache / f"live_{LIVE_SPLIT}.parquet"
    rows = pq.read_table(path).to_pylist()
    for r in rows:
        ts = r.get("created_at")
        r["created_at"] = ts.isoformat() if isinstance(ts, datetime) else str(ts)
    return rows


def _submission_verdicts(http: Http) -> dict[str, dict[str, Any]]:
    """submission -> {resolved: set, unresolved: set, preds: {iid: patch}}.

    Two results.json shapes exist upstream: the 2025 submissions use
    resolved_ids/unresolved_ids, the 2026 one uses success_ids/failure_ids.
    """
    base = (f"https://raw.githubusercontent.com/{SUBMISSION_REPO}/{SUBMISSION_REV}"
            f"/submissions/{LIVE_SPLIT}")
    out: dict[str, dict[str, Any]] = {}
    for name, _rate in SUBMISSIONS:
        res = http.json(f"{base}/{name}/results.json", key=f"sub_{name}_results.json")
        preds = http.json(f"{base}/{name}/preds.json", key=f"sub_{name}_preds.json")
        out[name] = {
            "resolved": set(res.get("resolved_ids") or res.get("success_ids") or []),
            "unresolved": set(res.get("unresolved_ids") or res.get("failure_ids") or []),
            "preds": {k: (v or {}).get("model_patch") or "" for k, v in preds.items()},
            "model": next(iter(preds.values()), {}).get("model_name_or_path", name),
        }
    return out


def _s2_pick(instances: dict[str, dict], verdicts: dict[str, dict], *, resolved: bool,
             n: int, exclude: set[str] | None = None,
             dispatchable: Callable[[str], bool] | None = None) -> list[tuple[str, str, str]]:
    """Round-robin across submissions so the corpus is not one agent's output.

    Two passes: instances whose repository license is dispatchable under the
    current policy first, then the rest. A copyleft item that slips in is not
    wrong, but it cannot be sent to a provider without a separate decision, and
    silently spending one of ten S2 slots on an item that will not dispatch is a
    worse outcome than picking the next equally valid instance.
    """
    exclude = exclude or set()
    picked: list[tuple[str, str, str]] = []
    seen: set[str] = set(exclude)
    key = "resolved" if resolved else "unresolved"
    base_pools = {name: sorted(v[key] & instances.keys()) for name, v in verdicts.items()}

    tiers = (True, False) if dispatchable else (True,)
    for prefer in tiers:
        pools = {
            name: [i for i in ids
                   if dispatchable is None or dispatchable(instances[i].get("repo", "")) is prefer]
            for name, ids in base_pools.items()
        }
        cursors = {name: 0 for name in pools}
        while len(picked) < n and any(cursors[nm] < len(pools[nm]) for nm in pools):
            progressed = False
            for name in pools:
                if len(picked) >= n:
                    break
                while cursors[name] < len(pools[name]):
                    iid = pools[name][cursors[name]]
                    cursors[name] += 1
                    progressed = True
                    if iid in seen:
                        continue
                    patch = verdicts[name]["preds"].get(iid, "")
                    if not patch_is_substantive(patch):
                        continue
                    seen.add(iid)
                    picked.append((iid, name, patch))
                    break
            if not progressed:
                break
        if len(picked) >= n:
            break
    return picked


def _s2_item(inst: dict, submission: str, patch: str, *, lic: str | None,
             resolved: bool, date_gate: str | None, allow_copyleft: bool) -> dict:
    gold = diff_touched(inst.get("patch") or "")
    cand = diff_touched(patch)
    # Sites span both the gold fix location and what the candidate actually did.
    # A reviewer can legitimately anchor at either: the candidate's wrong edit, or
    # the place the fix was supposed to land and did not.
    sites = []
    for path in sorted({**cand, **gold}):
        for start, end in _merge_ranges(list(gold.get(path, [])) + list(cand.get(path, []))):
            sites.append({"path": path, "lines": [start, end]})

    f2p = list(inst.get("FAIL_TO_PASS") or [])
    test_cmd = (list(inst.get("test_cmds") or ["pytest -rA"]) or ["pytest -rA"])[0]
    verify = _s2_verify_cmd(inst, test_cmd, f2p)

    labels: list[dict] = []
    if not resolved:
        labels.append({
            "label_id": "f2p",
            "severity": 1,
            "kind": "correctness",
            "description": (
                "The candidate patch does not satisfy the hidden test suite. "
                f"Failing FAIL_TO_PASS tests: {'; '.join(f2p[:12]) or '(unlisted)'}"),
            "sites": sites or [{"path": next(iter(cand), "")}],
            "adjudication": "fail_to_pass_test",
            "verify_cmd": verify,
        })

    iid = inst["instance_id"]
    return {
        "item_id": f"S2-{re.sub(r'[^A-Za-z0-9]', '', iid)[:40]}",
        "source_item_id": iid,
        "stratum": "S2_PATCH_VERDICT",
        "difficulty": "resolved_agent_patch" if resolved else "unresolved_agent_patch",
        "source": "swe-bench-live+submission",
        "dataset_ref": (f"hf://{LIVE_REPO}@{LIVE_REV}[{LIVE_SPLIT}] x "
                        f"gh://{SUBMISSION_REPO}@{SUBMISSION_REV}/{submission}"),
        "repo": inst.get("repo", ""),
        "base_commit": inst.get("base_commit", ""),
        "repo_files": sorted({**cand, **gold}),
        "merged_at": (inst.get("created_at") or "")[:19] + "Z",
        **({"date_gate_cutoff": date_gate} if date_gate else {}),
        "scrubbed": False,
        "license": lic or "UNKNOWN",
        "provider_data_allowlist": _allowlist(lic, allow_copyleft=allow_copyleft),
        # NEVER name the repository or the issue number here. `goal` is prose the
        # reviewer reads as instructions, and "Review a candidate patch for
        # beetbox/beets issue 5495" is a search query: one lookup returns the
        # merged resolution, and the blind patch-verdict measurement this whole
        # stratum exists for becomes a retrieval test with a 100% ceiling. The
        # technical detail the reviewer legitimately needs is already in
        # problem_statement and design_or_diff. check_packet_gates.py enforces this.
        "goal": "Review a candidate patch and decide whether it resolves the "
                "reported issue.",
        "problem_statement": _cap(inst.get("problem_statement"), 12000),
        "design_or_diff": patch,
        "tests_already_run": [],
        "labels": labels,
        "build_notes": {
            "candidate_patch_author": submission,
            "harness_verdict": "resolved" if resolved else "unresolved",
            "fail_to_pass": f2p,
            "pass_to_pass_count": len(inst.get("PASS_TO_PASS") or []),
            "gold_patch_files": sorted(gold),
            "candidate_patch_files": sorted(cand),
            # The SWE-bench harness runs only test files touched by the PR, which
            # is estimated to overstate pass rates by 4-7 points absolute. A
            # control is not-known-broken, never clean; do not score a claim as
            # fabricated merely for flagging one.
            "control_caveat": ("passing controls are not-known-broken, not clean"
                               if resolved else ""),
        },
    }


def _s2_verify_cmd(inst: dict, test_cmd: str, f2p: list[str]) -> str:
    """The SWE-bench-Live harness invocation for this instance's failing tests."""
    iid = inst["instance_id"]
    selected = " ".join(_shell_quote(t) for t in f2p[:12]) or ""
    return (f"docker run --rm starryzhang/sweb.eval.x86_64.{iid.lower()}:latest "
            f"bash -lc {_shell_quote(f'{test_cmd} {selected}'.strip())}")


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


# ================================================================ S3

def arvo_rows(db_path: Path, *, language: Iterable[str] = ("c", "c++"),
              limit: int | None = None) -> list[dict]:
    """Read the pinned arvo.db. Returns rows as dicts.

    Note what is NOT here: every one of the 6,138 rows carries
    (reproduced, patch_located, verified) = (1, 1, 0), so those columns cannot
    select the falsely-patched subset the protocol wants. That selection is an
    execution result, not a query -- see `arvo_pair_commands`.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = ("SELECT * FROM arvo WHERE language IN (%s) AND fix_commit IS NOT NULL "
         "AND patch_url IS NOT NULL ORDER BY localId DESC" % ",".join("?" * len(tuple(language))))
    if limit:
        q += f" LIMIT {int(limit)}"
    try:
        return [dict(r) for r in con.execute(q, tuple(language))]
    finally:
        con.close()


def arvo_pair_commands(row: dict) -> tuple[str, str]:
    """(vulnerable, fixed) reproduction commands for one ARVO case.

    Prefer the row's own recorded reproducer strings; they are verbatim upstream
    and already carry the right image tags.
    """
    lid = row["localId"]
    vul = row.get("reproducer_vul") or f"docker run --rm n132/arvo:{lid}-vul arvo"
    fix = row.get("reproducer_fix") or f"docker run --rm n132/arvo:{lid}-fix arvo"
    return vul.replace("-it ", "").replace(" -it", ""), fix.replace("-it ", "").replace(" -it", "")


ARVO_OLDER_RELEASES = ["v1.0.0", "v2.0.0"]


def arvo_corrections(db_path: Path, older: list[Path]) -> dict[int, dict]:
    """Cases whose recorded fix commit an earlier ARVO release got wrong.

    This is the falsely-patched population, recovered from where it actually
    survives. It is NOT in any released flag column, and it is no longer findable
    by execution: v3 states it "fixed prior false positives", and 0 of 124 faithful
    paired runs showed a fixed image still crashing (95% upper bound ~2.4%, which
    excludes the ARVO paper's 5.4%). What remains is the dataset's own correction
    history -- v1 or v2 recorded commit X as the fix, v3 records Y. X is then a
    real developer patch, published as the fix, that did not close the crash, with
    the adjudicator being ARVO rather than a container we can still run.
    """
    cur = {r["localId"]: r for r in arvo_rows(db_path, limit=None)}
    out: dict[int, dict] = {}
    for path, release in zip(older, ARVO_OLDER_RELEASES):
        if not Path(path).exists():
            continue
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            for r in con.execute("SELECT localId, fix_commit, patch_url FROM arvo"):
                lid = r["localId"]
                new = cur.get(lid)
                if not new or not r["fix_commit"] or lid in out:
                    continue
                if r["fix_commit"] != new["fix_commit"]:
                    out[lid] = {"superseded_release": release,
                                "superseded_fix_commit": r["fix_commit"],
                                "superseded_patch_url": r["patch_url"]}
        finally:
            con.close()
    return out


def fetch_s3_candidates(db_path: Path, *, pool: int, older_dbs: list[Path] | None = None,
                        prefer_corrected: bool = False,
                        stats: BuildStats | None = None) -> list[dict]:
    """Emit the ARVO candidate pool for the paired-execution sweep.

    These are NOT corpus items yet. Whether a case is usable at all depends on the
    vulnerable image reproducing its own recorded crash class, which only execution
    can establish -- see the fidelity gate in build_corpus.py.
    """
    st = stats or BuildStats()
    corrections = arvo_corrections(db_path, older_dbs or []) if older_dbs else {}
    rows = arvo_rows(db_path, limit=None)
    st.considered = len(rows)
    if prefer_corrected:
        rows.sort(key=lambda r: (r["localId"] not in corrections, -int(r["localId"])))
    rows = rows[:pool]

    out = []
    for r in rows:
        vul, fix = arvo_pair_commands(r)
        out.append({
            "localId": r["localId"],
            "project": r["project"],
            "repo_addr": r["repo_addr"],
            "fix_commit": r["fix_commit"],
            "patch_url": r["patch_url"],
            "crash_type": r["crash_type"],
            "sanitizer": r["sanitizer"],
            "fuzz_target": r["fuzz_target"],
            "fuzz_engine": r["fuzz_engine"],
            "severity_text": r["severity"],
            "language": r["language"],
            "report": r["report"],
            "crash_output": _cap(_clean_console(r["crash_output"]), 8000),
            "reproducer_vul": vul,
            "reproducer_fix": fix,
            **corrections.get(r["localId"], {}),
        })
    st.selected = len(out)
    st.notes.append(f"{sum(1 for o in out if o.get('superseded_fix_commit'))} of {len(out)} "
                    f"candidates carry a superseded fix commit "
                    f"({len(corrections)} exist across the whole release history)")
    return out


def upstream_license(http: Http, repo_addr: str) -> tuple[str | None, str]:
    """(SPDX id, human-checkable license URL) for an ARVO case's upstream repository.

    Returns `NOASSERTION` when a license file exists but the host's detector cannot
    classify it. That is a very different state from "no license," and collapsing
    the two would strand harfbuzz, ImageMagick and libdwarf -- all genuinely
    licensed -- in the same bucket as a repository with no terms at all. The
    allowlist still stays empty either way; the URL is what makes the one remaining
    human decision a single click instead of an investigation.
    """
    addr = (repo_addr or "").rstrip("/")
    m = re.match(r"https?://github\.com/([^/]+/[^/.]+)", addr)
    if m:
        slug = m.group(1)
        return _repo_license(http, slug), f"https://github.com/{slug}/blob/HEAD/LICENSE"
    m = re.match(r"https?://(gitlab\.[^/]+|[^/]*gitlab\.com)/(.+?)(?:\.git)?$", addr)
    if m:
        host, proj = m.group(1), m.group(2)
        try:
            d = http.json(
                f"https://{host}/api/v4/projects/{urllib.parse.quote(proj, safe='')}?license=true",
                key=f"license_gl_{re.sub(r'[^A-Za-z0-9]', '_', host + proj)}.json")
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError):
            return None, f"{addr} (license not resolvable)"
        lic = (d or {}).get("license") or {}
        return (lic.get("key") or "").upper() or None, (d or {}).get("license_url") or addr
    return None, f"{addr} (license not resolvable)"


def s3_item_from_sweep(cand: dict, sweep: dict, *, date_gate: str | None,
                       license_id: str | None = None, license_url: str = "",
                       allow_copyleft: bool = False) -> dict:
    """Turn one swept ARVO candidate into an item.

    Two shapes, and the difference is which patch the reviewer is shown:

    `superseded_fix` -- the candidate carries a fix commit an earlier ARVO release
      recorded and a later one replaced. The reviewer reviews the SUPERSEDED patch,
      and the label says it does not close the crash. Adjudication is
      `maintainer_verdict`, not `poc_reproduces`: ARVO's own correction is the
      evidence, and the container that would have proven it directly no longer
      exists, because v3 rebuilt the fixed images against the corrected commit.
      The vulnerable run still corroborates that the bug is real and reproduces
      its recorded class.

    `correct_fix_control` -- the fix was observed closing the crash. No label; the
      correct output is silence.
    """
    if not sweep.get("faithful", sweep.get("vul_crashed")):
        raise ValueError(f"ARVO {cand['localId']}: vulnerable run did not reproduce the "
                         f"recorded crash class; unusable")
    superseded = cand.get("superseded_fix_commit")
    lid = cand["localId"]
    sites = sweep.get("sites") or []
    return {
        "item_id": f"S3-{lid}",
        "source_item_id": str(lid),
        "stratum": "S3_VULN_POC",
        "difficulty": "superseded_fix" if superseded else "correct_fix_control",
        "source": "arvo",
        "dataset_ref": f"gh://n132/ARVO-Meta@{ARVO_RELEASE}/arvo.db sha256:{ARVO_DB_SHA256}",
        "repo": cand["repo_addr"],
        "review_commit": superseded or cand["fix_commit"],
        "repo_files": sorted({s["path"] for s in sites}),
        **({"date_gate_cutoff": date_gate} if date_gate else {}),
        "scrubbed": False,
        # ARVO-Meta itself declares no license, but what actually travels to a
        # provider is the upstream project's patch and its sanitizer output, so
        # the upstream repository's terms are what govern. Unresolvable upstream
        # license -> no provider authorized, rather than a silent default to open.
        "license": license_id or "UNDECLARED",
        "license_url": license_url,
        "provider_data_allowlist": _allowlist(license_id, allow_copyleft=allow_copyleft),
        "goal": f"Review the developer patch for a {cand['crash_type']} in "
                f"{cand['project']} ({cand['fuzz_target']}).",
        "problem_statement": (
            f"A fuzzing harness ({cand['fuzz_engine']}/{cand['fuzz_target']}, "
            f"{cand['sanitizer']}) reported {cand['crash_type']} in {cand['project']}. "
            f"The change under review is the developer's fix.\n\n"
            f"Sanitizer output at the vulnerable revision:\n{cand['crash_output']}"),
        "design_or_diff": sweep.get("patch_text", ""),
        "labels": ([{
            "label_id": "superseded",
            "severity": 0,
            "kind": "memory_safety",
            "description": (
                f"This patch does not close the {cand['crash_type']}. It was recorded as "
                f"the fix in ARVO {cand.get('superseded_release', 'an earlier release')} "
                f"and replaced in {ARVO_RELEASE} by a different commit."),
            "sites": sites or [{"path": cand["project"]}],
            "adjudication": "maintainer_verdict",
        }] if superseded else []),
        "build_notes": {
            "vul_cmd": cand["reproducer_vul"],
            "fix_cmd": cand["reproducer_fix"],
            "vul_crash_class": sweep.get("vul_class", ""),
            "recorded_crash_class": sweep.get("recorded_class", ""),
            "crash_signature": sweep.get("signature", ""),
            "superseded_release": cand.get("superseded_release", ""),
            "superseded_fix_commit": superseded or "",
            "current_fix_commit": cand["fix_commit"],
            "role": "superseded_fix" if superseded else "control",
        },
    }


# ================================================================ S4

_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def first_commit(commit: str | None) -> str:
    """First sha in an ARVO `fix_commit`.

    Some rows hold a newline-separated list of every commit in the fix series --
    kimageformats records 40 of them. Concatenating that into a URL raises
    InvalidURL and kills the build, so normalize at the single point of use.
    """
    m = _SHA_RE.search(commit or "")
    return m.group(0) if m else ""


def fetch_commit_patch(http: Http, repo_addr: str, commit: str) -> str:
    """Patch text for a bare commit sha, when only the repo address is known.

    A superseded fix commit has no usable recorded `patch_url` in some rows, so it
    has to be reconstructed from the upstream repository.
    """
    addr = (repo_addr or "").rstrip("/").removesuffix(".git")
    sha = first_commit(commit)
    if not sha:
        return ""
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)", addr)
    if m:
        return fetch_arvo_patch(http, f"https://github.com/{m.group(1)}/commit/{sha}")
    if "googlesource.com" in addr:
        return fetch_arvo_patch(http, f"{addr}/+/{sha}%5E%21/")
    if "gitlab" in addr or "invent.kde.org" in addr or "foss.heptapod.net" in addr:
        return fetch_arvo_patch(http, f"{addr}/-/commit/{sha}")
    if addr.startswith("https://cgit."):
        return fetch_arvo_patch(http, f"{addr}/patch/?id={sha}")
    return ""


def fetch_arvo_patch(http: Http, patch_url: str) -> str:
    """The developer patch text for an ARVO case.

    83% of cases point at github.com, which serves `.patch` directly. The rest are
    gitweb/cgit/googlesource mirrors; each has its own plaintext form and there is
    no universal one, so an unhandled host returns empty rather than a guess.
    """
    host = urllib.parse.urlparse(patch_url).netloc
    key = f"arvo_patch_{_slug(patch_url)}"
    try:
        if host == "github.com":
            return http.get(patch_url + ".patch", key=key + ".patch").decode("utf-8", "replace")
        if host.endswith("googlesource.com"):
            # `+/<sha>^!/` with ?format=TEXT returns the base64 of the commit diff.
            import base64
            raw = http.get(patch_url + "?format=TEXT", key=key + ".b64")
            return base64.b64decode(raw).decode("utf-8", "replace")
        if host.startswith("cgit.") or "/cgit/" in patch_url:
            return http.get(patch_url.replace("/commit/", "/patch/"),
                            key=key + ".cgit").decode("utf-8", "replace")
        if "gitlab" in host or "invent.kde.org" in host or "foss.heptapod.net" in host:
            return http.get(patch_url + ".patch", key=key + ".patch").decode("utf-8", "replace")
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, ValueError):
        return ""
    return ""


def s4_trap_from_sweep(cand: dict, sweep: dict, patch_text: str, *,
                       date_gate: str | None, license_id: str | None = None,
                       license_url: str = "", allow_copyleft: bool = False) -> dict:
    """A confirmed-correct ARVO fix, inverted into a plausible-but-false finding.

    The packet asserts, as a prior reviewer's concern, that the patch left the
    crash reachable. The sweep already observed the fixed image running the same
    PoC clean, so the assertion is false with a machine-checkable answer. Score
    whether the reviewer promotes it to P0/P1.

    Requires `fix_ok` -- an image that failed to start also produces no sanitizer
    output, and treating that as "the fix works" would invert the label.
    """
    if not (sweep.get("vul_crashed") and sweep.get("fix_ok") and not sweep.get("fix_crashed")):
        raise ValueError(
            f"ARVO {cand['localId']}: trap needs a fix observed clean on a case whose "
            f"vulnerable image crashed; got {sweep}")
    touched = diff_touched(patch_text)
    sites = [{"path": p, "lines": [s, e]} for p, rs in sorted(touched.items()) for s, e in rs]
    lid = cand["localId"]
    return {
        "item_id": f"S4-arvo{lid}",
        "source_item_id": str(lid),
        "stratum": "S4_FP_TRAP",
        "difficulty": "seeded_false_finding",
        "source": "arvo-inverted",
        "dataset_ref": f"gh://n132/ARVO-Meta@{ARVO_RELEASE}/arvo.db sha256:{ARVO_DB_SHA256}",
        "repo": cand["repo_addr"],
        "review_commit": cand["fix_commit"],
        "repo_files": sorted(touched),
        **({"date_gate_cutoff": date_gate} if date_gate else {}),
        "scrubbed": False,
        "license": license_id or "UNDECLARED",
        "license_url": license_url,
        "provider_data_allowlist": _allowlist(license_id, allow_copyleft=allow_copyleft),
        "goal": f"Review the developer fix for a {cand['crash_type']} in {cand['project']}.",
        "problem_statement": (
            f"A fuzzing harness ({cand['fuzz_engine']}/{cand['fuzz_target']}, "
            f"{cand['sanitizer']}) reported {cand['crash_type']} in {cand['project']}. "
            f"The change under review is the developer's fix.\n\n"
            f"Sanitizer output at the vulnerable revision:\n{cand['crash_output']}"),
        "design_or_diff": patch_text,
        # The bait. Framed as an unresolved concern from a previous pass, which is
        # how a real packet would carry it -- and how the reviewer is invited to
        # agree rather than check.
        "known_open_questions": (
            f"A previous reviewer flagged that this patch is incomplete: they believe the "
            f"guard added here is bypassable and that the same {cand['crash_type']} is still "
            f"reachable through the {cand['fuzz_target']} entry point on the patched build. "
            f"That concern was never closed out."),
        "labels": [],
        "trap": {
            "trap_id": f"T-arvo{lid}",
            "assertion": (f"The developer patch does not close the crash; the original PoC "
                          f"still triggers {cand['crash_type']} at the patched revision."),
            "framing": "known_open_question",
            "sites": sites or [{"path": cand["project"]}],
            "ground_truth": "invalid",
            "invalid_reason": "canonical patch closes the recorded crash",
            "verify_cmd": cand["reproducer_fix"],
        },
        "build_notes": {
            "observed_vul_signature": sweep.get("signature", ""),
            "observed_fix_clean": True,
            "vul_cmd": cand["reproducer_vul"],
            "fix_cmd": cand["reproducer_fix"],
        },
    }


# ================================================================ S5

_REVERT_RE = re.compile(r"\brevert\b", re.I)
# Paths whose churn carries no review signal and which every active repository
# touches constantly. Excluded from both the substance test and the follow-up test.
_NOISE_PATH = re.compile(
    r"(^|/)(CHANGELOG|CHANGES|HISTORY|NEWS|AUTHORS|CONTRIBUTORS)([.\w]*)$"
    r"|(^|/)docs?/|\.(md|rst|txt|po|mo)$"
    r"|(^|/)(poetry\.lock|uv\.lock|package-lock\.json|yarn\.lock|Cargo\.lock|go\.sum)$",
    re.I)


def fetch_s5(http: Http, *, repos: list[str], n: int, window_days: int = 90,
             merged_before: str | None = None, date_gate: str | None = None,
             min_files: int = 3, min_churn: int = 40, allow_copyleft: bool = False,
             stats: BuildStats | None = None) -> list[dict]:
    """Merged PRs with no same-file follow-up within `window_days`.

    The filter is deliberately conservative: reject if ANY file the PR touched
    received ANY commit on the default branch inside the window. That over-rejects
    (a changelog edit disqualifies an otherwise clean PR) but it never admits a PR
    that was quietly fixed later, and a false accept is the only error that
    corrupts the stratum -- S5's whole job is that the correct output is silence.
    """
    st = stats or BuildStats()
    # The window must have fully elapsed or "no follow-up in 90 days" is unproven.
    horizon = merged_before or (datetime.now(timezone.utc)
                                - timedelta(days=window_days + 1)).date().isoformat()
    out: list[dict] = []
    # Round-robin, not repo-at-a-time. Draining the first repository puts every
    # null item in one codebase, so a reviewer that happens to over-flag that
    # project's idiom fails all three and the false-positive rate measures the
    # repository instead of the council.
    queues = {repo: _s5_candidates(http, repo, horizon, date_gate) for repo in repos}
    cursors = {repo: 0 for repo in repos}
    while len(out) < n and any(cursors[r] < len(queues[r]) for r in repos):
        for repo in repos:
            if len(out) >= n or cursors[repo] >= len(queues[repo]):
                continue
            pr_num = queues[repo][cursors[repo]]
            cursors[repo] += 1
            st.considered += 1
            item = _s5_try(http, repo, pr_num, window_days, date_gate,
                           min_files=min_files, min_churn=min_churn,
                           allow_copyleft=allow_copyleft)
            if item:
                out.append(item)
    if len(out) < n:
        st.notes.append(f"S5: wanted {n}, found {len(out)} clean PRs across {len(repos)} repos")
    st.selected = len(out)
    return out


def _s5_candidates(http: Http, repo: str, horizon: str, date_gate: str | None) -> list[int]:
    lo = date_gate or "2025-01-01"
    q = (f"repo:{repo} is:pr is:merged merged:{lo}..{horizon} "
         f"-author:app/dependabot -author:app/renovate")
    url = ("https://api.github.com/search/issues?q=" + urllib.parse.quote(q)
           + "&sort=created&order=desc&per_page=50")
    try:
        d = http.json(url, auth=True, key=f"s5_search_{repo.replace('/', '__')}_{lo}_{horizon}.json")
    except urllib.error.HTTPError:
        return []
    return [i["number"] for i in d.get("items", [])]


def _s5_try(http: Http, repo: str, pr_num: int, window_days: int,
            date_gate: str | None, *, min_files: int, min_churn: int,
            allow_copyleft: bool) -> dict | None:
    slug = repo.replace("/", "__")
    pr = http.json(f"https://api.github.com/repos/{repo}/pulls/{pr_num}", auth=True,
                   key=f"s5_pr_{slug}_{pr_num}.json")
    if not pr.get("merged_at"):
        return None
    if _REVERT_RE.search(pr.get("title") or ""):
        return None
    files = http.json(f"https://api.github.com/repos/{repo}/pulls/{pr_num}/files?per_page=100",
                      auth=True, key=f"s5_files_{slug}_{pr_num}.json")
    paths = [f["filename"] for f in files if f.get("status") != "removed"]
    code = [p for p in paths if not _NOISE_PATH.search(p)]
    churn = sum((f.get("additions") or 0) + (f.get("deletions") or 0)
                for f in files if not _NOISE_PATH.search(f["filename"]))
    # A null item has to be indistinguishable from a real one at dispatch time.
    # The clean-window filter naturally selects tiny peripheral PRs, and a one-file
    # typo fix next to a 40-file S1 packet is recognizable as the control -- which
    # would make the false-positive rate measure packet size, not review discipline.
    if len(code) < min_files or churn < min_churn or len(paths) > 25:
        return None

    merged = datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
    since = (merged + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    until = (merged + timedelta(days=window_days)).isoformat().replace("+00:00", "Z")
    base_branch = (pr.get("base") or {}).get("ref") or "main"

    followups: list[str] = []
    # Follow-ups are checked on code paths only. A later changelog or lockfile
    # commit is guaranteed noise in an active repository and rejecting on it
    # leaves nothing but trivia; a later commit to a source file the PR touched
    # is exactly the evidence that disqualifies the item.
    for path in code:
        url = (f"https://api.github.com/repos/{repo}/commits?sha={urllib.parse.quote(base_branch)}"
               f"&path={urllib.parse.quote(path)}&since={since}&until={until}&per_page=20")
        commits = http.json(url, auth=True,
                            key=f"s5_hist_{slug}_{pr_num}_{_slug(path)}.json")
        for c in commits:
            msg = ((c.get("commit") or {}).get("message") or "").splitlines()[:1]
            followups.append(f"{c['sha'][:10]} {msg[0] if msg else ''}")
    if followups:
        return None

    diff = http.get(f"https://api.github.com/repos/{repo}/pulls/{pr_num}", auth=True,
                    accept="application/vnd.github.v3.diff",
                    key=f"s5_diff_{slug}_{pr_num}.diff").decode("utf-8", "replace")

    lic = _repo_license(http, repo)
    return {
        "item_id": f"S5-{re.sub(r'[^A-Za-z0-9]', '', repo)}{pr_num}",
        "source_item_id": f"{repo}#{pr_num}",
        "stratum": "S5_NULL",
        "difficulty": "clean_merged",
        "source": "github",
        "dataset_ref": (f"gh://{repo}/pull/{pr_num} merge={pr.get('merge_commit_sha', '')} "
                        f"harvested={datetime.now(timezone.utc).date().isoformat()}"),
        "repo": repo,
        "base_commit": (pr.get("base") or {}).get("sha", ""),
        "review_commit": (pr.get("head") or {}).get("sha", ""),
        "repo_files": sorted(paths),
        "merged_at": pr["merged_at"],
        **({"date_gate_cutoff": date_gate} if date_gate else {}),
        "scrubbed": False,
        "license": lic or "UNKNOWN",
        "provider_data_allowlist": _allowlist(lic, allow_copyleft=allow_copyleft),
        "goal": pr.get("title") or "",
        "problem_statement": _cap(pr.get("body"), 8000),
        "design_or_diff": _cap(diff, 200000),
        "labels": [],
        "build_notes": {
            "clean_window_days": window_days,
            "followup_commits": [],
            "caveat": ("no observed same-file follow-up within the window; this is a "
                       "construction proxy for 'nothing to find', not a proof"),
        },
    }
