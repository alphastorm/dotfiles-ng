#!/usr/bin/env python3
"""Atomic preparation API for critical-review reviewer Tasks.

A structurally valid reviewer dispatch is one where the material under review,
the reviewer's standing, and the bytes actually transmitted are all the same
thing that some earlier step committed to. This module is the only path that
produces one.

`prepare` is the operator path. It resolves the selected roster first, validates
that every reviewer can receive evidence in the proposed subject shape, freezes
the subject, resolves standing, builds the dispatch envelope, and runs the same
verification used by the Task policy gate. No provider is called by this module.
The lower-level freeze, resolution, and envelope functions are internal policy
stages used by `prepare` and focused tests; they are not public CLI commands and
cannot produce a second operator route.

`freeze_subject` binds the assurance scope, immutable packet, optional frozen
review record, and optional resolver-owned panel manifest. A repository subject
also binds a clean HEAD commit and every exact reviewed regular file. A
repository without a full commit and file list is a mutable working tree and is
refused.

`resolve_assignments` derives every standing field from the fixed live
authority. Initial standing comes only from the manifest re-resolved during
freeze; focused standing names exactly one configured critic; targeted
refutation uses the complete fixed pool. Receipts bind the subject, authority,
both resolvers, and all schemas.

Canonical task construction re-reads every bound byte, re-resolves standing,
and embeds the verified scope and packet bytes. `verify-task` is internal and is
what the policy extension calls immediately before transmission. It rehashes
the envelope, repeats evidence compatibility, and rebuilds the canonical Task
input. No provider-ready envelope can be created through the CLI except by
`prepare`.

Every generated artifact is strict, hash-bound, atomic, read-only, and never
overwritten. The private receipt schema is generated from the live authority and
must sit beside it: a matrix that no longer matches its checked schema stops
dispatch rather than resolving a tuple the schema never admitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast

try:
    from jsonschema import Draft202012Validator

    import qualification
except ModuleNotFoundError:
    venv_python = Path(__file__).resolve().parent / ".venv/bin/python"
    if (
        __name__ != "__main__"
        or not venv_python.is_file()
        or Path(sys.executable).resolve() == venv_python.resolve()
    ):
        raise
    os.execv(venv_python, [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])

HERE = Path(__file__).resolve().parent

SUBJECT_SCHEMA_VERSION = 2
RECEIPT_SCHEMA_VERSION = 1
ENVELOPE_SCHEMA_VERSION = 1

# Both resolver modules are named by fixed skill-relative literals, checked
# against where the file really sits. A receipt free to name whichever copy
# produced it could authenticate a forgery against itself.
RESOLVER_RELATIVE_PATH = "lrhe/review_dispatch.py"
SUBJECT_SCHEMA_RELATIVE_PATH = "lrhe/frozen-subject.schema.json"
PANEL_SCHEMA_RELATIVE_PATH = "lrhe/panel-selection.schema.json"
ENVELOPE_SCHEMA_RELATIVE_PATH = "lrhe/review-dispatch-envelope.schema.json"
# Generated, not written by hand, and co-located with the authority it was
# generated from. A schema beside a different authority describes a different
# matrix, so the pair is checked rather than assumed.
RECEIPT_SCHEMA_FILENAME = "resolver-receipt.schema.json"

# The live authority is not a flag. The caller chooses the subject, the lead
# family, the review class, and the reviewers; which qualification record grants
# standing is not a caller decision at any step.
LIVE_AUTHORITY = qualification.DEFAULT_QUALIFICATION

REPOSITORY_KIND = "repository"
PACKET_ONLY_KIND = "packet-only"
SUBJECT_KINDS = (PACKET_ONLY_KIND, REPOSITORY_KIND)
# Named so it can be refused by name. A working tree is mutable, so it is not a
# third subject kind that happens to be unsupported -- it is the thing freezing
# exists to exclude.
WORKING_TREE_KIND = "working-tree"

FOCUSED = "focused"
INITIAL = "initial"
TARGETED_REFUTER = "targeted-refuter"
REVIEW_CLASSES = (FOCUSED, INITIAL, TARGETED_REFUTER)
# The two classes whose roster is the protocol's own, so the record's dispatch
# gate decides whether they may run at all.
RECORD_BOUND_CLASSES = (INITIAL, TARGETED_REFUTER)

# `focused` is the one selection class this module adds to the resolver's
# vocabulary, and only because a focused review is a single configured critic
# dispatched outside a council -- it is neither unconditional council membership
# nor a specialist seat nor a conditional lane.
FOCUSED_SELECTION_CLASS = "focused"
SELECTION_CLASSES = (FOCUSED_SELECTION_CLASS, *qualification.SELECTION_CLASSES)

DISPATCH_MARKER = "CRITICAL_REVIEW_DISPATCH_V1"
RECEIPT_MARKER = "CRITICAL_REVIEW_RESOLVER_RECEIPT_V1"
SUBJECT_DIGEST_DOMAIN = "omp.critical-review.frozen-subject.v2"
# The Task call's intent line is generated, never carried from the caller. The
# verifier approves an exact payload, so a caller-chosen intent would leave one
# field of the compared object outside the comparison.
DISPATCH_TASK_INTENT = "Dispatching resolved reviewers"
INLINE_EVIDENCE_FORMAT = "critical-review-complete-inline-evidence-v1"

# The only tree entries a reviewer can be pointed at. A committed symlink is a
# clean, immutable entry whose *target* is neither: following one would let a
# read leave the frozen subject entirely, so mode is checked and not just
# presence. A gitlink (160000) is refused for the same reason -- the submodule's
# contents are not in this commit at all.
REGULAR_BLOB_MODES = ("100644", "100755")

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_COMMIT_HEX = re.compile(r"[0-9a-f]{40}")


class DispatchError(ValueError):
    """The dispatch cannot be shown to be structurally valid, so it does not run."""


# --------------------------------------------------------------------------
# bytes


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path, label: str) -> bytes:
    if not path.is_file():
        raise DispatchError(f"{label} is not a readable file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DispatchError(f"{label} is not readable: {exc}") from exc


def _read_text(path: Path, label: str) -> tuple[str, str]:
    """Return one snapshot's text and digest, taken from the same read."""

    raw = _read_bytes(path, label)
    try:
        return raw.decode("utf-8"), _digest_bytes(raw)
    except UnicodeDecodeError as exc:
        raise DispatchError(f"{label} is not UTF-8 text: {exc}") from exc


def _resolved(path: Path, label: str) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError as exc:
        raise DispatchError(f"{label} path cannot be resolved: {exc}") from exc


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise DispatchError(f"{field} must be a lowercase 64-hex SHA-256 digest, not {value!r}")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _artifact_text(document: Mapping[str, object]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def _write_once(path: Path, text: str, label: str) -> Path:
    """Persist one artifact exactly once, atomically and read-only.

    The durability, atomicity, exclusive-create, and read-only guarantees are the
    resolver's existing ones rather than a second copy of them; the pre-check
    only exists so the ordinary collision names the artifact kind, while the
    delegate still closes the race.
    """

    resolved = path.expanduser()
    if resolved.exists():
        raise DispatchError(f"refusing to overwrite existing {label}: {resolved}")
    try:
        return qualification.write_manifest(resolved, text)
    except qualification.QualificationError as exc:
        raise DispatchError(f"{label} cannot be written: {exc}") from exc


def _validate(document: object, schema: Mapping[str, object], label: str) -> Mapping[str, object]:
    validator = Draft202012Validator(cast("dict[str, object]", dict(schema)))
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise DispatchError(f"{label} is not schema-valid at {location}: {first.message}")
    if not isinstance(document, Mapping):  # pragma: no cover -- the schema pins the type
        raise DispatchError(f"{label} must be a JSON object")
    return cast(Mapping[str, object], document)


def _load_json(path: Path, label: str) -> tuple[Mapping[str, object], str]:
    raw = _read_bytes(path, label)
    try:
        value: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DispatchError(f"{label} cannot be parsed: {exc}") from exc
    if not isinstance(value, Mapping):
        raise DispatchError(f"{label} must be a JSON object")
    return cast(Mapping[str, object], value), _digest_bytes(raw)


# --------------------------------------------------------------------------
# authority, resolver, and schema bindings


def resolver_binding() -> tuple[str, str]:
    """Name this module's authoritative path and digest for a receipt."""

    module = Path(__file__).resolve()
    if "/".join(module.parts[-2:]) != RESOLVER_RELATIVE_PATH:
        raise DispatchError(
            f"dispatch resolver runs from {module}, which is not "
            f"{RESOLVER_RELATIVE_PATH!r} under a skill root; a receipt cannot bind an "
            "authority that is not there"
        )
    return RESOLVER_RELATIVE_PATH, _digest_bytes(_read_bytes(module, "dispatch resolver"))


def bind_public_schema(relative: str) -> tuple[Path, str, Mapping[str, object]]:
    """Bind one checked public schema's bytes beside this module."""

    path = HERE / Path(relative).name
    document, digest = _load_json(path, f"{relative}")
    Draft202012Validator.check_schema(cast("dict[str, object]", dict(document)))
    return path, digest, document


def bind_live_authority() -> tuple[Path, str, Mapping[str, object]]:
    """Read, hash, and validate the one authority every command uses."""

    return qualification.bind_qualification(LIVE_AUTHORITY)


def receipt_schema_path(authority_path: Path) -> Path:
    return authority_path.parent / RECEIPT_SCHEMA_FILENAME


def bind_receipt_schema(
    authority_path: Path, document: Mapping[str, object]
) -> tuple[Path, str, Mapping[str, object]]:
    """Bind the checked receipt schema, refusing any drift from the live matrix.

    The schema is generated from the authority, so a lane, profile, or standing
    that changed in `qualification.yml` without regenerating it would leave a
    schema admitting tuples the resolver no longer produces -- or refusing ones
    it does. Neither is a schema worth validating against, so the pair is
    compared byte for byte and dispatch stops until they agree.
    """

    path = receipt_schema_path(authority_path)
    generated = receipt_schema_text(document)
    raw = _read_bytes(path, "generated receipt schema")
    if raw != generated.encode("utf-8"):
        raise DispatchError(
            f"{path} is not the receipt schema generated from {authority_path}; regenerate "
            "it from the live authority before resolving or dispatching"
        )
    return path, _digest_bytes(raw), json.loads(generated)


# --------------------------------------------------------------------------
# frozen subject


@dataclass(frozen=True)
class FrozenSubject:
    """One immutable statement of what is under review."""

    kind: str
    scope_path: Path
    scope_sha256: str
    packet_path: Path
    packet_sha256: str
    record_path: Path | None
    record_sha256: str | None
    manifest_path: Path | None
    manifest_sha256: str | None
    manifest_schema_sha256: str | None
    repository_path: Path | None
    subject_commit: str | None
    files: tuple[str, ...]

    @property
    def subject_digest(self) -> str:
        """Digest the subject's whole identity, not one of its components.

        Files are already sorted, unique, and free of newlines, so the joined
        payload is injective: no two distinct subjects render the same bytes.
        """

        payload = "\n".join(
            (
                SUBJECT_DIGEST_DOMAIN,
                self.kind,
                self.scope_sha256,
                self.packet_sha256,
                self.record_sha256 or "-",
                self.manifest_sha256 or "-",
                self.manifest_schema_sha256 or "-",
                self.subject_commit or "-",
                *self.files,
            )
        )
        return _digest_bytes((payload + "\n").encode("utf-8"))


@dataclass(frozen=True)
class VerifiedSubject:
    """One frozen subject whose every bound byte was just re-read and rehashed."""

    subject: FrozenSubject
    scope_text: str
    packet_text: str
    packet: Mapping[str, object]
    record: Mapping[str, object] | None
    manifest: Mapping[str, object] | None


@dataclass(frozen=True)
class BoundManifest:
    """One schema-valid panel manifest snapshot and the bytes that bind it."""

    path: Path
    sha256: str
    schema_sha256: str
    document: Mapping[str, object]


def _repository_files(values: Sequence[object]) -> tuple[str, ...]:
    """Validate one exact repository-relative file list.

    Duplicates are refused rather than collapsed: a caller who named a path
    twice does not agree with this resolver about what the subject is, and
    silently agreeing for them hides that.
    """

    if not values:
        raise DispatchError(
            "a repository subject needs at least one bound file; an empty file list "
            "freezes nothing and would authorize review of the whole tree"
        )
    files: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise DispatchError(f"repository file {value!r} is not a path")
        if value != value.strip():
            raise DispatchError(f"repository file {value!r} is padded with whitespace")
        if value.startswith("/"):
            raise DispatchError(f"repository file {value!r} must be repository-relative")
        if "\\" in value or "\n" in value or "\0" in value:
            raise DispatchError(
                f"repository file {value!r} contains a separator, newline, or NUL that no "
                "repository-relative POSIX path in a bound list may carry"
            )
        parts = value.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise DispatchError(
                f"repository file {value!r} has an empty, current, or parent component; a "
                "bound path must name exactly one tracked file"
            )
        files.append(value)
    duplicates = sorted({name for name in files if files.count(name) > 1})
    if duplicates:
        raise DispatchError(f"repository file list names {duplicates} more than once")
    return tuple(sorted(files))


def _git(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise DispatchError(f"git is not runnable: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise DispatchError(f"git {' '.join(args)} failed in {repo}: {detail}")
    return completed.stdout


def verify_repository(repository_path: Path, subject_commit: str, files: Sequence[str]) -> None:
    """Prove the repository still holds exactly the frozen subject.

    Four separate facts, because each fails on its own: the caller named the
    commit that is actually checked out, nothing in the tree differs from it,
    every bound path is present in that commit rather than deleted, expanded from
    a directory, or never there -- and every present entry is a regular blob.

    The last one is not pedantry about file modes. A symlink is committed, clean,
    and immutable as a tree entry while its target is none of those, so a
    reviewer told to read it reads bytes this commit never fixed, possibly from
    outside the repository. A gitlink is worse: the submodule's contents are not
    in this commit at all. Both are refused rather than followed.
    """

    toplevel = _git(repository_path, "rev-parse", "--show-toplevel").strip()
    if _resolved(Path(toplevel), "repository top level") != repository_path:
        raise DispatchError(
            f"{repository_path} is not a repository root; its top level is {toplevel}, and a "
            "repository-relative file list resolved from a subdirectory names other files"
        )
    head = _git(repository_path, "rev-parse", "HEAD").strip()
    if head != subject_commit:
        raise DispatchError(
            f"repository {repository_path} is at {head}, not the frozen subject commit "
            f"{subject_commit}"
        )
    status = _git(repository_path, "status", "--porcelain")
    if status.strip():
        raise DispatchError(
            f"repository {repository_path} has modified or untracked files, so its working "
            f"tree is not commit {subject_commit}: "
            f"{sorted(line[3:] for line in status.splitlines() if line.strip())}"
        )
    listed = _git(
        repository_path,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        subject_commit,
        "--",
        *files,
    )
    bound: dict[str, tuple[str, str]] = {}
    for entry in listed.split("\0"):
        if not entry:
            continue
        meta, tab, name = entry.partition("\t")
        fields = meta.split(" ")
        if not tab or not name or len(fields) != 3:
            raise DispatchError(
                f"git ls-tree emitted an entry this resolver cannot read: {entry!r}"
            )
        bound[name] = (fields[0], fields[1])
    requested = set(files)
    missing = sorted(requested - set(bound))
    if missing:
        raise DispatchError(
            f"commit {subject_commit} does not bind {missing}; a frozen subject cannot name a "
            "path the commit does not contain"
        )
    extra = sorted(set(bound) - requested)
    if extra:
        raise DispatchError(
            f"bound paths expand to {extra} in commit {subject_commit}; name each reviewed "
            "file exactly, never a directory"
        )
    irregular = sorted(
        f"{name} ({mode} {kind})"
        for name, (mode, kind) in bound.items()
        if kind != "blob" or mode not in REGULAR_BLOB_MODES
    )
    if irregular:
        raise DispatchError(
            f"commit {subject_commit} binds {irregular} as something other than a regular "
            f"file (modes {list(REGULAR_BLOB_MODES)}); a symlink or submodule entry does not "
            "fix the bytes a reviewer would read"
        )


def _inline_evidence(packet: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """Verify the packet carries self-authenticating UTF-8 evidence bytes.

    A path, prose summary, or unstructured diff cannot prove which bytes an
    inline reviewer received. The versioned bundle makes the producer assert
    completeness while this verifier proves that every named artifact's bytes
    are present and match their declared digest.
    """

    bundle = packet.get("design_or_diff")
    if not isinstance(bundle, Mapping):
        raise DispatchError(
            "inline evidence requires design_or_diff to be a "
            f"{INLINE_EVIDENCE_FORMAT!r} bundle containing complete evidence bytes; "
            "a path, summary, or unstructured excerpt never counts as inline evidence"
        )
    if set(bundle) != {"format", "artifacts"} or bundle.get("format") != INLINE_EVIDENCE_FORMAT:
        raise DispatchError(
            "inline evidence design_or_diff must contain exactly "
            f"format={INLINE_EVIDENCE_FORMAT!r} and artifacts"
        )
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)) or not artifacts:
        raise DispatchError("inline evidence artifacts must be a nonempty array of embedded bytes")
    result: list[Mapping[str, object]] = []
    names: set[str] = set()
    for index, value in enumerate(artifacts):
        if not isinstance(value, Mapping) or set(value) != {"name", "sha256", "content"}:
            raise DispatchError(
                f"inline evidence artifact {index} must contain exactly name, sha256, and content"
            )
        name = value.get("name")
        content = value.get("content")
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise DispatchError(f"inline evidence artifact {index} has an invalid name {name!r}")
        if name in names:
            raise DispatchError(f"inline evidence artifact name {name!r} appears more than once")
        if not isinstance(content, str) or not content:
            raise DispatchError(f"inline evidence artifact {name!r} embeds no UTF-8 content bytes")
        declared = _digest(value.get("sha256"), f"inline evidence artifact {name!r} sha256")
        actual = _digest_bytes(content.encode("utf-8"))
        if declared != actual:
            raise DispatchError(
                f"inline evidence artifact {name!r} content digests to {actual}, not {declared}"
            )
        names.add(name)
        result.append(cast(Mapping[str, object], value))
    return tuple(result)


def validate_evidence_compatibility(
    subject: FrozenSubject,
    packet: Mapping[str, object],
    evidence_deliveries: Sequence[str],
) -> None:
    """Fail closed unless every selected reviewer can inspect this subject."""

    deliveries = tuple(evidence_deliveries)
    if not deliveries:
        raise DispatchError("evidence compatibility cannot be checked for an empty reviewer set")
    unknown = sorted(set(deliveries) - {"inline", "repository"})
    if unknown:
        raise DispatchError(f"selected reviewers declare unknown evidence delivery modes {unknown}")
    if "repository" in deliveries and subject.kind != REPOSITORY_KIND:
        raise DispatchError(
            "a selected reviewer with evidence_delivery=repository requires "
            "subject_kind=repository, an immutable subject_commit, a clean tree, and every "
            "reviewed path bound in that commit; packet-only evidence is incompatible"
        )
    if subject.kind == PACKET_ONLY_KIND and any(mode != "inline" for mode in deliveries):
        raise DispatchError(
            "a packet-only subject requires every selected reviewer to use inline evidence"
        )
    if "repository" in deliveries and (
        subject.repository_path is None or subject.subject_commit is None or not subject.files
    ):
        raise DispatchError(
            "repository evidence requires a repository root, immutable subject commit, and "
            "nonempty exact file list"
        )
    if "inline" in deliveries:
        _inline_evidence(packet)


def bind_panel_manifest(path: Path) -> BoundManifest:
    """Read and schema-bind one immutable panel selection manifest."""

    resolved = _resolved(path, "panel selection manifest")
    document, digest = _load_json(resolved, "panel selection manifest")
    _, schema_sha256, schema = bind_public_schema(PANEL_SCHEMA_RELATIVE_PATH)
    _validate(document, schema, "panel selection manifest")
    return BoundManifest(resolved, digest, schema_sha256, document)


def _validate_panel_manifest(
    binding: BoundManifest,
    subject: FrozenSubject,
    packet: Mapping[str, object],
    record: Mapping[str, object] | None,
) -> None:
    """Re-resolve and validate the roster whose evidence modes gate freezing."""

    manifest = binding.document
    if record is None or subject.record_path is None or subject.record_sha256 is None:
        raise DispatchError("a panel manifest requires the frozen review record that selected it")
    if manifest.get("mode") != INITIAL:
        raise DispatchError(
            f"freeze accepts an initial council panel manifest, not mode {manifest.get('mode')!r}"
        )
    expected_bindings = (
        ("reviewRecordPath", str(subject.record_path)),
        ("reviewRecordSha256", subject.record_sha256),
        ("packetPath", str(subject.packet_path)),
        ("packetSha256", subject.packet_sha256),
    )
    for field, expected in expected_bindings:
        if manifest.get(field) != expected:
            raise DispatchError(
                f"panel manifest {field} is {manifest.get(field)!r}, not the frozen {expected!r}"
            )
    authority_path, authority_sha256, authority = bind_live_authority()
    qualification_path, qualification_sha256 = qualification.qualification_binding()
    for field, expected in (
        ("authorityPath", str(authority_path)),
        ("authoritySha256", authority_sha256),
        ("qualificationPath", qualification_path),
        ("qualificationSha256", qualification_sha256),
    ):
        if manifest.get(field) != expected:
            raise DispatchError(
                f"panel manifest {field} is {manifest.get(field)!r}, not the live {expected!r}"
            )
    expected_manifest = qualification.select_full_council(
        authority,
        record,
        packet,
        lead_family=cast(str, manifest["leadFamily"]),
        record_path=subject.record_path,
        record_sha256=subject.record_sha256,
        packet_path=subject.packet_path,
        packet_sha256=subject.packet_sha256,
        authority_path=authority_path,
        authority_sha256=authority_sha256,
    )
    if dict(manifest) != expected_manifest:
        raise DispatchError(
            "panel manifest does not equal a fresh roster resolution from its bound record, "
            "packet, lead family, and live authority; it is stale or was altered"
        )
    selected = cast(Sequence[object], manifest["selected"])
    deliveries = [
        cast(str, cast(Mapping[str, object], row)["evidence_delivery"]) for row in selected
    ]
    validate_evidence_compatibility(subject, packet, deliveries)


def _bind_material(
    scope_path: Path, packet_path: Path, record_path: Path | None
) -> tuple[
    str,
    str,
    str,
    str,
    Mapping[str, object],
    Mapping[str, object] | None,
    str | None,
    Path,
    Path,
    Path | None,
]:
    """Bind the scope, packet, and optional record from one read of each."""

    scope_resolved = _resolved(scope_path, "assurance scope")
    scope_text, scope_sha256 = _read_text(scope_resolved, "assurance scope")
    if not scope_text.strip():
        raise DispatchError(f"assurance scope {scope_resolved} is empty")
    if record_path is None:
        packet_resolved = _resolved(packet_path, "packet")
        packet_text, packet_sha256 = _read_text(packet_resolved, "packet")
        # Parsed from the bytes already digested rather than from a second read
        # of the path, so the recorded digest always describes the packet this
        # resolution actually understood. The sibling resolver reuses private
        # helpers across these two modules the same way.
        packet = qualification._parse_packet_text(packet_text)
        return (
            scope_text,
            scope_sha256,
            packet_text,
            packet_sha256,
            packet,
            None,
            None,
            scope_resolved,
            packet_resolved,
            None,
        )
    record_resolved, record_sha256, record = qualification.bind_record(record_path)
    packet_resolved, packet_sha256, packet = qualification.bind_packet(
        packet_path, record_resolved, record_sha256
    )
    packet_text = packet_resolved.read_text(encoding="utf-8")
    if _digest_bytes(packet_text.encode("utf-8")) != packet_sha256:
        raise DispatchError(
            f"packet {packet_resolved} changed while it was being bound; freeze it before "
            "resolving anything against it"
        )
    return (
        scope_text,
        scope_sha256,
        packet_text,
        packet_sha256,
        packet,
        record,
        record_sha256,
        scope_resolved,
        packet_resolved,
        record_resolved,
    )


def freeze_subject(
    *,
    scope_path: Path,
    packet_path: Path,
    record_path: Path | None = None,
    manifest_path: Path | None = None,
    _manifest_binding: BoundManifest | None = None,
    repository_path: Path | None = None,
    subject_commit: str | None = None,
    files: Sequence[str] = (),
) -> VerifiedSubject:
    """Bind one immutable subject, or refuse to pretend a mutable one is frozen."""

    if repository_path is None:
        if subject_commit is not None or files:
            raise DispatchError(
                "a packet-only subject has no repository, so it cannot carry a commit or a "
                "bound file list; name a repository root to freeze a repository subject"
            )
        kind = PACKET_ONLY_KIND
        repository_resolved: Path | None = None
        bound_files: tuple[str, ...] = ()
    else:
        if subject_commit is None or not files:
            raise DispatchError(
                f"a repository without a commit and an exact file list is a "
                f"{WORKING_TREE_KIND} subject, which is mutable and is never frozen; supply "
                "the full commit and every reviewed path"
            )
        if not isinstance(subject_commit, str) or not _COMMIT_HEX.fullmatch(subject_commit):
            raise DispatchError(
                f"subject commit {subject_commit!r} must be a lowercase 40-hex commit; an "
                "abbreviated or uppercase revision does not identify one object exactly"
            )
        kind = REPOSITORY_KIND
        repository_resolved = _resolved(repository_path, "repository")
        bound_files = _repository_files(list(files))
        verify_repository(repository_resolved, subject_commit, bound_files)
    (
        scope_text,
        scope_sha256,
        packet_text,
        packet_sha256,
        packet,
        record,
        record_sha256,
        scope_resolved,
        packet_resolved,
        record_resolved,
    ) = _bind_material(scope_path, packet_path, record_path)
    if manifest_path is not None and _manifest_binding is not None:
        raise DispatchError(
            "freeze received both a manifest path and an in-memory manifest binding"
        )
    manifest_binding = (
        _manifest_binding
        if _manifest_binding is not None
        else bind_panel_manifest(manifest_path)
        if manifest_path is not None
        else None
    )
    subject = FrozenSubject(
        kind=kind,
        scope_path=scope_resolved,
        scope_sha256=scope_sha256,
        packet_path=packet_resolved,
        packet_sha256=packet_sha256,
        record_path=record_resolved,
        record_sha256=record_sha256,
        manifest_path=manifest_binding.path if manifest_binding is not None else None,
        manifest_sha256=manifest_binding.sha256 if manifest_binding is not None else None,
        manifest_schema_sha256=(
            manifest_binding.schema_sha256 if manifest_binding is not None else None
        ),
        repository_path=repository_resolved,
        subject_commit=subject_commit,
        files=bound_files,
    )
    if manifest_binding is not None:
        _validate_panel_manifest(manifest_binding, subject, packet, record)
    return VerifiedSubject(
        subject=subject,
        scope_text=scope_text,
        packet_text=packet_text,
        packet=packet,
        record=record,
        manifest=manifest_binding.document if manifest_binding is not None else None,
    )


def subject_document(subject: FrozenSubject) -> dict[str, object]:
    document: dict[str, object] = {
        "schemaVersion": SUBJECT_SCHEMA_VERSION,
        "kind": subject.kind,
        "scopePath": str(subject.scope_path),
        "scopeSha256": subject.scope_sha256,
        "packetPath": str(subject.packet_path),
        "packetSha256": subject.packet_sha256,
        "subjectDigest": subject.subject_digest,
    }
    if subject.record_path is not None and subject.record_sha256 is not None:
        document["recordPath"] = str(subject.record_path)
        document["recordSha256"] = subject.record_sha256
    if (
        subject.manifest_path is not None
        and subject.manifest_sha256 is not None
        and subject.manifest_schema_sha256 is not None
    ):
        document["manifestPath"] = str(subject.manifest_path)
        document["manifestSha256"] = subject.manifest_sha256
        document["manifestSchemaPath"] = PANEL_SCHEMA_RELATIVE_PATH
        document["manifestSchemaSha256"] = subject.manifest_schema_sha256
    if subject.kind == REPOSITORY_KIND:
        document["repositoryPath"] = str(cast(Path, subject.repository_path))
        document["subjectCommit"] = cast(str, subject.subject_commit)
        document["files"] = list(subject.files)
    return document


def load_subject(document: Mapping[str, object]) -> FrozenSubject:
    """Rebuild one frozen subject from a schema-valid document, digest included."""

    kind = document["kind"]
    if kind not in SUBJECT_KINDS:
        raise DispatchError(
            f"subject kind {kind!r} is not one of {list(SUBJECT_KINDS)}; a "
            f"{WORKING_TREE_KIND} subject is mutable and was never frozen"
        )
    record_path = document.get("recordPath")
    manifest_path = document.get("manifestPath")
    subject = FrozenSubject(
        kind=cast(str, kind),
        scope_path=Path(cast(str, document["scopePath"])),
        scope_sha256=_digest(document["scopeSha256"], "scopeSha256"),
        packet_path=Path(cast(str, document["packetPath"])),
        packet_sha256=_digest(document["packetSha256"], "packetSha256"),
        record_path=Path(cast(str, record_path)) if isinstance(record_path, str) else None,
        record_sha256=(
            _digest(document["recordSha256"], "recordSha256")
            if isinstance(record_path, str)
            else None
        ),
        manifest_path=(Path(cast(str, manifest_path)) if isinstance(manifest_path, str) else None),
        manifest_sha256=(
            _digest(document["manifestSha256"], "manifestSha256")
            if isinstance(manifest_path, str)
            else None
        ),
        manifest_schema_sha256=(
            _digest(document["manifestSchemaSha256"], "manifestSchemaSha256")
            if isinstance(manifest_path, str)
            else None
        ),
        repository_path=(
            Path(cast(str, document["repositoryPath"])) if kind == REPOSITORY_KIND else None
        ),
        subject_commit=cast(str, document["subjectCommit"]) if kind == REPOSITORY_KIND else None,
        files=(
            _repository_files(cast(Sequence[object], document["files"]))
            if kind == REPOSITORY_KIND
            else ()
        ),
    )
    declared = _digest(document["subjectDigest"], "subjectDigest")
    if subject.subject_digest != declared:
        raise DispatchError(
            f"frozen subject declares digest {declared} but its own bound components digest "
            f"to {subject.subject_digest}"
        )
    return subject


def verify_subject(subject: FrozenSubject) -> VerifiedSubject:
    """Re-read every bound byte and refuse any drift from the frozen subject."""

    if subject.kind == REPOSITORY_KIND:
        verify_repository(
            cast(Path, subject.repository_path),
            cast(str, subject.subject_commit),
            subject.files,
        )
    (
        scope_text,
        scope_sha256,
        packet_text,
        packet_sha256,
        packet,
        record,
        record_sha256,
        _scope_path,
        _packet_path,
        _record_path,
    ) = _bind_material(subject.scope_path, subject.packet_path, subject.record_path)
    for label, frozen, current in (
        ("assurance scope", subject.scope_sha256, scope_sha256),
        ("packet", subject.packet_sha256, packet_sha256),
        ("review record", subject.record_sha256, record_sha256),
    ):
        if frozen != current:
            raise DispatchError(
                f"{label} now digests to {current}, not the frozen {frozen}; the subject was "
                "edited after it was frozen and no reviewer may be sent it"
            )
    manifest: Mapping[str, object] | None = None
    if subject.manifest_path is not None:
        binding = bind_panel_manifest(subject.manifest_path)
        if binding.sha256 != subject.manifest_sha256:
            raise DispatchError(
                f"panel manifest now digests to {binding.sha256}, not the frozen "
                f"{subject.manifest_sha256}"
            )
        if binding.schema_sha256 != subject.manifest_schema_sha256:
            raise DispatchError(
                f"panel manifest schema now digests to {binding.schema_sha256}, not the "
                f"frozen {subject.manifest_schema_sha256}"
            )
        _validate_panel_manifest(binding, subject, packet, record)
        manifest = binding.document
    return VerifiedSubject(
        subject=subject,
        scope_text=scope_text,
        packet_text=packet_text,
        packet=packet,
        record=record,
        manifest=manifest,
    )


# --------------------------------------------------------------------------
# standing


@dataclass(frozen=True)
class Standing:
    """One valid lead-family x review-class x reviewer tuple and its standing."""

    lead_family: str
    review_class: str
    reviewer_id: str
    agent: str
    selection_class: str
    role: str
    independence_class: str
    authority: str


@dataclass(frozen=True)
class Assignment:
    """One resolved reviewer Task, standing included and never caller-supplied."""

    reviewer_id: str
    agent: str
    model: str
    model_family: str
    correlation_group: str
    provider_route: str
    access_profile: str
    data_allowlist_key: str
    execution_mode: str
    evidence_delivery: str
    lens: str
    selection_class: str
    role: str
    independence_class: str
    authority: str
    reason_codes: tuple[str, ...]

    @property
    def standing(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.reviewer_id,
            self.agent,
            self.selection_class,
            self.role,
            self.independence_class,
            self.authority,
        )


def lead_families(document: Mapping[str, object]) -> tuple[str, ...]:
    """Return every configured lead-family profile, sorted."""

    live = document.get("liveDispatch")
    if not isinstance(live, Mapping):
        raise DispatchError("qualification liveDispatch must be a mapping")
    profiles = cast(Mapping[str, object], live).get("byLeadFamily")
    if not isinstance(profiles, Mapping):
        raise DispatchError("qualification liveDispatch.byLeadFamily must be a mapping")
    return tuple(sorted(cast(Mapping[str, object], profiles)))


def _standing(
    lead_family: str,
    review_class: str,
    reviewer: qualification.LiveReviewer,
    selection_class: str,
) -> Standing:
    return Standing(
        lead_family=lead_family,
        review_class=review_class,
        reviewer_id=reviewer.reviewer_id,
        agent=reviewer.agent,
        selection_class=selection_class,
        role=reviewer.role,
        independence_class=reviewer.independence_class,
        authority=reviewer.authority,
    )


def class_candidates(
    document: Mapping[str, object], lead_family: str, review_class: str
) -> tuple[Standing, ...]:
    """Return every reviewer one class may ever dispatch under one lead family."""

    if review_class == FOCUSED:
        return tuple(
            _standing(lead_family, FOCUSED, reviewer, FOCUSED_SELECTION_CLASS)
            for reviewer in qualification.live_reviewers(document, "initial", lead_family)
        )
    if review_class == TARGETED_REFUTER:
        return tuple(
            _standing(lead_family, TARGETED_REFUTER, reviewer, "unconditional")
            for reviewer in qualification.live_reviewers(document, "targeted-refuter", lead_family)
        )
    if review_class == INITIAL:
        return (
            *(
                _standing(lead_family, INITIAL, reviewer, "unconditional")
                for reviewer in qualification.live_reviewers(document, "initial", lead_family)
            ),
            *(
                _standing(lead_family, INITIAL, reviewer, "specialist")
                for reviewer in qualification.live_specialists(document, lead_family)
            ),
            *(
                _standing(lead_family, INITIAL, critic.reviewer, "conditional")
                for critic in qualification.conditional_critics(document, lead_family)
            ),
        )
    raise DispatchError(f"review class {review_class!r} is not one of {list(REVIEW_CLASSES)}")


def roster_arity(
    document: Mapping[str, object], lead_family: str, review_class: str
) -> tuple[int, int]:
    """Return how many reviewers one class may dispatch, at least and at most.

    Only `initial` has a range, and only because a conditional lane has a
    not-selected state that the protocol treats as a routing fact. Everything
    else has one exact arity.
    """

    if review_class == FOCUSED:
        return 1, 1
    candidates = class_candidates(document, lead_family, review_class)
    if review_class == TARGETED_REFUTER:
        return len(candidates), len(candidates)
    optional = sum(1 for row in candidates if row.selection_class == "conditional")
    return len(candidates) - optional, len(candidates)


def standing_matrix(document: Mapping[str, object]) -> tuple[Standing, ...]:
    """Return every valid tuple across every profile and every review class."""

    rows: list[Standing] = []
    for lead_family in lead_families(document):
        for review_class in REVIEW_CLASSES:
            candidates = class_candidates(document, lead_family, review_class)
            if not candidates:
                raise DispatchError(
                    f"lead family {lead_family!r} configures no reviewer for review class "
                    f"{review_class!r}; a class with no candidate cannot be dispatched"
                )
            rows.extend(candidates)
    return tuple(rows)


def _assignment(
    reviewer: qualification.LiveReviewer, selection_class: str, reason_codes: Sequence[str]
) -> Assignment:
    return Assignment(
        reviewer_id=reviewer.reviewer_id,
        agent=reviewer.agent,
        model=reviewer.model,
        model_family=reviewer.model_family,
        correlation_group=reviewer.correlation_group,
        provider_route=reviewer.provider_route,
        access_profile=reviewer.access_profile,
        data_allowlist_key=reviewer.data_allowlist_key,
        execution_mode=reviewer.execution_mode,
        evidence_delivery=reviewer.evidence_delivery,
        lens=reviewer.lens,
        selection_class=selection_class,
        role=reviewer.role,
        independence_class=reviewer.independence_class,
        authority=reviewer.authority,
        reason_codes=tuple(reason_codes),
    )


def _assignment_from_row(row: Mapping[str, object]) -> Assignment:
    """Adopt one resolver-owned council row without re-deciding any of it."""

    reason_codes = row["reasonCodes"]
    return Assignment(
        reviewer_id=cast(str, row["reviewer_id"]),
        agent=cast(str, row["agent"]),
        model=cast(str, row["model"]),
        model_family=cast(str, row["model_family"]),
        correlation_group=cast(str, row["correlation_group"]),
        provider_route=cast(str, row["provider_route"]),
        access_profile=cast(str, row["access_profile"]),
        data_allowlist_key=cast(str, row["data_allowlist_key"]),
        execution_mode=cast(str, row["execution_mode"]),
        evidence_delivery=cast(str, row["evidence_delivery"]),
        lens=cast(str, row["lens"]),
        selection_class=cast(str, row["selectionClass"]),
        role=cast(str, row["role"]),
        independence_class=cast(str, row["independence_class"]),
        authority=cast(str, row["authority"]),
        reason_codes=tuple(cast(Sequence[str], reason_codes)),
    )


def _require_grants(reviewer: qualification.LiveReviewer, packet: Mapping[str, object]) -> None:
    """Refuse a lane the packet does not authorize, rather than dropping it.

    Only lanes with no not-selected state reach here -- the one focused critic
    and the whole fixed refutation pool -- so silently omitting one would shrink
    a roster nobody agreed to shrink and emitting it would transmit material to a
    lane the packet never authorized. `initial` is not checked here because the
    council resolver already applies both grants, skipping only the conditional
    lanes that are allowed to be skipped.
    """

    reasons = qualification.packet_authorization_reason_codes(reviewer, packet)
    if reasons:
        raise DispatchError(
            f"packet does not authorize reviewers.{reviewer.reviewer_id} (access_profile "
            f"{reviewer.access_profile!r}, data_allowlist_key "
            f"{reviewer.data_allowlist_key!r}): {list(reasons)}"
        )


def _require_exact_roster(
    review_class: str, requested: Sequence[str], resolved: Sequence[str]
) -> None:
    requested_roster = tuple(requested)
    resolved_roster = tuple(resolved)
    missing = sorted(set(resolved_roster) - set(requested_roster))
    unexpected = sorted(set(requested_roster) - set(resolved_roster))
    if requested_roster != resolved_roster:
        raise DispatchError(
            f"review class {review_class!r} dispatches the complete resolved roster "
            f"{list(resolved_roster)}; the request omits {missing}, adds {unexpected}, and "
            "must preserve resolver order exactly. A changed roster is a different panel "
            "from the one that was resolved"
        )


def resolve_assignments(
    document: Mapping[str, object],
    verified: VerifiedSubject,
    *,
    lead_family: str,
    review_class: str,
    reviewer_ids: Sequence[str],
    authority_path: Path,
    authority_sha256: str,
) -> tuple[Assignment, ...]:
    """Resolve one review class into its complete, standing-bearing assignment list."""

    if review_class not in REVIEW_CLASSES:
        raise DispatchError(f"review class {review_class!r} is not one of {list(REVIEW_CLASSES)}")
    requested = tuple(reviewer_ids)
    if not requested:
        raise DispatchError("no reviewer was named; a dispatch with no reviewer is not a review")
    duplicates = sorted({name for name in requested if requested.count(name) > 1})
    if duplicates:
        raise DispatchError(f"reviewer ids name {duplicates} more than once")
    subject = verified.subject
    if review_class in RECORD_BOUND_CLASSES and verified.record is None:
        raise DispatchError(
            f"review class {review_class!r} is resolved from the frozen review record, so the "
            "subject must bind one; freeze it with --record"
        )

    if review_class == FOCUSED:
        critics = {
            reviewer.reviewer_id: reviewer
            for reviewer in qualification.live_reviewers(document, "initial", lead_family)
        }
        if len(requested) != 1:
            raise DispatchError(
                f"a focused review dispatches exactly one configured initial critic, not "
                f"{list(requested)}; escalating by naming more reviewers is a council nobody "
                "resolved"
            )
        reviewer = critics.get(requested[0])
        if reviewer is None:
            raise DispatchError(
                f"{requested[0]!r} is not a configured initial critic for lead family "
                f"{lead_family!r}; the configured critics are {sorted(critics)}"
            )
        _require_grants(reviewer, verified.packet)
        assignments = (
            _assignment(
                reviewer, FOCUSED_SELECTION_CLASS, (qualification.UNCONDITIONAL_REASON_CODE,)
            ),
        )
        validate_evidence_compatibility(
            subject, verified.packet, [item.evidence_delivery for item in assignments]
        )
        return assignments

    if review_class == TARGETED_REFUTER:
        decision = qualification.select_review_action(cast(Mapping[str, object], verified.record))
        if decision.action != TARGETED_REFUTER:
            raise DispatchError(
                f"record does not authorize targeted refutation: status={decision.status!r}, "
                f"action={decision.action!r}, reasons={list(decision.reason_codes)}"
            )
        pool = qualification.live_reviewers(document, "targeted-refuter", lead_family)
        if not pool:
            raise DispatchError(
                f"liveDispatch.targetedRefuters selects no refuter for lead family {lead_family!r}"
            )
        for reviewer in pool:
            _require_grants(reviewer, verified.packet)
        _require_exact_roster(review_class, requested, [item.reviewer_id for item in pool])
        assignments = tuple(
            _assignment(reviewer, "unconditional", (qualification.TARGETED_REFUTER_REASON_CODE,))
            for reviewer in pool
        )
        validate_evidence_compatibility(
            subject, verified.packet, [item.evidence_delivery for item in assignments]
        )
        return assignments

    # The initial roster was resolved before freeze and is now part of the
    # subject. verify_subject re-resolved it from the bound record, packet, lead
    # family, and live authority, so adopting these exact rows does not create a
    # second standing authority.
    manifest = verified.manifest
    if manifest is None:
        raise DispatchError(
            "review class 'initial' requires a frozen panel manifest; use prepare or "
            "freeze --manifest before resolving standing"
        )
    if manifest.get("mode") != INITIAL or manifest.get("leadFamily") != lead_family:
        raise DispatchError(
            f"panel manifest resolves {manifest.get('mode')!r} for lead family "
            f"{manifest.get('leadFamily')!r}, not initial for {lead_family!r}"
        )
    if (
        manifest.get("authorityPath") != str(authority_path)
        or manifest.get("authoritySha256") != authority_sha256
    ):
        raise DispatchError("panel manifest is not bound to the authority resolving this receipt")
    selected = tuple(
        _assignment_from_row(cast(Mapping[str, object], row))
        for row in cast(Sequence[object], manifest["selected"])
    )
    _require_exact_roster(review_class, requested, [item.reviewer_id for item in selected])
    validate_evidence_compatibility(
        subject, verified.packet, [item.evidence_delivery for item in selected]
    )
    return selected


# --------------------------------------------------------------------------
# generated receipt schema


def _tuple_branch(row: Standing) -> dict[str, object]:
    """Pin one whole tuple, not one field of it.

    Standing is a relationship, so admitting each field independently would admit
    a cross-family authority on a same-lineage seat. Every field of one valid
    tuple is fixed by `const` in the same branch, and a row matches only by
    matching all of them.
    """

    return {
        "title": f"{row.lead_family}/{row.review_class}/{row.reviewer_id}",
        "properties": {
            "reviewer_id": {"const": row.reviewer_id},
            "agent": {"const": row.agent},
            "selectionClass": {"const": row.selection_class},
            "role": {"const": row.role},
            "independence_class": {"const": row.independence_class},
            "authority": {"const": row.authority},
        },
        "required": [
            "agent",
            "authority",
            "independence_class",
            "reviewer_id",
            "role",
            "selectionClass",
        ],
    }


def receipt_schema(document: Mapping[str, object]) -> dict[str, object]:
    """Generate the receipt schema from the live authority's own matrix."""

    families = lead_families(document)
    matrix = standing_matrix(document)
    branches: list[dict[str, object]] = []
    for lead_family in families:
        for review_class in REVIEW_CLASSES:
            rows = tuple(
                row
                for row in matrix
                if row.lead_family == lead_family and row.review_class == review_class
            )
            minimum, maximum = roster_arity(document, lead_family, review_class)
            branches.append(
                {
                    "if": {
                        "properties": {
                            "leadFamily": {"const": lead_family},
                            "reviewClass": {"const": review_class},
                        },
                        "required": ["leadFamily", "reviewClass"],
                    },
                    "then": {
                        "properties": {
                            "assignments": {
                                "minItems": minimum,
                                "maxItems": maximum,
                                "items": {"anyOf": [_tuple_branch(row) for row in rows]},
                            }
                        },
                        "description": (
                            f"Lead family {lead_family!r} under review class {review_class!r} "
                            f"dispatches between {minimum} and {maximum} reviewers, each of "
                            "which must match one whole configured tuple."
                        ),
                    },
                }
            )
    schema: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": RECEIPT_SCHEMA_FILENAME,
        "title": "Critical Review Resolver Receipt",
        "description": (
            "One resolver-owned reviewer assignment set, written once before any provider "
            "dispatch and bound to one frozen subject. Generated from the live qualification "
            "authority beside it: every lead-family x review-class x reviewer tuple the "
            "resolver can emit appears here as a `const` branch, so a receipt naming a "
            "reviewer that is not configured for its class, or carrying standing that profile "
            "does not grant, is invalid rather than merely unusual. Regenerate it whenever the "
            "authority's roster or standing changes; a stale schema stops dispatch."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schemaVersion",
            "panelId",
            "leadFamily",
            "reviewClass",
            "subject",
            "subjectPath",
            "subjectSha256",
            "subjectDigest",
            "authorityPath",
            "authoritySha256",
            "qualificationPath",
            "qualificationSha256",
            "resolverPath",
            "resolverSha256",
            "receiptSchemaSha256",
            "subjectSchemaPath",
            "subjectSchemaSha256",
            "envelopeSchemaPath",
            "envelopeSchemaSha256",
            "assignments",
        ],
        "properties": {
            "schemaVersion": {
                "const": RECEIPT_SCHEMA_VERSION,
                "description": (
                    "Pins this receipt contract; a consumer that accepts an unknown version "
                    "is guessing at what was resolved."
                ),
            },
            "panelId": {
                "const": cast(Mapping[str, object], document["liveDispatch"])["panelId"],
                "description": (
                    "The live panel definition that resolved this receipt, so a receipt "
                    "resolved under a superseded panel is visible rather than silent."
                ),
            },
            "leadFamily": {
                "enum": list(families),
                "description": (
                    "The accountable main-session model family. It selects one profile and "
                    "makes every assignment's cross-family or same-lineage standing auditable."
                ),
            },
            "reviewClass": {
                "enum": list(REVIEW_CLASSES),
                "description": (
                    "Which resolution produced this receipt: one configured critic outside a "
                    "council, the complete selected council, or the complete fixed refutation "
                    "pool."
                ),
            },
            "subject": {
                "type": "object",
                "description": (
                    "The frozen subject verbatim, so the receipt is self-describing about what "
                    "was reviewed without trusting a path to still hold it."
                ),
            },
            "subjectPath": {"type": "string", "pattern": "^/"},
            "subjectSha256": {"$ref": "#/$defs/sha256"},
            "subjectDigest": {"$ref": "#/$defs/sha256"},
            "authorityPath": {"type": "string", "pattern": "^/"},
            "authoritySha256": {"$ref": "#/$defs/sha256"},
            "qualificationPath": {
                "const": qualification.QUALIFICATION_RELATIVE_PATH,
                "description": (
                    "Skill-relative POSIX path of the standing authority. Fixed rather than "
                    "reported, because a receipt free to name whichever copy resolved it could "
                    "authenticate a forgery against itself."
                ),
            },
            "qualificationSha256": {"$ref": "#/$defs/sha256"},
            "resolverPath": {
                "const": RESOLVER_RELATIVE_PATH,
                "description": (
                    "Skill-relative POSIX path of the dispatch resolver, fixed the same way."
                ),
            },
            "resolverSha256": {"$ref": "#/$defs/sha256"},
            "receiptSchemaSha256": {
                "$ref": "#/$defs/sha256",
                "description": (
                    "Digest of this generated schema's exact bytes, so a receipt validated "
                    "against one matrix cannot later be replayed against another."
                ),
            },
            "subjectSchemaPath": {"const": SUBJECT_SCHEMA_RELATIVE_PATH},
            "subjectSchemaSha256": {"$ref": "#/$defs/sha256"},
            "envelopeSchemaPath": {"const": ENVELOPE_SCHEMA_RELATIVE_PATH},
            "envelopeSchemaSha256": {"$ref": "#/$defs/sha256"},
            "assignments": {
                "type": "array",
                "uniqueItems": True,
                "minItems": 1,
                "items": {"$ref": "#/$defs/assignment"},
                "description": (
                    "Every reviewer this resolution authorizes, in resolver order. The "
                    "dispatcher consumes it exactly and never adds, removes, reorders, or "
                    "substitutes a member."
                ),
            },
        },
        "allOf": branches,
        "$defs": {
            "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "assignment": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "reviewer_id",
                    "agent",
                    "model",
                    "model_family",
                    "correlation_group",
                    "provider_route",
                    "access_profile",
                    "data_allowlist_key",
                    "execution_mode",
                    "evidence_delivery",
                    "lens",
                    "selectionClass",
                    "role",
                    "independence_class",
                    "authority",
                    "reasonCodes",
                ],
                "properties": {
                    "reviewer_id": {"enum": sorted({row.reviewer_id for row in matrix})},
                    "agent": {"enum": sorted({row.agent for row in matrix})},
                    "model": {"type": "string", "minLength": 1},
                    "model_family": {"type": "string", "minLength": 1},
                    "correlation_group": {"type": "string", "minLength": 1},
                    "provider_route": {"type": "string", "minLength": 1},
                    "access_profile": {"type": "string", "minLength": 1},
                    "data_allowlist_key": {"type": "string", "minLength": 1},
                    "execution_mode": {"enum": list(qualification.EXECUTION_MODES)},
                    "evidence_delivery": {"type": "string", "minLength": 1},
                    "lens": {"type": "string"},
                    "selectionClass": {"enum": list(SELECTION_CLASSES)},
                    "role": {"enum": list(qualification.SELECTABLE_ROLES)},
                    "independence_class": {
                        "enum": [
                            qualification.CROSS_FAMILY,
                            qualification.SAME_LINEAGE_BLIND_SAMPLE,
                        ]
                    },
                    "authority": {
                        "enum": [
                            qualification.INDEPENDENT_EVIDENCE,
                            qualification.SUPPLEMENTAL_EVIDENCE,
                        ]
                    },
                    "reasonCodes": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    }
    Draft202012Validator.check_schema(schema)
    return schema


def receipt_schema_text(document: Mapping[str, object]) -> str:
    return _artifact_text(receipt_schema(document))


# --------------------------------------------------------------------------
# receipt


@dataclass(frozen=True)
class Receipt:
    """One resolution, bound to every byte that produced it."""

    panel_id: str
    lead_family: str
    review_class: str
    subject: FrozenSubject
    subject_path: Path
    subject_sha256: str
    authority_path: Path
    authority_sha256: str
    qualification_sha256: str
    resolver_sha256: str
    receipt_schema_sha256: str
    subject_schema_sha256: str
    envelope_schema_sha256: str
    assignments: tuple[Assignment, ...]


def _assignment_document(assignment: Assignment) -> dict[str, object]:
    return {
        "reviewer_id": assignment.reviewer_id,
        "agent": assignment.agent,
        "model": assignment.model,
        "model_family": assignment.model_family,
        "correlation_group": assignment.correlation_group,
        "provider_route": assignment.provider_route,
        "access_profile": assignment.access_profile,
        "data_allowlist_key": assignment.data_allowlist_key,
        "execution_mode": assignment.execution_mode,
        "evidence_delivery": assignment.evidence_delivery,
        "lens": assignment.lens,
        "selectionClass": assignment.selection_class,
        "role": assignment.role,
        "independence_class": assignment.independence_class,
        "authority": assignment.authority,
        "reasonCodes": list(assignment.reason_codes),
    }


def receipt_document(receipt: Receipt) -> dict[str, object]:
    return {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "panelId": receipt.panel_id,
        "leadFamily": receipt.lead_family,
        "reviewClass": receipt.review_class,
        "subject": subject_document(receipt.subject),
        "subjectPath": str(receipt.subject_path),
        "subjectSha256": receipt.subject_sha256,
        "subjectDigest": receipt.subject.subject_digest,
        "authorityPath": str(receipt.authority_path),
        "authoritySha256": receipt.authority_sha256,
        "qualificationPath": qualification.QUALIFICATION_RELATIVE_PATH,
        "qualificationSha256": receipt.qualification_sha256,
        "resolverPath": RESOLVER_RELATIVE_PATH,
        "resolverSha256": receipt.resolver_sha256,
        "receiptSchemaSha256": receipt.receipt_schema_sha256,
        "subjectSchemaPath": SUBJECT_SCHEMA_RELATIVE_PATH,
        "subjectSchemaSha256": receipt.subject_schema_sha256,
        "envelopeSchemaPath": ENVELOPE_SCHEMA_RELATIVE_PATH,
        "envelopeSchemaSha256": receipt.envelope_schema_sha256,
        "assignments": [_assignment_document(item) for item in receipt.assignments],
    }


def load_receipt(document: Mapping[str, object]) -> Receipt:
    return Receipt(
        panel_id=cast(str, document["panelId"]),
        lead_family=cast(str, document["leadFamily"]),
        review_class=cast(str, document["reviewClass"]),
        subject=load_subject(cast(Mapping[str, object], document["subject"])),
        subject_path=Path(cast(str, document["subjectPath"])),
        subject_sha256=_digest(document["subjectSha256"], "subjectSha256"),
        authority_path=Path(cast(str, document["authorityPath"])),
        authority_sha256=_digest(document["authoritySha256"], "authoritySha256"),
        qualification_sha256=_digest(document["qualificationSha256"], "qualificationSha256"),
        resolver_sha256=_digest(document["resolverSha256"], "resolverSha256"),
        receipt_schema_sha256=_digest(document["receiptSchemaSha256"], "receiptSchemaSha256"),
        subject_schema_sha256=_digest(document["subjectSchemaSha256"], "subjectSchemaSha256"),
        envelope_schema_sha256=_digest(document["envelopeSchemaSha256"], "envelopeSchemaSha256"),
        assignments=tuple(
            _assignment_from_row(cast(Mapping[str, object], row))
            for row in cast(Sequence[object], document["assignments"])
        ),
    )


# --------------------------------------------------------------------------
# canonical reviewer tasks


def canonical_task_text(
    receipt: Receipt, receipt_sha256: str, assignment: Assignment, verified: VerifiedSubject
) -> str:
    """Render one reviewer's whole assignment from verified bytes.

    The scope and packet are reproduced inline rather than referenced, in both
    subject kinds. A path is a promise about a file's future contents that
    nothing can keep, so a reviewer handed one may read bytes nobody froze; a
    reviewer handed the bytes reads the subject. A repository subject also names
    its commit and its exact bound paths, which the commit does keep.
    """

    subject = receipt.subject
    header = [
        RECEIPT_MARKER,
        f"receipt_sha256={receipt_sha256}",
        f"subject_digest={subject.subject_digest}",
        f"subject_kind={subject.kind}",
        f"lead_family={receipt.lead_family}",
        f"review_class={receipt.review_class}",
        f"reviewer_id={assignment.reviewer_id}",
        f"agent={assignment.agent}",
        f"selectionClass={assignment.selection_class}",
        f"role={assignment.role}",
        f"independence_class={assignment.independence_class}",
        f"authority={assignment.authority}",
        f"evidence_delivery={assignment.evidence_delivery}",
        f"subject_commit={subject.subject_commit or 'none'}",
        f"scope_sha256={subject.scope_sha256}",
        f"packet_sha256={subject.packet_sha256}",
    ]
    if subject.record_sha256 is not None:
        header.append(f"record_sha256={subject.record_sha256}")
    if subject.kind == REPOSITORY_KIND:
        header.append(f"repository_path={cast(Path, subject.repository_path)}")
        header.append(f"subject_file_count={len(subject.files)}")
        target = [
            f"Review commit {cast(str, subject.subject_commit)} in repository "
            f"{cast(Path, subject.repository_path)}, restricted to these "
            f"{len(subject.files)} bound paths:",
            *(f"- {name}" for name in subject.files),
            "",
            "Read no other path. The verified assurance scope and immutable packet below are "
            "the same bytes that were frozen with that commit.",
        ]
    else:
        target = [
            "Review only the verified assurance scope and immutable packet reproduced below. "
            "Do not inspect any path: this subject is its bytes, and no repository epoch is "
            "bound to it.",
        ]
    sections = [
        "\n".join(header),
        "",
        "# Target",
        *target,
        "Do not modify files or inspect peer output.",
        "",
        "# Resolved standing",
        f"`subject_commit`: {subject.subject_commit or 'none (packet-only subject)'}",
        f"`lead_family`: {receipt.lead_family}",
        f"`selectionClass`: {assignment.selection_class}",
        f"`role`: {assignment.role}",
        f"`independence_class`: {assignment.independence_class}",
        f"`authority`: {assignment.authority}",
        "",
        "These values were resolved from the live qualification authority and are bound to "
        f"receipt {receipt_sha256}. Never infer, choose, or renegotiate your own standing.",
        "",
        f"# Assurance scope (verified bytes, sha256 {subject.scope_sha256})",
        verified.scope_text.rstrip("\n"),
        "",
        f"# Immutable packet (verified bytes, sha256 {subject.packet_sha256})",
        verified.packet_text.rstrip("\n"),
    ]
    if subject.record_sha256 is not None:
        sections += [
            "",
            "# Frozen review record",
            f"Bound by digest {subject.record_sha256}. It is dispatch control state, not "
            "reviewed material; the packet above is the material.",
        ]
    sections += [
        "",
        "# Change",
        "Apply the common critical floor and the primary lens defined by your agent. Return "
        "falsifiable root-cause claims whose evidence anchors exist in the verified material "
        "above. This is review only: no implementation and no competing rewrite.",
        "",
        "# State fidelity",
        "Before accepting readiness or lifecycle claims, compare the implementation's assumed "
        "starting state with the bound predecessor evidence. Report the smallest concrete "
        "mismatch; do not answer one with a generalized recovery system unless the declared "
        "consequence requires it.",
        "",
        "# Acceptance",
        "Return one schema-valid summary/evidence/unresolved object, at most 12 evidence "
        "items, exact anchors present in the supplied evidence, and explicit missing evidence "
        "for unresolved claims. Every evidence item must identify the protected asset or "
        "invariant and the residual consequence after declared controls. Do not report general "
        "hardening or speculative future-proofing as a defect. Zero findings is a valid result.",
    ]
    return "\n".join(sections) + "\n"


def canonical_tasks(
    receipt: Receipt, receipt_sha256: str, verified: VerifiedSubject
) -> tuple[dict[str, str], ...]:
    """Build the exact Task items for one receipt, in resolver order."""

    return tuple(
        {
            "agent": assignment.agent,
            "task": canonical_task_text(receipt, receipt_sha256, assignment, verified),
        }
        for assignment in receipt.assignments
    )


def dispatch_marker(envelope_path: Path, envelope_sha256: str) -> str:
    return f"{DISPATCH_MARKER}\nenvelope_path={envelope_path}\nenvelope_sha256={envelope_sha256}"


def envelope_document(
    receipt: Receipt, receipt_path: Path, receipt_sha256: str, tasks: Sequence[Mapping[str, str]]
) -> dict[str, object]:
    items = [dict(task) for task in tasks]
    return {
        "schemaVersion": ENVELOPE_SCHEMA_VERSION,
        "panelId": receipt.panel_id,
        "leadFamily": receipt.lead_family,
        "reviewClass": receipt.review_class,
        "subjectDigest": receipt.subject.subject_digest,
        "receiptPath": str(receipt_path),
        "receiptSha256": receipt_sha256,
        "taskIntent": DISPATCH_TASK_INTENT,
        "tasks": items,
        "tasksSha256": _digest_bytes(_canonical_json(items).encode("utf-8")),
    }


def task_input(
    envelope_path: Path, envelope_sha256: str, tasks: Sequence[Mapping[str, str]]
) -> dict[str, object]:
    """Render the one canonical batch Task call for any reviewer count."""

    if not tasks:
        raise DispatchError("review dispatch has no reviewer tasks")
    return {
        "i": DISPATCH_TASK_INTENT,
        "context": dispatch_marker(envelope_path, envelope_sha256),
        "tasks": [dict(task) for task in tasks],
    }


# --------------------------------------------------------------------------
# commands


def _verified_receipt(
    receipt_path: Path,
) -> tuple[Receipt, str, VerifiedSubject, tuple[dict[str, str], ...]]:
    """Re-derive one receipt's whole claim from bytes read right now.

    Nothing here trusts the receipt about anything it could have been forged to
    say. The authority must be the live install rather than whatever path the
    receipt names, both resolver modules and all three schemas must still hash to
    what it recorded, the subject's every bound byte is re-read, and the council
    is resolved again and required to equal the recorded assignments.
    """

    resolved_receipt = _resolved(receipt_path, "resolver receipt")
    document, receipt_sha256 = _load_json(resolved_receipt, "resolver receipt")
    authority_path, authority_sha256, authority = bind_live_authority()
    _, receipt_schema_sha256, schema = bind_receipt_schema(authority_path, authority)
    _validate(document, schema, "resolver receipt")
    receipt = load_receipt(document)

    if receipt.authority_path != authority_path:
        raise DispatchError(
            f"receipt binds authority {receipt.authority_path}, which is not the live "
            f"qualification authority {authority_path}"
        )
    _, resolver_sha256 = resolver_binding()
    _, qualification_sha256 = qualification.qualification_binding()
    _, subject_schema_sha256, subject_schema = bind_public_schema(SUBJECT_SCHEMA_RELATIVE_PATH)
    _, envelope_schema_sha256, _envelope_schema = bind_public_schema(ENVELOPE_SCHEMA_RELATIVE_PATH)
    for label, recorded, current in (
        ("qualification authority", receipt.authority_sha256, authority_sha256),
        ("standing resolver", receipt.qualification_sha256, qualification_sha256),
        ("dispatch resolver", receipt.resolver_sha256, resolver_sha256),
        ("receipt schema", receipt.receipt_schema_sha256, receipt_schema_sha256),
        ("frozen subject schema", receipt.subject_schema_sha256, subject_schema_sha256),
        ("envelope schema", receipt.envelope_schema_sha256, envelope_schema_sha256),
    ):
        if recorded != current:
            raise DispatchError(
                f"{label} now digests to {current}, not the {recorded} this receipt was "
                "resolved against; re-resolve before dispatching"
            )

    subject_now, subject_sha256 = _load_json(
        _resolved(receipt.subject_path, "frozen subject"), "frozen subject"
    )
    if subject_sha256 != receipt.subject_sha256:
        raise DispatchError(
            f"frozen subject {receipt.subject_path} now digests to {subject_sha256}, not the "
            f"{receipt.subject_sha256} this receipt resolved"
        )
    _validate(subject_now, subject_schema, "frozen subject")
    if load_subject(subject_now) != receipt.subject:
        raise DispatchError(
            f"frozen subject {receipt.subject_path} does not match the subject embedded in the "
            "receipt"
        )
    verified = verify_subject(receipt.subject)

    fresh = resolve_assignments(
        authority,
        verified,
        lead_family=receipt.lead_family,
        review_class=receipt.review_class,
        reviewer_ids=[assignment.reviewer_id for assignment in receipt.assignments],
        authority_path=authority_path,
        authority_sha256=authority_sha256,
    )
    if fresh != receipt.assignments:
        raise DispatchError(
            "re-resolving this receipt produces a different roster or different standing than "
            f"it records: {[item.standing for item in fresh]} instead of "
            f"{[item.standing for item in receipt.assignments]}"
        )
    return receipt, receipt_sha256, verified, canonical_tasks(receipt, receipt_sha256, verified)


def _build_receipt(
    verified: VerifiedSubject,
    *,
    subject_path: Path,
    subject_sha256: str,
    lead_family: str,
    review_class: str,
    reviewer_ids: Sequence[str],
) -> tuple[Receipt, str]:
    """Resolve and validate one receipt without requiring intermediate files."""

    authority_path, authority_sha256, authority = bind_live_authority()
    _, receipt_schema_sha256, schema = bind_receipt_schema(authority_path, authority)
    _, subject_schema_sha256, _subject_schema = bind_public_schema(SUBJECT_SCHEMA_RELATIVE_PATH)
    _, envelope_schema_sha256, _envelope_schema = bind_public_schema(ENVELOPE_SCHEMA_RELATIVE_PATH)
    _, resolver_sha256 = resolver_binding()
    _, qualification_sha256 = qualification.qualification_binding()
    assignments = resolve_assignments(
        authority,
        verified,
        lead_family=lead_family,
        review_class=review_class,
        reviewer_ids=reviewer_ids,
        authority_path=authority_path,
        authority_sha256=authority_sha256,
    )
    receipt = Receipt(
        panel_id=cast(str, cast(Mapping[str, object], authority["liveDispatch"])["panelId"]),
        lead_family=lead_family,
        review_class=review_class,
        subject=verified.subject,
        subject_path=subject_path,
        subject_sha256=subject_sha256,
        authority_path=authority_path,
        authority_sha256=authority_sha256,
        qualification_sha256=qualification_sha256,
        resolver_sha256=resolver_sha256,
        receipt_schema_sha256=receipt_schema_sha256,
        subject_schema_sha256=subject_schema_sha256,
        envelope_schema_sha256=envelope_schema_sha256,
        assignments=assignments,
    )
    body = receipt_document(receipt)
    _validate(body, schema, "resolver receipt")
    return receipt, _artifact_text(body)


def _preflight_output_paths(items: Sequence[tuple[str, Path]]) -> None:
    paths = [path for _label, path in items]
    if len(set(paths)) != len(paths):
        raise DispatchError("preparation artifact paths must be distinct")
    for label, path in items:
        if path.exists():
            raise DispatchError(f"refusing to overwrite existing {label}: {path}")


def _publish_preparation(
    artifacts: Sequence[tuple[str, Path, str]],
    *,
    envelope_path: Path,
    envelope_sha256: str,
) -> str:
    """Write dependencies first and the dispatchable envelope last.

    The envelope is the commit marker: a process that stops before its write
    leaves no provider-addressable dispatch. Caught failures roll back every
    path created by this invocation; `verify-task` still revalidates all current
    bytes before this command returns a payload.
    """

    if not artifacts or artifacts[-1][1] != envelope_path:
        raise DispatchError("the review dispatch envelope must be the final preparation artifact")
    _preflight_output_paths([(label, path) for label, path, _text in artifacts])
    created: list[Path] = []
    try:
        for label, path, text in artifacts:
            created.append(_write_once(path, text, label))
        return command_verify_task(
            argparse.Namespace(envelope=envelope_path, sha256=envelope_sha256)
        )
    except BaseException as exc:
        cleanup_errors: list[str] = []
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                cleanup_errors.append(f"{path}: {cleanup_error}")
        if cleanup_errors:
            raise DispatchError(
                "preparation failed and rollback could not remove every published artifact: "
                + "; ".join(cleanup_errors)
            ) from exc
        raise


def command_freeze(args: argparse.Namespace) -> str:
    verified = freeze_subject(
        scope_path=args.scope,
        packet_path=args.packet,
        record_path=args.record,
        manifest_path=args.manifest,
        repository_path=args.repo,
        subject_commit=args.commit,
        files=args.files,
    )
    document = subject_document(verified.subject)
    _, _, schema = bind_public_schema(SUBJECT_SCHEMA_RELATIVE_PATH)
    _validate(document, schema, "frozen subject")
    text = _artifact_text(document)
    _write_once(args.out, text, "frozen subject")
    return text


def command_resolve(args: argparse.Namespace) -> str:
    subject_path = _resolved(args.subject, "frozen subject")
    document, subject_sha256 = _load_json(subject_path, "frozen subject")
    _, _, subject_schema = bind_public_schema(SUBJECT_SCHEMA_RELATIVE_PATH)
    _validate(document, subject_schema, "frozen subject")
    verified = verify_subject(load_subject(document))
    _receipt, text = _build_receipt(
        verified,
        subject_path=subject_path,
        subject_sha256=subject_sha256,
        lead_family=args.lead_family,
        review_class=args.review_class,
        reviewer_ids=args.reviewers,
    )
    _write_once(args.out, text, "resolver receipt")
    return text


def command_prepare(args: argparse.Namespace) -> str:
    """Prepare every dispatch artifact and return only verifier-approved Task input."""

    if args.review_class not in REVIEW_CLASSES:
        raise DispatchError(
            f"review class {args.review_class!r} is not one of {list(REVIEW_CLASSES)}"
        )
    subject_path = _resolved(args.subject, "frozen subject output")
    receipt_path = _resolved(args.receipt, "resolver receipt output")
    envelope_path = _resolved(args.out, "review dispatch envelope output")
    manifest_binding: BoundManifest | None = None
    manifest_text: str | None = None
    reviewers = tuple(args.reviewers)

    authority_path, authority_sha256, authority = bind_live_authority()
    if args.review_class == INITIAL:
        if args.record is None or args.manifest is None:
            raise DispatchError("an initial council preparation requires --record and --manifest")
        if reviewers:
            raise DispatchError("prepare resolves the initial roster; do not supply --reviewer")
        record_path, record_sha256, record = qualification.bind_record(args.record)
        packet_path, packet_sha256, packet = qualification.bind_packet(
            args.packet, record_path, record_sha256
        )
        manifest = qualification.select_full_council(
            authority,
            record,
            packet,
            lead_family=args.lead_family,
            record_path=record_path,
            record_sha256=record_sha256,
            packet_path=packet_path,
            packet_sha256=packet_sha256,
            authority_path=authority_path,
            authority_sha256=authority_sha256,
        )
        manifest_text = qualification.manifest_text(manifest)
        manifest_path = _resolved(args.manifest, "panel selection manifest output")
        _, manifest_schema_sha256, manifest_schema = bind_public_schema(PANEL_SCHEMA_RELATIVE_PATH)
        _validate(manifest, manifest_schema, "panel selection manifest")
        manifest_binding = BoundManifest(
            path=manifest_path,
            sha256=_digest_bytes(manifest_text.encode("utf-8")),
            schema_sha256=manifest_schema_sha256,
            document=manifest,
        )
        reviewers = tuple(
            cast(str, cast(Mapping[str, object], row)["reviewer_id"])
            for row in cast(Sequence[object], manifest["selected"])
        )
    else:
        if args.manifest is not None:
            raise DispatchError("only an initial council has a panel manifest")
        if args.review_class == FOCUSED:
            if len(reviewers) != 1:
                raise DispatchError("a focused preparation requires exactly one --reviewer")
        else:
            if args.record is None:
                raise DispatchError("a targeted-refuter preparation requires --record")
            if reviewers:
                raise DispatchError(
                    "prepare resolves the targeted-refuter pool; do not supply --reviewer"
                )
            reviewers = tuple(
                reviewer.reviewer_id
                for reviewer in qualification.live_reviewers(
                    authority, "targeted-refuter", args.lead_family
                )
            )

    verified = freeze_subject(
        scope_path=args.scope,
        packet_path=args.packet,
        record_path=args.record,
        _manifest_binding=manifest_binding,
        repository_path=args.repo,
        subject_commit=args.commit,
        files=args.files,
    )
    subject_document_value = subject_document(verified.subject)
    _, _, subject_schema = bind_public_schema(SUBJECT_SCHEMA_RELATIVE_PATH)
    _validate(subject_document_value, subject_schema, "frozen subject")
    subject_text = _artifact_text(subject_document_value)
    subject_sha256 = _digest_bytes(subject_text.encode("utf-8"))
    receipt, receipt_text = _build_receipt(
        verified,
        subject_path=subject_path,
        subject_sha256=subject_sha256,
        lead_family=args.lead_family,
        review_class=args.review_class,
        reviewer_ids=reviewers,
    )
    receipt_sha256 = _digest_bytes(receipt_text.encode("utf-8"))
    tasks = canonical_tasks(receipt, receipt_sha256, verified)
    envelope_value = envelope_document(receipt, receipt_path, receipt_sha256, tasks)
    _, _, envelope_schema = bind_public_schema(ENVELOPE_SCHEMA_RELATIVE_PATH)
    _validate(envelope_value, envelope_schema, "review dispatch envelope")
    envelope_text = _artifact_text(envelope_value)
    envelope_sha256 = _digest_bytes(envelope_text.encode("utf-8"))

    artifacts = [
        ("frozen subject", subject_path, subject_text),
        ("resolver receipt", receipt_path, receipt_text),
        ("review dispatch envelope", envelope_path, envelope_text),
    ]
    if manifest_binding is not None and manifest_text is not None:
        artifacts.insert(
            0,
            ("panel selection manifest", manifest_binding.path, manifest_text),
        )
    return _publish_preparation(
        artifacts,
        envelope_path=envelope_path,
        envelope_sha256=envelope_sha256,
    )


def command_dispatch(args: argparse.Namespace) -> str:
    receipt, receipt_sha256, _verified, tasks = _verified_receipt(args.receipt)
    receipt_path = _resolved(args.receipt, "resolver receipt")
    document = envelope_document(receipt, receipt_path, receipt_sha256, tasks)
    _, _, schema = bind_public_schema(ENVELOPE_SCHEMA_RELATIVE_PATH)
    _validate(document, schema, "review dispatch envelope")
    envelope_path = _write_once(args.out, _artifact_text(document), "review dispatch envelope")
    envelope_sha256 = _digest_bytes(_read_bytes(envelope_path, "review dispatch envelope"))
    return _canonical_json({"task_input": task_input(envelope_path, envelope_sha256, tasks)}) + "\n"


def command_verify_task(args: argparse.Namespace) -> str:
    envelope_path = _resolved(args.envelope, "review dispatch envelope")
    supplied = args.sha256
    if not isinstance(supplied, str) or not _SHA256_HEX.fullmatch(supplied):
        raise DispatchError(f"--sha256 {supplied!r} must be a lowercase 64-hex SHA-256 digest")
    raw = _read_bytes(envelope_path, "review dispatch envelope")
    envelope_sha256 = _digest_bytes(raw)
    if envelope_sha256 != supplied:
        raise DispatchError(
            f"review dispatch envelope {envelope_path} digests to {envelope_sha256}, not the "
            f"supplied {supplied}"
        )
    try:
        value: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DispatchError(f"review dispatch envelope cannot be parsed: {exc}") from exc
    _, _, schema = bind_public_schema(ENVELOPE_SCHEMA_RELATIVE_PATH)
    document = _validate(value, schema, "review dispatch envelope")
    receipt_path = _resolved(Path(cast(str, document["receiptPath"])), "resolver receipt")
    receipt, receipt_sha256, verified, tasks = _verified_receipt(receipt_path)
    # Deliberate duplicate of the resolve/dispatch preflight. The Task gate is
    # the final trust boundary and must reject a stale or manually altered
    # envelope even when an earlier artifact was prepared correctly.
    validate_evidence_compatibility(
        receipt.subject,
        verified.packet,
        [assignment.evidence_delivery for assignment in receipt.assignments],
    )
    if receipt_sha256 != document["receiptSha256"]:
        raise DispatchError(
            f"resolver receipt digests to {receipt_sha256}, not the "
            f"{document['receiptSha256']} this envelope was built from"
        )
    if envelope_document(receipt, receipt_path, receipt_sha256, tasks) != dict(document):
        raise DispatchError(
            "rebuilding this envelope from freshly read bytes does not reproduce it; the "
            "reviewed material or the resolved roster changed after dispatch"
        )
    return _canonical_json({"task_input": task_input(envelope_path, envelope_sha256, tasks)}) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare",
        help=(
            "resolve the roster, validate evidence delivery, freeze, resolve standing, build "
            "the envelope, and print verifier-approved Task input in one command"
        ),
    )
    prepare.add_argument("--scope", type=Path, required=True)
    prepare.add_argument("--packet", type=Path, required=True)
    prepare.add_argument("--record", type=Path)
    prepare.add_argument(
        "--manifest",
        type=Path,
        help="initial-council panel manifest output; prohibited for other review classes",
    )
    prepare.add_argument(
        "--repo", type=Path, help="repository root; requires --commit and at least one --file"
    )
    prepare.add_argument("--commit", help="full lowercase 40-hex commit, which must be HEAD")
    prepare.add_argument(
        "--file",
        action="append",
        default=[],
        dest="files",
        help="one exact repository-relative reviewed path; repeat for each",
    )
    prepare.add_argument("--subject", type=Path, required=True, help="frozen subject output")
    prepare.add_argument("--receipt", type=Path, required=True, help="resolver receipt output")
    prepare.add_argument("--out", type=Path, required=True, help="dispatch envelope output")
    prepare.add_argument(
        "--lead-family",
        required=True,
        help="accountable main-session model_family; must name a configured profile",
    )
    prepare.add_argument(
        "--review-class", required=True, help=f"one of {', '.join(REVIEW_CLASSES)}"
    )
    prepare.add_argument(
        "--reviewer",
        action="append",
        default=[],
        dest="reviewers",
        help="exactly one for focused; inferred and prohibited for roster-owned classes",
    )
    verify = commands.add_parser(
        "verify-task",
        help=(
            "internal: rehash one envelope, repeat the whole dispatch verification, rebuild "
            "the canonical tasks from current bytes, and print the approved Task input"
        ),
    )
    verify.add_argument("--envelope", type=Path, required=True)
    verify.add_argument("--sha256", required=True)

    args = parser.parse_args(argv)
    handlers = {
        "prepare": command_prepare,
        "verify-task": command_verify_task,
    }
    try:
        output = handlers[args.command](args)
    except (DispatchError, qualification.QualificationError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
