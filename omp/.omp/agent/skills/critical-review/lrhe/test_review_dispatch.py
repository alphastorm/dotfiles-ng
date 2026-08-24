"""Meta-tests for the freeze -> resolve -> dispatch reviewer Task API.

The expected standing matrix is hard-coded here on purpose. Deriving it from the
same authority the resolver reads would make these tests agree with whatever the
resolver does, including a silent widening of who may review what or with what
standing. Written out, a profile edit that changes a reviewer's role, lineage
relationship, or evidentiary authority fails the suite and has to be argued for.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import qualification  # noqa: E402  -- needs the path above
import review_dispatch as rd  # noqa: E402

# (lead_family, review_class, reviewer_id) -> (agent, selectionClass, role,
# independence_class, authority). Standing is lead-relative and never
# reviewer-intrinsic: `daybreak-blue` is an independent primary critic under a
# Claude lead and a supplemental same-lineage specialist under a GPT lead, and
# `claude` is independent conditional evidence everywhere except under its own
# lineage, where it is supplemental.
EXPECTED_TUPLES: dict[tuple[str, str, str], tuple[str, str, str, str, str]] = {
    ("gpt", "focused", "claude-opus"): (
        "review-claude-opus",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gpt", "focused", "gemini"): (
        "review-gemini",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gpt", "focused", "grok"): (
        "review-grok",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gpt", "initial", "claude-opus"): (
        "review-claude-opus",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gpt", "initial", "gemini"): (
        "review-gemini",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gpt", "initial", "grok"): (
        "review-grok",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gpt", "initial", "daybreak-blue"): (
        "review-daybreak-blue",
        "specialist",
        "security_specialist",
        "same_lineage_blind_sample",
        "supplemental_evidence",
    ),
    ("gpt", "initial", "claude"): (
        "review-claude-fable",
        "conditional",
        "conditional_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gpt", "targeted-refuter", "glm"): (
        "review-glm-floor",
        "unconditional",
        "targeted_refuter",
        "cross_family",
        "independent_evidence",
    ),
    ("claude", "focused", "daybreak-blue"): (
        "review-daybreak-blue",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("claude", "focused", "gemini"): (
        "review-gemini",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("claude", "focused", "grok"): (
        "review-grok",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("claude", "initial", "daybreak-blue"): (
        "review-daybreak-blue",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("claude", "initial", "gemini"): (
        "review-gemini",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("claude", "initial", "grok"): (
        "review-grok",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("claude", "initial", "claude-opus"): (
        "review-claude-opus",
        "specialist",
        "security_specialist",
        "same_lineage_blind_sample",
        "supplemental_evidence",
    ),
    ("claude", "initial", "claude"): (
        "review-claude-fable",
        "conditional",
        "conditional_critic",
        "same_lineage_blind_sample",
        "supplemental_evidence",
    ),
    ("claude", "targeted-refuter", "glm"): (
        "review-glm-floor",
        "unconditional",
        "targeted_refuter",
        "cross_family",
        "independent_evidence",
    ),
    ("gemini", "focused", "claude-opus"): (
        "review-claude-opus",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gemini", "focused", "daybreak-blue"): (
        "review-daybreak-blue",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gemini", "focused", "grok"): (
        "review-grok",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gemini", "initial", "claude-opus"): (
        "review-claude-opus",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gemini", "initial", "daybreak-blue"): (
        "review-daybreak-blue",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gemini", "initial", "grok"): (
        "review-grok",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gemini", "initial", "claude"): (
        "review-claude-fable",
        "conditional",
        "conditional_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("gemini", "targeted-refuter", "glm"): (
        "review-glm-floor",
        "unconditional",
        "targeted_refuter",
        "cross_family",
        "independent_evidence",
    ),
    ("grok", "focused", "claude-opus"): (
        "review-claude-opus",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("grok", "focused", "daybreak-blue"): (
        "review-daybreak-blue",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("grok", "focused", "gemini"): (
        "review-gemini",
        "focused",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("grok", "initial", "claude-opus"): (
        "review-claude-opus",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("grok", "initial", "daybreak-blue"): (
        "review-daybreak-blue",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("grok", "initial", "gemini"): (
        "review-gemini",
        "unconditional",
        "primary_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("grok", "initial", "claude"): (
        "review-claude-fable",
        "conditional",
        "conditional_critic",
        "cross_family",
        "independent_evidence",
    ),
    ("grok", "targeted-refuter", "glm"): (
        "review-glm-floor",
        "unconditional",
        "targeted_refuter",
        "cross_family",
        "independent_evidence",
    ),
}

# How many reviewers each class dispatches, at least and at most. Only `initial`
# has a range, and only by the number of conditional lanes that may be skipped.
EXPECTED_ARITY: dict[tuple[str, str], tuple[int, int]] = {
    ("gpt", "focused"): (1, 1),
    ("gpt", "initial"): (4, 5),
    ("gpt", "targeted-refuter"): (1, 1),
    ("claude", "focused"): (1, 1),
    ("claude", "initial"): (4, 5),
    ("claude", "targeted-refuter"): (1, 1),
    ("gemini", "focused"): (1, 1),
    ("gemini", "initial"): (3, 4),
    ("gemini", "targeted-refuter"): (1, 1),
    ("grok", "focused"): (1, 1),
    ("grok", "initial"): (3, 4),
    ("grok", "targeted-refuter"): (1, 1),
}

EXPECTED_LEAD_FAMILIES = ("claude", "gemini", "gpt", "grok")


def _live_document() -> dict:
    if not rd.LIVE_AUTHORITY.is_file():
        pytest.skip("private qualification authority is not present in this checkout")
    return yaml.safe_load(rd.LIVE_AUTHORITY.read_text(encoding="utf-8"))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _packet_text(document: dict, *, record_path: str, record_sha256: str, diff: str) -> str:
    """One immutable packet granting every configured lane both grants.

    The grants are read off the authority rather than hard-coded: these tests
    assert what standing a reviewer holds, not which vendor tokens a fixture
    happens to spell, and an unauthorized lane fails resolution for a reason
    that has nothing to do with the matrix under test.
    """

    reviewers = document["reviewers"]
    body = {
        "review_record_path": record_path,
        "review_record_sha256": record_sha256,
        "goal": "Prove one reviewer dispatch is structurally valid.",
        "non_goals": ["broadening the reviewed subject"],
        "requirements": ["the transmitted evidence equals the frozen evidence"],
        "invariants": ["no reviewer receives standing it was not granted"],
        "trust_boundaries": ["lead to hosted reviewer"],
        "data_or_state_transitions": ["none"],
        "rollback_contract": "discard the generated artifacts",
        "compatibility_contract": "internal only",
        "design_or_diff": diff,
        "known_open_questions": ["none"],
        "rejected_alternatives_and_reasons": ["trusting a path: it can be edited"],
        "provider_data_allowlist": sorted(
            {entry["data_allowlist_key"] for entry in reviewers.values()}
        ),
        "reviewer_access_profile_allowlist": sorted(
            {entry["access_profile"] for entry in reviewers.values()}
        ),
    }
    return "# Packet\n\n```yaml\n" + yaml.safe_dump(body, sort_keys=True) + "```\n"


@pytest.fixture
def authority(tmp_path, monkeypatch) -> dict:
    """Bind the live matrix at a temp path, with its generated schema beside it.

    The matrix is the real one -- that is what is under test -- but every path
    the resolver writes to or hashes lives under `tmp_path`, so no test can
    depend on or disturb the installed skill.
    """

    document = _live_document()
    root = tmp_path / "authority"
    root.mkdir()
    installed = root / "qualification.yml"
    shutil.copyfile(rd.LIVE_AUTHORITY, installed)
    monkeypatch.setattr(rd, "LIVE_AUTHORITY", installed)
    (root / rd.RECEIPT_SCHEMA_FILENAME).write_text(
        rd.receipt_schema_text(qualification.validate_qualification(document)),
        encoding="utf-8",
    )
    return document


@pytest.fixture
def material(tmp_path, authority) -> dict:
    """One scope file and one packet file, outside any repository."""

    scope = tmp_path / "scope.md"
    scope.write_text(
        "# Assurance scope\nClass: production/hard-to-reverse.\nAsset: the dispatch path.\n",
        encoding="utf-8",
    )
    packet = tmp_path / "packet.md"
    packet.write_text(
        _packet_text(
            authority,
            record_path="/frozen/review-record.json",
            record_sha256="a" * 64,
            diff="src/dispatch.py gained one ordered API.",
        ),
        encoding="utf-8",
    )
    return {"scope": scope, "packet": packet}


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(repo),
        },
    )
    return completed.stdout


@pytest.fixture
def repository(tmp_path) -> dict:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/dispatch.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "src/other.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "subject")
    commit = _git(repo, "rev-parse", "HEAD").strip()
    return {"path": repo.resolve(), "commit": commit}


# --------------------------------------------------------------------------
# the standing matrix


def test_standing_matrix_is_exactly_the_configured_tuples(authority):
    """Every valid tuple, and no others, across 4 families x 3 classes."""

    document = qualification.validate_qualification(authority)
    assert rd.lead_families(document) == EXPECTED_LEAD_FAMILIES
    resolved = {
        (row.lead_family, row.review_class, row.reviewer_id): (
            row.agent,
            row.selection_class,
            row.role,
            row.independence_class,
            row.authority,
        )
        for row in rd.standing_matrix(document)
    }
    assert resolved == EXPECTED_TUPLES
    for (lead_family, review_class), arity in EXPECTED_ARITY.items():
        assert rd.roster_arity(document, lead_family, review_class) == arity, (
            f"{lead_family}/{review_class}"
        )


def test_checked_receipt_schema_equals_the_generated_one():
    """A schema beside the authority that no longer describes it stops dispatch."""

    document = qualification.validate_qualification(_live_document())
    checked = rd.receipt_schema_path(rd.LIVE_AUTHORITY)
    assert checked.is_file(), f"{checked} was never generated"
    assert checked.read_text(encoding="utf-8") == rd.receipt_schema_text(document)


def _receipt_document(
    subject: dict, *, lead_family: str, review_class: str, assignments: list[dict]
) -> dict:
    return {
        "schemaVersion": rd.RECEIPT_SCHEMA_VERSION,
        "panelId": qualification.LIVE_PANEL_ID,
        "leadFamily": lead_family,
        "reviewClass": review_class,
        "subject": subject,
        "subjectPath": "/frozen/frozen-subject.json",
        "subjectSha256": "1" * 64,
        "subjectDigest": subject["subjectDigest"],
        "authorityPath": "/frozen/qualification.yml",
        "authoritySha256": "2" * 64,
        "qualificationPath": qualification.QUALIFICATION_RELATIVE_PATH,
        "qualificationSha256": "3" * 64,
        "resolverPath": rd.RESOLVER_RELATIVE_PATH,
        "resolverSha256": "4" * 64,
        "receiptSchemaSha256": "5" * 64,
        "subjectSchemaPath": rd.SUBJECT_SCHEMA_RELATIVE_PATH,
        "subjectSchemaSha256": "6" * 64,
        "envelopeSchemaPath": rd.ENVELOPE_SCHEMA_RELATIVE_PATH,
        "envelopeSchemaSha256": "7" * 64,
        "assignments": assignments,
    }


def _assignment(reviewer_id: str, tuple_row: tuple[str, str, str, str, str]) -> dict:
    agent, selection_class, role, independence_class, authority = tuple_row
    return {
        "reviewer_id": reviewer_id,
        "agent": agent,
        "model": "vendor/model:max",
        "model_family": "vendor",
        "correlation_group": "vendor-model",
        "provider_route": "vendor",
        "access_profile": "vendor-default",
        "data_allowlist_key": "vendor",
        "execution_mode": "task_agent",
        "evidence_delivery": "repository",
        "lens": "architecture",
        "selectionClass": selection_class,
        "role": role,
        "independence_class": independence_class,
        "authority": authority,
        "reasonCodes": ["configured-primary-critic"],
    }


@pytest.fixture
def receipt_validator(authority) -> Draft202012Validator:
    return Draft202012Validator(rd.receipt_schema(qualification.validate_qualification(authority)))


@pytest.fixture
def packet_only_subject(material) -> dict:
    return rd.subject_document(
        rd.freeze_subject(scope_path=material["scope"], packet_path=material["packet"]).subject
    )


def test_receipt_schema_admits_every_valid_tuple(receipt_validator, packet_only_subject):
    for (lead_family, review_class, reviewer_id), row in EXPECTED_TUPLES.items():
        if review_class == "initial":
            continue  # arity is asserted separately; a lone member fails minItems
        document = _receipt_document(
            packet_only_subject,
            lead_family=lead_family,
            review_class=review_class,
            assignments=[_assignment(reviewer_id, row)],
        )
        assert receipt_validator.is_valid(document), (
            f"{lead_family}/{review_class}/{reviewer_id} is configured but rejected"
        )


def test_receipt_schema_refuses_a_reviewer_that_class_never_dispatches(
    receipt_validator, packet_only_subject
):
    """A configured reviewer is still invalid under a class it does not serve.

    `daybreak-blue` is a real reviewer with real standing under a GPT lead, but
    never as that lead's focused critic: it shares the lead's lineage. Admitting
    reviewers globally rather than per class would make the same-lineage bar a
    convention instead of a check.
    """

    document = _receipt_document(
        packet_only_subject,
        lead_family="gpt",
        review_class="focused",
        assignments=[
            _assignment(
                "daybreak-blue",
                (
                    "review-daybreak-blue",
                    "focused",
                    "primary_critic",
                    "cross_family",
                    "independent_evidence",
                ),
            )
        ],
    )
    assert not receipt_validator.is_valid(document)


def test_receipt_schema_refuses_a_wrong_role(receipt_validator, packet_only_subject):
    """One field of a tuple cannot be swapped while the rest stay valid."""

    agent, selection_class, _role, independence_class, authority = EXPECTED_TUPLES[
        ("gpt", "focused", "claude-opus")
    ]
    document = _receipt_document(
        packet_only_subject,
        lead_family="gpt",
        review_class="focused",
        assignments=[
            _assignment(
                "claude-opus",
                (agent, selection_class, "security_specialist", independence_class, authority),
            )
        ],
    )
    assert not receipt_validator.is_valid(document)


def test_receipt_schema_refuses_a_wrong_authority(receipt_validator, packet_only_subject):
    """A supplemental seat cannot promote itself to independent evidence.

    This is the substitution the whole lead-relative model exists to prevent: a
    same-lineage blind sample counted as independent evidence would let a council
    satisfy its independence floor with a sample of the lead.
    """

    agent, selection_class, role, independence_class, _authority = EXPECTED_TUPLES[
        ("claude", "initial", "claude-opus")
    ]
    document = _receipt_document(
        packet_only_subject,
        lead_family="claude",
        review_class="initial",
        assignments=[
            _assignment("daybreak-blue", EXPECTED_TUPLES[("claude", "initial", "daybreak-blue")]),
            _assignment("gemini", EXPECTED_TUPLES[("claude", "initial", "gemini")]),
            _assignment("grok", EXPECTED_TUPLES[("claude", "initial", "grok")]),
            _assignment(
                "claude-opus",
                (agent, selection_class, role, independence_class, "independent_evidence"),
            ),
        ],
    )
    assert not receipt_validator.is_valid(document)


def test_receipt_schema_refuses_an_incomplete_council(receipt_validator, packet_only_subject):
    """A council short of its always-selected members cannot even be recorded."""

    complete = [
        _assignment(reviewer_id, EXPECTED_TUPLES[("gpt", "initial", reviewer_id)])
        for reviewer_id in ("claude-opus", "gemini", "grok", "daybreak-blue")
    ]
    assert receipt_validator.is_valid(
        _receipt_document(
            packet_only_subject,
            lead_family="gpt",
            review_class="initial",
            assignments=complete,
        )
    )
    assert not receipt_validator.is_valid(
        _receipt_document(
            packet_only_subject,
            lead_family="gpt",
            review_class="initial",
            assignments=complete[:-1],
        )
    )


def test_incomplete_padded_and_reordered_rosters_are_refused_by_name():
    """The roster gate names omissions, additions, and order drift."""

    with pytest.raises(rd.DispatchError, match=r"omits \['grok'\]"):
        rd._require_exact_roster(
            "initial", ["claude-opus", "gemini"], ["claude-opus", "gemini", "grok"]
        )
    with pytest.raises(rd.DispatchError, match=r"adds \['kimi'\]"):
        rd._require_exact_roster("targeted-refuter", ["glm", "kimi"], ["glm"])
    with pytest.raises(rd.DispatchError, match="preserve resolver order"):
        rd._require_exact_roster("initial", ["gemini", "claude-opus"], ["claude-opus", "gemini"])


def test_task_input_uses_one_batch_shape_for_every_reviewer_count(tmp_path):
    first = {"agent": "review-gemini", "task": f"{rd.RECEIPT_MARKER}\nreviewer_id=gemini\n"}
    second = {"agent": "review-grok", "task": f"{rd.RECEIPT_MARKER}\nreviewer_id=grok\n"}
    envelope = (tmp_path / "dispatch.json").resolve()
    digest = "a" * 64

    for tasks in ([first], [first, second]):
        payload = rd.task_input(envelope, digest, tasks)
        assert set(payload) == {"i", "context", "tasks"}
        assert payload["context"] == rd.dispatch_marker(envelope, digest)
        assert payload["tasks"] == tasks

    with pytest.raises(rd.DispatchError, match="no reviewer tasks"):
        rd.task_input(envelope, digest, [])


# --------------------------------------------------------------------------
# freezing


def test_freeze_refuses_an_abbreviated_commit(material, repository):
    with pytest.raises(rd.DispatchError, match="lowercase 40-hex commit"):
        rd.freeze_subject(
            scope_path=material["scope"],
            packet_path=material["packet"],
            repository_path=repository["path"],
            subject_commit=repository["commit"][:12],
            files=["src/dispatch.py"],
        )


def test_freeze_refuses_a_modified_or_untracked_tree(material, repository):
    (repository["path"] / "src/dispatch.py").write_text("VALUE = 99\n", encoding="utf-8")
    with pytest.raises(rd.DispatchError, match="modified or untracked"):
        rd.freeze_subject(
            scope_path=material["scope"],
            packet_path=material["packet"],
            repository_path=repository["path"],
            subject_commit=repository["commit"],
            files=["src/dispatch.py"],
        )
    _git(repository["path"], "checkout", "--", "src/dispatch.py")
    (repository["path"] / "src/extra.py").write_text("VALUE = 3\n", encoding="utf-8")
    with pytest.raises(rd.DispatchError, match="modified or untracked"):
        rd.freeze_subject(
            scope_path=material["scope"],
            packet_path=material["packet"],
            repository_path=repository["path"],
            subject_commit=repository["commit"],
            files=["src/dispatch.py"],
        )


def test_freeze_refuses_a_working_tree_subject(material, repository):
    """A repository with no commit and no file list is the mutable tree."""

    with pytest.raises(rd.DispatchError, match=rd.WORKING_TREE_KIND):
        rd.freeze_subject(
            scope_path=material["scope"],
            packet_path=material["packet"],
            repository_path=repository["path"],
        )
    with pytest.raises(rd.DispatchError, match=rd.WORKING_TREE_KIND):
        rd.freeze_subject(
            scope_path=material["scope"],
            packet_path=material["packet"],
            repository_path=repository["path"],
            subject_commit=repository["commit"],
        )


def test_freeze_refuses_an_empty_or_unbound_file_list(material, repository):
    with pytest.raises(rd.DispatchError, match="at least one bound file"):
        rd._repository_files([])
    with pytest.raises(rd.DispatchError, match="does not bind"):
        rd.freeze_subject(
            scope_path=material["scope"],
            packet_path=material["packet"],
            repository_path=repository["path"],
            subject_commit=repository["commit"],
            files=["src/dispatch.py", "src/absent.py"],
        )
    with pytest.raises(rd.DispatchError, match="does not bind|expand to"):
        rd.freeze_subject(
            scope_path=material["scope"],
            packet_path=material["packet"],
            repository_path=repository["path"],
            subject_commit=repository["commit"],
            files=["src"],
        )


def test_freeze_refuses_a_symlink_entry(material, repository):
    """A committed symlink is clean and immutable; the bytes it names are not."""

    repo = repository["path"]
    (repo / "src/link.py").symlink_to("/etc/hosts")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "link")
    commit = _git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(rd.DispatchError, match="other than a regular file"):
        rd.freeze_subject(
            scope_path=material["scope"],
            packet_path=material["packet"],
            repository_path=repo,
            subject_commit=commit,
            files=["src/link.py"],
        )


def test_freeze_refuses_a_submodule_entry(material, repository, tmp_path):
    """A gitlink names a commit whose contents this commit does not carry."""

    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "value.py").write_text("VALUE = 4\n", encoding="utf-8")
    _git(inner, "init", "-q", "-b", "main")
    _git(inner, "add", "-A")
    _git(inner, "commit", "-q", "-m", "inner")
    repo = repository["path"]
    _git(
        repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "--quiet",
        "add",
        str(inner),
        "vendor",
    )
    _git(repo, "commit", "-q", "-m", "submodule")
    commit = _git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(rd.DispatchError, match="other than a regular file"):
        rd.freeze_subject(
            scope_path=material["scope"],
            packet_path=material["packet"],
            repository_path=repo,
            subject_commit=commit,
            files=["vendor"],
        )


def test_freeze_refuses_repository_fields_on_a_packet_only_subject(material, repository):
    with pytest.raises(rd.DispatchError, match="packet-only subject has no repository"):
        rd.freeze_subject(
            scope_path=material["scope"],
            packet_path=material["packet"],
            subject_commit=repository["commit"],
        )


def test_a_subject_digest_covers_every_bound_component(material, repository):
    base = rd.freeze_subject(
        scope_path=material["scope"],
        packet_path=material["packet"],
        repository_path=repository["path"],
        subject_commit=repository["commit"],
        files=["src/dispatch.py"],
    ).subject
    wider = rd.freeze_subject(
        scope_path=material["scope"],
        packet_path=material["packet"],
        repository_path=repository["path"],
        subject_commit=repository["commit"],
        files=["src/dispatch.py", "src/other.py"],
    ).subject
    assert base.subject_digest != wider.subject_digest
    document = rd.subject_document(base)
    document["subjectDigest"] = "0" * 64
    with pytest.raises(rd.DispatchError, match="own bound components digest to"):
        rd.load_subject(document)


def test_verify_subject_refuses_a_scope_or_packet_edited_after_freezing(material):
    verified = rd.freeze_subject(scope_path=material["scope"], packet_path=material["packet"])
    assert rd.verify_subject(verified.subject).subject == verified.subject
    material["scope"].write_text("# Assurance scope\nClass: bounded experiment.\n", "utf-8")
    with pytest.raises(rd.DispatchError, match="assurance scope now digests to"):
        rd.verify_subject(verified.subject)


# --------------------------------------------------------------------------
# resolving


def test_focused_refuses_a_same_lineage_or_unconfigured_critic(material, authority):
    document = qualification.validate_qualification(authority)
    verified = rd.freeze_subject(scope_path=material["scope"], packet_path=material["packet"])
    for reviewer_id in ("daybreak-blue", "kimi", "minimax"):
        with pytest.raises(rd.DispatchError, match="not a configured initial critic"):
            rd.resolve_assignments(
                document,
                verified,
                lead_family="gpt",
                review_class="focused",
                reviewer_ids=[reviewer_id],
                authority_path=rd.LIVE_AUTHORITY,
                authority_sha256="0" * 64,
            )
    with pytest.raises(rd.DispatchError, match="exactly one configured initial critic"):
        rd.resolve_assignments(
            document,
            verified,
            lead_family="gpt",
            review_class="focused",
            reviewer_ids=["claude-opus", "gemini"],
            authority_path=rd.LIVE_AUTHORITY,
            authority_sha256="0" * 64,
        )


def test_record_bound_classes_refuse_a_subject_without_a_record(material, authority):
    document = qualification.validate_qualification(authority)
    verified = rd.freeze_subject(scope_path=material["scope"], packet_path=material["packet"])
    for review_class in rd.RECORD_BOUND_CLASSES:
        with pytest.raises(rd.DispatchError, match="subject must bind one"):
            rd.resolve_assignments(
                document,
                verified,
                lead_family="gpt",
                review_class=review_class,
                reviewer_ids=["glm"],
                authority_path=rd.LIVE_AUTHORITY,
                authority_sha256="0" * 64,
            )


def test_an_unknown_review_class_is_refused(material, authority):
    document = qualification.validate_qualification(authority)
    verified = rd.freeze_subject(scope_path=material["scope"], packet_path=material["packet"])
    with pytest.raises(rd.DispatchError, match="is not one of"):
        rd.resolve_assignments(
            document,
            verified,
            lead_family="gpt",
            review_class="second-opinion",
            reviewer_ids=["claude-opus"],
            authority_path=rd.LIVE_AUTHORITY,
            authority_sha256="0" * 64,
        )


# --------------------------------------------------------------------------
# the whole focused path


def test_focused_dispatch_end_to_end(tmp_path, material, repository, capsys):
    """freeze -> resolve -> dispatch -> verify-task, then prove it is not replayable."""

    subject_out = tmp_path / "frozen-subject.json"
    assert (
        rd.main(
            [
                "freeze",
                "--scope",
                str(material["scope"]),
                "--packet",
                str(material["packet"]),
                "--repo",
                str(repository["path"]),
                "--commit",
                repository["commit"],
                "--file",
                "src/dispatch.py",
                "--out",
                str(subject_out),
            ]
        )
        == 0
    )
    subject = json.loads(subject_out.read_text(encoding="utf-8"))
    assert subject["kind"] == "repository"
    assert subject["files"] == ["src/dispatch.py"]
    assert subject["subjectCommit"] == repository["commit"]

    receipt_out = tmp_path / "resolver-receipt.json"
    assert (
        rd.main(
            [
                "resolve",
                "--subject",
                str(subject_out),
                "--lead-family",
                "gpt",
                "--review-class",
                "focused",
                "--reviewer",
                "claude-opus",
                "--out",
                str(receipt_out),
            ]
        )
        == 0
    )
    receipt = json.loads(receipt_out.read_text(encoding="utf-8"))
    assert receipt["reviewClass"] == "focused"
    assert [row["reviewer_id"] for row in receipt["assignments"]] == ["claude-opus"]
    assignment = receipt["assignments"][0]
    agent, selection_class, role, independence_class, authority = EXPECTED_TUPLES[
        ("gpt", "focused", "claude-opus")
    ]
    assert (
        assignment["agent"],
        assignment["selectionClass"],
        assignment["role"],
        assignment["independence_class"],
        assignment["authority"],
    ) == (agent, selection_class, role, independence_class, authority)

    capsys.readouterr()
    envelope_out = tmp_path / "review-dispatch-envelope.json"
    assert rd.main(["dispatch", "--receipt", str(receipt_out), "--out", str(envelope_out)]) == 0
    emitted = json.loads(capsys.readouterr().out)["task_input"]
    envelope_sha256 = _digest(envelope_out.read_text(encoding="utf-8"))

    assert (
        rd.main(["verify-task", "--envelope", str(envelope_out), "--sha256", envelope_sha256]) == 0
    )
    approved = json.loads(capsys.readouterr().out)["task_input"]
    assert approved == emitted
    assert set(approved) == {"i", "context", "tasks"}
    assert approved["i"] == rd.DISPATCH_TASK_INTENT
    assert approved["context"] == rd.dispatch_marker(envelope_out.resolve(), envelope_sha256)
    assert len(approved["tasks"]) == 1
    item = approved["tasks"][0]
    assert item["agent"] == agent
    task = item["task"]
    assert task.startswith(f"{rd.RECEIPT_MARKER}\n")
    assert "envelope_path=" not in task
    assert "envelope_sha256=" not in task
    assert f"subject_commit={repository['commit']}" in task
    assert f"repository_path={repository['path'].resolve()}" in task
    assert f"independence_class={independence_class}" in task
    # The transmitted evidence is the bytes, not a path that may later hold
    # something else.
    assert material["scope"].read_text(encoding="utf-8").rstrip("\n") in task
    assert material["packet"].read_text(encoding="utf-8").rstrip("\n") in task

    # A digest the caller did not take from these exact bytes is refused.
    assert rd.main(["verify-task", "--envelope", str(envelope_out), "--sha256", "f" * 64]) == 1
    # And an edit to the scope after dispatch stops transmission rather than
    # quietly sending material nobody froze.
    material["scope"].write_text("# Assurance scope\nClass: bounded experiment.\n", "utf-8")
    assert (
        rd.main(["verify-task", "--envelope", str(envelope_out), "--sha256", envelope_sha256]) == 1
    )


def test_generated_artifacts_are_read_only_and_never_overwritten(tmp_path, material, repository):
    out = tmp_path / "frozen-subject.json"
    argv = [
        "freeze",
        "--scope",
        str(material["scope"]),
        "--packet",
        str(material["packet"]),
        "--repo",
        str(repository["path"]),
        "--commit",
        repository["commit"],
        "--file",
        "src/dispatch.py",
        "--out",
        str(out),
    ]
    assert rd.main(argv) == 0
    assert out.stat().st_mode & 0o777 == 0o444
    assert rd.main(argv) == 1


def test_a_receipt_naming_another_authority_is_refused(tmp_path, material, repository):
    subject_out = tmp_path / "frozen-subject.json"
    receipt_out = tmp_path / "resolver-receipt.json"
    assert (
        rd.main(
            [
                "freeze",
                "--scope",
                str(material["scope"]),
                "--packet",
                str(material["packet"]),
                "--repo",
                str(repository["path"]),
                "--commit",
                repository["commit"],
                "--file",
                "src/dispatch.py",
                "--out",
                str(subject_out),
            ]
        )
        == 0
    )
    assert (
        rd.main(
            [
                "resolve",
                "--subject",
                str(subject_out),
                "--lead-family",
                "gpt",
                "--review-class",
                "focused",
                "--reviewer",
                "claude-opus",
                "--out",
                str(receipt_out),
            ]
        )
        == 0
    )
    forged = json.loads(receipt_out.read_text(encoding="utf-8"))
    forged["authorityPath"] = str(tmp_path / "elsewhere/qualification.yml")
    elsewhere = tmp_path / "forged-receipt.json"
    elsewhere.write_text(json.dumps(forged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(rd.DispatchError, match="not the live qualification authority"):
        rd._verified_receipt(elsewhere)
