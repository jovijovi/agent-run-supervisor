"""A2 — declared command semantics are preserved exactly.

``argv[0]`` is the declared ``command`` string, byte for byte. The exec image is
located by ordinary ``execvp``-style lookup over the **child's** projected
``PATH`` for a bare name, and by the declared absolute path otherwise. There is
no ``executable=`` override, no descriptor-based image, no realpath, and no
pre-flight resolution gate.

That is what makes version-manager and package-manager shims work, symlink farms
work, package-relative resolution from the real script location work, multicall
``argv[0]`` dispatch work, and an agent's own self-update and self-relaunch logic
work. ARS classifies the exec failure itself rather than pre-checking, so those
classifications are ordinary configuration errors — "you upgraded and the shim
moved" — never security refusals.
"""

from __future__ import annotations

import ast
import asyncio
import errno
import os
import stat
from pathlib import Path

import pytest

from agent_run_supervisor import managed_process as mp
from agent_run_supervisor.managed_process import (
    ManagedProcessError,
    ManagedProcessLimits,
    spawn_managed_process,
)

SOURCE = Path(mp.__file__)


def write_script(path: Path, body: str, *, mode: int = 0o755) -> Path:
    path.write_text(body, encoding="utf-8")
    os.chmod(path, mode)
    return path


SHELL = "/bin/sh"
# ``sh -c SCRIPT`` with no operand leaves ``$0`` as the shell's own argv[0], so
# this reports exactly what ARS passed — provided the image is a real binary.
#
# A ``#!`` script cannot be used for this assertion: the kernel invokes the
# interpreter with the *resolved script path*, so the shell's ``$0`` is rewritten
# before any ARS code could preserve it. Refining A2 for that case is follow-up
# F4 and is deliberately not asserted here.
REPORT_ARGV0 = 'printf "argv0=%s\\n" "$0" >&2; exec cat >/dev/null'


def reporter(path: Path) -> Path:
    """A real-binary command that reports the ``argv[0]`` it was handed."""
    path.symlink_to(SHELL)
    return path


async def spawn(argv, *, cwd, env):
    return await spawn_managed_process(
        argv=list(argv), cwd=cwd, env=env, limits=ManagedProcessLimits()
    )


async def run_and_collect(argv, *, cwd, env) -> str:
    proc = await spawn(argv, cwd=cwd, env=env)
    proc.stdin.close()
    await proc.wait()
    return proc.stderr_bytes().decode("utf-8", "replace")


# -- argv[0] is the declared string ------------------------------------------


def test_bare_command_keeps_its_bare_argv0(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    reporter(bin_dir / "some-agent")
    stderr = asyncio.run(
        run_and_collect(
            ["some-agent", "-c", REPORT_ARGV0],
            cwd=tmp_path,
            env={"PATH": str(bin_dir)},
        )
    )
    assert "argv0=some-agent\n" in stderr


def test_absolute_command_keeps_its_absolute_argv0(tmp_path):
    target = reporter(tmp_path / "some-agent")
    stderr = asyncio.run(
        run_and_collect(
            [str(target), "-c", REPORT_ARGV0], cwd=tmp_path, env={"PATH": "/nonexistent"}
        )
    )
    assert f"argv0={target}\n" in stderr


def test_image_is_located_through_the_childs_projected_path(tmp_path):
    """The projected ``PATH`` decides, not the daemon's own."""
    real = tmp_path / "real"
    real.mkdir()
    reporter(real / "some-agent")
    stderr = asyncio.run(
        run_and_collect(
            ["some-agent", "-c", REPORT_ARGV0], cwd=tmp_path, env={"PATH": str(real)}
        )
    )
    assert "argv0=some-agent\n" in stderr
    with pytest.raises(ManagedProcessError) as excinfo:
        asyncio.run(spawn(["some-agent"], cwd=tmp_path, env={"PATH": "/nonexistent"}))
    assert excinfo.value.code == "COMMAND_NOT_FOUND"


def test_a_symlink_farm_works(tmp_path):
    """Two hops of symlink, and the child still sees the spelling ARS was given."""
    store = tmp_path / "store"
    store.mkdir()
    reporter(store / "some-agent-1")
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "some-agent").symlink_to(store / "some-agent-1")
    stderr = asyncio.run(
        run_and_collect(
            ["some-agent", "-c", REPORT_ARGV0], cwd=tmp_path, env={"PATH": str(shims)}
        )
    )
    assert "argv0=some-agent\n" in stderr
    # No realpath anywhere: neither hop's target appears.
    assert str(store) not in stderr
    assert SHELL not in stderr


def test_a_shell_shim_that_execs_the_payload_works(tmp_path):
    """Package-manager shims compose: a wrapper that ``exec``s stays supervised."""
    store = tmp_path / "store"
    store.mkdir()
    payload = reporter(store / "payload")
    shims = tmp_path / "shims"
    shims.mkdir()
    write_script(
        shims / "some-agent",
        f'#!/bin/sh\nprintf "shim=yes\\n" >&2\nexec "{payload}" "$@"\n',
    )
    stderr = asyncio.run(
        run_and_collect(
            ["some-agent", "-c", 'printf "payload=yes\\n" >&2; exec cat >/dev/null'],
            cwd=tmp_path,
            env={"PATH": str(shims)},
        )
    )
    assert "shim=yes" in stderr
    assert "payload=yes" in stderr


def test_args_are_passed_as_an_argv_list_never_through_a_shell(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    reporter(bin_dir / "some-agent")
    stderr = asyncio.run(
        run_and_collect(
            [
                "some-agent",
                "-c",
                'printf "arg1=%s\\n" "$1" >&2; exec cat >/dev/null',
                "ignored-argv0",
                "a b; echo pwned",
            ],
            cwd=tmp_path,
            env={"PATH": str(bin_dir)},
        )
    )
    assert "arg1=a b; echo pwned\n" in stderr
    assert "pwned\n" not in stderr.replace("arg1=a b; echo pwned\n", "")


# -- errno classification, never a pre-flight gate ---------------------------


def test_enoent_classifies_as_command_not_found(tmp_path):
    with pytest.raises(ManagedProcessError) as excinfo:
        asyncio.run(spawn(["no-such-agent"], cwd=tmp_path, env={"PATH": str(tmp_path)}))
    assert excinfo.value.code == "COMMAND_NOT_FOUND"


def test_eacces_classifies_as_command_not_executable(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    target = write_script(bin_dir / "some-agent", "#!/bin/sh\nexit 0\n", mode=0o644)
    assert not os.access(target, os.X_OK)
    with pytest.raises(ManagedProcessError) as excinfo:
        asyncio.run(spawn(["some-agent"], cwd=tmp_path, env={"PATH": str(bin_dir)}))
    assert excinfo.value.code == "COMMAND_NOT_EXECUTABLE"


def test_other_failures_classify_as_spawn_failed(tmp_path):
    directory = tmp_path / "a-directory"
    directory.mkdir()
    with pytest.raises(ManagedProcessError) as excinfo:
        asyncio.run(spawn([str(directory)], cwd=tmp_path, env={}))
    assert excinfo.value.code in ("SPAWN_FAILED", "COMMAND_NOT_EXECUTABLE")


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (errno.ENOENT, "COMMAND_NOT_FOUND"),
        (errno.EACCES, "COMMAND_NOT_EXECUTABLE"),
        # Everything else is SPAWN_FAILED, and the contract is exact rather
        # than "anything permission-shaped". EPERM and EISDIR are the two that
        # look like they belong above and do not: EPERM is not "the image is
        # not executable" (it is raised for ptrace scope, seccomp, and
        # no_new_privs situations that say nothing about the file), and EISDIR
        # says the operand was not a program at all. Reporting either as
        # COMMAND_NOT_EXECUTABLE would tell an operator to fix a permission
        # bit that is not the problem.
        (errno.EPERM, "SPAWN_FAILED"),
        (errno.EISDIR, "SPAWN_FAILED"),
        (errno.ELOOP, "SPAWN_FAILED"),
        (errno.ENOEXEC, "SPAWN_FAILED"),
        (errno.ENOMEM, "SPAWN_FAILED"),
        (0, "SPAWN_FAILED"),
        (None, "SPAWN_FAILED"),
    ],
)
def test_the_errno_matrix_is_exactly_the_approved_contract(number, expected):
    """Two mapped errnos, and everything else falls through. No third class."""
    exc = OSError()
    exc.errno = number
    assert mp.classify_spawn_errno(exc) == expected


def test_only_two_errnos_are_mapped_at_all():
    """A structural pin: the table itself is the contract, not a habit."""
    assert set(mp._ERRNO_CLASSIFICATION) == {errno.ENOENT, errno.EACCES}


def test_an_exception_without_an_errno_is_spawn_failed():
    assert mp.classify_spawn_errno(ValueError("no errno here")) == "SPAWN_FAILED"


def test_spawn_failure_message_embeds_no_raw_exception_text(tmp_path):
    secret_named = tmp_path / "SeNtInEl-agent-name"
    with pytest.raises(ManagedProcessError) as excinfo:
        asyncio.run(spawn([str(secret_named)], cwd=tmp_path, env={}))
    assert "SeNtInEl" not in str(excinfo.value)
    assert "Errno" not in str(excinfo.value)


def test_there_is_no_pre_flight_resolution_check(tmp_path, monkeypatch):
    """ARS classifies the exec failure itself; it never stats the command first."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    reporter(bin_dir / "some-agent")

    queried: list[str] = []
    real_stat = os.stat
    real_access = os.access

    def watch_stat(path, *args, **kwargs):
        queried.append(str(path))
        return real_stat(path, *args, **kwargs)

    def watch_access(path, *args, **kwargs):
        queried.append(str(path))
        return real_access(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", watch_stat)
    monkeypatch.setattr(os, "access", watch_access)
    asyncio.run(
        run_and_collect(
            ["some-agent", "-c", REPORT_ARGV0], cwd=tmp_path, env={"PATH": str(bin_dir)}
        )
    )
    assert not [item for item in queried if item.endswith("some-agent")]


# -- structural: the image is never chosen by ARS ----------------------------


def test_source_has_no_executable_override_and_no_descriptor_image():
    text = SOURCE.read_text(encoding="utf-8")
    assert "/proc/self/fd" not in text
    assert "interpreter_fd" not in text
    assert "pass_fds" not in text
    assert "os.path.realpath" not in text
    assert ".resolve()" not in text
    # Prose may *name* the override it refuses; only a real keyword is banned,
    # and the AST test below is what proves the call site itself.
    assert "executable=f" not in text
    assert 'executable="' not in text


def test_create_subprocess_exec_is_called_without_an_executable_keyword():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_subprocess_exec"
    ]
    assert calls, "the spawn seam moved; this structural test must move with it"
    for call in calls:
        keywords = {keyword.arg for keyword in call.keywords}
        assert "executable" not in keywords
        assert "pass_fds" not in keywords
        assert "shell" not in keywords
        assert keywords >= {"cwd", "env", "start_new_session"}


def test_spawn_signature_carries_no_image_override():
    import inspect

    parameters = inspect.signature(spawn_managed_process).parameters
    assert "interpreter_fd" not in parameters
    assert "executable" not in parameters


def test_spawn_accepts_the_resolved_environment_carrier(tmp_path):
    """The spawn seam is one of exactly two consumers of the value carrier."""
    from agent_run_supervisor.native_acp import profile as profile_mod
    from agent_run_supervisor.native_acp import spec
    from agent_run_supervisor.native_acp.agent_registration import AgentEntry

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    reporter(bin_dir / "some-agent")
    resolved = spec.resolve_run_environment(
        arsd_env={"PATH": str(bin_dir)},
        profile=profile_mod.STANDARD_NATIVE_ACP_V1,
        entry=AgentEntry(
            agent_id="a-1", profile_id="standard-native-acp-v1", command="some-agent"
        ),
    )
    stderr = asyncio.run(
        run_and_collect(
            ["some-agent", "-c", REPORT_ARGV0], cwd=tmp_path, env=resolved.exec_mapping
        )
    )
    assert "argv0=some-agent\n" in stderr


# -- C: an empty token is an ordinary argv token -----------------------------

REPORT_ARGV = 'for a in "$@"; do printf "arg=[%s]\\n" "$a" >&2; done; exec cat >/dev/null'


def test_an_empty_declared_arg_token_reaches_exec_exactly(tmp_path):
    """C — ``args=[""]`` survives registry → entry → argv → exec, unchanged.

    Nothing between the operator's file and the child may drop, coalesce, or
    substitute a declared token. An empty string is a token the child can see and
    count, so silently removing it changes the command the operator declared.
    """
    from agent_run_supervisor.native_acp.agent_registration import AgentEntry

    entry = AgentEntry(
        agent_id="a-1",
        profile_id="standard-native-acp-v1",
        command=SHELL,
        args=("-c", REPORT_ARGV, "sh", "--label", "", "--end"),
    )
    assert entry.argv() == (SHELL, "-c", REPORT_ARGV, "sh", "--label", "", "--end")

    stderr = asyncio.run(
        run_and_collect(list(entry.argv()), cwd=tmp_path, env={"PATH": "/usr/bin:/bin"})
    )
    assert stderr.splitlines() == ["arg=[--label]", "arg=[]", "arg=[--end]"]
