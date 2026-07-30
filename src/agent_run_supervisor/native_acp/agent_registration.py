"""The operator-owned Agent Registration value and its bounded grammars.

Layer 1b of the runtime-authority split. A registration says which agent an
already-registered, source-closed profile is being instantiated as: its ACP
name, its argv tokens, its selector ids and value domains, the capabilities it
narrows, which source-registered mediation binding it selects, and which
credential slots it declares.

Everything it may say either **selects inside** or **narrows** something the
source contract already declared. It can supply no executable, path, digest,
version, env key, launch kind, protocol version, or capability *requirement* —
those are not fields here, so the refusal is structural rather than filtered.

This module is **pure**. It performs no filesystem query, opens nothing, and
resolves no path, mirroring the :mod:`agent_run_supervisor.arsd.operand`
precedent: a grammar that decides on text alone cannot be raced, redirected, or
made to disagree with what a later reader sees. The single reader of a Binding
root stays :mod:`agent_run_supervisor.native_acp.runtime_binding`, which calls
into here with bytes it has already decoded.

``agent_registration_hash`` covers the whole payload **except** ``provenance``,
so re-recording a receipt does not retire an agent's Sessions while any
compatibility-bearing edit does.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .profile import (
    AgentProfile,
    ProfileValidationError,
    VersionProbeRule,
    is_registered_mediation_binding,
)

REGISTRATION_SCHEMA_VERSION = 1

REGISTRATION_FIELDS = (
    "schema_version",
    "agent_id",
    "contract_identity",
    "acp",
    "launch",
    "config",
    "credentials",
    "provenance",
)
_CONTRACT_IDENTITY_FIELDS = ("profile_id", "profile_revision", "adapter_contract_hash")
_ACP_FIELDS = ("agent_name", "forbidden_capabilities")
_LAUNCH_FIELDS = ("argv_tokens", "version_probe_argv_suffix", "permission_binding_id")
_CONFIG_FIELDS = (
    "model_selector_id",
    "effort_selector_id",
    "registered_models",
    "allowed_efforts",
    "default_model",
    "default_effort",
)
_CREDENTIAL_FIELDS = ("slots", "required_refs")
_PROVENANCE_FIELDS = (
    "created_at",
    "accepted_by",
    "accepted_at",
    "acceptance_receipt",
    "discovery_receipt",
    "permission_canary_receipt",
)
_RECEIPT_FIELDS = ("ref", "sha256")

# An argv token is structurally incapable of being a path or a shell fragment:
# no separator, no ``..`` run, no ``=``, no whitespace, no NUL, ASCII only.
_ARGV_TOKEN_RE = re.compile(r"^(--?[A-Za-z0-9][A-Za-z0-9-]*|[A-Za-z0-9][A-Za-z0-9._-]*)$")
_ARGV_TOKEN_MIN = 1
_ARGV_TOKEN_MAX = 4
_ARGV_TOKEN_CHARS_MAX = 32

_SELECTOR_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

_DOMAIN_MAX = 32
_DOMAIN_VALUE_CHARS_MAX = 128
_CREDENTIAL_SLOTS_MAX = 8
_CREDENTIAL_ID_CHARS_MAX = 128
_TEXT_MAX = 256
_CAPABILITY_MAX = 32
_ACP_NAME_MAX = 128


class AgentRegistrationError(Exception):
    """Fail-closed registration refusal; ``rule`` names the failing rule.

    The message carries the rule and structural facts only — never file bytes,
    credential material, or provenance text.
    """

    def __init__(self, *, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message


def _refuse(rule: str, message: str) -> AgentRegistrationError:
    return AgentRegistrationError(
        rule=rule, message=f"agent registration refused [{rule}]: {message}"
    )


@dataclass(frozen=True)
class AgentRegistration:
    """One operator-authored agent, projected into immutable typed values.

    Every field here is either a value the source contract's bounds admit or a
    narrowing of one. ``provenance`` is recorded and reported; it is never an
    authorization input and never decides admissibility.
    """

    schema_version: int
    agent_id: str
    profile_id: str
    profile_revision: int
    adapter_contract_hash: str
    acp_agent_name: str
    forbidden_capabilities: tuple[str, ...]
    argv_tokens: tuple[str, ...]
    version_probe: VersionProbeRule
    permission_binding_id: str | None
    model_selector_id: str
    effort_selector_id: str
    registered_models: tuple[str, ...]
    allowed_efforts: tuple[str, ...]
    default_model: str
    default_effort: str
    credential_slots: tuple[str, ...]
    required_credential_refs: tuple[str, ...] | None
    provenance: Mapping[str, Any]
    registration_hash: str

    def contract_identity(self) -> dict[str, Any]:
        """The identity a Binding generation for this agent must re-declare."""
        return {
            "profile_id": self.profile_id,
            "profile_revision": self.profile_revision,
            "adapter_contract_hash": self.adapter_contract_hash,
            "agent_id": self.agent_id,
            "agent_registration_hash": self.registration_hash,
        }


# -- canonical hashing --------------------------------------------------------


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def registration_hash(payload: Mapping[str, Any]) -> str:
    """Hash the registration over everything except ``provenance``.

    Provenance is deliberately outside: re-recording an acceptance, discovery,
    or canary receipt is bookkeeping, and making it retire every Session under
    the agent would push operators toward *not* recording receipts. Every
    compatibility-bearing field is inside, so a real edit does retire them.
    """
    material = {key: value for key, value in payload.items() if key != "provenance"}
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


# -- shape helpers ------------------------------------------------------------


def _require_object(value: Any, *, rule: str, surface: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise _refuse(rule, f"{surface} must be a JSON object")
    return value


def _require_exact_fields(
    payload: Mapping[str, Any],
    fields: tuple[str, ...],
    *,
    unknown_rule: str,
    missing_rule: str,
    surface: str,
) -> None:
    unknown = sorted(set(payload) - set(fields))
    if unknown:
        raise _refuse(unknown_rule, f"{surface} carries unknown field(s): {unknown}")
    missing = sorted(set(fields) - set(payload))
    if missing:
        raise _refuse(missing_rule, f"{surface} omits {missing}")


def _require_text(value: Any, *, rule: str, surface: str, maximum: int = _TEXT_MAX) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or not all(ch.isprintable() for ch in value)
    ):
        raise _refuse(rule, f"{surface} must be a short printable string")
    return value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _require_string_list(value: Any, *, rule: str, surface: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _refuse(rule, f"{surface} must be an array of strings")
    return list(value)


# -- the bounded grammars -----------------------------------------------------


def _parse_argv_tokens(value: Any) -> tuple[str, ...]:
    """Tokens that cannot express a path, a redirection, or an assignment."""
    tokens = _require_string_list(value, rule="ARGV_TOKEN_UNSAFE", surface="argv_tokens")
    if not (_ARGV_TOKEN_MIN <= len(tokens) <= _ARGV_TOKEN_MAX):
        raise _refuse(
            "ARGV_TOKEN_UNSAFE",
            f"argv_tokens must hold {_ARGV_TOKEN_MIN}..{_ARGV_TOKEN_MAX} tokens",
        )
    if len(set(tokens)) != len(tokens):
        raise _refuse("ARGV_TOKEN_UNSAFE", "duplicate argv token")
    for token in tokens:
        if not (1 <= len(token) <= _ARGV_TOKEN_CHARS_MAX):
            raise _refuse("ARGV_TOKEN_UNSAFE", "argv token length is out of range")
        if not token.isascii() or _ARGV_TOKEN_RE.fullmatch(token) is None:
            raise _refuse("ARGV_TOKEN_UNSAFE", "argv token is not a bounded ASCII token")
        if ".." in token:
            raise _refuse("ARGV_TOKEN_UNSAFE", "argv token carries a traversal run")
    return tuple(tokens)


def _parse_version_probe(value: Any, contract_rule: VersionProbeRule) -> VersionProbeRule:
    """Only the fixed argv suffix is registration-selected; the rest is code-owned.

    The suffix is validated by ``VersionProbeRule`` itself rather than by a
    second copy of its rules here — the parser, the timeout, and the output
    bound stay exactly as the contract froze them.
    """
    suffix = _require_string_list(
        value, rule="VERSION_PROBE_UNSAFE", surface="version_probe_argv_suffix"
    )
    try:
        return VersionProbeRule(
            argv_suffix=tuple(suffix),
            parser_id=contract_rule.parser_id,
            timeout_seconds=contract_rule.timeout_seconds,
            max_output_bytes=contract_rule.max_output_bytes,
        )
    except ProfileValidationError as exc:
        raise _refuse("VERSION_PROBE_UNSAFE", str(exc)) from None


def _parse_selector_id(value: Any, surface: str) -> str:
    if not isinstance(value, str) or _SELECTOR_ID_RE.fullmatch(value) is None:
        raise _refuse("SELECTOR_ID_UNSAFE", f"{surface} is not a bounded selector id")
    return value


def _parse_domain(value: Any, surface: str) -> tuple[str, ...]:
    entries = _require_string_list(
        value, rule="SELECTOR_DOMAIN_UNSAFE", surface=surface
    )
    if not (1 <= len(entries) <= _DOMAIN_MAX):
        raise _refuse("SELECTOR_DOMAIN_UNSAFE", f"{surface} must hold 1..{_DOMAIN_MAX} entries")
    if len(set(entries)) != len(entries):
        raise _refuse("SELECTOR_DOMAIN_UNSAFE", f"{surface} carries a duplicate entry")
    for entry in entries:
        if (
            not entry
            or len(entry) > _DOMAIN_VALUE_CHARS_MAX
            or not all(ch.isprintable() for ch in entry)
        ):
            raise _refuse("SELECTOR_DOMAIN_UNSAFE", f"{surface} entry is out of range")
    return tuple(entries)


def _parse_capabilities(value: Any, profile: AgentProfile) -> tuple[str, ...]:
    """A registration raises the source floor; it can never lower it."""
    entries = _require_string_list(
        value, rule="CAPABILITY_UNSAFE", surface="forbidden_capabilities"
    )
    if len(entries) > _CAPABILITY_MAX:
        raise _refuse("CAPABILITY_UNSAFE", "forbidden_capabilities exceeds its bound")
    if len(set(entries)) != len(entries):
        raise _refuse("CAPABILITY_UNSAFE", "duplicate forbidden capability")
    for entry in entries:
        _require_text(entry, rule="CAPABILITY_UNSAFE", surface="forbidden capability")
    declared = set(entries)
    floor = set(profile.contract.forbidden_capabilities)
    if not floor <= declared:
        raise _refuse(
            "CAPABILITY_FLOOR_LOWERED",
            f"registration drops source-forbidden capabilities {sorted(floor - declared)}",
        )
    overlap = declared & set(profile.contract.required_capabilities)
    if overlap:
        raise _refuse(
            "CAPABILITY_CONFLICT",
            f"capability declared both required and forbidden: {sorted(overlap)}",
        )
    return tuple(sorted(declared))


def _parse_credentials(payload: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...] | None]:
    _require_exact_fields(
        payload,
        _CREDENTIAL_FIELDS,
        unknown_rule="UNKNOWN_REGISTRATION_FIELD",
        missing_rule="REGISTRATION_FIELD_MISSING",
        surface="credentials",
    )
    slots = _require_string_list(
        payload["slots"], rule="CREDENTIAL_REFS_UNSAFE", surface="credentials.slots"
    )
    if len(slots) > _CREDENTIAL_SLOTS_MAX:
        raise _refuse("CREDENTIAL_REFS_UNSAFE", "credentials.slots exceeds its bound")
    if len(set(slots)) != len(slots):
        raise _refuse("CREDENTIAL_REFS_UNSAFE", "duplicate credential slot")
    for slot in slots:
        _require_text(
            slot,
            rule="CREDENTIAL_REFS_UNSAFE",
            surface="credential slot",
            maximum=_CREDENTIAL_ID_CHARS_MAX,
        )
    raw_refs = payload["required_refs"]
    if raw_refs is None:
        return tuple(slots), None
    refs = _require_string_list(
        raw_refs, rule="CREDENTIAL_REFS_UNSAFE", surface="credentials.required_refs"
    )
    if not set(refs) <= set(slots):
        raise _refuse(
            "CREDENTIAL_REFS_UNSAFE",
            "credentials.required_refs is not a subset of the declared slots",
        )
    return tuple(slots), tuple(refs)


def _require_provenance(payload: Any) -> Mapping[str, Any]:
    """Shape-validated, recorded, and never consulted — C4 verbatim.

    Requiring the record to be well formed and refusing to let it decide
    anything are two separate rules, and both hold: a flawless provenance block
    rescues no unsafe argv token, no lowered capability floor, and no mismatched
    contract identity.
    """
    provenance = _require_object(
        payload, rule="PROVENANCE_FIELD_TYPE", surface="provenance"
    )
    _require_exact_fields(
        provenance,
        _PROVENANCE_FIELDS,
        unknown_rule="UNKNOWN_REGISTRATION_FIELD",
        missing_rule="PROVENANCE_FIELD_MISSING",
        surface="provenance",
    )
    for key in ("created_at", "accepted_by", "accepted_at"):
        _require_text(
            provenance[key], rule="PROVENANCE_FIELD_TYPE", surface=f"provenance {key}"
        )
    for key in ("acceptance_receipt", "discovery_receipt", "permission_canary_receipt"):
        receipt = _require_object(
            provenance[key], rule="PROVENANCE_FIELD_TYPE", surface=key
        )
        _require_exact_fields(
            receipt,
            _RECEIPT_FIELDS,
            unknown_rule="UNKNOWN_REGISTRATION_FIELD",
            missing_rule="PROVENANCE_FIELD_MISSING",
            surface=key,
        )
        _require_text(
            receipt["ref"], rule="PROVENANCE_FIELD_TYPE", surface=f"{key} ref"
        )
        if not _is_sha256(receipt["sha256"]):
            raise _refuse("PROVENANCE_FIELD_TYPE", f"{key} sha256 is not a digest")
    return dict(provenance)


# -- the parser ---------------------------------------------------------------


def parse_registration(
    payload: Mapping[str, Any], *, profile: AgentProfile, expected_agent_id: str
) -> AgentRegistration:
    """Project one decoded registration payload, or refuse by a stable rule.

    ``expected_agent_id`` is the component the caller already descended into, so
    the registration must re-declare it as an explicit machine field: path
    separation alone would let a directory rename silently re-label an agent.
    """
    body = _require_object(
        payload, rule="UNKNOWN_REGISTRATION_FIELD", surface="registration"
    )
    _require_exact_fields(
        body,
        REGISTRATION_FIELDS,
        unknown_rule="UNKNOWN_REGISTRATION_FIELD",
        missing_rule="REGISTRATION_FIELD_MISSING",
        surface="registration",
    )
    contract = profile.contract
    if body["schema_version"] != REGISTRATION_SCHEMA_VERSION or (
        contract.registration_schema_version != REGISTRATION_SCHEMA_VERSION
    ):
        raise _refuse(
            "REGISTRATION_SCHEMA_VERSION",
            "registration schema_version is not the one this contract accepts",
        )
    if body["agent_id"] != expected_agent_id:
        raise _refuse(
            "REGISTRATION_AGENT_MISMATCH",
            "registration declares a different agent than the one resolving it",
        )

    identity = _require_object(
        body["contract_identity"],
        rule="REGISTRATION_CONTRACT_MISMATCH",
        surface="contract_identity",
    )
    _require_exact_fields(
        identity,
        _CONTRACT_IDENTITY_FIELDS,
        unknown_rule="UNKNOWN_REGISTRATION_FIELD",
        missing_rule="REGISTRATION_FIELD_MISSING",
        surface="contract_identity",
    )
    live = {
        "profile_id": profile.profile_id,
        "profile_revision": profile.revision,
        "adapter_contract_hash": profile.adapter_contract_hash(),
    }
    if dict(identity) != live:
        # A contract revision therefore retires every registration accepted
        # under the old hash, closed — exactly as it retires a generation.
        raise _refuse(
            "REGISTRATION_CONTRACT_MISMATCH",
            "registration was accepted under a different contract identity",
        )

    acp = _require_object(body["acp"], rule="UNKNOWN_REGISTRATION_FIELD", surface="acp")
    _require_exact_fields(
        acp,
        _ACP_FIELDS,
        unknown_rule="UNKNOWN_REGISTRATION_FIELD",
        missing_rule="REGISTRATION_FIELD_MISSING",
        surface="acp",
    )
    agent_name = _require_text(
        acp["agent_name"], rule="ACP_NAME_UNSAFE", surface="acp.agent_name",
        maximum=_ACP_NAME_MAX,
    )
    forbidden = _parse_capabilities(acp["forbidden_capabilities"], profile)

    launch = _require_object(
        body["launch"], rule="UNKNOWN_REGISTRATION_FIELD", surface="launch"
    )
    _require_exact_fields(
        launch,
        _LAUNCH_FIELDS,
        unknown_rule="UNKNOWN_REGISTRATION_FIELD",
        missing_rule="REGISTRATION_FIELD_MISSING",
        surface="launch",
    )
    argv_tokens = _parse_argv_tokens(launch["argv_tokens"])
    probe = _parse_version_probe(launch["version_probe_argv_suffix"], contract.version_probe)
    binding_id = launch["permission_binding_id"]
    if binding_id is not None and not is_registered_mediation_binding(binding_id):
        raise _refuse(
            "UNKNOWN_PERMISSION_BINDING",
            "registration selects a mediation binding that source does not own",
        )

    config = _require_object(
        body["config"], rule="UNKNOWN_REGISTRATION_FIELD", surface="config"
    )
    _require_exact_fields(
        config,
        _CONFIG_FIELDS,
        unknown_rule="UNKNOWN_REGISTRATION_FIELD",
        missing_rule="REGISTRATION_FIELD_MISSING",
        surface="config",
    )
    model_selector = _parse_selector_id(
        config["model_selector_id"], "config.model_selector_id"
    )
    effort_selector = _parse_selector_id(
        config["effort_selector_id"], "config.effort_selector_id"
    )
    if model_selector == effort_selector:
        raise _refuse("SELECTOR_ID_UNSAFE", "the two selector ids must be distinct")
    models = _parse_domain(config["registered_models"], "config.registered_models")
    efforts = _parse_domain(config["allowed_efforts"], "config.allowed_efforts")
    default_model = config["default_model"]
    default_effort = config["default_effort"]
    if default_model not in models:
        raise _refuse(
            "SELECTOR_DEFAULT_UNSAFE", "default_model is outside registered_models"
        )
    if default_effort not in efforts:
        raise _refuse(
            "SELECTOR_DEFAULT_UNSAFE", "default_effort is outside allowed_efforts"
        )

    slots, required_refs = _parse_credentials(
        _require_object(
            body["credentials"],
            rule="UNKNOWN_REGISTRATION_FIELD",
            surface="credentials",
        )
    )
    provenance = _require_provenance(body["provenance"])

    return AgentRegistration(
        schema_version=REGISTRATION_SCHEMA_VERSION,
        agent_id=expected_agent_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        adapter_contract_hash=live["adapter_contract_hash"],
        acp_agent_name=agent_name,
        forbidden_capabilities=forbidden,
        argv_tokens=argv_tokens,
        version_probe=probe,
        permission_binding_id=binding_id,
        model_selector_id=model_selector,
        effort_selector_id=effort_selector,
        registered_models=models,
        allowed_efforts=efforts,
        default_model=default_model,
        default_effort=default_effort,
        credential_slots=slots,
        required_credential_refs=required_refs,
        provenance=provenance,
        registration_hash=registration_hash(body),
    )
