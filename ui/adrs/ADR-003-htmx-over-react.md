# ADR-003 — HTMX + Alpine.js over React/Vue for the frontend

- **Status:** Accepted
- **UTC:** 2026-05-16T15:54Z
- **Authored by:** agent-aa79 (ARCHITECT, P3-B)
- **Concordant with:** FRONTEND P2.5-D (no build step, structural-only test selectors)

## Context

The orchestrator-console UI is:
- **Single-operator, localhost-only.** No SEO concern. No CDN. No worldwide latency budget.
- **Six tabs**, each rendering filesystem-derived data with periodic SSE updates.
- **Read-heavy with discrete action endpoints.** Most interaction is "view"; mutations are explicit form submits.
- **Mission-bounded.** A session lives 3–7 hours and then archives.
- **Test-criticality is high.** Playwright must drive the UI reliably during PHASE-VERIFY (TEST P3-E).

## Decision

**Vanilla JS + HTMX + Alpine.js. No build step.**

- **Server-side render** initial HTML via Jinja2 (FastAPI/Starlette default).
- **HTMX** for SSE-driven DOM swaps (`hx-sse`, `hx-swap`) and POST action forms.
- **Alpine.js** for in-page reactive state (filter chips, modal toggles, dropdown).
- **Plain CSS** with custom properties; three media-query breakpoints per `ui/adrs/S6-mobile-spec.md`.
- **Zero build-time tooling.** No webpack, vite, esbuild, npm install. All assets served from `ui/static/` via FastAPI `StaticFiles`.

## Considered alternatives

1. **React + Vite + TanStack Query** — modern SPA stack.
2. **Vue 3 + Vite + Pinia** — modern SPA stack, similar to React.
3. **Next.js / Remix / SvelteKit** — meta-framework with SSR + hydration.
4. **Preact + HTM** — minimal SPA, no build step possible.
5. **HTMX + Alpine.js + Jinja2 SSR** (chosen).

## Why not React/Vue/Next

- **Build-step overhead.** A localhost tool that must work from `git clone → uv run` ought not require `npm install` + `npm run build`. Build steps fail; build steps drift; build steps add ~80MB of `node_modules` for a localhost utility.
- **Bundle size.** React + ReactDOM is ~120KB gzipped before any app code. HTMX (10KB) + Alpine (7KB) = 17KB. For a single-operator localhost tool, this isn't a network concern — it's an unnecessary parser/runtime cost during cold load.
- **Hydration/SSR complexity.** Next.js handles SSR well but introduces routing, data fetching, and component conventions far heavier than the use case warrants.
- **Test stability.** Virtual-DOM frameworks introduce timing layers (concurrent rendering, suspense, batching) that make Playwright tests flakier. FRONTEND P2.5-D committed to "structural-only test selectors" and "data-last-event-id settle hook" — these are easier when DOM is rendered server-side and updated by direct HTMX swaps.
- **Cognitive load for maintainers.** This codebase will be read by Claude sessions and humans. Server-side HTML + small JS islands is the most universally legible stack.

## Why not Preact + HTM

Closer to the right shape — small, no build step possible. But still client-rendered, which means:
- Initial paint blank until JS executes.
- Filter state lives in client store; loses on hard refresh unless serialized to URL.
- Harder to test SSR-rendered content directly.

HTMX retains the page as the source of truth; Preact retains the JS store as the source of truth. For a renderer of filesystem state, page-as-truth is the better mental model.

## Architecture sketch

```
GET /  (FastAPI)
  -> Jinja2 renders index.html with:
       - current snapshot data inlined (server fetch of /api/v1/snapshot)
       - <div hx-sse="connect:/api/v1/stream"> at root
       - per-tab partials registered for `hx-trigger="sse:status-change"`
  -> Browser:
       - Initial paint immediate (no JS needed for first render)
       - HTMX opens SSE
       - On `status-change`, HTMX swaps the affected lane row via `hx-target`
       - Alpine handles local state (which filters are active, which modal is open)

POST /api/v1/signal (form submit)
  -> HTMX intercepts via hx-post
  -> CSRF token from <meta name="csrf-token"> auto-included
  -> On 200, swap response HTML into a confirmation banner
  -> On 4xx, swap error message
```

## Specific commitments (from FRONTEND P2.5-D)

- **`data-testid="..."` attributes** on every interactive element + every Alpine-bound region. TEST drives by structural selectors only; never by text or visual position.
- **`data-last-event-id="<utc>"` settle hook** on the root container — Playwright waits for it to reach a target value to know the SSE event has applied.
- **Per-glyph ARIA** on status chips, severity badges, phase progress.
- **Viewport 1280–1920 primary; 768–1279 Compact; <768 Glance** per `ui/adrs/S6-mobile-spec.md`.

## Consequences

**Positive:**
- Zero build step. `git clone && uv run python ui/server.py` works.
- ~25KB initial payload. Trivial cold-load.
- Playwright tests are stable; no act/render/hydrate timing surprises.
- Easy to inspect: View Source shows the actual DOM.

**Negative:**
- No component library ecosystem (React's vast). Mitigation: limited UI surface (6 tabs, ~10 widget types); plain CSS suffices.
- Server-rendered means more server CPU per page navigation. Mitigation: localhost; not a real cost.
- HTMX's `hx-` attribute soup can clutter HTML. Mitigation: limited number of interactive elements; the soup stays bounded.

## References

- FRONTEND P2.5-D: `findings/agent-1371-D-P2.5-frontend-plan-v2-2026-05-16T15-45Z.md`
- ARCHITECT P1-B §5 (stack rec): `findings/agent-aa79-B-P1-arch-plan-2026-05-16T15-33Z.md`
- ARCHITECT P2.5-B §5 (confirmed): `findings/agent-aa79-B-P2.5-arch-plan-v2-2026-05-16T15-46Z.md`
