---
title: Operations runbook
description: Install, configure, deploy, accept, upgrade, roll back, and remove an ARS user service.
---

# Operations runbook

This runbook takes one Linux operator through the complete lifecycle of one
unprivileged `arsd` installation. It uses an exact PyPI version, an immutable
virtual environment per release, and a user-scope `systemd` service. Replace
every angle-bracket placeholder before running a command.

The example unit is `agent-run-supervisor-arsd.service`. You may choose another
name, but use that name consistently. Nothing on this page describes the state
of any particular machine.

!!! warning "Seven decisions, not one"

    Keep these decisions explicit and separately authorized:

    1. **Install** creates an immutable ARS runtime.
    2. **Configure** creates local registry, environment, identity, path, and
       state choices.
    3. **Enable** asks the user service manager to start the unit in future user
       sessions.
    4. **Restart** stops the serving daemon and starts it again; it may affect
       active Runs.
    5. **Cut over** permits callers to submit to the new daemon.
    6. **Release** publishes an artifact. This runbook consumes an already
       published exact PyPI wheel; it does not publish one.
    7. **Deploy** is the operator-approved combination of the applicable steps.

    A package install does not configure, enable, restart, cut over, release, or
    deploy anything. A green check authorizes none of the next decisions.

## 1. Set the installation values

Run this whole runbook as the unprivileged service user, never as root and never
with `sudo`. `arsd` refuses to run as root.

Choose a version that is already published on PyPI and fill in the caller
mapping approved for this installation. The UID must be the UID of each trusted
local caller. A typical single-user installation maps the service user's own
UID; do not copy an identity mapping from another installation.

```bash
export ARS_VERSION='<X.Y.Z>'
export ARS_SERVICE='agent-run-supervisor-arsd.service'
export ARS_RELEASE_ROOT="$HOME/.local/opt/agent-run-supervisor/releases"
export ARS_RUNTIME="$ARS_RELEASE_ROOT/$ARS_VERSION/venv"
export ARS_CONFIG_ROOT="$HOME/.config/agent-run-supervisor"
export ARS_CONFIG="$ARS_CONFIG_ROOT/releases/$ARS_VERSION"
export ARS_STATE="$HOME/.local/share/agent-run-supervisor"
export ARS_SOCKET="${XDG_RUNTIME_DIR:?a user runtime directory is required}/agent-run-supervisor/arsd.sock"
export ARS_UNIT_DIR="$HOME/.config/systemd/user"
export ARS_UNIT="$ARS_UNIT_DIR/$ARS_SERVICE"
export ARS_CALLER_MAPPING="$(id -u):<principal_id>:<owner>:<namespace>"
```

Checkpoint:

```bash
id -u
python3 --version
systemctl --user --version
loginctl show-user "$USER" -p Linger --value
printf '%s\n' "$XDG_RUNTIME_DIR" "$ARS_RUNTIME" "$ARS_CONFIG" "$ARS_STATE" "$ARS_SOCKET" "$ARS_UNIT"
```

Proceed only when Python is 3.11 or newer, `id -u` is not `0`, the user service
manager is available, `Linger` is `yes`, and every path printed is the intended
user-owned path. Linux, `AF_UNIX`, and a persistent user service-manager cgroup
are required. If `Linger` is `no`, stop here and ask the host administrator to
enable it for the service user as a separate privileged host decision:

```bash
# Administrator action, not a command to run as the unprivileged service user:
loginctl enable-linger <service-user>
```

Then log in again if required and repeat the `loginctl show-user` check. Enabling
a user unit without linger does not make the user manager survive logout.
ARS has no TCP listener, HTTP health endpoint, web console, or root/system
service.

Also install, configure, and authenticate each ACP-capable external agent using
its own instructions and package manager. Codex CLI and Claude Code do not expose
the ACP stdio server ARS needs by themselves; install their adapters separately:

```bash
export CODEX_ACP_VERSION='<exact-version>'
export CLAUDE_ACP_VERSION='<exact-version>'
npm install -g "@agentclientprotocol/codex-acp@$CODEX_ACP_VERSION"
npm install -g "@agentclientprotocol/claude-agent-acp@$CLAUDE_ACP_VERSION"
codex-acp --version
claude-agent-acp --version
```

Use the current [`@agentclientprotocol/codex-acp`](https://github.com/agentclientprotocol/codex-acp)
package, not the archived `@zed-industries/codex-acp` package. The adapters are
operator-managed software: ARS never installs, upgrades, removes, or manages an
external agent, adapter, credential, home, plugin, cache, configuration, or
conversation store. Pin versions appropriate for the deployment and re-run agent
acceptance after either the adapter or underlying CLI changes.

## 2. Install one immutable ARS runtime

Choose exactly one installation source. **PyPI is the recommended production
path.** A local source checkout is useful for development, acceptance of an
unreleased commit, or an operator-controlled build. Do not combine the two paths
in one runtime.

### 2.1 Install the published PyPI package

Create a new virtual environment for this exact release. Never upgrade or
otherwise mutate the virtual environment used by a live unit.

```bash
umask 077
install -d -m 0700 "$ARS_RELEASE_ROOT"
test ! -e "$ARS_RUNTIME"
python3 -m venv "$ARS_RUNTIME"
"$ARS_RUNTIME/bin/python" -m pip install --upgrade pip
"$ARS_RUNTIME/bin/python" -m pip install \
  --only-binary=:all: \
  "agent-run-supervisor[native]==$ARS_VERSION"
"$ARS_RUNTIME/bin/python" -m pip check
export ARS_EXPECTED_VERSION="$ARS_VERSION"
"$ARS_RUNTIME/bin/python" - <<'PY'
from importlib.metadata import version
print(version("agent-run-supervisor"))
print(version("agent-client-protocol"))
PY
"$ARS_RUNTIME/bin/agent-run-supervisor" --help
"$ARS_RUNTIME/bin/python" -m agent_run_supervisor.arsd --help
```

Checkpoint: the first printed version must equal `$ARS_VERSION`; `pip check`
and both help commands must succeed. The `native` extra installs the pinned ACP
client needed to drive real agents. `--only-binary=:all:` makes this path consume
published wheels rather than building unreviewed source locally. If PyPI has no
compatible wheel for the selected platform and Python, stop and choose a
supported published artifact; do not mutate an older live runtime.

The directory named by `$ARS_RUNTIME` is now an immutable release artifact.
Keep it until the rollback window closes.

### 2.2 Install from a pinned local source checkout

Use a dedicated clean checkout at an exact commit. Do not install from a moving
branch or a checkout with local changes. Give the runtime and configuration a
commit-derived release id so it cannot collide with a PyPI runtime carrying the
same package version:

```bash
export ARS_SOURCE='<absolute-path-to-agent-run-supervisor-checkout>'
export ARS_SOURCE_COMMIT='<full-40-character-commit-id>'
test "$(git -C "$ARS_SOURCE" rev-parse HEAD)" = "$ARS_SOURCE_COMMIT"
test -z "$(git -C "$ARS_SOURCE" status --porcelain=v1 -uall)"

export ARS_SOURCE_RELEASE="source-${ARS_SOURCE_COMMIT:0:12}"
export ARS_RUNTIME="$ARS_RELEASE_ROOT/$ARS_SOURCE_RELEASE/venv"
export ARS_CONFIG="$ARS_CONFIG_ROOT/releases/$ARS_SOURCE_RELEASE"

umask 077
install -d -m 0700 "$ARS_RELEASE_ROOT"
test ! -e "$ARS_RUNTIME"
python3 -m venv "$ARS_RUNTIME"
"$ARS_RUNTIME/bin/python" -m pip install --upgrade pip
"$ARS_RUNTIME/bin/python" -m pip install "$ARS_SOURCE[native]"
"$ARS_RUNTIME/bin/python" -m pip check
export ARS_EXPECTED_VERSION="$(
  "$ARS_RUNTIME/bin/python" -c \
    'from importlib.metadata import version; print(version("agent-run-supervisor"))'
)"
"$ARS_RUNTIME/bin/python" - <<'PY'
from importlib.metadata import version
print(version("agent-run-supervisor"))
print(version("agent-client-protocol"))
PY
"$ARS_RUNTIME/bin/agent-run-supervisor" --help
"$ARS_RUNTIME/bin/python" -m agent_run_supervisor.arsd --help
printf '%s\n' "$ARS_SOURCE_COMMIT" >"$ARS_RUNTIME/.ars-source-commit"
chmod 0600 "$ARS_RUNTIME/.ars-source-commit"
test "$(cat "$ARS_RUNTIME/.ars-source-commit")" = "$ARS_SOURCE_COMMIT"
```

Checkpoint: the checkout remains clean at the exact commit, `pip check` and both
help commands succeed, and `.ars-source-commit` records the installed source
identity. The installation copies/builds the package into the release venv; the
service does not run with `PYTHONPATH=src` and does not depend on the checkout
remaining at that path. Keep the source commit reachable for rebuild and audit.

## 3. Create the local configuration

Create a release-specific configuration directory, an operator-owned
`agents.toml`, and a local daemon environment file. Both files are deployment
configuration, not repository content.

```bash
install -d -m 0700 "$ARS_CONFIG_ROOT" "$ARS_CONFIG_ROOT/releases" "$ARS_CONFIG"
install -d -m 0700 "$ARS_STATE"
```

Create `$ARS_CONFIG/agents.toml` with mode `0600`:

```toml
schema_version = 1

[agents.example-agent]
profile = "standard-native-acp-v1"
command = "example-agent-cli"
args = ["acp"]
mediation = "ask-privileged-tool-families-v1"
env_passthrough = ["EXAMPLE_PROVIDER_TOKEN"]
env_overlay = { PATH = "/home/<service-user>/.local/bin:/usr/local/bin:/usr/bin:/bin" }
```

`example-agent` and `example-agent-cli` are placeholders. Select the profile and
arguments documented for your agent. The file must be regular and not group- or
world-writable. The daemon requires its **absolute** path. See the [agents file
reference](../reference/agents-file.md) and the agent-specific [How-to
Guides](../how-to/index.md).

A user service has a smaller environment than an interactive shell. A bare
`command` is resolved against the child `PATH`, preserving the declared PATH,
shim, symlink, and `argv[0]` semantics. Therefore set an explicit
`env_overlay.PATH` containing the installed command, or register its absolute
path. Do not assume a version-manager shell hook runs under `systemd`.

Create `$ARS_CONFIG/arsd.env` with mode `0600` using systemd
`EnvironmentFile=` syntax. Put real values only in this local file:

```ini
EXAMPLE_PROVIDER_TOKEN=[REDACTED]
```

Then enforce and inspect ownership and modes:

```bash
chmod 0600 "$ARS_CONFIG/agents.toml" "$ARS_CONFIG/arsd.env"
test -O "$ARS_CONFIG" && test -O "$ARS_CONFIG/agents.toml" && test -O "$ARS_CONFIG/arsd.env"
test "$(stat -c '%a' "$ARS_CONFIG")" = 700
test "$(stat -c '%a' "$ARS_CONFIG/agents.toml")" = 600
test "$(stat -c '%a' "$ARS_CONFIG/arsd.env")" = 600
```

Keep the state root across upgrades and ordinary rollbacks. It contains durable
Run and Session facts. Runs terminate; Sessions do not close. A Session remains
durable and resumable across daemon restarts unless it is quarantined or an
operator deliberately changes an identity choice such as `agent_id`, profile,
or `session_epoch`.

## 4. Validate the registry and diagnose each agent

Validation is offline and starts nothing. Doctor with `--no-probe` also starts
nothing; doctor without it starts the external command for a zero-prompt ACP
handshake, may let that agent write its own state, and always reaps the child.

```bash
"$ARS_RUNTIME/bin/agent-run-supervisor" agents validate \
  --agents-file "$ARS_CONFIG/agents.toml"
"$ARS_RUNTIME/bin/agent-run-supervisor" agents doctor \
  --agents-file "$ARS_CONFIG/agents.toml" \
  --agent example-agent \
  --no-probe
(
  set -a
  . "$ARS_CONFIG/arsd.env"
  set +a
  "$ARS_RUNTIME/bin/agent-run-supervisor" agents doctor \
    --agents-file "$ARS_CONFIG/agents.toml" \
    --agent example-agent
)
```

Checkpoint: validation and every selected agent's doctor complete successfully;
the declared command, arguments, projected environment **names**, ACP protocol,
and required capabilities match the intended installation. Repeat doctor for
every registered agent. ARS does not install or authenticate an agent when a
probe fails.

## 5. Render, review, and install the user unit

Rendering writes only stdout. It does not install, enable, or start anything.
Render with the new runtime's exact interpreter and this configuration's
absolute paths:

```bash
umask 077
UNIT_CANDIDATE="$(mktemp --suffix=.service)"
"$ARS_RUNTIME/bin/python" -m agent_run_supervisor.arsd \
  --print-service-unit \
  --socket "$ARS_SOCKET" \
  --supervisor-root "$ARS_STATE" \
  --agents-file "$ARS_CONFIG/agents.toml" \
  --caller-mapping "$ARS_CALLER_MAPPING" \
  >"$UNIT_CANDIDATE"
chmod 0600 "$UNIT_CANDIDATE"
```

The renderer does not add an environment file. Add this installation's local
environment file to the candidate immediately below `[Service]` using a text
editor, with this exact directive and an absolute path:

```ini
EnvironmentFile=/absolute/path/to/arsd.env
```

Review the complete candidate before installing it:

```bash
systemd-analyze --user verify "$UNIT_CANDIDATE"
grep -F "ExecStart=$ARS_RUNTIME/bin/python" "$UNIT_CANDIDATE"
grep -F -- "--supervisor-root $ARS_STATE" "$UNIT_CANDIDATE"
grep -F -- "--agents-file $ARS_CONFIG/agents.toml" "$UNIT_CANDIDATE"
grep -F -- "--caller-mapping $ARS_CALLER_MAPPING" "$UNIT_CANDIDATE"
grep -F "EnvironmentFile=$ARS_CONFIG/arsd.env" "$UNIT_CANDIDATE"
grep -Fx 'Restart=on-failure' "$UNIT_CANDIDATE"
grep -Fx 'KillMode=control-group' "$UNIT_CANDIDATE"
```

Checkpoint: `systemd-analyze` reports no error; `ExecStart` names this release's
exact interpreter; paths and caller mappings are correct; and
`Restart=on-failure` plus `KillMode=control-group` are present. The latter two
are load-bearing: restart performs fail-closed reconciliation, while the user
service cgroup kills descendants that remain in that cgroup if `arsd` crashes.
This is crash containment, not an OS sandbox and not containment of a process
relocated to another supervisor, namespace, or cgroup.

Installing the reviewed file is a separate decision:

```bash
install -d -m 0700 "$ARS_UNIT_DIR"
test ! -e "$ARS_UNIT"
install -m 0600 "$UNIT_CANDIDATE" "$ARS_UNIT"
rm -f "$UNIT_CANDIDATE"
systemctl --user daemon-reload
systemctl --user cat "$ARS_SERVICE"
```

If replacing an existing unit during upgrade, use the upgrade procedure below;
do not use `test ! -e` or overwrite the live unit ad hoc.

## 6. Enable, start, and verify

Enabling future starts and starting now are separate decisions:

```bash
systemctl --user enable "$ARS_SERVICE"
systemctl --user start "$ARS_SERVICE"
systemctl --user status --no-pager "$ARS_SERVICE"
systemctl --user show "$ARS_SERVICE" \
  -p ActiveState -p SubState -p MainPID -p FragmentPath -p KillMode -p Restart
```

Checkpoint: the unit is `active (running)`, `MainPID` is nonzero, and the shown
properties include `KillMode=control-group` and `Restart=on-failure`. If startup
fails, inspect bounded service diagnostics without publishing environment
values, caller mappings, or credentials:

```bash
journalctl --user -u "$ARS_SERVICE" -n 100 --no-pager
```

There is no TCP port or health URL to probe. Verify the production ingress by
calling `server_info` over the Unix socket:

```bash
ARS_RUNTIME="$ARS_RUNTIME" ARS_SOCKET="$ARS_SOCKET" \
  "$ARS_RUNTIME/bin/python" - <<'PY'
import os
from importlib.metadata import version
from agent_run_supervisor.arsd.client import ArsdClient

with ArsdClient(os.environ["ARS_SOCKET"]) as client:
    info = client.server_info(request_id="ops-server-info")
print(info)
expected = os.environ["ARS_EXPECTED_VERSION"]
assert version("agent-run-supervisor") == expected
assert info["version"] == expected
assert info["api_version"] == 3
PY
```

Export `ARS_EXPECTED_VERSION` to this command's environment if your shell does
not retain exports from the selected installation path. Check that `server_info`
reports API version 3 and that expected ARS package version and limits. Socket
success proves that the caller UID is mapped; an unmapped UID is refused.

At this point the service is installed and reachable, but do **not** cut callers
over until acceptance passes.

## 7. Acceptance before cutover

Acceptance has two distinct parts for each registered agent: a real denied-action
canary and a two-Run Session continuity test. Use a disposable, known-empty
workspace and a model/effort the live agent advertises. Never run the canary in a
valuable workspace.

Save the following local helper as `$ARS_CONFIG/acceptance_submit.py` with mode
`0600`. It submits exactly one Run, waits for its terminal, reads every event
page, and prints one JSON document. Replace the hash placeholders with references
and hashes issued by your own admission/grant pipeline before use.

```python title="acceptance_submit.py"
import json
import os
import time

from agent_run_supervisor.arsd.client import ArsdClient

request = {
    "owner": os.environ["ARS_OWNER"],
    "namespace": os.environ["ARS_NAMESPACE"],
    "agent_id": os.environ["ARS_AGENT_ID"],
    "expected_binding_hash": None,
    "input_refs": [{
        "ref": "prompt:inline",
        "content_hash": os.environ["ARS_INPUT_HASH"],
    }],
    "requested_model": os.environ["ARS_MODEL"],
    "requested_effort": os.environ["ARS_EFFORT"],
    "grant_ref": os.environ["ARS_GRANT_REF"],
    "grant_hash": os.environ["ARS_GRANT_HASH"],
    "grant_role_hash": os.environ["ARS_GRANT_ROLE_HASH"],
    "grant_capabilities": json.loads(os.environ["ARS_GRANT_CAPABILITIES"]),
    "mcp_snapshot_hashes": [],
    "credential_refs": [],
    "limits": {},
    "evidence_policy_hash": os.environ["ARS_EVIDENCE_POLICY_HASH"],
    "recovery_policy_hash": os.environ["ARS_RECOVERY_POLICY_HASH"],
}
session_id = os.environ.get("ARS_SESSION_ID")
if session_id:
    request["session_id"] = session_id

with ArsdClient(os.environ["ARS_SOCKET"]) as client:
    ack = client.submit(
        request_id=os.environ["ARS_REQUEST_ID"],
        payload={
            "request": request,
            "prompt_text": os.environ["ARS_PROMPT"],
            "workspace_root": os.environ["ARS_WORKSPACE"],
        },
    )

deadline = time.monotonic() + 21600
while True:
    with ArsdClient(os.environ["ARS_SOCKET"]) as client:
        status = client.run_status(ack["run_id"])
    if "result" in status:
        break
    if time.monotonic() >= deadline:
        raise TimeoutError("Run did not reach a terminal within 6 hours")
    time.sleep(1)

events = []
from_seq = 0
while True:
    with ArsdClient(os.environ["ARS_SOCKET"]) as client:
        page = client.run_events(ack["run_id"], from_seq=from_seq, limit=1000)
    events.extend(page.get("events", []))
    if page.get("exhausted") is not False:
        break
    next_seq = page.get("next_from_seq")
    if not isinstance(next_seq, int) or next_seq <= from_seq:
        raise RuntimeError("event pagination did not advance")
    from_seq = next_seq

print(json.dumps({"ack": ack, "status": status, "events": events}, indent=2))
```

Prepare the values shared by all three acceptance Runs. A model-only profile
uses its documented effort sentinel; other profiles use an effort the agent
advertises.

```bash
chmod 0600 "$ARS_CONFIG/acceptance_submit.py"
export ARS_OWNER='<owner>'
export ARS_NAMESPACE='<namespace>'
export ARS_AGENT_ID='example-agent'
export ARS_MODEL='<model-the-agent-advertises>'
export ARS_EFFORT='<effort-the-agent-advertises>'
export ARS_GRANT_REF='grant:<operator-grant-reference>'
export ARS_GRANT_HASH='sha256:<64-hex-grant-hash>'
export ARS_GRANT_ROLE_HASH='sha256:<64-hex-role-hash>'
export ARS_EVIDENCE_POLICY_HASH='sha256:<64-hex-policy-hash>'
export ARS_RECOVERY_POLICY_HASH='sha256:<64-hex-policy-hash>'
```

### 7.1 Mandatory denied-action canary

Create a disposable workspace owned by the service user:

```bash
export ARS_CANARY_WORKSPACE="$(mktemp -d)"
chmod 0700 "$ARS_CANARY_WORKSPACE"
test -z "$(find "$ARS_CANARY_WORKSPACE" -mindepth 1 -print -quit)"
```

Submit a Run with a frozen read-only grant and ask for one observable write:

```bash
export ARS_WORKSPACE="$ARS_CANARY_WORKSPACE"
export ARS_GRANT_CAPABILITIES='["read"]'
export ARS_PROMPT='Create the file ars-denied-canary.txt in the workspace and write the word denied into it.'
export ARS_INPUT_HASH="$($ARS_RUNTIME/bin/python -c \
  'import hashlib,os; print("sha256:" + hashlib.sha256(os.environ["ARS_PROMPT"].encode()).hexdigest())')"
export ARS_REQUEST_ID="ops-denied-canary-$($ARS_RUNTIME/bin/python -c 'import secrets; print(secrets.token_hex(8))')"
unset ARS_SESSION_ID
(
  set -a
  . "$ARS_CONFIG/arsd.env"
  set +a
  "$ARS_RUNTIME/bin/python" "$ARS_CONFIG/acceptance_submit.py"
) >"$ARS_CONFIG/denied-canary-result.json"
chmod 0600 "$ARS_CONFIG/denied-canary-result.json"
"$ARS_RUNTIME/bin/python" -m json.tool "$ARS_CONFIG/denied-canary-result.json"
```

Checkpoint, all required:

1. the requested file does **not** exist;
2. the Run reaches one terminal result;
3. events contain a mediation request and a denied decision for the attempted
   write; and
4. the evidence is for this Run and this registered agent.

```bash
test ! -e "$ARS_CANARY_WORKSPACE/ars-denied-canary.txt"
```

Zero mediation events prove nothing. Permission mediation is cooperative policy
enforcement, not an OS sandbox; repeat the real canary for **every** registered
agent before allowing that agent into service. Treat the write-family canary as
deny-only: it proves a forbidden action is refused and does not establish that a
write-capable grant is usable. Never substitute a permissive or unmediated mode
when the denial cannot be proved.

### 7.2 First Run and same-Session continuity

Submit one harmless read-only Run **without** a `session_id`, extract the Session
id from its acknowledgement, and submit a second Run against that same Session:

```bash
export ARS_GRANT_CAPABILITIES='["read"]'
export ARS_PROMPT='Remember this acceptance phrase exactly: amber-orbit-47. Reply with the phrase.'
export ARS_INPUT_HASH="$($ARS_RUNTIME/bin/python -c \
  'import hashlib,os; print("sha256:" + hashlib.sha256(os.environ["ARS_PROMPT"].encode()).hexdigest())')"
export ARS_REQUEST_ID="ops-continuity-1-$($ARS_RUNTIME/bin/python -c 'import secrets; print(secrets.token_hex(8))')"
unset ARS_SESSION_ID
(
  set -a
  . "$ARS_CONFIG/arsd.env"
  set +a
  "$ARS_RUNTIME/bin/python" "$ARS_CONFIG/acceptance_submit.py"
) >"$ARS_CONFIG/continuity-run-1.json"
chmod 0600 "$ARS_CONFIG/continuity-run-1.json"

export ARS_SESSION_ID="$($ARS_RUNTIME/bin/python -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["ack"]["session_id"])' \
  "$ARS_CONFIG/continuity-run-1.json")"
export ARS_PROMPT='What exact acceptance phrase did I ask you to remember in the previous Run?'
export ARS_INPUT_HASH="$($ARS_RUNTIME/bin/python -c \
  'import hashlib,os; print("sha256:" + hashlib.sha256(os.environ["ARS_PROMPT"].encode()).hexdigest())')"
export ARS_REQUEST_ID="ops-continuity-2-$($ARS_RUNTIME/bin/python -c 'import secrets; print(secrets.token_hex(8))')"
(
  set -a
  . "$ARS_CONFIG/arsd.env"
  set +a
  "$ARS_RUNTIME/bin/python" "$ARS_CONFIG/acceptance_submit.py"
) >"$ARS_CONFIG/continuity-run-2.json"
chmod 0600 "$ARS_CONFIG/continuity-run-2.json"
"$ARS_RUNTIME/bin/python" -m json.tool "$ARS_CONFIG/continuity-run-1.json"
"$ARS_RUNTIME/bin/python" -m json.tool "$ARS_CONFIG/continuity-run-2.json"
```

Checkpoint, all required:

1. Run 1 completes with the intended model and effort exactly read back;
2. Run 2 has a different `run_id` and the same ARS `session_id`;
3. Run 2 uses real `session/load` continuity and correctly recalls the first
   Run's distinctive fact;
4. both Runs have one immutable terminal and bounded, sequence-ordered events;
5. no unexpected permission is granted; and
6. a service restart between additional completed Runs does not invalidate the
   Session.

A Run terminal is a technical supervisor result, not a business verdict. Do not
retry a Run reported `unknown`, and do not reuse a quarantined Session. ARS has
no operation that closes a Session: retained Sessions remain resumable
indefinitely.

After these checks, permitting production callers to submit is the separate
**cutover** decision.

## 8. Routine operation

Use the socket and service manager, not a port check:

```bash
systemctl --user status --no-pager "$ARS_SERVICE"
journalctl --user -u "$ARS_SERVICE" --since today --no-pager
```

Call `server_info` as shown above for an authenticated liveness and version
check. Use `run_status`, `run_events`, `session_status`, and `session_list` over
the [Socket API](../reference/socket-api.md) for owner-scoped technical facts,
and `agent_list` to read back which canonical agent ids *this running daemon*
loaded — the answer a restart, not a file edit, changes.
Inspect one persisted Run without changing it:

```bash
"$ARS_RUNTIME/bin/agent-run-supervisor" run inspect --run-dir <absolute-run-directory>
```

Size and retain `$ARS_STATE` according to your data-governance policy. Deleting
it deletes ARS's durable Run and Session facts and is never part of an ordinary
upgrade, rollback, service removal, or package removal.

The agents file is parsed once at startup and never re-read while serving:

- after changing `agents.toml`, validate and doctor it, drain active Runs, then
  restart the service exactly once;
- after upgrading an external agent or adapter behind an unchanged registered
  command, do **not** restart ARS merely for that upgrade; the next Run resolves
  the command normally;
- changing `command`, arguments, environment declarations, mediation, profile,
  or `session_epoch` is a registry edit and therefore requires restart;
- bump `session_epoch` only when deliberately cutting continuity. Adding it for
  the first time also cuts existing continuity.

## 9. Upgrade without mutating the live runtime

Assume the current accepted release is `<OLD_VERSION>` and the target published
release is `<NEW_VERSION>`.

1. **Pause new submissions at every caller.** There is no administrative drain
   endpoint.
2. Use `run_status` for every caller-known active Run and wait for a trustworthy
   terminal. A Session has no close; draining means only that no Run is active.
3. Install `<NEW_VERSION>` into a new release-specific virtual environment by
   repeating sections 1–2 with `ARS_VERSION=<NEW_VERSION>`.
4. Copy the old local configuration into a new release-specific config directory,
   review it, and make only approved changes. Preserve the old directory.
5. Run registry validate and doctor with the **new** runtime and new config.
6. Render and review a new candidate unit. Its `ExecStart` must name the new
   immutable interpreter and its paths must name the intended config/state.
7. Replace the installed unit, reload, and restart as one controlled cutover.

Example unit/config preparation, after setting the new values:

```bash
export OLD_VERSION='<OLD_VERSION>'
export NEW_VERSION='<NEW_VERSION>'
export OLD_CONFIG="$ARS_CONFIG_ROOT/releases/$OLD_VERSION"
export NEW_CONFIG="$ARS_CONFIG_ROOT/releases/$NEW_VERSION"

test -d "$OLD_CONFIG"
test ! -e "$NEW_CONFIG"
install -d -m 0700 "$NEW_CONFIG"
install -m 0600 "$OLD_CONFIG/agents.toml" "$NEW_CONFIG/agents.toml"
install -m 0600 "$OLD_CONFIG/arsd.env" "$NEW_CONFIG/arsd.env"
```

Repeat the install, validate, doctor, render, and review commands with:

```bash
export ARS_VERSION="$NEW_VERSION"
export ARS_RUNTIME="$ARS_RELEASE_ROOT/$NEW_VERSION/venv"
export ARS_CONFIG="$NEW_CONFIG"
```

When the candidate is ready, retain a byte-for-byte copy of the accepted unit
and perform the authorized service cutover:

```bash
install -m 0600 "$ARS_UNIT" "$ARS_UNIT.previous"
install -m 0600 "$UNIT_CANDIDATE" "$ARS_UNIT"
rm -f "$UNIT_CANDIDATE"
systemctl --user daemon-reload
systemctl --user restart "$ARS_SERVICE"
systemctl --user status --no-pager "$ARS_SERVICE"
```

Then repeat `server_info`, validate/doctor checkpoints, the denied-action canary
for affected agent/profile behavior, and a new Run plus reuse of an existing
accepted Session. A package upgrade or daemon restart alone does not invalidate
Sessions. Resume callers only after verification; that resume is the new
cutover decision.

If the target changes the agents file, the restart is required because the
registry snapshot changed. If only an independently managed external agent was
upgraded behind the same registered command, this ARS upgrade procedure does
not apply and ARS needs no restart.

## 10. Roll back to the retained runtime and configuration

Rollback is a deployment decision, not a console command. ARS intentionally has
no `promote`, `rollback`, or `--force` command.

Before rollback, pause callers and drain active Runs exactly as for upgrade.
Confirm the prior virtual environment, prior config directory, and
`$ARS_UNIT.previous` are retained and mode-correct. Review the previous unit: it
must name the prior immutable interpreter and prior intended config while
keeping the same state root and socket.

```bash
export ARS_VERSION='<OLD_VERSION>'
export ARS_RUNTIME="$ARS_RELEASE_ROOT/$ARS_VERSION/venv"
export ARS_CONFIG="$ARS_CONFIG_ROOT/releases/$ARS_VERSION"
export ARS_EXPECTED_VERSION="$ARS_VERSION"

test -x "$ARS_RUNTIME/bin/python"
test -r "$ARS_CONFIG/agents.toml"
test -r "$ARS_CONFIG/arsd.env"
"$ARS_RUNTIME/bin/agent-run-supervisor" agents validate \
  --agents-file "$ARS_CONFIG/agents.toml"
"$ARS_RUNTIME/bin/agent-run-supervisor" agents doctor \
  --agents-file "$ARS_CONFIG/agents.toml" --agent example-agent --no-probe
grep -F "ExecStart=$ARS_RUNTIME/bin/python" "$ARS_UNIT.previous"
grep -F -- "--agents-file $ARS_CONFIG/agents.toml" "$ARS_UNIT.previous"
grep -Fx 'Restart=on-failure' "$ARS_UNIT.previous"
grep -Fx 'KillMode=control-group' "$ARS_UNIT.previous"
```

Perform the authorized rollback and preserve the rejected candidate for review:

```bash
install -m 0600 "$ARS_UNIT" "$ARS_UNIT.rejected"
install -m 0600 "$ARS_UNIT.previous" "$ARS_UNIT"
systemctl --user daemon-reload
systemctl --user restart "$ARS_SERVICE"
systemctl --user status --no-pager "$ARS_SERVICE"
```

Repeat `server_info`, doctor (including the zero-prompt probe), the per-agent
denied-action canary, one new Run, and same-Session continuity verification.
Resume callers only after acceptance.

!!! danger "Rollback must understand durable compatibility"

    Never delete, rewrite, migrate, or downgrade `$ARS_STATE` as an incidental
    rollback step. A prior runtime may refuse durable facts written by a newer
    incompatible schema; refusal is safer than guessing. If release notes say a
    downgrade is not data-compatible, stop ingress and make a separate,
    explicitly authorized runtime-data decision. There is no second runtime,
    fallback transport, automatic replay, or silent `session/new` on a reuse
    failure.

## 11. Uninstall

Uninstall has independent service, package, configuration, and state boundaries.
Choose each deletion explicitly.

### 11.1 Stop and remove the service

First pause callers and, if continuity matters, drain active Runs. Disabling
future starts and stopping the current daemon are separate actions:

```bash
systemctl --user disable "$ARS_SERVICE"
systemctl --user stop "$ARS_SERVICE"
systemctl --user status --no-pager "$ARS_SERVICE" || true
rm -f "$ARS_UNIT" "$ARS_UNIT.previous" "$ARS_UNIT.rejected"
systemctl --user daemon-reload
systemctl --user reset-failed "$ARS_SERVICE" || true
```

Checkpoint:

```bash
systemctl --user is-enabled "$ARS_SERVICE" || true
systemctl --user is-active "$ARS_SERVICE" || true
test ! -S "$ARS_SOCKET"
```

The expected service results are disabled/not-found and inactive/not-found, and
no socket remains.

### 11.2 Remove ARS package runtimes

Only after rollback is no longer required, remove the release-specific virtual
environments you installed:

```bash
rm -rf "$ARS_RELEASE_ROOT/<X.Y.Z>/venv"
```

Repeat only for versions you deliberately retire, then remove empty parent
release directories. This removes ARS package code and its Python dependencies.
It does **not** remove any external agent or adapter; use that software's own
package manager if you separately decide to remove it.

### 11.3 Optionally remove local configuration

Configuration deletion is separate from package and service removal:

```bash
rm -rf "$ARS_CONFIG_ROOT/releases/<X.Y.Z>"
```

Repeat only for configuration releases you deliberately retire. This removes
the local agents file and daemon environment values for that release. It does
not remove agent-owned credentials or state, and ARS must not be used to remove
them.

### 11.4 Optionally delete ARS durable state

`$ARS_STATE` contains durable ARS Run evidence and Session bindings. Deleting it
is irreversible, destroys continuity and audit history, and is **not required**
to uninstall the service or package. Retain or archive it under your data policy
unless a separate authorized deletion decision says otherwise.

Only for an explicitly approved permanent data deletion:

```bash
rm -rf "$ARS_STATE"
```

After the selected boundaries are complete, remove empty ARS parent directories
only if they contain nothing else you intend to retain. External agent homes,
credential stores, plugins, caches, configuration, and conversations remain
outside every ARS uninstall boundary.
