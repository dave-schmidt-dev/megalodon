#!/usr/bin/env bash
# Pre-flight gate for a dogfood run. Each check prints "CHECK <name> PASS|FAIL".
# Exits non-zero if any check fails. --dry-run skips the live loops-armed check.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
DRY=0; [[ "${1:-}" == "--dry-run" ]] && DRY=1
FAIL=0
ok()  { echo "CHECK $1 PASS"; }
bad() { echo "CHECK $1 FAIL: $2" >&2; FAIL=1; }

# 1. pytest collection scope.
if uv run pytest scripts/tests/test_pytest_collection_scope.py -q >/dev/null 2>&1; then
  ok pytest-scope; else bad pytest-scope "testpaths/norecursedirs not set"; fi

# 2. test deps resolve + portable suite is green (Python third of Task 4.2).
#    Excludes: `isolated`-marked real-tmux tests + the non-portable pipe-pane
#    ANSI test (CI-Linux-only; they fail on macOS by design), and this gate's
#    own test file (would recurse: test_preflight.py -> preflight.sh -> pytest).
#    Set PREFLIGHT_SKIP_HEAVY=1 to skip the suite run (used by test_preflight.py
#    so the gate's unit test stays fast and cannot recurse).
if [[ "${PREFLIGHT_SKIP_HEAVY:-0}" == "1" ]]; then
  ok test-deps
elif uv run --extra test python3 -c \
       "import pytest_asyncio, freezegun, fastapi, httpx, sse_starlette, yaml" >/dev/null 2>&1 \
     && uv run --extra test pytest scripts/tests -q -p no:cacheprovider \
          -m "not isolated" \
          --ignore=scripts/tests/test_preflight.py \
          --ignore=scripts/tests/test_pipe_pane_preserves_ansi_escapes.py \
          >/dev/null 2>&1; then
  ok test-deps
else
  bad test-deps "deps unresolved or portable scripts/tests not green"
fi

# 3. friction allowlist.
if uv run pytest scripts/tests/test_settings_friction_allowlist.py -q >/dev/null 2>&1; then
  ok friction-allowlist; else bad friction-allowlist "helper-script wildcards missing"; fi

# 4. lifecycle scripts smoke (new_run -> archive_run on throwaway, in a temp git repo).
# Use a SHORT /tmp root, not mktemp's default: on macOS the default $TMPDIR is
# /var/folders/<uid>/T/... (~50 bytes), which alone pushes the smoke run's
# <TMP>/runs/<UTC>--smoke/.fleet/tmux.sock past new_run.sh's 100-byte socket-path
# guard, failing preflight on every Mac regardless of the repo. (Same Unix-socket
# limit the Playwright config dodges via /tmp/m.)
TMP="$(mktemp -d /tmp/mega-pf.XXXXXX)"
(
  cd "$TMP"
  git init -q && git config user.email t@t && git config user.name t
  ln -s "$REPO_ROOT/scripts" scripts; ln -s "$REPO_ROOT/templates" templates
  printf '.archive/\n' > .gitignore   # reproduce the real repo: .archive is gitignored
  mkdir .archive
  printf '# Index\n\n| Run ID | Mission | Started | Completed | Wall clock | Outputs |\n|---|---|---|---|---|---|\n' > .archive/INDEX.md
  RUN_LIB_REPO_ROOT="$TMP" bash scripts/new_run.sh smoke --title S --summary S >/dev/null
  rd="$(ls -d runs/*--smoke)"
  printf 'RUN-START x\nCOMPLETE x\n' > "$rd/.mission-events"
  git add -A && git commit -qm run
  RUN_LIB_REPO_ROOT="$TMP" bash scripts/archive_run.sh "$TMP/$rd" >/dev/null
  # compgen expands the glob (pathname expansion does NOT happen inside [[ -d ]]).
  compgen -G "$TMP/.archive/*--smoke" >/dev/null && [[ ! -d "$TMP/$rd" ]]
) && ok lifecycle-scripts || bad lifecycle-scripts "smoke round-trip failed"
rm -rf "$TMP"

# 5. loops armed (live only).
if [[ $DRY -eq 0 ]]; then
  echo "CHECK loops-armed MANUAL: confirm 6 lanes show >=2 STATUS heartbeats within 10 min"
fi

[[ $FAIL -eq 0 ]] && echo "PREFLIGHT: PASS" || { echo "PREFLIGHT: FAIL" >&2; exit 1; }
