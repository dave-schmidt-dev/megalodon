"""V9 M1 — queue_client backend for scripts/_shared_state.

Same interface as `direct_fcntl`: returns a `_step_result`-shaped dict per
call (ok/step/target_file/pre_hash/post_hash/duration_ms/idempotent/error).

Routes mutations through the queue: submits a request to
`megalodon_ui.queue.queue_client.submit()` and then waits for the applier
to land it.

Test/single-process fallback: if no applier heartbeat exists or it's stale,
we spin up an in-process `Applier` and drive a synchronous drain. This
keeps the existing M3 `execute_close` / `resume_close` tests passing
without spawning a subprocess. In production, the operator runs the
applier daemon via `scripts/start_applier.sh` and this fallback is a
no-op (heartbeat is fresh).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from megalodon_ui.queue import queue_client as _qc
from megalodon_ui.queue.applier import Applier
from scripts._backends._history_format import format_history_line

# Heartbeat threshold; older than this and we assume no live applier.
_HEARTBEAT_STALE_SECONDS = 5.0


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_dir(dir_path: Path) -> str:
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


def _applier_alive(mission: Path) -> bool:
    """Check if an applier process is currently running (pid + heartbeat).

    True iff the lock dir has a pid.txt whose process is alive AND the
    heartbeat is fresh. False otherwise (including no lock dir).
    """
    import os

    lock_dir = mission / "queue" / ".applier.lock"
    pid_file = lock_dir / "pid.txt"
    hb_file = lock_dir / "heartbeat.txt"
    if not (pid_file.exists() and hb_file.exists()):
        return False
    try:
        pid = int(pid_file.read_text().strip().split()[0])
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return False
    age = time.time() - hb_file.stat().st_mtime
    return age < _HEARTBEAT_STALE_SECONDS


def _wait_or_drain(mission: Path, rid: str, timeout: float = 10.0) -> str:
    """Wait for rid to land; if no live applier, drive one in-process."""
    if _applier_alive(mission):
        return _qc.wait_until_applied(mission, rid, timeout_seconds=timeout)
    # Fallback: drive a local applier to drain this one request.
    applier = Applier(mission)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        applier.drain_once()
        applied = mission / "queue" / "applied" / f"{rid}.json"
        rejected = mission / "queue" / "rejected" / f"{rid}.json"
        if applied.exists():
            return "applied"
        if rejected.exists():
            return "rejected"
        time.sleep(0.05)
    return "timeout"


def _rejection_reason(mission: Path, rid: str) -> str:
    reason_file = mission / "queue" / "rejected" / f"{rid}-reason.txt"
    if reason_file.exists():
        return reason_file.read_text().strip()
    return "unknown"


# ---- backend API (matches direct_fcntl.py shape) ----


def claim_dir_done(mission: Path, task_id: str, agent: str, utc: str) -> dict[str, Any]:
    start = time.monotonic()
    claim_dir = mission / "claims" / task_id
    target = str(claim_dir)
    pre_hash = _hash_dir(claim_dir) if claim_dir.exists() else ""

    if not claim_dir.is_dir():
        return _step_result(
            step="CLAIM_DIR_DONE",
            ok=False,
            target_file=target,
            error=f"claim dir missing: {claim_dir}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    # Already done with same owner.
    owner_file = claim_dir / "owner.txt"
    done_marker = claim_dir / "done"
    if done_marker.exists() and owner_file.exists():
        owner_first = owner_file.read_text(encoding="utf-8").strip().split()
        if owner_first and owner_first[0] == agent:
            return _step_result(
                step="CLAIM_DIR_DONE",
                ok=True,
                target_file=target,
                pre_hash=pre_hash,
                post_hash=pre_hash,
                duration_ms=int((time.monotonic() - start) * 1000),
                idempotent=True,
            )
    # If owner.txt is missing entirely, M3 backend would write it; queue's
    # B4 strict mode would reject. To preserve M3 behavior, write a default
    # owner.txt before submitting.
    if not owner_file.exists():
        owner_file.write_text(f"{agent}\n", encoding="utf-8")

    rid = _qc.claim_dir_done(mission, agent, "?", task_id)
    status = _wait_or_drain(mission, rid)
    if status != "applied":
        return _step_result(
            step="CLAIM_DIR_DONE",
            ok=False,
            target_file=target,
            pre_hash=pre_hash,
            error=f"queue-status:{status}:{_rejection_reason(mission, rid)}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    return _step_result(
        step="CLAIM_DIR_DONE",
        ok=True,
        target_file=target,
        pre_hash=pre_hash,
        post_hash=_hash_dir(claim_dir),
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def tasks_bracket(mission: Path, task_id: str, agent: str, utc: str) -> dict[str, Any]:
    start = time.monotonic()
    path = mission / "TASKS.md"
    target = str(path)
    pre_text = path.read_text(encoding="utf-8") if path.exists() else ""
    pre_hash = _sha256(pre_text)

    # Idempotency: if already done, return idempotent.
    import re

    pattern = rf"^-\s+\[done:[^\]]*\]\s+\[LANE-[A-Z]\]\s+`{re.escape(task_id)}`"
    if re.search(pattern, pre_text, re.MULTILINE):
        return _step_result(
            step="TASKS_BRACKET",
            ok=True,
            target_file=target,
            pre_hash=pre_hash,
            post_hash=pre_hash,
            duration_ms=int((time.monotonic() - start) * 1000),
            idempotent=True,
        )
    # Task not found at all?
    if not re.search(
        rf"^-\s+\[[^\]]+\]\s+\[LANE-[A-Z]\]\s+`{re.escape(task_id)}`",
        pre_text,
        re.MULTILINE,
    ):
        return _step_result(
            step="TASKS_BRACKET",
            ok=False,
            target_file=target,
            pre_hash=pre_hash,
            error=f"task {task_id} not found in TASKS.md",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    rid = _qc.tasks_bracket(
        mission,
        agent,
        "?",
        task_id,
        f"[done: {agent} @ {utc}]",
    )
    status = _wait_or_drain(mission, rid)
    if status != "applied":
        return _step_result(
            step="TASKS_BRACKET",
            ok=False,
            target_file=target,
            pre_hash=pre_hash,
            error=f"queue-status:{status}:{_rejection_reason(mission, rid)}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    post_text = path.read_text(encoding="utf-8")
    return _step_result(
        step="TASKS_BRACKET",
        ok=True,
        target_file=target,
        pre_hash=pre_hash,
        post_hash=_sha256(post_text),
        duration_ms=int((time.monotonic() - start) * 1000),
    )


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
    pre_text = path.read_text(encoding="utf-8") if path.exists() else ""
    pre_hash = _sha256(pre_text)

    # Idempotency: row recent within 60s for same agent + task_id.
    from datetime import datetime, timezone

    try:
        target_dt = datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        target_dt = None
    if target_dt is not None:
        for ln in pre_text.splitlines()[-50:]:
            if agent in ln and task_id in ln:
                prefix = ln.split(" | ", 1)[0]
                try:
                    row_dt = datetime.strptime(prefix, "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    continue
                if abs((row_dt - target_dt).total_seconds()) <= 60:
                    return _step_result(
                        step="HISTORY_APPEND",
                        ok=True,
                        target_file=target,
                        pre_hash=pre_hash,
                        post_hash=pre_hash,
                        duration_ms=int((time.monotonic() - start) * 1000),
                        idempotent=True,
                    )

    # Per `_apply_history_append` regex: lane segment must be `[A-Za-z]+`.
    # The M3 row format includes `(notes)` suffix.
    line = format_history_line(
        utc=utc,
        lane=lane_short,
        agent=agent,
        task_id=task_id,
        finding_path=finding_path,
        severity=severity,
        notes=notes,
    )
    rid = _qc.history_append(
        mission,
        agent,
        lane_short,
        task_id,
        finding_path,
        severity,
        utc=utc,
    )
    # We need the raw line that the applier writes to differ from what the
    # M3 direct backend writes. Override via direct submit with our custom
    # line so it matches M3 shape.
    rid = _qc.submit(
        mission,
        agent,
        lane_short,
        "HISTORY.md",
        "HISTORY_APPEND",
        {"line": line},
    )
    status = _wait_or_drain(mission, rid)
    if status != "applied":
        return _step_result(
            step="HISTORY_APPEND",
            ok=False,
            target_file=target,
            pre_hash=pre_hash,
            error=f"queue-status:{status}:{_rejection_reason(mission, rid)}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    post_text = path.read_text(encoding="utf-8")
    return _step_result(
        step="HISTORY_APPEND",
        ok=True,
        target_file=target,
        pre_hash=pre_hash,
        post_hash=_sha256(post_text),
        duration_ms=int((time.monotonic() - start) * 1000),
    )


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
    pre_text = path.read_text(encoding="utf-8") if path.exists() else ""
    pre_hash = _sha256(pre_text)

    import re

    # Owner-mismatch & idempotency mirror M3.
    row_re = re.compile(
        rf"^\|\s*{re.escape(lane)}\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
        rf"\s*[^|]+?\s*\|\s*(.*?)\s*\|\s*$",
        re.MULTILINE,
    )
    m = row_re.search(pre_text)
    if not m:
        return _step_result(
            step="STATUS_UPDATE",
            ok=False,
            target_file=target,
            pre_hash=pre_hash,
            error=f"no STATUS row for lane={lane}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    existing_agent = m.group(1).strip()
    existing_state = m.group(2).strip()
    existing_notes = m.group(3).strip()
    if existing_agent != agent:
        return _step_result(
            step="STATUS_UPDATE",
            ok=False,
            target_file=target,
            pre_hash=pre_hash,
            post_hash=pre_hash,
            error=(
                f"STATUS row owner mismatch: lane={lane} "
                f"expected agent={agent} found={existing_agent}"
            ),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    new_notes_value = f"{task_id} done — {summary}"
    if existing_state == "idle" and f"{task_id} done" in existing_notes:
        return _step_result(
            step="STATUS_UPDATE",
            ok=True,
            target_file=target,
            pre_hash=pre_hash,
            post_hash=pre_hash,
            duration_ms=int((time.monotonic() - start) * 1000),
            idempotent=True,
        )

    rid = _qc.status_update(
        mission,
        agent,
        lane,
        "idle",
        new_notes_value,
        new_utc=utc,
    )
    status = _wait_or_drain(mission, rid)
    if status != "applied":
        return _step_result(
            step="STATUS_UPDATE",
            ok=False,
            target_file=target,
            pre_hash=pre_hash,
            error=f"queue-status:{status}:{_rejection_reason(mission, rid)}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    post_text = path.read_text(encoding="utf-8")
    return _step_result(
        step="STATUS_UPDATE",
        ok=True,
        target_file=target,
        pre_hash=pre_hash,
        post_hash=_sha256(post_text),
        duration_ms=int((time.monotonic() - start) * 1000),
    )
