"""M3 direct-fcntl write backend for _shared_state.

Each function writes to one target file under fcntl.LOCK_EX (where applicable),
returns a StepResult dict matching the schema in
docs/superpowers/specs/2026-05-16-v9-m3-helper-scripts-design.md §5.3.

At M1, scripts/_shared_state.py swaps its `_backend` import to queue_delegate;
this module remains as the M3 reference implementation.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any

from megalodon_ui.mission_config.default_v9_0_shape import synthesize as _synthesize
from megalodon_ui.mission_config.regex_builder import (
    build_status_row_re as _build_status_row_re,
)
from scripts._backends._history_format import format_history_line

LOCK_TIMEOUT_SECONDS = 5.0
LOCK_RETRY_INTERVAL = 0.05


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_dir(dir_path: Path) -> str:
    """Deterministic content hash of a directory's immediate contents."""
    parts = []
    if not dir_path.is_dir():
        return _sha256("")
    for child in sorted(dir_path.iterdir()):
        if child.is_file():
            parts.append(
                f"{child.name}\0{child.read_text(encoding='utf-8', errors='replace')}"
            )
        else:
            parts.append(f"{child.name}/")
    return _sha256("\n".join(parts))


def _step_result(
    *,
    step: str,
    ok: bool,
    target_file: str,
    pre_hash: str = "",
    post_hash: str = "",
    duration_ms: int = 0,
    idempotent: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "step": step,
        "ok": ok,
        "target_file": target_file,
        "pre_hash": pre_hash,
        "post_hash": post_hash,
        "duration_ms": duration_ms,
        "idempotent": idempotent,
        "error": error,
    }


def claim_dir_done(mission: Path, task_id: str, agent: str, utc: str) -> dict[str, Any]:
    start = time.monotonic()
    claim_dir = mission / "claims" / task_id
    target = str(claim_dir)
    if not claim_dir.is_dir():
        return _step_result(
            step="CLAIM_DIR_DONE",
            ok=False,
            target_file=target,
            error=f"claim dir missing: {claim_dir}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    done_marker = claim_dir / "done"
    owner_file = claim_dir / "owner.txt"
    pre_hash = _hash_dir(claim_dir)

    if (
        done_marker.exists()
        and owner_file.exists()
        and owner_file.read_text(encoding="utf-8").strip() == agent
    ):
        return _step_result(
            step="CLAIM_DIR_DONE",
            ok=True,
            target_file=target,
            pre_hash=pre_hash,
            post_hash=pre_hash,
            duration_ms=int((time.monotonic() - start) * 1000),
            idempotent=True,
        )

    done_marker.touch()
    owner_file.write_text(f"{agent}\n", encoding="utf-8")
    post_hash = _hash_dir(claim_dir)
    return _step_result(
        step="CLAIM_DIR_DONE",
        ok=True,
        target_file=target,
        pre_hash=pre_hash,
        post_hash=post_hash,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


TASK_LINE_RE = re.compile(
    r"^(?P<prefix>- )"
    r"\[(?P<state>[^\]]+)\]"
    r" "
    r"\[LANE-(?P<lane_short>[A-F])\] "
    r"`(?P<task_id>[^`]+)`"
    r"(?P<rest>.*)$"
)


class LockTimeoutError(RuntimeError):
    pass


def _acquire_lock(fd: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockTimeoutError(f"could not acquire lock within {timeout}s")
            time.sleep(LOCK_RETRY_INTERVAL)


def _atomic_replace(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _read_under_lock(path: Path, timeout: float) -> tuple[str, int]:
    """Open path for r+, acquire LOCK_EX, return (text, fd). Caller closes fd."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _acquire_lock(fd, timeout)
        with os.fdopen(fd, "r+", encoding="utf-8", closefd=False) as f:
            text = f.read()
        return text, fd
    except Exception:
        os.close(fd)
        raise


def tasks_bracket(mission: Path, task_id: str, agent: str, utc: str) -> dict[str, Any]:
    start = time.monotonic()
    path = mission / "TASKS.md"
    target = str(path)
    new_state = f"done: {agent} @ {utc}"
    try:
        text, fd = _read_under_lock(path, LOCK_TIMEOUT_SECONDS)
    except LockTimeoutError as e:
        return _step_result(
            step="TASKS_BRACKET",
            ok=False,
            target_file=target,
            error=str(e),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    try:
        pre_hash = _sha256(text)
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            m = TASK_LINE_RE.match(line)
            if m and m["task_id"] == task_id:
                if m["state"].startswith("done:"):
                    return _step_result(
                        step="TASKS_BRACKET",
                        ok=True,
                        target_file=target,
                        pre_hash=pre_hash,
                        post_hash=pre_hash,
                        duration_ms=int((time.monotonic() - start) * 1000),
                        idempotent=True,
                    )
                lines[i] = (
                    f"{m['prefix']}[{new_state}] [LANE-{m['lane_short']}] "
                    f"`{task_id}`{m['rest']}"
                )
                if not lines[i].endswith("\n"):
                    lines[i] += "\n"
                new_text = "".join(lines)
                _atomic_replace(path, new_text)
                return _step_result(
                    step="TASKS_BRACKET",
                    ok=True,
                    target_file=target,
                    pre_hash=pre_hash,
                    post_hash=_sha256(new_text),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
        return _step_result(
            step="TASKS_BRACKET",
            ok=False,
            target_file=target,
            error=f"task {task_id} not found in TASKS.md",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


_default_config = _synthesize(Path.cwd())
STATUS_ROW_RE = _build_status_row_re(_default_config)


def _history_row_recent(text: str, agent: str, task_id: str, utc: str) -> bool:
    """Return True if last ~50 lines contain a row with same agent+task_id
    within ±60 seconds of utc."""
    from datetime import datetime, timezone

    try:
        target = datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    lines = text.splitlines()[-50:]
    for ln in lines:
        if agent not in ln or task_id not in ln:
            continue
        # Try to parse leading UTC stamp
        prefix = ln.split(" | ", 1)[0]
        try:
            row_utc = datetime.strptime(prefix, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if abs((row_utc - target).total_seconds()) <= 60:
            return True
    return False


def history_append(
    mission: Path,
    *,
    agent: str,
    lane_short: str,
    task_id: str,
    finding_path: str,
    severity: str,
    notes: str,
    utc: str,
) -> dict[str, Any]:
    start = time.monotonic()
    path = mission / "HISTORY.md"
    target = str(path)
    line = (
        format_history_line(
            utc=utc,
            lane=lane_short,
            agent=agent,
            task_id=task_id,
            finding_path=finding_path,
            severity=severity,
            notes=notes,
        )
        + "\n"
    )
    try:
        text, fd = _read_under_lock(path, LOCK_TIMEOUT_SECONDS)
    except LockTimeoutError as e:
        return _step_result(
            step="HISTORY_APPEND",
            ok=False,
            target_file=target,
            error=str(e),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    try:
        pre_hash = _sha256(text)
        if _history_row_recent(text, agent, task_id, utc):
            return _step_result(
                step="HISTORY_APPEND",
                ok=True,
                target_file=target,
                pre_hash=pre_hash,
                post_hash=pre_hash,
                duration_ms=int((time.monotonic() - start) * 1000),
                idempotent=True,
            )
        if text and not text.endswith("\n"):
            text += "\n"
        new_text = text + line
        _atomic_replace(path, new_text)
        return _step_result(
            step="HISTORY_APPEND",
            ok=True,
            target_file=target,
            pre_hash=pre_hash,
            post_hash=_sha256(new_text),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def status_update(
    mission: Path,
    *,
    lane: str,
    agent: str,
    task_id: str,
    summary: str,
    utc: str,
) -> dict[str, Any]:
    start = time.monotonic()
    path = mission / "STATUS.md"
    target = str(path)
    try:
        text, fd = _read_under_lock(path, LOCK_TIMEOUT_SECONDS)
    except LockTimeoutError as e:
        return _step_result(
            step="STATUS_UPDATE",
            ok=False,
            target_file=target,
            error=str(e),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    try:
        pre_hash = _sha256(text)
        lines = text.splitlines(keepends=True)
        new_notes = f"{task_id} done — {summary}"
        for i, line in enumerate(lines):
            m = STATUS_ROW_RE.match(line.rstrip("\n"))
            if not m or m["lane"].strip() != lane:
                continue
            if m["agent"].strip() != agent:
                return _step_result(
                    step="STATUS_UPDATE",
                    ok=False,
                    target_file=target,
                    pre_hash=pre_hash,
                    post_hash=pre_hash,
                    error=(
                        f"STATUS row owner mismatch: lane={lane} "
                        f"expected agent={agent} found={m['agent'].strip()}"
                    ),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if m["state"].strip() == "idle" and f"{task_id} done" in m["notes"]:
                return _step_result(
                    step="STATUS_UPDATE",
                    ok=True,
                    target_file=target,
                    pre_hash=pre_hash,
                    post_hash=pre_hash,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    idempotent=True,
                )
            lines[i] = f"| {lane:9} | {agent} | {'idle':6} | {utc} | {new_notes} |\n"
            new_text = "".join(lines)
            _atomic_replace(path, new_text)
            return _step_result(
                step="STATUS_UPDATE",
                ok=True,
                target_file=target,
                pre_hash=pre_hash,
                post_hash=_sha256(new_text),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return _step_result(
            step="STATUS_UPDATE",
            ok=False,
            target_file=target,
            error=f"no STATUS row for lane={lane}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
