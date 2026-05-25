"""Governor PreToolUse hook entry-point (Task 1.2).

Thin runtime wrapper executed by Claude Code's ``PreToolUse`` hook on every
tool call.  Reads a PreToolUse JSON event from stdin, calls the pure policy
engine, writes a permissionDecision JSON to stdout, and appends an audit line.

Usage (command hook)::

    python -m megalodon_ui.governor.hook

Confirmed stdin/stdout schema (Claude Code docs, 2026-05-25):

    stdin  → {"session_id": "...", "transcript_path": "...", "cwd": "...",
               "permission_mode": "...", "hook_event_name": "PreToolUse",
               "tool_name": "Bash", "tool_input": {...}}

    stdout ← {"hookSpecificOutput": {"hookEventName": "PreToolUse",
               "permissionDecision": "allow"|"deny",
               "permissionDecisionReason": "<str>"}}

Design constraints:
  * Import-light: only stdlib + ``megalodon_ui.governor.policy``.
  * Fail-closed: any exception in parse/decide → a valid deny JSON is emitted
    to stdout. The ONE thing this module cannot recover from is a failing
    stdout WRITE itself (a broken pipe / closed stream): there is then no
    channel to emit a decision, and lane safety relies on Claude Code treating
    a hook that produces no parseable output / non-zero exit as fail-closed.
    Audit-write failures are best-effort (never crash the hook, never block the
    decision).
  * Secret-safe: raw tool_input is NEVER written to the audit log; only the
    sha256 of its canonical JSON serialisation. The durable ``reason`` is
    per-category sanitized (see ``_audit_reason``) so no untrusted command /
    path / content reaches the log even diagnostically.
  * Crash-safe: bad stdin, missing fields, policy error all produce a deny.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from io import TextIOBase
from pathlib import Path
from typing import Any

try:
    # Package-style import: works under `python -m megalodon_ui.governor.hook`
    # and for tests that import via the package.
    from megalodon_ui.governor.policy import Decision, decide
except ModuleNotFoundError:  # pragma: no cover - exercised by the bare-interpreter shim
    # Standalone import: the shim (scripts/governor_hook.py) puts this module's
    # OWN directory (megalodon_ui/governor/) on sys.path[0] and imports `hook`
    # as a top-level module, so the heavy `megalodon_ui/__init__` (which drags
    # in yaml) is never executed. `policy` is then a sibling top-level module.
    from policy import Decision, decide  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HOOK_EVENT_NAME = "PreToolUse"

# Deny categories whose ``Decision.reason`` interpolates ARBITRARY input-derived
# data — a full command/segment, a raw/canonical path, a URL/host, content, or
# an exception string that may quote any of those. For these the durable audit
# reason is REDUCED to the bare category (the human-readable reason still goes
# to stdout for the model; only the persistent log is sanitized — plan §3.4 vs
# §8.4). This set was built by exhaustively auditing every ``_deny(...)`` site
# in ``policy.py`` (see that module). Categories NOT listed here embed at most a
# bounded command HEAD (e.g. ``sudo``/``python3``/``curl``) which is safe and
# diagnostically useful — but the runtime defensive net below still redacts any
# instance that, despite its category, embeds a real ``tool_input`` value.
#
# Audited input-bearing categories:
#   anti-tamper            -> "...governor file {path}"
#   write-secret           -> "write to secret path: {path}"
#   write-out-of-scope     -> "write outside scope: {path} -> {canonical}"
#   secret-read            -> "secret path: {raw}" / "secret basename: {cand}"
#   out-of-scope           -> "path outside scope: {raw} -> {canonical}"
#   bash-substitution      -> "command/process substitution: {command!r}"
#   bash-parse-error       -> "unparseable segment ({exc}): {segment!r}"
#   bash-root-destructive  -> "root-destructive command: {segment!r}"
#   bash-installer         -> "installer/package-manager: {segment!r}"
#   bash-flag-exec         -> "{tool} dangerous flag {a}" / "...: {segment!r}"
#   network-host           -> "host not on allowlist: {host or url!r}"
#   governor-error         -> "governor-error: {exc}" (exc may quote input)
_INPUT_BEARING_CATEGORIES = frozenset(
    {
        "anti-tamper",
        "write-secret",
        "write-out-of-scope",
        "secret-read",
        "out-of-scope",
        "bash-substitution",
        "bash-parse-error",
        "bash-root-destructive",
        "bash-installer",
        "bash-flag-exec",
        "network-host",
        "governor-error",
    }
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _input_sha256(tool_input: Any) -> str:
    """Sha256 of the canonical JSON serialization of ``tool_input``.

    Uses ``sort_keys=True`` and compact separators for a stable, reproducible
    digest.  Never stores raw content.
    """
    canonical = json.dumps(tool_input, sort_keys=True, separators=(",", ":"))
    # The digest is brute-forceable for low-entropy inputs — acceptable: it is a
    # correlation/audit digest, not a confidentiality control.
    return hashlib.sha256(canonical.encode()).hexdigest()


# Max nesting depth walked by _iter_input_strings. A deeply nested but legit
# tool_input must still get AUDITED (not RecursionError out); 6 covers every
# real tool schema with margin. Beyond it we stop descending (the leaf strings
# would only be diagnostic detail, and the sha256 still covers the whole input).
# NB: the defensive net's future-proofing only covers tool_input values within
# this depth. The depth-INDEPENDENT primary defense is the
# _INPUT_BEARING_CATEGORIES whitelist; the net is only a backstop for values
# reachable within _MAX_INPUT_DEPTH.
_MAX_INPUT_DEPTH = 6


def _iter_input_strings(tool_input: Any) -> list[str]:
    """All non-trivial string values nested anywhere in ``tool_input``.

    Used by the defensive net to confirm a kept reason does not echo any input
    value. Walks dicts/lists recursively (depth-capped at ``_MAX_INPUT_DEPTH``
    so a pathological deep input cannot ``RecursionError``); ignores very short
    strings (< 4 chars) to avoid spurious substring collisions on tiny tokens.
    """
    found: list[str] = []

    def _walk(node: Any, depth: int) -> None:
        if depth > _MAX_INPUT_DEPTH:
            return
        if isinstance(node, str):
            if len(node) >= 4:
                found.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v, depth + 1)
        elif isinstance(node, (list, tuple)):
            for v in node:
                _walk(v, depth + 1)

    _walk(tool_input, 0)
    return found


def _value_fragments(value: str) -> list[str]:
    """Forms a value can take inside a reason string, for leak detection.

    A reason may embed an input value verbatim (``{x}``), repr-escaped
    (``{x!r}`` → e.g. ``'tok\\nL2'`` with a literal backslash-n), or
    JSON-escaped (``json.dumps`` → ``"tok\\nL2"``). We strip the outer quote
    each transform adds so a substring test against the reason catches the
    BODY regardless of which quoting the reason used. This is what makes the
    net future-proof against a new ``{value!r}`` reason whose escaping would
    otherwise dodge a raw-value comparison.
    """
    frags = [value]
    r = repr(value)
    if len(r) >= 2 and r[0] in "'\"" and r[-1] == r[0]:
        frags.append(r[1:-1])
    else:
        frags.append(r)
    j = json.dumps(value)
    if len(j) >= 2 and j[0] == '"' and j[-1] == '"':
        frags.append(j[1:-1])
    else:
        frags.append(j)
    return [f for f in frags if f]


def _audit_reason(decision: Decision, tool_input: Any) -> str:
    """The durable audit reason: full detail when safe, else the bare category.

    Two layers:
      1. Category whitelist — any category known to interpolate arbitrary
         input-derived data (path/command/url/content/exc) is reduced to its
         bare category string (``_INPUT_BEARING_CATEGORIES``).
      2. Defensive net — even for a category we keep (it normally embeds only a
         bounded command head), if the reason text actually contains a
         ``tool_input`` value in ANY of its embeddable forms (raw, repr-escaped,
         or JSON-escaped — see :func:`_value_fragments`) or a home-path (``~``)
         fragment, fall back to the bare category. This future-proofs the hard
         "never store raw input" rule against a later policy change adding input
         to a currently-safe category: such a change fails SAFE (loses detail)
         instead of leaking a secret.
    """
    if decision.category in _INPUT_BEARING_CATEGORIES:
        return decision.category
    reason = decision.reason
    if "~" in reason:
        return decision.category
    for value in _iter_input_strings(tool_input):
        for fragment in _value_fragments(value):
            if fragment in reason:
                return decision.category
    return reason


def _write_audit(
    fleet_dir: Path,
    *,
    lane: str,
    tool_name: str,
    decision: Decision,
    tool_input: Any,
) -> None:
    """Append one JSON audit line to the daily governor log.

    File: ``<fleet_dir>/governor-log-<UTC YYYY-MM-DD>.jsonl``
    Keys: ts, lane, tool, permission, category, reason, input_sha256.

    Args:
        fleet_dir: The ``.fleet/`` directory under the project_dir.
        lane: Opaque lane identifier (basename of cwd or marker-file value).
        tool_name: Name of the tool being invoked.
        decision: The :class:`~megalodon_ui.governor.policy.Decision` verdict.
        tool_input: Raw tool_input dict (hashed, never stored literally).
    """
    fleet_dir.mkdir(parents=True, exist_ok=True)
    ts_now = datetime.now(timezone.utc)
    date_str = ts_now.strftime("%Y-%m-%d")
    log_path = fleet_dir / f"governor-log-{date_str}.jsonl"
    # ``reason`` in the audit is per-category-sanitized (see _audit_reason):
    # diagnostic detail is kept for categories that embed at most a command
    # head, and reduced to the bare category for any category (or any reason
    # that, per the defensive net, actually echoes a tool_input value) that
    # could leak raw input. The FULL reason still goes to stdout for the model.
    entry = {
        "ts": ts_now.isoformat(),
        "lane": lane,
        "tool": tool_name,
        "permission": decision.permission,
        "category": decision.category,
        "reason": _audit_reason(decision, tool_input),
        "input_sha256": _input_sha256(tool_input),
    }
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _deny_response(reason: str) -> dict[str, Any]:
    """Build the stdout deny payload for a given reason string."""
    return {
        "hookSpecificOutput": {
            "hookEventName": _HOOK_EVENT_NAME,
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _decision_response(decision: Decision) -> dict[str, Any]:
    """Convert a :class:`Decision` to the Claude Code hook output payload."""
    return {
        "hookSpecificOutput": {
            "hookEventName": _HOOK_EVENT_NAME,
            "permissionDecision": decision.permission,
            "permissionDecisionReason": decision.reason,
        }
    }


# ---------------------------------------------------------------------------
# Core logic (testable, accepts explicit I/O + env)
# ---------------------------------------------------------------------------


def run(
    *,
    stdin: TextIOBase | None = None,
    stdout: TextIOBase | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Read a PreToolUse event, decide, emit decision, append audit.

    Args:
        stdin: Readable text stream (defaults to ``sys.stdin``).
        stdout: Writable text stream (defaults to ``sys.stdout``).
        env: Environment dict (defaults to ``os.environ``).  Must be a plain
            ``dict[str, str]``; tests pass a minimal override.
    """
    _stdin = stdin if stdin is not None else sys.stdin
    _stdout = stdout if stdout is not None else sys.stdout
    _env = env if env is not None else dict(os.environ)

    def _emit(payload: dict[str, Any]) -> None:
        _stdout.write(json.dumps(payload))

    # ------------------------------------------------------------------
    # 1. Parse stdin — fail closed on bad JSON or missing required fields
    # ------------------------------------------------------------------
    try:
        raw = _stdin.read()
        event = json.loads(raw)
        tool_name: str = event["tool_name"]  # required — KeyError if absent
        tool_input: Any = event.get("tool_input", {})
        cwd: str = event.get("cwd", "")
    except Exception as exc:  # noqa: BLE001
        _emit(_deny_response(f"governor-error: bad hook input — {exc}"))
        return

    # ------------------------------------------------------------------
    # 2. Derive project_dir and lane (no megalodon env var dependency)
    # ------------------------------------------------------------------
    # project_dir: prefer $CLAUDE_PROJECT_DIR; fallback to event cwd.
    project_dir_str = _env.get("CLAUDE_PROJECT_DIR") or cwd or "."
    project_dir = Path(project_dir_str)

    # lane: basename of the event cwd (simple, documented; refined in later wiring).
    lane = Path(cwd).name if cwd else "unknown"

    # ------------------------------------------------------------------
    # 3. Call the policy engine (already fail-closed internally)
    # ------------------------------------------------------------------
    try:
        decision = decide(
            tool_name,
            tool_input if isinstance(tool_input, dict) else {},
            project_dir=project_dir,
            lane=lane,
        )
    except Exception as exc:  # noqa: BLE001 — belt-and-suspenders (decide never raises)
        decision = Decision(
            permission="deny",
            reason=f"governor-error: {exc}",
            category="governor-error",
        )

    # ------------------------------------------------------------------
    # 4. Emit decision to stdout
    # ------------------------------------------------------------------
    _emit(_decision_response(decision))

    # ------------------------------------------------------------------
    # 5. Append audit line (best-effort — never crash the hook)
    # ------------------------------------------------------------------
    try:
        fleet_dir = project_dir / ".fleet"
        _write_audit(
            fleet_dir,
            lane=lane,
            tool_name=tool_name,
            decision=decision,
            tool_input=tool_input,
        )
    except Exception:  # noqa: BLE001
        # Audit-write failure must never block or crash the hook.
        pass


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry-point for ``python -m megalodon_ui.governor.hook``."""
    run()


if __name__ == "__main__":
    main()
