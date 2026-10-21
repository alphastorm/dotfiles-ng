"""Provider-free latency evidence, never an authorization or settlement decision."""

import json
from pathlib import Path

import pytest

import review_dispatch as rd


def test_measurement_table_reproduces_recorded_sample():
    fixture = json.loads(Path(__file__).with_name("review-latency.fixture.json").read_text())
    report = rd.review_latency(fixture["attempts"], fixture["launchTimings"])
    assert report["batchCount"] == 17
    assert report["seats"]["review-grok"]["elapsedMs"] == {
        "n": 17, "p50": 884189, "p95": 1233823,
    }
    assert report["seats"]["review-daybreak-blue"]["elapsedMs"]["p95"] == 391029
    assert report["seats"]["review-gemini-argon"]["elapsedMs"]["p50"] == 237454
    assert report["seats"]["review-grok"]["queueWaitMs"] == {"n": 17, "p50": 0, "p95": 0}
    assert report["classes"]["focused"]["batchDurationMs"] == {
        "n": 15, "p50": 884210, "p95": 1090792,
    }
    assert report["classes"]["focused"]["longestSeats"] == {"review-grok": 15}
    assert report["classes"]["initial"]["longestSeats"] == {
        "review-claude-fable": 1, "review-claude-mythos": 1,
    }
    assert report["seats"]["review-grok"]["outcomes"] == {"completed": 17}
    assert report["seats"]["review-grok"]["firstTokenAtCount"] == 0


def test_missing_queue_is_unknown_not_zero_and_copies_do_not_inflate_sample():
    fixture = json.loads(Path(__file__).with_name("review-latency.fixture.json").read_text())
    record = fixture["attempts"][0]
    report = rd.review_latency([record, record])
    assert report["batchCount"] == 1
    assert report["seats"]["review-grok"]["queueWaitMs"] == {"n": 0, "p50": None, "p95": None}
    assert all(row["startedAt"] is None for row in report["samples"])
    assert all(row["endedAt"] is None for row in report["samples"])


def test_conflicting_duplicate_is_refused():
    fixture = json.loads(Path(__file__).with_name("review-latency.fixture.json").read_text())
    record = fixture["attempts"][0]
    conflicting = json.loads(json.dumps(record))
    conflicting["result"]["details"]["totalDurationMs"] += 1
    with pytest.raises(rd.DispatchError, match="conflicting"):
        rd.review_latency([record, conflicting])


def test_abort_error_and_unknown_are_not_clean():
    fixture = json.loads(Path(__file__).with_name("review-latency.fixture.json").read_text())
    record = fixture["attempts"][0]
    rows = record["result"]["details"]["results"]
    rows[0]["aborted"] = True
    rows[1]["exitCode"] = 1
    rows[2].pop("exitCode")
    report = rd.review_latency([record])
    assert [row["outcome"] for row in report["samples"]] == ["abort", "error", "unknown"]
