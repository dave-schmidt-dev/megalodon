# Contrarian review — 2026-05-17 megalodon v9.2 tmux design

**Reviewer:** GPT 5.5 (codex, xhigh reasoning), read-only sandbox
**Target:** docs/superpowers/specs/2026-05-17-megalodon-v9-2-tmux-design.md
**Started:** 2026-05-18T00:11:05Z
**Note:** codex's read-only sandbox blocked direct file creation; full report captured from last-message and saved here by the orchestrator.

---

## 1. Obviously Wrong

1. `capture-pane` snapshots are not an xterm.js byte stream. The spec sends full `capture-pane -e -p` frames to `term.write()` (`docs/...tmux-design.md:231`, `:240`, `:303`). tmux documents `capture-pane -e` as captured pane content with attributes, not a cursor-positioned terminal replay stream; xterm `write()` consumes PTY-style bytes. Concrete failure: the browser appends repeated full-screen snapshots instead of repainting the lane.

2. The stdin proxy does not match the adapter contract. P5 assumes interactive input can be sent with `send-keys` (`docs/...tmux-design.md:244-250`, `:310-334`), but v9.1 adapters build one-shot commands (`adapter-protocol-slice.py:22-31`; e.g. `claude --print`, `codex exec`, `gemini -p`). Concrete failure: `send-keys` posts succeed while real harnesses ignore stdin or have already exited.

3. The auth rewrite is incomplete. The cookie model in §4 says no URL token after bootstrap (`docs/...tmux-design.md:151-160`), but P4 still stores `?t=` in `sessionStorage` (`:241`), Flow 2 still uses `/pane-stream?t=<token>` (`:300`), Flow 3 still sends `X-Megalodon-Token` (`:319`), and E2E still tests "no `?t=`" (`:556`). OW-1 was not actually fixed across the spec.

4. The shutdown rewrite is incomplete. Default shutdown is non-destructive (`docs/...tmux-design.md:364-375`), but P6 still says SIGTERM kills all sessions and deletes `.fleet/ui.token` (`:257`), and the integration test asserts exactly that destructive behavior (`:547`). OW-4 remains live.

5. Token write is still race-prone. The spec probes the port, closes it, then unlinks an existing token and retries `O_EXCL` (`docs/...tmux-design.md:344-345`). Two concurrent starts can both pass the probe; one can overwrite the token file, then lose the uvicorn bind race, leaving the winning server with an invalid printed token. The port-ordering fix does not solve WR-4.

6. Session names are globally collision-prone. Runtime sessions are named `lane-AUDIT` etc. on the default tmux socket (`docs/...tmux-design.md:34`, `:97`, `:112`, `:417-433`). "One mission per server" (`:582`) does not prevent two server processes or two mission dirs on one host. Concrete failure: mission B reattaches to mission A's panes and pipes A output into B's `.fleet` logs.

7. Destructive shutdown can kill unrelated sessions. The `--shutdown` mode kills each `lane-*` session (`docs/...tmux-design.md:381`) while `tmux.list_sessions(prefix="lane-")` is the wrapper shape (`:88`). With the default socket, that can kill another mission's or user's lane-prefixed tmux sessions.

8. Runtime secrets/logs are not protected from git. The spec creates `.fleet/ui.token` and `.fleet/*.stream.log` (`docs/...tmux-design.md:57-60`), but current `.gitignore` ignores queue state and `.fleet-ledger`, not `.fleet` (`.gitignore:55-80`). This violates the no-secrets rule and makes PW-4 worse than retention policy: the token and transcripts are commit candidates.

9. Disk-full handling is fictional. The spec says disk full writing `.fleet/*.stream.log` is fatal and triggers clean shutdown (`docs/...tmux-design.md:413`), but the writer is tmux `pipe-pane` to a shell command (`:85`, `:223`; roadmap `docs/v9/v9-2-ROADMAP.md:35`). The Python server has no specified error channel from `cat >> file`, so it cannot reliably perform the promised fatal shutdown.

10. The documented launch command uses flags the current entrypoint does not have. The spec says `--mission` (`docs/...tmux-design.md:190`, `:551`) and `--shutdown <mission>` (`:381`), while the current entrypoint accepts `--mission-dir`, `--port`, and `--host` only (`megalodon_ui/__main__.py:22-33`). The proposed replacement command will not start as written.

## 2. Probably Wrong

1. Blocking tmux subprocesses run inside the FastAPI event loop. `tmux.py` is specified as synchronous `subprocess.run` (`docs/...tmux-design.md:91`), capture tasks call `capture-pane` every 500 ms (`:231`), and this all runs in lifespan/uvicorn's loop (`:338-359`). Concrete risk: SSE and control endpoints stall behind repeated process spawning.

2. PW-2 is real but is a symptom, not the disease. Playwright `workers: 1` is justified by tmux name collisions (`docs/...tmux-design.md:551`). That caps parallelism because the design chose global tmux names and a fixed port, not because Playwright requires serialization.

3. PW-4 is real and under-classified. `.fleet/*.stream.log` is source of truth (`docs/...tmux-design.md:59`) and no rotation ships (`:225-226`, `:581`). The 500 MB watchdog threshold is unexplained (`:226`) and no redaction boundary exists.

4. PW-6 is real. Multi-mission ergonomics are not merely undefined; they corrupt state through global tmux names and shared cookie host/path behavior (`docs/...tmux-design.md:23`, `:34`, `:156`, `:582`).

5. PW-7 is real, but the spec looks at the wrong bottleneck. It claims `pipe-pane` line-buffered latency (`docs/...tmux-design.md:282`), but tmux only documents piping pane output to a shell command; it does not promise line buffering. High-output lanes can block or distort both applier and UI assumptions.

6. PW-8 is real. `capture_queues` is an unbounded list (`docs/...tmux-design.md:117`), while only each individual queue is capped (`:231`, `:306`). Localhost SSE flooding can grow subscriber state without bound.

7. The stub fixture added by the self-pass is broken. It uses `pathlib` and `json` without imports (`docs/...tmux-design.md:490-499`) and returns `happy`/`error` model args while the shell fixture switches on `audit-happy`/`audit-error` (`:511-525`). CI tests based on this fixture fail before testing tmux.

8. The test plan still encodes obsolete auth and shutdown expectations. `token-rejection.spec.ts` references missing `?t=` (`docs/...tmux-design.md:556`), and `test_shutdown_cleanup.py` expects destructive shutdown (`:547`). These tests will either fail against the intended design or force implementation back to the old broken behavior.

## 3. Worth Reconsidering

1. WR-1 is real. The spec says N lanes (`docs/...tmux-design.md:29`, `:239`) but repeatedly bakes in 6 (`:34`, `:219`, `:242`, `:545`, `:552`). v9.1's config-driven lane count is being undermined.

2. WR-2 is real. xterm.js is "vendored, not CDN" (`docs/...tmux-design.md:178-183`), but no version, license handling, update path, or integrity story is specified.

3. WR-3 is real. The spec relies on EventSource reconnect defaults (`docs/...tmux-design.md:308`); the HTML standard leaves the initial reconnection time implementation-defined, roughly a few seconds. No retry cadence is specified.

4. WR-4 should be escalated. `O_CREAT|O_EXCL` is named (`docs/...tmux-design.md:146`, `:345`), but unlink-and-retry after a port probe reintroduces a multi-process race. Python documents mode is still subject to umask, and the spec does not verify final permissions.

5. WR-5 does not appear real on current GitHub Ubuntu 24.04 runner images. The current runner image software list does not include `tmux`; `apt install tmux` is not obviously redundant.

6. WR-6 is real. The stale-session branch is now described as mostly reachable only with `--fresh` or residual artifacts (`docs/...tmux-design.md:402`), but `--fresh` is not specified in the CLI migration table (`:195-207`) or current entrypoint.

7. Numeric constants are agent fingerprints. 500 ms capture (`docs/...tmux-design.md:36`, `:231`), 200×50 panes (`:99`), queue depth 4 (`:231`), 50 ms debounce (`:248`), 500 MB watchdog (`:226`), 86400 cookie max-age (`:156`), and 32-byte token (`:144`) are asserted without measurement or threat model.

8. New server logging is not designed. The spec says "log warning" / "log + continue" / "log + shutdown" repeatedly (`docs/...tmux-design.md:403`, `:412-413`) but does not define the required rotating file logging. Existing helper logging does this explicitly in `scripts/_logging.py:1-24`.

---

## Verdict

spec-should-be-redone

---

External checks used: tmux man page for `capture-pane`, `pipe-pane`, `new-session`, and `remain-on-exit`; FastAPI lifespan docs; WHATWG EventSource/HTML fetch credentials and reconnect behavior; Python `asyncio.Queue` and `os.open` docs; GitHub runner image software list.
