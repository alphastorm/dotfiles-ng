#!/usr/bin/env python3
"""Freeze or offline-verify the provider terms named by LRHE policies.

Fetch exit statuses are 0 for success, 3 for an HTTP/network failure, 4 for an
offline verification failure, and 5 for a local I/O failure.  Argparse uses 2
for usage errors.  Offline mode never constructs an opener or performs a URL
request; ``--offline --self-test`` exercises that same verifier with a temporary
local snapshot when a network-independent smoke test is needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


EXIT_OK = 0
EXIT_FETCH_ERROR = 3
EXIT_VERIFY_ERROR = 4
EXIT_IO_ERROR = 5
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_REDIRECTS = 10
USER_AGENT = (
    "LRHE-Terms-Snapshot/1.0 "
    "(+https://github.com/alphastorm/dotfiles-ng; compliance archive fetch)"
)
MANIFEST_NAME = "MANIFEST.sha256"
MANIFEST_LINE = re.compile(r"^([0-9a-fA-F]{64}) ([ *])(.+)$")

# Component keys are stable filenames; snapshot IDs are the exact values a policy
# in provider-policies.yaml names in `termsSnapshotId`, so a rights record can be
# re-audited against the documents it was actually decided on. Keeping them
# separate avoids pretending one web page is a complete contractual snapshot for a
# route that rests on several documents.
#
# These URLs are the verified source list in HANDOFF.md section 12. Do not
# "correct" them from memory: opencode.ai uses /legal/terms-of-service, and the
# Anthropic consumer article lives on privacy.claude.com, not privacy.anthropic.com.
# A snapshot of the wrong page is worse than no snapshot -- it looks like evidence.
TERMS_SOURCES: dict[str, str] = {
    "opencode-go-docs": "https://opencode.ai/docs/go/",
    "opencode-terms-of-service": "https://opencode.ai/legal/terms-of-service",
    "opencode-privacy-policy": "https://opencode.ai/legal/privacy-policy",
    "anthropic-consumer-model-training": (
        "https://privacy.claude.com/en/articles/10023580-is-my-data-used-for-model-training"
    ),
}

# Keys MUST match `termsSnapshotId` in provider-policies.yaml exactly.
SNAPSHOT_COMPONENTS: dict[str, tuple[str, ...]] = {
    "opencode-terms-2026-03-06__go-docs-2026-07-27": (
        "opencode-go-docs",
        "opencode-terms-of-service",
        "opencode-privacy-policy",
    ),
    "anthropic-consumer-privacy-2026-03-16": (
        "anthropic-consumer-model-training",
    ),
}


class SnapshotError(Exception):
    """A fetch, freeze, or verification operation failed safely."""


class RecordingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow ordinary redirects while retaining and constraining every hop."""

    def __init__(self) -> None:
        super().__init__()
        self.redirects: list[dict[str, Any]] = []

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        target = urllib.parse.urljoin(request.full_url, new_url)
        source_scheme = urllib.parse.urlparse(request.full_url).scheme.lower()
        target_scheme = urllib.parse.urlparse(target).scheme.lower()
        if target_scheme not in {"http", "https"}:
            raise SnapshotError(
                f"redirect from {request.full_url} used unsupported scheme {target_scheme!r}"
            )
        if source_scheme == "https" and target_scheme != "https":
            raise SnapshotError(
                f"refusing HTTPS downgrade redirect from {request.full_url} to {target}"
            )
        if len(self.redirects) >= MAX_REDIRECTS:
            raise SnapshotError(
                f"redirect limit ({MAX_REDIRECTS}) exceeded while fetching {request.full_url}"
            )
        self.redirects.append(
            {
                "http_status": code,
                "from_url": request.full_url,
                "to_url": target,
            }
        )
        return super().redirect_request(
            request, file_pointer, code, message, headers, target
        )


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fetch_component(component: str, timeout: float) -> dict[str, Any]:
    url = TERMS_SOURCES[component]
    redirects = RecordingRedirectHandler()
    opener = urllib.request.build_opener(redirects)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )

    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.getcode()
            final_url = response.geturl()
            if status != 200:
                raise SnapshotError(
                    f"{url} returned HTTP {status}; no snapshot was written"
                )
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise SnapshotError(
                    f"{url} exceeded the {MAX_RESPONSE_BYTES}-byte snapshot limit"
                )
            content_type = response.headers.get("Content-Type")
    except urllib.error.HTTPError as exc:
        raise SnapshotError(
            f"{url} returned HTTP {exc.code} ({exc.reason}); no snapshot was written"
        ) from exc
    except SnapshotError:
        raise
    except (socket.timeout, TimeoutError) as exc:
        raise SnapshotError(
            f"{url} timed out after {timeout:g} seconds; no snapshot was written"
        ) from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise SnapshotError(
                f"{url} timed out after {timeout:g} seconds; no snapshot was written"
            ) from exc
        raise SnapshotError(
            f"{url} could not be fetched ({reason}); no snapshot was written"
        ) from exc
    except OSError as exc:
        raise SnapshotError(
            f"{url} could not be fetched ({exc}); no snapshot was written"
        ) from exc

    sha256 = _sha256_bytes(body)
    metadata = {
        "url": url,
        "final_url": final_url,
        "sha256": sha256,
        "byte_length": len(body),
        "fetched_at": _utc_now(),
        "http_status": status,
        "content_type": content_type,
        "redirects": redirects.redirects,
        "user_agent": USER_AGENT,
    }
    return {
        "component": component,
        "body": body,
        "metadata": metadata,
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _destination_paths(
    terms_root: Path, snapshot_id: str, component: str
) -> tuple[Path, Path]:
    directory = terms_root / snapshot_id
    return directory / f"{component}.body", directory / f"{component}.metadata.json"


def _metadata_bytes(metadata: dict[str, Any]) -> bytes:
    return (
        json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _preflight_freeze(
    terms_root: Path,
    fetched: list[tuple[str, dict[str, Any]]],
) -> set[tuple[str, str]]:
    reused: set[tuple[str, str]] = set()
    for snapshot_id, result in fetched:
        component = result["component"]
        body_path, metadata_path = _destination_paths(
            terms_root, snapshot_id, component
        )
        body_exists = body_path.exists()
        metadata_exists = metadata_path.exists()
        if body_exists != metadata_exists:
            raise SnapshotError(
                f"refusing partial existing snapshot for {snapshot_id}/{component}"
            )
        if not body_exists:
            continue
        if body_path.is_symlink() or metadata_path.is_symlink():
            raise SnapshotError(
                f"refusing symlinked existing snapshot for {snapshot_id}/{component}"
            )
        existing_sha256 = _sha256_file(body_path)
        fetched_sha256 = result["metadata"]["sha256"]
        if existing_sha256 != fetched_sha256:
            raise SnapshotError(
                f"snapshot {snapshot_id}/{component} is immutable: existing SHA-256 "
                f"{existing_sha256} differs from fetched {fetched_sha256}"
            )
        try:
            existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SnapshotError(
                f"cannot verify existing sidecar {metadata_path}: {exc}"
            ) from exc
        if (
            not isinstance(existing_metadata, dict)
            or existing_metadata.get("sha256") != existing_sha256
            or existing_metadata.get("url") != TERMS_SOURCES[component]
        ):
            raise SnapshotError(
                f"existing sidecar {metadata_path} does not describe its frozen body"
            )
        reused.add((snapshot_id, component))
    return reused


def _manifest_files(terms_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in terms_root.rglob("*"):
        if path == terms_root / MANIFEST_NAME or not path.is_file():
            continue
        if path.is_symlink():
            raise SnapshotError(f"refusing symlink in snapshot tree: {path}")
        files.append(path)
    return sorted(files, key=lambda path: path.relative_to(terms_root).as_posix())


def _write_manifest(terms_root: Path) -> int:
    lines = []
    for path in _manifest_files(terms_root):
        relative = path.relative_to(terms_root).as_posix()
        lines.append(f"{_sha256_file(path)}  {relative}\n")
    if not lines:
        raise SnapshotError("refusing to write an empty terms manifest")
    _atomic_write(terms_root / MANIFEST_NAME, "".join(lines).encode("utf-8"))
    return len(lines)


def freeze_snapshots(
    terms_root: Path,
    snapshot_ids: tuple[str, ...],
    timeout: float,
) -> int:
    # Fetch every source before writing any result.  A timeout or 404 therefore
    # cannot leave a plausible-looking manifest for only part of a policy.
    fetched: list[tuple[str, dict[str, Any]]] = []
    for snapshot_id in snapshot_ids:
        for component in SNAPSHOT_COMPONENTS[snapshot_id]:
            result = _fetch_component(component, timeout)
            fetched.append((snapshot_id, result))

    reused = _preflight_freeze(terms_root, fetched)
    for snapshot_id, result in fetched:
        component = result["component"]
        body_path, metadata_path = _destination_paths(
            terms_root, snapshot_id, component
        )
        if (snapshot_id, component) not in reused:
            _atomic_write(body_path, result["body"])
            _atomic_write(metadata_path, _metadata_bytes(result["metadata"]))
        metadata = result["metadata"]
        print(
            json.dumps(
                {
                    "snapshot_id": snapshot_id,
                    "component": component,
                    "url": metadata["url"],
                    "http_status": metadata["http_status"],
                    "sha256": metadata["sha256"],
                    "byte_length": metadata["byte_length"],
                    "reused": (snapshot_id, component) in reused,
                },
                separators=(",", ":"),
            )
        )
    count = _write_manifest(terms_root)
    print(f"wrote {terms_root / MANIFEST_NAME} with {count} entries")
    return count


def _parse_manifest(terms_root: Path) -> dict[PurePosixPath, str]:
    manifest = terms_root / MANIFEST_NAME
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SnapshotError(f"cannot read manifest {manifest}: {exc}") from exc
    if not lines:
        raise SnapshotError(f"manifest {manifest} is empty")

    entries: dict[PurePosixPath, str] = {}
    for line_number, line in enumerate(lines, start=1):
        match = MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise SnapshotError(
                f"{manifest}:{line_number}: not shasum -a 256 manifest syntax"
            )
        expected, marker, raw_name = match.groups()
        if marker != " ":
            raise SnapshotError(
                f"{manifest}:{line_number}: binary-mode '*' marker is not canonical"
            )
        relative = PurePosixPath(raw_name)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise SnapshotError(
                f"{manifest}:{line_number}: unsafe manifest path {raw_name!r}"
            )
        if relative == PurePosixPath(MANIFEST_NAME):
            raise SnapshotError(f"{manifest}:{line_number}: manifest cannot hash itself")
        if relative in entries:
            raise SnapshotError(
                f"{manifest}:{line_number}: duplicate path {raw_name!r}"
            )
        entries[relative] = expected.lower()
    return entries


def _validate_sidecars(
    terms_root: Path,
    entries: dict[PurePosixPath, str],
    expected_snapshot_ids: tuple[str, ...],
) -> None:
    entry_names = {path.as_posix() for path in entries}
    body_names = {name for name in entry_names if name.endswith(".body")}
    metadata_names = {
        name for name in entry_names if name.endswith(".metadata.json")
    }
    expected_metadata = {
        f"{name[:-len('.body')]}.metadata.json" for name in body_names
    }
    expected_bodies = {
        f"{name[:-len('.metadata.json')]}.body" for name in metadata_names
    }
    if metadata_names != expected_metadata or body_names != expected_bodies:
        raise SnapshotError("every frozen body must have exactly one manifested sidecar")

    for raw_name in sorted(metadata_names):
        relative = PurePosixPath(raw_name)
        path = terms_root.joinpath(*relative.parts)
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SnapshotError(f"cannot read sidecar {path}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise SnapshotError(f"sidecar {path} must contain a JSON object")
        required = {
            "url",
            "sha256",
            "byte_length",
            "fetched_at",
            "http_status",
        }
        missing = sorted(required - metadata.keys())
        if missing:
            raise SnapshotError(
                f"sidecar {path} is missing: {', '.join(missing)}"
            )
        if metadata["http_status"] != 200:
            raise SnapshotError(f"sidecar {path} records non-200 HTTP status")
        if not isinstance(metadata["byte_length"], int) or metadata["byte_length"] < 0:
            raise SnapshotError(f"sidecar {path} has invalid byte_length")
        if not isinstance(metadata["fetched_at"], str):
            raise SnapshotError(f"sidecar {path} has invalid fetched_at")
        try:
            datetime.fromisoformat(metadata["fetched_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise SnapshotError(f"sidecar {path} has invalid fetched_at") from exc

        body_name = f"{raw_name[:-len('.metadata.json')]}.body"
        body_path = terms_root.joinpath(*PurePosixPath(body_name).parts)
        body_sha256 = _sha256_file(body_path)
        if metadata["sha256"] != body_sha256:
            raise SnapshotError(f"sidecar {path} SHA-256 does not match {body_path}")
        if metadata["byte_length"] != body_path.stat().st_size:
            raise SnapshotError(f"sidecar {path} byte_length does not match {body_path}")

    for snapshot_id in expected_snapshot_ids:
        for component in SNAPSHOT_COMPONENTS[snapshot_id]:
            body_name = f"{snapshot_id}/{component}.body"
            sidecar_name = f"{snapshot_id}/{component}.metadata.json"
            if body_name not in entry_names or sidecar_name not in entry_names:
                raise SnapshotError(
                    f"snapshot {snapshot_id} is missing component {component}"
                )
            sidecar_path = terms_root / snapshot_id / f"{component}.metadata.json"
            metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if metadata.get("url") != TERMS_SOURCES[component]:
                raise SnapshotError(
                    f"sidecar {sidecar_path} URL does not match component {component}"
                )


def verify_snapshots(
    terms_root: Path,
    expected_snapshot_ids: tuple[str, ...],
) -> int:
    entries = _parse_manifest(terms_root)
    for relative, expected in entries.items():
        path = terms_root.joinpath(*relative.parts)
        if path.is_symlink():
            raise SnapshotError(f"manifest path is a symlink: {path}")
        if not path.is_file():
            raise SnapshotError(f"manifest path is missing: {path}")
        actual = _sha256_file(path)
        if actual != expected:
            raise SnapshotError(
                f"SHA-256 mismatch for {path}: expected {expected}, got {actual}"
            )

    actual_files = {
        path.relative_to(terms_root).as_posix()
        for path in _manifest_files(terms_root)
    }
    manifested_files = {path.as_posix() for path in entries}
    extra = sorted(actual_files - manifested_files)
    if extra:
        raise SnapshotError(
            "snapshot tree contains unmanifested files: " + ", ".join(extra)
        )

    _validate_sidecars(terms_root, entries, expected_snapshot_ids)
    return len(entries)


def _run_offline_self_test() -> int:
    # The fixture is intentionally built locally and then passed through the
    # production verifier.  No alternate permissive test-only verification path
    # can therefore conceal a broken or network-dependent --offline mode.
    with tempfile.TemporaryDirectory(prefix="lrhe-terms-offline-") as temporary:
        root = Path(temporary) / "terms"
        snapshot_id = "offline-self-test"
        component = "local-fixture"
        body = b"LRHE offline terms verification fixture\n"
        body_path, metadata_path = _destination_paths(root, snapshot_id, component)
        metadata = {
            "url": "https://example.invalid/offline-fixture",
            "final_url": "https://example.invalid/offline-fixture",
            "sha256": _sha256_bytes(body),
            "byte_length": len(body),
            "fetched_at": "2026-07-27T00:00:00Z",
            "http_status": 200,
            "content_type": "text/plain",
            "redirects": [],
            "user_agent": USER_AGENT,
        }
        _atomic_write(body_path, body)
        _atomic_write(metadata_path, _metadata_bytes(metadata))
        _write_manifest(root)
        count = verify_snapshots(root, ())
        print(f"offline self-test verified {count} manifest entries")
    return EXIT_OK


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Fetch immutable provider-terms snapshots, or verify existing "
            "snapshots without making a network call."
        ),
        epilog=(
            "fetch exit codes: 0 success; 3 HTTP/network failure; "
            "4 verification failure; 5 local I/O failure; 2 usage error"
        ),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="verify existing bodies and sidecars against MANIFEST.sha256; never use network",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="with --offline, verify a locally constructed temporary snapshot",
    )
    parser.add_argument(
        "--terms-dir",
        type=Path,
        default=here / "terms",
        help="snapshot root (default: %(default)s)",
    )
    parser.add_argument(
        "--snapshot-id",
        action="append",
        choices=tuple(sorted(SNAPSHOT_COMPONENTS)),
        help="snapshot to fetch or require; repeatable (default: all)",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=30.0,
        help="per-request timeout in seconds (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.self_test and not args.offline:
        parser.error("--self-test requires --offline")
    if args.self_test:
        try:
            return _run_offline_self_test()
        except (OSError, UnicodeError, SnapshotError) as exc:
            print(f"offline self-test failed: {exc}", file=sys.stderr)
            return EXIT_VERIFY_ERROR

    snapshot_ids = tuple(args.snapshot_id or sorted(SNAPSHOT_COMPONENTS))
    if args.offline:
        try:
            count = verify_snapshots(args.terms_dir, snapshot_ids)
        except (OSError, UnicodeError, json.JSONDecodeError, SnapshotError) as exc:
            print(f"offline verification failed: {exc}", file=sys.stderr)
            return EXIT_VERIFY_ERROR
        print(
            f"offline verification passed: {count} manifest entries in {args.terms_dir}"
        )
        return EXIT_OK

    try:
        freeze_snapshots(args.terms_dir, snapshot_ids, args.timeout)
    except SnapshotError as exc:
        print(f"snapshot fetch failed: {exc}", file=sys.stderr)
        return EXIT_FETCH_ERROR
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"snapshot write failed: {exc}", file=sys.stderr)
        return EXIT_IO_ERROR
    except KeyboardInterrupt:
        print("snapshot fetch interrupted; no complete snapshot was certified", file=sys.stderr)
        return EXIT_FETCH_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
