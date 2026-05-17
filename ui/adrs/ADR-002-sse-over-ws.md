# ADR-002 — Server-Sent Events over WebSockets for realtime push

- **Status:** Accepted
- **UTC:** 2026-05-16T15:54Z
- **Authored by:** agent-aa79 (ARCHITECT, P3-B)
- **Concordant with:** BACKEND P2.5-C §Δ12, FRONTEND P2.5-D (settle hook uses Last-Event-ID)

## Context

The UI must push file-system change events to the browser in near-realtime: status-row updates, finding additions, phase flips, history appends, signal emissions. The protocol's 3-minute tick cadence is too slow for an operator dashboard; sub-second push is the target.

Two realtime push mechanisms apply: WebSockets (bidirectional) and Server-Sent Events (server-to-client unidirectional over plain HTTP).

## Decision

**Server-Sent Events** via `sse-starlette`.

Event grammar in SPEC §4. Per-client bounded queue (100 events default) + drop-oldest + `lagging` event with `resync_urls`. Server-side coalescing of `status-change` events at 100ms granularity. Heartbeat every 15s; client timeout 37.5s.

File-watch path: `watchfiles` (Rust-backed FSEvents wrapper on macOS) with 100ms debounce, plus 2-second poll backstop to catch FSEvents coalescing/drops (BACKEND P1-C §1; my P2.5-B §C5 acceptance).

## Why not WebSockets

1. **Flow is 95% server-to-client.** Client-to-server traffic is POST actions (CSRF-protected, retryable, non-streaming). No legitimate need for client-to-server streaming.
2. **SSE auto-reconnects** with `Last-Event-ID` headers; WS reconnection is manual.
3. **Plain HTTP.** Works through every reverse proxy without special config; relevant if operator ever wants to put a tunnel between their phone and laptop.
4. **Simpler tests.** Playwright handles SSE via `page.waitForEvent` against EventSource; WS testing requires more setup.
5. **No HTTP/2 push concerns.** SSE works identically on HTTP/1.1 and HTTP/2.
6. **Same-process, single-origin.** FastAPI's `StaticFiles` mount + SSE share the uvicorn worker; no separate WS gateway needed.

WebSockets would only justify if the FE needed to stream events to the server beyond POSTs — not in v1 design (BACKEND P2.5-C §Δ12 confirms).

## Why FSEvents alone is insufficient (the 2s poll backstop)

`watchfiles` on macOS uses FSEvents under the hood. Apple's documentation states FSEvents may coalesce events; under burst load (phase flips: 20–30 events in <30s), community reports indicate drops are possible.

BACKEND C5 (P2-C→B): "discovered events are post-2s, so a real-time dashboard becomes a 2s-delayed one. Acceptable, but only if the poll backstop is specified." My P2.5-B §C5 accepted this.

Both signals merge into the same internal `FileChanged` event bus; re-emission is idempotent for the render pipeline (consumers dedupe by `(path, mtime, hash)`).

## Backpressure rationale

P1-B §6.4 estimated "2 events/min steady-state" — BACKEND C4 corrected this to ~12 events/min with phase-flip bursts of 20–30 events in <30s. Empirically I observed ~6 events from my single tick alone.

The bounded queue protects the operator's browser from a backlog if their tab is backgrounded (Page Visibility API) or if they hit a poor network mid-tunnel. Drop-oldest is the right policy: stale `status-change` events are not interesting (latest-wins); the `lagging` event tells the FE to resync the slices it cares about.

## Coalescing

100ms `status-change` coalescing is sub-perceptible to the operator (human reaction time ≈250ms). During the 30s post-`phase-flip` burst window, the window relaxes to 30ms to keep the dashboard's "everyone just claimed their next task" visualization responsive.

No coalescing for `signal-new`, `finding-added`, `claim-added/done` — each is a discrete event the operator may care about individually.

## Consequences

**Positive:**
- Simpler client (`EventSource` is built-in to browsers).
- Auto-reconnect for free.
- Trivial to mock in tests (write file → assert event arrives).
- Cache-friendly: SSE is just an HTTP response.

**Negative:**
- No client-to-server streaming. (Not needed in v1; SPEC §10 future-flag for v2 if multi-operator.)
- HTTP/1.1 connection-per-tab. With 1 operator and ≤6 tabs (one per page), this is fine.
- IE / old-browser support is N/A (operator uses modern browser; explicitly out of scope).

## References

- BACKEND P2-C→B C4, C5: `findings/agent-8318-C-P2-challenge-of-architect-...md:74-105`
- BACKEND P2.5-C §Δ6, §Δ12: `findings/agent-8318-C-P2.5-backend-plan-v2-...md`
- ARCHITECT P2.5-B §C: `findings/agent-aa79-B-P2.5-arch-plan-v2-...md`
