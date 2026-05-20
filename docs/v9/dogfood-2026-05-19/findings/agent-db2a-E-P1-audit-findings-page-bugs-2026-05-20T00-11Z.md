# LANE-E Audit Finding — Findings Page: Missing UI Elements
**Agent:** agent-db2a | **Lane:** LANE-E (TEST) | **Task:** P1-E
**UTC:** 2026-05-20T00-11Z | **Severity:** MAJOR
**Refs:** `ui/static/pages/findings.js`

## Summary

The findings page is missing interactive elements expected by the audit spec.
Two tests failed on both Chromium and WebKit (84 tests total, both browsers):

| Test | Line | Status | Both Browsers |
|------|------|--------|---------------|
| severity filter chip narrows the list | 101 | ✘ FAIL | ✓ |
| clicking a finding row opens preview panel | 89 | ✓ PASS | ✓ |
| findings page loads and renders at least one finding row | 81 | ✓ PASS | ✓ |

## Failures Detail

### BUG-FINDINGS-FILTER: `[data-testid="filter-severity-MAJOR"]` not found

**Test:** `severity filter chip narrows the list` (line 101)
**Selector:** `[data-testid="filter-severity-MAJOR"]`
**Expected:** A clickable filter chip element with this data-testid exists on `/findings`
**Actual:** Element not found → assertion fails in ~180ms (no such element)
**Root cause:** `findings.js` does not render per-severity filter chips.

```ts
// Current test (audit spec line 101-108):
await page.locator('[data-testid="filter-severity-MAJOR"]').click();
// Times out — element does not exist
```

The live findings list renders rows (✓ passing) but provides no severity filter UI.

## What Passes

- Finding rows render with `data-testid^="finding-row-"` (live fleet has 75+ findings)
- Clicking a row opens a preview panel (`data-testid="finding-preview"`) with markdown content

## Recommendations for LANE-D

1. Add severity filter chips to `findings.js` with `data-testid="filter-severity-{SEVERITY}"` for at least: `INFO`, `MAJOR`, `WARN`.
2. Wire click handler to filter `[data-testid^="finding-row-"]` by severity.
3. The filter should narrow (not empty) the list — at least MAJOR findings exist in the live fleet.

## Next steps

Waiting in signal `signals/LANE-E-to-LANE-D-2026-05-20T00-11Z.md`.
