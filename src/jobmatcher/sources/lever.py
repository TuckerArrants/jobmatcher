"""Lever postings API (public, no key)."""

from __future__ import annotations

from typing import Any

from jobmatcher.models import Posting, parse_time
from jobmatcher.sources.common import build_posting, split_locations, workplace_from
from jobmatcher.sources.http import Http

NAME = "lever"


def board_url(token: str) -> str:
    return f"https://api.lever.co/v0/postings/{token}"


def _salary(job: dict[str, Any]) -> tuple[tuple[float | None, float | None], str | None]:
    rng = job.get("salaryRange") or {}
    lo, hi = rng.get("min"), rng.get("max")
    if lo is None and hi is None:
        return (None, None), None
    interval = (rng.get("interval") or "").lower()
    factor = 2080 if "hour" in interval else 12 if "month" in interval else 1
    lo = float(lo) * factor if lo is not None else None
    hi = float(hi) * factor if hi is not None else None
    return (lo, hi), rng.get("currency")


def _description(job: dict[str, Any]) -> str:
    parts = [job.get("description") or ""]
    for section in job.get("lists") or []:
        parts.append(f"<h3>{section.get('text', '')}</h3><ul>{section.get('content', '')}</ul>")
    parts.append(job.get("additional") or "")
    return "\n".join(parts)


def parse(data: list[dict[str, Any]], company: str) -> list[Posting]:
    out = []
    for job in data or []:
        url = job.get("hostedUrl") or job.get("applyUrl")
        if not url:
            continue
        cats = job.get("categories") or {}
        locs = split_locations(*(cats.get("allLocations") or [cats.get("location")]))
        salary, currency = _salary(job)
        out.append(
            build_posting(
                source=NAME,
                company=company,
                title=job.get("text", ""),
                url=url,
                description_html=_description(job),
                locations=locs,
                workplace=workplace_from(locs, job.get("workplaceType")),
                posted_at=parse_time(job.get("createdAt")),
                salary=salary,
                currency=currency,
                raw=job,
            )
        )
    return out


def fetch(token: str, company: str, http: Http) -> list[Posting]:
    return parse(http.get_json(board_url(token), params={"mode": "json"}), company)
