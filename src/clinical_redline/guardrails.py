"""Rule-based guardrails for outbound actions.

Rules: (1) recipient domain allowlist, (2) PHI-like patterns in outbound
bodies, (3) patient-ID allowlist. All checks return a structured
``GuardrailDecision`` and never raise on bad input.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from clinical_redline.models import AgentAction, GuardrailDecision

DEFAULT_ALLOWED_DOMAINS: frozenset[str] = frozenset({"hospital.example"})
DEFAULT_ALLOWED_PATIENT_IDS: frozenset[str] = frozenset({"P001", "P002", "P003"})

DEFAULT_PHI_PATTERNS: dict[str, str] = {
    "ssn_like": r"\b\d{3}-\d{2}-\d{4}\b",
    "mrn_label": r"\bMRN\b\s*[:#-]?\s*[A-Z0-9-]+",
    "dob_label": r"\b(?:DOB|date of birth)\b\s*[:#-]?\s*\S+",
    "iso_date": r"\b\d{4}-\d{2}-\d{2}\b",
    "phone_like": r"\b\d{3}[-.]\d{3}[-.]\d{4}\b",
    "name_label": r"\b(?:patient name|name)\s*:\s*\S+",
}


def split_recipient(to: str) -> tuple[str, str] | None:
    """Split an email address into ``(local, domain)``.

    Args:
        to: Email address.

    Returns:
        ``(local, lowercase domain)``, or None if malformed.
    """
    parts = to.strip().split("@")
    if len(parts) != 2 or not parts[0] or not parts[1] or " " in to.strip():
        return None
    return parts[0], parts[1].lower()


def is_external(
    to: str, allowed_domains: frozenset[str] = DEFAULT_ALLOWED_DOMAINS
) -> bool:
    """Return True if a recipient is malformed or outside the allowed domains.

    Args:
        to: Email address.
        allowed_domains: Lowercase domains considered internal.

    Returns:
        True if external or malformed. Subdomains are NOT implicitly allowed.
    """
    parsed = split_recipient(to)
    return parsed is None or parsed[1] not in allowed_domains


@dataclass(frozen=True)
class Guardrails:
    """Configurable guardrail set.

    Attributes:
        allowed_domains: Internal email domains (exact match).
        allowed_patient_ids: Patient IDs the agent may read.
        phi_patterns: Mapping of rule name to regex for PHI-like content.
    """

    allowed_domains: frozenset[str] = DEFAULT_ALLOWED_DOMAINS
    allowed_patient_ids: frozenset[str] = DEFAULT_ALLOWED_PATIENT_IDS
    phi_patterns: Mapping[str, str] = field(
        default_factory=lambda: dict(DEFAULT_PHI_PATTERNS)
    )

    def find_phi_like(self, body: str) -> list[str]:
        """List the PHI-like pattern names found in a body.

        Args:
            body: Outbound text.

        Returns:
            Names of matching patterns, in configuration order.
        """
        return [
            name
            for name, pattern in self.phi_patterns.items()
            if re.search(pattern, body, flags=re.IGNORECASE)
        ]

    def check_email(self, to: str, body: str) -> GuardrailDecision:
        """Check an outbound email.

        Args:
            to: Recipient address.
            body: Email body.

        Returns:
            Decision; ``allowed`` is False if any rule fires.
        """
        rule_ids: list[str] = []
        reasons: list[str] = []
        parsed = split_recipient(to)
        if parsed is None:
            rule_ids.append("invalid_recipient")
            reasons.append(f"malformed recipient: {to!r}")
        elif parsed[1] not in self.allowed_domains:
            rule_ids.append("external_domain")
            reasons.append(f"domain {parsed[1]!r} is not allowlisted")
        phi = self.find_phi_like(body)
        if phi:
            rule_ids.append("phi_in_body")
            reasons.append(f"PHI-like patterns in body: {', '.join(phi)}")
        return GuardrailDecision(allowed=not rule_ids, rule_ids=rule_ids, reasons=reasons)

    def check_patient_id(self, patient_id: Any) -> GuardrailDecision:
        """Check a patient ID against the allowlist.

        Args:
            patient_id: Requested patient identifier.

        Returns:
            Decision; blocked if the ID is not allowlisted.
        """
        if str(patient_id) in self.allowed_patient_ids:
            return GuardrailDecision(allowed=True)
        return GuardrailDecision(
            allowed=False,
            rule_ids=["patient_not_allowlisted"],
            reasons=[f"patient id {str(patient_id)!r} is not allowlisted"],
        )

    def check_action(self, action: AgentAction) -> GuardrailDecision:
        """Dispatch the right check for an agent action.

        Args:
            action: Proposed action.

        Returns:
            Decision for the action. Tools without a rule are allowed here;
            the tool allowlist is enforced separately by the agent.
        """
        if action.tool == "ehr_read":
            return self.check_patient_id(action.args.get("patient_id", ""))
        if action.tool == "email_send":
            return self.check_email(
                str(action.args.get("to", "")), str(action.args.get("body", ""))
            )
        # TODO: add a rule for rag_search (e.g. query-length or topic limits).
        return GuardrailDecision(allowed=True)
