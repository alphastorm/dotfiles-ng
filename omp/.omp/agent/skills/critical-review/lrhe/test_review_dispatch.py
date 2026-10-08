"""Meta-tests for atomic critical-review Task preparation and verification.

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
from test_review_sequence import _ready_record  # noqa: E402

# (lead_family, review_class, reviewer_id) -> (agent, selectionClass, role,
# independence_class, authority).
EXPECTED_TUPLES: dict[tuple[str, str, str], tuple[str, str, str, str, str]] = {
    ("gpt", "canary", "claude-opus"): (
        "review-claude-opus", "canary", qualification.STRONG_ROLE,
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("gpt", "canary", "gemini"): (
        "review-gemini", "canary", qualification.SUPPLEMENT_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("gpt", "canary", "grok"): (
        "review-grok", "canary", qualification.SUPPLEMENT_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("gpt", "canary", "daybreak-blue"): (
        "review-daybreak-blue", "canary", qualification.LEAD_FAMILY_SECURITY_ROLE,
        qualification.SAME_LINEAGE_BLIND_SAMPLE, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("gpt", "canary", "claude"): (
        "review-claude-fable", "canary", qualification.ARCHITECTURE_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("gpt", "canary", "glm"): (
        "review-glm-floor", "canary", "targeted_refuter",
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("gpt", "focused", "claude-opus"): (
        "review-claude-opus", "focused", qualification.STRONG_ROLE,
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("gpt", "focused", "grok"): (
        "review-grok", "supplement", qualification.SUPPLEMENT_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("gpt", "replay", "claude-opus"): (
        "review-claude-opus", "replay", qualification.STRONG_ROLE,
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("gpt", "initial", "claude-opus"): (
        "review-claude-opus", "strong", qualification.STRONG_ROLE,
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("gpt", "initial", "gemini"): (
        "review-gemini", "supplement", qualification.SUPPLEMENT_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("gpt", "initial", "grok"): (
        "review-grok", "supplement", qualification.SUPPLEMENT_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("gpt", "initial", "daybreak-blue"): (
        "review-daybreak-blue", "supplement", qualification.LEAD_FAMILY_SECURITY_ROLE,
        qualification.SAME_LINEAGE_BLIND_SAMPLE, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("gpt", "initial", "claude"): (
        "review-claude-fable", "conditional", qualification.ARCHITECTURE_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("gpt", "targeted-refuter", "glm"): (
        "review-glm-floor", "targeted", "targeted_refuter",
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("claude", "canary", "daybreak-blue"): (
        "review-daybreak-blue", "canary", qualification.STRONG_ROLE,
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("claude", "canary", "gemini"): (
        "review-gemini", "canary", qualification.SUPPLEMENT_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("claude", "canary", "grok"): (
        "review-grok", "canary", qualification.SUPPLEMENT_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("claude", "canary", "claude-mythos"): (
        "review-claude-mythos", "canary", qualification.LEAD_FAMILY_SECURITY_ROLE,
        qualification.SAME_LINEAGE_BLIND_SAMPLE, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("claude", "canary", "claude"): (
        "review-claude-fable", "canary", qualification.ARCHITECTURE_ROLE,
        qualification.SAME_LINEAGE_BLIND_SAMPLE, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("claude", "canary", "glm"): (
        "review-glm-floor", "canary", "targeted_refuter",
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("claude", "focused", "daybreak-blue"): (
        "review-daybreak-blue", "focused", qualification.STRONG_ROLE,
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("claude", "focused", "grok"): (
        "review-grok", "supplement", qualification.SUPPLEMENT_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("claude", "replay", "daybreak-blue"): (
        "review-daybreak-blue", "replay", qualification.STRONG_ROLE,
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("claude", "initial", "daybreak-blue"): (
        "review-daybreak-blue", "strong", qualification.STRONG_ROLE,
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
    ("claude", "initial", "gemini"): (
        "review-gemini", "supplement", qualification.SUPPLEMENT_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("claude", "initial", "grok"): (
        "review-grok", "supplement", qualification.SUPPLEMENT_ROLE,
        qualification.CROSS_FAMILY, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("claude", "initial", "claude-mythos"): (
        "review-claude-mythos", "supplement", qualification.LEAD_FAMILY_SECURITY_ROLE,
        qualification.SAME_LINEAGE_BLIND_SAMPLE, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("claude", "initial", "claude"): (
        "review-claude-fable", "conditional", qualification.ARCHITECTURE_ROLE,
        qualification.SAME_LINEAGE_BLIND_SAMPLE, qualification.SUPPLEMENTAL_EVIDENCE,
    ),
    ("claude", "targeted-refuter", "glm"): (
        "review-glm-floor", "targeted", "targeted_refuter",
        qualification.CROSS_FAMILY, qualification.INDEPENDENT_EVIDENCE,
    ),
}

EXPECTED_ARITY: dict[tuple[str, str], tuple[int, int]] = {
    ("gpt", "canary"): (1, 1),
    ("gpt", "focused"): (2, 2),
    ("gpt", "replay"): (1, 1),
    ("gpt", "initial"): (4, 5),
    ("gpt", "targeted-refuter"): (1, 1),
    ("claude", "canary"): (1, 1),
    ("claude", "focused"): (2, 2),
    ("claude", "replay"): (1, 1),
    ("claude", "initial"): (4, 5),
    ("claude", "targeted-refuter"): (1, 1),
}

EXPECTED_LEAD_FAMILIES = ("claude", "gpt")


def _live_document() -> dict:
    if not rd.LIVE_AUTHORITY.is_file():
        pytest.skip("private qualification authority is not present in this checkout")
    return yaml.safe_load(rd.LIVE_AUTHORITY.read_text(encoding="utf-8"))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _complete_inline_evidence(name: str, content: str) -> dict[str, object]:
    return {
        "format": rd.INLINE_EVIDENCE_FORMAT,
        "artifacts": [{"name": name, "sha256": _digest(content), "content": content}],
    }


def _packet_text(document: dict, *, record_path: str, record_sha256: str, diff: object) -> str:
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
            | (
                {document["oracleShadow"]["access_profile"]}
                if document.get("oracleShadow", {}).get("enabled") is True
                else set()
            )
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


@pytest.fixture
def council_material(tmp_path, authority) -> dict:
    """One ready record, packet, scope, and freshly resolved initial manifest."""

    root = tmp_path / "council"
    record = _ready_record(root)
    record_path = root / "review-record.json"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    packet_path = root / "packet.md"
    packet_path.write_text(
        _packet_text(
            authority,
            record_path=str(record_path.resolve()),
            record_sha256=_digest(record_path.read_text(encoding="utf-8")),
            diff="controller.py",
        ),
        encoding="utf-8",
    )
    scope_path = root / "scope.md"
    scope_path.write_text(
        "# Assurance scope\nClass: production/hard-to-reverse.\nAsset: dispatch policy.\n",
        encoding="utf-8",
    )
    bound_record, record_sha256, record_document = qualification.bind_record(record_path)
    bound_packet, packet_sha256, packet = qualification.bind_packet(
        packet_path, bound_record, record_sha256
    )
    authority_path, authority_sha256, authority_document = qualification.bind_qualification(
        rd.LIVE_AUTHORITY
    )
    manifest = qualification.select_full_council(
        authority_document,
        record_document,
        packet,
        lead_family="gpt",
        record_path=bound_record,
        record_sha256=record_sha256,
        packet_path=bound_packet,
        packet_sha256=packet_sha256,
        authority_path=authority_path,
        authority_sha256=authority_sha256,
    )
    manifest_path = root / "panel-selection.json"
    manifest_path.write_text(qualification.manifest_text(manifest), encoding="utf-8")
    return {
        "scope": scope_path,
        "packet": packet_path,
        "record": record_path,
        "manifest": manifest_path,
        "manifest_document": manifest,
    }


# --------------------------------------------------------------------------
# the standing matrix


def test_standing_matrix_is_exactly_the_configured_tuples(authority):
    """Every valid tuple, and no others, across all lead families and classes."""

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
        "reasonCodes": ["configured-strong-critic"],
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
        if review_class in ("initial", "focused"):
            continue  # a lone member of a multi-seat class fails minItems
        document = _receipt_document(
            packet_only_subject,
            lead_family=lead_family,
            review_class=review_class,
            assignments=[_assignment(reviewer_id, row)],
        )
        assert receipt_validator.is_valid(document), (
            f"{lead_family}/{review_class}/{reviewer_id} is configured but rejected"
        )


@pytest.mark.parametrize("lead_family", EXPECTED_LEAD_FAMILIES)
def test_receipt_schema_requires_the_whole_focused_roster(
    receipt_validator, packet_only_subject, lead_family: str
):
    """A focused receipt without its supplemental seat cannot even be recorded."""

    roster = [
        _assignment(reviewer_id, row)
        for (family, review_class, reviewer_id), row in EXPECTED_TUPLES.items()
        if family == lead_family and review_class == "focused"
    ]
    assert [row["reviewer_id"] for row in roster][1:] == ["grok"]
    for assignments, valid in ((roster, True), (roster[:1], False)):
        document = _receipt_document(
            packet_only_subject,
            lead_family=lead_family,
            review_class="focused",
            assignments=assignments,
        )
        assert receipt_validator.is_valid(document) is valid


@pytest.mark.parametrize(
    ("lead_family", "reviewer_id"),
    (("gpt", "claude-opus"), ("claude", "daybreak-blue")),
)
def test_receipt_schema_limits_replay_to_one_distinct_strong_critic(
    receipt_validator, packet_only_subject, lead_family: str, reviewer_id: str
):
    critic = _assignment(reviewer_id, EXPECTED_TUPLES[(lead_family, "replay", reviewer_id)])
    supplement = _assignment("grok", EXPECTED_TUPLES[(lead_family, "focused", "grok")])
    receipt = _receipt_document(
        packet_only_subject,
        lead_family=lead_family,
        review_class="replay",
        assignments=[critic],
    )
    assert receipt_validator.is_valid(receipt)
    for assignments in (
        [],
        [critic, supplement],
        [supplement],
        [dict(critic, selectionClass="focused")],
    ):
        assert not receipt_validator.is_valid(dict(receipt, assignments=assignments))
    assert not receipt_validator.is_valid(
        dict(receipt, reviewClass="focused", assignments=[critic, supplement])
    )


@pytest.mark.parametrize("lead_family", EXPECTED_LEAD_FAMILIES)
@pytest.mark.parametrize("review_class", ("canary", "focused", "initial", "targeted-refuter"))
def test_pre_replay_receipts_remain_schema_valid(
    receipt_validator, packet_only_subject, lead_family: str, review_class: str
):
    roster = [
        _assignment(reviewer_id, row)
        for (family, class_name, reviewer_id), row in EXPECTED_TUPLES.items()
        if family == lead_family and class_name == review_class
    ]
    if review_class == "canary":
        roster = roster[:1]
    receipt = _receipt_document(
        packet_only_subject,
        lead_family=lead_family,
        review_class=review_class,
        assignments=roster,
    )
    receipt["schemaVersion"] = 1
    receipt["panelId"] = "critical-review-primary-v9"
    assert receipt_validator.is_valid(receipt)


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
                (agent, selection_class, "supplement", independence_class, authority),
            )
        ],
    )
    assert not receipt_validator.is_valid(document)


def test_receipt_schema_refuses_a_wrong_authority(receipt_validator, packet_only_subject):
    agent, selection_class, role, independence_class, _authority = EXPECTED_TUPLES[
        ("claude", "initial", "gemini")
    ]
    document = _receipt_document(
        packet_only_subject,
        lead_family="claude",
        review_class="initial",
        assignments=[
            _assignment("daybreak-blue", EXPECTED_TUPLES[("claude", "initial", "daybreak-blue")]),
            _assignment(
                "gemini",
                (agent, selection_class, role, independence_class, qualification.INDEPENDENT_EVIDENCE),
            ),
            _assignment("grok", EXPECTED_TUPLES[("claude", "initial", "grok")]),
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


def test_evidence_compatibility_matrix(tmp_path, material, authority, repository):
    packet_only = rd.freeze_subject(
        scope_path=material["scope"], packet_path=material["packet"]
    ).subject
    content = "VALUE = 1\n"
    inline_packet = tmp_path / "inline-packet.md"
    inline_packet.write_text(
        _packet_text(
            authority,
            record_path="/frozen/review-record.json",
            record_sha256="a" * 64,
            diff=_complete_inline_evidence("src/dispatch.py", content),
        ),
        encoding="utf-8",
    )
    complete_inline = rd.freeze_subject(scope_path=material["scope"], packet_path=inline_packet)
    repository_subject = rd.freeze_subject(
        scope_path=material["scope"],
        packet_path=material["packet"],
        repository_path=repository["path"],
        subject_commit=repository["commit"],
        files=["src/dispatch.py"],
    )
    matrix = (
        (
            "packet-only + repository reviewer",
            packet_only,
            material["packet"],
            ("repository",),
            "evidence_delivery=repository",
        ),
        (
            "packet-only + path-only evidence",
            packet_only,
            material["packet"],
            ("inline",),
            "complete evidence bytes",
        ),
        (
            "packet-only + complete inline evidence",
            complete_inline.subject,
            inline_packet,
            ("inline",),
            None,
        ),
        (
            "repository reviewer + clean bound commit/files",
            repository_subject.subject,
            material["packet"],
            ("repository",),
            None,
        ),
    )
    for name, subject, packet_path, deliveries, refusal in matrix:
        packet = qualification.parse_packet(packet_path)
        if refusal is None:
            rd.validate_evidence_compatibility(subject, packet, deliveries)
        else:
            with pytest.raises(rd.DispatchError, match=refusal) as error:
                rd.validate_evidence_compatibility(subject, packet, deliveries)
            assert refusal in str(error.value), name


def test_freeze_binds_panel_manifest_and_its_evidence_modes(council_material, repository):
    verified = rd.freeze_subject(
        scope_path=council_material["scope"],
        packet_path=council_material["packet"],
        record_path=council_material["record"],
        manifest_path=council_material["manifest"],
        repository_path=repository["path"],
        subject_commit=repository["commit"],
        files=["src/dispatch.py"],
    )
    assert verified.subject.manifest_sha256 == _digest(
        council_material["manifest"].read_text(encoding="utf-8")
    )
    assert verified.manifest == council_material["manifest_document"]
    assert rd.verify_subject(verified.subject).manifest == council_material["manifest_document"]

    with pytest.raises(rd.DispatchError, match="evidence_delivery=repository"):
        rd.freeze_subject(
            scope_path=council_material["scope"],
            packet_path=council_material["packet"],
            record_path=council_material["record"],
            manifest_path=council_material["manifest"],
        )


def test_verify_subject_refuses_panel_schema_drift(
    tmp_path, council_material, repository, monkeypatch
):
    schema_copy = tmp_path / "panel-selection.schema.json"
    schema_source = Path(rd.__file__).parent / Path(rd.PANEL_SCHEMA_RELATIVE_PATH).name
    schema_copy.write_bytes(schema_source.read_bytes())
    bind_public_schema = rd.bind_public_schema

    def bind_from_copy(relative: str):
        if relative != rd.PANEL_SCHEMA_RELATIVE_PATH:
            return bind_public_schema(relative)
        raw = schema_copy.read_bytes()
        return schema_copy, hashlib.sha256(raw).hexdigest(), json.loads(raw)

    monkeypatch.setattr(rd, "bind_public_schema", bind_from_copy)
    verified = rd.freeze_subject(
        scope_path=council_material["scope"],
        packet_path=council_material["packet"],
        record_path=council_material["record"],
        manifest_path=council_material["manifest"],
        repository_path=repository["path"],
        subject_commit=repository["commit"],
        files=["src/dispatch.py"],
    )
    schema_copy.write_text(schema_copy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(rd.DispatchError, match="panel manifest schema now digests"):
        rd.verify_subject(verified.subject)


def _initial_prepare_args(council_material, repository, paths):
    return [
        "prepare",
        "--scope",
        str(council_material["scope"]),
        "--packet",
        str(council_material["packet"]),
        "--record",
        str(council_material["record"]),
        "--manifest",
        str(paths["manifest"]),
        "--repo",
        str(repository["path"]),
        "--commit",
        repository["commit"],
        "--file",
        "src/dispatch.py",
        "--lead-family",
        "gpt",
        "--review-class",
        "initial",
        "--subject",
        str(paths["subject"]),
        "--receipt",
        str(paths["receipt"]),
        "--out",
        str(paths["envelope"]),
    ]


def _initial_prepare_paths(root):
    return {
        "manifest": root / "panel-selection.json",
        "subject": root / "frozen-subject.json",
        "receipt": root / "resolver-receipt.json",
        "envelope": root / "review-dispatch-envelope.json",
    }


def test_prepare_builds_the_verified_initial_dispatch_atomically(
    tmp_path, council_material, repository, capsys
):
    paths = _initial_prepare_paths(tmp_path / "prepared")
    assert rd.main(_initial_prepare_args(council_material, repository, paths)) == 0
    emitted = json.loads(capsys.readouterr().out)["task_input"]
    assert all(path.is_file() for path in paths.values())
    subject = json.loads(paths["subject"].read_text(encoding="utf-8"))
    assert subject["manifestPath"] == str(paths["manifest"].resolve())
    manifest_text = paths["manifest"].read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert len(emitted["tasks"]) == len(manifest["selected"])
    envelope = json.loads(paths["envelope"].read_text(encoding="utf-8"))
    shadow = envelope["oracleShadow"]
    assert envelope["schemaVersion"] == 2
    assert shadow["selection"] == qualification.ORACLE_SHADOW_SELECTED
    assert shadow["reasonCodes"] == []
    assert shadow["standing"] == "none"
    assert shadow["blocksClosure"] is False
    assert shadow["receivesPeerOutput"] is False
    assert shadow["request"]["subjectCommit"] == repository["commit"]
    assert shadow["request"]["files"] == ["src/dispatch.py"]
    assert shadow["request"]["prompt"].startswith(rd.ORACLE_SHADOW_MARKER + "\n")
    assert _digest(shadow["request"]["prompt"]) == shadow["request"]["promptSha256"]
    assert shadow["requestPath"].startswith(str(council_material["record"].parent))
    record = json.loads(council_material["record"].read_text(encoding="utf-8"))
    run_digest = hashlib.sha256(
        f"{record['review_id']}\0{envelope['receiptSha256']}".encode("utf-8")
    ).hexdigest()[:16]
    assert Path(shadow["requestPath"]).name == (
        f"oracle-shadow-{envelope['subjectDigest'][:16]}-{run_digest}-request.json"
    )
    assert Path(shadow["datasetRecordPath"]).name == (
        f"{envelope['subjectDigest']}-{run_digest}.json"
    )

    envelope_sha256 = _digest(paths["envelope"].read_text(encoding="utf-8"))
    paths["manifest"].chmod(0o644)
    paths["manifest"].write_text(manifest_text + "\n", encoding="utf-8")
    assert (
        rd.main(
            [
                "verify-task",
                "--envelope",
                str(paths["envelope"]),
                "--sha256",
                envelope_sha256,
            ]
        )
        == 1
    )
    refusal = capsys.readouterr()
    assert refusal.out == ""
    assert "panel manifest now digests" in refusal.err


def test_prepare_refuses_a_dirty_repository_before_writing_artifacts(
    tmp_path, council_material, repository, capsys
):
    (repository["path"] / "src/dispatch.py").write_text("VALUE = 99\n", encoding="utf-8")
    paths = _initial_prepare_paths(tmp_path / "dirty-prepared")
    assert rd.main(_initial_prepare_args(council_material, repository, paths)) == 1
    captured = capsys.readouterr()
    assert "modified or untracked" in captured.err
    assert not any(path.exists() for path in paths.values())


def test_prepare_rolls_back_every_artifact_when_final_verification_fails(
    tmp_path, council_material, repository, monkeypatch, capsys
):
    paths = _initial_prepare_paths(tmp_path / "failed-prepared")

    def refuse_final_verification(_args):
        raise rd.DispatchError("forced final verification refusal")

    monkeypatch.setattr(rd, "command_verify_task", refuse_final_verification)
    assert rd.main(_initial_prepare_args(council_material, repository, paths)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "forced final verification refusal" in captured.err
    assert not any(path.exists() for path in paths.values())


def test_prepare_never_publishes_envelope_after_dependency_write_failure(
    tmp_path, council_material, repository, monkeypatch, capsys
):
    paths = _initial_prepare_paths(tmp_path / "dependency-failure")
    write_once = rd._write_once
    attempted: list[str] = []

    def fail_receipt(path, text, label):
        attempted.append(label)
        if label == "resolver receipt":
            raise rd.DispatchError("forced receipt write failure")
        return write_once(path, text, label)

    monkeypatch.setattr(rd, "_write_once", fail_receipt)
    assert rd.main(_initial_prepare_args(council_material, repository, paths)) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "forced receipt write failure" in captured.err
    assert attempted == [
        "panel selection manifest",
        "frozen subject",
        "resolver receipt",
    ]
    assert not any(path.exists() for path in paths.values())


def test_prepare_enforces_packet_only_evidence_end_to_end(tmp_path, material, authority, capsys):
    def output_paths(root):
        return {
            "subject": root / "frozen-subject.json",
            "receipt": root / "resolver-receipt.json",
            "envelope": root / "review-dispatch-envelope.json",
        }

    def prepare_args(packet_path, paths):
        return [
            "prepare",
            "--scope",
            str(material["scope"]),
            "--packet",
            str(packet_path),
            "--lead-family",
            "gpt",
            "--review-class",
            "focused",
            "--subject",
            str(paths["subject"]),
            "--receipt",
            str(paths["receipt"]),
            "--out",
            str(paths["envelope"]),
        ]

    repository_only = output_paths(tmp_path / "repository-only")
    assert rd.main(prepare_args(material["packet"], repository_only)) == 1
    refusal = capsys.readouterr()
    assert "evidence_delivery=repository" in refusal.err
    assert refusal.out == ""
    assert not any(path.exists() for path in repository_only.values())

    for reviewer_id in ("claude-opus", "grok"):
        authority["reviewers"][reviewer_id]["evidenceDelivery"] = "inline"
        authority["reviewers"][reviewer_id]["tools"] = []
    validated_authority = qualification.validate_qualification(authority)
    rd.LIVE_AUTHORITY.write_text(
        yaml.safe_dump(validated_authority, sort_keys=False), encoding="utf-8"
    )
    (rd.LIVE_AUTHORITY.parent / rd.RECEIPT_SCHEMA_FILENAME).write_text(
        rd.receipt_schema_text(validated_authority), encoding="utf-8"
    )

    path_packet = tmp_path / "path-packet.md"
    path_packet.write_text(
        _packet_text(
            validated_authority,
            record_path="/frozen/review-record.json",
            record_sha256="a" * 64,
            diff="src/dispatch.py",
        ),
        encoding="utf-8",
    )
    path_only = output_paths(tmp_path / "path-only")
    assert rd.main(prepare_args(path_packet, path_only)) == 1
    refusal = capsys.readouterr()
    assert "complete evidence bytes" in refusal.err
    assert refusal.out == ""
    assert not any(path.exists() for path in path_only.values())

    content = "VALUE = 1\n"
    inline_packet = tmp_path / "complete-inline-packet.md"
    inline_packet.write_text(
        _packet_text(
            validated_authority,
            record_path="/frozen/review-record.json",
            record_sha256="a" * 64,
            diff=_complete_inline_evidence("src/dispatch.py", content),
        ),
        encoding="utf-8",
    )
    complete = output_paths(tmp_path / "complete")
    assert rd.main(prepare_args(inline_packet, complete)) == 0
    emitted = json.loads(capsys.readouterr().out)["task_input"]
    assert [item["agent"] for item in emitted["tasks"]] == ["review-claude-opus", "review-grok"]
    # Nothing is retrieved for a packet-only subject, so the retrieval directive
    # would be an instruction about paths this reviewer must not open.
    assert all(rd.BATCHED_RETRIEVAL_DIRECTIVE not in item["task"] for item in emitted["tasks"])
    assert all(path.is_file() for path in complete.values())

    envelope_sha256 = _digest(complete["envelope"].read_text(encoding="utf-8"))
    inline_packet.write_text(
        inline_packet.read_text(encoding="utf-8").replace("VALUE = 1", "VALUE = 2"),
        encoding="utf-8",
    )
    assert (
        rd.main(
            [
                "verify-task",
                "--envelope",
                str(complete["envelope"]),
                "--sha256",
                envelope_sha256,
            ]
        )
        == 1
    )
    refusal = capsys.readouterr()
    assert refusal.out == ""
    assert "packet now digests" in refusal.err


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


def test_focused_refuses_every_caller_selected_alternative(material, authority):
    document = qualification.validate_qualification(authority)
    verified = rd.freeze_subject(scope_path=material["scope"], packet_path=material["packet"])
    for reviewer_ids in (
        ["claude-opus"],
        ["grok", "claude-opus"],
        ["claude-opus", "gemini"],
        ["claude-opus", "grok", "gemini"],
        ["daybreak-blue", "grok"],
    ):
        with pytest.raises(rd.DispatchError, match="complete resolved roster"):
            rd.resolve_assignments(
                document,
                verified,
                lead_family="gpt",
                review_class="focused",
                reviewer_ids=reviewer_ids,
                authority_path=rd.LIVE_AUTHORITY,
                authority_sha256="0" * 64,
            )


@pytest.mark.parametrize(
    ("lead_family", "reviewer_id"),
    (("gpt", "claude-opus"), ("claude", "daybreak-blue")),
)
def test_focused_roster_is_the_reciprocal_strong_critic_then_grok(
    material, authority, repository, lead_family: str, reviewer_id: str
):
    document = qualification.validate_qualification(authority)
    verified = rd.freeze_subject(
        scope_path=material["scope"],
        packet_path=material["packet"],
        repository_path=repository["path"],
        subject_commit=repository["commit"],
        files=["src/dispatch.py"],
    )
    strong, supplement = rd.resolve_assignments(
        document,
        verified,
        lead_family=lead_family,
        review_class="focused",
        reviewer_ids=[reviewer_id, "grok"],
        authority_path=rd.LIVE_AUTHORITY,
        authority_sha256="0" * 64,
    )
    assert strong.reason_codes == (qualification.STRONG_REASON_CODE,)
    assert strong.role == qualification.STRONG_ROLE
    assert strong.authority == qualification.INDEPENDENT_EVIDENCE
    assert supplement.reviewer_id == "grok"
    assert supplement.reason_codes == (qualification.SUPPLEMENT_REASON_CODE,)
    assert supplement.role == qualification.SUPPLEMENT_ROLE
    assert supplement.authority == qualification.SUPPLEMENTAL_EVIDENCE


def test_focused_refuses_a_packet_that_withholds_the_supplement_grant(
    tmp_path, material, authority
):
    """Grok's vendor grant is checked like the anchor's; it is never dropped."""

    text = material["packet"].read_text(encoding="utf-8")
    assert "- xai\n" in text
    packet = tmp_path / "no-xai-packet.md"
    packet.write_text(text.replace("- xai\n", ""), encoding="utf-8")
    document = qualification.validate_qualification(authority)
    verified = rd.freeze_subject(scope_path=material["scope"], packet_path=packet)
    with pytest.raises(rd.DispatchError, match=r"does not authorize reviewers\.grok"):
        rd.resolve_assignments(
            document,
            verified,
            lead_family="gpt",
            review_class="focused",
            reviewer_ids=["claude-opus", "grok"],
            authority_path=rd.LIVE_AUTHORITY,
            authority_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    ("lead_family", "reviewer_id"),
    (("gpt", "claude-opus"), ("claude", "daybreak-blue")),
)
@pytest.mark.parametrize("supplements", (["grok"], ["gemini", "grok"]))
def test_replay_resolves_only_the_reciprocal_critic_without_supplement_grants(
    material, authority, repository, lead_family: str, reviewer_id: str, supplements: list[str]
):
    authority["liveDispatch"]["byLeadFamily"][lead_family]["focusedSupplements"] = supplements
    document = qualification.validate_qualification(authority)
    packet_text = material["packet"].read_text(encoding="utf-8")
    assert "- xai\n" in packet_text
    material["packet"].write_text(packet_text.replace("- xai\n", ""), encoding="utf-8")
    verified = rd.freeze_subject(
        scope_path=material["scope"],
        packet_path=material["packet"],
        repository_path=repository["path"],
        subject_commit=repository["commit"],
        files=["src/dispatch.py"],
    )
    assert verified.record is None
    assignments = rd.resolve_assignments(
        document,
        verified,
        lead_family=lead_family,
        review_class="replay",
        reviewer_ids=[reviewer_id],
        authority_path=rd.LIVE_AUTHORITY,
        authority_sha256="0" * 64,
    )
    assert len(assignments) == 1
    critic = assignments[0]
    assert (
        critic.agent,
        critic.selection_class,
        critic.role,
        critic.independence_class,
        critic.authority,
    ) == EXPECTED_TUPLES[(lead_family, "replay", reviewer_id)]
    assert critic.reason_codes == (qualification.STRONG_REASON_CODE,)
    assert rd.roster_arity(document, lead_family, "replay") == (1, 1)
    for supplement in supplements:
        for requested in ([supplement], [reviewer_id, supplement]):
            with pytest.raises(rd.DispatchError, match="complete resolved roster"):
                rd.resolve_assignments(
                    document,
                    verified,
                    lead_family=lead_family,
                    review_class="replay",
                    reviewer_ids=requested,
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
# the whole record-free path


def _record_free_prepare_args(
    tmp_path, material, repository, *, lead_family="gpt", review_class="focused"
):
    paths = {
        "subject": tmp_path / "frozen-subject.json",
        "receipt": tmp_path / "resolver-receipt.json",
        "envelope": tmp_path / "review-dispatch-envelope.json",
    }
    return paths, [
        "prepare",
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
        "--lead-family",
        lead_family,
        "--review-class",
        review_class,
        "--subject",
        str(paths["subject"]),
        "--receipt",
        str(paths["receipt"]),
        "--out",
        str(paths["envelope"]),
    ]


def test_focused_prepare_rejects_every_reviewer_override(
    tmp_path, material, repository
):
    paths, argv = _record_free_prepare_args(tmp_path, material, repository)
    with pytest.raises(SystemExit) as refusal:
        rd.main([*argv, "--reviewer", "gemini"])
    assert refusal.value.code == 2
    assert not any(path.exists() for path in paths.values())


def test_focused_prepare_end_to_end(tmp_path, material, repository, capsys):
    paths, argv = _record_free_prepare_args(tmp_path, material, repository)
    assert rd.main(argv) == 0
    emitted = json.loads(capsys.readouterr().out)["task_input"]

    subject = json.loads(paths["subject"].read_text(encoding="utf-8"))
    assert subject["kind"] == "repository"
    assert subject["files"] == ["src/dispatch.py"]
    assert subject["subjectCommit"] == repository["commit"]
    receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    assert receipt["reviewClass"] == "focused"
    assert [row["reviewer_id"] for row in receipt["assignments"]] == ["claude-opus", "grok"]
    for assignment in receipt["assignments"]:
        assert (
            assignment["agent"],
            assignment["selectionClass"],
            assignment["role"],
            assignment["independence_class"],
            assignment["authority"],
        ) == EXPECTED_TUPLES[("gpt", "focused", assignment["reviewer_id"])]

    envelope_sha256 = _digest(paths["envelope"].read_text(encoding="utf-8"))
    assert (
        rd.main(
            [
                "verify-task",
                "--envelope",
                str(paths["envelope"]),
                "--sha256",
                envelope_sha256,
            ]
        )
        == 0
    )
    approved = json.loads(capsys.readouterr().out)["task_input"]
    assert approved == emitted
    assert set(approved) == {"i", "context", "tasks"}
    assert approved["i"] == rd.DISPATCH_TASK_INTENT
    assert [item["agent"] for item in approved["tasks"]] == ["review-claude-opus", "review-grok"]
    for item, assignment in zip(approved["tasks"], receipt["assignments"], strict=True):
        task = item["task"]
        assert task.startswith(f"{rd.RECEIPT_MARKER}\n")
        assert f"subject_commit={repository['commit']}" in task
        assert f"repository_path={repository['path'].resolve()}" in task
        assert rd.BATCHED_RETRIEVAL_DIRECTIVE in task
        assert f"authority={assignment['authority']}" in task
        assert material["scope"].read_text(encoding="utf-8").rstrip("\n") in task
        assert material["packet"].read_text(encoding="utf-8").rstrip("\n") in task

    material["scope"].write_text("# Assurance scope\nClass: bounded experiment.\n", "utf-8")
    assert (
        rd.main(
            [
                "verify-task",
                "--envelope",
                str(paths["envelope"]),
                "--sha256",
                envelope_sha256,
            ]
        )
        == 1
    )
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("lead_family", "reviewer_id"),
    (("gpt", "claude-opus"), ("claude", "daybreak-blue")),
)
def test_replay_prepare_end_to_end(
    tmp_path, material, repository, capsys, lead_family: str, reviewer_id: str
):
    paths, argv = _record_free_prepare_args(
        tmp_path, material, repository, lead_family=lead_family, review_class="replay"
    )
    assert rd.main(argv) == 0
    emitted = json.loads(capsys.readouterr().out)["task_input"]
    subject = json.loads(paths["subject"].read_text(encoding="utf-8"))
    assert "recordPath" not in subject
    receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    assert receipt["reviewClass"] == "replay"
    assert [row["reviewer_id"] for row in receipt["assignments"]] == [reviewer_id]
    assignment = receipt["assignments"][0]
    assert (
        assignment["agent"],
        assignment["selectionClass"],
        assignment["role"],
        assignment["independence_class"],
        assignment["authority"],
    ) == EXPECTED_TUPLES[(lead_family, "replay", reviewer_id)]
    envelope = json.loads(paths["envelope"].read_text(encoding="utf-8"))
    assert envelope["reviewClass"] == "replay"
    assert envelope["oracleShadow"] is None
    assert [task["agent"] for task in emitted["tasks"]] == [assignment["agent"]]
    assert "review_class=replay" in emitted["tasks"][0]["task"]
    assert rd.main(
        [
            "verify-task",
            "--envelope",
            str(paths["envelope"]),
            "--sha256",
            _digest(paths["envelope"].read_text(encoding="utf-8")),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["task_input"] == emitted


@pytest.mark.parametrize(
    ("lead_family", "reviewer_id"),
    (("gpt", "claude-opus"), ("claude", "daybreak-blue")),
)
def test_replay_prepare_rejects_even_the_resolved_reviewer_override(
    tmp_path, material, repository, lead_family: str, reviewer_id: str
):
    paths, argv = _record_free_prepare_args(
        tmp_path, material, repository, lead_family=lead_family, review_class="replay"
    )
    for requested in (reviewer_id, "grok"):
        with pytest.raises(SystemExit) as refusal:
            rd.main([*argv, "--reviewer", requested])
        assert refusal.value.code == 2
        assert not any(path.exists() for path in paths.values())


def _canary_prepare_args(tmp_path, material, repository, *, reviewer: str | None = "gemini"):
    paths = {
        "subject": tmp_path / "frozen-subject.json",
        "receipt": tmp_path / "resolver-receipt.json",
        "envelope": tmp_path / "review-dispatch-envelope.json",
    }
    argv = [
        "prepare",
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
        "--lead-family",
        "gpt",
        "--review-class",
        rd.CANARY,
        "--subject",
        str(paths["subject"]),
        "--receipt",
        str(paths["receipt"]),
        "--out",
        str(paths["envelope"]),
    ]
    if reviewer is not None:
        argv += ["--reviewer", reviewer]
    return paths, argv


def test_canary_prepare_probes_one_named_lane(tmp_path, material, repository, capsys):
    """A probe is one seat in its own selection class, disclosed, with no shadow."""

    paths, argv = _canary_prepare_args(tmp_path, material, repository)
    assert rd.main(argv) == 0
    emitted = json.loads(capsys.readouterr().out)["task_input"]

    receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    assert receipt["reviewClass"] == rd.CANARY
    assert [row["reviewer_id"] for row in receipt["assignments"]] == ["gemini"]
    assignment = receipt["assignments"][0]
    agent, selection_class, role, independence_class, authority = EXPECTED_TUPLES[
        ("gpt", "canary", "gemini")
    ]
    assert (
        assignment["agent"],
        assignment["selectionClass"],
        assignment["role"],
        assignment["independence_class"],
        assignment["authority"],
        assignment["reasonCodes"],
    ) == (
        agent,
        selection_class,
        role,
        independence_class,
        authority,
        [rd.CANARY_REASON_CODE],
    )

    envelope = json.loads(paths["envelope"].read_text(encoding="utf-8"))
    assert envelope["reviewClass"] == rd.CANARY
    assert envelope["oracleShadow"] is None
    assert len(emitted["tasks"]) == 1
    task = emitted["tasks"][0]["task"]
    assert f"review_class={rd.CANARY}" in task
    assert rd.CANARY_PROBE_DISCLOSURE in task

    envelope_sha256 = _digest(paths["envelope"].read_text(encoding="utf-8"))
    assert (
        rd.main(
            [
                "verify-task",
                "--envelope",
                str(paths["envelope"]),
                "--sha256",
                envelope_sha256,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["task_input"] == emitted


def test_canary_prepare_refuses_an_unconfigured_lane_a_record_and_no_lane(
    tmp_path, material, repository
):
    """A probe names one configured lane and nothing else, and writes nothing when it cannot."""

    paths, argv = _canary_prepare_args(tmp_path, material, repository, reviewer="minimax")
    assert rd.main(argv) == 1
    assert not any(path.exists() for path in paths.values())

    record = tmp_path / "canary-record.json"
    record.write_text('{"review_id": "probe"}\n', encoding="utf-8")
    paths, argv = _canary_prepare_args(tmp_path, material, repository)
    assert rd.main([*argv, "--record", str(record)]) == 1
    assert not any(path.exists() for path in paths.values())

    paths, argv = _canary_prepare_args(tmp_path, material, repository, reviewer=None)
    with pytest.raises(SystemExit) as refusal:
        rd.main(argv)
    assert refusal.value.code == 2
    assert not any(path.exists() for path in paths.values())


def test_targeted_refuter_prepare_infers_the_fixed_pool(tmp_path, authority, repository, capsys):
    root = tmp_path / "targeted"
    record = _ready_record(root, "remediation")
    record["resolved_finding_ids"] = []
    record["disputed_or_unresolved_p01"] = ["P1-001"]
    record["lead_verification"] = [
        {
            "finding_id": "P1-001",
            "result": "disputed",
            "evidence": "direct reproducer is inconclusive",
        }
    ]
    record_path = root / "review-record.json"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    packet_path = root / "packet.md"
    packet_path.write_text(
        _packet_text(
            authority,
            record_path=str(record_path.resolve()),
            record_sha256=_digest(record_path.read_text(encoding="utf-8")),
            diff="src/dispatch.py",
        ),
        encoding="utf-8",
    )
    scope_path = root / "scope.md"
    scope_path.write_text("# Targeted refutation scope\n", encoding="utf-8")
    paths = {
        "subject": root / "frozen-subject.json",
        "receipt": root / "resolver-receipt.json",
        "envelope": root / "review-dispatch-envelope.json",
    }
    assert (
        rd.main(
            [
                "prepare",
                "--scope",
                str(scope_path),
                "--packet",
                str(packet_path),
                "--record",
                str(record_path),
                "--repo",
                str(repository["path"]),
                "--commit",
                repository["commit"],
                "--file",
                "src/dispatch.py",
                "--lead-family",
                "gpt",
                "--review-class",
                "targeted-refuter",
                "--subject",
                str(paths["subject"]),
                "--receipt",
                str(paths["receipt"]),
                "--out",
                str(paths["envelope"]),
            ]
        )
        == 0
    )
    emitted = json.loads(capsys.readouterr().out)["task_input"]
    receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    expected = [
        reviewer.reviewer_id
        for reviewer in qualification.global_reviewers(authority, "targetedRefuters", "gpt")
    ]
    assert [row["reviewer_id"] for row in receipt["assignments"]] == expected
    assert len(emitted["tasks"]) == len(expected)


@pytest.mark.parametrize("legacy_command", ["freeze", "resolve", "dispatch"])
def test_cli_has_no_manual_dispatch_stage(legacy_command):
    with pytest.raises(SystemExit) as refusal:
        rd.main([legacy_command])
    assert refusal.value.code == 2


def test_generated_artifacts_are_read_only_and_never_overwritten(tmp_path, material, repository):
    paths, argv = _record_free_prepare_args(tmp_path, material, repository)
    assert rd.main(argv) == 0
    assert all(path.stat().st_mode & 0o777 == 0o444 for path in paths.values())
    assert rd.main(argv) == 1


def test_a_receipt_naming_another_authority_is_refused(tmp_path, material, repository, capsys):
    paths, argv = _record_free_prepare_args(tmp_path, material, repository)
    assert rd.main(argv) == 0
    capsys.readouterr()
    forged = json.loads(paths["receipt"].read_text(encoding="utf-8"))
    forged["authorityPath"] = str(tmp_path / "elsewhere/qualification.yml")
    elsewhere = tmp_path / "forged-receipt.json"
    elsewhere.write_text(json.dumps(forged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(rd.DispatchError, match="not the live qualification authority"):
        rd._verified_receipt(elsewhere)
