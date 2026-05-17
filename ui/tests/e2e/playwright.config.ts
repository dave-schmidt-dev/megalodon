// Playwright config for Megalodon UI E2E tests.
// Spec source: findings/agent-9265-E-P1-test-plan-2026-05-16T15-33Z.md §7.

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts/,

  // Per-test fixture mission dir avoids worker contention.
  // BACKEND must honor B.2 (mission_dir env var) per P1-E §6.
  workers: process.env.CI ? 2 : 4,
  retries: process.env.CI ? 2 : 0,
  timeout: 30_000,
  expect: { timeout: 5_000 },

  reporter: [
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
    ['list'],
  ],

  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
  },

  // REPAIR-MUTATIONS-E2E-4-FIXTURE-OVERRIDE: split into 2 projects + 2 webServers so
  // test_failure_modes.spec.ts runs against fix-medium-failure-modes fixture while
  // all other specs run against fix-medium. Per SIGNAL-FE-2 Option A.
  projects: [
    {
      name: 'chromium-default',
      testIgnore: /test_failure_modes\.spec\.ts$/,
      use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:8765' },
    },
    {
      name: 'chromium-failure-modes',
      testMatch: /test_failure_modes\.spec\.ts$/,
      use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:8766' },
    },
  ],

  webServer: [
    {
      command: 'uv run --directory /Users/dave/Documents/Projects/megalodon --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml python3 -m megalodon_ui --port 8765 --mission-dir $MEGALODON_MISSION_DIR_DEFAULT',
      url: 'http://127.0.0.1:8765/',
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      env: {
        MEGALODON_MISSION_DIR_DEFAULT: process.env.MEGALODON_MISSION_DIR
          || `${__dirname}/../fixtures/fix-medium`,
      },
    },
    {
      command: 'uv run --directory /Users/dave/Documents/Projects/megalodon --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml python3 -m megalodon_ui --port 8766 --mission-dir $MEGALODON_MISSION_DIR_FAILURE_MODES',
      url: 'http://127.0.0.1:8766/',
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      env: {
        MEGALODON_MISSION_DIR_FAILURE_MODES: process.env.MEGALODON_MISSION_DIR_FAILURE_MODES
          || `${__dirname}/../fixtures/fix-medium-failure-modes`,
      },
    },
  ],
});
