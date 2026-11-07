"""Greenhouse job board API (public, no key)."""

from __future__ import annotations

from typing import Any

from jobmatcher.models import Posting, parse_time
from jobmatcher.sources.common import build_posting, split_locations, workplace_from
from jobmatcher.sources.http import Http

NAME = "greenhouse"


def board_url(token: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def parse(data: dict[str, Any], company: str) -> list[Posting]:
    out = []
    for job in data.get("jobs", []):
        url = job.get("absolute_url")
        if not url:
            continue
        locs = split_locations((job.get("location") or {}).get("name"))
        if not locs:
            locs = split_locations(*[o.get("name") for o in job.get("offices") or []])
        out.append(
            build_posting(
                source=NAME,
                company=company,
                title=job.get("title", ""),
                url=url,
                description_html=job.get("content") or "",
                locations=locs,
                workplace=workplace_from(locs),
                posted_at=parse_time(job.get("first_published") or job.get("updated_at")),
                raw=job,
            )
        )
    return out


def fetch(token: str, company: str, http: Http) -> list[Posting]:
    return parse(http.get_json(board_url(token), params={"content": "true"}), company)
