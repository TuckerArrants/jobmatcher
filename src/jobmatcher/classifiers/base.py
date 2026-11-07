"""The classifier interface. Everything downstream depends only on this, not on Jev."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from jobmatcher.models import Posting

LABELS = ("apply", "consider", "skip")


@dataclass
class Decision:
    label: str  # apply | consider | skip
    sort_score: float  # ranks postings within a label, higher first
    sub_scores: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    rule: str = ""  # which decision rule produced the label
    cost_usd: float = 0.0
    raw: Any = None  # the classifier's raw answer, kept for re-deciding without re-asking


class Classifier(Protocol):
    name: str  # recorded with every result, e.g. "jev:jobs_v1#1a2b3c4d@jev-1.13.0"

    def classify(self, posting: Posting, profile: str) -> Decision: ...
