"""v9.4 Task 3.4 — Unit tests for extract_pattern() in approval_rules.py.

Covers:
- curl with flags → Bash(curl <flags> <host>/*)
- curl with quoted URL → quotes stripped, same result
- generic commands → Bash(<program>:*)
- compound commands (&&, |, ;, for, redirects) → None
- empty / whitespace → None
- edge cases: URL without path, multiple curl flags, https, unknown program
"""

from __future__ import annotations


from megalodon_ui.approval_rules import extract_pattern


# ---------------------------------------------------------------------------
# Core required cases (from task spec)
# ---------------------------------------------------------------------------


def test_curl_with_flag_and_path():
    """curl -s http://127.0.0.1:8765/foo → Bash(curl -s http://127.0.0.1:8765/*)"""
    result = extract_pattern("curl -s http://127.0.0.1:8765/foo")
    assert result == "Bash(curl -s http://127.0.0.1:8765/*)"


def test_curl_with_quoted_url():
    """curl -s "http://127.0.0.1:8765/foo" → same as unquoted (quotes stripped)"""
    result = extract_pattern('curl -s "http://127.0.0.1:8765/foo"')
    assert result == "Bash(curl -s http://127.0.0.1:8765/*)"


def test_find_generic():
    """find . -name x → Bash(find:*)"""
    result = extract_pattern("find . -name x")
    assert result == "Bash(find:*)"


def test_pytest_generic():
    """pytest scripts/tests/ -v → Bash(pytest:*)"""
    result = extract_pattern("pytest scripts/tests/ -v")
    assert result == "Bash(pytest:*)"


def test_compound_and():
    """git status && npm test → None (compound)"""
    result = extract_pattern("git status && npm test")
    assert result is None


def test_pipe():
    """ls | wc -l → None (pipe)"""
    result = extract_pattern("ls | wc -l")
    assert result is None


def test_for_loop():
    """for f in *.py; do cat $f; done → None (control flow)"""
    result = extract_pattern("for f in *.py; do cat $f; done")
    assert result is None


def test_redirect():
    """echo x > out.txt → None (redirect)"""
    result = extract_pattern("echo x > out.txt")
    assert result is None


def test_empty_string():
    """'' → None"""
    result = extract_pattern("")
    assert result is None


def test_whitespace_only():
    """'   ' → None"""
    result = extract_pattern("   ")
    assert result is None


# ---------------------------------------------------------------------------
# Extended edge cases
# ---------------------------------------------------------------------------


def test_curl_no_flags():
    """curl http://localhost:9000/api → Bash(curl http://localhost:9000/*)"""
    result = extract_pattern("curl http://localhost:9000/api")
    assert result == "Bash(curl http://localhost:9000/*)"


def test_curl_https():
    """curl -X GET https://api.example.com/v1/users → preserves https prefix"""
    result = extract_pattern("curl -X GET https://api.example.com/v1/users")
    assert result == "Bash(curl -X GET https://api.example.com/*)"


def test_curl_multiple_flags():
    """curl -s -H 'Content-Type: application/json' http://host/path"""
    result = extract_pattern(
        "curl -s -H 'Content-Type: application/json' http://host/path"
    )
    # Header flag with space-containing value is one token after shlex split
    assert result == "Bash(curl -s -H Content-Type: application/json http://host/*)"


def test_curl_no_url_falls_to_generic():
    """curl --version (no URL) → Bash(curl:*)"""
    result = extract_pattern("curl --version")
    assert result == "Bash(curl:*)"


def test_git_generic():
    """git log --oneline → Bash(git:*)"""
    result = extract_pattern("git log --oneline")
    assert result == "Bash(git:*)"


def test_npm_generic():
    """npm run build → Bash(npm:*)"""
    result = extract_pattern("npm run build")
    assert result == "Bash(npm:*)"


def test_python_generic():
    """python script.py --verbose → Bash(python:*)"""
    result = extract_pattern("python script.py --verbose")
    assert result == "Bash(python:*)"


def test_or_operator():
    """cmd1 || cmd2 → None (logical OR)"""
    result = extract_pattern("cmd1 || cmd2")
    assert result is None


def test_semicolon_compound():
    """echo a; echo b → None (semicolon separator)"""
    result = extract_pattern("echo a; echo b")
    assert result is None


def test_redirect_append():
    """echo hello >> log.txt → None (redirect)"""
    result = extract_pattern("echo hello >> log.txt")
    assert result is None


def test_redirect_input():
    """wc -l < file.txt → None (input redirect)"""
    result = extract_pattern("wc -l < file.txt")
    assert result is None


def test_curl_url_no_path():
    """curl http://127.0.0.1:8765 (no trailing path) → Bash(curl http://127.0.0.1:8765/*)"""
    result = extract_pattern("curl http://127.0.0.1:8765")
    assert result == "Bash(curl http://127.0.0.1:8765/*)"


def test_custom_tool():
    """custom-tool --flag value → Bash(custom-tool:*)"""
    result = extract_pattern("custom-tool --flag value")
    assert result == "Bash(custom-tool:*)"


def test_single_token_command():
    """ls → Bash(ls:*)"""
    result = extract_pattern("ls")
    assert result == "Bash(ls:*)"


def test_while_loop():
    """while true; do ...; done → None (control flow)"""
    result = extract_pattern("while true; do sleep 1; done")
    assert result is None


def test_if_statement():
    """if [ -f x ]; then echo y; fi → None"""
    result = extract_pattern("if [ -f x ]; then echo y; fi")
    assert result is None


def test_backtick_substitution():
    """echo `date` → None (command substitution)"""
    result = extract_pattern("echo `date`")
    assert result is None


def test_dollar_paren_substitution():
    """echo $(date) → None (command substitution)"""
    result = extract_pattern("echo $(date)")
    assert result is None
