"""megalodon_ui.queue — V9 M1 queue applier + client.

Standalone applier daemon serializes shared-state writes to STATUS.md /
TASKS.md / HISTORY.md / .mission-events / claims/. Workers submit
write-intents via `queue_client.submit()`; applier drains
`queue/pending/*.json` under per-file fcntl.LOCK_EX and journals (WAL)
for crash safety.

Spec: docs/superpowers/specs/2026-05-16-v9-m1-queue-trio-design.md
"""

from __future__ import annotations
