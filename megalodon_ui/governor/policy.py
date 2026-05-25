"""Governor permission-policy engine (Task 1.1) — the security core.

A pure, import-light, fail-closed decision function:

    decide(tool_name, tool_input, *, project_dir, lane) -> Decision

Policy model: **allow-by-default + deny-matched-dangerous**, per tool family.
The net invariant is "strictly no looser than the prior --allowedTools system":
default to allow, deny only matched-dangerous vectors.

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

The interpreter/destructive head posture replicates (does not import) the intent
of ``megalodon_ui/harnesses/claude.py``'s ``_FORBIDDEN_HEAD_CMDS`` /
``_is_unbounded_tool`` so this module can become the single source of truth when
that logic is moved out of ``claude.py`` later.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
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
    path: str, *, project_dir: Path
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

    # Scope check: must be within project_dir or an allowed temp dir.
    if _is_within(canonical, str(project_dir)) or _is_temp_path(canonical):
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


def _adjudicate_write_target(path: str, *, project_dir: Path) -> Decision:
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

    # Scope: must be within project_dir or an allowed temp dir.
    if _is_within(canonical, str(project_dir)) or _is_temp_path(canonical):
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
    {"python", "python2", "python3", "eval", "ruby", "perl", "node"}
)
# Heads that are interpreters only with a code-exec flag (e.g. `bash -c`).
_DASH_C_SHELLS = frozenset({"bash", "sh", "zsh", "ksh", "dash"})

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

# Command-wrapper heads: they run a child command, so we adjudicate the wrapped
# command instead of the wrapper.
_WRAPPER_HEADS = frozenset({"nohup", "timeout", "env", "nice", "ionice", "stdbuf"})

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


def _segment_tar_dangerous(tokens: list[str]) -> bool:
    for a in tokens[1:]:
        if any(a == p or a.startswith(p + "=") for p in _TAR_BAD_FLAG_PREFIXES):
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


# Floor categories that an allow-override can NEVER lift.
_FLOOR_CATEGORIES = frozenset(
    {"bash-root-destructive", "bash-privilege", "secret-read"}
)


# Classic fork-bomb signature (collapse whitespace before matching).
_FORKBOMB_RE = re.compile(r":\(\)\s*\{.*:\|:.*\}")

# A token that is purely a redirection operator (e.g. `>`, `>>`, `2>`, `<`,
# `&>`, `1>>`) optionally glued to its target (e.g. `2>/dev/null`, `>out`).
_REDIRECT_TOKEN_RE = re.compile(r"^(?P<op>[0-9]*&?(?:>>|>|<<|<))(?P<tgt>.*)$")


def _handle_redirects(
    tokens: list[str], *, project_dir: Path
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
                    tgt, project_dir=project_dir
                )
                if verdict is not None:
                    return verdict, out
            i += 1
            continue
        out.append(tok)
        i += 1
    return None, out


def _adjudicate_write_redirect_target(
    target: str, *, project_dir: Path
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
    verdict = _adjudicate_write_target(resolved, project_dir=project_dir)
    if verdict.permission == "deny":
        # Re-attribute against the original spelling for a clearer reason.
        return _deny(verdict.reason.replace(resolved, tgt), verdict.category)
    return None


def _adjudicate_segment(segment: str, *, project_dir: Path) -> Decision | None:
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
    redirect_verdict, tokens = _handle_redirects(tokens, project_dir=project_dir)
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
            # `env VAR=val cmd ...` — drop leading VAR=val assignments.
            if head == "env":
                j = 0
                while j < len(rest) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", rest[j]):
                    j += 1
                rest = rest[j:]
            # `timeout 5 cmd ...` / `nice -n 5 cmd` — drop the wrapper's own args
            # until a token that is not a flag/number.
            elif head in ("timeout", "nice", "ionice", "stdbuf"):
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
    if head_base == "tar" and _segment_tar_dangerous(tokens):
        return _deny(f"tar exec flag: {segment!r}", "bash-flag-exec")

    # --- Layer 3: secret/scope check on read-style heads ---
    if head_base in _READ_STYLE_HEADS:
        path_args = _read_style_path_args(head_base, tokens)
        for a in path_args:
            cand = _strip_quotes(a)
            if _looks_like_path(cand):
                # Full check: secret signatures + scope (canonicalized).
                blocked, reason, category = _path_is_secret_or_out_of_scope(
                    cand, project_dir=project_dir
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


def _decide_bash(tool_input: dict, *, project_dir: Path) -> Decision:
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
        verdict = _adjudicate_segment(segment, project_dir=project_dir)
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


def _decide_read(tool_input: dict, *, project_dir: Path) -> Decision:
    path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(path, str) or not path:
        # Grep/Glob may search the cwd with no explicit path — allow.
        return _allow("read-style tool, no path arg", "read-ok")
    blocked, reason, category = _path_is_secret_or_out_of_scope(
        path, project_dir=project_dir
    )
    if blocked:
        return _deny(reason, category)
    return _allow("read within scope", "read-ok")


def _decide_write(tool_input: dict, *, project_dir: Path) -> Decision:
    path = (
        tool_input.get("file_path")
        or tool_input.get("notebook_path")
        or tool_input.get("path")
    )
    if not isinstance(path, str) or not path:
        return _deny("write tool missing target path", "write-out-of-scope")
    # Single write-target lock (anti-tamper + secret + scope).
    return _adjudicate_write_target(path, project_dir=project_dir)


def decide_webfetch(url: str, *, allowlist: set[str] | None = None) -> Decision:
    """Adjudicate a WebFetch/WebSearch URL against a host allowlist.

    ``allowlist`` defaults to ``None`` ⇒ deny-all (no committed hosts file yet;
    that arrives in a later task). Exposed as a standalone, testable hook.
    """
    hosts = allowlist or set()
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except (ValueError, AttributeError):
        host = ""
    if host and host in hosts:
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
) -> Decision:
    """Adjudicate a single PreToolUse event. Pure + fail-closed.

    Args:
        tool_name: The tool being invoked (e.g. ``"Bash"``, ``"Read"``).
        tool_input: The structured PreToolUse input dict for that tool.
        project_dir: The mission/run dir — the scope boundary.
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
            return _decide_bash(tin, project_dir=pdir)

        if tool_name in _READ_TOOLS:
            return _decide_read(tin, project_dir=pdir)

        if tool_name in _WRITE_TOOLS:
            return _decide_write(tin, project_dir=pdir)

        if tool_name in _NETWORK_TOOLS:
            url = tin.get("url") or tin.get("query") or ""
            return decide_webfetch(url, allowlist=None)

        # Genuinely-unknown future tool: allow, but flag for classification.
        return _allow(
            f"WARN unknown tool {tool_name!r} — allowed by default, classify it",
            "unknown-tool-warn",
        )
    except Exception as exc:  # noqa: BLE001 — fail closed on ANY error
        return _deny(f"governor-error: {exc}", "governor-error")
