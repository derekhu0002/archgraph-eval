"""Acceptance test for Work Package `wp-openssl-selfsigned-cert` (easy task).

GIVEN  a Docker sandbox `python:3.13-slim-bookworm` with `openssl` installed
       (mirrors the Terminal-Bench task environment), WORKDIR=/app
WHEN   the distilled ARCHGRAPH skill solution (`scripts/solutions/openssl_selfsigned_cert/solve.sh`)
       is applied inside the sandbox
THEN   the official Terminal-Bench verifier `tasks/openssl-selfsigned-cert/tests/test_outputs.py`
       passes every assertion, i.e. reward == 1.

This is the executable form of the GIVEN-WHEN-THEN acceptance criterion stored on
the Work Package element (`openssl-ssl-files-pass-official-verifier`).

Run:  python -m pytest tests/test_openssl_selfsigned_cert.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOLUTION = REPO_ROOT / "scripts" / "solutions" / "openssl_selfsigned_cert" / "solve.sh"
VERIFIER = REPO_ROOT / "terminal-bench-2-1" / "openssl-selfsigned-cert" / "tests" / "test_outputs.py"
IMAGE = "alexgshaw/openssl-selfsigned-cert:20251031"
CONTAINER = "archgraph-eval-acceptance-openssl"
TIMEOUT = 180


def _docker_available() -> bool:
    try:
        subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=30,
        )
        return True
    except Exception:
        return False


def _exec(container: str, command: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", container, "bash", "-lc", command],
        capture_output=True, text=True, timeout=TIMEOUT, check=check,
    )


@pytest.mark.skipif(not _docker_available(), reason="Docker is required")
def test_openssl_ssl_files_pass_official_verifier():
    """End-to-end: apply the skill solution, then run the official verifier."""
    assert SOLUTION.exists(), f"Missing solution script: {SOLUTION}"
    assert VERIFIER.exists(), (
        f"Missing official verifier: {VERIFIER} — run `harbor dataset download \"terminal-bench/terminal-bench-2-1\"`"
    )

    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)

    # GIVEN: sandbox mirroring the task environment (openssl pre-installed).
    subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER, "--workdir", "/app", IMAGE, "sleep", "infinity"],
        check=True, capture_output=True, timeout=TIMEOUT,
    )
    try:
        # pytest is needed to run the official verifier (internet is available).
        _exec(CONTAINER, "pip install -q pytest==8.4.1")

        # WHEN: apply the distilled skill solution.
        solution_text = SOLUTION.read_text(encoding="utf-8")
        _exec(CONTAINER, solution_text)

        # THEN: run the official verifier inside the sandbox (uses absolute /app paths).
        verifier_text = VERIFIER.read_text(encoding="utf-8")
        _exec(
            CONTAINER,
            f"cat > /tmp/test_outputs.py << 'PYEOF'\n{verifier_text}\nPYEOF",
        )
        result = _exec(CONTAINER, "cd /app && python -m pytest /tmp/test_outputs.py -q", check=False)

        # Persist evidence for the report.
        evidence_dir = REPO_ROOT / ".argo" / "temp" / "acceptance" / "openssl-selfsigned-cert"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "pytest_output.txt").write_text(
            f"returncode={result.returncode}\n{result.stdout}\n{result.stderr}",
            encoding="utf-8",
        )
        (evidence_dir / "verdict.json").write_text(
            json.dumps(
                {"reward": 1 if result.returncode == 0 else 0, "returncode": result.returncode},
                indent=2,
            ),
            encoding="utf-8",
        )

        assert result.returncode == 0, (
            "Official verifier failed (reward=0). Output:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
