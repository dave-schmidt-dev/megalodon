---
lane: TEST
agent: agent-7248
task: scratch
severity: NIT
utc: 2026-01-01T01:45Z
artifact: synthetic (fixture)
scratch: true
---

# Scratch: working notes

## Summary

Seeded fixture scratch file. Body intentionally minimal. Used by `test_status_view:53` to verify scratch-chip toggle filter behavior on the FINDINGS view.

## Notes

- Filename suffix `.scratch.md` is the convention; FE filter chip should toggle visibility based on this suffix OR `scratch: true` frontmatter.
- This is one of N scratch files; tests assert toggling changes the rendered count.
