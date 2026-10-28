#!/usr/bin/env python3
"""No-provider CLI coverage for exact-head end-to-end review admission."""
from __future__ import annotations

import hashlib
import json
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import review_budget as budget

HEAD = "a" * 40
SCRIPT = Path(budget.__file__).resolve()


def invoke(lane_dir, command, *args, round_number=1, head=HEAD):
    return subprocess.run(
        [sys.executable, str(SCRIPT), command, "--lane-dir", str(lane_dir),
         "--round", str(round_number), "--head", head, *map(str, args)],
        capture_output=True, check=False,
    )


def run(lane_dir, cwd, code="pass", *, round_number=1, head=HEAD, name="fake-e2e", extra=()):
    return invoke(lane_dir, "e2e", "--name", name, "--cwd", cwd, "--",
                  sys.executable, "-c", code, *extra, round_number=round_number, head=head)


@pytest.fixture
def spec(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text("{}\n", encoding="utf-8")
    return path


def test_e2e_receipt_round_trip_appends_and_captures_exact_output(tmp_path, spec):
    lane = tmp_path / "lane"
    stdout, stderr = b"a" * 3000 + b"\xff", b"b" * 4000 + b"\x00"
    code = f"""import json, os, signal, subprocess, sys
from pathlib import Path
assert sys.argv[1:] == ['', ' spaced argument ']
worker = "from pathlib import Path; import os, signal; Path('pending.json').write_text('{{\\\"sent\\\": true}}'); os.kill(os.getpid(), signal.SIGTERM)"
crash = subprocess.run([sys.executable, '-c', worker], check=False)
assert crash.returncode == -signal.SIGTERM
assert json.loads(Path('pending.json').read_text()) == {{'sent': True}}
Path('pending.json').replace('reconciled.json')
Path('export.json').write_bytes(Path('reconciled.json').read_bytes())
sys.stdout.buffer.write({stdout!r})
sys.stderr.buffer.write({stderr!r})
"""
    result = run(lane, tmp_path, code, name="fake-provider-recovery-export", extra=("", " spaced argument "))
    assert result.returncode == 0, result.stderr.decode()
    assert (tmp_path / "export.json").read_bytes() == (tmp_path / "reconciled.json").read_bytes()
    second = run(lane, tmp_path, name="second-path")
    assert second.returncode == 0, second.stderr.decode()
    receipt = json.loads((lane / "e2e-r1.json").read_text())
    assert receipt["head"] == HEAD
    first, appended = receipt["runs"]
    assert first["head"] == HEAD
    assert first["name"] == "fake-provider-recovery-export"
    assert first["argv"] == [sys.executable, "-c", code, "", " spaced argument "]
    assert first["cwd"] == str(tmp_path.resolve())
    assert first["rc"] == 0
    started, finished = (datetime.fromisoformat(first[key]) for key in ("started_at", "finished_at"))
    assert started.tzinfo is not None and finished >= started
    for stream, content in (("stdout", stdout), ("stderr", stderr)):
        assert first[stream] == {
            "sha256": hashlib.sha256(content).hexdigest(),
            "tail": content[-2048:].decode("utf-8", errors="replace"),
        }
    assert appended["name"] == "second-path"
    checked = invoke(lane, "check", "--spec", spec)
    assert checked.returncode == 0, checked.stderr.decode()


@pytest.mark.parametrize("round_number", [3, 10])
def test_check_refuses_a_third_round(tmp_path, spec, round_number):
    result = invoke(tmp_path / "lane", "check", "--spec", spec, round_number=round_number)
    assert result.returncode == 1
    assert b"two-round cap" in result.stderr


@pytest.mark.parametrize("fields", [{}, {"affected_invariants": []}, {"affected_invariants": None},
                                    {"affected_invariants": ""}])
def test_second_round_requires_fix_only_affected_invariants(tmp_path, spec, fields):
    lane = tmp_path / "lane"
    assert run(lane, tmp_path, round_number=2).returncode == 0
    spec.write_text(json.dumps(fields), encoding="utf-8")
    result = invoke(lane, "check", "--spec", spec, round_number=2)
    assert result.returncode == 1
    assert b"fix-only round 2 requires non-empty affected_invariants" in result.stderr


def test_second_round_accepts_named_affected_invariants(tmp_path, spec):
    lane = tmp_path / "lane"
    assert run(lane, tmp_path, round_number=2).returncode == 0
    spec.write_text(json.dumps({"affected_invariants": ["exact-head receipt binding"]}), encoding="utf-8")
    result = invoke(lane, "check", "--spec", spec, round_number=2)
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize("binding", ["receipt", "run"])
def test_check_binds_receipt_and_every_run_to_exact_head(tmp_path, spec, binding):
    lane = tmp_path / "lane"
    assert run(lane, tmp_path).returncode == 0
    path = lane / "e2e-r1.json"
    receipt = json.loads(path.read_text())
    target = receipt if binding == "receipt" else receipt["runs"][0]
    target["head"] = "b" * 40
    path.write_text(json.dumps(receipt), encoding="utf-8")
    result = invoke(lane, "check", "--spec", spec)
    assert result.returncode == 1
    assert b"different head" in result.stderr


def test_appending_another_head_refuses_before_command_execution(tmp_path):
    lane = tmp_path / "lane"
    assert run(lane, tmp_path).returncode == 0
    before = (lane / "e2e-r1.json").read_bytes()
    result = run(lane, tmp_path, "from pathlib import Path; Path('must-not-run').touch()", head="b" * 40)
    assert result.returncode == 1
    assert b"different head" in result.stderr
    assert not (tmp_path / "must-not-run").exists()
    assert (lane / "e2e-r1.json").read_bytes() == before


def test_failing_rc_is_preserved_and_a_later_pass_cannot_hide_it(tmp_path, spec):
    lane = tmp_path / "lane"
    failed = run(lane, tmp_path, "import sys; print('failed', file=sys.stderr); sys.exit(7)")
    assert failed.returncode == 7
    assert run(lane, tmp_path).returncode == 0
    receipt = json.loads((lane / "e2e-r1.json").read_text())
    assert [entry["rc"] for entry in receipt["runs"]] == [7, 0]
    assert receipt["runs"][0]["stderr"]["tail"] == "failed\n"
    result = invoke(lane, "check", "--spec", spec)
    assert result.returncode == 1
    assert b"nonzero" in result.stderr


def test_crashed_command_records_signal_and_refuses_review(tmp_path, spec):
    lane = tmp_path / "lane"
    crashed = run(lane, tmp_path, "import os, signal; os.kill(os.getpid(), signal.SIGTERM)")
    assert crashed.returncode == 128 + signal.SIGTERM
    receipt = json.loads((lane / "e2e-r1.json").read_text())
    assert receipt["runs"][0]["rc"] == -signal.SIGTERM
    assert invoke(lane, "check", "--spec", spec).returncode == 1


def test_missing_receipt_refuses_review(tmp_path, spec):
    result = invoke(tmp_path / "lane", "check", "--spec", spec)
    assert result.returncode == 1
    assert b"missing e2e receipt" in result.stderr


def test_receipt_without_runs_refuses_review(tmp_path, spec):
    lane = tmp_path / "lane"
    lane.mkdir()
    (lane / "e2e-r1.json").write_text(json.dumps({"head": HEAD, "runs": []}), encoding="utf-8")
    result = invoke(lane, "check", "--spec", spec)
    assert result.returncode == 1
    assert b"has no runs" in result.stderr


def test_e2e_inside_a_checkout_binds_its_actual_clean_head(tmp_path, spec):
    repo, lane = tmp_path / "repo", tmp_path / "lane"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    subprocess.run([*git, "add", "tracked.txt"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    head = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    wrong = run(lane, repo, head=HEAD)
    assert wrong.returncode == 1 and b"not head" in wrong.stderr
    assert not (lane / "e2e-r1.json").exists()
    (repo / "tracked.txt").write_text("edited\n", encoding="utf-8")
    dirty = run(lane, repo, head=head)
    assert dirty.returncode == 1 and b"uncommitted tracked changes" in dirty.stderr
    subprocess.run([*git, "checkout", "-q", "--", "tracked.txt"], check=True)
    assert run(lane, repo, head=head).returncode == 0
    assert invoke(lane, "check", "--spec", spec, head=head).returncode == 0
