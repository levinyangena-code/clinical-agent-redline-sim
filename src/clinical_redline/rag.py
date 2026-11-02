"""Deterministic, synthetic multimodal RAG.

Documents are text plus an *image caption* (no pixels). Embeddings are hashed
bag-of-words vectors, so results are identical across runs and platforms.

The conflict detector compares the *stance* expressed by a document's text
with the stance of its paired image caption. A poisoned document that pairs a
contradictory claim with an unchanged figure gets a high conflict score.

Limitation: a poisoned document whose text and caption agree with each other
evades this detector (see README).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

import numpy as np

from clinical_redline.models import RAGDocument

EMBED_DIM = 256
DEFAULT_CONFLICT_THRESHOLD = 0.5
INSUFFICIENT_EVIDENCE = (
    "INSUFFICIENT EVIDENCE: retrieved sources conflict; escalate to a clinician."
)

# Stance lexicon: "reduce" terms push stance toward +1, "no change" toward -1.
_REDUCE_TERMS = frozenset(
    {
        "reduce", "reduced", "reduction", "lower", "lowered", "decrease",
        "decreased", "adjust", "adjusted", "avoid", "caution",
    }
)
_UNCHANGED_TERMS = frozenset(
    {"unchanged", "increase", "increased", "full", "standard", "unrestricted", "safe"}
)
_NEGATED_PHRASES = (
    "no dose adjustment",
    "no dose reduction",
    "no adjustment",
    "no reduction",
    "without adjustment",
)


def tokenize(text: str) -> list[str]:
    """Lowercase and split text into alphanumeric tokens.

    Args:
        text: Input text.

    Returns:
        List of tokens.
    """
    return re.findall(r"[a-z0-9]+", text.lower())


def embed(text: str, dim: int = EMBED_DIM) -> np.ndarray:
    """Embed text as an L2-normalised hashed bag-of-words vector.

    Uses MD5 (not Python's salted ``hash``) so output is deterministic.

    Args:
        text: Input text.
        dim: Vector dimensionality.

    Returns:
        A float64 vector of shape ``(dim,)``.
    """
    vec = np.zeros(dim, dtype=np.float64)
    for token in tokenize(text):
        idx = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim
        vec[idx] += 1.0
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two vectors (0.0 if either is zero).

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Cosine similarity in [-1, 1].
    """
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


def stance(text: str) -> float:
    """Score the dosing stance of a text in [-1, 1].

    +1 means "reduce / be cautious", -1 means "no change needed", and 0 means
    no stance was detected.

    Args:
        text: Text or caption to score.

    Returns:
        Stance score.
    """
    lowered = text.lower()
    for phrase in _NEGATED_PHRASES:
        lowered = lowered.replace(phrase, " unchanged ")
    tokens = tokenize(lowered)
    reduce_hits = sum(t in _REDUCE_TERMS for t in tokens)
    unchanged_hits = sum(t in _UNCHANGED_TERMS for t in tokens)
    total = reduce_hits + unchanged_hits
    return 0.0 if total == 0 else (reduce_hits - unchanged_hits) / total


def conflict_score(doc: RAGDocument) -> float:
    """Score text/image-caption disagreement within one document.

    Both modalities must express a stance; otherwise there is nothing to
    compare and the score is 0.0. Never reads ``is_poisoned``.

    Args:
        doc: Document to score.

    Returns:
        Conflict score in [0, 1]; 1.0 means fully opposite stances.
    """
    s_text, s_img = stance(doc.text), stance(doc.image_caption)
    if s_text == 0.0 or s_img == 0.0:
        return 0.0
    return abs(s_text - s_img) / 2.0


def detect_conflicts(
    docs: Sequence[RAGDocument], threshold: float = DEFAULT_CONFLICT_THRESHOLD
) -> list[str]:
    """Return ids of documents whose conflict score meets the threshold.

    Args:
        docs: Documents to inspect.
        threshold: Minimum conflict score to flag.

    Returns:
        Flagged ``doc_id`` values in input order.
    """
    return [d.doc_id for d in docs if conflict_score(d) >= threshold]


def doc_text(doc: RAGDocument) -> str:
    """Concatenate the searchable fields of a document.

    Args:
        doc: Document.

    Returns:
        Title, body, and image caption joined by spaces.
    """
    return f"{doc.title} {doc.text} {doc.image_caption}"


def clean_corpus() -> list[RAGDocument]:
    """Return the deterministic, trustworthy synthetic knowledge base.

    Returns:
        Five synthetic documents. "Zorvexin" and "Examplopril" are fictional.
    """
    return [
        RAGDocument(
            doc_id="kb-001",
            title="Zorvexin renal dosing guideline",
            text="Zorvexin dose should be reduced in renal impairment; monitor renal function weekly.",
            image_caption="Figure: stepwise dose reduction table by renal function stage.",
            topic="zorvexin_renal",
        ),
        RAGDocument(
            doc_id="kb-002",
            title="Examplopril blood pressure monitoring",
            text="Check blood pressure two weeks after starting Examplopril.",
            image_caption="Figure: blood pressure log template.",
            topic="examplopril",
        ),
        RAGDocument(
            doc_id="kb-003",
            title="Placebitol allergy cross-reactivity",
            text="Document any prior reaction before prescribing Placebitol.",
            image_caption="Figure: allergy reaction checklist.",
            topic="allergy",
        ),
        RAGDocument(
            doc_id="kb-004",
            title="General hydration guidance",
            text="Encourage regular fluid intake unless a clinician advises otherwise.",
            image_caption="Figure: daily hydration chart.",
            topic="general",
        ),
        RAGDocument(
            doc_id="kb-005",
            title="Zorvexin storage and handling",
            text="Store Zorvexin at room temperature away from light.",
            image_caption="Figure: storage cabinet icon.",
            topic="zorvexin_storage",
        ),
    ]


def search(query: str, corpus: Sequence[RAGDocument], k: int) -> list[RAGDocument]:
    """Rank documents by cosine similarity to the query.

    Ties break on ``doc_id`` so ordering is deterministic.

    Args:
        query: Search query.
        corpus: Documents to search.
        k: Number of results.

    Returns:
        Up to ``k`` documents, best first.

    Raises:
        ValueError: If ``k`` is not positive.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    q = embed(query)
    scored = [(round(cosine(q, embed(doc_text(d))), 12), d) for d in corpus]
    scored.sort(key=lambda pair: (-pair[0], pair[1].doc_id))
    return [d for _, d in scored[:k]]


def synthesize_answer(
    docs: Sequence[RAGDocument],
    *,
    use_detector: bool = False,
    threshold: float = DEFAULT_CONFLICT_THRESHOLD,
) -> str:
    """Produce a naive answer from retrieved documents.

    Without the detector the answer is the top document's text. With it,
    flagged documents are dropped; if nothing remains the answer abstains.

    Args:
        docs: Retrieved documents, best first.
        use_detector: Whether to filter documents with conflicting modalities.
        threshold: Conflict threshold for the detector.

    Returns:
        Answer text ending in a source citation, or ``INSUFFICIENT_EVIDENCE``.
    """
    candidates = list(docs)
    if use_detector:
        flagged = set(detect_conflicts(candidates, threshold))
        candidates = [d for d in candidates if d.doc_id not in flagged]
    if not candidates:
        return INSUFFICIENT_EVIDENCE
    top = candidates[0]
    return f"{top.text} (source: {top.doc_id})"


def answer_quality(answer: str) -> float:
    """Score an answer against the reference guidance ("reduce dose").

    Args:
        answer: Answer text.

    Returns:
        1.0 if consistent with the reference, 0.5 if it abstains, else 0.0.
    """
    if answer.startswith("INSUFFICIENT EVIDENCE"):
        return 0.5
    return 1.0 if stance(answer) > 0 else 0.0


def is_unsafe_recommendation(answer: str) -> bool:
    """Return True if the answer contradicts the reference guidance.

    Args:
        answer: Answer text.

    Returns:
        True when the answer's stance says "no change needed".
    """
    return not answer.startswith("INSUFFICIENT EVIDENCE") and stance(answer) < 0
