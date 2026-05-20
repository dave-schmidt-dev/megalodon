"""Megalodon v9.1 — ClaudeAdapter.

Wraps the ``claude`` CLI (v2.1.133+) for non-interactive use.

Verified invocation shape (research doc 2026-05-17):
    claude --print --model <id> "<prompt>"
    claude --print --output-format stream-json --model <id> "<prompt>"

Auth: ANTHROPIC_API_KEY (or ``claude setup-token`` interactive flow — not our
concern here).  env_overlay is always empty because the key is expected to
already be in the caller's environment.

Session logs: ~/.claude/projects/<sanitized-cwd>/<uuid>.jsonl
  Sanitisation: strip leading slash, replace '/' with '-', collapse
  leading dashes.
"""

from __future__ import annotations

import json
import pathlib

from .base import Capabilities, Event, ModelSpec


class ClaudeAdapter:
    """Concrete HarnessAdapter for the Claude Code CLI."""

    name: str = "claude"
    default_model: str = "claude-opus-4-7"
    supports_autonomous_loop = True  # CR-4: only Claude supports autonomous loop in v9.1

    available_models: tuple[ModelSpec, ...] = (
        ModelSpec(id="claude-opus-4-7", aliases=("opus",), is_default=True),
        ModelSpec(id="claude-sonnet-4-6", aliases=("sonnet",)),
        ModelSpec(id="claude-haiku-4-5-20251001", aliases=("haiku",)),
        # Legacy models (research doc 2026-05-17)
        ModelSpec(id="claude-opus-4-6"),
        ModelSpec(id="claude-opus-4-5-20251101"),
        ModelSpec(id="claude-sonnet-4-5-20250929"),
    )

    # ------------------------------------------------------------------
    # build_argv
    # ------------------------------------------------------------------

    def build_argv(
        self,
        prompt_or_launch_path: str,
        *,
        model: str,
        cwd: pathlib.Path,
        session_id: str | None = None,
        output_format: str = "text",
        extra_env: dict[str, str] | None = None,
        live_repl: bool = False,
    ) -> tuple[list[str], dict[str, str]]:
        if live_repl:
            # --allowedTools policy for live_repl agents:
            #
            # AUTO-APPROVED (no operator prompt):
            #  * Claude file tools: Read / Edit / Write / Grep / Glob
            #  * /loop runtime + in-session task tools: ScheduleWakeup, Task*
            #  * Read-only project-workspace shell ops (ls/grep/rg/cat/head/
            #    tail/wc/echo/diff/stat/file/realpath/basename/dirname/pwd/
            #    tree/which/date/true/false). Operators authorize these by
            #    accepting the agent into the mission — read-from-workspace
            #    is a basic capability, not a per-command decision.
            #  * Read-only git (status/diff/log/show/branch/rev-parse/ls-files
            #    /config --get).
            #  * v9 protocol primitives (mkdir/rm/rmdir against claims/).
            #
            # PROMPTS THE OPERATOR (surfaces via dashboard permission banner):
            #  * python3 / uv / pytest / npx — runtime execution
            #  * find — has -exec arbitrary-command-execution capability
            #  * Bash compound `&&` / `|` / `;` shells
            #  * Network ops (curl / wget / ssh / scp)
            #  * Writes outside claims/findings/feedback (those go through
            #    the Write tool which IS auto-approved, scoped by caller)
            allowed = (
                # Claude-native tools
                "Read Edit Write Grep Glob "
                "ScheduleWakeup TaskCreate TaskUpdate TaskGet TaskList TaskOutput "
                # Read-only project-workspace shell ops
                "Bash(ls:*) Bash(grep:*) Bash(rg:*) Bash(cat:*) "
                "Bash(head:*) Bash(tail:*) Bash(wc:*) Bash(echo:*) "
                "Bash(diff:*) Bash(stat:*) Bash(file:*) Bash(realpath:*) "
                "Bash(basename:*) Bash(dirname:*) Bash(pwd:*) Bash(tree:*) "
                "Bash(which:*) Bash(date:*) Bash(true:*) Bash(false:*) "
                # Read-only git
                "Bash(git status*) Bash(git diff*) Bash(git log*) "
                "Bash(git show*) Bash(git branch*) Bash(git rev-parse*) "
                "Bash(git ls-files*) Bash(git config --get*) "
                # v9 protocol primitives (claims/ mutex)
                "Bash(mkdir claims/*) Bash(rm -rf claims/*) Bash(rmdir claims/*) "
                # v9.3 queue endpoints — agents call localhost-scoped curl to
                # route TASKS.md / STATUS.md / HISTORY.md mutations through
                # the in-process applier instead of direct file edits. Scope
                # is localhost ONLY (matches `127.0.0.1*` prefix), no
                # external network surface.
                "Bash(curl -s -b /tmp/*) Bash(curl -s -c /tmp/*) "
                "Bash(curl -s http://127.0.0.1*) "
                "Bash(curl -s -X POST http://127.0.0.1*) "
                "Bash(curl -s -X POST -H Content-Type:* http://127.0.0.1*) "
                # v9.3.3 test runners — TEST lane runs Playwright + pytest
                # constantly; BACKEND/FRONTEND verify their own changes per
                # the launch template's "run tests before claiming done" rule.
                # Operator explicitly authorized these at-launch (2026-05-19).
                #
                # NARROW SCOPE (operator chose option 1, 2026-05-19T19:11Z):
                #  * `Bash(pytest:*)`             — bare pytest
                #  * `Bash(uv run --with pytest*)` — uv invocations that DECLARE
                #    pytest in their --with deps. Matches the launch template's
                #    documented test command. Does NOT match `uv run --with
                #    arbitrary-pkg python -c "..."` — that still prompts.
                #  * `Bash(./scripts/run_e2e.sh*)` / `Bash(scripts/run_e2e.sh*)`
                #    — the project's playwright runner (checked-in script).
                #  * `Bash(npx playwright:*)`     — playwright via npx only.
                #  * `Bash(npm test*)` / `Bash(npm run test*)` — node test scripts.
                "Bash(pytest:*) Bash(uv run --with pytest*) "
                "Bash(./scripts/run_e2e.sh*) Bash(scripts/run_e2e.sh*) "
                "Bash(npx playwright:*) Bash(npm test*) Bash(npm run test*)"
            )
            return ["claude", "--model", model, "--allowedTools", allowed], {}
        argv = ["claude", "--print", "--model", model]
        if output_format == "stream-json":
            argv += ["--output-format", "stream-json"]
        argv.append(prompt_or_launch_path)
        return argv, {}

    # ------------------------------------------------------------------
    # build_followup_argv (P6.1) — adds --resume <prior_session_id>
    # ------------------------------------------------------------------

    def build_followup_argv(
        self,
        prompt: str,
        *,
        prior_session_id: str | None,
        model: str,
        cwd: pathlib.Path,
        output_format: str = "text",
        extra_env: dict[str, str] | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        argv = ["claude", "--print", "--model", model]
        if prior_session_id:
            argv += ["--resume", prior_session_id]
        if output_format == "stream-json":
            argv += ["--output-format", "stream-json"]
        argv.append(prompt)
        return argv, {}

    # ------------------------------------------------------------------
    # parse_stream_line
    # ------------------------------------------------------------------

    def parse_stream_line(self, line: str) -> Event | None:
        line = line.rstrip("\n")
        if not line.strip():
            return None
        if line.lstrip().startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                return Event(kind="text", text=line)
            # Extract text from common Claude stream-json shapes
            event_type = parsed.get("type", "")
            if event_type == "text":
                return Event(kind="text", text=parsed.get("text", ""), raw=parsed)
            if "content" in parsed:
                content = parsed["content"]
                text = content if isinstance(content, str) else ""
                return Event(kind="text", text=text, raw=parsed)
            # JSON but not a recognised text event — surface as system
            return Event(kind="system", text="", raw=parsed)
        # Plain text line
        return Event(kind="text", text=line)

    # ------------------------------------------------------------------
    # session_log_path
    # ------------------------------------------------------------------

    def session_log_path(
        self, cwd: pathlib.Path, session_id: str
    ) -> pathlib.Path | None:
        return self.session_log_dir(cwd) / f"{session_id}.jsonl"  # type: ignore[operator]

    def session_log_dir(self, cwd: pathlib.Path) -> pathlib.Path | None:
        # Sanitise: remove leading slash, replace '/' with '-', collapse
        # consecutive leading dashes produced by an absolute path like /tmp.
        raw = str(cwd).lstrip("/")
        sanitized = raw.replace("/", "-").lstrip("-") or "root"
        return pathlib.Path.home() / ".claude" / "projects" / sanitized

    # ------------------------------------------------------------------
    # auth / capabilities
    # ------------------------------------------------------------------

    def auth_env_keys(self) -> list[str]:
        return ["ANTHROPIC_API_KEY"]

    def supports(self) -> Capabilities:
        return Capabilities(
            supports_autonomous_loop=True,
            supports_session_resume=True,
            supports_stream_json=True,
            supports_tool_use=True,
        )
