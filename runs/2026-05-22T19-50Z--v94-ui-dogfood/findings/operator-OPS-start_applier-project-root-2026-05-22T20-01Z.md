# FINDING — start_applier.sh computed a two-line PROJECT_ROOT (uv "os error 2")

- **Lane:** operator (kickoff, pre-spawn)
- **Severity:** high (blocks fleet launch — applier never starts)
- **Surface:** ops / `scripts/start_applier.sh`
- **Status:** fixed in this run (commit pending)

## Symptom

`./scripts/start_applier.sh <run-dir>` exited with uv's `error: No such file or
directory (os error 2)` and never wrote a heartbeat. The fleet launcher gates on
`queue/.applier.lock/heartbeat.txt`, so this silently blocked kickoff.

## Root cause

```sh
PROJECT_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || cd "$(dirname "$0")/.." && pwd)"
```

`&&` and `||` are left-associative with equal precedence, so this is
`(git... || cd...) && pwd`. When `git rev-parse` **succeeds**, the `||` skips the
`cd`, but the trailing `&& pwd` still runs (git's exit 0 satisfies the `&&`) — so
the command substitution captures BOTH git's output and `pwd`'s output:

```
/Users/dave/Documents/Projects/megalodon
/Users/dave/Documents/Projects/megalodon
```

`uv run --directory "<two-line path>"` then fails with ENOENT. The bug was latent:
it only bites when invoked as `./scripts/start_applier.sh` from inside the git repo
(the common case); running the `megalodon_ui.queue.applier` module directly avoided it.

## Fix

Bind the fallback to its own assignment so `pwd` only runs on the git-failure path:

```sh
PROJECT_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)" \
  || PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
```

Verified: clean start, `pid=` line printed, heartbeat written.

## Follow-up (v10 / backlog)

- No test covers `start_applier.sh` PROJECT_ROOT resolution. Add a smoke test that
  asserts a single-line repo root and a written heartbeat (candidate for preflight).
- Audit other helper scripts for the same `A || B && C` idiom.
