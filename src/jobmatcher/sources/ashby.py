"""Ashby posting API (public, no key)."""

from __future__ import annotations

from typing import Any

from jobmatcher.models import Posting, parse_time
from jobmatcher.sources.common import build_posting, split_locations, workplace_from
from jobmatcher.sources.http import Http

NAME = "ashby"


def board_url(token: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{token}"


def _salary(job: dict[str, Any]) -> tuple[tuple[float | None, float | None], str | None]:
    comp = job.get("compensation") or {}
    for c in comp.get("summaryComponents") or []:
        if (c.get("compensationType") or "").lower() != "salary":
            continue
        interval = (c.get("interval") or "").upper()
        factor = 2080 if "HOUR" in interval else 12 if "MONTH" in interval else 1
        lo, hi = c.get("minValue"), c.get("maxValue")
        return (
            (float(lo) * factor if lo is not None else None, float(hi) * factor if hi is not None else None),
            c.get("currencyCode"),
        )
    return (None, None), None


def parse(data: dict[str, Any], company: str) -> list[Posting]:
    out = []
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        url = job.get("jobUrl") or job.get("applyUrl")
        if not url:
            continue
        locs = split_locations(
            job.get("location"), *[s.get("location") for s in job.get("secondaryLocations") or []]
        )
        explicit = "remote" if job.get("isRemote") and not job.get("workplaceType") else job.get("workplaceType")
        salary, currency = _salary(job)
        out.append(
            build_posting(
                source=NAME,
                company=company,
                title=job.get("title", ""),
                url=url,
                description_html=job.get("descriptionHtml") or job.get("descriptionPlain") or "",
                locations=locs,
                workplace=workplace_from(locs, explicit),
                posted_at=parse_time(job.get("publishedAt")),
                salary=salary,
                currency=currency,
                raw=job,
            )
        )
    return out


def fetch(token: str, company: str, http: Http) -> list[Posting]:
    return parse(
        http.get_json(board_url(token), params={"includeCompensation": "true"}), company
    )
