"""Governor permission-policy engine (Task 1.1) — the security core.

A pure, import-light, fail-closed decision function:

    decide(tool_name, tool_input, *, project_dir, lane) -> Decision

Policy model: **allow-by-default + deny-matched-dangerous**, per tool family.
The net invariant is "strictly no looser than the prior --allowedTools system":
default to allow, deny only matched-dangerous vectors.

Bash deny posture and overridability:
  * The newly-denied **generic-exec** commands (build/exec runners such as
    ``make`` → ``bash-exec-runner``; editors that shell out / write back such as
    ``vim``/``ed`` → ``bash-editor``) are **non-floor**: an operator can re-admit
    them per-project via ``<project_dir>/.fleet/approval-rules.json`` (see
    :func:`_load_override_patterns` / :func:`_override_allows`), which flips the
    deny → allow with category ``allow-override``.
  * The **floor** categories in :data:`_FLOOR_CATEGORIES`
    (``bash-root-destructive``, ``bash-privilege``, ``secret-read``,
    ``write-out-of-scope``, ``anti-tamper``, ``write-secret``) are NOT
    overridable — no approval rule can re-admit them.
  * The fleet's own test gate runs via the allowlisted script heads
    ``scripts/run_tests.sh`` / ``scripts/run_e2e.sh``, which the policy allows
    (the deny tightening above must never catch these).

Discipline (enforced by design):
  * NO I/O of any kind except reading ``<project_dir>/.fleet/approval-rules.json``
    for the operator allow-override (last step before returning a Bash deny).
    No stdout, no logging, no network.
  * Any exception inside :func:`decide` is caught and converted to
    ``Decision("deny", reason="governor-error: <detail>", category="governor-error")``
    — fail closed.
  * stdlib only (``shlex``, ``os``, ``re``, ``dataclasses``, ``pathlib``,
    ``json``). No third-party deps — this module is imported by a
    latency-sensitive hook in a later task.

The interpreter/destructive head posture replicates the intent of the
now-removed ``_FORBIDDEN_HEAD_CMDS`` / ``_is_unbounded_tool`` (deleted from
``megalodon_ui/harnesses/claude.py`` in Task 3.3): this module is now the single
source of truth for that logic.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Decision:
    """A permission verdict.

    Attributes:
        permission: ``"allow"`` or ``"deny"``.
        reason: Human-readable justification (carries a ``WARN`` marker for
            the ``unknown-tool-warn`` category).
        category: A stable machine-readable bucket (e.g. ``bash-substitution``,
            ``secret-read``, ``allow-override``, ``governor-error``).
    """

    permission: str
    reason: str
    category: str


def _allow(reason: str, category: str) -> Decision:
    return Decision("allow", reason, category)


def _deny(reason: str, category: str) -> Decision:
    return Decision("deny", reason, category)


# ---------------------------------------------------------------------------
# Governor canary sentinel (Task 2.3) — the single source of truth
# ---------------------------------------------------------------------------

# A unique, recognizable marker. When a Bash command contains this exact token,
# :func:`decide` denies it EARLY (before any allow logic), so the sentinel is
# always denied regardless of how benign the surrounding command looks. The
# token is chosen to be SAFE-WHEN-UNBLOCKED: if the governor is NOT enforcing
# and the command runs, `echo megalodon-governor-canary-v1` merely prints the
# token — it neither mutates state nor escapes scope. That makes it a sound
# probe for the agent-side runtime canary (launch.md Step 0) and the fleet-side
# self-test (wiring.governor_canary_selftest). ONE constant, referenced by the
# policy / self-test / launch prose / tests — never duplicated as a literal.
GOVERNOR_CANARY_TOKEN = "megalodon-governor-canary-v1"

# The stable machine-readable bucket the canary deny carries.
GOVERNOR_CANARY_CATEGORY = "governor-canary"


def canary_command() -> str:
    """The exact Bash command the agent / self-test issues to probe enforcement.

    Returns ``echo <token>``: harmless if it ever DOES run (it just prints the
    token), but denied by :func:`decide` when the governor is enforcing. The
    single source of truth for the probe string — launch prose, the fleet-side
    self-test, and the tests all call this rather than hardcoding the command.
    """
    return f"echo {GOVERNOR_CANARY_TOKEN}"


# ---------------------------------------------------------------------------
# Constants — tool families
# ---------------------------------------------------------------------------

# Tools that touch nothing (no fs/exec/network) — always allowed.
_INERT_TOOLS = frozenset({"TodoWrite", "AskUserQuestion", "ExitPlanMode"})

# Inert/safe control tools that legitimately reach the FINAL (genuinely-unknown)
# branch of :func:`decide`. The default for an unknown tool is now DENY
# (fail-closed: a fabricated MCP tool carrying a destructive payload must not be
# allowed-by-default), so this allowlist is the carve-out for the handful of
# control tools that touch nothing — no filesystem, no exec, no network — yet
# are not native tool families. They are checked BEFORE the unknown-tool deny
# and return an allow with a LOUD WARN so a genuinely-new tool that happens to
# land here is still surfaced for explicit classification rather than silently
# trusted. Kept in sync with (a superset of) _INERT_TOOLS so that if the early
# _INERT_TOOLS short-circuit is ever removed these still fail safe to allow.
#   TodoWrite       — writes the agent's in-memory todo list only (no fs/exec).
#   AskUserQuestion — surfaces a question to the operator (no side effects).
#   ExitPlanMode    — a mode-transition control signal (no side effects).
_INERT_ALLOWED_TOOLS = frozenset({"TodoWrite", "AskUserQuestion", "ExitPlanMode"})

# Subagent-spawning tools — a spawned subagent could run OUTSIDE the governor.
_SPAWN_TOOLS = frozenset({"Task", "Agent"})

# Native read-style tools whose path argument is scope/secret-checked.
_READ_TOOLS = frozenset({"Read", "Grep", "Glob"})

# Native write-style tools (NotebookEdit is governed AS Write).
_WRITE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

_NETWORK_TOOLS = frozenset({"WebFetch", "WebSearch"})


# ---------------------------------------------------------------------------
# Secret / scope path helper (ONE helper, two call sites: native Read + Bash L3)
# ---------------------------------------------------------------------------

# Private-key / credential / secret path signatures, matched against the
# canonicalized path's basename or full path.
_SECRET_BASENAME_RE = re.compile(
    r"""
    (?:^|.*[/\\])                      # anchored at a path component
    (?:
        \.env(?:\..+)?                 # .env / .env.local / .env.production
      | .*credentials.*                # *credentials*
      | .*_rsa(?:\..*)?                # *_rsa / id_rsa.pub etc
      | .*_dsa(?:\..*)?
      | .*_ed25519(?:\..*)?
      | id_rsa.*
      | .*\.pem
      | .*\.key
      | .*\.p12
      | .*\.keystore
    )$
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Anything under a ~/.ssh directory is a secret regardless of basename.
_SSH_DIR_RE = re.compile(r"(?:^|/)\.ssh(?:/|$)")


def _canonicalize(path: str) -> str:
    """Canonicalize a path: expand ``~``, resolve ``..`` and symlinks.

    ``os.path.realpath`` (after ``expanduser`` + ``expandvars``) resolves
    ``..`` and follows symlinks, defeating traversal bypasses such as
    ``./a/../../.ssh/id_rsa``.
    """
    expanded = os.path.expanduser(os.path.expandvars(path))
    return os.path.realpath(expanded)


def _is_within(child: str, parent: str) -> bool:
    """True if canonical ``child`` is ``parent`` or nested under it."""
    parent_real = os.path.realpath(parent)
    try:
        common = os.path.commonpath([child, parent_real])
    except ValueError:
        # Different drives / mix of absolute+relative — treat as outside.
        return False
    return common == parent_real


def _is_temp_path(canonical: str) -> bool:
    """True if a canonical path lives under an allowed scratch temp dir.

    Deliberately restricted to the well-known ``/tmp`` (and its macOS
    ``/private/tmp`` realpath). ``$TMPDIR`` is intentionally NOT honored here:
    on macOS it points under ``/var/folders/.../T`` which can transitively
    contain a mission/run tree, and admitting it would let an out-of-scope
    write masquerade as scratch. The ``rm``-target carve-out uses
    :func:`_is_explicit_temp_target` instead, which honors ``$TMPDIR`` only for
    a path literally spelled with it.
    """
    return any(_is_within(canonical, c) for c in ("/tmp", "/private/tmp"))


def _within_scope(canonical: str, project_dir: Path, target_dir: Path | None) -> bool:
    """True if a canonical path is within an allowed read/write root.

    Allowed roots are ``project_dir``, the optional work-on-target ``target_dir``
    (the external repo the fleet edits in place), and the usual ``/tmp`` scratch.
    ``target_dir`` widens the sandbox by exactly ONE explicitly-configured root —
    everything outside ``{project_dir, target_dir, /tmp}`` stays denied. Floors
    (secret / anti-tamper) are checked by callers BEFORE this scope test, so an
    in-target secret-path or governor-config write still denies.
    """
    if _is_within(canonical, str(project_dir)):
        return True
    if target_dir is not None and _is_within(canonical, str(target_dir)):
        return True
    return _is_temp_path(canonical)


def _is_explicit_temp_target(raw: str) -> bool:
    """True if a raw (pre-canonical) path is an explicit scratch temp target.

    Used by the destructive-``rm`` carve-out: a target is "explicit temp" when
    it is spelled under ``/tmp`` or under ``$TMPDIR`` (the literal env value).
    """
    if raw.startswith(("/tmp/", "/tmp")) or raw == "/tmp":
        return _is_within(_canonicalize(raw), "/tmp")
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir and (raw.startswith("$TMPDIR") or raw.startswith(tmpdir)):
        return True
    return False


def _path_is_secret_or_out_of_scope(
    path: str, *, project_dir: Path, target_dir: Path | None = None
) -> tuple[bool, str, str]:
    """Adjudicate a path argument.

    Canonicalizes first (realpath → resolves ``..``, follows symlinks, expands
    ``~``), then checks secret signatures and scope.

    Returns ``(blocked, reason, category)``. ``blocked`` is False when the path
    is allowed; ``reason``/``category`` are only meaningful when blocked.
    """
    raw = path.strip()
    canonical = _canonicalize(raw)

    # Secret check (on both the canonical path and the original spelling, so a
    # symlink that happens to canonicalize away the suffix still trips on the
    # pre-resolution name).
    for probe in (canonical, os.path.expanduser(os.path.expandvars(raw))):
        if _SSH_DIR_RE.search(probe) or _SECRET_BASENAME_RE.match(probe):
            return True, f"secret path: {raw}", "secret-read"

    # Scope check: must be within project_dir, the target repo, or a temp dir.
    if _within_scope(canonical, project_dir, target_dir):
        return False, "", ""
    return True, f"path outside scope: {raw} -> {canonical}", "out-of-scope"


# Anti-tamper: the governor's own config / hook / .claude governor files. The
# single source of truth for the anti-tamper lock — used by BOTH the native
# Write/Edit/NotebookEdit tools and Bash write-redirect targets, so two paths to
# the same file enforce the same guard.
_ANTI_TAMPER_RE = re.compile(
    r"(?:^|/)(?:governor-settings\.json|governor_hook\.py)$"
    r"|(?:^|/)\.claude/.*governor",
    re.IGNORECASE,
)


def _adjudicate_write_target(
    path: str, *, project_dir: Path, target_dir: Path | None = None
) -> Decision:
    """Adjudicate a WRITE destination: anti-tamper + secret + scope.

    The single write-target lock. ONE helper, multiple call sites
    (Write/Edit/NotebookEdit AND Bash ``>``/``>>`` redirect targets) so the
    governor's own config cannot be overwritten through either route.
    """
    canonical = _canonicalize(path)

    # Anti-tamper: governor's own config/hook/settings (check both canonical and
    # original spelling, so a relative `.claude/...governor` is caught even if
    # canonicalization rebases it).
    for probe in (canonical, os.path.expanduser(os.path.expandvars(path))):
        if _ANTI_TAMPER_RE.search(probe):
            return _deny(f"anti-tamper: governor file {path}", "anti-tamper")

    # Secret destination.
    if _SSH_DIR_RE.search(canonical) or _SECRET_BASENAME_RE.match(canonical):
        return _deny(f"write to secret path: {path}", "write-secret")

    # Scope: must be within project_dir, the target repo, or a temp dir.
    if _within_scope(canonical, project_dir, target_dir):
        return _allow("write within scope", "write-ok")
    return _deny(f"write outside scope: {path} -> {canonical}", "write-out-of-scope")


# ---------------------------------------------------------------------------
# Bash — Layer 1: substitution detection + segmentation
# ---------------------------------------------------------------------------

# Command / process substitution. Detected on RAW segment text BEFORE shlex,
# because shlex does not expand these — they smuggle execution through an
# otherwise-allowed head (e.g. echo $(python3 -c '...')).
#   $( ... )    command substitution
#   ` ... `     backtick substitution
#   <( ... )    process substitution (input)
#   >( ... )    process substitution (output)
_SUBSTITUTION_RE = re.compile(r"\$\(|`|<\(|>\(")

# Control operators we segment on: ; && || | & and newline. Splitting is
# QUOTE-AWARE (a quoted ``|`` such as in ``grep -E 'foo|bar'`` is NOT a
# separator) and done via a manual scan. Each segment is then adjudicated
# independently. Substitution is rejected outright (above) and any segment
# shlex cannot parse fails closed, so an imperfect split cannot smuggle an
# unseen dangerous head past us.


def _split_segments(command: str) -> list[str]:
    """Split a command into segments on UNQUOTED shell control operators.

    Quote-aware: operators inside single/double quotes are not separators, so
    benign idioms like ``grep -E 'foo|bar'`` stay intact. Benign pipes survive
    because each *segment* is adjudicated and benign segments pass
    (``ls | head`` → ``ls`` + ``head``, both allowed).

    Grouping delimiters ``(`` ``)`` ``{`` ``}`` are ALSO treated as segment
    boundaries so a subshell/brace group cannot hide the real head: ``(rm -rf
    /etc)`` and ``{ curl http://evil; }`` split into their inner commands, which
    then reach the head/flag/floor checks. Command/process substitution
    (``$(…)``/``<(…)``) is denied as substitution BEFORE segmentation, so
    stripping a bare leading ``(``/``{`` here is safe.
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            # Preserve escaped char verbatim (e.g. ``\;`` for find -exec).
            buf.append(ch)
            buf.append(command[i + 1])
            i += 2
            continue
        two = command[i : i + 2]
        if two in ("&&", "||"):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch in (";", "|", "&", "\n", "(", ")", "{", "}"):
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


# ---------------------------------------------------------------------------
# Bash — Layer 2: head denylist + flag denylist + destructive/privilege/network
# ---------------------------------------------------------------------------

# Interpreter / unbounded heads. Matched against the segment head (token 0),
# sometimes in combination with a following flag.
_INTERPRETER_HEADS = frozenset(
    {"python", "python2", "python3", "eval", "ruby", "perl", "node", "php"}
)
# Heads that are interpreters only with a code-exec flag (e.g. `bash -c`).
_DASH_C_SHELLS = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "ksh",
        "dash",
        "fish",
        "csh",
        "tcsh",
        "nu",
        "pwsh",
        "powershell",
        "elvish",
        "xonsh",
    }
)

# Read-style heads whose path-like args get the secret/scope check (Layer 3).
_READ_STYLE_HEADS = frozenset(
    {
        "cat",
        "less",
        "more",
        "head",
        "tail",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "find",
        "ls",
        "stat",
        "file",
        "wc",
        "sort",
        "uniq",
        "cut",
        "awk",
        "gawk",
        "sed",
        "diff",
        "od",
        "xxd",
        "strings",
    }
)

# Shell keyword prefixes that precede (and run) the REAL command. Stripped from
# the segment head so the inner command is adjudicated: `time python3 ...`,
# `exec python3 ...`, `command python3 ...`, `builtin cd ...`, `! python3 ...`.
_KEYWORD_PREFIXES = frozenset(
    {"exec", "command", "builtin", "time", "!", "then", "do", "else", "elif"}
)

# Privilege-escalation heads.
_PRIVILEGE_HEADS = frozenset({"sudo", "su", "doas"})

# Raw network heads (MVP: Bash network is simply denied; no host allowlist).
_NETWORK_HEADS = frozenset(
    {"curl", "wget", "ssh", "scp", "nc", "ncat", "telnet", "ftp", "sftp", "rsync"}
)

# Installers / package managers.
_INSTALLER_HEADS = frozenset(
    {
        "pip",
        "pip3",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "brew",
        "apt",
        "apt-get",
        "yum",
        "dnf",
        "apk",
        "gem",
        "cargo",
    }
)

# Destructive non-interpreter heads (always denied — there is no safe form here).
_DESTRUCTIVE_HEADS = frozenset(
    {"dd", "shred", "mkfs", "mkfs.ext4", "mkfs.ext3", "mkfs.xfs", "mkfs.vfat"}
)

# Editors: arbitrary-code surfaces (shell-out via `:!`/`-c`, ex commands, and
# they write their file operand back). Denied outright — there is no safe form
# under an autonomous agent. NOT a floor category (operator-overridable later).
_EDITOR_HEADS = frozenset({"vim", "vi", "ex", "ed", "nano", "emacs"})

# Build / task runners + GNU parallel: each reads a recipe/jobfile and executes
# arbitrary commands (Makefile, justfile, Rakefile, build.gradle, pom.xml,
# `parallel <cmd> ::: args`). Denied outright. `make test`/gate commands are
# handled later by the allow-override layer, NOT special-cased here. NOT a floor.
_EXEC_RUNNER_HEADS = frozenset(
    {"make", "just", "task", "rake", "gradle", "mvn", "ant", "parallel"}
)

# Command-wrapper heads: they run a child command, so we adjudicate the wrapped
# command instead of the wrapper.
_WRAPPER_HEADS = frozenset(
    {"nohup", "timeout", "env", "nice", "ionice", "stdbuf", "setsid", "watch"}
)

# Flag denylists per head (any flag that spawns a child or writes a file).
_FIND_BAD_FLAGS = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprintf", "-fprint", "-fls"}
)
_RG_BAD_FLAGS = frozenset({"--pre", "--pre-glob", "--hostname-bin"})
_FD_BAD_FLAGS = frozenset({"-x", "-X", "--exec", "--exec-batch"})
_TREE_BAD_FLAGS = frozenset({"-o"})
_TAR_BAD_FLAG_PREFIXES = (
    "--checkpoint-action",
    "--to-command",
    "--use-compress-program",
)


def _strip_quotes(tok: str) -> str:
    return tok.strip("\"'")


def _segment_destructive_rm(tokens: list[str], *, project_dir: Path) -> bool:
    """True if an ``rm -r``/``-f``/``-rf`` targets anything outside a temp dir.

    Per the threat model only the recursive/force forms are denied; a plain
    ``rm file`` inside scope is mutating but bounded and not in the deny matrix.
    """
    flags = "".join(t for t in tokens[1:] if t.startswith("-"))
    recursive_or_force = (
        ("r" in flags)
        or ("f" in flags)
        or ("-recursive" in tokens)
        or ("-force" in tokens)
    )
    if not recursive_or_force:
        return False
    targets = [t for t in tokens[1:] if not t.startswith("-")]
    if not targets:
        # `rm -rf` with no explicit target — refuse.
        return True
    # Denied unless EVERY target is an explicit scratch temp path.
    return any(not _is_explicit_temp_target(_strip_quotes(t)) for t in targets)


def _plain_rm_violation(tokens: list[str], *, project_dir: Path) -> Decision | None:
    """Scope/secret-check the targets of a PLAIN (non-recursive/force) ``rm``.

    The recursive/force forms are handled by :func:`_segment_destructive_rm`;
    this guards the plain form, which previously had NO scope/secret check at
    all. Each non-flag operand is anchored at ``project_dir`` (the bash cwd) and
    routed through the same secret/scope floor as ``cat``: a secret target
    denies as ``secret-read`` (a floor), an out-of-scope target as
    ``out-of-scope``. An in-scope ``rm file.txt`` raises no objection.
    """
    targets = [t for t in tokens[1:] if not t.startswith("-")]
    for tgt in targets:
        cand = _strip_quotes(tgt)
        if not cand:
            continue
        blocked, reason, category = _path_is_secret_or_out_of_scope(
            _anchor_to_project(cand, project_dir), project_dir=project_dir
        )
        if blocked:
            return _deny(f"{reason} (via rm)", category)
        if _SECRET_BASENAME_RE.match(cand):
            return _deny(f"secret target basename: {cand} (via rm)", "secret-read")
    return None


def _segment_git_dangerous(tokens: list[str]) -> bool:
    """True if a git invocation uses config-injection / external-diff flags.

    Read-only git WITHOUT these (`git log`, `git status`, `git diff`) is allowed.
    Denied:
      * ``-c key=val`` and the glued ``-ckey=val`` form (arbitrary config
        injection, e.g. ``git -c core.pager='rm x' log``);
      * ``--ext-diff`` (runs an external diff program);
      * ``--config-env`` config-override long opts.
    """
    for a in tokens[1:]:
        if a == "-c" or (a.startswith("-c") and len(a) > 2):
            return True
        if a == "--ext-diff":
            return True
        if a.startswith("--config-env"):
            return True
    return False


def _segment_awk_dangerous(tokens: list[str]) -> bool:
    """True if an awk/gawk program string or flag enables exec/IO."""
    for a in tokens[1:]:
        if a == "-f":
            return True  # external program file
        if a.startswith("-f") and len(a) > 2:
            return True
    prog = " ".join(tokens[1:])
    if "system(" in prog:
        return True
    if "BEGIN{" in prog or "BEGIN {" in prog:
        return True
    if "|" in prog:  # piping inside the awk program (e.g. print | "sh")
        return True
    return False


def _segment_sed_dangerous(tokens: list[str]) -> bool:
    """True if a sed program uses e/w/W commands or in-place editing."""
    for a in tokens[1:]:
        if (
            a in ("-i", "--in-place")
            or a.startswith("--in-place")
            or (a.startswith("-i") and len(a) > 2)
        ):
            return True
    # `-f`/`--file <script>` runs an external sed program file = arbitrary sed
    # code from an out-of-band file (parity with awk's `-f` deny). Deny outright
    # BEFORE the script-body inspection (where `-f` would otherwise be consumed
    # as a skipped value-flag and never adjudicated). The `f` flag can appear
    # ANYWHERE in a bundled short-flag group (`-nf evil.sed`, `-senf evil.sed`),
    # so scan the chars of every short-flag token, not just a leading `-f`.
    for a in tokens[1:]:
        if a in ("--file",) or a.startswith("--file="):
            return True
        if a.startswith("-") and not a.startswith("--") and len(a) > 1:
            if "f" in a[1:]:
                return True
    # Inspect script bodies (tokens that are not flags / not the file operand).
    scripts: list[str] = []
    skip_next = False
    for i, a in enumerate(tokens[1:]):
        if skip_next:
            scripts.append(a)
            skip_next = False
            continue
        if a in ("-e", "-f", "--expression", "--file"):
            skip_next = True
            continue
        if a.startswith("-"):
            continue
        scripts.append(a)
    for s in scripts:
        body = _strip_quotes(s)
        # Write command/flag: `w <file>` or `W <file>`, incl. the substitution
        # write flag `s/x/y/w /tmp/out` — a `w`/`W` followed by a filename.
        if re.search(r"[wW]\s+\S", body) or body.rstrip().endswith(("w", "W")):
            return True
        # Execute: the `s///e` flag or a standalone `e` command.
        if re.search(r"/e\b", body) or re.search(r"(^|;)\s*e(\s|$)", body):
            return True
    return False


def _segment_tar_dangerous(tokens: list[str], *, project_dir: Path) -> bool:
    """True if a tar invocation is exec-dangerous or writes/extracts out of scope.

    Denied:
      * exec-spawning flags (``--to-command``/``--checkpoint-action``/
        ``--use-compress-program``);
      * a ``-C``/``--directory`` whose value resolves outside the scope (extract
        target dir — ``tar -C /etc -xf a.tar`` writes into ``/etc``);
      * a create-mode archive output (``-f FILE`` with ``-c``) outside scope.
    """
    for a in tokens[1:]:
        if any(a == p or a.startswith(p + "=") for p in _TAR_BAD_FLAG_PREFIXES):
            return True
    # Scope-check the change-directory value: tar -C DIR / --directory=DIR / -CDIR
    # is where extracted files land, so an out-of-scope DIR is an out-of-scope
    # write. tar's classic short form BUNDLES flags (`-xCf /etc a.tar`): within a
    # bundle the value-consuming flags (`C`,`f`,...) draw their values, in order,
    # from the following positional tokens. Decompose bundles so `-xCf` and
    # `-czf` are handled the same as separated `-x -C ... -f ...`.
    change_dirs: list[str] = []
    archive_create = False
    archive_file: str | None = None
    rest = tokens[1:]
    # tar short flags that consume a value (the value is the glued remainder of
    # the bundle, else the next positional tokens in flag order).
    # ...and the set of ALL recognized tar short option letters. A value flag's
    # value is the glued bundle remainder ONLY when that remainder does not itself
    # begin with another option letter (so `-Cdir` -> C=dir, but
    # `-xCf /etc a.tar` -> C and f BOTH draw positionals because the char after C
    # (`f`) is an option letter — tar's old-option rule). Keeps separated forms
    # (`-C dir`, `-f name`) working too.
    tar_value_short = {"C", "f", "T", "b", "X", "g", "F", "K", "N", "L"}
    tar_option_letters = tar_value_short | set("cxtrudvzjJ")
    i = 0
    while i < len(rest):
        tok = rest[i]
        # Long options.
        if tok.startswith("--"):
            if tok == "--directory" and i + 1 < len(rest):
                change_dirs.append(rest[i + 1])
                i += 2
                continue
            if tok.startswith("--directory="):
                change_dirs.append(tok.partition("=")[2])
            elif tok == "--file" and i + 1 < len(rest):
                archive_file = rest[i + 1]
                i += 2
                continue
            elif tok.startswith("--file="):
                archive_file = tok.partition("=")[2]
            elif tok == "--create":
                archive_create = True
            i += 1
            continue
        # Short flag bundle, e.g. `-xCf`, `-czf`, `-C`, `-f`, `-Cdir`.
        if tok.startswith("-") and len(tok) > 1:
            chars = tok[1:]
            positional_value_flags: list[str] = []
            k = 0
            while k < len(chars):
                ch = chars[k]
                if ch == "c":
                    archive_create = True
                if ch in tar_value_short:
                    remainder = chars[k + 1 :]
                    if remainder and remainder[0] not in tar_option_letters:
                        # Glued inline value, e.g. `-Cdir`, `-fname`.
                        if ch == "C":
                            change_dirs.append(remainder)
                        elif ch == "f":
                            archive_file = remainder
                        break  # remainder consumed as this flag's value
                    # Value comes from a following positional token (queued).
                    positional_value_flags.append(ch)
                k += 1
            j = i + 1
            for c in positional_value_flags:
                if j >= len(rest):
                    break
                if c == "C":
                    change_dirs.append(rest[j])
                elif c == "f":
                    archive_file = rest[j]
                j += 1
            i = j
            continue
        i += 1
    for cd in change_dirs:
        cand = _strip_quotes(cd)
        if (
            _adjudicate_write_target(
                _anchor_to_project(cand, project_dir), project_dir=project_dir
            ).permission
            == "deny"
        ):
            return True
    if archive_create and archive_file and archive_file not in ("-",):
        cand = _strip_quotes(archive_file)
        if (
            _adjudicate_write_target(
                _anchor_to_project(cand, project_dir), project_dir=project_dir
            ).permission
            == "deny"
        ):
            return True
    return False


def _is_root_destructive(tokens: list[str]) -> bool:
    """Non-overridable floor: root-level destruction (``rm -rf /`` and kin)."""
    if not tokens or os.path.basename(tokens[0]).lower() != "rm":
        return False
    flags = "".join(t for t in tokens[1:] if t.startswith("-"))
    if not (("r" in flags) or ("f" in flags)):
        return False
    for tgt in (t for t in tokens[1:] if not t.startswith("-")):
        t = _strip_quotes(tgt)
        if t in ("/", "/*") or _canonicalize(t) == "/":
            return True
    return False


# Floor categories that an allow-override can NEVER lift. The write-target lock
# categories (anti-tamper / write-secret / write-out-of-scope) are floors too:
# an out-of-scope write or governor-config tamper — whether via a `>` redirect
# or a mutation head (cp/mv/tee/...) — must not be flippable by an operator
# rule, matching the spec's "floor categories non-overridable" requirement for
# the mutation write-targets.
_FLOOR_CATEGORIES = frozenset(
    {
        "bash-root-destructive",
        "bash-privilege",
        "secret-read",
        "anti-tamper",
        "write-secret",
        "write-out-of-scope",
    }
)


# Classic fork-bomb signature (collapse whitespace before matching).
_FORKBOMB_RE = re.compile(r":\(\)\s*\{.*:\|:.*\}")

# A token that is purely a redirection operator (e.g. `>`, `>>`, `2>`, `<`,
# `&>`, `1>>`) optionally glued to its target (e.g. `2>/dev/null`, `>out`).
_REDIRECT_TOKEN_RE = re.compile(r"^(?P<op>[0-9]*&?(?:>>|>|<<|<))(?P<tgt>.*)$")


def _handle_redirects(
    tokens: list[str], *, project_dir: Path, target_dir: Path | None = None
) -> tuple[Decision | None, list[str]]:
    """Adjudicate write-redirect targets and strip all redirect tokens.

    Returns ``(deny_or_None, remaining_tokens)``. A write redirect (``>``/``>>``)
    to a path outside ``project_dir`` (and not a scratch temp) is denied; benign
    redirects (``2>/dev/null``, input ``<``) are simply removed so they are not
    mis-read as path operands of the head command.
    """
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        m = _REDIRECT_TOKEN_RE.match(tok)
        if m and (">" in m.group("op") or "<" in m.group("op")):
            is_write = ">" in m.group("op")
            tgt = m.group("tgt")
            if not tgt and i + 1 < len(tokens):
                # Operator and target are separate tokens (e.g. `> out`).
                tgt = tokens[i + 1]
                i += 1
            if tgt and is_write:
                verdict = _adjudicate_write_redirect_target(
                    tgt, project_dir=project_dir, target_dir=target_dir
                )
                if verdict is not None:
                    return verdict, out
            i += 1
            continue
        out.append(tok)
        i += 1
    return None, out


def _adjudicate_write_redirect_target(
    target: str, *, project_dir: Path, target_dir: Path | None = None
) -> Decision | None:
    """Deny a ``>``/``>>`` redirect whose target fails the write-target lock.

    Routes through the SAME :func:`_adjudicate_write_target` used by the native
    Write/Edit/NotebookEdit tools — so the anti-tamper, secret, and scope guards
    are identical whether a file is reached via a tool or a Bash redirect. The
    write-target categories (``anti-tamper`` / ``write-secret`` /
    ``write-out-of-scope``) are preserved so a deny is attributed correctly.
    """
    tgt = _strip_quotes(target)
    if tgt in ("/dev/null", "/dev/stdout", "/dev/stderr"):
        return None
    # A Bash command runs with cwd = the mission dir, so a RELATIVE redirect
    # target resolves against project_dir — not the governor process's cwd.
    # Anchor it there before the scope check (anti-tamper still matches the raw
    # spelling, so a relative `.claude/...governor` is caught regardless).
    expanded = os.path.expanduser(os.path.expandvars(tgt))
    resolved = (
        tgt if os.path.isabs(expanded) else os.path.join(str(project_dir), expanded)
    )
    verdict = _adjudicate_write_target(
        resolved, project_dir=project_dir, target_dir=target_dir
    )
    if verdict.permission == "deny":
        # Re-attribute against the original spelling for a clearer reason.
        return _deny(verdict.reason.replace(resolved, tgt), verdict.category)
    return None


# ---------------------------------------------------------------------------
# Bash — mutation heads (write-target lock + secret-source floor)
# ---------------------------------------------------------------------------

# Heads that WRITE/CREATE/OVERWRITE their file operands. These were previously
# treated as opaque and returned `bash-ok` — a confirmed hole: `cp secret
# /etc/passwd`, `tee -a /etc/sudoers`, `mv x /etc/cron.d/job`,
# `truncate -s 0 /etc/hosts`, `touch /etc/evil`, `mkdir /etc/evil`,
# `ln -s /etc/passwd /tmp/leak` all slipped past the scope/secret guards. Each
# head's destination operand(s) are now routed through the SAME
# :func:`_adjudicate_write_target` lock used by Write/Edit and `>` redirects,
# and SOURCE operands (the thing being copied/linked) through the SAME
# secret-read floor used by `cat`. We FAIL CLOSED: if the operands cannot be
# parsed confidently, the segment is denied (these are mutation/exfil commands).
_MUTATION_HEADS = frozenset(
    {
        "cp",
        "mv",
        "tee",
        "truncate",
        "touch",
        "mkdir",
        "ln",
        "install",
        "mktemp",
        "split",
        "csplit",
        "pv",
    }
)

# Flags that consume a following VALUE token (so the value is not mis-read as a
# path operand), keyed by head. CRITICAL: `-s` is a VALUE flag for `truncate`
# (`--size`) but a NO-VALUE flag for `ln` (`--symbolic`) — sharing one set would
# let `ln -s /etc/passwd /tmp/leak` swallow the laundered target. Long `--x=val`
# flags carry their own value and are handled generically (not listed here).
_MUTATION_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "truncate": frozenset({"-s", "--size", "-r", "--reference", "-o", "--io-blocks"}),
    "install": frozenset(
        {"-m", "--mode", "-o", "--owner", "-g", "--group", "-t", "--target-directory"}
    ),
    "cp": frozenset({"-t", "--target-directory", "-S", "--suffix"}),
    "mv": frozenset({"-t", "--target-directory", "-S", "--suffix"}),
    "tee": frozenset(),
    "touch": frozenset({"-d", "--date", "-r", "--reference", "-t"}),
    "mkdir": frozenset({"-m", "--mode"}),
    "ln": frozenset({"-S", "--suffix", "-t", "--target-directory"}),
    # `mktemp -p DIR` (a.k.a. --tmpdir) names the write directory; the trailing
    # template positional is the write destination otherwise. `--suffix` consumes
    # a value. `-d/-q/-u/-t` are no-value flags.
    "mktemp": frozenset({"-p", "--tmpdir", "--suffix"}),
    # `split` value flags carry sizes/counts; the LAST positional is the output
    # prefix (write dest), the first positional the input.
    "split": frozenset(
        {
            "-b",
            "--bytes",
            "-C",
            "--line-bytes",
            "-l",
            "--lines",
            "-n",
            "--number",
            "-a",
            "--suffix-length",
            "--additional-suffix",
        }
    ),
    # `csplit -f PREFIX` is the output prefix (write dest); other value flags
    # carry counts/widths.
    "csplit": frozenset({"-f", "--prefix", "-b", "--suffix-format", "-n", "--digits"}),
    # `pv -o FILE` is the write destination.
    "pv": frozenset(
        {
            "-o",
            "--output",
            "-s",
            "--size",
            "-L",
            "--rate-limit",
            "-B",
            "--buffer-size",
            "-N",
            "--name",
            "-i",
            "--interval",
            "-w",
            "--width",
            "-H",
            "--height",
            "-l",
            "--line-mode",
        }
    ),
}


# `-t`/`--target-directory <dir>` (cp/mv/install) names the WRITE destination
# directory and makes EVERY positional operand a source. Its value must NOT be
# discarded as an opaque flag value (the prior bug: the out-of-scope target dir
# was consumed and never scope-checked). We capture it for the caller to lock.
_TARGET_DIR_FLAGS = frozenset({"-t", "--target-directory"})

# Per-head flags whose VALUE is a WRITE destination (captured like target_dir,
# never discarded as an opaque flag value): mktemp `-p DIR`, csplit `-f PREFIX`,
# pv `-o FILE`. These heads have no positional destination when the flag is
# present (mktemp/pv) or the prefix IS the destination (csplit). Captured into
# the returned ``target_dir`` slot and scope-locked by the caller.
_DEST_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "mktemp": frozenset({"-p", "--tmpdir"}),
    "csplit": frozenset({"-f", "--prefix"}),
    "pv": frozenset({"-o", "--output"}),
}


def _mutation_operands(
    head_base: str, tokens: list[str]
) -> tuple[list[str], str | None, bool]:
    """Extract the non-flag path operands of a mutation head.

    Returns ``(operands, target_dir, ok)``. ``ok`` is False when a
    value-consuming flag is dangling (no value) — caller must fail closed.
    ``target_dir`` is the ``-t``/``--target-directory`` value when present (and
    relevant to ``head_base``), else ``None``; when set, EVERY operand is a
    source and ``target_dir`` is the sole write destination. ``--`` ends option
    parsing; everything after is a literal operand. Flags (``-r``, ``-f``,
    ``--``, glued ``-rf``) are skipped; value-flags (``-s 0``, ``-m 600``,
    ``--mode=600``) consume their value. Quoted operands are de-quoted by shlex
    already.
    """
    value_flags = _MUTATION_VALUE_FLAGS.get(head_base, frozenset())
    track_target = head_base in ("cp", "mv", "install", "ln")
    # Per-head flags whose VALUE is the write destination (mktemp -p, csplit -f,
    # pv -o). Captured into the same ``target_dir`` slot for the caller to lock.
    dest_value_flags = _DEST_VALUE_FLAGS.get(head_base, frozenset())
    operands: list[str] = []
    target_dir: str | None = None
    rest = tokens[1:]
    i = 0
    end_of_opts = False
    while i < len(rest):
        tok = rest[i]
        if end_of_opts:
            operands.append(tok)
            i += 1
            continue
        if tok == "--":
            end_of_opts = True
            i += 1
            continue
        if tok.startswith("-") and tok != "-":
            # `--target-directory=/etc` style carries its own value inline.
            if "=" in tok and tok.startswith("--"):
                name, _, val = tok.partition("=")
                if track_target and name in _TARGET_DIR_FLAGS:
                    target_dir = val
                elif name in dest_value_flags:
                    target_dir = val
                i += 1
                continue
            if tok in value_flags:
                # Consumes the next token as its value.
                if i + 1 >= len(rest):
                    return operands, None, False  # dangling value-flag → fail
                if track_target and tok in _TARGET_DIR_FLAGS:
                    target_dir = rest[i + 1]
                elif tok in dest_value_flags:
                    target_dir = rest[i + 1]
                i += 2
                continue
            # Glued short value flag carrying its value inline: `-p/etc` (mktemp),
            # `-f/etc/out` (csplit), `-o/etc/x` (pv), `-t/etc` (cp/mv/ln target).
            # Without this the glued remainder was swallowed by the plain-flag
            # fall-through and the out-of-scope destination never scope-checked.
            if not tok.startswith("--") and len(tok) > 2:
                short = tok[:2]
                glued = tok[2:]
                if track_target and short in _TARGET_DIR_FLAGS:
                    target_dir = glued
                    i += 1
                    continue
                if short in dest_value_flags:
                    target_dir = glued
                    i += 1
                    continue
                if short in value_flags:
                    # Other glued value flags (e.g. `-b10`) carry their own value
                    # inline — just consume the token.
                    i += 1
                    continue
            # Plain flag (bundled short flags like `-rf`, or `-s`/`-f`/`-a`).
            i += 1
            continue
        operands.append(tok)
        i += 1
    return operands, target_dir, True


def _adjudicate_mutation_head(
    head_base: str,
    tokens: list[str],
    *,
    project_dir: Path,
    target_root: Path | None = None,
) -> Decision | None:
    """Adjudicate a mutation-head segment: secret-source floor + write-target lock.

    Routes destination operand(s) through :func:`_adjudicate_write_target`
    (anti-tamper + secret + scope) and SOURCE operands through the secret-read
    floor — identical to Write/Edit/`>` and `cat` respectively. Returns a deny
    Decision, or ``None`` if every operand is in-scope/non-secret. FAIL CLOSED:
    unparseable operands → deny.
    """
    operands, target_dir, ok = _mutation_operands(head_base, tokens)
    if not ok:
        return _deny(
            f"mutation command with unparseable operands: {head_base}",
            "write-out-of-scope",
        )

    # Partition operands into (sources, destinations) per head semantics.
    sources: list[str] = []
    dests: list[str] = []
    if head_base in ("cp", "mv", "install"):
        if target_dir is not None:
            # `cp -t DIR SRC...` — DIR is the sole write destination and EVERY
            # positional operand is a source. The target dir was previously
            # consumed as an opaque value-flag and NEVER scope-checked, so an
            # out-of-scope `-t /etc/cron.d` slipped past. Scope-lock it now.
            if not operands:
                # `cp -t DIR` with no sources is malformed → fail closed.
                return _deny(
                    f"mutation command with too few operands: {head_base}",
                    "write-out-of-scope",
                )
            dests = [target_dir]
            sources = operands
        else:
            # `cp SRC... DST` — last operand is the destination, rest sources.
            if len(operands) < 2:
                # Single/zero operand for a copy/move is malformed → fail closed.
                return _deny(
                    f"mutation command with too few operands: {head_base}",
                    "write-out-of-scope",
                )
            dests = [operands[-1]]
            sources = operands[:-1]
    elif head_base == "ln":
        # `ln [-s] TARGET LINK` (or `ln TARGET` defaulting link to basename in
        # cwd). TARGET is the secret-source (symlink laundering: `ln -s
        # /etc/passwd /tmp/leak`); LINK is the write target. With `-t DIR`
        # (`ln -t /etc README.md`) DIR is the sole write destination and EVERY
        # positional operand is a source — the target dir was previously consumed
        # as an opaque value-flag and NEVER scope-checked.
        if target_dir is not None:
            if not operands:
                return _deny(
                    "mutation command with too few operands: ln", "write-out-of-scope"
                )
            dests = [target_dir]
            sources = operands
        else:
            if not operands:
                return _deny("ln with no operands", "write-out-of-scope")
            sources = [operands[0]]
            dests = operands[1:] if len(operands) > 1 else []
    elif head_base == "tee":
        # `tee FILE...` — every operand is a write destination (stdin → files).
        dests = operands
    elif head_base in ("truncate", "touch", "mkdir"):
        # All operands are write/create targets; no source operand.
        dests = operands
    elif head_base == "mktemp":
        # `mktemp -p DIR XXXXXX` — DIR (target_dir) is the write directory; with
        # no `-p`, the template POSITIONAL is the write path (`mktemp /etc/tmpX`).
        if target_dir is not None:
            dests = [target_dir]
        elif operands:
            dests = [operands[-1]]
        else:
            dests = []
    elif head_base == "split":
        # `split [OPTS] INPUT PREFIX` — the LAST positional is the output prefix
        # (write dest); a preceding positional is the input source.
        if not operands:
            return _deny(
                "mutation command with too few operands: split", "write-out-of-scope"
            )
        dests = [operands[-1]]
        sources = operands[:-1]
    elif head_base == "csplit":
        # `csplit -f PREFIX INPUT PATTERNS...` — `-f` value (target_dir) is the
        # output prefix (write dest). The FIRST positional is the INPUT file
        # (read-exfil vector: `csplit /etc/passwd 10`); remaining positionals are
        # split patterns (`10`, `/re/`) and are NOT paths.
        if target_dir is not None:
            dests = [target_dir]
        else:
            dests = []
        if operands:
            sources = [operands[0]]
    elif head_base == "pv":
        # `pv -o FILE INPUT` — `-o` value (target_dir) is the write dest; the
        # positional is the input source.
        if target_dir is not None:
            dests = [target_dir]
        sources = operands
    else:  # pragma: no cover — defensive
        dests = operands

    # Secret/scope floor on SOURCE operands (e.g. `cp ~/.ssh/id_rsa /tmp/x`,
    # `ln -s /etc/passwd /tmp/leak`). Reading an out-of-scope or secret file via
    # a mutation head is the SAME exfiltration vector as `cat`, so the source is
    # routed through the identical :func:`_path_is_secret_or_out_of_scope` check
    # (a symlink target out-of-scope is laundering). A relative source anchors
    # at project_dir (the Bash cwd), matching the redirect handler.
    for src in sources:
        cand = _strip_quotes(src)
        if not cand:
            continue
        blocked, reason, category = _path_is_secret_or_out_of_scope(
            _anchor_to_project(cand, project_dir),
            project_dir=project_dir,
            target_dir=target_root,
        )
        if blocked:
            return _deny(f"{reason} (via {head_base})", category)
        # Also catch a bare-basename secret source (`cp id_rsa /tmp/x`).
        if _SECRET_BASENAME_RE.match(cand):
            return _deny(
                f"secret source basename: {cand} (via {head_base})", "secret-read"
            )

    # Write-target lock on DESTINATION operands.
    for dst in dests:
        cand = _strip_quotes(dst)
        if not cand:
            # An empty/blank destination is ambiguous → fail closed.
            return _deny(
                f"mutation command with empty destination: {head_base}",
                "write-out-of-scope",
            )
        resolved = _anchor_to_project(cand, project_dir)
        verdict = _adjudicate_write_target(
            resolved, project_dir=project_dir, target_dir=target_root
        )
        if verdict.permission == "deny":
            # Re-attribute against the original spelling for a clearer reason.
            return _deny(verdict.reason.replace(resolved, cand), verdict.category)
    return None


def _anchor_to_project(path: str, project_dir: Path) -> str:
    """Anchor a RELATIVE bash operand at ``project_dir`` (the bash cwd).

    Absolute / ``~`` / ``$VAR`` paths are returned expanded as-is; a relative
    spelling is joined onto ``project_dir`` so the scope check resolves against
    the mission dir rather than the governor process's cwd (mirrors the
    redirect-target handling).
    """
    expanded = os.path.expanduser(os.path.expandvars(path))
    if os.path.isabs(expanded):
        return expanded
    return os.path.join(str(project_dir), expanded)


# `env`'s OWN options (consumed by env, NOT part of the inner command). Boolean
# flags take no value; value flags consume the following token (or carry it
# inline as `--flag=val` / `-uVAL`). `-S`/`--split-string` is an arbitrary-code
# surface (env parses+execs a single string) and is denied, not peeled.
_ENV_BOOL_FLAGS = frozenset(
    {"-i", "--ignore-environment", "-0", "--null", "-v", "--debug"}
)
_ENV_VALUE_FLAGS = frozenset({"-u", "--unset", "-C", "--chdir"})


def _peel_env_flags(rest: list[str]) -> tuple[list[str], Decision | None]:
    """Strip a leading ``env`` invocation's VAR=val assignments AND own options.

    Returns ``(remaining_tokens, deny_or_None)``. The remaining tokens begin at
    the REAL inner command so the peel loop re-adjudicates it (e.g. ``env -i
    setsid rm -rf /`` → ``setsid rm -rf /`` → floor). ``-S``/``--split-string``
    is denied (arbitrary-code surface), not peeled.
    """
    i = 0
    n = len(rest)
    while i < n:
        tok = rest[i]
        # Leading VAR=val environment assignments.
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1
            continue
        if not tok.startswith("-") or tok == "-":
            break  # reached the inner command head
        # `-S`/`--split-string` (and glued `-S...` / `--split-string=...`): env
        # parses+execs an arbitrary command string — deny outright.
        if (
            tok in ("-S", "--split-string")
            or tok.startswith("--split-string=")
            or (tok.startswith("-S") and len(tok) > 2)
        ):
            return rest[i:], _deny("env -S arbitrary-code surface", "bash-exec-runner")
        # Long value flag with inline value: `--unset=PATH`, `--chdir=/x`.
        if tok.startswith("--") and "=" in tok:
            name = tok.partition("=")[0]
            if name in _ENV_VALUE_FLAGS:
                i += 1
                continue
            # Unknown long flag — skip it (boolean-ish) and keep peeling.
            i += 1
            continue
        # Short flag bundle (`-i`, `-iu PATH`, `-iuPATH`, `-ui`, `-iC /etc`).
        # Decompose char-by-char with the standard GETOPT rule (same as tar C2):
        # boolean letters are consumed in place; when a VALUE letter (`u`/`C`) is
        # hit, the REST of the bundle is its inline value (regardless of whether
        # those chars look like option letters) and scanning stops — so `-ui` is
        # `-u i` (value `i`), drawing ZERO positionals; only a value letter that
        # is the LAST char of the bundle draws ONE following positional token.
        # `S` anywhere is the split-string arbitrary-code surface → deny.
        chars = tok[1:]
        if "S" in chars:
            return rest[i:], _deny("env -S arbitrary-code surface", "bash-exec-runner")
        tail_value = False
        k = 0
        while k < len(chars):
            ch = chars[k]
            if ("-" + ch) in _ENV_VALUE_FLAGS:
                if k + 1 < len(chars):
                    # Inline value: the rest of the bundle IS the value. Stop.
                    break
                # Value letter is the last char → its value is the next token.
                tail_value = True
            k += 1
        # Consume the flag token, plus ONE positional iff a tail value letter
        # draws its value from the following token.
        i += 2 if tail_value else 1
    return rest[i:], None


def _adjudicate_segment(
    segment: str, *, project_dir: Path, target_dir: Path | None = None
) -> Decision | None:
    """Adjudicate one Bash segment. Return a deny Decision or None (segment ok).

    ``None`` means "this segment raised no objection"; the caller treats an
    all-``None`` command as allow.
    """
    # NB: command/process substitution AND fork-bomb/function-definition
    # detection both live ONCE in _decide_bash, run on the FULL command before
    # segmentation. The segmenter splits on `(`/`{` (to defeat subshell/brace
    # head-hiding), which would shatter `$(`/`<(` and `name(){ ... }`, so those
    # checks must precede segmentation and are intentionally NOT duplicated here.

    try:
        tokens = shlex.split(segment)
    except ValueError as exc:
        return _deny(f"unparseable segment ({exc}): {segment!r}", "bash-parse-error")

    # Adjudicate write-redirect (`>`/`>>`) targets for destructiveness, then
    # drop ALL redirect tokens (incl. benign `2>/dev/null`, `<input`) so they
    # are not mistaken for path operands of the head command.
    redirect_verdict, tokens = _handle_redirects(
        tokens, project_dir=project_dir, target_dir=target_dir
    )
    if redirect_verdict is not None:
        return redirect_verdict

    if not tokens:
        return None

    # Peel leading shell-keyword prefixes (`exec`/`command`/`builtin`/`time`/
    # `!`/...), ``VAR=val`` environment assignments, and wrapper heads
    # (env/nohup/timeout/nice/...), iterating until the head stabilises. Each of
    # these can precede (and run) the REAL command, so without peeling them
    # `time python3 -c 1`, `exec python3 -c 1`, `X=1 find . -delete`, or
    # `env X=1 time python3 ...` would slip past the head/flag denylists.
    # Keyword/wrapper matching is case-sensitive (exact shell builtins/keywords);
    # command-name case-folding happens at head_base below.
    changed = True
    while changed and tokens:
        changed = False
        while tokens and tokens[0] in _KEYWORD_PREFIXES:
            tokens = tokens[1:]
            changed = True
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens = tokens[1:]
            changed = True
        while tokens and tokens[0] in _WRAPPER_HEADS:
            head = tokens[0]
            rest = tokens[1:]
            # `env VAR=val cmd ...` — drop leading VAR=val assignments AND env's
            # OWN options (else `env -i rm -rf /` left `-i` as the head → ALLOW).
            if head == "env":
                rest, env_deny = _peel_env_flags(rest)
                if env_deny is not None:
                    return env_deny
            # `timeout 5 cmd ...` / `nice -n 5 cmd` / `watch -n 5 cmd` — drop the
            # wrapper's own args until a token that is not a flag/number. `setsid`
            # takes no args of its own (like nohup) so it peels with no skipping.
            elif head in ("timeout", "nice", "ionice", "stdbuf", "watch"):
                j = 0
                while j < len(rest) and (
                    rest[j].startswith("-") or rest[j].replace(".", "").isdigit()
                ):
                    j += 1
                rest = rest[j:]
            tokens = rest
            changed = True
    if not tokens:
        return None

    head = tokens[0]
    head_base = os.path.basename(head).lower()

    # --- Non-overridable floors first (so the override step can short-circuit) ---
    if _is_root_destructive(tokens):
        return _deny(f"root-destructive command: {segment!r}", "bash-root-destructive")
    if head_base in _PRIVILEGE_HEADS:
        return _deny(f"privilege escalation: {head_base}", "bash-privilege")

    # --- source / . builtins: sourcing a file executes its contents ---
    if head_base in ("source", ".") and len(tokens) > 1:
        return _deny(f"source/dot file execution: {head_base}", "bash-interpreter")

    # --- Interpreter heads / shells with -c / eval ---
    if head_base in _INTERPRETER_HEADS:
        return _deny(f"interpreter head: {head_base}", "bash-interpreter")
    if head_base in _DASH_C_SHELLS and len(tokens) > 1:
        # Any shell given an argument runs arbitrary code: `sh -c '...'`,
        # `bash script.sh`, etc. A bare interactive `sh` (no args) is harmless.
        # Reason names the HEAD only (never the untrusted segment), so the audit
        # log can retain this reason without echoing raw input.
        return _deny(f"shell code-exec: {head_base}", "bash-interpreter")
    # `uv run <interpreter/test>` is an interpreter escape.
    if head_base == "uv" and len(tokens) > 1 and tokens[1] == "run":
        return _deny("uv run interpreter escape", "bash-interpreter")
    # bare pytest (missing test deps / arbitrary code via conftest).
    if head_base == "pytest":
        return _deny("bare pytest", "bash-interpreter")

    # --- Editors: arbitrary-code surface (shell-out + write-back) ---
    if head_base in _EDITOR_HEADS:
        return _deny(f"editor exec/write surface: {head_base}", "bash-editor")

    # --- Build / task runners + parallel: run arbitrary recipe commands ---
    if head_base in _EXEC_RUNNER_HEADS:
        return _deny(f"arbitrary-code runner: {head_base}", "bash-exec-runner")

    # --- Privilege via chmod/chown (any permission/owner change is denied;
    # this is stricter than the prior allowlist, never looser). ---
    if head_base in ("chmod", "chown"):
        # HEAD-only reason (never the untrusted segment): keeps this category
        # safe to retain verbatim in the durable audit log.
        return _deny(f"permission/owner change: {head_base}", "bash-privilege")

    # --- Installers / package managers ---
    if head_base in _INSTALLER_HEADS:
        # `pip install`, `npm install`/`npm i`, `brew install`, apt/yum/apk ...
        return _deny(f"installer/package-manager: {segment!r}", "bash-installer")

    # --- Raw network ---
    if head_base in _NETWORK_HEADS:
        return _deny(f"raw network tool: {head_base}", "bash-network")

    # --- Destructive non-interpreters ---
    if head_base in _DESTRUCTIVE_HEADS or head_base.startswith("mkfs"):
        return _deny(f"destructive command: {head_base}", "bash-destructive")
    if head_base == "rm":
        if _segment_destructive_rm(tokens, project_dir=project_dir):
            # HEAD-only reason (never the untrusted segment, which could carry a
            # secret target path): safe to retain in the durable audit log.
            return _deny(f"destructive fs command: {head_base}", "bash-destructive")
        # Plain (non-recursive/non-force) rm: still scope/secret-check each target.
        # A bounded in-scope `rm file.txt` is allowed, but `rm /etc/passwd`
        # (out-of-scope delete) and `rm /root/.ssh/id_rsa` (secret) must deny —
        # the same scope/secret floor every other path-touching head enforces.
        plain = _plain_rm_violation(tokens, project_dir=project_dir)
        if plain is not None:
            return plain

    # --- xargs runs an arbitrary command ---
    if head_base == "xargs":
        return _deny("xargs runs an arbitrary command", "bash-flag-exec")

    # --- Flag denylists on otherwise-allowed heads ---
    if head_base == "find":
        for a in tokens[1:]:
            if a in _FIND_BAD_FLAGS:
                return _deny(f"find dangerous flag {a}", "bash-flag-exec")
    if head_base == "rg":
        for a in tokens[1:]:
            if a in _RG_BAD_FLAGS or any(a.startswith(f + "=") for f in _RG_BAD_FLAGS):
                return _deny(f"rg dangerous flag {a}", "bash-flag-exec")
    if head_base == "fd":
        for a in tokens[1:]:
            if a in _FD_BAD_FLAGS:
                return _deny(f"fd dangerous flag {a}", "bash-flag-exec")
    if head_base == "git" and _segment_git_dangerous(tokens):
        return _deny(
            f"git config-injection/external-diff: {segment!r}", "bash-flag-exec"
        )
    if head_base == "tree":
        for a in tokens[1:]:
            if a in _TREE_BAD_FLAGS:
                return _deny(f"tree write flag {a}", "bash-flag-exec")
    if head_base in ("awk", "gawk") and _segment_awk_dangerous(tokens):
        return _deny(f"awk exec/io program: {segment!r}", "bash-flag-exec")
    if head_base == "sed" and _segment_sed_dangerous(tokens):
        return _deny(f"sed exec/write program: {segment!r}", "bash-flag-exec")
    if head_base == "tar" and _segment_tar_dangerous(tokens, project_dir=project_dir):
        return _deny(f"tar exec/out-of-scope: {segment!r}", "bash-flag-exec")

    # --- Mutation heads: write-target scope-lock + secret-source floor ---
    # cp/mv/tee/truncate/touch/mkdir/ln/install WRITE or CREATE their operands
    # (and cp/mv/ln/install READ a source). Route destinations through the same
    # write-target lock as `>`/Write/Edit and sources through the same
    # secret-read floor as `cat`, so exfil (`cp ~/.ssh/id_rsa /tmp/x`) and
    # out-of-scope writes (`tee -a /etc/sudoers`, `truncate -s 0 /etc/hosts`)
    # are denied. Fail-closed on unparseable operands.
    if head_base in _MUTATION_HEADS:
        mut = _adjudicate_mutation_head(
            head_base, tokens, project_dir=project_dir, target_root=target_dir
        )
        if mut is not None:
            return mut

    # --- Layer 3: secret/scope check on read-style heads ---
    if head_base in _READ_STYLE_HEADS:
        path_args = _read_style_path_args(head_base, tokens)
        for a in path_args:
            cand = _strip_quotes(a)
            if _looks_like_path(cand):
                # Full check: secret signatures + scope (canonicalized).
                blocked, reason, category = _path_is_secret_or_out_of_scope(
                    cand, project_dir=project_dir, target_dir=target_dir
                )
                if blocked:
                    return _deny(f"{reason} (via {head_base})", category)
            else:
                # Bare-basename secret read (no slash, no leading dot) — e.g.
                # `cat id_rsa`, `cat server.pem`, `cat credentials.json` — would
                # skip the path heuristic, so apply the SPECIFIC secret-signature
                # regex directly. The signatures (id_rsa/*.pem/*credentials*/
                # *.key/...) are narrow enough not to trip ordinary search terms
                # like `grep foo`. A literal `grep id_rsa README` is a rare
                # false-deny; since `secret-read` is a non-overridable floor it
                # is NOT recoverable via approval-override — accepted as the
                # fail-safe trade (rephrase the search to avoid the signature).
                if _SECRET_BASENAME_RE.match(cand):
                    return _deny(
                        f"secret basename: {cand} (via {head_base})", "secret-read"
                    )

    # No objection from this segment.
    return None


def _read_style_path_args(head_base: str, tokens: list[str]) -> list[str]:
    """Return the args of a read-style command that name actual file operands.

    For most heads this is every non-flag arg. For ``sed``/``awk``/``gawk`` the
    FIRST non-flag arg is the inline PROGRAM (e.g. ``/x/{p}``, ``{print $1}``),
    NOT a path — running the path/secret check on it causes a spurious
    out-of-scope deny (a work-stall). That program operand is skipped here
    UNLESS the program was instead supplied via ``-e``/``-f`` (in which case
    there is no inline-program operand and the first non-flag arg IS a file).
    The awk/sed exec-denials (``system(``/``BEGIN{``/``w``/``-f``) are enforced
    separately and are unaffected by this skip.
    """
    non_flags = [a for a in tokens[1:] if not a.startswith("-")]
    if head_base in ("sed", "awk", "gawk"):
        # If a program was given via -e/-f, there is no inline-program operand.
        has_program_flag = any(
            t in ("-e", "-f", "--expression", "--file")
            or t.startswith(("-e", "-f", "--expression=", "--file="))
            for t in tokens[1:]
        )
        if not has_program_flag and non_flags:
            # Drop the inline program (first non-flag arg); keep file operands.
            return non_flags[1:]
    return non_flags


def _looks_like_path(arg: str) -> bool:
    """Heuristic: does this non-flag arg name a filesystem path?

    Avoids flagging regex/search patterns (e.g. ``grep foo``) while still
    catching ``~/.ssh/id_rsa``, ``./a/../x``, ``/etc/shadow``, ``.env``.
    """
    if not arg:
        return False
    if arg.startswith(("~", "/", "./", "../")):
        return True
    if "/" in arg:
        return True
    # bare dotfiles like `.env`
    if arg.startswith(".") and arg not in (".", ".."):
        return True
    return False


# ---------------------------------------------------------------------------
# Operator allow-override
# ---------------------------------------------------------------------------

# A stored Bash pattern looks like ``Bash(<specifier>)``. The specifier is
# either ``<head>:*`` (the conservative head form emitted by
# approval_rules.extract_pattern) or a literal command prefix with ``*``
# wildcards (e.g. ``curl -s http://host/*``).
_BASH_PATTERN_RE = re.compile(r"^Bash\((?P<spec>.*)\)$")


def _load_override_patterns(project_dir: Path) -> list[str]:
    """Read stored operator-approved Bash specifiers from approval-rules.json.

    Returns a list of specifier strings (the inside of ``Bash(...)``). Never
    raises: a missing / malformed file yields ``[]`` (no overrides). Reading
    this file is the ONLY I/O :func:`decide` is permitted.
    """
    rules_file = project_dir / ".fleet" / "approval-rules.json"
    try:
        if not rules_file.exists():
            return []
        raw = json.loads(rules_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    specs: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pattern = entry.get("pattern")
        if not isinstance(pattern, str):
            continue
        m = _BASH_PATTERN_RE.match(pattern.strip())
        if m:
            specs.append(m.group("spec").strip())
    return specs


def _spec_matches_segment(spec: str, segment: str, head_base: str) -> bool:
    """Does a stored ``Bash(specifier)`` match THIS denied segment?

    Matching is scoped to the specific denied *segment* — never the full command
    — so an operator rule for one head cannot flip a different segment's deny
    (e.g. a ``Bash(ls:*)`` rule must not override the ``curl`` deny in
    ``ls && curl http://evil``).

    Two forms, consistent with ``approval_rules.extract_pattern``:
      * ``<head>:*`` — head-scoped: matches when the segment's head equals
        ``<head>`` (or its basename does).
      * literal prefix with ``*`` wildcards — glob-matched against the segment.
    """
    spec = spec.strip()
    if spec.endswith(":*"):
        want = spec[:-2].strip()
        want_base = os.path.basename(want)
        seg_head = segment.split()[0] if segment.split() else ""
        return head_base == want or head_base == want_base or seg_head == want
    # Wildcard glob form: translate ``*`` to ``.*`` and anchor against the segment.
    regex = "^" + ".*".join(re.escape(p) for p in spec.split("*")) + "$"
    return re.match(regex, segment.strip()) is not None


def _override_allows(segment: str, head_base: str, *, project_dir: Path) -> bool:
    """True if an operator-stored rule matches this (non-floor) denied segment."""
    for spec in _load_override_patterns(project_dir):
        if _spec_matches_segment(spec, segment, head_base):
            return True
    return False


# ---------------------------------------------------------------------------
# Bash dispatch
# ---------------------------------------------------------------------------


def _decide_bash(
    tool_input: dict, *, project_dir: Path, target_dir: Path | None = None
) -> Decision:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        # Nothing to run — allow (empty command is inert).
        return _allow("empty bash command", "bash-empty")

    # Governor canary sentinel (Task 2.3) — checked FIRST, before any allow
    # logic, so the sentinel is denied no matter how benign the surrounding
    # command looks (e.g. embedded inside a larger pipeline). A deny here proves
    # the governor is actually enforcing; the fleet-side self-test and the
    # agent-side runtime canary both rely on this exact verdict.
    if GOVERNOR_CANARY_TOKEN in command:
        return _deny(
            "governor canary — enforcement confirmed", GOVERNOR_CANARY_CATEGORY
        )

    # Apply bash LINE-CONTINUATION semantics FIRST: a backslash immediately
    # followed by a newline is spliced out (the token continues on the next
    # line). Without this, `r\<nl>m -rf /` would tokenize to head `r` and the
    # real `rm -rf /` would slip past the floors; `$\<nl>(` would also dodge the
    # substitution check. We remove ONLY the `\<nl>` pair (and the rare CRLF
    # `\<cr><nl>`); every OTHER escape (`\;`, `\ `, ...) is preserved verbatim,
    # matching bash. Done before substitution detection so a split `$(`/`<(`
    # cannot reassemble past that guard.
    command = re.sub(r"\\\r?\n", "", command)

    # Command/process substitution must be detected on the FULL command BEFORE
    # segmentation. The segmenter treats `(`/`{` as boundaries (to defeat
    # subshell/brace-group head-hiding), which would split `$(`/`<(` apart and
    # hide the substitution. shlex would not reveal these either — they smuggle
    # execution through an otherwise-allowed head (e.g. `echo $(python3 -c x)`).
    if _SUBSTITUTION_RE.search(command):
        return _deny(f"command/process substitution: {command!r}", "bash-substitution")

    # Fork-bomb / function-definition check on the FULL command, before
    # segmentation (segmentation would shatter `:(){ :|:& };:` into noise).
    if _FORKBOMB_RE.search(re.sub(r"\s+", " ", command)) or re.search(
        r"\b[\w:]+\s*\(\s*\)\s*\{", command
    ):
        # No command echo (would land verbatim in the durable audit log under
        # the head-only-safe bash-destructive category).
        return _deny("shell function-definition/fork-bomb", "bash-destructive")

    segments = _split_segments(command)
    if not segments:
        return _allow("no actionable segments", "bash-empty")

    for segment in segments:
        verdict = _adjudicate_segment(
            segment, project_dir=project_dir, target_dir=target_dir
        )
        if verdict is not None and verdict.permission == "deny":
            # Operator allow-override: flip a deny to allow UNLESS it is a
            # non-overridable floor category.
            if verdict.category in _FLOOR_CATEGORIES:
                return verdict
            try:
                tokens = shlex.split(segment)
            except ValueError:
                return verdict
            head_base = os.path.basename(tokens[0]).lower() if tokens else ""
            # Override is matched against the SPECIFIC denied segment, never the
            # full command — a rule for one head cannot flip another segment.
            if _override_allows(segment, head_base, project_dir=project_dir):
                return _allow(
                    f"operator approval-rule override (was {verdict.category}: "
                    f"{verdict.reason})",
                    "allow-override",
                )
            return verdict

    return _allow("bounded bash command", "bash-ok")


# ---------------------------------------------------------------------------
# Native tool dispatch
# ---------------------------------------------------------------------------


def _decide_read(
    tool_input: dict, *, project_dir: Path, target_dir: Path | None = None
) -> Decision:
    path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(path, str) or not path:
        # Grep/Glob may search the cwd with no explicit path — allow.
        return _allow("read-style tool, no path arg", "read-ok")
    blocked, reason, category = _path_is_secret_or_out_of_scope(
        path, project_dir=project_dir, target_dir=target_dir
    )
    if blocked:
        return _deny(reason, category)
    return _allow("read within scope", "read-ok")


def _decide_write(
    tool_input: dict, *, project_dir: Path, target_dir: Path | None = None
) -> Decision:
    path = (
        tool_input.get("file_path")
        or tool_input.get("notebook_path")
        or tool_input.get("path")
    )
    if not isinstance(path, str) or not path:
        return _deny("write tool missing target path", "write-out-of-scope")
    # Single write-target lock (anti-tamper + secret + scope).
    return _adjudicate_write_target(
        path, project_dir=project_dir, target_dir=target_dir
    )


# Committed WebFetch/WebSearch host allowlist. Resolved relative to THIS
# module's directory (megalodon_ui/governor/) so it is found both under the
# package import and under the bare-interpreter standalone shim (which puts
# that same directory on sys.path[0]). Module-level so tests can monkeypatch it.
_HOSTS_FILE = Path(__file__).resolve().parent / "governor-hosts.txt"


@lru_cache(maxsize=1)
def _load_host_allowlist() -> frozenset[str]:
    """Load the committed host allowlist from :data:`_HOSTS_FILE`.

    One host per line; blank lines and ``#`` comments ignored; each host is
    lowercased and stripped. Cached per-process (the file is committed and does
    not change at runtime). A MISSING / unreadable file yields the empty set —
    i.e. deny-all — preserving the conservative default. Tests clear the cache
    (``_load_host_allowlist.cache_clear()``) after monkeypatching ``_HOSTS_FILE``.
    """
    try:
        text = _HOSTS_FILE.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return frozenset()
    hosts: set[str] = set()
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        hosts.add(entry.lower())
    return frozenset(hosts)


def _host_allowed(host: str, hosts: frozenset[str] | set[str]) -> bool:
    """True if ``host`` is an exact match for, or a subdomain of, an allow entry.

    Subdomain matching is dot-anchored: ``raw.github.com`` is admitted by a
    ``github.com`` rule, but ``evilgithub.com`` is NOT (suffix check requires a
    leading ``.`` so it cannot be gamed by a sibling registrable domain).
    """
    host = host.lower()
    for allowed in hosts:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def decide_webfetch(url: str, *, allowlist: set[str] | None = None) -> Decision:
    """Adjudicate a WebFetch/WebSearch URL against a host allowlist.

    ``allowlist`` is the set of permitted hosts (exact or parent-domain). When
    ``None`` the committed :func:`_load_host_allowlist` set is used; a missing
    hosts file there yields deny-all. A URL whose hostname exactly matches, or
    is a subdomain of, an allowlisted host is allowed; everything else (incl. a
    malformed URL with no parseable host) is denied. Exposed as a standalone,
    testable hook.
    """
    hosts = allowlist if allowlist is not None else _load_host_allowlist()
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except (ValueError, AttributeError):
        host = ""
    if host and _host_allowed(host, hosts):
        return _allow(f"host on allowlist: {host}", "network-host-ok")
    return _deny(f"host not on allowlist: {host or url!r}", "network-host")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def decide(
    tool_name: str,
    tool_input: dict,
    *,
    project_dir: Path | str,
    lane: str,
    target_dir: Path | str | None = None,
) -> Decision:
    """Adjudicate a single PreToolUse event. Pure + fail-closed.

    Args:
        tool_name: The tool being invoked (e.g. ``"Bash"``, ``"Read"``).
        tool_input: The structured PreToolUse input dict for that tool.
        project_dir: The mission/run dir — the primary scope boundary.
        target_dir: Optional second read/write root (work-on-target mode): the
            external repo the fleet edits in place. When set, allowed roots are
            exactly ``{project_dir, target_dir, /tmp}``; floors are unaffected.
        lane: Opaque lane id. Reserved for the caller/audit layer (Task 1.2);
            the decision itself does NOT depend on or echo it. Kept in the
            signature so the hook can pass it through without a later breaking
            change.

    Returns:
        A :class:`Decision`. Any internal exception is caught and converted to
        a deny with category ``governor-error`` (fail closed).
    """
    try:
        pdir = Path(project_dir)
        tdir = Path(target_dir) if target_dir else None
        tin = tool_input if isinstance(tool_input, dict) else {}
        # `lane` is reserved for the caller/audit layer (Task 1.2); the decision
        # does not depend on it. Explicit no-op so the contract param is kept.
        del lane

        if tool_name in _INERT_TOOLS:
            return _allow(f"inert interaction tool: {tool_name}", "inert")

        if tool_name in _SPAWN_TOOLS:
            return _deny(
                f"subagent spawn could run outside the governor: {tool_name}",
                "subagent-spawn",
            )

        if tool_name == "Bash":
            return _decide_bash(tin, project_dir=pdir, target_dir=tdir)

        if tool_name in _READ_TOOLS:
            return _decide_read(tin, project_dir=pdir, target_dir=tdir)

        if tool_name in _WRITE_TOOLS:
            return _decide_write(tin, project_dir=pdir, target_dir=tdir)

        if tool_name in _NETWORK_TOOLS:
            url = tin.get("url") or tin.get("query") or ""
            # allowlist=None ⇒ use the committed governor-hosts.txt set (deny-all
            # if that file is missing). The set is cached per-process.
            return decide_webfetch(url, allowlist=None)

        # Inert/safe control tools that legitimately reach this branch: allow,
        # but keep the LOUD WARN so a genuinely-new tool is still surfaced for
        # classification. Checked BEFORE the unknown-tool deny.
        if tool_name in _INERT_ALLOWED_TOOLS:
            return _allow(
                f"WARN inert control tool {tool_name!r} — allowed by allowlist, "
                f"classify it",
                "inert-tool",
            )

        # Genuinely-unknown future tool: DENY (fail-closed). A fabricated MCP
        # tool carrying a destructive payload must not be allowed-by-default; the
        # safe control tools that reach here are carved out above.
        return _deny(
            f"unknown tool {tool_name!r} denied by default — classify it, then "
            f"add to an allowlist if safe",
            "unknown-tool-deny",
        )
    except Exception as exc:  # noqa: BLE001 — fail closed on ANY error
        return _deny(f"governor-error: {exc}", "governor-error")
