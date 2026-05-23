"""Narrative layer — turns a Claude session transcript into a short advisory digest.

Two responsibilities, split so the deterministic part has no model dependency:

- ``digest`` — parse a Claude session JSONL transcript into a clean, compact
  event list (``SessionDigest``) and render it for an LLM prompt. Pure, fast,
  no network. This is the load-bearing input layer; the deterministic status
  board can also read these facts directly.
- ``prompt`` — build the system/user messages that ask a small local model to
  phrase the digest into a 1-3 sentence advisory narrative.

The narrative itself is advisory only: the deterministic board carries the
load-bearing facts (state, current tool, approval), so a small model's
occasional fuzziness is tolerable.
"""

from .digest import DigestEvent, SessionDigest, parse_session, render_for_prompt

__all__ = ["DigestEvent", "SessionDigest", "parse_session", "render_for_prompt"]
