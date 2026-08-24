"""Acceptance tests for the 8 Round-3c Work Packages (k=20 expansion slice).

Runs the OFFICIAL verifier against the REFERENCE solution inside each task's
official environment image, proving the acceptance criterion (reward == 1) is
achievable — the executable form of the GIVEN-WHEN-THEN cases on each Work
Package element. (Agent resolve is measured separately via `harbor run`.)

Run:  python -m pytest tests/test_round3c_tasks.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET = REPO_ROOT / "terminal-bench-2-1"

ROUND3C_TASKS = [
    ("git-multibranch", "alexgshaw/git-multibranch:20251031"),
    ("nginx-request-logging", "alexgshaw/nginx-request-logging:20251031"),
    ("pypi-server", "alexgshaw/pypi-server:20251031"),
    ("build-cython-ext", "alexgshaw/build-cython-ext:20251031"),
    ("dna-insert", "alexgshaw/dna-insert:20251031"),
    ("chess-best-move", "alexgshaw/chess-best-move:20251031"),
    ("vulnerable-secret", "alexgshaw/vulnerable-secret:20251031"),
    ("large-scale-text-editing", "alexgshaw/large-scale-text-editing:20251031"),
]

# Verifiers that import third-party packages not preinstalled in the task image.
VERIFIER_DEPS = {
    "nginx-request-logging": ["requests"],
    "build-cython-ext": ["numpy", "packaging"],
}

TIMEOUT = 900


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
@pytest.mark.parametrize("task_dir,image", ROUND3C_TASKS)
def test_round3c_task(task_dir: str, image: str):
    """Official verifier passes against the reference solution (reward=1)."""
    task_root = DATASET / task_dir
    solution = task_root / "solution" / "solve.sh"
    verifier = task_root / "tests" / "test_outputs.py"
    assert solution.exists(), f"Missing solution: {solution}"
    assert verifier.exists(), f"Missing verifier: {verifier}"

    container = f"archgraph-eval-acceptance-r3c-{task_dir}"
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    subprocess.run(
        ["docker", "run", "-d", "--name", container, image, "sleep", "infinity"],
        check=True, capture_output=True, timeout=TIMEOUT,
    )
    try:
        # Apply the reference solution (may install deps; internet allowed).
        _exec(container, solution.read_text(encoding="utf-8"), check=False)

        # Ensure a Python interpreter + pytest exist.
        _exec(container,
              "command -v python3 >/dev/null || (export DEBIAN_FRONTEND=noninteractive && apt-get update -y && apt-get install -y python3 python3-pip)",
              check=False)
        _exec(container,
              "python -m pip install --break-system-packages -q pytest==8.4.1 2>/dev/null || "
              "python3 -m pip install --break-system-packages -q pytest==8.4.1 2>/dev/null || "
              "(export DEBIAN_FRONTEND=noninteractive && apt-get install -y python3-pip && python3 -m pip install --break-system-packages -q pytest==8.4.1)",
              check=False)

        # Install verifier deps not preinstalled in the task image.
        deps = VERIFIER_DEPS.get(task_dir)
        if deps:
            _exec(container,
                  "python -m pip install --break-system-packages -q " + " ".join(deps) +
                  " 2>/dev/null || python3 -m pip install --break-system-packages -q " + " ".join(deps),
                  check=False)

        # Copy the WHOLE tests/ directory so verifiers can read sibling files.
        subprocess.run(["docker", "exec", container, "mkdir", "-p", "/tmp/tests"],
                       capture_output=True, check=False)
        cp = subprocess.run(["docker", "cp", f"{task_root / 'tests'}/.", f"{container}:/tmp/tests/"],
                            capture_output=True, text=True, timeout=TIMEOUT)
        assert cp.returncode == 0, f"docker cp tests dir failed: {cp.stderr}"
        result = _exec(container, "cd /app && (python -m pytest /tmp/tests/test_outputs.py -q 2>/dev/null || python3 -m pytest /tmp/tests/test_outputs.py -q)", check=False)

        evidence_dir = REPO_ROOT / ".argo" / "temp" / "acceptance" / task_dir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "verdict.json").write_text(
            json.dumps({"reward": 1 if result.returncode == 0 else 0, "returncode": result.returncode}, indent=2),
            encoding="utf-8",
        )
        (evidence_dir / "pytest_output.txt").write_text(
            f"returncode={result.returncode}\n{result.stdout}\n{result.stderr}", encoding="utf-8",
        )

        assert result.returncode == 0, (
            f"[{task_dir}] official verifier failed (reward=0):\n{result.stdout}\n{result.stderr}"
        )
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
