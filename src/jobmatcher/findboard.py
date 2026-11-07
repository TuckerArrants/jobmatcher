"""Find which public job-board system a company uses, and its board token."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobmatcher.sources import ATS
from jobmatcher.sources.http import Http, NotFound, SourceError


@dataclass
class BoardHit:
    ats: str
    token: str
    jobs: int


def candidate_tokens(company: str) -> list[str]:
    """"Recursion Pharmaceuticals, Inc." -> recursionpharmaceuticals, recursion-pharmaceuticals,
    recursion, ..."""
    base = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|the)\b\.?", "", company.lower())
    words = re.findall(r"[a-z0-9]+", base)
    if not words:
        return []
    out = ["".join(words), "-".join(words), words[0]]
    if len(words) > 1:
        out.append("".join(w[0] for w in words) + words[-1])  # e.g. "d. e. shaw" style initials
    return list(dict.fromkeys(t for t in out if len(t) > 1))


def _count(ats: str, data) -> int:
    if ats == "lever":
        return len(data) if isinstance(data, list) else 0
    return len((data or {}).get("jobs", []))


def find_board(company: str, http: Http, tokens: list[str] | None = None) -> list[BoardHit]:
    hits = []
    for token in tokens or candidate_tokens(company):
        for name, module in ATS.items():
            try:
                data = http.get_json(module.board_url(token))
            except (NotFound, SourceError, ValueError):
                continue
            # Lever answers unknown tokens with 200 and an empty list; Greenhouse/Ashby with 404.
            if name == "lever" and not data:
                continue
            hits.append(BoardHit(name, token, _count(name, data)))
    return hits
