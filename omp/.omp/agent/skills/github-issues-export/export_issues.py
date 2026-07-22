#!/usr/bin/env python3
"""Export a GitHub repository's issues and comments to a verified ZIP archive."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote


REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ISSUE_URL_RE = re.compile(r"/issues/(\d+)$")


class ExportError(RuntimeError):
    """Expected export failure with a user-facing message."""


def run_gh(arguments: list[str]) -> str:
    command = ["gh", *arguments]
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        reason = detail[-1] if detail else f"exit status {result.returncode}"
        raise ExportError(f"GitHub CLI failed: {reason}")
    return result.stdout


def parse_json(raw: str, context: str) -> object:
    try:
        return cast(object, json.loads(raw))
    except json.JSONDecodeError as error:
        raise ExportError(f"GitHub returned invalid JSON for {context}: {error}") from error


def resolve_repository(explicit_repository: str | None) -> str:
    if explicit_repository:
        repository = explicit_repository.strip()
    else:
        repository = run_gh(
            ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
        ).strip()

    if not REPOSITORY_RE.fullmatch(repository):
        raise ExportError(
            f"Could not resolve an unambiguous OWNER/REPO repository: {repository!r}"
        )
    return repository


def fetch_pages(endpoint: str, context: str) -> list[dict[str, Any]]:
    parsed_pages = parse_json(run_gh(["api", "--paginate", "--slurp", endpoint]), context)
    if not isinstance(parsed_pages, list):
        raise ExportError(f"GitHub returned an unexpected page envelope for {context}")

    pages = cast(list[object], parsed_pages)
    records: list[dict[str, Any]] = []
    for parsed_page in pages:
        if not isinstance(parsed_page, list):
            raise ExportError(f"GitHub returned an unexpected page for {context}")
        page = cast(list[object], parsed_page)
        for parsed_record in page:
            if not isinstance(parsed_record, dict):
                raise ExportError(f"GitHub returned a non-object record for {context}")
            records.append(cast(dict[str, Any], parsed_record))
    return records


def api_repository_path(repository: str) -> str:
    owner, name = repository.split("/", 1)
    return f"repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def collect_issues(repository: str) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    repository_path = api_repository_path(repository)
    issue_records = fetch_pages(
        f"{repository_path}/issues?state=all&per_page=100&sort=created&direction=asc",
        "issues",
    )
    issues = [record for record in issue_records if "pull_request" not in record]
    issues.sort(key=lambda issue: int(issue["number"]))

    issue_numbers = {int(issue["number"]) for issue in issues}
    raw_comments = fetch_pages(
        f"{repository_path}/issues/comments?per_page=100&sort=created&direction=asc",
        "issue comments",
    )
    comments_by_issue: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for comment in raw_comments:
        match = ISSUE_URL_RE.search(str(comment.get("issue_url", "")))
        if not match:
            raise ExportError("GitHub returned an issue comment without a valid issue URL")
        issue_number = int(match.group(1))
        if issue_number in issue_numbers:
            comments_by_issue[issue_number].append(comment)

    for comments in comments_by_issue.values():
        comments.sort(key=lambda comment: (str(comment.get("created_at", "")), int(comment["id"])))

    incomplete: list[str] = []
    for issue in issues:
        issue_number = int(issue["number"])
        expected = int(issue.get("comments", 0))
        actual = len(comments_by_issue[issue_number])
        if expected != actual:
            incomplete.append(f"#{issue_number}: expected {expected}, fetched {actual}")
    if incomplete:
        sample = "; ".join(incomplete[:5])
        suffix = "" if len(incomplete) <= 5 else f"; plus {len(incomplete) - 5} more"
        raise ExportError(f"Issue comment export was incomplete ({sample}{suffix})")

    return issues, dict(comments_by_issue)


def user_login(value: Any) -> str:
    if not isinstance(value, dict):
        return "unknown"
    login = cast(dict[str, Any], value).get("login")
    return f"@{login}" if login else "unknown"


def label_names(issue: dict[str, Any]) -> list[str]:
    labels_value = issue.get("labels")
    if not isinstance(labels_value, list):
        return []
    labels = cast(list[object], labels_value)
    return [
        str(cast(dict[str, Any], label).get("name", ""))
        for label in labels
        if isinstance(label, dict)
    ]


def assignee_names(issue: dict[str, Any]) -> list[str]:
    assignees_value = issue.get("assignees")
    if not isinstance(assignees_value, list):
        return []
    return [user_login(assignee) for assignee in cast(list[object], assignees_value)]


def markdown_value(value: Any) -> str:
    return str(value) if value not in (None, "") else "none"


def render_issue(
    repository: str,
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
) -> str:
    number = int(issue["number"])
    title = str(issue.get("title") or "")
    labels = label_names(issue)
    assignees = assignee_names(issue)
    milestone_value = issue.get("milestone")
    milestone = cast(dict[str, Any], milestone_value) if isinstance(milestone_value, dict) else None
    milestone_title = milestone.get("title") if milestone is not None else None
    body = issue.get("body")

    lines = [
        f"# #{number} {title}",
        "",
        f"- Repository: `{repository}`",
        f"- URL: {markdown_value(issue.get('html_url'))}",
        f"- State: `{markdown_value(issue.get('state'))}`",
        f"- State reason: `{markdown_value(issue.get('state_reason'))}`",
        f"- Author: {user_login(issue.get('user'))}",
        f"- Created: `{markdown_value(issue.get('created_at'))}`",
        f"- Updated: `{markdown_value(issue.get('updated_at'))}`",
        f"- Closed: `{markdown_value(issue.get('closed_at'))}`",
        f"- Labels: {', '.join(f'`{label}`' for label in labels) if labels else 'none'}",
        f"- Assignees: {', '.join(assignees) if assignees else 'none'}",
        f"- Milestone: {markdown_value(milestone_title)}",
        "",
        "## Body",
        "",
        str(body) if body not in (None, "") else "_No body._",
        "",
        f"## Comments ({len(comments)})",
        "",
    ]

    if not comments:
        lines.extend(["_No comments._", ""])
    else:
        for index, comment in enumerate(comments, start=1):
            lines.extend(
                [
                    f"### Comment {index} — {user_login(comment.get('user'))} — {markdown_value(comment.get('created_at'))}",
                    "",
                    f"Source: {markdown_value(comment.get('html_url'))}",
                    "",
                    str(comment.get("body")) if comment.get("body") not in (None, "") else "_No body._",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def filename_slug(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").lower()
    return (slug[:80].rstrip("-") or "issue")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_entries(
    repository: str,
    issues: list[dict[str, Any]],
    comments_by_issue: dict[int, list[dict[str, Any]]],
    exported_at: datetime,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    entries: dict[str, bytes] = {}
    issue_documents: list[str] = []
    json_issues: list[dict[str, Any]] = []

    for issue in issues:
        number = int(issue["number"])
        comments = comments_by_issue.get(number, [])
        document = render_issue(repository, issue, comments)
        filename = f"issues/{number:06d}-{filename_slug(str(issue.get('title') or ''))}.md"
        entries[filename] = document.encode("utf-8")
        issue_documents.append(document)
        json_issues.append({"issue": copy.deepcopy(issue), "comments": copy.deepcopy(comments)})

    open_count = sum(1 for issue in issues if issue.get("state") == "open")
    closed_count = sum(1 for issue in issues if issue.get("state") == "closed")
    comment_count = sum(len(comments) for comments in comments_by_issue.values())
    exported_at_text = exported_at.isoformat().replace("+00:00", "Z")

    combined_header = (
        f"# GitHub issues — {repository}\n\n"
        f"Exported: `{exported_at_text}`  \n"
        f"Issues: {len(issues)} ({open_count} open, {closed_count} closed)  \n"
        f"Comments: {comment_count}\n\n"
    )
    entries["all-issues.md"] = (
        combined_header + "\n---\n\n".join(issue_documents)
    ).encode("utf-8")

    json_payload = {
        "format_version": 1,
        "repository": repository,
        "exported_at": exported_at_text,
        "issues": json_issues,
    }
    entries["issues.json"] = (
        json.dumps(json_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    manifest = {
        "format_version": 1,
        "repository": repository,
        "exported_at": exported_at_text,
        "counts": {
            "issues": len(issues),
            "open": open_count,
            "closed": closed_count,
            "comments": comment_count,
        },
        "entries": sorted([*entries, "manifest.json"]),
    }
    entries["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return entries, manifest


def write_verified_zip(output: Path, entries: dict[str, bytes], manifest: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.is_dir():
        raise ExportError(f"Output path is a directory: {output}")

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for name in sorted(entries):
                archive.writestr(name, entries[name])

        with zipfile.ZipFile(temporary_path, mode="r") as archive:
            bad_entry = archive.testzip()
            if bad_entry is not None:
                raise ExportError(f"ZIP integrity validation failed at {bad_entry}")
            actual_entries = sorted(archive.namelist())
            expected_entries = manifest["entries"]
            if actual_entries != expected_entries:
                raise ExportError("ZIP entry inventory validation failed")
            archived_manifest = json.loads(archive.read("manifest.json"))
            if archived_manifest != manifest:
                raise ExportError("ZIP manifest validation failed")

        os.replace(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export all GitHub issues and issue comments to a verified ZIP archive."
    )
    parser.add_argument(
        "--repo",
        metavar="OWNER/REPO",
        help="GitHub repository; defaults to the current checkout",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("~/Desktop/issues.zip"),
        help="ZIP destination (default: ~/Desktop/issues.zip)",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if shutil.which("gh") is None:
        raise ExportError("GitHub CLI `gh` is not installed or not on PATH")

    repository = resolve_repository(arguments.repo)
    output = arguments.output.expanduser().absolute()
    if output.suffix.lower() != ".zip":
        raise ExportError(f"Output path must end in .zip: {output}")

    issues, comments_by_issue = collect_issues(repository)
    entries, manifest = build_entries(repository, issues, comments_by_issue, utc_now())
    write_verified_zip(output, entries, manifest)

    counts = manifest["counts"]
    print(f"Created: {output}")
    print(f"Repository: {repository}")
    print(
        f"Issues: {counts['issues']} ({counts['open']} open, {counts['closed']} closed)"
    )
    print(f"Comments: {counts['comments']}")
    print(f"Size: {output.stat().st_size} bytes")
    print(f"SHA-256: {sha256_file(output)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExportError as error:
        print(f"Export failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as error:
        print(f"Export failed: {error}", file=sys.stderr)
        raise SystemExit(1)
