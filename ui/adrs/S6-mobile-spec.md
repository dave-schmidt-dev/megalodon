# ADR S-6 — Mobile-Responsive Layout Spec

- **Status:** Proposed (CROSS task; awaits P3-B `ui/SPEC.md` integration)
- **Authored by:** agent-aa79 (ARCHITECT, LANE-B)
- **Task:** TASKS.md `S-6`
- **UTC:** 2026-05-16T15:50Z
- **Supersedes:** N/A
- **Related:** P1-B §3 (page structure), P2.5-B §10 (ASCII task-ids), FRONTEND P2.5-D (viewport 1280–1920 commitment)

## Context

The orchestrator-console UI is a localhost-only single-mission tool (per MISSION.md "Out of scope: Multi-mission UI"; "Auth: localhost-only"). FRONTEND's plan-v2 explicitly commits to **desktop viewports 1280–1920px** for the primary product surface, including the 6-tab layout, finding-explorer side-by-side panel, and timeline swim-lanes.

But: an operator running a long mission (3–7 hours, per MISSION.md "Deliverable date") frequently wants to **glance at status from a phone** without opening their laptop. Read-only "is the mission still healthy?" use cases are mobile-suited; orchestrator actions (inject CHALLENGE, reclaim, post SIGNAL, flip phase) are not — those need keyboard input, deliberate confirmation, and visibility of the full lane state.

This ADR specifies what the UI does on viewports <1280px **without expanding the engineering scope of P3-D (FRONTEND build)**.

## Decision

**Tier the experience by viewport, not by feature flag.**

| Viewport | Tier | What works |
|---|---|---|
| ≥1280px | **Full** (FRONTEND P3-D primary commitment) | All 6 tabs, side-by-side panels, action modals, keyboard shortcuts |
| 768px–1279px | **Compact** | All 6 tabs; tab content stacks vertically (no side-by-side); action modals work but lose the "current state preview" pane |
| <768px | **Read-only Glance** | Dashboard tab only; lane grid renders as cards (one per lane); phase progress bar; recent activity list. **Action tab hidden** with explanatory message: "Mutations require a wider display — use a laptop or tablet." Other tabs accessible via menu, render in compact mode. |

**No JS framework switch.** HTMX + Alpine.js (per P2.5-B §5) handle this entirely via CSS media queries plus a `viewport-tier` data-attribute computed once on load (and on resize via `ResizeObserver`).

## CSS architecture

Three breakpoints, three media-query blocks. Use `clamp()` and CSS Grid `auto-fit` for the lane-card layout. Avoid JS-driven layout shifts; CSS resizing is enough.

```css
:root {
  --lane-card-min: 280px;
}

/* Full tier (≥1280) — default styles */
.lane-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; }
.tab-pane { display: grid; grid-template-columns: 320px 1fr; }

/* Compact tier (768–1279) */
@media (max-width: 1279px) {
  .lane-grid { grid-template-columns: 1fr 1fr; }
  .tab-pane { display: flex; flex-direction: column; }
  .tab-pane .sidebar { order: 2; }
}

/* Glance tier (<768) */
@media (max-width: 767px) {
  body[data-viewport-tier="glance"] .tab-bar { overflow-x: auto; }
  body[data-viewport-tier="glance"] .lane-grid {
    grid-template-columns: 1fr;
  }
  body[data-viewport-tier="glance"] .tab-pane[data-tab="actions"] {
    display: none;
  }
  body[data-viewport-tier="glance"] .actions-mobile-notice {
    display: block;
  }
  /* Findings/Timeline collapse multi-pane to single-column. */
  body[data-viewport-tier="glance"] .finding-detail {
    position: fixed; inset: 0; z-index: 10; background: var(--bg);
    /* full-screen view; back-arrow closes */
  }
}
```

## Specific component behaviors

### Dashboard tab
- **Lane grid**: Full = 3-column; Compact = 2-column; Glance = 1-column stacked cards. Each card shows lane code, agent ID, state badge, staleness chip (color-coded green/yellow/red), and last UTC.
- **Phase progress**: 4-segment progress bar (PLAN → CHALLENGE → BUILD → VERIFY). Glance tier renders progress horizontally but compact-labeled (e.g., `PLAN · CHL · BLD · VER` for ≤480px width).
- **Recent activity feed**: Virtualized list. Glance shows last 5 items only (vs. 20 in Full).

### Tasks tab
- Full: phase-grouped accordions, filter-chip bar on right.
- Compact: phase-grouped accordions, filter chips above the list.
- Glance: phase-grouped collapsed accordions (tap to expand). Filter chips become a select dropdown.

### Findings tab
- Full: sidebar filters + virtualized list + right pane with markdown render.
- Compact: filters at top, list below, click-to-route to a separate `/finding/<id>` URL that renders full-width.
- Glance: same as Compact, but pinned **back to findings list** affordance per RULE-3 in iOS swipe-back gesture conventions.

### Timeline tab
- Full: 2-axis swim lanes (lanes vertical, time horizontal).
- Compact: linear chronological list with lane-color chips.
- Glance: same linear list, smaller text, evidence-citation popovers become full-page modals.

### History tab
- Same as Tasks: phase-grouped, collapse on Glance.

### Actions tab
- Full / Compact: as designed in P2.5-B §B (phase-flip modal with `{from}` preview).
- **Glance: HIDDEN** with explanatory placeholder. Reasoning:
  - Phase-flip and reclaim are irreversible writes; small-screen confirmation modals are error-prone.
  - Inject CHALLENGE requires typing a finding-id reference; mobile keyboards make path:line citations tedious.
  - Operator can switch device — protocol cadence is 3 min so urgency is bounded.
  - Reduces P3-D scope: no mobile action UX to design/test.

## Touch targets

WCAG 2.1 §2.5.5 minimum touch target = 24×24 CSS pixels. We target **44×44** (Apple HIG, slightly stricter) for all interactive elements in Compact + Glance tiers:

- Lane card whole card is tappable to focus that lane.
- Tab buttons in the bottom-bar variant (Glance only) are 56px tall.
- Filter chips are 32px tall but with 12px vertical padding → 44px effective hit area.

## Accessibility carry-overs

FRONTEND P2.5-D committed to "per-glyph ARIA" — that commitment **applies across all tiers**. Glance tier additionally:
- Larger default font-size (17px → 19px) to honor mobile reading distance.
- `prefers-reduced-motion: reduce` disables phase-flip progress-bar animation entirely.
- Color-coded staleness chips have non-color affordance too (text label: `OK | STALE | DEAD`).

## Performance budget

- Initial HTML payload: ≤25 KB gzipped for Glance entry path.
- CSS: ≤15 KB gzipped, includes all three tiers (one stylesheet, media-queried).
- No mobile-specific JS bundle. HTMX (~10 KB) + Alpine.js (~7 KB) is the same on all tiers.
- Hero `GET /api/snapshot` returns ≤30 KB JSON for a typical mission (6 lanes × 6 phases × ~20 findings).

## What this ADR does NOT mandate

- Mobile-specific routes (we use the same URLs).
- Service worker / offline support (out of scope).
- Push notifications (out of scope; operator polls).
- Native app (explicitly avoided; this is HTML/CSS responsive).
- Touch gestures beyond default browser behavior (no custom swipe handlers).

## Test plan (handoff to LANE-E)

- Playwright fixtures should include three viewport profiles: `desktop` (1440×900), `tablet` (1024×768), `phone` (390×844 — iPhone 14).
- Smoke tests on each tier: load `/`, verify lane grid renders, verify tab navigation works.
- Glance tier negative test: verify Actions tab is `display:none` and the explanatory placeholder is present.
- A11y audit (axe-core) on Glance tier — same WCAG bar as Full tier.

## Consequences

**Positive:**
- Operator can glance from phone during a long mission without dedicated mobile engineering.
- FRONTEND's primary commitment (1280–1920px) is preserved; mobile is graceful degradation, not a new product surface.
- Accessibility baseline applies uniformly across tiers.

**Negative:**
- Three breakpoints to maintain in CSS — moderate cost.
- Glance-tier "Actions hidden" message is a UX paper-cut; some operators will want quick mobile actions and have to switch device.
- Tabbed-content stacking on Compact loses the "two-pane comparison" affordance for Findings — operators reviewing multiple findings simultaneously must open separate browser tabs.

**Mitigation for Negative:**
- If operator feedback during PHASE-VERIFY suggests mobile-action need, a future ADR (post-MVP) could specify a constrained "post SIGNAL" action accessible on Glance (limited to free-text + lane target — no phase-flip). Out of scope for this run.

## Open questions for P3-B integration

1. Should the Glance tier omit the Timeline tab entirely (potentially heavy SSE traffic on metered mobile)? **My recommendation:** keep but pause SSE on backgrounded tabs (Page Visibility API).
2. Should action-tab-hidden also defer SSE subscription to actions-related events? **My recommendation:** yes; SSE filters can be queried-param-driven.
3. Should we offer a "Pin to home screen" PWA manifest? **My recommendation:** no for v1 (out-of-scope creep); revisit if usage justifies.
