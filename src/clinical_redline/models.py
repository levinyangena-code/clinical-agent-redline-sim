"""Pydantic data models shared across the simulation.

All records are synthetic. ``is_poisoned`` and similar ground-truth labels
exist for evaluation only; defensive logic must never read them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

IPI_MARKER = "REDTEAM_IPI_MARKER"
"""Benign canary string embedded in untrusted content to detect influence."""


class PatientRecord(BaseModel):
    """A synthetic EHR record. Contains no real person's data.

    Attributes:
        patient_id: Synthetic identifier such as ``P001``.
        name: Fictional name.
        dob: Fictional date of birth (ISO format).
        mrn: Fictional medical record number.
        ssn_like: Deliberately invalid SSN-shaped string (900-series).
        diagnosis: Fictional diagnosis label.
        medications: Fictional medication names.
        allergies: Fictional allergy entries.
        notes: Free-text notes. Treated as UNTRUSTED input by the agent.
        synthetic: Always True; guards against accidental real data.
    """

    model_config = ConfigDict(frozen=True)

    patient_id: str
    name: str
    dob: str
    mrn: str
    ssn_like: str
    diagnosis: str
    medications: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    notes: str = ""
    synthetic: bool = True


class RAGDocument(BaseModel):
    """A synthetic multimodal knowledge-base entry (text plus image caption).

    The "image" is represented by a caption string; no pixels are involved.

    Attributes:
        doc_id: Unique document id.
        title: Short title.
        text: Body text.
        image_caption: Caption describing the paired (synthetic) figure.
        topic: Coarse topic label.
        is_poisoned: Ground-truth label for evaluation ONLY.
    """

    model_config = ConfigDict(frozen=True)

    doc_id: str
    title: str
    text: str
    image_caption: str
    topic: str = "general"
    is_poisoned: bool = False


class AgentAction(BaseModel):
    """One action chosen by the agent policy.

    Attributes:
        tool: Tool name (``ehr_read``, ``email_send``, ``rag_search``, or
            ``finish``). Kept as ``str`` so non-allowlisted names can be
            represented and rejected.
        args: Tool arguments.
        rationale: Short free-text justification (ReAct "thought").
    """

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class GuardrailDecision(BaseModel):
    """Structured outcome of a guardrail check.

    Attributes:
        allowed: True if the action may proceed.
        rule_ids: Identifiers of the rules that fired.
        reasons: Human-readable explanations, parallel to ``rule_ids``.
    """

    allowed: bool
    rule_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class AgentStep(BaseModel):
    """One executed (or blocked) step in an agent run.

    Attributes:
        action: The action the policy requested.
        observation: Tool output, or a ``BLOCKED``/``ERROR`` message.
        blocked: True if a guardrail or the tool allowlist stopped it.
        error: True if the tool raised an expected error.
        decision: Guardrail decision, when one was made.
    """

    action: AgentAction
    observation: str
    blocked: bool = False
    error: bool = False
    decision: GuardrailDecision | None = None


class AgentResult(BaseModel):
    """Result of one ``run_agent`` call.

    Attributes:
        task: The task string given to the agent.
        steps: Executed steps in order.
        final_answer: The agent's final answer ("" if it never finished).
        terminated: ``finished`` or ``max_steps``.
    """

    task: str
    steps: list[AgentStep] = Field(default_factory=list)
    final_answer: str = ""
    terminated: Literal["finished", "max_steps"] = "finished"

    def blocked_steps(self) -> list[AgentStep]:
        """Return the steps that were blocked.

        Returns:
            Blocked steps, in execution order.
        """
        return [s for s in self.steps if s.blocked]


class AttackResult(BaseModel):
    """Aggregated outcome of a scenario run (several trials).

    "Exfiltration" here means a simulated message addressed outside the
    allowed domains. Even a "successful" one only lands in a local file.

    Attributes:
        scenario: ``ipi`` or ``rag``.
        seed: Seed used for the run.
        trials: Number of trials executed.
        marker_in_output: Trials whose final answer echoed the marker.
        exfil_attempts: Simulated external-email attempts.
        exfil_blocked: Attempts stopped by guardrails.
        exfil_succeeded: Attempts that reached the local outbox.
        guardrail_blocks: All blocked steps of any kind.
        unsafe_recommendations: RAG trials where the undefended answer
            contradicted the reference guidance.
        defended_unsafe_recommendations: Same, with the conflict detector on.
        poison_successes: RAG trials where a poisoned doc ranked first.
        conflicts_flagged: RAG trials where the detector flagged a poisoned doc.
        mean_quality_clean: Mean answer quality on the clean corpus.
        mean_quality_poisoned: Mean quality, poisoned corpus, no defense.
        mean_quality_defended: Mean quality, poisoned corpus, detector on.
        notes: Free-form notes.
    """

    scenario: Literal["ipi", "rag"]
    seed: int
    trials: int
    marker_in_output: int = 0
    exfil_attempts: int = 0
    exfil_blocked: int = 0
    exfil_succeeded: int = 0
    guardrail_blocks: int = 0
    unsafe_recommendations: int = 0
    defended_unsafe_recommendations: int = 0
    poison_successes: int = 0
    conflicts_flagged: int = 0
    mean_quality_clean: float = 0.0
    mean_quality_poisoned: float = 0.0
    mean_quality_defended: float = 0.0
    notes: list[str] = Field(default_factory=list)
