"""In-repo fake ACP agent subprocess for L2 driver tests.

Speaks real newline-delimited JSON-RPC 2.0 over stdio, scripted via the
``FAKE_AGENT_SCRIPT`` (JSON) and ``FAKE_AGENT_TRACE`` environment variables.
It is spawned through ``ManagedProcess`` by the tests — never a driver mock.
L2-only test asset: never product runtime, never production evidence.

Script keys (all optional):
- ``session_id``: external session id returned by session/new.
- ``load_session_advertised``: initialize capability flag (default True).
- ``initial_options`` / ``post_model_options``: wire-shaped config option
  lists; setting the model selector swaps the whole set to the post-model
  list (models the model-dependent option set).
- ``wrong_readback``: {config_id: value} — respond with a different current
  value than requested (inexact-readback fault).
- ``omit_initial_options``: session/new returns null configOptions.
- ``malformed_frame_on_initialize``: emit a garbage line and exit.
- ``hang_on_set_config`` / ``exit_on_set_config`` /
  ``exit_before_prompt_response`` / ``hang_prompt_until_cancel``: fault taps.
- ``silent_new_on_load``: session/load succeeds but later updates carry a
  new session id (silent recreation).
- ``load_fails``: session/load returns a JSON-RPC error.
- ``replay_on_load``: list of texts emitted as ``agent_message_chunk``
  session updates before the session/load response (models the official
  adapters' history replay).
- ``final_message``: agent_message_chunk text before end_turn.
- ``nonce_memory``: echo previously prompted nonce text back (used by C9
  switching tests to model context continuity).
- ``fs_read_path``: during the prompt, send a client-bound
  ``fs/read_text_file`` request for that path and echo the outcome in the
  final message (``FS_CONTENT:<text>`` on success, ``FS_DENIED`` on error).
- ``prompt_tool_updates``: raw session-update dicts notified during the
  prompt before the final message (models agent-owned tool activity that
  never asked for permission — the A4-S2 rogue-write shape).
- ``ask_permission``: ``{"kind":..., "path":..., "content":...}`` — during
  the prompt, send ``session/request_permission`` with OpenCode-shaped
  options (once/always/reject); on an allow outcome write ``path`` and
  answer ``ASK_ALLOWED``, otherwise write nothing and answer ``ASK_DENIED``.
- ``echo_env``: final message becomes ``ENV:<value>`` of that environment
  variable (``ENV_MISSING`` when unset) — proves spawn-env injection.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _default_initial_options() -> list[dict[str, Any]]:
    return [
        {
            "id": "model",
            "name": "Model",
            "type": "select",
            "currentValue": "provider/base",
            "options": [
                {"value": "provider/base", "name": "Base"},
                {"value": "kimi-for-coding/k3", "name": "K3"},
            ],
        },
        {
            "id": "effort",
            "name": "Effort",
            "type": "select",
            "currentValue": "high",
            "options": [{"value": "high", "name": "High"}],
        },
    ]


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _result(request_id: Any, result: Any) -> None:
    _emit({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, code: int, message: str) -> None:
    _emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


class FakeAgent:
    def __init__(self, script: dict[str, Any], trace_path: str | None) -> None:
        self.script = script
        self.trace_path = trace_path
        self.session_id = script.get("session_id", "fake-external-session-1")
        initial = script.get("initial_options") or _default_initial_options()
        self.options = {option["id"]: dict(option) for option in initial}
        self.pending_prompt_id: Any = None
        self.pending_fs_prompt_id: Any = None
        self.pending_permission_prompt_id: Any = None
        self.update_session_id: str | None = None
        self.remembered: list[str] = []

    def _trace(self, method: str) -> None:
        if not self.trace_path:
            return
        with open(self.trace_path, "a", encoding="utf-8") as handle:
            handle.write(method + "\n")

    def _options_list(self) -> list[dict[str, Any]]:
        return [dict(option) for option in self.options.values()]

    def _notify_update(self, update: dict[str, Any]) -> None:
        _emit(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": self.update_session_id or self.session_id,
                    "update": update,
                },
            }
        )

    # -- dispatch ----------------------------------------------------------

    def handle(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method is None:
            if message.get("id") == "fs-req-1" and self.pending_fs_prompt_id is not None:
                self._on_fs_response(message)
            elif (
                message.get("id") == "perm-req-1"
                and self.pending_permission_prompt_id is not None
            ):
                self._on_permission_response(message)
            return
        self._trace(method)
        params = message.get("params") or {}
        if method == "initialize":
            self._on_initialize(request_id, params)
        elif method == "session/new":
            self._on_new(request_id)
        elif method == "session/load":
            self._on_load(request_id, params)
        elif method == "session/set_config_option":
            self._on_set_config(request_id, params)
        elif method == "session/prompt":
            self._on_prompt(request_id, params)
        elif method == "session/cancel":
            self._on_cancel()
        elif method == "session/close":
            _result(request_id, {})
        elif request_id is not None:
            _error(request_id, -32601, f"method not found: {method}")

    def _on_initialize(self, request_id: Any, params: dict[str, Any]) -> None:
        if self.script.get("malformed_frame_on_initialize"):
            sys.stdout.write("this line is not a json-rpc frame\n")
            sys.stdout.flush()
            sys.exit(0)
        _result(
            request_id,
            {
                "protocolVersion": params.get("protocolVersion", 1),
                "agentCapabilities": {
                    "loadSession": self.script.get("load_session_advertised", True)
                },
                "agentInfo": {"name": "fake-acp-agent", "version": "1.0.0"},
            },
        )

    def _on_new(self, request_id: Any) -> None:
        config_options = (
            None if self.script.get("omit_initial_options") else self._options_list()
        )
        _result(request_id, {"sessionId": self.session_id, "configOptions": config_options})

    def _on_load(self, request_id: Any, params: dict[str, Any]) -> None:
        if self.script.get("load_fails"):
            _error(request_id, -32603, "session load rejected by fake script")
            return
        if self.script.get("silent_new_on_load"):
            # Model a silent recreation: updates start carrying a new external
            # session id, emitted before the load response and before every
            # config response so the client observes the identity change.
            self.update_session_id = "fake-recreated-session-2"
            self._notify_update({"sessionUpdate": "session_info_update"})
        for text in self.script.get("replay_on_load", []):
            # History replay precedes the load response, like the official
            # adapters: assistant chunks for turns of *previous* Runs.
            self._notify_update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                }
            )
        _result(request_id, {"configOptions": self._options_list()})

    def _on_set_config(self, request_id: Any, params: dict[str, Any]) -> None:
        if self.script.get("hang_on_set_config"):
            return
        if self.script.get("exit_on_set_config"):
            sys.exit(1)
        config_id = params.get("configId")
        value = params.get("value")
        if value in self.script.get("reject_set_config_values", []):
            _error(request_id, -32602, f"value rejected by fake script: {value}")
            return
        if self.update_session_id is not None:
            self._notify_update({"sessionUpdate": "session_info_update"})
        readback = self.script.get("wrong_readback", {}).get(config_id, value)
        if config_id == "model":
            by_value = self.script.get("post_model_options_by_value", {})
            if value in by_value:
                self.options = {
                    option["id"]: dict(option) for option in by_value[value]
                }
            elif "post_model_options" in self.script:
                self.options = {
                    option["id"]: dict(option)
                    for option in self.script["post_model_options"]
                }
        if config_id in self.options:
            self.options[config_id]["currentValue"] = readback
        _result(request_id, {"configOptions": self._options_list()})

    def _on_prompt(self, request_id: Any, params: dict[str, Any]) -> None:
        if self.script.get("exit_before_prompt_response"):
            sys.exit(1)
        for block in params.get("prompt") or []:
            text = block.get("text")
            if isinstance(text, str):
                self.remembered.append(text)
        for update in self.script.get("prompt_tool_updates", []):
            self._notify_update(update)
        if self.script.get("ask_permission"):
            ask = self.script["ask_permission"]
            self.pending_permission_prompt_id = request_id
            _emit(
                {
                    "jsonrpc": "2.0",
                    "id": "perm-req-1",
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": self.update_session_id or self.session_id,
                        "toolCall": {
                            "toolCallId": "perm-call-1",
                            "title": "Scripted permission ask",
                            "kind": ask.get("kind", "edit"),
                            "status": "pending",
                        },
                        "options": [
                            {"optionId": "once", "name": "Allow once", "kind": "allow_once"},
                            {"optionId": "always", "name": "Always allow", "kind": "allow_always"},
                            {"optionId": "reject", "name": "Reject", "kind": "reject_once"},
                        ],
                    },
                }
            )
            return
        if self.script.get("fs_read_path"):
            self.pending_fs_prompt_id = request_id
            _emit(
                {
                    "jsonrpc": "2.0",
                    "id": "fs-req-1",
                    "method": "fs/read_text_file",
                    "params": {
                        "sessionId": self.update_session_id or self.session_id,
                        "path": self.script["fs_read_path"],
                    },
                }
            )
            return
        if self.script.get("hang_prompt_until_cancel"):
            self.pending_prompt_id = request_id
            return
        message = self.script.get("final_message", "FAKE_AGENT_OK")
        if self.script.get("echo_env"):
            value = os.environ.get(self.script["echo_env"])
            message = f"ENV:{value}" if value is not None else "ENV_MISSING"
        if self.script.get("nonce_memory"):
            message = " ".join(self.remembered[:-1]) or message
        self._notify_update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": message},
            }
        )
        _result(
            request_id,
            {
                "stopReason": "end_turn",
                "usage": {"totalTokens": 30, "inputTokens": 20, "outputTokens": 10},
            },
        )

    def _on_fs_response(self, message: dict[str, Any]) -> None:
        prompt_id = self.pending_fs_prompt_id
        self.pending_fs_prompt_id = None
        result = message.get("result")
        if isinstance(result, dict) and isinstance(result.get("content"), str):
            text = f"FS_CONTENT:{result['content']}"
        else:
            text = "FS_DENIED"
        self._notify_update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            }
        )
        _result(
            prompt_id,
            {
                "stopReason": "end_turn",
                "usage": {"totalTokens": 30, "inputTokens": 20, "outputTokens": 10},
            },
        )

    def _on_permission_response(self, message: dict[str, Any]) -> None:
        prompt_id = self.pending_permission_prompt_id
        self.pending_permission_prompt_id = None
        ask = self.script.get("ask_permission") or {}
        result = message.get("result") or {}
        outcome = result.get("outcome") or {}
        allowed = outcome.get("outcome") == "selected" and outcome.get(
            "optionId"
        ) in ("once", "always")
        if allowed:
            path = ask.get("path")
            if path:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(ask.get("content", "DENIED_CANARY"))
            text = "ASK_ALLOWED"
        else:
            text = "ASK_DENIED"
        self._notify_update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            }
        )
        _result(
            prompt_id,
            {
                "stopReason": "end_turn",
                "usage": {"totalTokens": 30, "inputTokens": 20, "outputTokens": 10},
            },
        )

    def _on_cancel(self) -> None:
        if self.pending_prompt_id is not None:
            _result(self.pending_prompt_id, {"stopReason": "cancelled"})
            self.pending_prompt_id = None


def main() -> None:
    script = json.loads(os.environ.get("FAKE_AGENT_SCRIPT", "{}"))
    trace_path = os.environ.get("FAKE_AGENT_TRACE")
    agent = FakeAgent(script, trace_path)
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        agent.handle(message)


if __name__ == "__main__":
    main()
