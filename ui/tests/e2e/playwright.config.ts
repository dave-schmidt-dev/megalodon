// Playwright config for Megalodon UI E2E tests.
//
// Architecture (post 2026-05-19 cross-engine refactor):
//
// - 8 projects = {chromium, webkit} × {default, mutations, failure-modes, v92-dashboard}.
//   WebKit ≈ Safari engine; both engines exercise the same specs so Safari users
//   get coverage too.
//
// - Each project owns its own webServer on its own port, pointing at its own
//   fixture tmpdir. The tmpdirs are populated at config-load time (a fresh copy
//   of the source fixture under `ui/tests/fixtures/`) so no Playwright run ever
//   mutates the git-tracked fixture, and engines don't race on shared state.
//
// - Mutation-bearing projects (`*-mutations`, `*-v92-dashboard`) run with
//   `workers: 1` because their specs share mutable server state (STATUS.md
//   appends, fake-spawner `/__fake__/*` calls). Read-only projects run parallel.
//
// - `reuseExistingServer` is always false: the tmpdir path changes per process,
//   so reusing a webServer started in a previous run would serve stale fixture
//   content.
//
// Origins:
//   - Original spec: findings/agent-9265-E-P1-test-plan-2026-05-16T15-33Z.md §7
//   - Cross-engine + isolation rework: 2026-05-19 user-facing test reliability work

import { defineConfig, devices } from '@playwright/test';
import { cpSync, existsSync, mkdirSync, rmSync } from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

const FIXTURES_SRC = path.join(__dirname, '..', 'fixtures');
// `/tmp/m/<short>` keeps the resulting `.fleet/tmux.sock` path under macOS's
// 104-byte Unix-socket limit (megalodon_ui/__main__.py:106 enforces ≤100).
// Using os.tmpdir() on macOS yields `/var/folders/<uid>/T/...` which is ~50
// bytes before the project label is even appended — too tight in practice.
const TMPDIR_ROOT = '/tmp/m';

// Playwright loads this config in multiple processes: the main controller
// (once) plus each worker process. Only the controller should prepare fixtures
// — workers should just compute the path that the controller has already
// populated. `TEST_WORKER_INDEX` is set by Playwright on worker processes;
// its absence identifies the controller.
const IS_CONTROLLER = process.env.TEST_WORKER_INDEX === undefined;

// Prepare a fresh fixture copy under TMPDIR_ROOT for the named project.
// Source fixture is copied recursively from `ui/tests/fixtures/<source>` to
// `<TMPDIR_ROOT>/<projectLabel>`. Any prior tmpdir at that path is removed
// first so each Playwright invocation starts clean. Worker processes skip the
// rm+copy and just return the path — the controller has already prepared it.
function prepareFixture(source: string, projectLabel: string): string {
  const dest = path.join(TMPDIR_ROOT, projectLabel);
  if (!IS_CONTROLLER) return dest;
  const src = path.join(FIXTURES_SRC, source);
  if (!existsSync(src)) {
    throw new Error(`prepareFixture: source fixture missing at ${src}`);
  }
  if (existsSync(dest)) rmSync(dest, { recursive: true, force: true });
  mkdirSync(dest, { recursive: true });
  cpSync(src, dest, { recursive: true });
  return dest;
}

const fixtures = {
  defaultChromium: prepareFixture('fix-medium', 'def-c'),
  mutationsChromium: prepareFixture('fix-medium', 'mut-c'),
  failureModesChromium: prepareFixture('fix-medium-failure-modes', 'fail-c'),
  v92Chromium: prepareFixture('fix-medium-v92', 'v92-c'),
  defaultWebkit: prepareFixture('fix-medium', 'def-w'),
  mutationsWebkit: prepareFixture('fix-medium', 'mut-w'),
  failureModesWebkit: prepareFixture('fix-medium-failure-modes', 'fail-w'),
  v92Webkit: prepareFixture('fix-medium-v92', 'v92-w'),
};

// Port allocation: keep chromium on the original 8765-8767 plus 8768 for v92;
// webkit gets 8775-8778. Mismatched-port assertions in specs read from baseURL,
// not literal ports, so this is purely an operational convenience.
const ports = {
  defaultChromium: 8765,
  mutationsChromium: 8766,
  failureModesChromium: 8767,
  v92Chromium: 8768,
  defaultWebkit: 8775,
  mutationsWebkit: 8776,
  failureModesWebkit: 8777,
  v92Webkit: 8778,
};

const SERVER_CMD = (port: number, missionDir: string) =>
  `uv run --directory ${path.resolve(__dirname, '..', '..', '..')} ` +
  `--with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml ` +
  `python3 -m megalodon_ui --port ${port} --mission-dir ${missionDir}`;

const NON_MUTATION_DEFAULT_ENV = {
  // v9.0 specs only need request handlers + static rendering; bypass
  // FleetSpawner.start_all() so the server starts without a real tmux
  // claude-CLI environment.
  MEGALODON_LIFESPAN_TEST_MODE: '1',
  // Run the queue applier in-process so mutation specs (challenge / reclaim /
  // inject-task / phase-flip) see their POSTs propagate to TASKS.md and
  // STATUS.md without a separate applier daemon.
  MEGALODON_INPROCESS_APPLIER: '1',
};

const V92_ENV = {
  MEGALODON_LIFESPAN_TEST_MODE: '1',
  MEGALODON_V92_DASHBOARD: '1',
  MEGALODON_FAKE_SPAWNER: '1',
};

// Specs that belong to the v9.2 dashboard project (xterm dashboard + auth +
// followup). They use the fake spawner and exchange state via /__fake__/*.
const V92_SPEC_PATTERN =
  /(dashboard-loads|auth-redirect|streams-render|lane-exit-detected|followup-send-debounced|followup)\.spec\.ts$/;
const FAILURE_MODES_PATTERN = /test_failure_modes\.spec\.ts$/;
const MUTATIONS_PATTERN = /test_orchestrator_actions\.spec\.ts$/;
// Everything that doesn't match the above three patterns belongs to *-default.
const DEFAULT_IGNORE = [V92_SPEC_PATTERN, FAILURE_MODES_PATTERN, MUTATIONS_PATTERN];

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts/,

  // Global worker cap. Per-project `workers: 1` overrides apply to
  // mutation/v92 projects whose specs share server state. The read-only
  // projects (default, failure-modes) on both engines can saturate this pool.
  // M-series Macs can comfortably run 12 concurrent Node workers + 8 Python
  // webServers; CI bumps down to fit smaller runners.
  workers: process.env.CI ? 4 : 12,
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

  projects: [
    // ---- Chromium ----
    {
      name: 'chromium-default',
      testIgnore: DEFAULT_IGNORE,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.defaultChromium}` },
    },
    {
      name: 'chromium-mutations',
      testMatch: MUTATIONS_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.mutationsChromium}` },
    },
    {
      name: 'chromium-failure-modes',
      testMatch: FAILURE_MODES_PATTERN,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.failureModesChromium}` },
    },
    {
      name: 'chromium-v92-dashboard',
      testMatch: V92_SPEC_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.v92Chromium}` },
    },
    // ---- WebKit (Safari engine) ----
    {
      name: 'webkit-default',
      testIgnore: DEFAULT_IGNORE,
      use: { ...devices['Desktop Safari'], baseURL: `http://127.0.0.1:${ports.defaultWebkit}` },
    },
    {
      name: 'webkit-mutations',
      testMatch: MUTATIONS_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Safari'], baseURL: `http://127.0.0.1:${ports.mutationsWebkit}` },
    },
    {
      name: 'webkit-failure-modes',
      testMatch: FAILURE_MODES_PATTERN,
      use: { ...devices['Desktop Safari'], baseURL: `http://127.0.0.1:${ports.failureModesWebkit}` },
    },
    {
      name: 'webkit-v92-dashboard',
      testMatch: V92_SPEC_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Safari'], baseURL: `http://127.0.0.1:${ports.v92Webkit}` },
    },
  ],

  webServer: [
    { command: SERVER_CMD(ports.defaultChromium, fixtures.defaultChromium),
      url: `http://127.0.0.1:${ports.defaultChromium}/`,
      reuseExistingServer: false, timeout: 30_000, env: NON_MUTATION_DEFAULT_ENV },
    { command: SERVER_CMD(ports.mutationsChromium, fixtures.mutationsChromium),
      url: `http://127.0.0.1:${ports.mutationsChromium}/`,
      reuseExistingServer: false, timeout: 30_000, env: NON_MUTATION_DEFAULT_ENV },
    { command: SERVER_CMD(ports.failureModesChromium, fixtures.failureModesChromium),
      url: `http://127.0.0.1:${ports.failureModesChromium}/`,
      reuseExistingServer: false, timeout: 30_000, env: NON_MUTATION_DEFAULT_ENV },
    { command: SERVER_CMD(ports.v92Chromium, fixtures.v92Chromium),
      url: `http://127.0.0.1:${ports.v92Chromium}/`,
      reuseExistingServer: false, timeout: 30_000, env: V92_ENV },
    { command: SERVER_CMD(ports.defaultWebkit, fixtures.defaultWebkit),
      url: `http://127.0.0.1:${ports.defaultWebkit}/`,
      reuseExistingServer: false, timeout: 30_000, env: NON_MUTATION_DEFAULT_ENV },
    { command: SERVER_CMD(ports.mutationsWebkit, fixtures.mutationsWebkit),
      url: `http://127.0.0.1:${ports.mutationsWebkit}/`,
      reuseExistingServer: false, timeout: 30_000, env: NON_MUTATION_DEFAULT_ENV },
    { command: SERVER_CMD(ports.failureModesWebkit, fixtures.failureModesWebkit),
      url: `http://127.0.0.1:${ports.failureModesWebkit}/`,
      reuseExistingServer: false, timeout: 30_000, env: NON_MUTATION_DEFAULT_ENV },
    { command: SERVER_CMD(ports.v92Webkit, fixtures.v92Webkit),
      url: `http://127.0.0.1:${ports.v92Webkit}/`,
      reuseExistingServer: false, timeout: 30_000, env: V92_ENV },
  ],
});
