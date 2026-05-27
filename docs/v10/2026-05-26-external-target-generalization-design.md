# v10 — External-Target Generalization (design)

**Date:** 2026-05-26
**Status:** Design approved (brainstorming); pending operator spec-review → implementation plan.
**Author:** orchestrator + operator (David Schmidt).
**First concrete target:** `~/Documents/Projects/wilted` (Python/uv/pytest, `src/` + `tests/`, Makefile).

## 1. Problem

Megalodon's fleet today can only run against **itself**. The project root, the governor's
write-scope, the lane roles, and the gate commands are all hardcoded to the megalodon repo:

- `scripts/gen_lane_launches.py` derives `repo_root` from megalodon's own location
  (`Path(__file__).resolve().parents[1]`); generated lane prompts say *"the project root is
  `/Users/dave/Documents/Projects/megalodon/`"* and *"implement in `megalodon_ui/`"*.
- `megalodon_ui/governor/policy.py` confines writes to the megalodon tree + the mission/run tree
  + scratch temp.
- `.mission-config.yaml` lane roles are megalodon-specific; the gates the lanes run are megalodon's
  (`uv run … pytest`).
- Run-1/Run-2 were self-improvement runs; the lanes' cwd is the mission dir, which *is* the workpiece.

To run the fleet against an external codebase (first: wilted), megalodon must be generalized from
"self-improvement only" to "arbitrary target." This is the v10 refactor.

## 2. Approach (chosen)

**Approach 1 — target profile + thin parameterization.** Add an optional `target` block to the
run's `.mission-config.yaml` and parameterize the four hardcoded surfaces to read it. Reframe
self-improvement as *"the target happens to be megalodon"*: absence of a `target` block defaults
the target to the megalodon repo, so today's behavior is the backward-compatible default and the
risky governor re-scope is exercised by megalodon's own existing tests (target=self) before it is
ever pointed outward.

Rejected: a pluggable per-ecosystem target-adapter abstraction (YAGNI — the first/only concrete
target is Python/uv like megalodon); per-target forks of megalodon (duplicative, drifts).

## 3. The target profile (config schema)

New optional `target` block in `.mission-config.yaml`. For wilted:

```yaml
target:
  root: /Users/dave/Documents/Projects/wilted   # absolute path to the target repo
  gates:                                         # the TARGET's own gates, not megalodon's
    test: "uv run --extra test pytest -q"        # or "make test"
    lint: "uv run ruff check src/"
    build: ""                                    # optional; empty = skip
  areas:                                         # advisory; informs lane roles, not enforced
    backend: ["src/**"]
    test:    ["tests/**"]
  conventions_doc: AGENTS.md                     # target's agent-instructions; lanes read it
```

- **Backward-compatible default:** no `target` block → `root` = the megalodon repo, `gates` =
  megalodon's current commands. Existing self-improvement runs are unchanged; external targeting is
  purely additive.
- **The target owns its gates.** VERIFY/TEST lanes run `target.gates.*` (wilted's commands) so the
  fleet validates work against the target's own definition of green.
- This one block is the single source of truth that governor scope, lane prompts, and working cwd
  all read from.

## 4. Governor write-scope re-scoping (security-critical core)

The allowed-write roots become **explicit configuration, not derived from the governor's own
location.**

**Scope contract** (target external, e.g. wilted):
```
ALLOWED writes = { canonical(target.root), canonical(run_dir), allowed scratch temp }
DENY everything else   (deny-by-default, unchanged)
```

**The inversion:** when targeting wilted, **megalodon's own source tree becomes read-only to the
lanes.** Lanes may read megalodon's `launch.md`/protocol, but the only writable path under megalodon
is the **run dir** (STATUS/TASKS/.mission-events/queue/claims). `megalodon_ui/`, `scripts/`, etc.
are off-limits. In self-improvement mode `target.root` *is* megalodon, so it is writable as today —
same code path, different config.

**How the governor learns scope:** injected explicitly at lane spawn —
`MEGALODON_TARGET_ROOT` + `MEGALODON_RUN_DIR` (env or read from the run's `.mission-config`) — rather
than inferred from cwd. The scope check canonicalizes the write path and confirms it is under one of
the allowed roots.

**Risks — each gets a negative/abuse test:**
- **Symlink / `..` escape** — canonicalize before the scope check; a symlink inside the target
  pointing out must be denied.
- **`-t`/`--target-directory` cp/mv/ln bypass** (closed in Fix R3) — must stay closed against the
  *new* roots.
- **Relative-path writes** — lanes cwd in `target.root`; resolve against cwd, then scope-check.
- **Secret scanning** — unchanged; denied regardless of scope.

This contract lands as a new entry in *megalodon's* `INVARIANTS.md` (see §7), gated by the abuse
suite — v10 dogfoods the closed-loop methodology.

## 5. Lane working model + launch templating

Lanes operate against **three roots**:

| Root | Purpose | Access |
|---|---|---|
| `target.root` (wilted) | the code the lane works on — **lane's cwd** | read/write (governor-scoped) |
| `run_dir` (megalodon/runs/&lt;run&gt;) | orchestration state: STATUS/TASKS/.mission-events/queue/claims | read/write |
| megalodon repo | protocol: `launch.md`, lane prompts | **read-only** |

A lane prompt becomes, in effect: *"Your cwd is the target repo `{target.root}`; do your work here.
Mission state lives at `{run_dir}` — read/write orchestration there via the queue. Read the protocol
from `{megalodon}/launch.md` (read-only)."*

- **`gen_lane_launches.py` generalizes** to template each `launch-<LANE>.md` with `target.root`, the
  lane's `areas` globs (BACKEND → `target.areas.backend` rather than the literal `megalodon_ui/`),
  `target.gates`, and `target.conventions_doc` (wilted's `AGENTS.md`, read so the lane follows the
  target's house style). Roles stay generic; target specifics are injected.
- **Gate-running re-pointed:** TEST/VERIFY lanes run `target.gates.test`/`lint` **in `target.root`**
  (generalize `scripts/run_tests.sh` to take the target dir + command instead of hardcoding
  `uv run --directory <megalodon> pytest`). The governor permits these (execute/write within target
  scope).
- **Commits land in the target.** Code changes are committed to wilted's git; the run dir holds only
  megalodon's orchestration bookkeeping (separate, per §3).

Backward-compat holds: no `target` block → all three roots collapse onto megalodon (today's
behavior).

## 6. Backward-compat & testing

**Backward-compat is the safety lever:** no `target` block → roots collapse to megalodon = today's
behavior, so the existing 1553-test suite + e2e are the regression gate for the `target=self` path.
The governor re-scope ships validated against the trusted codebase, then is pointed outward.

**Testing strategy:**
- **Governor abuse suite (new, security):** target=external → writes inside target allowed; megalodon
  source denied (read-only); run dir writable; outside-all denied; `-t`/symlink/`..` escape denied;
  secret-scan unchanged. Plus target=self regression stays green.
- **Config:** target-profile parse — with/without block, defaults.
- **`gen_lane_launches`:** golden test — no target → byte-identical to today's output; with target →
  wilted paths/gates injected.
- **E2E dry-run against wilted:** `launch_fleet --dry-run` shows lanes cwd'd to wilted + governor
  scoped to wilted, **no spawn**.

## 7. Build ordering (decomposition into shippable units)

1. **Target-profile schema + parsing** — additive, safe.
2. **Governor re-scope** — security-critical; new megalodon `INV` + abuse tests; validate target=self
   first (existing governor tests stay green), then target=external.
3. **Lane model + `gen_lane_launches` templating + gate-running re-point.**
4. **E2E dry-run against wilted** — prove the wiring, no live fleet.
5. **`:megalodon`** run-setup prompt (the originally-requested artifact, now unblocked): an Espanso
   prompt at `~/.agent/prompts/megalodon.md` wired into `base.yml`. Interactive orchestrator
   interview → locks the run frame (target repo, scope, **exit criteria**, lanes/models;
   light-touch closed-loop: if `<target>/INVARIANTS.md` exists, surface a freeze/recurring-failure
   brief, else no-op) → writes `.mission-config` (target=wilted) + MISSION.md → `new_run.sh` scaffold
   → `preflight.sh` → **prints the exact `launch_fleet` command and STOPS (no spawn)**. A human pulls
   the trigger on the live fleet.

Then, and only then, a live wilted run.

## 8. Dependencies / risks

- **CI re-enable is a natural prerequisite track.** This is a large change to a safety-sensitive
  system and wants CI back as a net. Re-enabling CI is exactly the frozen **INV-3** consolidation
  plan (CI currently disabled as `test.yml.disabled` after the May Actions-cost blowout). Land that
  consolidation (no macOS, `concurrency: cancel-in-progress`, scoped triggers, budget cap, dated
  INV-3 resolution) before/alongside v10.
- **New megalodon invariant.** §4's governor-scope contract becomes a charter entry +
  abuse-test gate (dogfoods the closed-loop).
- **Residual:** the governor re-scope is the blast-radius risk — a scope bug means an autonomous
  fleet writes outside the target. Mitigated by deny-by-default + the abuse suite + target=self
  validation first, but it is the section to review hardest.
- **`:megalodon` endpoint is launch-ready, never auto-spawn** (operator decision) — a live
  multi-agent fleet only starts on explicit human action.

## 9. Out of scope

- Pluggable per-ecosystem target adapters (until a non-Python target appears).
- Non-`software-engineering` mission types.
- Auto-spawning the fleet from `:megalodon`.
- Multi-target / concurrent-target runs.
