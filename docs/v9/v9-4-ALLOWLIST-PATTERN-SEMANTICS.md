# v9.4: Claude `--allowedTools` Pattern Semantics

**Purpose**: Document the exact behavior of Claude CLI's `--allowedTools` flag pattern matching, required for Tasks 3.3 and 3.4 (approval-rules → spawn pattern extraction).

**Source**: Official Claude Code documentation (https://code.claude.com/docs/en/permissions)

---

## Section 1: Pattern Syntax

### Overview

Claude Code uses a permission rule system with the syntax:

```
Tool(specifier)  or  Tool
```

Permission rules are evaluated in order: **deny → ask → allow**. The first matching rule wins.

### Rule Types

| Rule Form | Meaning | Example |
|-----------|---------|---------|
| `Bash` or `Bash(*)` | Match ALL Bash commands (no specifier) | `Bash` matches every shell command |
| `Bash(specifier)` | Match specific Bash patterns | `Bash(npm run build)` matches that exact command |
| `Bash(prefix *)` | Match commands starting with prefix | `Bash(npm run *)` matches any npm run script |
| `Bash(prefix:*)` | Equivalent to `prefix *` (trailing wildcard) | `Bash(npm:*)` matches any npm command |
| `Read(path)` | Match file reads (gitignore syntax) | `Read(.env)`, `Read(/src/**/*.ts)` |
| `Edit(path)` | Match file edits (gitignore syntax) | `Edit(/docs/**)` |
| `WebFetch(domain:example.com)` | Match fetch requests to domain | `WebFetch(domain:github.com)` |

### Wildcard Semantics

**Single `*` wildcard**:
- Matches **any sequence of characters**, including spaces
- Can appear **at any position** in a Bash pattern (beginning, middle, end)
- Examples:
  - `Bash(npm *)` → matches `npm run build`, `npm install`, `npm install @types/node`
  - `Bash(* install)` → matches `npm install`, `pip install`, `npm -g install`
  - `Bash(git * main)` → matches `git checkout main`, `git push origin main`, `git merge main`

**Word boundary with trailing `*`**:
- A space before `*` at the end enforces word boundary: `Bash(ls *)` vs `Bash(ls*)`
- `Bash(ls *)` → matches `ls -la`, `ls -la src/` (requires space after `ls`)
- `Bash(ls*)` → matches `ls -la` AND `lsof` (no word boundary)
- The `:*` suffix (`Bash(ls:*)`) is equivalent to `Bash(ls *)`

**Multiple wildcards**:
- A pattern can contain multiple `*` characters: `Bash(git * -- *)` is valid
- Each `*` independently matches any sequence of characters

### Pattern Positions

All positions in a Bash command are matchable:

```
Bash(prefix *)          → matches commands starting with prefix
Bash(prefix * suffix)   → matches commands with prefix...suffix pattern
Bash(* suffix)          → matches commands ending with suffix
Bash(* middle *)        → matches commands containing middle anywhere
```

---

## Section 2: Worked Examples

### Bash Pattern Matching

| Pattern | Matches | Does NOT Match | Reason |
|---------|---------|----------------|--------|
| `Bash(npm run build)` | `npm run build` | `npm run build --env=prod` | Requires exact match (no wildcards) |
| `Bash(npm run *)` | `npm run build`, `npm run test`, `npm run build --env=prod` | `npm install` | Only matches `npm run ...` |
| `Bash(npm *)` | `npm install`, `npm run build`, `npm -g install @types/node` | `npx jest` | Requires space after `npm` (word boundary) |
| `Bash(npm:*)` | `npm install`, `npm run build`, `npm -g install @types/node` | `npx jest` | Equivalent to `npm *` (trailing wildcard with `:`) |
| `Bash(git * main)` | `git checkout main`, `git push origin main`, `git merge main` | `git checkout develop` | Must contain `main` at end |
| `Bash(* --version)` | `npm --version`, `node --version`, `python --version` | `npm --version-check` | Exact match of ` --version` required |
| `Bash(ls *)` | `ls -la`, `ls src/` | `lsof`, `ls-la` | Word boundary: requires space after `ls` |
| `Bash(ls*)` | `ls -la`, `lsof`, `ls-la` | (none of above fail) | No word boundary: `*` directly after `ls` |
| `Bash(curl http://*)` | `curl http://example.com/x` | `curl -s http://example.com` (⚠️ see section 3) | Simple glob match—but **very fragile** (see warnings below) |

### File Path Patterns (gitignore semantics)

| Pattern | Matches | Does NOT Match |
|---------|---------|----------------|
| `Read(.env)` | `.env` in current directory or any subdirectory | `.env` in parent directory |
| `Read(~/.zshrc)` | `/Users/alice/.zshrc` (home directory file) | `/Users/alice/proj/.zshrc` |
| `Read(/src/**)` | All files under `<project>/src/` recursively | Files outside `src/` |
| `Read(//Users/alice/secrets/**)` | Any file under `/Users/alice/secrets/` (absolute path) | Files elsewhere on filesystem |
| `Edit(/docs/**)` | All files under `<project>/docs/` | All files in `/docs/` (root level) |

**Note**: Path patterns use gitignore semantics:
- `*` matches files in a single directory
- `**` matches files recursively across directories
- Patterns are relative to project root by default (prefix `/`)
- Prefix `//` for absolute filesystem paths
- Prefix `~` for home directory

### URL Pattern Semantics (WebFetch)

| Pattern | Matches |
|---------|---------|
| `WebFetch(domain:github.com)` | `https://github.com/user/repo`, `https://api.github.com/v1/user` |
| `WebFetch(domain:example.com)` | `https://example.com`, `http://example.com`, `https://sub.example.com` |

**Domain matching is domain-aware**: subdomains match if they are part of the specified domain.

---

## Section 3: Bash Argument Matching Pitfalls & Fragility

### ⚠️ Warning: Position-Sensitive Arguments

Bash patterns **do not parse command structure**—they perform **literal substring/glob matching**. This creates fragility:

**Pattern does NOT reliably constrain**:
- Option order: `Bash(curl http://example.com)` does NOT block `curl -L http://example.com` or `curl -X GET http://example.com` (option before URL)
- Protocol: `Bash(curl http://*)` does NOT block `curl https://example.com` (different protocol)
- Redirects: `Bash(curl http://github.com/*)` does NOT block `curl -L http://bit.ly/xyz` (redirect target not visible)
- Variables: `Bash(curl http://github.com/*)` does NOT block `URL=http://github.com && curl $URL` (variable expansion)
- Whitespace: `Bash(curl http://github.com/*)` does NOT block `curl  http://github.com/` (extra spaces)

**Recommendation** (from official docs):
> For more reliable URL filtering, consider:
> - **Restrict Bash network tools**: deny `Bash(curl)` / `Bash(wget)`, use `WebFetch(domain:...)` instead
> - **Use PreToolUse hooks**: validate URLs in Bash at runtime
> - **Add CLAUDE.md guidance**: shape what Claude tries (not enforcement)

### Compound Commands

**Recognized shell operators**: `&&`, `||`, `;`, `|`, `|&`, `&`, newlines

When a compound command is approved with "Yes, don't ask again":
- Claude Code **parses the shell structure** and saves **separate rules for each subcommand**
- Example: approving `git status && npm test` saves TWO rules:
  - One for `git status`
  - One for `npm test`
- Future invocations of either command alone are recognized
- **Up to 5 rules may be saved for a single compound command**

A rule like `Bash(safe-cmd *)` **does NOT grant permission** to run `safe-cmd && other-cmd`—each subcommand must match independently.

### Process Wrappers (Automatically Stripped)

Before matching Bash rules, these wrappers are stripped and do not affect pattern matching:

| Wrapper | Effect |
|---------|--------|
| `timeout`, `time`, `nice`, `nohup`, `stdbuf` | Stripped always |
| `xargs` (bare, no flags) | Stripped; `Bash(grep *)` matches `xargs grep pattern` |
| `xargs -n1 grep pattern` | **NOT stripped** (has flags); treated as `xargs` command, not `grep` |

**NOT stripped** (rules must match wrapper + command):
- `direnv exec`, `devbox run`, `mise exec`, `npx`, `docker exec`
- `watch`, `setsid`, `ionice`, `flock`
- `find -exec` or `find -delete`

Example: `Bash(devbox run *)` matches whatever comes after `run`, including `devbox run rm -rf .`. Write specific rules: `Bash(devbox run npm test)` per approved inner command.

### Built-in Read-Only Commands (No Prompt Required)

These commands execute without prompting in all permission modes:

```
ls, cat, echo, pwd, head, tail, grep, find, wc, which, diff, stat, du, cd
```

Also: read-only forms of `git` (not including `git push`, `git commit`, etc.)

Unquoted globs are permitted for read-only commands: `ls *.ts`, `wc -l src/*.py`.

---

## Section 4: CLI Flag Semantics

### `--allowedTools` and `--disallowedTools` Flags

**Format** (space-separated list of rules):

```bash
claude --allowedTools "Bash(git log *)" "Bash(git diff *)" "Read"
claude --disallowedTools "Bash(rm *)" "Edit"
```

**Behavior**:
- Rules use identical syntax to `settings.json` `permissions.allow` / `permissions.deny`
- CLI flags **override** settings file rules at the **same precedence level** (command-line > project > user)
- A bare tool name like `Bash` removes that tool from the model's context entirely
- A scoped rule like `Bash(rm *)` leaves the tool available but blocks matching calls

**Precedence**:
1. Managed settings (cannot be overridden)
2. CLI flags (`--allowedTools`, `--disallowedTools`)
3. Local project settings (`.claude/settings.local.json`)
4. Shared project settings (`.claude/settings.json`)
5. User settings (`~/.claude/settings.json`)

**Key rule**: If a tool is denied at ANY level, no other level can allow it. A managed settings deny cannot be overridden by `--allowedTools`.

### `-p` (Print Mode) Interaction

In print mode (`-p`), the model runs non-interactively. Permission prompts are not shown to the user. Rules are still evaluated; if a prompt would normally be required, the tool call is **rejected** unless pre-approved via `--allowedTools` or `settings.json` allow rules.

---

## Section 5: Recommended Extraction Strategy for Megalodon

### Goals (Tasks 3.3 & 3.4)

1. Extract patterns from **operator-approved Bash commands**
2. Merge extracted patterns into `--allowedTools` at spawn time
3. Ensure spawned Claude CLI receives correct pattern format

### Extraction Heuristic

**Input**: User-approved shell command (e.g., `curl -s http://localhost:8080/api/events`)

**Extraction strategy**:

1. **Identify command type** (first token):
   - Common spawnable patterns: `npm`, `git`, `curl`, `python`, `node`, `bash`

2. **Extract prefix pattern** (conservative):
   - Prefix-only with wildcard: `Bash(npm *)`
   - Reason: Full argument matching is fragile (option order, whitespace, variables)

3. **Special case: `curl` with localhost**:
   - Pattern: `Bash(curl http://localhost:*)`
   - Reason: URL filtering is unreliable; rely on domain-based restrictions via PreToolUse hooks or WebFetch in the spawned session

4. **Avoid position-sensitive patterns**:
   - ❌ `Bash(curl -s http://localhost:*)`  (fragile: options might reorder)
   - ❌ `Bash(npm run build --env=prod)`  (fragile: not generalizable)
   - ✅ `Bash(npm *)`  (safe: any npm subcommand)

5. **Fallback for unknown commands**:
   - If the command cannot be safely generalized, save the **exact command**:
   - `Bash(echo hello world)` (exact match, no wildcard)

### Example Extractions

| Approved Command | Extracted Pattern | Rationale |
|------------------|-------------------|-----------|
| `npm run build` | `Bash(npm *)` | Prefix pattern; safe generalization |
| `npm install axios` | `Bash(npm *)` | Prefix pattern |
| `git log --oneline` | `Bash(git *)` | Prefix pattern |
| `curl -s http://localhost:8080/api/events` | `Bash(curl http://localhost:*)` | URL-based; avoid option fragility |
| `python script.py --verbose` | `Bash(python *)` | Prefix pattern |
| `echo hello` | `Bash(echo *)` | Prefix pattern |
| `custom-tool --special-flag` | `Bash(custom-tool --special-flag)` | Exact match if unknown command |

### Implementation Checklist

- [ ] Parse approved command into tokens
- [ ] Extract first token (command name)
- [ ] Apply heuristic above
- [ ] Format as `Bash(pattern)`
- [ ] Pass to `--allowedTools` at spawn time
- [ ] Log extracted pattern for audit trail (HISTORY.md)

---

## Section 6: Open Questions for Empirical Testing (Tasks 3.3 & 3.4)

The official documentation does not specify behavior in these edge cases. **Implement tests during Tasks 3.3 & 3.4**:

1. **Multiple `*` in single pattern**:
   - Does `Bash(git * -- *)` work as expected?
   - How does Claude Code tokenize/match this?

2. **`*` in middle vs. end**:
   - Does `Bash(curl http://* http://*)` match `curl http://example.com http://other.com`?
   - Any difference in matching efficiency?

3. **Case sensitivity**:
   - Are Bash commands case-sensitive? (likely yes, but confirm)
   - Are paths case-sensitive on case-insensitive filesystems (macOS)?

4. **Exact match vs. prefix**:
   - When CLI receives `--allowedTools "Bash(npm run build)"`, does it allow ONLY `npm run build` or also `npm run build --env=prod`?
   - Confirm exact-match behavior

5. **Compound commands in spawn**:
   - If we pass `--allowedTools "Bash(git status)" "Bash(npm test)"`, does the spawn correctly parse compounds like `git status && npm test`?
   - Or does it require manual rule splitting?

6. **Relative vs. absolute paths for Read/Edit**:
   - When spawning in `/Users/dave/Documents/Projects/megalodon`, does `Read(./src/*)` work as intended?
   - Or must we always use `/src/*` (project-root-relative)?

7. **PreToolUse hook ordering**:
   - If we set `--allowedTools "Bash(curl *)"` and also provide a PreToolUse hook that denies `curl https://*`, which wins?
   - Confirm hook precedence relative to CLI flags

---

## Summary for Tasks 3.3 & 3.4

**Key findings**:
1. **Pattern format is critical**: `Tool(specifier)` must be exact; wrong format silently no-ops (PM-3)
2. **Wildcard `*` matches any characters** (including spaces, options, URLs), making position-sensitive patterns fragile
3. **Extraction should use prefix patterns** (`Bash(npm *)`) rather than full command strings to avoid brittleness

**Top 3 action items**:
- Extract patterns conservatively (prefix-only with `*`)
- Test exact-match vs. prefix-match behavior empirically
- Implement audit logging of extracted patterns for debugging

---

## References

- [Claude Code: Configure Permissions](https://code.claude.com/docs/en/permissions)
- [Claude Code: CLI Reference](https://code.claude.com/docs/en/cli-reference)
- [Claude Code: Hooks Guide](https://code.claude.com/docs/en/hooks-guide)
