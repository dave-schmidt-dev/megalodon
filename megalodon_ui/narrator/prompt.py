"""Build the chat messages that ask a small local model to narrate a lane.

The narrative is ADVISORY (the deterministic board owns the load-bearing facts),
so the prompt optimizes for *format stability* and *faithfulness* over depth:
one short, plain sentence about what this agent is doing, drawn only from the
supplied event list. This is the exact prompt the benchmark scores candidate
models against, so changes here change both production and the benchmark.
"""

from __future__ import annotations

from .digest import SessionDigest, render_for_prompt

SYSTEM_PROMPT = (
    "You write a one-line status update describing what a software engineering "
    "agent is DOING, for an operator monitoring a fleet at a glance.\n"
    "\n"
    "Rules:\n"
    "- Output ONE sentence (two only if truly needed). No preamble, no markdown, "
    "no quotes, no lists.\n"
    "- Describe the agent's ACTIONS — the tools it ran and the files it read/wrote "
    "(e.g. 'Read launch-AUDIT.md and is checking mission state'). \n"
    "- CRITICAL: do NOT restate the CONTENTS of a file the agent read as if they "
    "were the agent's own status. If a file it read contains text like 'pending "
    "approval', 'all lanes unclaimed', or 'manual gate', that is file content the "
    "agent observed — NOT the agent's state. Report that it read the file, not "
    "what the file says.\n"
    "- Only say the agent is waiting / blocked / awaiting approval if a RESULT line "
    "EXPLICITLY shows a tool was rejected or errored. If no rejection or error "
    "appears, the agent is actively working — never invent a block or approval. A "
    "rejection is done by the operator, never by the agent itself.\n"
    "- Use ONLY the events provided; never invent a tool, file, action, or outcome. "
    "If a tool was issued but no RESULT follows, say the agent 'ran' or 'is running' "
    "it — do NOT claim it completed or what it returned.\n"
    "- Prefer the most recent activity. Be concrete (name the file/command), under "
    "~35 words.\n"
    "- Do not explain these rules or mention that you are summarizing."
)


def _user_turn(lane_name: str, digest_text: str) -> str:
    return (
        f"Agent lane: {lane_name}\n"
        f"Recent activity (oldest first):\n{digest_text}\n\n"
        f"Write the one-line status update for {lane_name}."
    )


# Few-shot demonstrations — small models learn the guardrails far better by
# example than by abstract rule. Each pair teaches one failure mode:
#   1. a rejected tool → say "waiting", attribute the rejection to the operator
#   2. file CONTENTS the agent read are NOT the agent's own state
#   3. a tool issued with no RESULT yet → "is running", not "completed"
_FEWSHOT: list[tuple[str, str, str]] = [
    (
        "DEPLOY",
        "- ASKED: Read deploy.md and ship the release.\n"
        "- SAID: I'll apply the production manifest.\n"
        "- TOOL: Bash(kubectl apply -f prod.yaml) — apply prod manifest\n"
        "- RESULT: The user doesn't want to proceed with this tool use. The tool "
        "use was rejected. STOP what you are doing and wait for the user.",
        "Tried to apply the production manifest, but the operator rejected the "
        "command; now paused awaiting direction.",
    ),
    (
        "DOCS",
        "- ASKED: Read guide.md and continue the migration.\n"
        "- TOOL: Read(guide.md)\n"
        "- RESULT: 1 # Migration guide 2 Status: BLOCKED pending legal review 3 "
        "Owner: unassigned\n"
        "- TOOL: Read(CHANGELOG.md)\n"
        "- RESULT: 1 # Changelog 2 v2.3 …",
        "Read guide.md and CHANGELOG.md to orient itself on the migration.",
    ),
    (
        "DATA",
        "- ASKED: Inspect the dataset before processing.\n"
        "- SAID: Let me see what's in the data directory.\n"
        "- TOOL: Bash(ls -la data/) — list dataset files",
        "Ran a listing of the data/ directory to inspect the dataset.",
    ),
]


def build_messages(lane_name: str, digest_text: str) -> list[dict]:
    """Return OpenAI chat messages (system + few-shot demos + the real turn).

    ``digest_text`` is the output of :func:`render_for_prompt`. ``lane_name`` is
    the human lane label (AUDIT, BACKEND, …) so the narrative can name the agent.
    The few-shot demos teach the faithfulness guardrails by example — essential
    for small models, which follow demonstrations far more reliably than rules.
    """
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for ex_lane, ex_digest, ex_out in _FEWSHOT:
        messages.append({"role": "user", "content": _user_turn(ex_lane, ex_digest)})
        messages.append({"role": "assistant", "content": ex_out})
    messages.append({"role": "user", "content": _user_turn(lane_name, digest_text)})
    return messages


def build_messages_for_digest(
    lane_name: str, digest: SessionDigest, **render_kw
) -> list[dict]:
    """Convenience: render a :class:`SessionDigest` and build messages in one step."""
    return build_messages(lane_name, render_for_prompt(digest, **render_kw))


# ---------------------------------------------------------------------------
# "Last" prompt — a SEPARATE, INDEPENDENT single-phrase call (OQ1).
#
# The board's "Last" column gets its own advisory phrase: one plain sentence
# about what the agent JUST COMPLETED (the closed task). This is a distinct
# prompt from the "Now" prompt above — each narrate call stays within the
# model's validated single-phrase competency. We do NOT make one call emit two
# phrases. The faithfulness/format discipline is identical (one sentence, no
# markdown/quotes/lists, only the provided events, never invent, ~35 words);
# only the framing differs: PAST-TENSE COMPLETION, grounded in the closed
# task's description plus the recent transcript.
# ---------------------------------------------------------------------------

LAST_SYSTEM_PROMPT = (
    "You write a one-line summary of what a software engineering agent JUST "
    "COMPLETED — the task it most recently finished — for an operator monitoring "
    "a fleet at a glance.\n"
    "\n"
    "Rules:\n"
    "- Output ONE past-tense sentence. No preamble, no markdown, no quotes, no "
    "lists.\n"
    "- Describe the COMPLETED work: what the agent finished, grounded in the "
    "stated task and the actions in the transcript (e.g. 'Finished wiring the "
    "permission banner after editing banner.js and running the tests').\n"
    "- CRITICAL: do NOT restate the CONTENTS of a file the agent read as if they "
    "were the agent's own outcome. File text the agent observed is NOT what the "
    "agent did.\n"
    "- Use ONLY the stated task and the events provided; never invent a tool, "
    "file, action, or outcome. If the transcript does not support a detail, omit "
    "it and stay with the stated task description.\n"
    "- Frame it as DONE / COMPLETED (past tense) — this is the closed task, not "
    "the current activity. Be concrete, under ~35 words.\n"
    "- Do not explain these rules or mention that you are summarizing."
)


def _last_user_turn(lane_name: str, last_task_desc: str, digest_text: str) -> str:
    return (
        f"Agent lane: {lane_name}\n"
        f"Just-completed task: {last_task_desc}\n"
        f"Recent activity (oldest first):\n{digest_text}\n\n"
        f"Write the one-line summary of what {lane_name} just completed."
    )


# Few-shot demos tuned for the "completed" framing — same guardrails as the Now
# demos, but the assistant output is past-tense completion of the stated task.
_LAST_FEWSHOT: list[tuple[str, str, str, str]] = [
    (
        "DOCS",
        "Migrate the onboarding guide to v2.",
        "- ASKED: Migrate the onboarding guide to v2.\n"
        "- TOOL: Edit(onboarding.md)\n"
        "- RESULT: applied\n"
        "- TOOL: Bash(make docs) — rebuild docs\n"
        "- RESULT: ok",
        "Completed the onboarding guide migration to v2 by editing onboarding.md "
        "and rebuilding the docs.",
    ),
    (
        "BUILD",
        "Cut the 2.3 release.",
        "- ASKED: Read release.md and continue.\n"
        "- TOOL: Read(release.md)\n"
        "- RESULT: 1 # Release 2 Status: pending sign-off\n"
        "- TOOL: Bash(git tag v2.3) — tag release\n"
        "- RESULT: ok",
        "Finished cutting the 2.3 release, tagging v2.3 after reviewing release.md.",
    ),
    (
        "DATA",
        "Validate the ingest dataset.",
        "- ASKED: Validate the ingest dataset.\n"
        "- TOOL: Bash(python validate.py data/) — validate dataset\n"
        "- RESULT: 0 errors",
        "Completed validation of the ingest dataset, which ran clean with no errors.",
    ),
]


def build_last_messages(
    lane_name: str, last_task_desc: str, digest_text: str
) -> list[dict]:
    """Return chat messages for the "Last" (just-completed) single-phrase prompt.

    A SEPARATE prompt from :func:`build_messages` — it asks for ONE past-tense
    sentence about the task the agent just COMPLETED, grounded in the closed
    task's description (``last_task_desc``) plus the recent transcript
    (``digest_text``, the output of :func:`render_for_prompt`). Mirrors the
    structure of :func:`build_messages` (system + few-shot demos + the real
    turn) so it is consistent and benchmarkable, and keeps each narrate call
    within the model's validated single-phrase competency.

    Args:
        lane_name: Human lane label (AUDIT, BACKEND, …).
        last_task_desc: Description of the just-completed (closed) task.
        digest_text: Rendered recent-activity digest from ``render_for_prompt``.
    """
    messages: list[dict] = [{"role": "system", "content": LAST_SYSTEM_PROMPT}]
    for ex_lane, ex_task, ex_digest, ex_out in _LAST_FEWSHOT:
        messages.append(
            {"role": "user", "content": _last_user_turn(ex_lane, ex_task, ex_digest)}
        )
        messages.append({"role": "assistant", "content": ex_out})
    messages.append(
        {
            "role": "user",
            "content": _last_user_turn(lane_name, last_task_desc, digest_text),
        }
    )
    return messages


def build_last_messages_for_digest(
    lane_name: str, last_task_desc: str, digest: SessionDigest, **render_kw
) -> list[dict]:
    """Convenience: render a :class:`SessionDigest` and build Last messages in one step."""
    return build_last_messages(
        lane_name, last_task_desc, render_for_prompt(digest, **render_kw)
    )
