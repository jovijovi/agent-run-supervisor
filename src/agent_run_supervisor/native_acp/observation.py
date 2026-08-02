"""Observed evidence — layer 4 of the four-way boundary. Recorded, never a gate.

What the agent turned out to be is evidence. It is not identity, and it is not
authority. A self-report is not an identity in either direction: a substituted
agent can report any name it likes, and an operator-declared expected name would
refuse Runs for cosmetic vendor renames. Observations therefore never flow
backward into a profile, a registry entry, or a Session record.

The **complete** set of observation-based refusals is five, and they are all
checks against a declared contract *inside one Run* rather than continuity
comparisons between Runs:

1. the ACP protocol major does not match the profile's frozen major;
2. a required capability is absent;
3. a forbidden capability is present;
4. the configuration readback is inexact or coerced (owned by
   :mod:`~agent_run_supervisor.native_acp.config_fidelity`);
5. on a compatibility profile, the required permission mode is not proven by
   readback (owned by the same fidelity sequence).

Everything else — the agent's self-reported name and version, capability drift
between two Runs of one Session, the image the kernel mapped, a probe result — is
recorded, may be emitted as a policy warning, and never refuses. That is what
makes an AGENT or adapter upgrade behind an unchanged registered command cost no
ARS action at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# Declared once, in order, so "which refusals are observation-based?" has a
# single answer that a test can pin and a reviewer can read.
OBSERVATION_REFUSALS = (
    "PROTOCOL_MISMATCH",
    "CAPABILITY_MISSING",
    "CAPABILITY_FORBIDDEN",
    "CONFIG_FIDELITY",
    "PERMISSION_MODE_UNPROVEN",
)

# Policy-warning codes. A warning is a recorded fact with an explicit
# ``authoritative: false``, never a soft refusal with a different name.
AGENT_SELF_REPORT_CHANGED = "AGENT_SELF_REPORT_CHANGED"
ADVERTISED_CAPABILITIES_CHANGED = "ADVERTISED_CAPABILITIES_CHANGED"

POLICY_WARNING = "policy_warning"

# The closed categorical vocabulary of the caller-facing policy-warning family
# (result/event schema §9.5). ``subject`` names *which* non-authoritative
# observation drifted and ``comparison`` names what it was compared against —
# always a record, never a gate. Neither ever carries what the observation
# actually said, so a warning discloses no name, version, capability, digest, or
# length. ``code`` is the stable machine-readable pairing of the two; it is
# documented alongside them and replaces neither.
SUBJECT_AGENT_SELF_REPORT = "agent_self_report"
SUBJECT_ADVERTISED_CAPABILITIES = "advertised_capabilities"
COMPARISON_PREVIOUS_RUN_OF_SESSION = "previous_run_of_session"

WARNING_SUBJECTS: frozenset[str] = frozenset(
    {SUBJECT_AGENT_SELF_REPORT, SUBJECT_ADVERTISED_CAPABILITIES}
)
WARNING_COMPARISONS: frozenset[str] = frozenset({COMPARISON_PREVIOUS_RUN_OF_SESSION})
WARNING_CODES: frozenset[str] = frozenset(
    {AGENT_SELF_REPORT_CHANGED, ADVERTISED_CAPABILITIES_CHANGED}
)
# Every string a policy warning may contain. A value outside this set means the
# emitter interpolated something observed, which is the one thing it may not do.
WARNING_VOCABULARY: frozenset[str] = (
    WARNING_SUBJECTS | WARNING_COMPARISONS | WARNING_CODES | {POLICY_WARNING}
)


@dataclass(frozen=True)
class InitializeObservation:
    """Exactly what one ``initialize`` exchange reported."""

    agent_info: Mapping[str, Any] | None = None
    protocol_version: int | None = None
    capabilities: Mapping[str, Any] | None = None
    load_session_advertised: bool | None = None

    def self_report(self) -> tuple[str, str]:
        info = self.agent_info or {}
        return str(info.get("name", "")), str(info.get("version", ""))

    def advertised(self) -> tuple[str, ...]:
        capabilities = self.capabilities or {}
        return tuple(sorted(name for name, on in capabilities.items() if on))

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_info": dict(self.agent_info) if self.agent_info else self.agent_info,
            "protocol_version": self.protocol_version,
            "capabilities": (
                dict(self.capabilities) if self.capabilities else self.capabilities
            ),
            "load_session_advertised": self.load_session_advertised,
        }


@dataclass(frozen=True)
class ObservationVerdict:
    """One Run's contract verdict plus its non-authoritative evidence."""

    observed: InitializeObservation
    refusal: str | None = None
    detail: str | None = None
    warnings: tuple[dict[str, Any], ...] = field(default=())

    def to_evidence(self) -> dict[str, Any]:
        """The durable projection.

        There is deliberately no ``expected`` block: with agent identity gone
        from source there is nothing to compare a self-report *to*, and a field
        that recorded one would be the first half of a gate.
        """
        return {
            "schema_version": 2,
            "authoritative": False,
            "observed": self.observed.to_dict(),
            "refusal": self.refusal,
            "warnings": [dict(warning) for warning in self.warnings],
        }


def _warning(code: str, *, subject: str, comparison: str) -> dict[str, Any]:
    """One policy-warning record, in the exact caller-facing shape of §9.5.

    ``refused: false`` is carried explicitly rather than left implied. The whole
    point of the family is that the Run continued and the Session stayed
    reusable, and a caller should be able to read that off the record instead of
    inferring it from the absence of a refusal somewhere else.
    """
    return {
        "type": POLICY_WARNING,
        "code": code,
        "subject": subject,
        "comparison": comparison,
        "authoritative": False,
        "refused": False,
    }


def judge_initialize(
    instance: Any,
    observed: InitializeObservation,
    *,
    previous: InitializeObservation | None = None,
) -> ObservationVerdict:
    """Judge one ``initialize`` exchange against the declared contract.

    ``previous`` is the same Session's earlier observation, when one exists. It
    is used **only** to emit drift warnings: nothing about it can change the
    verdict, which is why the refusal decision below never reads it.
    """
    warnings: list[dict[str, Any]] = []
    if previous is not None:
        if previous.self_report() != observed.self_report():
            warnings.append(
                _warning(
                    AGENT_SELF_REPORT_CHANGED,
                    subject=SUBJECT_AGENT_SELF_REPORT,
                    comparison=COMPARISON_PREVIOUS_RUN_OF_SESSION,
                )
            )
        if previous.advertised() != observed.advertised():
            warnings.append(
                _warning(
                    ADVERTISED_CAPABILITIES_CHANGED,
                    subject=SUBJECT_ADVERTISED_CAPABILITIES,
                    comparison=COMPARISON_PREVIOUS_RUN_OF_SESSION,
                )
            )

    refusal, detail = _contract_verdict(instance, observed)
    return ObservationVerdict(
        observed=observed,
        refusal=refusal,
        detail=detail,
        warnings=tuple(warnings),
    )


def _contract_verdict(
    instance: Any, observed: InitializeObservation
) -> tuple[str | None, str | None]:
    """The three initialize-time contract checks, in a fixed order.

    Each names a declared contract term, so its message is a rule name and a
    capability name — never an observed value, and never a comparison against
    something the agent said about itself.
    """
    expected_major = str(instance.acp_protocol_version)
    if str(observed.protocol_version) != expected_major:
        return "PROTOCOL_MISMATCH", f"the profile speaks ACP major {expected_major}"

    advertised = observed.capabilities or {}
    for capability in instance.required_capabilities:
        present = bool(advertised.get(capability))
        if capability == "loadSession" and observed.load_session_advertised:
            present = True
        if not present:
            return "CAPABILITY_MISSING", f"required capability {capability} is absent"

    for capability in instance.forbidden_capabilities:
        if bool(advertised.get(capability)):
            return (
                "CAPABILITY_FORBIDDEN",
                f"forbidden capability {capability} is advertised",
            )
    return None, None


def observation_from_record(record: Any) -> InitializeObservation | None:
    """The previous ``initialize`` observation of a Session, or ``None``.

    Rebuilt from the three value-blind fields the record carries. The capability
    *names* are rebuilt into a truthy mapping so :meth:`advertised` answers
    identically on both sides of the comparison — the stored form is a sorted
    name list, and comparing it to a live mapping directly would report drift
    every time.

    A record with no observation yields ``None``, which is why a Session's first
    Run has nothing to warn about.
    """
    name = getattr(record, "native_last_agent_info_name", None)
    version = getattr(record, "native_last_agent_info_version", None)
    capabilities = getattr(record, "native_last_advertised_capabilities", None)
    if name is None and version is None and capabilities is None:
        return None
    return InitializeObservation(
        agent_info={"name": name or "", "version": version or ""},
        capabilities={item: True for item in (capabilities or ())},
    )


def observed_runtime_evidence(
    *,
    declared_command: str,
    argv: tuple[str, ...],
    path_lookup_hit: str | None = None,
    mapped_image: str | None = None,
) -> dict[str, Any]:
    """Resolution facts, recorded with ``authoritative: false``.

    Where the kernel found the image is genuinely useful to a human triaging a
    failure and genuinely useless as a gate: ARS performs no pre-flight
    resolution and no ownership, mode, ancestor, symlink, or digest check on the
    command or its ancestors, so nothing here has anything to be compared to.
    """
    return {
        "authoritative": False,
        "declared_command": declared_command,
        "argv": list(argv),
        "path_lookup_hit": path_lookup_hit,
        "mapped_image": mapped_image,
    }
