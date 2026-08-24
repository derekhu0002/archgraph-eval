"""Acceptance tests for the 8 Round-3 Work Packages (expanded slice).

Runs the OFFICIAL verifier against the REFERENCE solution inside each task's
official environment image, proving the acceptance criterion (reward == 1) is
achievable — the executable form of the GIVEN-WHEN-THEN cases on each Work
Package element. (Agent resolve is measured separately via `harbor run`.)

Run:  python -m pytest tests/test_round3_tasks.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET = REPO_ROOT / "terminal-bench-2-1"

ROUND3_TASKS = [
    ("sanitize-git-repo", "alexgshaw/sanitize-git-repo:20251031"),
    ("regex-log", "alexgshaw/regex-log:20251031"),
    ("log-summary-date-ranges", "alexgshaw/log-summary-date-ranges:20251031"),
    ("filter-js-from-html", "alexgshaw/filter-js-from-html:20251031"),
    ("overfull-hbox", "alexgshaw/overfull-hbox:20260403"),
    ("cobol-modernization", "alexgshaw/cobol-modernization:20251031"),
    ("db-wal-recovery", "alexgshaw/db-wal-recovery:20251031"),
    ("largest-eigenval", "alexgshaw/largest-eigenval:20251031"),
]

TIMEOUT = 600


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
@pytest.mark.parametrize("task_dir,image", ROUND3_TASKS)
def test_round3_task(task_dir: str, image: str):
    """Official verifier passes against the reference solution (reward=1)."""
    task_root = DATASET / task_dir
    solution = task_root / "solution" / "solve.sh"
    verifier = task_root / "tests" / "test_outputs.py"
    assert solution.exists(), f"Missing solution: {solution}"
    assert verifier.exists(), f"Missing verifier: {verifier}"

    container = f"archgraph-eval-acceptance-r3-{task_dir}"
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    # Use each image's default WORKDIR (task solutions rely on it).
    subprocess.run(
        ["docker", "run", "-d", "--name", container, image, "sleep", "infinity"],
        check=True, capture_output=True, timeout=TIMEOUT,
    )
    try:
        # Apply the reference solution.
        _exec(container, solution.read_text(encoding="utf-8"), check=False)

        # Ensure Python + pytest exist (Ubuntu vs python-slim images differ).
        # --break-system-packages handles Ubuntu 24.04's PEP 668 env.
        _exec(container,
              "command -v python3 >/dev/null || (export DEBIAN_FRONTEND=noninteractive && apt-get update -y && apt-get install -y python3 python3-pip)",
              check=False)
        _exec(container,
              "python -m pip install --break-system-packages -q pytest==8.4.1 2>/dev/null || "
              "python3 -m pip install --break-system-packages -q pytest==8.4.1 2>/dev/null || "
              "(export DEBIAN_FRONTEND=noninteractive && apt-get install -y python3-pip && python3 -m pip install --break-system-packages -q pytest==8.4.1)",
              check=False)
        verifier_text = verifier.read_text(encoding="utf-8")
        _exec(container, f"cat > /tmp/test_outputs.py << 'PYEOF'\n{verifier_text}\nPYEOF", check=False)
        result = _exec(container, "cd /app && (python -m pytest /tmp/test_outputs.py -q 2>/dev/null || python3 -m pytest /tmp/test_outputs.py -q)", check=False)

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
