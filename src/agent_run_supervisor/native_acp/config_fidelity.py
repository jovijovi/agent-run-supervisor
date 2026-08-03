"""Exact-or-zero configuration fidelity state machine (PRD R3).

The single-Run sequence is explicit: initial option discovery → set model →
consume the complete model-dependent set → rediscover effort from that fresh
set only → set effort → consume the complete set → exact readback →
ready-to-prompt. Effort discovery structurally reads only the stored
post-set-model set, so skipping rediscovery is impossible. Any violation
raises :class:`ConfigFidelityError`, which callers convert into a zero-Turn
pre-dispatch failure.

A profile may additionally freeze a **permission mode** selector (B4). That
selector is set *first* — before model and effort — with its own exact
readback, and the final post-set-effort readback must still show it exact, so
prompt dispatch is impossible unless mode, model, and effort are all proven.
This exists because the official Claude adapter resolves its initial permission
mode from ambient settings and auto-allows tool calls in-process while that
mode is ``bypassPermissions``: a session that starts there would never consult
the frozen grant. Profiles without the binding keep the exact legacy phases,
snapshot labels, and wire sequence.

Two **configuration-fidelity modes** exist, and a profile declares exactly one.

``separate-selectors`` is the sequence above: an independent effort selector is
rediscovered from the post-set-model set, set, and read back exactly.

``model-only`` describes an agent whose model selector *is* the whole
configuration. There is no independent effort selector to discover, so the
sequence stops at the exact model readback: no effort option is discovered, no
effort ``set_config_option`` is dispatched, and the effective effort is the
shared :data:`EFFORT_NOT_APPLICABLE` sentinel. A request for such an agent must
carry that sentinel; any other effort is refused before the prompt rather than
silently ignored.

The selector value stays **opaque** in both modes. A model literal such as
``grok-4.5[effort=high,fast=true]`` is set and read back byte-for-byte: nothing
parses it, infers an effort from it, maps a model name, or treats an agent's
ACP ``mode`` selector as an effort.

Option inputs are wire-shaped plain dicts (``id`` / ``currentValue`` /
``options``) — the SDK-facing driver dumps models by alias before they reach
this stdlib-only module.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class ConfigFidelityError(RuntimeError):
    """Exact configuration could not be proven; the Run must not prompt."""


# The one effective-effort value a model-only agent can honestly report, and
# therefore the only requested effort such a Run may carry. Declared once: a
# second spelling of it anywhere would be a second contract.
EFFORT_NOT_APPLICABLE = "N/A"

FIDELITY_SEPARATE_SELECTORS = "separate-selectors"
FIDELITY_MODEL_ONLY = "model-only"
FIDELITY_MODES: tuple[str, ...] = (
    FIDELITY_SEPARATE_SELECTORS,
    FIDELITY_MODEL_ONLY,
)


def validate_fidelity_pairing(
    *,
    fidelity_mode: str,
    effort_selector_id: str | None,
    requested_effort: str,
) -> None:
    """One rule, asked wherever a mode meets a selector and a request.

    It lives here rather than in each caller so admission, the machine, and the
    diagnostic probe cannot drift into three readings of the same contract.
    """
    if fidelity_mode not in FIDELITY_MODES:
        raise ConfigFidelityError(
            f"unknown configuration fidelity mode {fidelity_mode!r} "
            f"(known: {list(FIDELITY_MODES)})"
        )
    if fidelity_mode == FIDELITY_MODEL_ONLY:
        if effort_selector_id is not None:
            raise ConfigFidelityError(
                "model-only fidelity has no effort selector; declaring "
                f"{effort_selector_id!r} would name a selector no Run ever sets"
            )
        if requested_effort != EFFORT_NOT_APPLICABLE:
            raise ConfigFidelityError(
                f"model-only fidelity requires effort {EFFORT_NOT_APPLICABLE!r}, "
                f"requested {requested_effort!r}"
            )
        return
    if not isinstance(effort_selector_id, str) or not effort_selector_id:
        raise ConfigFidelityError(
            "separate-selector fidelity requires an effort selector id"
        )


_PHASE_INIT = "init"
_PHASE_INITIAL_OPTIONS = "initial_options"
_PHASE_MODE_PLANNED = "mode_planned"
_PHASE_POST_MODE = "post_mode"
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
        effort_selector_id: str | None,
        requested_model: str,
        requested_effort: str,
        permission_mode_selector_id: str | None = None,
        required_permission_mode: str | None = None,
        fidelity_mode: str = FIDELITY_SEPARATE_SELECTORS,
    ) -> None:
        if (permission_mode_selector_id is None) != (required_permission_mode is None):
            raise ConfigFidelityError(
                "a permission-mode selector and its required value must be "
                "declared together"
            )
        validate_fidelity_pairing(
            fidelity_mode=fidelity_mode,
            effort_selector_id=effort_selector_id,
            requested_effort=requested_effort,
        )
        self._fidelity_mode = fidelity_mode
        self._model_selector_id = model_selector_id
        self._effort_selector_id = effort_selector_id
        self._requested_model = requested_model
        self._requested_effort = requested_effort
        self._permission_mode_selector_id = permission_mode_selector_id
        self._required_permission_mode = required_permission_mode
        self._phase = _PHASE_INIT
        self._initial_options: dict[str, _Option] | None = None
        self._post_mode_options: dict[str, _Option] | None = None
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
    def fidelity_mode(self) -> str:
        return self._fidelity_mode

    @property
    def is_model_only(self) -> bool:
        return self._fidelity_mode == FIDELITY_MODEL_ONLY

    @property
    def has_permission_mode(self) -> bool:
        return self._permission_mode_selector_id is not None

    @property
    def required_permission_mode(self) -> str | None:
        return self._required_permission_mode

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

    def permission_mode_plan(self) -> str:
        """Verify the required permission mode is advertised; return its id."""
        if self._permission_mode_selector_id is None:
            raise ConfigFidelityError(
                "permission_mode_plan requires a declared permission-mode selector"
            )
        self._expect_phase(_PHASE_INITIAL_OPTIONS, "permission_mode_plan")
        assert self._initial_options is not None
        option = self._initial_options.get(self._permission_mode_selector_id)
        if option is None or not option.is_select:
            raise ConfigFidelityError(
                f"permission mode selector {self._permission_mode_selector_id!r} "
                "is not advertised as a select option"
            )
        if self._required_permission_mode not in option.choices:
            raise ConfigFidelityError(
                f"required permission mode {self._required_permission_mode!r} is "
                f"not advertised (choices: {sorted(option.choices)})"
            )
        self._phase = _PHASE_MODE_PLANNED
        return self._permission_mode_selector_id

    def record_post_mode_options(
        self, options: Sequence[Mapping[str, Any]] | None
    ) -> None:
        """Consume the complete post-set-mode set and require exact readback."""
        self._expect_phase(_PHASE_MODE_PLANNED, "record_post_mode_options")
        parsed = _parse_options(options, phase="post-set-permission-mode")
        # Snapshot before the check: a refused Run must still keep the mode the
        # agent actually reported as durable evidence.
        self._record_snapshot("post_mode", options or [])
        self._require_permission_mode_exact(parsed, "permission mode readback")
        self._post_mode_options = parsed
        self._phase = _PHASE_POST_MODE

    def _require_permission_mode_exact(
        self, parsed: dict[str, _Option], label: str
    ) -> None:
        assert self._permission_mode_selector_id is not None
        mode = parsed.get(self._permission_mode_selector_id)
        effective = None if mode is None else mode.current_value
        if effective != self._required_permission_mode:
            raise ConfigFidelityError(
                f"{label} mismatch: required "
                f"{self._required_permission_mode!r}, effective {effective!r}"
            )

    def model_plan(self) -> str:
        """Verify the requested model is advertised; return the selector id."""
        if self._permission_mode_selector_id is None:
            self._expect_phase(_PHASE_INITIAL_OPTIONS, "model_plan")
            assert self._initial_options is not None
            discovered = self._initial_options
        else:
            # Structural, not conventional: with a frozen permission mode the
            # model is planned only from the post-set-mode set, so skipping the
            # mode leg is impossible.
            self._expect_phase(_PHASE_POST_MODE, "model_plan")
            assert self._post_mode_options is not None
            discovered = self._post_mode_options
        option = discovered.get(self._model_selector_id)
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
        """Consume the complete model-dependent option set.

        Under model-only fidelity this is also the *final* readback: there is
        no effort leg after it, so a verified machine — and therefore a
        reachable prompt — exists only once the exact model literal has been
        read back here.
        """
        self._expect_phase(_PHASE_MODEL_PLANNED, "record_post_model_options")
        parsed = _parse_options(options, phase="post-set-model")
        model = parsed.get(self._model_selector_id)
        if model is None or model.current_value != self._requested_model:
            observed = None if model is None else model.current_value
            raise ConfigFidelityError(
                f"model readback mismatch: requested {self._requested_model!r}, "
                f"effective {observed!r}"
            )
        if self.is_model_only and self._permission_mode_selector_id is not None:
            # The same re-proof the effort leg performs for the other mode: a
            # mode restored as a side effect of the model switch must not reach
            # a prompt.
            self._require_permission_mode_exact(
                parsed, "permission mode readback after model set"
            )
        self._post_model_options = parsed
        self._record_snapshot("post_model", options or [])
        self._phase = _PHASE_VERIFIED if self.is_model_only else _PHASE_POST_MODEL

    def effort_plan(self) -> str:
        """Rediscover effort from the post-set-model set only."""
        if self.is_model_only:
            raise ConfigFidelityError(
                "model-only fidelity discovers no effort selector; effort_plan "
                "is unreachable"
            )
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
        if self.is_model_only:
            raise ConfigFidelityError(
                "model-only fidelity sets no effort option; there is no "
                "post-set-effort set to consume"
            )
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
        if self._permission_mode_selector_id is not None:
            # A mode restored between the mode leg and here (silently, or as a
            # side effect of the model switch) must not reach a prompt.
            self._require_permission_mode_exact(
                parsed, "permission mode readback after effort set"
            )
        self._phase = _PHASE_VERIFIED

    def record_option_update(self, options: Sequence[Mapping[str, Any]]) -> None:
        """Record an agent-pushed config_option_update as evidence."""
        self._record_snapshot("option_update", options)

    def require_ready(self) -> tuple[str, str]:
        """The prompt gate: only a verified machine releases the exact pair.

        Under model-only fidelity the effort half is the ``N/A`` sentinel the
        request had to carry, so what is returned and persisted is exactly what
        was proven — never a value inferred from the model literal.
        """
        if self._phase != _PHASE_VERIFIED:
            raise ConfigFidelityError(
                f"prompt is unreachable: config fidelity phase is {self._phase!r}, "
                "not 'verified'"
            )
        return self._requested_model, self._requested_effort
