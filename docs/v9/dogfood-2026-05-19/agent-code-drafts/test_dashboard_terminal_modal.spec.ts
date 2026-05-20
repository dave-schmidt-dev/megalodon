// test_dashboard_terminal_modal.spec.ts
// S-HYBRID-DASHBOARD: "View terminal" button and modal Playwright contract.
// See: docs/v9/v9-3-HYBRID-DASHBOARD.md §5.2, §7
// Agent: agent-07c5 (LANE-D FRONTEND)
//
// BE endpoints (terminal_meta / terminal_stream) not yet implemented;
// tests verify button presence, accessibility, and disabled state.
// Tests for modal open/close are noted as pending BE; run against fix-medium fixture.

import { test, expect } from "@playwright/test";

const LANES = ["AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"];

// ── View terminal button contract ────────────────────────────────────────────

test.describe("S-HYBRID-DASHBOARD: View terminal button", () => {

  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
  });

  for (const lane of LANES) {
    test(`${lane}: View terminal button is present`, async ({ page }) => {
      const btn = page.locator(`[data-testid="action-view-terminal-${lane}"]`);
      await expect(btn).toBeVisible();
    });

    test(`${lane}: View terminal button has non-empty title`, async ({ page }) => {
      const btn = page.locator(`[data-testid="action-view-terminal-${lane}"]`);
      const title = await btn.getAttribute("title");
      expect(title).toBeTruthy();
      expect(title!.length).toBeGreaterThan(0);
    });

    test(`${lane}: View terminal button disabled when no stream log (fixture has no .fleet)`, async ({ page }) => {
      // The fix-medium fixture has no .fleet/ directory, so terminal_meta returns 404.
      // Buttons must remain disabled until BE confirms stream_log_exists=true.
      const btn = page.locator(`[data-testid="action-view-terminal-${lane}"]`);
      await expect(btn).toBeDisabled();
    });

    test(`${lane}: Show details button still present alongside View terminal`, async ({ page }) => {
      // Regression: buttonRow must contain both buttons; toggleBtn must not be lost.
      const toggleBtn = page.locator(`[data-testid="action-toggle-lane-${lane}"]`);
      await expect(toggleBtn).toBeVisible();
    });
  }

});

// ── Modal accessibility contract (requires enabled button — needs BE) ─────────
//
// These tests are skipped until terminal_meta endpoint is implemented by LANE-C.
// Remove the skip when BE endpoints are live.

test.describe("S-HYBRID-DASHBOARD: terminal modal accessibility", () => {

  test.skip("modal has role=dialog and aria-modal when opened (needs BE terminal_meta endpoint)", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    // Would click action-view-terminal-FRONTEND if button were enabled.
    const modal = page.locator('[data-testid="terminal-modal"]');
    await expect(modal).toHaveAttribute("role", "dialog");
    await expect(modal).toHaveAttribute("aria-modal", "true");
  });

  test.skip("Esc closes terminal modal (needs BE terminal_meta endpoint)", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    // Would open modal then press Escape.
    const modal = page.locator('[data-testid="terminal-modal"]');
    await expect(modal).not.toBeVisible();
  });

  test.skip("backdrop click closes terminal modal (needs BE terminal_meta endpoint)", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const modal = page.locator('[data-testid="terminal-modal"]');
    await expect(modal).not.toBeVisible();
  });

  test.skip("Copy buffer button present in modal (needs BE terminal_meta endpoint)", async ({ page }) => {
    const copyBtn = page.locator('[data-testid="terminal-modal-copy"]');
    await expect(copyBtn).toBeVisible();
  });

});
