"""clinical_redline: a defensive, sandboxed red-team simulation.

Everything in this package operates on synthetic data only. No real PHI is
used, no real email is sent, and no network calls are made. "Attack" content
consists of benign marker strings such as ``REDTEAM_IPI_MARKER``.
"""

from clinical_redline.models import (
    IPI_MARKER,
    AgentAction,
    AgentResult,
    AttackResult,
    GuardrailDecision,
    PatientRecord,
    RAGDocument,
)

__version__ = "0.1.0"

__all__ = [
    "IPI_MARKER",
    "AgentAction",
    "AgentResult",
    "AttackResult",
    "GuardrailDecision",
    "PatientRecord",
    "RAGDocument",
    "__version__",
]
