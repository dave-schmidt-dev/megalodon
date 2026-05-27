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
  // P2.6: v92 project split into read-only (parallel) + mutation (workers:1)
  // halves; each owns its own tmpdir so the parallel RO half can never observe
  // the MUT half's server-state writes (and vice-versa).
  v92RoChromium: prepareFixture('fix-medium-v92', 'v92ro-c'),
  v92MutChromium: prepareFixture('fix-medium-v92', 'v92mut-c'),
  // P2.6: board project split into read-only (parallel) + mutation (workers:1)
  // halves; each owns its own tmpdir.
  boardRoChromium: prepareFixture('fix-small', 'boardro-c'),
  boardMutChromium: prepareFixture('fix-small', 'boardmut-c'),
  // Phase-1 smoke: same 3-lane fix-small fixture, fake spawner enabled.
  gridSmokeChromium: prepareFixture('fix-small', 'smoke-c'),
  defaultWebkit: prepareFixture('fix-medium', 'def-w'),
  mutationsWebkit: prepareFixture('fix-medium', 'mut-w'),
  failureModesWebkit: prepareFixture('fix-medium-failure-modes', 'fail-w'),
  v92RoWebkit: prepareFixture('fix-medium-v92', 'v92ro-w'),
  v92MutWebkit: prepareFixture('fix-medium-v92', 'v92mut-w'),
  boardRoWebkit: prepareFixture('fix-small', 'boardro-w'),
  boardMutWebkit: prepareFixture('fix-small', 'boardmut-w'),
};

// Port allocation: keep chromium on the original 8765-8767 plus 8768 for v92;
// webkit gets 8775-8778. Smoke gets 8770.
// P2.6: the v92 and board projects each split into RO + MUT halves, each with
// its own webServer/port. Chromium RO/MUT halves get 8768/8771 (v92) and
// 8769/8772 (board); webkit gets 8778/8780 (v92) and 8779/8781 (board).
// Mismatched-port assertions in specs read from baseURL, not literal ports,
// so this is purely an operational convenience.
const ports = {
  defaultChromium: 8765,
  mutationsChromium: 8766,
  failureModesChromium: 8767,
  v92RoChromium: 8768,
  v92MutChromium: 8771,
  boardRoChromium: 8769,
  boardMutChromium: 8772,
  gridSmokeChromium: 8770,
  defaultWebkit: 8775,
  mutationsWebkit: 8776,
  failureModesWebkit: 8777,
  v92RoWebkit: 8778,
  v92MutWebkit: 8780,
  boardRoWebkit: 8779,
  boardMutWebkit: 8781,
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
  'chromium-v92-ro': ports.v92RoChromium,
  'chromium-v92-mut': ports.v92MutChromium,
  'chromium-board-ro': ports.boardRoChromium,
  'chromium-board-mut': ports.boardMutChromium,
  'chromium-grid-smoke': ports.gridSmokeChromium,
  'webkit-default': ports.defaultWebkit,
  'webkit-mutations': ports.mutationsWebkit,
  'webkit-failure-modes': ports.failureModesWebkit,
  'webkit-v92-ro': ports.v92RoWebkit,
  'webkit-v92-mut': ports.v92MutWebkit,
  'webkit-board-ro': ports.boardRoWebkit,
  'webkit-board-mut': ports.boardMutWebkit,
};

// Projects that intentionally have NO Playwright-managed webServer (they spawn
// their own). Selecting ONLY these must start zero webServers — otherwise the
// "empty wantedPorts → return all" fallback would boot all ~11 servers.
const SELF_MANAGED_SERVER_PROJECTS = new Set<string>(['chromium-restart']);

function filterWebServersByProject<T extends { url: string }>(all: T[]): T[] {
  const sel = selectedProjectNames();
  if (sel.size === 0) return all;
  // If every selected project manages its own server, start none.
  if ([...sel].every(p => SELF_MANAGED_SERVER_PROJECTS.has(p))) return [];
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
//
// P2.6 split: these specs were previously one `workers:1` project (serialized
// because SOME of them mutate shared server state). Partitioned into:
//   - V92_RO_PATTERN: render/read-only specs (no POST, no shared-file writes) →
//     fully parallel, saturates the global worker pool.
//   - V92_MUT_PATTERN: specs that POST to /__fake__/emit, /__fake__/set_state,
//     or /api/v1/control-mode (global server state) → fullyParallel:false,
//     workers:1.
// V92_SPEC_PATTERN is retained as the union (used by DEFAULT_IGNORE etc.).
const V92_RO_PATTERN = /(dashboard-loads|auth-redirect|followup-send-debounced)\.spec\.ts$/;
const V92_MUT_PATTERN = /(streams-render|lane-exit-detected|followup|test_terminal_pane)\.spec\.ts$/;
const V92_SPEC_PATTERN =
  /(dashboard-loads|auth-redirect|streams-render|lane-exit-detected|followup-send-debounced|followup|test_terminal_pane)\.spec\.ts$/;
const FAILURE_MODES_PATTERN = /test_failure_modes\.spec\.ts$/;
const MUTATIONS_PATTERN = /test_orchestrator_actions\.spec\.ts$/;
// Board page specs run against the 3-lane fix-small fixture (board projects).
// Also includes lane_detail spec which navigates from the board.
// The v9.4 phase-1 smoke spec runs against chromium-grid-smoke (fake spawner enabled).
// The stale-badge spec (T2.8) runs under a board project because it needs MEGALODON_FAKE_SPAWNER=1
// (uses _test/stale_override endpoint) — board projects include MEGALODON_FAKE_SPAWNER.
//
// P2.6 split: the board project was previously one `workers:1` project
// (serialized only because SOME board specs mutate shared server state —
// STATUS.md/findings/signals file writes, /__fake__/narrative, /__fake__/emit,
// _test/stale_override, setControlMode). Partitioned into:
//   - BOARD_RO_PATTERN: specs that only render/read the dashboard (no POST, no
//     shared-file writes) → fullyParallel:true, saturates the global pool.
//   - BOARD_MUT_PATTERN: specs that write fixture files or POST mutations →
//     fullyParallel:false, workers:1.
// Partition is exhaustive and disjoint over BOARD_SPEC_PATTERN (verified:
// 15 RO + 17 MUT = 32). BOARD_SPEC_PATTERN is retained as the union for
// DEFAULT_IGNORE and other ignore lists.
const BOARD_RO_PATTERN = /(test_board_activity_backfill|test_board_activity_cap|test_board_activity_reconnect|test_board_coordination|test_board_frontdoor|test_board_goal_progress|test_board_phase_strip|test_board_reauth_nonblocking|test_board_rows|test_board_signals_3channels|test_board_signals_antispoof|test_board_status_change_guard|test_findings_page|test_mission_page|test_tasks_page)\.spec\.ts$/;
const BOARD_MUT_PATTERN = /(test_activity_wall|test_approval_rules|test_board_activity_autoscroll|test_board_signals_live|test_signals_page|test_v94_phase2_smoke|test_board_auth_resilience|test_board_blocked_and_stale|test_board_drawer|test_board_fix_round3|test_board_narrative|test_board_precedence|test_board_safety|test_board_ungoverned|test_lane_detail|test_board_stale|test_stale_badge)\.spec\.ts$/;
const BOARD_SPEC_PATTERN = /test_(board_[a-z0-9_]+|lane_detail|activity_wall|stale_badge|v94_phase2_smoke|findings_page|signals_page|mission_page|tasks_page|approval_rules)\.spec\.ts$/;
const GRID_SMOKE_SPEC_PATTERN = /test_v94_phase1_smoke\.spec\.ts$/;
// The restart-reconnect spec (Task D6 / PW-3) manages its OWN server process
// (Node child_process), so it runs under the dedicated `chromium-restart`
// project which has NO Playwright-managed webServer. Every other project must
// ignore it so they don't try to run it against their (wrong) webServer.
const RESTART_SPEC_PATTERN = /test_restart_reconnect\.spec\.ts$/;
// Everything that doesn't match the above patterns belongs to *-default.
const DEFAULT_IGNORE = [V92_SPEC_PATTERN, FAILURE_MODES_PATTERN, MUTATIONS_PATTERN, BOARD_SPEC_PATTERN, GRID_SMOKE_SPEC_PATTERN, RESTART_SPEC_PATTERN];

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
      // P2.6: v92 read-only half — no shared server-state writes, so fully
      // parallel against the global worker cap.
      name: 'chromium-v92-ro',
      testMatch: V92_RO_PATTERN,
      fullyParallel: true,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.v92RoChromium}` },
    },
    {
      // P2.6: v92 mutation half — POSTs to /__fake__/* and /api/v1/control-mode
      // share server state, so kept serial.
      name: 'chromium-v92-mut',
      testMatch: V92_MUT_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.v92MutChromium}` },
    },
    {
      // P2.6: board read-only half — render/read only, so fully parallel.
      name: 'chromium-board-ro',
      testMatch: BOARD_RO_PATTERN,
      fullyParallel: true,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.boardRoChromium}` },
    },
    {
      // P2.6: board mutation half — fixture-file writes + /__fake__/narrative /
      // _test/stale_override / setControlMode share server state, so serial.
      name: 'chromium-board-mut',
      testMatch: BOARD_MUT_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.boardMutChromium}` },
    },
    {
      name: 'chromium-grid-smoke',
      testMatch: GRID_SMOKE_SPEC_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ports.gridSmokeChromium}` },
    },
    {
      // Task D6 / PW-3: restart-reconnect. Manages its own server process, so
      // NO webServer entry below for this project and no baseURL (the spec uses
      // absolute http://127.0.0.1:<port> URLs against the port it spawns).
      // Chromium-only (no webkit needed for this server-lifecycle test).
      name: 'chromium-restart',
      testMatch: RESTART_SPEC_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Chrome'] },
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
      // P2.6: webkit v92 read-only half (mirrors chromium-v92-ro).
      name: 'webkit-v92-ro',
      testMatch: V92_RO_PATTERN,
      fullyParallel: true,
      use: { ...devices['Desktop Safari'], baseURL: `http://127.0.0.1:${ports.v92RoWebkit}` },
    },
    {
      // P2.6: webkit v92 mutation half (mirrors chromium-v92-mut).
      name: 'webkit-v92-mut',
      testMatch: V92_MUT_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Safari'], baseURL: `http://127.0.0.1:${ports.v92MutWebkit}` },
    },
    {
      // Safari-engine coverage for the board (operator uses Safari).
      // P2.6: read-only half — fully parallel (mirrors chromium-board-ro).
      name: 'webkit-board-ro',
      testMatch: BOARD_RO_PATTERN,
      fullyParallel: true,
      use: { ...devices['Desktop Safari'], baseURL: `http://127.0.0.1:${ports.boardRoWebkit}` },
    },
    {
      // P2.6: webkit board mutation half — serial (mirrors chromium-board-mut).
      name: 'webkit-board-mut',
      testMatch: BOARD_MUT_PATTERN,
      fullyParallel: false,
      workers: 1,
      use: { ...devices['Desktop Safari'], baseURL: `http://127.0.0.1:${ports.boardMutWebkit}` },
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
    // P2.6: v92 RO + MUT halves, each its own webServer/port/tmpdir.
    { command: SERVER_CMD(ports.v92RoChromium, fixtures.v92RoChromium),
      url: `http://127.0.0.1:${ports.v92RoChromium}/`,
      reuseExistingServer: false, timeout: 30_000, env: V92_ENV },
    { command: SERVER_CMD(ports.v92MutChromium, fixtures.v92MutChromium),
      url: `http://127.0.0.1:${ports.v92MutChromium}/`,
      reuseExistingServer: false, timeout: 30_000, env: V92_ENV },
    // board projects use MEGALODON_FAKE_SPAWNER=1 so test_stale_badge.spec.ts
    // can call _test/stale_override (T2.8). The board/lane_detail specs are
    // unaffected — they don't test spawner behaviour directly.
    // P2.6: board RO + MUT halves, each its own webServer/port/tmpdir.
    { command: SERVER_CMD(ports.boardRoChromium, fixtures.boardRoChromium),
      url: `http://127.0.0.1:${ports.boardRoChromium}/`,
      reuseExistingServer: false, timeout: 30_000, env: GRID_SMOKE_ENV },
    { command: SERVER_CMD(ports.boardMutChromium, fixtures.boardMutChromium),
      url: `http://127.0.0.1:${ports.boardMutChromium}/`,
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
    // P2.6: webkit v92 RO + MUT halves.
    { command: SERVER_CMD(ports.v92RoWebkit, fixtures.v92RoWebkit),
      url: `http://127.0.0.1:${ports.v92RoWebkit}/`,
      reuseExistingServer: false, timeout: 30_000, env: V92_ENV },
    { command: SERVER_CMD(ports.v92MutWebkit, fixtures.v92MutWebkit),
      url: `http://127.0.0.1:${ports.v92MutWebkit}/`,
      reuseExistingServer: false, timeout: 30_000, env: V92_ENV },
    // P2.6: webkit board RO + MUT halves.
    { command: SERVER_CMD(ports.boardRoWebkit, fixtures.boardRoWebkit),
      url: `http://127.0.0.1:${ports.boardRoWebkit}/`,
      reuseExistingServer: false, timeout: 30_000, env: GRID_SMOKE_ENV },
    { command: SERVER_CMD(ports.boardMutWebkit, fixtures.boardMutWebkit),
      url: `http://127.0.0.1:${ports.boardMutWebkit}/`,
      reuseExistingServer: false, timeout: 30_000, env: GRID_SMOKE_ENV },
  ]),
});
