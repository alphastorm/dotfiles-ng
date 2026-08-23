from __future__ import annotations

import os
import re
import socket
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "lrhe.yml"
_NONCE = "12345678-1234-1234-1234-123456789abc"
_VALID_ENVIRONMENT = {
    "TARGET_SHA": "a" * 40,
    "ROUTING_LABEL": f"lrhe-linux-x64-{_NONCE}",
    "FLEET_SITE": "nyc-pc",
    "DISPATCH_ID": f"lrhe-nyc-20260823T010203Z-{_NONCE}",
    "RUNNER_NAME": "gateway-ci-nyc-1",
}


def _source() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _validation_script() -> str:
    block = (
        _source()
        .split("      - name: Validate bounded LRHE dispatch", 1)[1]
        .split("\n      - name:", 1)[0]
    )
    return textwrap.dedent(block.split("        run: |\n", 1)[1])


def _run_validation(
    socket_path: Path,
    *,
    updates: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    script = _validation_script().replace("/var/run/docker.sock", str(socket_path))
    environment = {**os.environ, **_VALID_ENVIRONMENT, **(updates or {})}
    return subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", script],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )


def test_fleet_workflow_keeps_automatic_checks_hosted_and_fails_closed() -> None:
    source = _source()
    script = _validation_script()

    assert "runs-on: ubuntu-latest" in source
    assert (
        "inputs.fleet_site == 'nyc-pc' && 'site-nyc' || "
        "inputs.fleet_site == 'sf-pc' && 'site-sf' || 'site-invalid'"
    ) in source
    assert source.index("Validate bounded LRHE dispatch") < source.index("Check out exact target")
    predicate_lines = [
        line.strip() for line in script.splitlines() if line.strip().startswith("[[")
    ]
    assert len(predicate_lines) == 5
    assert all(re.search(r"\]\] \|\| fail [a-z_]+$", line) for line in predicate_lines)
    assert "lrhe-nyc-" not in source
    assert "gateway-ci-nyc-1" not in source


@pytest.mark.parametrize(("fleet_site", "site_short"), [("nyc-pc", "nyc"), ("sf-pc", "sf")])
def test_fleet_validation_accepts_each_qualified_site(
    tmp_path: Path,
    fleet_site: str,
    site_short: str,
) -> None:
    updates = {
        "FLEET_SITE": fleet_site,
        "DISPATCH_ID": f"lrhe-{site_short}-20260823T010203Z-{_NONCE}",
        "RUNNER_NAME": f"gateway-ci-{site_short}-1",
    }
    satisfied = _run_validation(tmp_path / "absent.sock", updates=updates).returncode == 0
    assert satisfied is True


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("TARGET_SHA", "A" * 40),
        ("ROUTING_LABEL", "lrhe-linux-x64-shared"),
        ("FLEET_SITE", "unknown-pc"),
        ("DISPATCH_ID", f"lrhe-sf-20260823T010203Z-{_NONCE}"),
        ("RUNNER_NAME", "gateway-ci-sf-1"),
    ],
)
def test_fleet_validation_rejects_invalid_member(
    tmp_path: Path,
    field: str,
    bad_value: str,
) -> None:
    result = _run_validation(tmp_path / "absent.sock", updates={field: bad_value})
    assert result.returncode == 2


def test_fleet_validation_rejects_real_docker_socket() -> None:
    with tempfile.TemporaryDirectory(prefix="lrhe-fleet-", dir="/tmp") as temporary_directory:
        socket_path = Path(temporary_directory) / "docker.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as docker_socket:
            docker_socket.bind(str(socket_path))
            result = _run_validation(socket_path)
    assert result.returncode == 2
