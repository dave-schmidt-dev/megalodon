"""
Atomic-write helpers for orchestrator actions.

Implements:
  - Per-file `asyncio.Lock` with alphabetical-absolute-path acquisition order
    (Δ5 / m6 — deadlock avoidance for multi-file ops like reclaim).
  - Content-hash compare-and-swap (CAS) for STATUS.md/TASKS.md/README.md
    writes (C1 — replaces flock; advisory locks can't protect against worker
    edits, but CAS detects them and retries).
  - O_APPEND atomic appends for .mission-events and HISTORY.md.
  - mkdir-as-lock for phase-flip race coordination.

All write helpers are async and return structured outcomes the FastAPI
endpoints translate into HTTP responses.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional


# ---------------------------------------------------------------------------
# Per-file lock registry
# ---------------------------------------------------------------------------

_file_locks: dict[Path, asyncio.Lock] = {}
_registry_lock = asyncio.Lock()


async def _get_lock(path: Path) -> asyncio.Lock:
    """Return the lock for path, creating it if absent. Thread-safe."""
    async with _registry_lock:
        if path not in _file_locks:
            _file_locks[path] = asyncio.Lock()
        return _file_locks[path]


@asynccontextmanager
async def file_locks(*paths: Path) -> AsyncIterator[None]:
    """Acquire multiple locks in alphabetical absolute-path order.

    Single-file usage: ``async with file_locks(path):``.
    Multi-file usage: ``async with file_locks(STATUS, TASKS):`` — server
    guarantees acquisition order regardless of arg order.
    """
    ordered = sorted({p.resolve() for p in paths})
    acquired: list[asyncio.Lock] = []
    try:
        for p in ordered:
            lock = await _get_lock(p)
            await lock.acquire()
            acquired.append(lock)
        yield
    finally:
        for lock in reversed(acquired):
            try:
                lock.release()
            except RuntimeError:
                pass


# ---------------------------------------------------------------------------
# Atomic write primitives
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """ISO-8601 UTC with trailing Z, minute precision (matches mission convention)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def utc_now_iso_seconds() -> str:
    """ISO-8601 UTC with trailing Z, second precision (for .mission-events)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_text(path: Path, content: str) -> None:
    """Write via temp-file + os.replace (POSIX-atomic rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def atomic_append_line(path: Path, line: str) -> None:
    """O_APPEND atomic append. Caller ensures line < PIPE_BUF (4096)."""
    if not line.endswith("\n"):
        line = line + "\n"
    line_bytes = line.encode("utf-8")
    if len(line_bytes) >= 4096:
        # Fall back to lock + temp-rename for safety
        existing = path.read_bytes() if path.exists() else b""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(existing + line_bytes)
        os.replace(tmp, path)
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def content_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass
class CasOutcome:
    ok: bool
    code: Optional[str] = None
    error: Optional[str] = None
    recoverable: bool = True
    attempts: int = 0
    final_hash: Optional[str] = None
    extra: dict[str, Any] = None  # type: ignore[assignment]


async def cas_modify(
    path: Path,
    mutator: Callable[[str], str],
    retries: int = 3,
    backoff_seconds: float = 0.1,
) -> CasOutcome:
    """Read-modify-write with content-hash CAS.

    Steps per attempt:
        1. Read original text.
        2. Compute new = mutator(original).
        3. Hold lock; re-read; if hash unchanged, atomic-write new.
        4. Read back to verify our write landed; if hash matches new, success.
        5. If concurrent write detected (hash drift), retry with backoff.

    Returns CasOutcome with attempts count and final hash. STALE_READ is the
    recoverable error code; FE retries via its own 3×/100ms backoff.
    """
    if not path.exists():
        return CasOutcome(
            ok=False,
            code="FILE_NOT_FOUND",
            error=f"{path} does not exist",
            recoverable=False,
            attempts=0,
        )
    lock = await _get_lock(path.resolve())
    for attempt in range(1, retries + 1):
        original = path.read_text(encoding="utf-8")
        original_hash_pre = content_hash(original)
        try:
            new = mutator(original)
        except Exception as exc:
            return CasOutcome(
                ok=False,
                code="MUTATOR_ERROR",
                error=str(exc),
                recoverable=False,
                attempts=attempt,
            )
        async with lock:
            # Re-read under lock; bail if hash drifted
            current = path.read_text(encoding="utf-8")
            if content_hash(current) != original_hash_pre:
                # External writer raced us between mutator call and lock
                await asyncio.sleep(backoff_seconds * attempt)
                continue
            atomic_write_text(path, new)
            # Verify our write landed (defense vs reordering on shared fs)
            verify = path.read_text(encoding="utf-8")
            new_hash = content_hash(new)
            if content_hash(verify) == new_hash:
                return CasOutcome(
                    ok=True, attempts=attempt, final_hash=new_hash, extra={}
                )
        await asyncio.sleep(backoff_seconds * attempt)
    return CasOutcome(
        ok=False,
        code="STALE_READ",
        error=f"concurrent write detected after {retries} attempts",
        recoverable=True,
        attempts=retries,
    )


# ---------------------------------------------------------------------------
# STATUS.md mutators
# ---------------------------------------------------------------------------


_STATUS_ROW_RE = lambda lane: re.compile(
    rf"^(\|\s*{lane}\s*\|)([^\n]+)(\|)\s*$", re.MULTILINE
)


def mutator_status_signal(target_lane: str, signal_token: str):
    """Return a mutator that appends a SIGNAL token to target_lane's Notes column."""

    def _mut(text: str) -> str:
        # Find the row for target_lane (case-insensitive prefix match)
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) < 5:
                continue
            if cells[0].upper() != target_lane.upper():
                continue
            # Append signal_token to Notes (last cell)
            new_notes = cells[4]
            if new_notes and not new_notes.endswith(" "):
                new_notes += " "
            new_notes += signal_token
            cells[4] = new_notes
            # Reconstruct line with original padding intent
            lines[i] = "| " + " | ".join(cells) + " |\n"
            return "".join(lines)
        raise ValueError(f"lane {target_lane!r} not found in STATUS.md")

    return _mut


def mutator_status_reclaim(target_lane: str, prev_agent: Optional[str], utc: str):
    """Return a mutator that resets target_lane's row to STALE-RECLAIMED."""

    def _mut(text: str) -> str:
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) < 5:
                continue
            if cells[0].upper() != target_lane.upper():
                continue
            note = f"reclaimed by orchestrator-ui @ {utc}"
            if prev_agent:
                note += f"; previous agent: {prev_agent}"
            cells[1] = "unclaimed"
            cells[2] = "STALE-RECLAIMED"
            cells[3] = utc
            cells[4] = note
            lines[i] = "| " + " | ".join(cells) + " |\n"
            return "".join(lines)
        raise ValueError(f"lane {target_lane!r} not found in STATUS.md")

    return _mut


# ---------------------------------------------------------------------------
# TASKS.md mutators
# ---------------------------------------------------------------------------


def mutator_tasks_reset(task_id: str):
    """Return a mutator that flips a TASKS bracket from [claimed/done] back to [ ]."""

    def _mut(text: str) -> str:
        # Match `- [<bracket>] [LANE-X] \`<task_id>\` — <desc>`
        pattern = re.compile(
            rf"^(- \[)[^\]]+(\] \[[^\]]+\] `{re.escape(task_id)}` — .+)$",
            re.MULTILINE,
        )
        new, n = pattern.subn(r"\1 \2", text)
        if n == 0:
            raise ValueError(f"task {task_id!r} not found in TASKS.md")
        return new

    return _mut


def mutator_tasks_inject(section_header: str, task_text: str):
    """Return a mutator that appends task_text to the section under section_header."""

    def _mut(text: str) -> str:
        # Insert task_text just before the next "##" header after section_header,
        # or at end of file if section is last.
        lines = text.splitlines(keepends=True)
        in_section = False
        insert_idx = None
        for i, line in enumerate(lines):
            if line.strip().startswith("## ") and section_header in line:
                in_section = True
                continue
            if in_section and line.strip().startswith("## "):
                insert_idx = i
                break
        if not in_section:
            raise ValueError(f"section {section_header!r} not found in TASKS.md")
        if insert_idx is None:
            insert_idx = len(lines)
        # Trim trailing blank lines so injection is clean
        while insert_idx > 0 and lines[insert_idx - 1].strip() == "":
            insert_idx -= 1
        line_to_insert = task_text if task_text.endswith("\n") else task_text + "\n"
        new_lines = lines[:insert_idx] + [line_to_insert, "\n"] + lines[insert_idx:]
        return "".join(new_lines)

    return _mut


# ---------------------------------------------------------------------------
# README.md Mission status mutator
# ---------------------------------------------------------------------------


def mutator_mission_status(new_status: str):
    """Return a mutator that replaces the `**Current: ...**` line in README.md."""

    def _mut(text: str) -> str:
        pattern = re.compile(r"^\*\*Current:[^*]*\*\*\s*$", re.MULTILINE)
        replacement = f"**Current: {new_status} (mission active — see `.mission-events` for authoritative phase)**"
        new, n = pattern.subn(replacement, text)
        if n == 0:
            raise ValueError("Mission status line not found in README.md")
        return new

    return _mut


# ---------------------------------------------------------------------------
# Phase-flip lock + .mission-events append
# ---------------------------------------------------------------------------


def try_acquire_phase_flip_lock(
    project_root: Path, from_phase: str, to_phase: str
) -> bool:
    """Atomic mkdir of `.phase-flip-locks/<from>-to-<to>`. True iff we won."""
    locks_dir = project_root / ".phase-flip-locks"
    locks_dir.mkdir(exist_ok=True)
    target = locks_dir / f"{from_phase}-to-{to_phase}"
    try:
        target.mkdir()
        return True
    except FileExistsError:
        return False


def append_phase_event(
    project_root: Path, from_phase: str, to_phase: str, by: str, reason: str
) -> str:
    """Append a phase event line to .mission-events. Returns the appended line."""
    utc = utc_now_iso_seconds()
    line = f"{utc} {from_phase}->{to_phase} by {by} — {reason}\n"
    atomic_append_line(project_root / ".mission-events", line)
    return line


# ---------------------------------------------------------------------------
# HISTORY.md append
# ---------------------------------------------------------------------------


def append_history_line(project_root: Path, line: str) -> None:
    """Append a completion line to HISTORY.md."""
    atomic_append_line(project_root / "HISTORY.md", line)


# ---------------------------------------------------------------------------
# Canonical SIGNAL token formatting (Δ2)
# ---------------------------------------------------------------------------


def format_canonical_signal(
    kind: str, from_agent: str, to: str, claim: str, evidence_path: str
) -> str:
    """Format a SIGNAL into the canonical <SIG ...> </SIG> token."""
    utc = utc_now_iso()
    return (
        f'<SIG kind="{kind}" from="{from_agent}" to="{to}" '
        f'utc="{utc}" evidence="{evidence_path}"> {claim} </SIG>'
    )
