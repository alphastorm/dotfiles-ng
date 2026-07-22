---
name: github-issues-export
description: Export every issue and issue comment from the current GitHub repository to a verified ~/Desktop/issues.zip archive when the user asks for an issue export, backup, copy, or zip.
---

# GitHub Issues Export

Create a complete, read-only snapshot of a GitHub repository's issues in one ZIP on the user's Desktop. A direct request to export issues authorizes GitHub reads and creation or replacement of exactly the requested local ZIP; it never authorizes a GitHub mutation.

## Defaults

- Repository: the GitHub repository for the current working directory.
- Output: `~/Desktop/issues.zip`.
- Scope: open and closed issues, their current metadata and Markdown bodies, and every visible issue comment. Pull requests and pull-request-only review comments are excluded.
- Optional repository argument: when the user explicitly supplies `OWNER/REPO`, pass it with `--repo` instead of resolving the current checkout.
- Optional output argument: honor an explicitly requested `.zip` path with `--output`; otherwise keep the stable Desktop path above so repeated exports are easy.

## Execute

Run the bundled deterministic exporter with the process tool from the user's current working directory:

```sh
python3 "$HOME/.omp/agent/skills/github-issues-export/export_issues.py" --output "$HOME/Desktop/issues.zip"
```

Add `--repo OWNER/REPO` only when the user explicitly selected another repository. Add a different `--output PATH` only when the user explicitly requested one. Do not replace the exporter with an ad hoc API loop, generated script, or lossy `gh issue list` output.

The exporter uses the authenticated GitHub CLI, filters pull requests out of the REST issues endpoint, fetches every API page, checks per-issue comment completeness, writes atomically, and validates the resulting ZIP before replacing the destination. It includes:

- one Markdown file per issue under `issues/`;
- `all-issues.md` for easy reading and search;
- `issues.json` with raw GitHub issue and comment payloads;
- `manifest.json` with repository, timestamp, counts, and entry inventory.

## Failure behavior

Fail closed. If repository resolution, authentication, API pagination, JSON decoding, comment completeness, ZIP writing, or archive validation fails, report the exact concise error and do not claim an export. Do not mutate issues, comments, labels, milestones, projects, repository settings, or any other GitHub state. Do not print credentials or authentication material.

## Report

After a successful run, report:

- the clickable absolute ZIP path;
- repository name;
- open, closed, and total issue counts;
- comment count;
- byte size and SHA-256 printed by the exporter.

Do not claim success based only on process exit; use the exporter's post-write validation summary.
