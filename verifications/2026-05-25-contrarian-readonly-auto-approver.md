# Contrarian review — 2026-05-25 read-only auto-approver

**Reviewer:** GPT-5.5 xhigh, read-only sandbox
**Target:** docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md
**Started:** 2026-05-25T14:37:42Z

---

## 1. Obviously Wrong
(Objectively incorrect or self-defeating. Spec-line citations required. Appended as discovered.)

### OW-1 — The allowlist approves code execution while claiming arbitrary execution is out of scope.

The design says auto-approval must not cover commands that execute arbitrary code, then allowlists command heads whose normal flags execute arbitrary helper programs. That is not a theoretical edge case: `rg --pre ./preprocess pattern file` is approved by the stated algorithm because `rg` is in `ALLOWLIST`, only `find` has a denylist, and `_has_compound_structure` only rejects shell metacharacter structure rather than command-specific execution flags; ripgrep documents `--pre` as executing a command for every searched file. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:25, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:100, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:127, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:136, /tmp/contrarian-2026-05-25/approval_rules.txt:42, /tmp/contrarian-2026-05-25/approval_rules.txt:53, https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md:596.

The same flaw exists for `fd`: `fd -x touch /tmp/pwn` fits the spec's approval path because `fd` is allowlisted and there is no `fd` flag denial, while `fd` documents `-x/--exec` and `-X/--exec-batch` as executing commands. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:103, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:125, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:127, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:136, https://man.archlinux.org/man/fd.1.en:265, https://man.archlinux.org/man/fd.1.en:294.

The git branch of the policy has the same class of hole after the subcommand, not before it: `git --no-pager diff --ext-diff` passes the stated global-option and subcommand checks, while Git documents `--ext-diff` and textconv as executing external helpers. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:107, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:123, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:131, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:134, https://git-scm.com/docs/git-diff:730, https://git-scm.com/docs/git-diff:737.

### OW-2 — The preview can hide a dangerous suffix without setting `truncated`.

The spec's fail-safe says it will never auto-approve a command it cannot fully see, but the canonical extractor cuts the preview at the first literal `Do you want` inside the excerpt, not only at the actual prompt marker. A command such as `ls Do you want ; rm x` can be rendered to policy as only `[Bash command] ls`, leaving the semicolon and mutating suffix outside the string passed to `_has_compound_structure`; the new `PromptInfo.truncated` fix does not cover this because the spec only defines truncation for cap clipping. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:95, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:100, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:141, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:144, /tmp/contrarian-2026-05-25/preview-shape.txt:51, /tmp/contrarian-2026-05-25/preview-shape.txt:53, /tmp/contrarian-2026-05-25/approval_rules.txt:42.

### OW-3 — The policy parses whitespace-collapsed display text, not the Bash program.

The extractor collapses all whitespace before policy sees the command, while Bash treats newlines as command separators. A rendered Bash block containing `ls` on one line and a mutating command on the next can reach the policy as a single `ls ...` token stream, so the compound-command guard never sees the separator it is supposed to reject. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:95, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:100, /tmp/contrarian-2026-05-25/preview-shape.txt:51, /tmp/contrarian-2026-05-25/preview-shape.txt:52, /tmp/contrarian-2026-05-25/approval_rules.txt:42, /tmp/contrarian-2026-05-25/approval_rules.txt:53.

The preview is also not just a command: the only canonical sample shows a descriptive line after the Bash command, and the extractor folds that prose into the same preview. The spec then asks `shlex.split` to treat that mixed command-plus-description string as the command, so the security decision is made on UI display text rather than an exact Bash AST or exact submitted Bash string. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:87, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:103, /tmp/contrarian-2026-05-25/preview-shape.txt:70, /tmp/contrarian-2026-05-25/preview-shape.txt:73, /tmp/contrarian-2026-05-25/preview-shape.txt:92.

### OW-4 — “Read-only” ignores confidentiality and filesystem scope.

The spec equates safety with not writing, mutating, fetching network, or executing code, but the allowlist auto-approves direct content readers (`cat`, `head`, `tail`, `grep`, `rg`) with no path boundary. That approves commands such as `cat ~/.ssh/id_rsa` or `grep -R token ~/.config`, which can expose secrets into the lane pane and `.fleet/<short>.stream.log`; the global rules explicitly treat secret exposure as a hard stop, not a harmless read. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:18, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:25, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:127, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:200, /Users/dave/.agent/AGENTS.md:12, README.md:29.

Broadening justification: I read `~/.agent/AGENTS.md` and `README.md` because this finding is gated on the repo's explicit secret-handling rule and on where lane output is persisted. Citations: /Users/dave/.agent/AGENTS.md:12, /Users/dave/.agent/AGENTS.md:16, README.md:29.

The spec makes the scope failure explicit by treating `find /` as an acceptable auto-approval consequence. That is not merely a runtime stall; it is whole-machine enumeration under the same policy whose stated goal is benign exploration. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:31, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:32, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:127, README.md:357.

### OW-5 — The allowlist includes a command with a normal write flag.

The design says writes still gate to the operator, then allowlists `tree` with no flag denylist. `tree -o /tmp/pwn` is approved by the stated algorithm and `tree` documents `-o filename` as sending output to a file, so this is a direct write path, not merely “read-only exploration.” Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:18, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:25, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:103, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:127, https://www.mankier.com/1/tree:132, https://www.mankier.com/1/tree:134.

### OW-6 — The proposed `on_change` wiring is clobbered by the existing ActivityWall.

The spec says to pass a server `on_change` handler into the live `PermissionWatcher`, but the existing `ActivityWall.start()` imperatively assigns `permission_watcher._on_change = self._on_permission_change`. In the live startup order, the watcher is constructed and started before `ActivityWall` starts, so the auto-approver callback would be overwritten and never run. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:58, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:152, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:155, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:157, megalodon_ui/server.py:1252, megalodon_ui/server.py:1259, megalodon_ui/server.py:1260, megalodon_ui/activity_wall.py:126, megalodon_ui/activity_wall.py:129.

Broadening justification: I read `megalodon_ui/activity_wall.py` because the spec relies on existing `on_change`/SSE machinery for visibility, and the canonical slice did not include the current `on_change` consumer that owns that machinery. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:184, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:185, megalodon_ui/activity_wall.py:89, megalodon_ui/activity_wall.py:91.

## 2. Probably Wrong
(Defensible but likely suboptimal given goals/constraints. Explain what would be better and why. Appended as discovered.)

### PW-1 — The proposed tests do not exercise the actual attack surface.

The test matrix calls `test_auto_approve.py` the security core, but it only tests `find` mutation flags, git pre-subcommand injection, generic shell structure, eligibility, and truncation. It does not test allowlisted command-specific execution/write flags (`rg --pre`, `fd -x`, `tree -o`, `git diff --ext-diff`), embedded `Do you want`, newline command separators, mixed command-plus-description previews, absolute paths, or secret-reading commands, so it would pass while every OW finding above remains exploitable. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:198, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:200, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:209, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:212, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:214, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:220.

The golden-fixture task is too weak for the risk it claims to retire: it asks for real `find`/`ls`/`git status` prompts only, but the parser failures above require adversarial real prompts with command descriptions, embedded prompt-marker text, and multi-line Bash. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:223, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:226, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:228, /tmp/contrarian-2026-05-25/preview-shape.txt:70, /tmp/contrarian-2026-05-25/preview-shape.txt:73.

### PW-2 — Default-on plus restart-only disable is a bad security posture.

The spec changes every existing mission config from human-gated Bash prompts to automatic approval because `auto_approve_readonly` defaults to `True`, while also declaring a runtime kill-switch out of scope. That means the first bad policy decision is discovered in a live lane and the documented control is mission config plus restart, not an immediate stop. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:20, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:36, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:169, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:174, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:176, /tmp/contrarian-2026-05-25/mission_config_schema.txt:12.

### PW-3 — The task-drain ownership is incoherent with the actual watcher API.

The spec says a handler-owned task set must be cancelled and drained on watcher `stop()`, but `PermissionWatcher.stop()` only cancels its own polling task and has no hook for handler-owned tasks. The live server currently calls `activity_wall.stop()` and then `perm_watcher.stop()` in lifespan teardown, so the spec is assigning lifecycle responsibility to an object that cannot own it. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:157, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:159, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:161, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:239, megalodon_ui/permission_watcher.py:241, megalodon_ui/permission_watcher.py:249, megalodon_ui/server.py:1410, megalodon_ui/server.py:1411.

Broadening justification: I read the raw watcher/server lifecycle because the context slice did not include `PermissionWatcher.stop()`, and this finding depends on the concrete shutdown owner. Citations: megalodon_ui/permission_watcher.py:241, megalodon_ui/server.py:1410.

### PW-4 — The “un-stallable” goal is contradicted by the reused compound detector.

The goal says benign read-only exploration should be un-stallable regardless of tool choice, but the reused compound detector rejects raw `|`, `(`, and `)` anywhere in the preview. That stalls ordinary read-only search syntax such as alternation in `grep -E 'foo|bar'`, ripgrep regex groups, and documented `tree -I 'dir1|dir2'` patterns, so the policy is simultaneously too permissive for dangerous flags and too blunt for common safe read-only queries. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:18, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:100, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:101, /tmp/contrarian-2026-05-25/approval_rules.txt:10, /tmp/contrarian-2026-05-25/approval_rules.txt:11, /tmp/contrarian-2026-05-25/approval_rules.txt:42, https://www.mankier.com/1/tree:82, https://www.mankier.com/1/tree:84.

## 3. Worth Reconsidering
(Not wrong today, but will cause pain as the project evolves. Reference roadmap/goals. Appended as discovered.)

### WR-1 — The static allowlist is the wrong long-term shape, and the spec already contains the reason.

The spec frames the static allowlist as the smallest first cut, but each allowlisted Unix head is its own mini-language with execution flags, write flags, config files, pager behavior, symlink behavior, and version drift. The current spec already had to special-case `find` and `git`, and it still missed `rg`, `fd`, `tree`, post-subcommand git flags, preview lossiness, and filesystem scope. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:105, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:107, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:127, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:249, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:251.

The §9 MCP direction is less structurally brittle because it moves agents from raw shell strings to typed, pre-authorized tools, and the roadmap explicitly names `survey_files` as the exploration replacement. Keeping the shell allowlist as the strategic shape preserves the exact brittle allowlist-pattern matching that §9 says should be replaced. Citations: docs/v10-readiness-plan.md:230, docs/v10-readiness-plan.md:235, docs/v10-readiness-plan.md:237, docs/v10-readiness-plan.md:240, docs/v10-readiness-plan.md:244, docs/v10-readiness-plan.md:245, docs/v10-readiness-plan.md:257, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:242, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:247.

Broadening justification: I read `docs/v10-readiness-plan.md` §9 because the target spec explicitly defers the static-allowlist vs. MCP `survey_files` judgment to this external review. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:249, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:253, docs/v10-readiness-plan.md:228.

---

## Verdict
spec-should-be-redone

The design fails its own security boundary: it approves execution, writes, secret reads, and commands whose dangerous suffixes are hidden by the canonical preview extractor. It also does not wire correctly into the existing `on_change` consumer. Citations: docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:18, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:25, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:95, docs/superpowers/specs/2026-05-25-readonly-auto-approver-design.md:152, /tmp/contrarian-2026-05-25/preview-shape.txt:52, /tmp/contrarian-2026-05-25/preview-shape.txt:53, megalodon_ui/activity_wall.py:129.
