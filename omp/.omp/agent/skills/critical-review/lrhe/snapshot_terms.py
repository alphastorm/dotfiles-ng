#!/usr/bin/env python3
"""Freeze or verify non-replicable provider-terms snapshots.

Fetch exits are:

* 0 — success
* 3 — fetch/network failure
* 4 — offline verification failure
* 5 — local I/O error

`argparse` keeps 2 for usage issues.  `--offline --self-test` exercises the
same verifier with a temporary local fixture and never performs any network
request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
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

# Keep HTTP, HTTPS, and file sources explicit because a "file" override is only
# for local, offline evidence.  Mixing override and URL fetch in one implicit
# branch silently blurs reproducibility.
VALID_COMPONENT_SOURCE_SCHEMES = ("file", "http", "https")

# Component keys are stable snapshot IDs names; policy files map `termsSnapshotId`
# to these groups and later check their snapshots by these exact names.
TERMS_SOURCES: dict[str, str] = {
    "opencode-go-docs": "https://opencode.ai/docs/go/",
    "opencode-terms-of-service": "https://opencode.ai/legal/terms-of-service",
    "opencode-privacy-policy": "https://opencode.ai/legal/privacy-policy",
    "opencode-zen-privacy": "https://opencode.ai/docs/zen/",
    "anthropic-consumer-model-training": (
        "https://privacy.claude.com/en/articles/10023580-is-my-data-used-for-model-training"
    ),
}

# Keys MUST match `termsSnapshotId` values in provider-policies.yaml exactly.
SNAPSHOT_COMPONENTS: dict[str, tuple[str, ...]] = {
    "opencode-terms-2026-03-06__go-docs-2026-07-27": (
        "opencode-go-docs",
        "opencode-terms-of-service",
        "opencode-privacy-policy",
        "opencode-zen-privacy",
    ),
    "anthropic-consumer-privacy-2026-03-16": (
        "anthropic-consumer-model-training",
    ),
}
DEFAULT_SNAPSHOT_IDS = tuple(sorted(SNAPSHOT_COMPONENTS))


class SnapshotError(Exception):
    """An immutable snapshot operation failed safely."""


class RecordingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow supported redirects while enforcing safety invariants."""

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
        return super().redirect_request(request, file_pointer, code, message, headers, target)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_bytes(data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _metadata_bytes(metadata: dict[str, Any]) -> bytes:
    # Keep pretty UTF-8 JSON for review: one-byte-per-visual-change diffs are the
    # only durable audit trail for legal evidence.
    return json.dumps(metadata, indent=2).encode("utf-8") + b"\n"


def _resolve_component_sources(raw_sources: list[str] | None) -> dict[str, str]:
    # Keep source overrides explicit and opt-in.  Missing overrides use the
    # canonical URL declared by TERMS_SOURCES.
    component_sources = dict(TERMS_SOURCES)
    if not raw_sources:
        return component_sources

    expected = ", ".join(sorted(component_sources))
    for raw in raw_sources:
        if "=" not in raw:
            raise ValueError(f"--component-source value {raw!r} is not COMPONENT=SOURCE")
        component, source = (part.strip() for part in raw.split("=", 1))
        if not component:
            raise ValueError("component name in --component-source cannot be empty")
        if component not in component_sources:
            raise ValueError(
                f"unknown component {component!r} for --component-source; expected one of: {expected}"
            )
        if not source:
            raise ValueError(
                f"--component-source for {component!r} must include a source URL or path"
            )
        component_sources[component] = source
    return component_sources


def _fetch_component(
    component: str,
    timeout: float,
    source: str,
    canonical_url: str,
) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(source)
    scheme = (parsed.scheme or "file").lower()
    if scheme not in VALID_COMPONENT_SOURCE_SCHEMES:
        raise SnapshotError(
            f"component {component} has unsupported source scheme {scheme!r} in {source!r}; "
            f"expected one of {', '.join(VALID_COMPONENT_SOURCE_SCHEMES)}"
        )

    metadata: dict[str, Any] = {
        "url": canonical_url,
        "fetched_at": _utc_now(),
        "http_status": 200,
    }
    if source != canonical_url:
        metadata["source"] = source

    if scheme == "file":
        if parsed.netloc and parsed.netloc not in {"", "localhost"}:
            raise SnapshotError(
                f"{source} for {component} uses unsupported file URL host {parsed.netloc!r}"
            )
        path = Path(parsed.path or source).expanduser()
        if not path.is_absolute():
            path = Path(source).expanduser()

        if not path.exists():
            raise SnapshotError(f"{source} for {component} does not exist")
        if not path.is_file():
            raise SnapshotError(f"{source} for {component} is not a regular file")
        if path.is_symlink():
            raise SnapshotError(
                f"{source} for {component} is a symlink, which bypasses offline reproducibility"
            )

        try:
            body = path.read_bytes()
        except OSError as exc:
            raise SnapshotError(
                f"{source} for {component} could not be read ({exc})"
            ) from exc

        if len(body) > MAX_RESPONSE_BYTES:
            raise SnapshotError(
                f"{source} for {component} exceeded the {MAX_RESPONSE_BYTES}-byte snapshot limit"
            )

        metadata.update(
            {
                "final_url": canonical_url,
                "sha256": _sha256_bytes(body),
                "byte_length": len(body),
                "content_type": mimetypes.guess_type(path.as_posix())[0] or "text/plain",
                "redirects": [],
                "user_agent": USER_AGENT,
            }
        )
        return {"component": component, "body": body, "metadata": metadata}

    # Keep HTTP/HTTPS in the existing deterministic fetch path: opener recreated
    # per run so each invocation is independently bounded by redirect and timeout.
    redirects = RecordingRedirectHandler()
    opener = urllib.request.build_opener(redirects)
    request = urllib.request.Request(
        source,
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
                    f"{source} returned HTTP {status}; no snapshot was written"
                )
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise SnapshotError(
                    f"{source} exceeded the {MAX_RESPONSE_BYTES}-byte snapshot limit"
                )
            content_type = response.headers.get("Content-Type")
    except urllib.error.HTTPError as exc:
        raise SnapshotError(
            f"{source} returned HTTP {exc.code} ({exc.reason}); no snapshot was written"
        ) from exc
    except (socket.timeout, TimeoutError) as exc:
        raise SnapshotError(
            f"{source} timed out after {timeout:g} seconds; no snapshot was written"
        ) from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise SnapshotError(
                f"{source} timed out after {timeout:g} seconds; no snapshot was written"
            ) from exc
        raise SnapshotError(
            f"{source} could not be fetched ({reason}); no snapshot was written"
        ) from exc
    except OSError as exc:
        raise SnapshotError(f"{source} could not be fetched ({exc}); no snapshot was written") from exc

    metadata.update(
        {
            "final_url": final_url,
            "sha256": _sha256_bytes(body),
            "byte_length": len(body),
            "content_type": content_type,
            "redirects": redirects.redirects,
            "user_agent": USER_AGENT,
        }
    )
    return {"component": component, "body": body, "metadata": metadata}


def _destination_paths(
    terms_root: Path,
    snapshot_id: str,
    component: str,
) -> tuple[Path, Path]:
    snapshot_dir = terms_root / snapshot_id
    return snapshot_dir / f"{component}.body", snapshot_dir / f"{component}.metadata.json"


def _preflight_freeze(
    terms_root: Path,
    fetched: list[tuple[str, dict[str, Any]]],
) -> set[tuple[str, str]]:
    # Preflight allows reusing an existing body+metadata pair only when the pair
    # is unchanged and self-consistent.  Any partial or altered file set blocks the
    # freeze so we do not silently write an apparently complete tree over stale
    # evidence.
    reused: set[tuple[str, str]] = set()
    for snapshot_id, result in fetched:
        component = result["component"]
        body_path, metadata_path = _destination_paths(terms_root, snapshot_id, component)

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

        if existing_metadata.get("http_status") != 200:
            raise SnapshotError(
                f"existing sidecar {metadata_path} records non-200 status"
            )

        reused.add((snapshot_id, component))

    return reused


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
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


def _manifest_files(terms_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in terms_root.rglob("*"):
        if path == terms_root / MANIFEST_NAME:
            continue
        if path.is_symlink():
            raise SnapshotError(f"refusing symlink in snapshot tree: {path}")
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(terms_root).as_posix())


def _write_manifest(terms_root: Path) -> int:
    lines: list[str] = []
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
    component_sources: dict[str, str],
) -> int:
    # Fetch every requested component before mutating anything.  A transient DNS
    # or timeout is not allowed to leave a partially refreshed manifest behind.
    fetched: list[tuple[str, dict[str, Any]]] = []
    for snapshot_id in snapshot_ids:
        for component in SNAPSHOT_COMPONENTS[snapshot_id]:
            result = _fetch_component(
                component,
                timeout,
                component_sources[component],
                TERMS_SOURCES[component],
            )
            fetched.append((snapshot_id, result))

    reused = _preflight_freeze(terms_root, fetched)
    for snapshot_id, result in fetched:
        component = result["component"]
        body_path, metadata_path = _destination_paths(
            terms_root,
            snapshot_id,
            component,
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
            raise SnapshotError(f"{manifest}:{line_number}: duplicate path {raw_name!r}")

        entries[relative] = expected.lower()

    return entries


def _validate_sidecars(
    terms_root: Path,
    entries: dict[PurePosixPath, str],
    expected_snapshot_ids: tuple[str, ...],
) -> None:
    entry_names = {path.as_posix() for path in entries}
    body_names = {name for name in entry_names if name.endswith(".body")}
    metadata_names = {name for name in entry_names if name.endswith(".metadata.json")}

    expected_metadata = {name[:-len(".body")] + ".metadata.json" for name in body_names}
    expected_bodies = {name[:-len(".metadata.json")] + ".body" for name in metadata_names}
    if metadata_names != expected_metadata or body_names != expected_bodies:
        raise SnapshotError("every frozen body must have exactly one manifested sidecar")

    for raw_name in sorted(metadata_names):
        relative = PurePosixPath(raw_name)
        sidecar = terms_root.joinpath(*relative.parts)
        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SnapshotError(f"cannot read sidecar {sidecar}: {exc}") from exc

        if not isinstance(metadata, dict):
            raise SnapshotError(f"sidecar {sidecar} must contain a JSON object")

        required = {
            "url",
            "sha256",
            "byte_length",
            "fetched_at",
            "http_status",
            "final_url",
            "content_type",
            "redirects",
            "user_agent",
        }
        missing = sorted(required - metadata.keys())
        if missing:
            raise SnapshotError(f"sidecar {sidecar} is missing: {', '.join(missing)}")

        if metadata["http_status"] != 200:
            raise SnapshotError(f"sidecar {sidecar} records non-200 HTTP status")

        if not isinstance(metadata["byte_length"], int) or metadata["byte_length"] < 0:
            raise SnapshotError(f"sidecar {sidecar} has invalid byte_length")

        if not isinstance(metadata["fetched_at"], str):
            raise SnapshotError(f"sidecar {sidecar} has invalid fetched_at")

        try:
            datetime.fromisoformat(metadata["fetched_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise SnapshotError(f"sidecar {sidecar} has invalid fetched_at") from exc

        body_name = raw_name[:-len(".metadata.json")] + ".body"
        body_path = terms_root.joinpath(*PurePosixPath(body_name).parts)
        body_sha256 = _sha256_file(body_path)
        if metadata["sha256"] != body_sha256:
            raise SnapshotError(f"sidecar {sidecar} SHA-256 does not match {body_path}")
        if metadata["byte_length"] != body_path.stat().st_size:
            raise SnapshotError(f"sidecar {sidecar} byte_length does not match {body_path}")

        if metadata["url"] not in TERMS_SOURCES.values():
            raise SnapshotError(
                f"sidecar {sidecar} uses unknown canonical url {metadata['url']!r}"
            )

    for snapshot_id in expected_snapshot_ids:
        for component in SNAPSHOT_COMPONENTS[snapshot_id]:
            body_name = f"{snapshot_id}/{component}.body"
            metadata_name = f"{snapshot_id}/{component}.metadata.json"
            if body_name not in entry_names or metadata_name not in entry_names:
                raise SnapshotError(
                    f"snapshot {snapshot_id} is missing component {component}"
                )
            sidecar_path = terms_root / snapshot_id / f"{component}.metadata.json"
            component_metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if component_metadata.get("url") != TERMS_SOURCES[component]:
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
    # Self-test builds a local tree and verifies it by the production verifier.
    # This catches verifier drift without any network dependency.
    fixtures = {
        "opencode-go-docs": b"LRHE offline terms verification fixture for opencode-go-docs\n",
        "opencode-terms-of-service": (
            b"LRHE offline terms verification fixture for opencode-terms-of-service\n"
        ),
        "opencode-privacy-policy": b"LRHE offline terms verification fixture for privacy-policy\n",
        "opencode-zen-privacy": b"LRHE offline terms verification fixture for zen-privacy\n",
    }

    with tempfile.TemporaryDirectory(prefix="lrhe-terms-offline-") as temporary:
        terms_root = Path(temporary) / "terms"
        snapshot_id = "opencode-terms-2026-03-06__go-docs-2026-07-27"
        for component, body in fixtures.items():
            body_path, metadata_path = _destination_paths(
                terms_root,
                snapshot_id,
                component,
            )
            metadata = {
                "url": TERMS_SOURCES[component],
                "final_url": TERMS_SOURCES[component],
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

        _write_manifest(terms_root)
        count = verify_snapshots(terms_root, (snapshot_id,))
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
            "snapshots without using the network."
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
        help="with --offline, verify a temporary local snapshot fixture",
    )
    parser.add_argument(
        "--terms-dir",
        type=Path,
        # Snapshots are evidence records (potentially changed by upstream policy
        # updates), so keep them in the private corpus location where the canonical
        # evidence already lives.
        default=Path.home() / ".omp/agent/skills/critical-review/lrhe-data/terms",
        help="snapshot root (default: %(default)s)",
    )
    parser.add_argument(
        "--snapshot-id",
        action="append",
        choices=DEFAULT_SNAPSHOT_IDS,
        help="snapshot to fetch or require; repeatable (default: all)",
    )
    parser.add_argument(
        "--component-source",
        action="append",
        metavar="COMPONENT=SOURCE",
        help=(
            "override a component fetch source; repeatable, for example "
            "opencode-go-docs=file:/tmp/opencode-go-docs.html"
        ),
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

    snapshot_ids = tuple(args.snapshot_id or DEFAULT_SNAPSHOT_IDS)

    if args.offline:
        try:
            count = verify_snapshots(args.terms_dir, snapshot_ids)
        except (OSError, UnicodeError, json.JSONDecodeError, SnapshotError) as exc:
            print(f"offline verification failed: {exc}", file=sys.stderr)
            return EXIT_VERIFY_ERROR

        print(f"offline verification passed: {count} manifest entries in {args.terms_dir}")
        return EXIT_OK

    try:
        component_sources = _resolve_component_sources(args.component_source)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        freeze_snapshots(args.terms_dir, snapshot_ids, args.timeout, component_sources)
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
