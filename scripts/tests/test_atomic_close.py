"""CLI integration tests for scripts/atomic_close.py."""

import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "atomic_close.py"


def _run(mission_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SCRIPT.resolve().parents[1])}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--mission-dir", str(mission_dir), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_help_runs():
    env = {**os.environ, "PYTHONPATH": str(SCRIPT.resolve().parents[1])}
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert res.returncode == 0
    assert "atomic_close" in res.stdout


def test_happy_path_returns_ok_json(mission_dir, agent):
    (mission_dir / "findings").mkdir(exist_ok=True)
    (mission_dir / "findings" / "f.md").write_text("body", encoding="utf-8")
    res = _run(
        mission_dir,
        "--task",
        "TEST-1",
        "--lane",
        "AUDIT",
        "--agent",
        agent,
        "--finding",
        "findings/f.md",
        "--severity",
        "DELTA",
        "--notes",
        "happy path",
        "--summary",
        "happy",
    )
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout.strip())
    assert payload["ok"] is True
    assert len(payload["steps"]) == 4


def test_arg_validation_exits_2(mission_dir, agent):
    res = _run(
        mission_dir,
        "--task",
        "lowercase-bad",
        "--lane",
        "AUDIT",
        "--agent",
        agent,
        "--finding",
        "findings/f.md",
        "--severity",
        "DELTA",
        "--notes",
        "x",
        "--summary",
        "x",
    )
    assert res.returncode == 2


def test_precondition_failure_exits_3(mission_dir, agent):
    res = _run(
        mission_dir,
        "--task",
        "P5-RUN-DOES-NOT-EXIST",
        "--lane",
        "AUDIT",
        "--agent",
        agent,
        "--finding",
        "findings/f.md",
        "--severity",
        "DELTA",
        "--notes",
        "x",
        "--summary",
        "x",
    )
    # CLAIM_DIR_DONE will fail (claims/P5-RUN-DOES-NOT-EXIST/ missing) → partial close → exit 3.
    assert res.returncode == 3
    payload = json.loads(res.stdout.strip())
    assert payload["ok"] is False
    assert payload["failed_step"] == "CLAIM_DIR_DONE"


def test_dry_run_writes_nothing(mission_dir, agent):
    (mission_dir / "findings").mkdir(exist_ok=True)
    (mission_dir / "findings" / "f.md").write_text("body", encoding="utf-8")
    before = (mission_dir / "STATUS.md").read_text(encoding="utf-8")
    res = _run(
        mission_dir,
        "--task",
        "TEST-1",
        "--lane",
        "AUDIT",
        "--agent",
        agent,
        "--finding",
        "findings/f.md",
        "--severity",
        "DELTA",
        "--notes",
        "dryrun",
        "--summary",
        "dryrun",
        "--dry-run",
    )
    assert res.returncode == 0
    after = (mission_dir / "STATUS.md").read_text(encoding="utf-8")
    assert before == after


def test_build_request_id_is_unique_same_second_same_agent():
    """Robust request-id scheme: no collisions for same-second, same-agent ids.

    Regression guard for the previous token_hex(2) (16-bit) tail, which had a
    ~256-id birthday bound and could let a colliding request_id silently
    clobber a prior pending close. The monotonic per-process counter makes
    within-process collisions impossible regardless of timestamp resolution.
    """
    sys.path.insert(0, str(SCRIPT.resolve().parents[1]))
    from scripts.atomic_close import _build_request_id

    ids = {_build_request_id("agent-abcd") for _ in range(5000)}
    assert len(ids) == 5000
    # Shape: ends with the 16-hex (8-byte) entropy tail, has the seq segment.
    sample = next(iter(ids))
    assert "-rule10-CLOSE-" in sample
    tail = sample.rsplit("-", 1)[-1]
    assert len(tail) == 16 and all(c in "0123456789abcdef" for c in tail)
