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
  boardChromium: prepareFixture('fix-small', 'board-c'),
  // Phase-1 smoke: same 3-lane fix-small fixture, fake spawner enabled.
  gridSmokeChromium: prepareFixture('fix-small', 'smoke-c'),
  defaultWebkit: prepareFixture('fix-medium', 'def-w'),
  mutationsWebkit: prepareFixture('fix-medium', 'mut-w'),
  failureModesWebkit: prepareFixture('fix-medium-failure-modes', 'fail-w'),
  v92Webkit: prepareFixture('fix-medium-v92', 'v92-w'),
  boardWebkit: prepareFixture('fix-small', 'board-w'),
};

// Port allocation: keep chromium on the original 8765-8767 plus 8768 for v92;
// webkit gets 8775-8778. Board project gets 8769. Smoke gets 8770.
// Mismatched-port assertions in specs read from baseURL, not literal ports,
// so this is purely an operational convenience.
const ports = {
  defaultChromium: 8765,
  mutationsChromium: 8766,
  failureModesChromium: 8767,
  v92Chromium: 8768,
  boardChromium: 8769,
  gridSmokeChromium: 8770,
  defaultWebkit: 8775,
  mutationsWebkit: 8776,
  failureModesWebkit: 8777,
  v92Webkit: 8778,
  boardWebkit: 8779,
};

const SERVER_CMD = (port: number, missionDir: string) =>
  `uv run --directory ${path.resolve(__dirname, '..', '..', '..')} ` +
  `--with fastapi --with "uvicorn[standard]" --with sse-starlette --with pyyaml ` +
  // --no-browser: test webServers must NEVER auto-open the dashboard. Without
  // it every project's webServer calls webbrowser.open(), so an unfiltered
  // `npx playwright test` (all ~11 projects) spawns ~11 real browser tabs.
  `python3 -m megalodon_ui --port ${port} --mission-dir ${missionDir} --no-browser`;

// Parse `--project=<name>` / `--project <name>` from argv so we only spin up
// the webServer(s) needed for the selected project. Without this, all 10
// webServers start on every run regardless of --project=, causing slow startup
// and parallel-execution port-collision races between concurrent test runs.
// Empty set (no --project flag) → start everything (full multi-project run).
function selectedProjectNames(): Set<string> {
  const argv = process.argv;
  const out = new Set<string>();
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--project' && argv[i + 1]) out.add(argv[i + 1]);
    else if (a.startsWith('--project=')) out.add(a.slice('--project='.length));
  }
  return out;
}

const PROJECT_TO_PORT: Record<string, number> = {
  'chromium-default': ports.defaultChromium,
  'chromium-mutations': ports.mutationsChromium,
  'chromium-failure-modes': ports.failureModesChromium,
  'chromium-v92-dashboard': ports.v92Chromium,
  'chromium-board': ports.boardChromium,
  'chromium-grid-smoke': ports.gridSmokeChromium,
  'webkit-default': ports.defaultWebkit,
  'webkit-mutations': ports.mutationsWebkit,
  'webkit-failure-modes': ports.failureModesWebkit,
  'webkit-v92-dashboard': ports.v92Webkit,
  'webkit-board': ports.boardWebkit,
};

function filterWebServersByProject<T extends { url: string }>(all: T[]): T[] {
  const sel = selectedProjectNames();
  if (sel.size === 0) return all;
  const wantedPorts = new Set<number>();
  for (const p of sel) {
    const port = PROJECT_TO_PORT[p];
    if (port !== undefined) wantedPorts.add(port);
  }
  if (wantedPorts.size === 0) return all;
  return all.filter(ws => {
    const m = ws.url.match(/:(\d+)/);
    return m ? wantedPorts.has(parseInt(m[1], 10)) : false;
  });
}

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

// Smoke environment: fake spawner enabled so /__fake__/emit works and so lane
// sessions are pre-populated (inject endpoint needs spawner.sessions). The
// fake-spawner lifespan branch in server.py runs before the test_mode branch,
// so MEGALODON_LIFESPAN_TEST_MODE would be a no-op here and is omitted.
const GRID_SMOKE_ENV = {
  MEGALODON_FAKE_SPAWNER: '1',
};

// Specs that belong to the v9.2 dashboard project (xterm dashboard + auth +
// followup + terminal-pane component). They use the fake spawner and exchange
// state via /__fake__/*.
const V92_SPEC_PATTERN =
  /(dashboard-loads|auth-redirect|streams-render|lane-exit-detected|followup-send-debounced|followup|test_terminal_pane)\.spec\.ts$/;
const FAILURE_MODES_PATTERN = /test_failure_modes\.spec\.ts$/;
const MUTATIONS_PATTERN = /test_orchestrator_actions\.spec\.ts$/;
// Board page specs run against the 3-lane fix-small fixture (chromium-board project).
// Also includes lane_detail spec which navigates from the board.
// The v9.4 phase-1 smoke spec runs against chromium-grid-smoke (fake spawner enabled).
// The stale-badge spec (T2.8) runs under chromium-board because it needs MEGALODON_FAKE_SPAWNER=1
// (uses _test/stale_override endpoint) — chromium-board includes MEGALODON_FAKE_SPAWNER.
const BOARD_SPEC_PATTERN = /test_(board_[a-z0-9_]+|lane_detail|activity_wall|stale_badge|v94_phase2_smoke|v94_phase3_smoke|findings_page|signals_page|mission_page|tasks_page|approval_rules)\.spec\.ts$/;
const GRID_SMOKE_SPEC_PATTERN = /test_v94_phase1_smoke\.spec\.ts$/;
// Everything that doesn't match the above four/five patterns belongs to *-default.
const DEFAULT_IGNORE = [V92_SPEC_PATTERN, FAILURE_MODES_PATTERN, MUTATIONS_PATTERN, BOARD_SPEC_PATTERN, GRID_SMOKE_SPEC_PATTERN];

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
    {
      name: 'chromium-board',
      testMatch: BOARD_SPEC_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.boardChromium}` },
    },
    {
      name: 'chromium-grid-smoke',
      testMatch: GRID_SMOKE_SPEC_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.gridSmokeChromium}` },
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
    {
      // Safari-engine coverage for the board (operator uses Safari). Mirrors
      // chromium-board: same BOARD_SPEC_PATTERN, fix-small fixture, fake spawner.
      name: 'webkit-board',
      testMatch: BOARD_SPEC_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Safari'], baseURL: `http://127.0.0.1:${ports.boardWebkit}` },
    },
  ],

  webServer: filterWebServersByProject([
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
    // chromium-board uses MEGALODON_FAKE_SPAWNER=1 so test_stale_badge.spec.ts
    // can call _test/stale_override (T2.8). The board/lane_detail specs are
    // unaffected — they don't test spawner behaviour directly.
    { command: SERVER_CMD(ports.boardChromium, fixtures.boardChromium),
      url: `http://127.0.0.1:${ports.boardChromium}/`,
      reuseExistingServer: false, timeout: 30_000, env: GRID_SMOKE_ENV },
    { command: SERVER_CMD(ports.gridSmokeChromium, fixtures.gridSmokeChromium),
      url: `http://127.0.0.1:${ports.gridSmokeChromium}/`,
      reuseExistingServer: false, timeout: 30_000, env: GRID_SMOKE_ENV },
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
    { command: SERVER_CMD(ports.boardWebkit, fixtures.boardWebkit),
      url: `http://127.0.0.1:${ports.boardWebkit}/`,
      reuseExistingServer: false, timeout: 30_000, env: GRID_SMOKE_ENV },
  ]),
});
