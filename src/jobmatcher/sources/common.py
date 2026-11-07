"""Helpers shared by the ATS adapters."""

from __future__ import annotations

import re

from jobmatcher.clean import clean_description, html_to_text, parse_salary
from jobmatcher.models import Posting

_SPLIT = re.compile(r"\s*(?:\||;|\bor\b|/(?=\s*[A-Z]))\s*")


def split_locations(*values: str | None) -> list[str]:
    """"Austin, TX | Remote - US" -> ["Austin, TX", "Remote - US"]; deduplicated, order kept."""
    out: list[str] = []
    for value in values:
        for part in _SPLIT.split(value or ""):
            part = part.strip(" ,-")
            if part and part.lower() not in {p.lower() for p in out}:
                out.append(part)
    return out


def workplace_from(locations: list[str], explicit: str | None = None) -> str | None:
    """Normalise an ATS workplace field, falling back to the location strings."""
    e = (explicit or "").lower().replace("-", "").replace("_", "").replace(" ", "")
    if e in ("remote", "fullyremote"):
        return "remote"
    if e == "hybrid":
        return "hybrid"
    if e in ("onsite", "inoffice", "office"):
        return "onsite"
    joined = " ".join(locations).lower()
    if re.search(r"\bremote\b", joined) and not re.search(r"\b(not|no)\s+remote\b", joined):
        return "remote"
    if "hybrid" in joined:
        return "hybrid"
    return "onsite" if locations else None


def build_posting(
    *,
    source: str,
    company: str,
    title: str,
    url: str,
    description_html: str,
    locations: list[str],
    workplace: str | None,
    posted_at,
    salary: tuple[float | None, float | None] = (None, None),
    currency: str | None = None,
    raw: dict,
) -> Posting:
    lo, hi = salary
    if lo is None and hi is None:
        lo, hi = parse_salary(html_to_text(description_html))
        currency = "USD" if lo is not None else None
    return Posting(
        source=source,
        company=company,
        title=(title or "").strip(),
        url=url,
        text=clean_description(description_html),
        locations=locations,
        workplace=workplace,
        posted_at=posted_at,
        salary_min=lo,
        salary_max=hi,
        salary_currency=currency,
        raw=raw,
    )
