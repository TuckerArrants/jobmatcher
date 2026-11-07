"""Find which public job-board system a company uses, and its board token."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

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
        out.append("".join(w[0] for w in words[:-1]) + words[-1])  # "d e shaw" -> "deshaw"
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
            # An unknown Lever token can come back as 200 with an empty list.
            if name == "lever" and not data:
                continue
            hits.append(BoardHit(name, token, _count(name, data)))
    return hits


def best(hits: list[BoardHit]) -> BoardHit | None:
    """The most specific match: hits for the earliest candidate token (the full company name
    comes first), then the one with most open jobs. A shorter token such as the first word can
    belong to a different company, so it is used only when the full name finds nothing."""
    if not hits:
        return None
    first_token = hits[0].token
    return max((h for h in hits if h.token == first_token), key=lambda h: h.jobs)


def add_to_companies(path: Path, entries: list[dict]) -> int:
    """Append entries to companies.yaml, skipping names already there. Returns how many added.
    Rewrites the file, so comments in it are not kept."""
    data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
    companies = data.get("companies") or []
    have = {str(c.get("name", "")).lower() for c in companies}
    added = [e for e in entries if e["name"].lower() not in have]
    data["companies"] = companies + added
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return len(added)
