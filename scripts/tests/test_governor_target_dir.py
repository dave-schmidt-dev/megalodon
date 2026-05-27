"""Governor write/read scope honors an explicit second root: ``target_dir``.

A self-referential run has ``project_dir == target``: the fleet edits the same
repo it coordinates in. Pointing the fleet at an EXTERNAL repo (work-on-target
mode) needs a SECOND allowed write/read root — the target repo — without
widening the sandbox to the whole filesystem and without lifting any floor.

Containment invariant preserved: with ``target_dir`` set, the allowed roots are
exactly ``{project_dir, target_dir}`` (plus the usual ``/tmp`` scratch). A write
to a THIRD directory still denies, and a floor violation (secret/anti-tamper)
inside the target still denies.

Fixture note: PROJECT_DIR and TARGET_DIR use the DEFAULT temp location
(``/var/folders`` on macOS), which the policy's ``_is_temp_path`` does NOT honor
(it only honors ``/tmp``). That is deliberate — if TARGET_DIR were under
``/tmp`` the "denied without target_dir" assertions would be vacuous.
"""

import tempfile
from megalodon_ui.governor.policy import decide

PROJECT_DIR = tempfile.mkdtemp(prefix="gov-proj-")  # default loc, NOT /tmp
TARGET_DIR = tempfile.mkdtemp(prefix="gov-target-")  # the external repo
THIRD_DIR = tempfile.mkdtemp(prefix="gov-third-")  # neither root


def test_write_into_target_allowed_when_target_set():
    d = decide(
        "Write",
        {"file_path": f"{TARGET_DIR}/src/foo.py"},
        project_dir=PROJECT_DIR,
        target_dir=TARGET_DIR,
        lane="BACKEND",
    )
    assert d.permission == "allow", f"{d.permission}/{d.category}: {d.reason}"


def test_write_into_target_denied_when_target_unset():
    # Default-safe: without target_dir the external path is out of scope.
    d = decide(
        "Write",
        {"file_path": f"{TARGET_DIR}/src/foo.py"},
        project_dir=PROJECT_DIR,
        lane="BACKEND",
    )
    assert d.permission == "deny"
    assert d.category == "write-out-of-scope"


def test_write_into_third_dir_denied_even_with_target_set():
    # Containment: target_dir adds exactly ONE root, not the whole filesystem.
    d = decide(
        "Write",
        {"file_path": f"{THIRD_DIR}/evil.py"},
        project_dir=PROJECT_DIR,
        target_dir=TARGET_DIR,
        lane="BACKEND",
    )
    assert d.permission == "deny"
    assert d.category == "write-out-of-scope"


def test_read_into_target_allowed_when_target_set():
    d = decide(
        "Read",
        {"file_path": f"{TARGET_DIR}/src/foo.py"},
        project_dir=PROJECT_DIR,
        target_dir=TARGET_DIR,
        lane="AUDIT",
    )
    assert d.permission == "allow", f"{d.permission}/{d.category}: {d.reason}"


def test_read_into_target_denied_when_target_unset():
    d = decide(
        "Read",
        {"file_path": f"{TARGET_DIR}/src/foo.py"},
        project_dir=PROJECT_DIR,
        lane="AUDIT",
    )
    assert d.permission == "deny"


def test_secret_floor_still_denies_inside_target():
    # A floor violation is NOT liftable by target scope: writing into a .ssh
    # dir under the target still trips the write-secret floor.
    d = decide(
        "Write",
        {"file_path": f"{TARGET_DIR}/.ssh/id_rsa"},
        project_dir=PROJECT_DIR,
        target_dir=TARGET_DIR,
        lane="BACKEND",
    )
    assert d.permission == "deny"
    assert d.category == "write-secret"


def test_bash_redirect_into_target_allowed_when_target_set():
    d = decide(
        "Bash",
        {"command": f"echo hi > {TARGET_DIR}/out.txt"},
        project_dir=PROJECT_DIR,
        target_dir=TARGET_DIR,
        lane="BACKEND",
    )
    assert d.permission == "allow", f"{d.permission}/{d.category}: {d.reason}"


def test_bash_redirect_into_target_denied_when_target_unset():
    d = decide(
        "Bash",
        {"command": f"echo hi > {TARGET_DIR}/out.txt"},
        project_dir=PROJECT_DIR,
        lane="BACKEND",
    )
    assert d.permission == "deny"
    assert d.category == "write-out-of-scope"


def test_bash_cp_into_target_allowed_when_target_set():
    d = decide(
        "Bash",
        {"command": f"cp {PROJECT_DIR}/a.txt {TARGET_DIR}/b.txt"},
        project_dir=PROJECT_DIR,
        target_dir=TARGET_DIR,
        lane="BACKEND",
    )
    assert d.permission == "allow", f"{d.permission}/{d.category}: {d.reason}"


def test_bash_cat_read_in_target_allowed_when_target_set():
    # Read-style heads (cat/head/grep) are run constantly during a review;
    # reading a target file via Bash must match the native Read tool.
    d = decide(
        "Bash",
        {"command": f"cat {TARGET_DIR}/src/foo.py"},
        project_dir=PROJECT_DIR,
        target_dir=TARGET_DIR,
        lane="AUDIT",
    )
    assert d.permission == "allow", f"{d.permission}/{d.category}: {d.reason}"


def test_bash_redirect_into_third_dir_denied_even_with_target_set():
    d = decide(
        "Bash",
        {"command": f"echo hi > {THIRD_DIR}/out.txt"},
        project_dir=PROJECT_DIR,
        target_dir=TARGET_DIR,
        lane="BACKEND",
    )
    assert d.permission == "deny"
    assert d.category == "write-out-of-scope"


def test_project_dir_writes_unaffected_by_target():
    # Regression: the original single-root behavior is intact.
    d = decide(
        "Write",
        {"file_path": f"{PROJECT_DIR}/findings/x.md"},
        project_dir=PROJECT_DIR,
        target_dir=TARGET_DIR,
        lane="AUDIT",
    )
    assert d.permission == "allow", f"{d.permission}/{d.category}: {d.reason}"
