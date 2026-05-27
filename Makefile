# Megalodon local test gate.
#
# CI was removed (2026-05-27); this Makefile is the single source of the local
# gate. `gate` (== gate-fast) is what the pre-push hook runs as a BLOCKING check;
# `gate-full` adds the slow real-tmux + Playwright tiers and is run manually
# before a fleet launch.
#
# Parallelism: test-py uses pytest-xdist (`-n auto`). `--dist loadgroup` honors
# the xdist_group marker on test_main_passes_fd_to_uvicorn.py (those tests bind a
# fixed port and must share one worker). Linter versions are PINNED to match the
# pre-commit hook exactly (ruff==0.15.14, vulture) per the CI-parity rule.

.PHONY: test-py test-isolated test-js lint test-e2e gate gate-fast gate-full

# Chromium e2e projects (post-P2.6 ro/mut split). Webkit mirrors run in CI-parity
# manual sweeps; the local gate uses chromium for speed.
E2E_CHROMIUM := \
	--project=chromium-default \
	--project=chromium-board-ro \
	--project=chromium-board-mut \
	--project=chromium-mutations \
	--project=chromium-failure-modes \
	--project=chromium-v92-ro \
	--project=chromium-v92-mut \
	--project=chromium-grid-smoke \
	--project=chromium-restart

test-py:  ## Fast Python suite (parallel, no real-tmux tier)
	uv run --extra test pytest scripts/tests ui/tests/integration ui/tests/unit \
		-m "not isolated" -n auto --dist loadgroup -q

test-isolated:  ## Real-tmux isolated tier (forked; runs on macOS)
	uv run --extra test pytest scripts/tests ui/tests -m isolated --forked -q

test-js:  ## JS unit suite (node:test)
	node --test ui/tests/unit/*.test.js

lint:  ## ruff + vulture (versions pinned to match pre-commit)
	uv run --with 'ruff==0.15.14' ruff check megalodon_ui scripts
	uv run --with vulture vulture

test-e2e:  ## Playwright chromium matrix (ro projects parallel, mut serial)
	cd ui/tests/e2e && npx playwright test $(E2E_CHROMIUM) --reporter=line

# Fast blocking gate: Python + JS + lint concurrently. This is what pre-push runs.
gate-fast:  ## Concurrent fast gate (test-py + test-js + lint)
	@echo "== gate-fast: test-py + test-js + lint (concurrent) =="
	@set -e; \
	$(MAKE) --no-print-directory test-py & p_py=$$!; \
	$(MAKE) --no-print-directory test-js & p_js=$$!; \
	$(MAKE) --no-print-directory lint & p_lint=$$!; \
	rc=0; \
	wait $$p_py || rc=1; \
	wait $$p_js || rc=1; \
	wait $$p_lint || rc=1; \
	if [ $$rc -ne 0 ]; then echo "== gate-fast FAILED =="; else echo "== gate-fast OK =="; fi; \
	exit $$rc

# Full gate: fast gate, then the slow real-tmux + e2e tiers (run sequentially
# after the fast tier to avoid tmux-socket / port contention with -n auto).
gate-full: gate-fast test-isolated test-e2e  ## Full gate (fast + isolated + e2e)
	@echo "== gate-full OK =="

# `gate` is the blocking pre-push tier.
gate: gate-fast
