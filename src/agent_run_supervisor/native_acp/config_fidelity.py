"""Exact-or-zero configuration fidelity state machine (PRD R3).

The single-Run sequence is explicit: initial option discovery → set model →
consume the complete model-dependent set → rediscover effort from that fresh
set only → set effort → consume the complete set → exact readback →
ready-to-prompt. Effort discovery structurally reads only the stored
post-set-model set, so skipping rediscovery is impossible. Any violation
raises :class:`ConfigFidelityError`, which callers convert into a zero-Turn
pre-dispatch failure.

Option inputs are wire-shaped plain dicts (``id`` / ``currentValue`` /
``options``) — the SDK-facing driver dumps models by alias before they reach
this stdlib-only module.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class ConfigFidelityError(RuntimeError):
    """Exact configuration could not be proven; the Run must not prompt."""


_PHASE_INIT = "init"
_PHASE_INITIAL_OPTIONS = "initial_options"
_PHASE_MODEL_PLANNED = "model_planned"
_PHASE_POST_MODEL = "post_model"
_PHASE_EFFORT_PLANNED = "effort_planned"
_PHASE_VERIFIED = "verified"


class _Option:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        option_id = payload.get("id")
        if not isinstance(option_id, str) or not option_id:
            raise ConfigFidelityError(f"config option without a usable id: {payload!r}")
        self.option_id = option_id
        self.current_value = payload.get("currentValue")
        choices: list[str] = []
        raw_options = payload.get("options")
        self.is_select = payload.get("type") == "select" or (
            "type" not in payload and isinstance(raw_options, list)
        )
        if self.is_select:
            for entry in raw_options:
                if not isinstance(entry, Mapping):
                    continue
                value = entry.get("value")
                if isinstance(value, str):
                    choices.append(value)
                    continue
                nested = entry.get("options")
                if isinstance(nested, list):  # grouped select options
                    choices.extend(
                        option.get("value")
                        for option in nested
                        if isinstance(option, Mapping)
                        and isinstance(option.get("value"), str)
                    )
        self.choices = tuple(choices)


def _parse_options(options: Sequence[Mapping[str, Any]] | None, *, phase: str):
    if options is None:
        raise ConfigFidelityError(
            f"agent advertised no config options at {phase}; exact configuration "
            "is impossible — failing closed"
        )
    parsed: dict[str, _Option] = {}
    for payload in options:
        option = _Option(payload)
        parsed[option.option_id] = option
    return parsed


class ConfigFidelityMachine:
    """One Run's exact configuration sequence as an explicit state machine."""

    def __init__(
        self,
        *,
        model_selector_id: str,
        effort_selector_id: str,
        requested_model: str,
        requested_effort: str,
    ) -> None:
        self._model_selector_id = model_selector_id
        self._effort_selector_id = effort_selector_id
        self._requested_model = requested_model
        self._requested_effort = requested_effort
        self._phase = _PHASE_INIT
        self._initial_options: dict[str, _Option] | None = None
        self._post_model_options: dict[str, _Option] | None = None
        self._snapshots: list[tuple[str, list[dict[str, Any]]]] = []

    # -- observability -----------------------------------------------------

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def requested_model(self) -> str:
        return self._requested_model

    @property
    def requested_effort(self) -> str:
        return self._requested_effort

    @property
    def snapshots(self) -> list[tuple[str, list[dict[str, Any]]]]:
        """Discovery snapshots (label, wire-shaped options) for evidence."""
        return list(self._snapshots)

    def _record_snapshot(
        self, label: str, options: Sequence[Mapping[str, Any]]
    ) -> None:
        self._snapshots.append((label, [dict(option) for option in options]))

    def _expect_phase(self, expected: str, action: str) -> None:
        if self._phase != expected:
            raise ConfigFidelityError(
                f"{action} is invalid in phase {self._phase!r} "
                f"(requires {expected!r})"
            )

    # -- sequence ----------------------------------------------------------

    def record_initial_options(
        self, options: Sequence[Mapping[str, Any]] | None
    ) -> None:
        self._expect_phase(_PHASE_INIT, "record_initial_options")
        self._initial_options = _parse_options(options, phase="session open")
        self._record_snapshot("initial", options or [])
        self._phase = _PHASE_INITIAL_OPTIONS

    def model_plan(self) -> str:
        """Verify the requested model is advertised; return the selector id."""
        self._expect_phase(_PHASE_INITIAL_OPTIONS, "model_plan")
        assert self._initial_options is not None
        option = self._initial_options.get(self._model_selector_id)
        if option is None or not option.is_select:
            raise ConfigFidelityError(
                f"model selector {self._model_selector_id!r} is not advertised "
                "as a select option"
            )
        if self._requested_model not in option.choices:
            raise ConfigFidelityError(
                f"requested model {self._requested_model!r} is not advertised "
                f"(choices: {sorted(option.choices)})"
            )
        self._phase = _PHASE_MODEL_PLANNED
        return self._model_selector_id

    def record_post_model_options(
        self, options: Sequence[Mapping[str, Any]] | None
    ) -> None:
        """Consume the complete model-dependent option set."""
        self._expect_phase(_PHASE_MODEL_PLANNED, "record_post_model_options")
        parsed = _parse_options(options, phase="post-set-model")
        model = parsed.get(self._model_selector_id)
        if model is None or model.current_value != self._requested_model:
            observed = None if model is None else model.current_value
            raise ConfigFidelityError(
                f"model readback mismatch: requested {self._requested_model!r}, "
                f"effective {observed!r}"
            )
        self._post_model_options = parsed
        self._record_snapshot("post_model", options or [])
        self._phase = _PHASE_POST_MODEL

    def effort_plan(self) -> str:
        """Rediscover effort from the post-set-model set only."""
        self._expect_phase(_PHASE_POST_MODEL, "effort_plan")
        assert self._post_model_options is not None
        option = self._post_model_options.get(self._effort_selector_id)
        if option is None or not option.is_select:
            raise ConfigFidelityError(
                f"effort selector {self._effort_selector_id!r} is absent from "
                "the post-set-model option set"
            )
        if self._requested_effort not in option.choices:
            raise ConfigFidelityError(
                f"requested effort {self._requested_effort!r} is not advertised "
                f"in the post-set-model set (choices: {sorted(option.choices)})"
            )
        self._phase = _PHASE_EFFORT_PLANNED
        return self._effort_selector_id

    def record_post_effort_options(
        self, options: Sequence[Mapping[str, Any]] | None
    ) -> None:
        """Consume the complete set and require the exact effective pair."""
        self._expect_phase(_PHASE_EFFORT_PLANNED, "record_post_effort_options")
        parsed = _parse_options(options, phase="post-set-effort")
        self._record_snapshot("post_effort", options or [])
        model = parsed.get(self._model_selector_id)
        effort = parsed.get(self._effort_selector_id)
        effective_model = None if model is None else model.current_value
        effective_effort = None if effort is None else effort.current_value
        if effective_model != self._requested_model:
            raise ConfigFidelityError(
                f"model readback mismatch after effort set: requested "
                f"{self._requested_model!r}, effective {effective_model!r}"
            )
        if effective_effort != self._requested_effort:
            raise ConfigFidelityError(
                f"effort readback mismatch: requested {self._requested_effort!r}, "
                f"effective {effective_effort!r}"
            )
        self._phase = _PHASE_VERIFIED

    def record_option_update(self, options: Sequence[Mapping[str, Any]]) -> None:
        """Record an agent-pushed config_option_update as evidence."""
        self._record_snapshot("option_update", options)

    def require_ready(self) -> tuple[str, str]:
        """The prompt gate: only a verified machine releases the exact pair."""
        if self._phase != _PHASE_VERIFIED:
            raise ConfigFidelityError(
                f"prompt is unreachable: config fidelity phase is {self._phase!r}, "
                "not 'verified'"
            )
        return self._requested_model, self._requested_effort
