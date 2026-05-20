// playwright-audit.config.ts
// Minimal config for running test_dashboard_live_audit.spec.ts against the live
// mission server on port 8765. Omits the 8766 failure-modes webServer to avoid the
// socket-path-length error triggered by the deep fixture directory path.
// Use: ./scripts/run_e2e.sh --config ui/tests/e2e/playwright-audit.config.ts

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: /test_dashboard_live_audit\.spec\.ts$/,

  workers: 4,
  retries: 0,
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

  projects: [
    {
      name: 'chromium-default',
      use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:8765' },
    },
    {
      name: 'webkit-default',
      use: { ...devices['Desktop Safari'], baseURL: 'http://127.0.0.1:8765' },
    },
  ],

  webServer: {
    command: 'uv run --directory /Users/dave/Documents/Projects/megalodon --with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml python3 -m megalodon_ui --port 8765 --mission-dir $MEGALODON_MISSION_DIR_DEFAULT',
    url: 'http://127.0.0.1:8765/',
    reuseExistingServer: true,
    timeout: 30_000,
    env: {
      MEGALODON_MISSION_DIR_DEFAULT: process.env.MEGALODON_MISSION_DIR
        || `${__dirname}/../fixtures/fix-medium`,
    },
  },
});
