"""Pattern extraction heuristic for Claude CLI --allowedTools patterns.

Converts operator-approved shell commands into conservative Claude CLI
``--allowedTools`` patterns of the form ``Bash(specifier)``.
"""

from __future__ import annotations

import re
import shlex
from urllib.parse import urlparse


# Shell metacharacters / constructs that make a command compound or ambiguous.
# We check for these BEFORE shlex.split so we catch them even inside contexts
# that shlex would normalise away.
_COMPOUND_RE = re.compile(
    r"""
    (
        &&                  # logical AND
      | \|\|                # logical OR
      | ;;                  # case statement terminator
      | \|                  # pipe (but NOT inside a word — raw scan is fine here)
      | (?<!\$)\(           # subshell ( — not $( variable expansion; we treat both as compound)
      | \$\(                # command substitution $(
      | `                   # backtick substitution
      | \bfor\b             # for loop
      | \bwhile\b           # while loop
      | \bif\b              # if statement
      | \bdo\b              # do block
      | \bdone\b            # done keyword
      | \bthen\b            # then keyword
    )
    """,
    re.VERBOSE,
)

# Redirects: >, <, >>  (not inside quotes — we check the raw string)
_REDIRECT_RE = re.compile(r"[><]")

# Semicolon used as command separator (not inside quotes)
_SEMICOLON_RE = re.compile(r";")


def _has_compound_structure(command: str) -> bool:
    """Return True if *command* looks like a compound/chained shell command.

    Checks are intentionally conservative: any ambiguous structure triggers
    refusal so we never emit an over-broad allowlist pattern.
    """
    # Use shlex to strip outer quotes, but scan the RAW string for shell ops
    # because shlex would swallow operators that are inside single quotes.
    raw = command

    if _COMPOUND_RE.search(raw):
        return True

    # Redirects: >, >>, <, 2>, etc.
    if _REDIRECT_RE.search(raw):
        return True

    # Bare semicolons (shell statement separator)
    if _SEMICOLON_RE.search(raw):
        return True

    return False


def _url_prefix(url: str) -> str | None:
    """Extract ``scheme://host:port/*`` from a URL.

    Returns None if the URL cannot be parsed or has no host.

    Examples:
        http://127.0.0.1:8765/foo/bar  →  http://127.0.0.1:8765/*
        https://example.com/api/v1     →  https://example.com/*
        http://localhost/path          →  http://localhost/*
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if not parsed.scheme or not parsed.netloc:
        return None

    return f"{parsed.scheme}://{parsed.netloc}/*"


def extract_pattern(command: str) -> str | None:
    """Convert a shell command into an ``--allowedTools`` pattern.

    Returns None for compound bash commands (chains, redirects, control flow)
    because the wildcard would be too broad to be meaningful.

    Args:
        command: A raw shell command string as it would be typed at a prompt.

    Returns:
        A ``Bash(specifier)`` pattern string, or ``None`` if the command is
        compound / ambiguous / empty.

    Examples::

        >>> extract_pattern("curl -s http://127.0.0.1:8765/foo")
        'Bash(curl -s http://127.0.0.1:8765/*)'

        >>> extract_pattern('curl -s "http://127.0.0.1:8765/foo"')
        'Bash(curl -s http://127.0.0.1:8765/*)'

        >>> extract_pattern("find . -name x")
        'Bash(find:*)'

        >>> extract_pattern("pytest scripts/tests/ -v")
        'Bash(pytest:*)'

        >>> extract_pattern("git status && npm test")  # compound → None

        >>> extract_pattern("ls | wc -l")  # pipe → None

        >>> extract_pattern("echo x > out.txt")  # redirect → None
    """
    if not command or not command.strip():
        return None

    # Compound / redirect check on raw string BEFORE any tokenisation.
    if _has_compound_structure(command):
        return None

    # Tokenise with shlex so quoted arguments are handled correctly.
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unterminated quote or similar parse error — refuse.
        return None

    if not tokens:
        return None

    program = tokens[0]

    # ------------------------------------------------------------------
    # Curl-aware path: preserve flags, extract URL prefix
    # ------------------------------------------------------------------
    if program == "curl":
        # Collect non-URL tokens that appear before the URL (i.e. flags).
        pre_url_flags: list[str] = []
        url_prefix_str: str | None = None

        for tok in tokens[1:]:
            # Strip surrounding quotes that shlex may have left (it typically
            # strips them, but be defensive).
            stripped = tok.strip("\"'")
            if stripped.startswith(("http://", "https://")):
                url_prefix_str = _url_prefix(stripped)
                # Stop: we don't care about tokens after the URL for the pattern.
                break
            else:
                pre_url_flags.append(tok)

        if url_prefix_str is not None:
            # Reconstruct the pattern including the flags before the URL.
            flags_part = (" ".join(pre_url_flags) + " ") if pre_url_flags else ""
            return f"Bash(curl {flags_part}{url_prefix_str})"

        # No URL found — fall through to generic handler.

    # ------------------------------------------------------------------
    # Generic: Bash(<program>:*)
    # ------------------------------------------------------------------
    return f"Bash({program}:*)"
