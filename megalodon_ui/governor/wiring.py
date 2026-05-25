"""Governor wiring helpers (Task 2.2).

Resolve and preflight the committed governor settings so every ``claude`` spawn
path can attach ``--settings <governor-settings.json>`` (the PreToolUse governor
hook + ``permissions.deny`` floor) and fail LOUD up front when the hook cannot
resolve — rather than degrading to a silent ``claude`` "command not found" that
downs the fleet lane-by-lane.

Import-light by design: only stdlib (``json``, ``os``, ``pathlib``). The mission
kill-switch is read off an already-constructed ``MissionConfig`` via duck-typed
attribute access, so this module imposes no import on the mission_config package.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

# Repo root: this file is megalodon_ui/governor/wiring.py → parents[2] is the
# repo root. Resolve from __file__ so we NEVER hardcode a machine-absolute path.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SETTINGS_REL = pathlib.Path(".claude") / "governor-settings.json"
# The run-dir-relative hook path. new_run.sh creates a `scripts/` symlink in the
# run dir → ../../scripts, so this resolves to the real executable shim.
_HOOK_REL = pathlib.Path("scripts") / "governor_hook.py"


class GovernorPreflightError(RuntimeError):
    """Raised when the governor is enabled but not wirable.

    The message names exactly what is missing and how to fix it, so a broken
    hook fails the whole spawn loudly instead of silently degrading.
    """


class GovernorCanaryError(RuntimeError):
    """Raised when the governor hook is reachable but is NOT actually denying.

    Distinct from :class:`GovernorPreflightError`: preflight proves the hook is
    *reachable + executable*; the canary self-test proves the hook actually
    *denies* what it must. If the sentinel probe slips through (allow / error /
    malformed output / non-zero exit / a deny for the wrong reason) the governor
    is silently non-enforcing — the most dangerous failure mode — so we convert
    it into a LOUD abort instead of letting lanes run ungoverned.
    """


# Subprocess wall-clock ceiling for the canary self-test. The shim is a tiny
# stdlib-only script; a few seconds is generous. A hang here would itself be a
# governor failure, so we cap it and treat a timeout as a canary failure.
_CANARY_TIMEOUT_S = 15


def governor_settings_path() -> pathlib.Path:
    """Absolute path to the committed ``.claude/governor-settings.json``.

    Resolved from this module's location, never hardcoded. Returns the path
    WITHOUT checking that it exists or is valid JSON — existence/validity is
    ``preflight_governor``'s responsibility (single-guard by design), so a
    caller must not assume the returned path points at a real file.
    """
    return _REPO_ROOT / _SETTINGS_REL


def governor_enabled(mission_config: Any) -> bool:
    """Read the mission-level governor kill-switch.

    Args:
        mission_config: a ``MissionConfig`` (or any object) carrying the
            ``governor_enabled`` flag.

    Returns:
        The flag value; defaults to ``True`` when the attribute is absent, so a
        legacy/back-compat config without the field keeps the governor ON.
    """
    return bool(getattr(mission_config, "governor_enabled", True))


def governor_kwargs(
    mission_config: Any,
    lane_cfg: Any,
    *,
    settings_path: pathlib.Path | None = None,
) -> dict[str, pathlib.Path]:
    """Single source of truth for the ``--settings`` gating decision (Task 2.2).

    Returns the ``{"governor_settings": <path>}`` kwarg to splat into a Claude
    adapter's ``build_argv`` / ``build_followup_argv`` when the governor should
    attach ``--settings`` for this lane, or an empty dict otherwise. The gate is
    applied here ONCE — both spawn sites and the server /followup site call this
    so a future edit cannot make one site silently drop ``--settings`` for a
    claude lane (the dangerous direction).

    The kwarg is attached only when BOTH hold:
      * the mission kill-switch ``governor_enabled`` is True, AND
      * the lane's harness CLI is ``"claude"`` (only ClaudeAdapter accepts the
        ``governor_settings`` kwarg / supports ``--settings``).

    Args:
        mission_config: the ``MissionConfig`` carrying the kill-switch flag.
        lane_cfg: the ``LaneConfig`` whose ``harness.cli`` selects the adapter.
        settings_path: when provided (spawn path — preflight already ran), the
            precomputed governor settings path is reused verbatim. When None
            (server /followup — no preflight, the fleet is already running), the
            path is re-derived via ``governor_settings_path()``.

    Returns:
        ``{"governor_settings": path}`` or ``{}``.
    """
    if not governor_enabled(mission_config):
        return {}
    if getattr(getattr(lane_cfg, "harness", None), "cli", None) != "claude":
        return {}
    path = settings_path if settings_path is not None else governor_settings_path()
    return {"governor_settings": path}


# ---------------------------------------------------------------------------
# Governed-marker provenance (Task 2.5 / PM-6 / CR-2 / CV-6)
#
# A reattached lane's process is the OLD one — born under whatever regime it
# started with — so the rebuilt argv (which Task 2.2 makes carry --settings)
# CANNOT be trusted to prove the live process is governed. The marker below is
# the SPAWN-IDENTITY signal: it is written ONLY when the fleet actually spawns a
# lane UNDER the governor, and it carries a fingerprint of the governor settings
# so a later settings change is detectable. Reattach reads the marker (not the
# argv) to decide ``governed``; absent/stale ⇒ ``ungoverned``. Fail TOWARD
# ungoverned — reporting governed-as-ungoverned merely prompts an unneeded
# respawn, whereas the reverse silently runs an ungoverned process.
#
# NOTE: ``ungoverned`` here is PROVENANCE of the live process (was it born under
# the governor?). It is a SEPARATE concept from the P3.2 deny-loop alarm /
# ``governor-blocked`` status (a *governed* process repeatedly hitting denies).
# Do not conflate them.
# ---------------------------------------------------------------------------

# Per-lane marker lives beside the existing ``.fleet/<short>.session.txt`` so it
# shares the run-state directory and atomic-write style (spawn.py:~707).
_MARKER_SUFFIX = ".governed"


def governed_marker_path(mission_dir: pathlib.Path, lane_short: str) -> pathlib.Path:
    """Absolute path to a lane's governed-marker file (``.fleet/<short>.governed``)."""
    return mission_dir / ".fleet" / f"{lane_short}{_MARKER_SUFFIX}"


def governor_fingerprint(
    settings_path: pathlib.Path | None = None,
) -> dict[str, str]:
    """Fingerprint the active governor settings for marker write/verify.

    The fingerprint binds the ABSOLUTE settings path plus a SHA-256 of the
    settings file content. A content hash (rather than mtime) survives a clean
    ``git checkout`` that preserves bytes but rewrites mtimes, while still
    flipping on any real edit — so a later governor-settings change is reliably
    detected as a stale marker.

    Args:
        settings_path: Override for tests; defaults to ``governor_settings_path()``.

    Returns:
        ``{"settings_path": str, "settings_sha256": str}``. The hash is the empty
        string when the settings file cannot be read (the marker would then never
        validate — fail toward ungoverned).
    """
    path = settings_path if settings_path is not None else governor_settings_path()
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        digest = ""
    return {"settings_path": str(path), "settings_sha256": digest}


def write_governed_marker(
    mission_dir: pathlib.Path,
    lane_short: str,
    *,
    settings_path: pathlib.Path | None = None,
) -> None:
    """Atomically write the lane's governed marker (write-temp-then-rename).

    Called ONLY when the fleet spawns/respawns a lane UNDER the governor. The
    atomic rename guarantees a crash mid-write cannot leave a half-written marker
    that a later reattach misreads as a valid governed signal. Best-effort: an
    OSError is swallowed (the in-memory ``LaneSession.governed`` still reflects
    the truth for the current process; the marker is the cross-restart cache).
    """
    marker = governed_marker_path(mission_dir, lane_short)
    payload = json.dumps(governor_fingerprint(settings_path), sort_keys=True)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(marker.parent), prefix=f".{lane_short}", suffix=_MARKER_SUFFIX
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_name, marker)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        marker.chmod(0o644)
    except OSError:
        _log_marker_warn("write", marker)


def remove_governed_marker(mission_dir: pathlib.Path, lane_short: str) -> None:
    """Remove a lane's governed marker if present (idempotent).

    Called whenever a lane is spawned/respawned NOT under the governor, so the
    marker always reflects the LIVE process and a stale governed marker can never
    outlive the governed regime it described.
    """
    marker = governed_marker_path(mission_dir, lane_short)
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        _log_marker_warn("remove", marker)


def read_governed_marker_is_valid(
    mission_dir: pathlib.Path,
    lane_short: str,
    *,
    settings_path: pathlib.Path | None = None,
) -> bool:
    """True iff a present marker's fingerprint matches the CURRENT settings.

    Used by the reattach branch to decide ``governed`` from SPAWN IDENTITY, NOT
    the (lying) rebuilt argv. Returns False — fail toward ungoverned — when the
    marker is absent, unreadable, malformed, or its fingerprint no longer matches
    the live governor settings (path or content hash changed).
    """
    marker = governed_marker_path(mission_dir, lane_short)
    try:
        stored = json.loads(marker.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(stored, dict):
        return False
    current = governor_fingerprint(settings_path)
    # An empty current hash means the settings file is unreadable now — a marker
    # cannot be validated against nothing, so fail toward ungoverned.
    if not current["settings_sha256"]:
        return False
    return (
        stored.get("settings_path") == current["settings_path"]
        and stored.get("settings_sha256") == current["settings_sha256"]
    )


def argv_is_governed(
    argv: list[str], *, settings_path: pathlib.Path | None = None
) -> bool:
    """True iff ``argv`` attaches ``--settings <current governor settings>``.

    Safe to trust ONLY for a spawn/respawn that is about to REPLACE the live
    process with exactly this argv (the process is genuinely (re)born with it) —
    e.g. ``FleetSpawner.respawn``. NEVER use this on the REATTACH path, where the
    rebuilt argv is a template that does not describe the already-running process.
    """
    path = str(settings_path if settings_path is not None else governor_settings_path())
    for i, tok in enumerate(argv):
        if tok == "--settings" and i + 1 < len(argv) and argv[i + 1] == path:
            return True
    return False


def _log_marker_warn(action: str, marker: pathlib.Path) -> None:
    """Best-effort WARNING for a marker IO failure (import-light: lazy logging)."""
    import logging

    logging.getLogger(__name__).warning(
        "governed-marker %s failed for %s — governance provenance may degrade to "
        "ungoverned on next reattach",
        action,
        marker,
    )


def preflight_governor(mission_dir: pathlib.Path) -> None:
    """Verify the governor is wirable; raise ``GovernorPreflightError`` if not.

    Checks, in order:
      1. ``governor_settings_path()`` exists and is valid JSON.
      2. The run-dir hook ``<mission_dir>/scripts/governor_hook.py`` exists
         (following the ``scripts/`` symlink new_run.sh creates) and is
         executable.

    Call this ONCE early in the spawn flow when the governor is enabled, before
    spawning any lane — so a broken hook fails the whole spawn up front rather
    than per-lane (and never degrades to a silent ``claude`` failure).

    Args:
        mission_dir: the run/mission directory (== ``$CLAUDE_PROJECT_DIR``).

    Raises:
        GovernorPreflightError: with a specific, actionable message.
    """
    settings = governor_settings_path()
    if not settings.exists():
        raise GovernorPreflightError(
            f"governor settings file missing: {settings}. "
            f"Expected the committed .claude/governor-settings.json at the repo "
            f"root — restore it from git."
        )
    try:
        json.loads(settings.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise GovernorPreflightError(
            f"governor settings file is not valid JSON: {settings} ({exc}). "
            f"Fix or restore .claude/governor-settings.json from git."
        ) from exc

    hook = mission_dir / _HOOK_REL
    # Resolve through the scripts/ symlink; .exists() already follows symlinks,
    # but resolve() makes the diagnostic concrete and catches dangling links.
    if not hook.exists():
        resolved = hook.resolve()
        raise GovernorPreflightError(
            f"governor hook not found at {hook} (resolves to {resolved}). "
            f"The run dir is missing its scripts/ symlink — new_run.sh should "
            f"create `scripts -> ../../scripts` in the run dir (new_run.sh:~77). "
            f"Without it the PreToolUse hook command file-not-founds and claude "
            f"would silently fail to start."
        )
    if not os.access(hook, os.X_OK):
        raise GovernorPreflightError(
            f"governor hook is not executable: {hook}. "
            f"Run `chmod +x scripts/governor_hook.py` — the PreToolUse hook is "
            f"invoked as a bare executable shim."
        )


def governor_canary_selftest(mission_dir: pathlib.Path) -> None:
    """Prove the run-dir governor hook actually DENIES the sentinel probe.

    Builds the canary PreToolUse event and pipes it through the run-dir shim as
    a subprocess EXACTLY as Claude Code will (the same path, the same
    ``CLAUDE_PROJECT_DIR`` env), then asserts the decision is ``deny`` AND
    attributed to the governor canary. Any other outcome — allow, hook error,
    malformed/empty stdout, non-zero exit, a timeout, or a deny for an unrelated
    reason — means the governor is NOT enforcing and raises
    :class:`GovernorCanaryError` naming exactly what happened.

    Call this ONCE early in the spawn flow, immediately AFTER
    :func:`preflight_governor` (same enabled-gate), so a mis-wired or
    non-enforcing governor aborts the whole spawn LOUDLY before any lane starts.

    Interpreter choice: the shim is executable with a ``#!/usr/bin/env python3``
    shebang, so we invoke it DIRECTLY (``[str(shim)]``) — the most faithful
    reproduction of how Claude Code runs it under a bare system ``python3``. If
    the shim is not marked executable (it should be — preflight checks that), we
    fall back to ``sys.executable`` so the self-test still gets a real verdict.

    Args:
        mission_dir: the run/mission directory (== ``$CLAUDE_PROJECT_DIR``).

    Raises:
        GovernorCanaryError: with a specific, actionable message.
    """
    # Import-light + lazy: the policy module is stdlib-only, but importing it
    # only when the self-test runs keeps the module-import cost off cold paths.
    from megalodon_ui.governor.policy import (
        GOVERNOR_CANARY_CATEGORY,
        canary_command,
    )

    shim = mission_dir / _HOOK_REL
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": canary_command()},
        "cwd": str(mission_dir),
    }
    event_json = json.dumps(event)

    # Most faithful: run the executable shim directly (its shebang picks the
    # interpreter, exactly as Claude Code does). Fall back to sys.executable only
    # if the shim is not executable.
    argv = [str(shim)] if os.access(shim, os.X_OK) else [sys.executable, str(shim)]

    try:
        proc = subprocess.run(
            argv,
            input=event_json,
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(mission_dir)},
            timeout=_CANARY_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GovernorCanaryError(
            f"governor canary self-test TIMED OUT after {_CANARY_TIMEOUT_S}s "
            f"running {shim}. The hook did not respond — the governor cannot be "
            f"trusted to enforce. Refusing to spawn."
        ) from exc
    except OSError as exc:
        raise GovernorCanaryError(
            f"governor canary self-test could not execute the hook {shim} "
            f"({exc}). The governor is not enforcing. Refusing to spawn."
        ) from exc

    if proc.returncode != 0:
        raise GovernorCanaryError(
            f"governor canary self-test: hook {shim} exited non-zero "
            f"(rc={proc.returncode}). stderr={proc.stderr.strip()!r}. The "
            f"governor is not enforcing. Refusing to spawn."
        )

    try:
        payload = json.loads(proc.stdout)
        hook_out = payload["hookSpecificOutput"]
        permission = hook_out["permissionDecision"]
        reason = hook_out.get("permissionDecisionReason", "")
    except (ValueError, KeyError, TypeError) as exc:
        raise GovernorCanaryError(
            f"governor canary self-test: hook {shim} produced malformed output "
            f"({exc}). stdout={proc.stdout.strip()!r}. The governor is not "
            f"enforcing. Refusing to spawn."
        ) from exc

    if permission != "deny":
        raise GovernorCanaryError(
            f"governor canary self-test: hook {shim} returned "
            f"permissionDecision={permission!r} for the sentinel probe — it "
            f"should DENY. The governor is NOT enforcing (a real dangerous "
            f"command would also slip through). Refusing to spawn. "
            f"reason={reason!r}"
        )

    # A deny is necessary but not sufficient: it must be the canary deny, not an
    # incidental deny for some unrelated reason (which would mask a governor that
    # is enforcing the WRONG thing). The hook reason carries the canary signature.
    if GOVERNOR_CANARY_CATEGORY not in reason and "canary" not in reason.lower():
        raise GovernorCanaryError(
            f"governor canary self-test: hook {shim} denied the sentinel but "
            f"NOT as the governor canary (reason={reason!r}). Enforcement cannot "
            f"be confirmed. Refusing to spawn."
        )
