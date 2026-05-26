"""Governor policy-engine test matrix (Task 1.1).

This file IS the spec for ``megalodon_ui/governor/policy.py``. Every deny
vector below must be a passing deny assertion; every benign-allow idiom a
passing allow assertion. The policy is the SECURITY CORE: a wrong "allow" is a
hole; a wrong "deny" stalls real work.

Policy model: allow-by-default, deny-matched-dangerous, per tool family.
``decide`` is pure (no I/O except reading ``.fleet/approval-rules.json``) and
fails closed (any internal exception → deny, category ``governor-error``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.governor import policy
from megalodon_ui.governor.policy import Decision, decide


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A scratch mission/run dir that doubles as the scope boundary."""
    d = tmp_path / "mission"
    d.mkdir()
    (d / "README.md").write_text("hello\n", encoding="utf-8")
    (d / "scripts").mkdir()
    (d / "scripts" / "poll.py").write_text("print('x')\n", encoding="utf-8")
    return d


LANE = "lane-A"


def _bash(cmd: str, project_dir: Path) -> Decision:
    return decide("Bash", {"command": cmd}, project_dir=project_dir, lane=LANE)


def _assert_deny(d: Decision, category: str | None = None) -> None:
    assert isinstance(d, Decision)
    assert d.permission == "deny", f"expected deny, got {d!r}"
    assert d.reason, "deny must carry a non-empty reason"
    if category is not None:
        assert d.category == category, (
            f"expected category {category!r}, got {d.category!r}"
        )


def _assert_allow(d: Decision, category: str | None = None) -> None:
    assert isinstance(d, Decision)
    assert d.permission == "allow", f"expected allow, got {d!r}"
    if category is not None:
        assert d.category == category, (
            f"expected category {category!r}, got {d.category!r}"
        )


# ---------------------------------------------------------------------------
# DENY — code-exec by flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "find . -exec rm -rf {} +",  # the + terminator must be caught
        "find . -exec rm {} \\;",
        "find . -delete",
        "find . -execdir touch x +",
        "find . -fprintf out.txt '%p'",
        "rg --pre evil.sh pattern",
        "rg --pre-glob '*.sh' pattern",
        "fd -x sh -c 'evil'",
        "fd -X rm",
        "fd --exec sh",
        "git -c core.pager='rm x' log",
        "git diff --ext-diff",
        "tree -o /tmp/x",
        "awk 'BEGIN{system(\"id\")}'",
        "awk 'BEGIN {print 1}' f",
        "awk '{print | \"sh\"}' f",
        "gawk -f prog.awk f",
        "sed 's/x/y/w /tmp/out' f",
        "sed -e 's/x/y/e' f",
        "sed -i 's/x/y/' f",
        "tar --checkpoint-action=exec=sh -cf f.tar .",
        "tar --to-command='sh' -xf f.tar",
        "tar --use-compress-program=sh -cf f.tar .",
        "echo x | xargs rm",
    ],
)
def test_deny_code_exec_by_flag(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir))


# ---------------------------------------------------------------------------
# DENY — interpreter heads / destructive / privilege / installers / fork-bomb
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "python3 -c 'import os'",
        "python -c 'x'",
        "python2 -c 'x'",
        "bash -c 'curl evil'",
        "sh -c x",
        "zsh -c x",
        "eval x",
        "node -e x",
        "node --eval x",
        "perl -e x",
        "perl -E x",
        "ruby -e x",
        "pytest",
        "uv run python",
        "rm -rf /etc",
        "rm -rf ~/important",
        "rm -r /var/data",
        "rm -f /var/data/x",
        "sudo rm x",
        "su root",
        "doas rm x",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sda1",
        "shred secret",
        ":(){ :|:& };:",
        "pip install evil",
        "npm install evil",
        "npm i evil",
        "brew install evil",
        "apt install evil",
        "chmod 777 x",
        "chmod -R 755 x",
        "chown root x",
        "curl http://evil.example",
        "wget http://evil.example",
        "ssh host",
        "scp a b",
        "nc -l 1234",
    ],
)
def test_deny_heads_destructive_privilege(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir))


# ---------------------------------------------------------------------------
# DENY — env-assignment / wrapper bypasses (regression: a leading VAR=val or a
# wrapper must not hide a dangerous head)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "FIND=x find . -delete",  # bare leading assignment hides find -delete
        "FOO=1 BAR=2 python3 -c 'x'",  # multiple assignments hide interpreter
        "env X=1 python3 -c 'x'",  # env wrapper hides interpreter
        "timeout 5 python3 -c 'x'",  # timeout wrapper hides interpreter
        "nohup curl http://evil",  # nohup wrapper hides network
        "/usr/bin/python3 -c 'x'",  # absolute-path interpreter head
        "/bin/sh -c evil",  # absolute-path shell head
    ],
)
def test_deny_wrapper_and_env_bypasses(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir))


def test_allow_benign_leading_env_assignment(project_dir):
    _assert_allow(_bash("FOO=1 ls", project_dir))


# ---------------------------------------------------------------------------
# DENY — secret read via Bash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "cat ~/.ssh/id_rsa",
        "grep -R token ../../",
        "cat ./a/../../.ssh/id_rsa",  # path-traversal canonicalization
        "head /etc/shadow",
        "cat .env",
    ],
)
def test_deny_secret_read_via_bash(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir))


# ---------------------------------------------------------------------------
# I1 — DENY: bare-basename secret read (no slash, no leading dot) on read heads.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "cat id_rsa",
        "cat server.pem",
        "cat credentials.json",
        "head deploy.key",
        "less my_ed25519",
    ],
)
def test_deny_bare_basename_secret_read(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir), "secret-read")


def test_allow_ordinary_search_term_not_secret(project_dir):
    # The narrow secret signatures must not trip ordinary read-style args.
    _assert_allow(_bash("grep foo file.txt", project_dir))
    _assert_allow(_bash("cat README.md", project_dir))


# ---------------------------------------------------------------------------
# C1 — DENY: grouping/keyword prefixes must not hide the real head. These all
# returned allow before the segmenter/peeler fix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "(rm -rf /etc)",  # subshell hides destructive rm
        "(sudo rm x)",  # subshell hides privilege floor
        "{ curl http://evil; }",  # brace group hides network
        "time python3 -c 1",  # time keyword prefix
        "exec python3 -c 1",  # exec keyword prefix
        "command python3 -c 1",  # command keyword prefix
        "! python3 -c 1",  # ! negation prefix
        "(python3 -c 1)",  # subshell + interpreter
    ],
)
def test_deny_grouping_and_keyword_prefix_bypasses(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir))


def test_deny_subshell_root_destructive_hits_floor(project_dir):
    # (rm -rf /) must hit the NON-OVERRIDABLE root-destructive floor.
    _write_rules(project_dir, ["Bash(rm:*)"])  # even with an rm rule present
    d = _bash("(rm -rf /)", project_dir)
    _assert_deny(d, "bash-root-destructive")


def test_allow_builtin_cd_prefix_benign(project_dir):
    # `builtin cd x` peels to `cd x` — a benign builtin, allowed.
    _assert_allow(_bash("builtin cd x", project_dir))


# ---------------------------------------------------------------------------
# CRITICAL — backslash-newline line-continuation must be spliced out (bash
# semantics) BEFORE tokenizing, so the governor's head matches what bash runs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "r\\\nm -rf /",  # → rm -rf /   (root-destructive floor)
        "sud\\\no rm x",  # → sudo rm x  (privilege floor)
        "py\\\nthon3 -c 'import os'",  # → python3 -c ...
        "cur\\\nl http://evil",  # → curl http://evil
        "rm -rf /et\\\nc",  # mid-token continuation → rm -rf /etc
        "$\\\n(python3 -c 'x')",  # split `$(` must NOT reassemble past sub-check
    ],
)
def test_deny_backslash_newline_continuation(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir))


def test_backslash_newline_substitution_denies(project_dir):
    # The reassembled `$(` is caught as substitution after splicing.
    _assert_deny(_bash("echo $\\\n(id)", project_dir), "bash-substitution")


def test_normal_escapes_still_behave(project_dir):
    # Non-newline escapes are preserved verbatim (not spliced): `echo \;` and a
    # find with the escaped `\;` terminator behave exactly as before.
    _assert_allow(_bash("echo \\;", project_dir))
    _assert_deny(
        _bash("find . -exec rm {} \\;", project_dir)
    )  # find -exec still denied


# ---------------------------------------------------------------------------
# MINOR — sed/awk inline PROGRAM operand must not be misread as a path (a `/x/`
# program starting with `/` previously caused a spurious out-of-scope deny).
# ---------------------------------------------------------------------------


def test_allow_sed_program_not_path(project_dir):
    _assert_allow(_bash("sed -n '/x/{p}' README.md", project_dir))


def test_allow_awk_program_not_path(project_dir):
    (project_dir / "data.txt").write_text("a b\n", encoding="utf-8")
    _assert_allow(_bash("awk '{print $1}' data.txt", project_dir))


def test_sed_write_command_still_denies(project_dir):
    _assert_deny(_bash("sed 's/x/y/w /tmp/out' f", project_dir))


def test_actual_file_operand_secret_check_unaffected(project_dir):
    # The secret check on a REAL file operand (not the program) still fires.
    _assert_deny(_bash("cat ~/.ssh/id_rsa", project_dir), "secret-read")


# ---------------------------------------------------------------------------
# C2 — allow-override must NOT leak across segments: a rule for one head cannot
# flip a different segment's deny.
# ---------------------------------------------------------------------------


def test_override_does_not_leak_across_segments(project_dir):
    _write_rules(project_dir, ["Bash(ls:*)"])  # rule for ls only
    d = _bash("ls && curl http://evil", project_dir)
    _assert_deny(d, "bash-network")  # curl segment stays denied


def test_override_flips_matching_segment(project_dir):
    # A rule that DOES match the denied segment still flips correctly.
    _write_rules(project_dir, ["Bash(curl:*)"])
    d = _bash("ls && curl http://evil", project_dir)
    _assert_allow(d, "allow-override")


# ---------------------------------------------------------------------------
# M1 — case-insensitive FS: uppercase heads must still be denied.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "Python3 -c 1",
        "SUDO rm x",
        "CURL http://evil",
        "RM -rf /etc",
    ],
)
def test_deny_uppercase_head_case_insensitive(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir))


# ---------------------------------------------------------------------------
# DENY — substitution / parse-fail
# ---------------------------------------------------------------------------


def test_deny_command_substitution_dollar(project_dir):
    _assert_deny(_bash("echo $(python3 -c 'x')", project_dir), "bash-substitution")


def test_deny_command_substitution_backtick(project_dir):
    _assert_deny(_bash("cat `id`", project_dir), "bash-substitution")


def test_deny_process_substitution(project_dir):
    _assert_deny(_bash("diff <(ls) <(ls)", project_dir), "bash-substitution")


def test_deny_parse_error(project_dir):
    # Unterminated quote → shlex.split raises ValueError → fail safe.
    _assert_deny(_bash("echo 'unterminated", project_dir), "bash-parse-error")


# ---------------------------------------------------------------------------
# DENY — Bash write-redirect targets enforce the SAME write-target lock as the
# Write tool (anti-tamper / secret / scope). A `>`/`>>` redirect must not be a
# back door to overwriting the governor's own config.
# ---------------------------------------------------------------------------


def test_deny_redirect_anti_tamper_governor_settings(project_dir):
    d = _bash("echo '{}' > .claude/governor-settings.json", project_dir)
    _assert_deny(d, "anti-tamper")


def test_deny_redirect_anti_tamper_governor_hook(project_dir):
    d = _bash("cat x > governor_hook.py", project_dir)
    _assert_deny(d, "anti-tamper")


def test_deny_redirect_append_anti_tamper_governor_settings(project_dir):
    d = _bash("echo x >> .claude/governor-settings.json", project_dir)
    _assert_deny(d, "anti-tamper")


def test_allow_redirect_in_scope_non_critical(project_dir):
    # In-scope, non-critical destination still ALLOWS (no over-blocking).
    _assert_allow(_bash("echo x > README.md", project_dir))


# ---------------------------------------------------------------------------
# DENY — mutation heads (cp/mv/tee/truncate/touch/mkdir/ln/install) route their
# DESTINATIONS through the same write-target lock as `>`/Write and their SOURCES
# through the same secret-read floor as `cat`. Confirmed exploit vectors that
# previously returned `bash-ok`. (See policy._MUTATION_HEADS.)
# ---------------------------------------------------------------------------


# The 8 verified-exploit commands and the floor category each must now carry.
@pytest.mark.parametrize(
    "cmd,category",
    [
        # Secret exfiltration via copy — source is a private key.
        ("cp ~/.ssh/id_rsa /tmp/stolen", "secret-read"),
        # Symlink laundering an out-of-scope file into scope (target /etc/passwd
        # is not a secret-signature file, so the accurate floor is out-of-scope).
        ("ln -s /etc/passwd /tmp/leak", "out-of-scope"),
        # Out-of-scope appends/writes via tee.
        ("tee -a /etc/sudoers", "write-out-of-scope"),
        ("echo x | tee /etc/passwd", "write-out-of-scope"),
        # Out-of-scope overwrite / persistence via cp/mv.
        ("cp secret /etc/passwd", "write-out-of-scope"),
        ("mv x /etc/cron.d/job", "write-out-of-scope"),
        # Destructive out-of-scope truncate.
        ("truncate -s 0 /etc/hosts", "write-out-of-scope"),
        # Out-of-scope creation via touch / mkdir.
        ("touch /etc/evil", "write-out-of-scope"),
        ("mkdir /etc/evil", "write-out-of-scope"),
    ],
)
def test_deny_mutation_head_exploits(cmd, category, project_dir):
    _assert_deny(_bash(cmd, project_dir), category)


@pytest.mark.parametrize(
    "cmd",
    [
        # Secret SOURCE operands on every read-source-bearing mutation head.
        "cp ~/.ssh/id_rsa /tmp/x",
        "cp id_rsa /tmp/x",  # bare-basename secret source
        "mv ~/.ssh/id_ed25519 /tmp/x",
        "install ~/.ssh/id_rsa /tmp/x",
        "ln -s ~/.ssh/id_rsa /tmp/leak",  # symlink launder via ~
        "cp server.pem /tmp/x",  # *.pem source
        "cp /home/u/.env /tmp/x",  # .env source
    ],
)
def test_deny_mutation_secret_source(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir), "secret-read")


@pytest.mark.parametrize(
    "cmd",
    [
        "tee /etc/passwd",
        "tee -a /etc/sudoers /var/spool/cron",  # multiple dests, one bad
        "install -m 600 README.md /etc/evil",  # value-flag then bad dest
        "ln README.md /etc/evil",  # hard-link write into /etc
        "mkdir -p /etc/evil/nested",
        "touch -- /etc/evil",  # `--` end-of-opts then bad target
    ],
)
def test_deny_mutation_out_of_scope_dest(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir), "write-out-of-scope")


def test_deny_mutation_anti_tamper_via_cp(project_dir):
    # cp into the governor's own config is anti-tamper (write-target lock).
    d = _bash("cp x .claude/governor-settings.json", project_dir)
    _assert_deny(d, "anti-tamper")


def test_deny_mutation_write_secret_dest(project_dir):
    # Writing TO a secret path (in-scope but secret-signature) is write-secret.
    d = _bash("cp README.md id_rsa", project_dir)
    _assert_deny(d, "write-secret")


def test_mutation_write_floor_not_overridable(project_dir):
    # An operator rule must NOT be able to flip an out-of-scope mutation write.
    _write_rules(project_dir, ["Bash(cp:*)"])
    _assert_deny(_bash("cp secret /etc/passwd", project_dir), "write-out-of-scope")


def test_mutation_secret_source_floor_not_overridable(project_dir):
    # secret-read floor on a mutation source is non-overridable too.
    _write_rules(project_dir, ["Bash(cp:*)"])
    _assert_deny(_bash("cp ~/.ssh/id_rsa /tmp/x", project_dir), "secret-read")


def test_deny_mutation_fail_closed_on_unparseable(project_dir):
    # A copy/move with too few operands is malformed → fail closed (deny),
    # never a silent allow.
    _assert_deny(_bash("cp /etc/passwd", project_dir), "write-out-of-scope")


# `-t`/`--target-directory <dir>` for cp/mv/install makes <dir> the WRITE
# destination and turns every positional operand into a SOURCE. The target dir
# MUST be scope-checked; previously it was consumed as an opaque value-flag and
# discarded, so the out-of-scope write slipped past (verified hole).
@pytest.mark.parametrize(
    "cmd",
    [
        "cp -t /etc/ a b",
        "cp -t /etc/cron.d f1 f2",
        "mv -t /etc/ a b",
        "install -t /etc/cron.d f1 f2",
        "cp --target-directory=/etc/cron.d f1 f2",
        "cp --target-directory /etc x y",
        "mv --target-directory=/etc/cron.d a b",
        "install -t /etc/evil README.md",
    ],
)
def test_deny_mutation_target_directory_out_of_scope(cmd, project_dir):
    _assert_deny(_bash(cmd, project_dir), "write-out-of-scope")


@pytest.mark.parametrize(
    "cmd",
    [
        "cp -t build a b",  # in-scope target dir, sources de-scoped at cwd
        "mv -t build a b",
        "cp --target-directory=build f1 f2",
        "cp --target-directory build x y",
        "install -t build README.md",
        "cp -t /tmp a b",  # /tmp scratch is allowed
    ],
)
def test_allow_mutation_target_directory_in_scope(cmd, project_dir):
    _assert_allow(_bash(cmd, project_dir))


def test_deny_mutation_target_directory_single_operand_still_denies(project_dir):
    # The previously-already-denied single-operand path (`cp -t /etc a`) must
    # still deny: /etc target is out of scope regardless of source count.
    _assert_deny(_bash("cp -t /etc a", project_dir), "write-out-of-scope")


# ---------------------------------------------------------------------------
# ALLOW — legitimate in-scope mutation usage must still pass (no fleet-bricking).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "cp README.md copy.txt",
        "cp -r scripts build",  # recursive copy within scope
        "mv README.md NOTES.md",
        "mkdir build",
        "mkdir -p build/nested",
        "touch out.log",
        "truncate -s 0 out.log",  # value-flag consumed, in-scope target
        "tee out.log",
        "echo x | tee build.log",
        "ln -s README.md link.md",  # symlink within scope, non-secret target
        "install -m 600 README.md out.bin",
        "cp README.md /tmp/scratch.txt",  # /tmp scratch is allowed
    ],
)
def test_allow_mutation_in_scope(cmd, project_dir):
    _assert_allow(_bash(cmd, project_dir))


# ---------------------------------------------------------------------------
# DENY — native read / write / spawn
# ---------------------------------------------------------------------------


def test_deny_read_ssh_key(project_dir):
    d = decide(
        "Read", {"file_path": "~/.ssh/id_rsa"}, project_dir=project_dir, lane=LANE
    )
    _assert_deny(d, "secret-read")


def test_deny_read_traversal_out_of_scope(project_dir):
    d = decide(
        "Read", {"file_path": "../../etc/passwd"}, project_dir=project_dir, lane=LANE
    )
    _assert_deny(d)


def test_deny_read_dotenv(project_dir):
    (project_dir / ".env").write_text("SECRET=1\n", encoding="utf-8")
    d = decide(
        "Read",
        {"file_path": str(project_dir / ".env")},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_deny(d, "secret-read")


def test_deny_write_outside_repo(project_dir, tmp_path):
    outside = tmp_path / "elsewhere" / "x.txt"
    d = decide(
        "Write",
        {"file_path": str(outside), "content": "x"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_deny(d, "write-out-of-scope")


def test_deny_write_anti_tamper_governor_settings(project_dir):
    d = decide(
        "Write",
        {"file_path": str(project_dir / "governor-settings.json"), "content": "{}"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_deny(d, "anti-tamper")


def test_deny_write_anti_tamper_governor_hook(project_dir):
    d = decide(
        "Write",
        {"file_path": str(project_dir / "governor_hook.py"), "content": "x"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_deny(d, "anti-tamper")


def test_deny_notebookedit_outside_repo(project_dir, tmp_path):
    outside = tmp_path / "elsewhere" / "nb.ipynb"
    d = decide(
        "NotebookEdit",
        {"notebook_path": str(outside), "new_source": "x"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_deny(d)


def test_deny_edit_secret(project_dir):
    d = decide(
        "Edit",
        {
            "file_path": str(project_dir / "deploy.pem"),
            "old_string": "a",
            "new_string": "b",
        },
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_deny(d, "write-secret")


def test_deny_task_spawn(project_dir):
    d = decide("Task", {"prompt": "do thing"}, project_dir=project_dir, lane=LANE)
    _assert_deny(d, "subagent-spawn")


def test_deny_agent_spawn(project_dir):
    d = decide("Agent", {"prompt": "do thing"}, project_dir=project_dir, lane=LANE)
    _assert_deny(d, "subagent-spawn")


def test_deny_webfetch_empty_allowlist(project_dir):
    d = decide(
        "WebFetch",
        {"url": "https://evil.example"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_deny(d, "network-host")


# ---------------------------------------------------------------------------
# ALLOW — benign exploration + native reads + inert
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "find . 2>/dev/null",
        "grep -E 'foo|bar' .",
        "ls | head",
        "find . | wc -l",
        "cat README.md",
        "git log --oneline -5",
        "git status",
        "git diff",
        "scripts/poll.py",
        "echo hello",
        "head -5 README.md",
        "wc -l README.md",
        "sort README.md | uniq",
        "ls -la",
        "stat README.md",
    ],
)
def test_allow_benign_exploration(cmd, project_dir):
    _assert_allow(_bash(cmd, project_dir))


def test_allow_read_inside_scope(project_dir):
    d = decide(
        "Read",
        {"file_path": str(project_dir / "README.md")},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_allow(d)


def test_allow_grep_inside_scope(project_dir):
    d = decide(
        "Grep",
        {"pattern": "x", "path": str(project_dir)},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_allow(d)


def test_allow_glob_inside_scope(project_dir):
    d = decide(
        "Glob",
        {"pattern": "**/*.py", "path": str(project_dir)},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_allow(d)


def test_allow_write_inside_scope(project_dir):
    d = decide(
        "Write",
        {"file_path": str(project_dir / "notes.txt"), "content": "x"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_allow(d)


def test_allow_todowrite(project_dir):
    d = decide("TodoWrite", {"todos": []}, project_dir=project_dir, lane=LANE)
    _assert_allow(d, "inert")


def test_allow_askuserquestion(project_dir):
    d = decide("AskUserQuestion", {"questions": []}, project_dir=project_dir, lane=LANE)
    _assert_allow(d, "inert")


def test_allow_exitplanmode(project_dir):
    d = decide("ExitPlanMode", {"plan": "x"}, project_dir=project_dir, lane=LANE)
    _assert_allow(d, "inert")


def test_webfetch_allowlisted_host(project_dir):
    """When the host is on the allowlist the fetch is allowed (testable hook)."""
    d = policy.decide_webfetch(
        "https://api.allowed.example/x", allowlist={"api.allowed.example"}
    )
    _assert_allow(d)


def test_webfetch_non_allowlisted_host(project_dir):
    d = policy.decide_webfetch(
        "https://evil.example/x", allowlist={"api.allowed.example"}
    )
    _assert_deny(d, "network-host")


def test_webfetch_subdomain_allowed(project_dir):
    """A subdomain of an allowlisted host is allowed."""
    d = policy.decide_webfetch(
        "https://docs.allowed.example/x", allowlist={"allowed.example"}
    )
    _assert_allow(d)


def test_webfetch_subdomain_suffix_not_substring(project_dir):
    """Suffix matching is on a dot boundary — `notallowed.example` is NOT a sub."""
    d = policy.decide_webfetch(
        "https://evilallowed.example/x", allowlist={"allowed.example"}
    )
    _assert_deny(d, "network-host")


def test_webfetch_malformed_url_denied(project_dir):
    d = policy.decide_webfetch("not a url ::::", allowlist={"allowed.example"})
    _assert_deny(d, "network-host")


# ---------------------------------------------------------------------------
# Committed host-allowlist loader (governor-hosts.txt)
# ---------------------------------------------------------------------------


def test_load_host_allowlist_parses_committed_file():
    """The shipped governor-hosts.txt loads, lowercased, comments/blanks dropped."""
    hosts = policy._load_host_allowlist()
    assert "github.com" in hosts
    assert "docs.python.org" in hosts
    assert "pypi.org" in hosts
    # No comment / blank artefacts leaked in.
    assert all(h and not h.startswith("#") for h in hosts)
    assert all(h == h.lower().strip() for h in hosts)


def test_load_host_allowlist_missing_file_deny_all(monkeypatch, tmp_path):
    """A missing hosts file yields an empty allowlist (deny-all)."""
    monkeypatch.setattr(policy, "_HOSTS_FILE", tmp_path / "does-not-exist.txt")
    policy._load_host_allowlist.cache_clear()
    try:
        assert policy._load_host_allowlist() == frozenset()
    finally:
        policy._load_host_allowlist.cache_clear()


def test_load_host_allowlist_parsing(monkeypatch, tmp_path):
    """Comments, blanks, surrounding whitespace and case are normalised away."""
    f = tmp_path / "hosts.txt"
    f.write_text(
        "# comment\n\n  Docs.Python.Org  \nexample.com\n   # indented comment\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(policy, "_HOSTS_FILE", f)
    policy._load_host_allowlist.cache_clear()
    try:
        hosts = policy._load_host_allowlist()
        assert hosts == frozenset({"docs.python.org", "example.com"})
    finally:
        policy._load_host_allowlist.cache_clear()


def test_decide_webfetch_uses_committed_allowlist(project_dir):
    """`decide` wires WebFetch through the committed allowlist (github.com OK)."""
    policy._load_host_allowlist.cache_clear()
    d = decide(
        "WebFetch",
        {"url": "https://github.com/anthropics/anthropic-sdk-python"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_allow(d)


def test_decide_webfetch_denies_non_allowlisted(project_dir):
    policy._load_host_allowlist.cache_clear()
    d = decide(
        "WebFetch",
        {"url": "https://evil.example/x"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_deny(d, "network-host")


def test_decide_websearch_uses_committed_allowlist(project_dir):
    """WebSearch with a query (no url) hits the allowlist via the query string."""
    policy._load_host_allowlist.cache_clear()
    d = decide(
        "WebSearch",
        {"query": "https://pypi.org/project/requests"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_allow(d)


def test_decide_websearch_plain_text_query_denied(project_dir):
    """A PLAIN-TEXT WebSearch query (the normal case) is denied — documents intent.

    ``decide`` routes WebSearch through ``decide_webfetch(tin["query"])``, which
    feeds the string straight to ``urlparse``. A real search string like
    ``"how to use asyncio"`` has no scheme/host, so ``urlparse(...).hostname`` is
    empty and the allowlist check denies it (category ``network-host``). The
    only WebSearch case that can ALLOW is a query that happens to be shaped like
    a URL on the host allowlist (see the test above). The sibling test using a
    URL-shaped query gives false confidence that ordinary searches pass; this
    pins the actual behavior — the overwhelmingly common plain-text query is
    DENIED. (If WebSearch should adjudicate by intent rather than by accidental
    URL shape, that is a deliberate policy change, and this test will flag it.)
    """
    policy._load_host_allowlist.cache_clear()
    d = decide(
        "WebSearch",
        {"query": "how to use asyncio"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_deny(d, "network-host")


# ---------------------------------------------------------------------------
# Unknown future tool
# ---------------------------------------------------------------------------


def test_unknown_tool_dangerous_denied(project_dir):
    """A genuinely-unknown tool (e.g. a fabricated MCP tool) is denied (fail-closed).

    Regression for the confirmed exploit: a fabricated tool carrying a
    destructive payload used to be allowed-by-default. The default for unknown
    tools is now DENY.
    """
    d = decide(
        "mcp__evil__shell",
        {"command": "rm -rf /"},
        project_dir=project_dir,
        lane=LANE,
    )
    _assert_deny(d, "unknown-tool-deny")


def test_unknown_tool_plain_denied(project_dir):
    d = decide("SomeBrandNewTool", {"foo": "bar"}, project_dir=project_dir, lane=LANE)
    _assert_deny(d, "unknown-tool-deny")


def test_inert_allowed_tool_in_unknown_branch(project_dir, monkeypatch):
    """A tool in _INERT_ALLOWED_TOOLS that reaches the unknown branch is allowed.

    The known inert tools (TodoWrite/AskUserQuestion/ExitPlanMode) are already
    short-circuited earlier as category `inert`. _INERT_ALLOWED_TOOLS is the
    last-line allowlist for any of those control tools that — should the earlier
    short-circuit ever be removed — would otherwise fall into the unknown-deny
    branch. We simulate that by emptying _INERT_TOOLS so the inert tool reaches
    the unknown branch, and assert it is allowed (category `inert-tool`) with a
    WARN flag for classification.
    """
    monkeypatch.setattr(policy, "_INERT_TOOLS", frozenset())
    d = decide("TodoWrite", {"todos": []}, project_dir=project_dir, lane=LANE)
    _assert_allow(d, "inert-tool")
    assert "warn" in d.reason.lower()


def test_inert_allowed_set_membership():
    """The inert-allowed set covers at least the audited control tools."""
    assert {"TodoWrite", "AskUserQuestion", "ExitPlanMode"} <= set(
        policy._INERT_ALLOWED_TOOLS
    )


# ---------------------------------------------------------------------------
# Operator allow-override
# ---------------------------------------------------------------------------


def _write_rules(project_dir: Path, patterns: list[str]) -> None:
    fleet = project_dir / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    rules = [
        {
            "pattern": p,
            "added_at_utc": "2026-01-01T00:00:00+00:00",
            "added_by_session": "s",
        }
        for p in patterns
    ]
    (fleet / "approval-rules.json").write_text(json.dumps(rules), encoding="utf-8")


def test_allow_override_flips_denied_by_flag(project_dir):
    # find -exec is denied by flag, but an operator rule for find flips it.
    _write_rules(project_dir, ["Bash(find:*)"])
    d = _bash("find . -exec echo {} +", project_dir)
    _assert_allow(d, "allow-override")


def test_allow_override_flips_denied_head(project_dir):
    _write_rules(project_dir, ["Bash(python3:*)"])
    d = _bash("python3 -c 'print(1)'", project_dir)
    _assert_allow(d, "allow-override")


def test_allow_override_floor_rm_rf_root_not_flippable(project_dir):
    _write_rules(project_dir, ["Bash(rm:*)"])
    d = _bash("rm -rf /", project_dir)
    _assert_deny(d)  # floor: root-destructive stays denied


def test_allow_override_floor_sudo_not_flippable(project_dir):
    _write_rules(project_dir, ["Bash(sudo:*)"])
    d = _bash("sudo rm x", project_dir)
    _assert_deny(d)  # floor: privilege stays denied


def test_allow_override_floor_secret_read_not_flippable(project_dir):
    _write_rules(project_dir, ["Bash(cat:*)"])
    d = _bash("cat ~/.ssh/id_rsa", project_dir)
    _assert_deny(d)  # floor: secret read stays denied


def test_allow_override_malformed_rules_no_raise(project_dir):
    fleet = project_dir / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    (fleet / "approval-rules.json").write_text("not json {{{", encoding="utf-8")
    # Must not raise; command still denied (no usable override).
    d = _bash("python3 -c 'x'", project_dir)
    _assert_deny(d)


def test_allow_override_missing_file_no_raise(project_dir):
    # No .fleet dir at all: denied stays denied, no exception.
    d = _bash("python3 -c 'x'", project_dir)
    _assert_deny(d)


# ---------------------------------------------------------------------------
# Exception path — fail closed
# ---------------------------------------------------------------------------


def test_internal_error_fails_closed(project_dir, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(policy, "_decide_bash", boom)
    d = _bash("ls", project_dir)
    _assert_deny(d, "governor-error")
    assert "kaboom" in d.reason


# ---------------------------------------------------------------------------
# Decision dataclass contract
# ---------------------------------------------------------------------------


def test_decision_shape(project_dir):
    d = _bash("ls", project_dir)
    assert hasattr(d, "permission")
    assert hasattr(d, "reason")
    assert hasattr(d, "category")
    assert d.permission in ("allow", "deny")


# ---------------------------------------------------------------------------
# Governor canary sentinel (Task 2.3)
# ---------------------------------------------------------------------------


def test_canary_command_is_denied_as_canary(project_dir):
    """The exact canary probe is denied EARLY with the governor-canary category."""
    d = _bash(policy.canary_command(), project_dir)
    _assert_deny(d, policy.GOVERNOR_CANARY_CATEGORY)
    assert "canary" in d.reason.lower()


def test_canary_token_embedded_in_larger_command_still_denied(project_dir):
    """The sentinel is denied no matter how benign the surrounding command — the
    early check runs before any allow logic."""
    d = _bash(f"ls && cat README.md # {policy.GOVERNOR_CANARY_TOKEN}", project_dir)
    _assert_deny(d, policy.GOVERNOR_CANARY_CATEGORY)


def test_normal_echo_is_not_a_canary_false_positive(project_dir):
    """A benign `echo hello` must NOT be denied as a canary (no false positives)."""
    d = _bash("echo hello", project_dir)
    assert d.category != policy.GOVERNOR_CANARY_CATEGORY
    _assert_allow(d)


def test_canary_command_helper_is_safe_when_unblocked():
    """canary_command() is a bare `echo <token>` — harmless if it ever runs."""
    cmd = policy.canary_command()
    assert cmd == f"echo {policy.GOVERNOR_CANARY_TOKEN}"
    assert "&&" not in cmd and "|" not in cmd and ";" not in cmd
