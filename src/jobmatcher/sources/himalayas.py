"""Himalayas remote-jobs API (free; a key is optional and only raises rate limits).

Terms (himalayas.app/docs/remote-jobs-api): visible credit to Himalayas with a link; apply
through the API's ``applicationLink``; no redistribution to other job boards or aggregators.
So the report credits Himalayas, posting links are the application links, and the data stays
in the private user/ folder. Its feed refreshes daily, so one search per query per run is plenty.
"""

from __future__ import annotations

import os
from typing import Any

from jobmatcher.models import Posting, parse_time, posting_id
from jobmatcher.sources.common import build_posting
from jobmatcher.sources.http import Http

NAME = "himalayas"
ATTRIBUTION = ("Remote jobs from Himalayas", "https://himalayas.app")
SEARCH_URL = "https://himalayas.app/jobs/api/search"
_PERIOD = {"hour": 2080, "hourly": 2080, "month": 12, "monthly": 12, "week": 52, "weekly": 52}


def _salary(job: dict[str, Any]) -> tuple[tuple[float | None, float | None], str | None]:
    if (job.get("currency") or "").upper() != "USD":
        return (None, None), None
    factor = _PERIOD.get(str(job.get("salaryPeriod") or "").lower(), 1)
    lo, hi = job.get("minSalary"), job.get("maxSalary")
    if lo is None and hi is None:
        return (None, None), None
    return (float(lo) * factor if lo is not None else None,
            float(hi) * factor if hi is not None else None), "USD"


def parse(data: dict[str, Any]) -> list[Posting]:
    out = []
    for job in data.get("jobs", []) or []:
        url = job.get("applicationLink")
        if not url:
            continue
        countries = [c for c in job.get("locationRestrictions") or [] if c]
        locs = [f"Remote - {c}" for c in countries] or ["Remote - Worldwide"]
        salary, currency = _salary(job)
        p = build_posting(
            source=NAME,
            company=job.get("companyName") or "Unknown",
            title=job.get("title", ""),
            url=url,
            description_html=job.get("description") or job.get("excerpt") or "",
            locations=locs,
            workplace="remote",
            posted_at=parse_time(job.get("pubDate")),
            salary=salary,
            currency=currency,
            raw=job,
        )
        if job.get("guid"):
            p.id = posting_id(str(job["guid"]))
        out.append(p)
    return out


def search(cfg: dict, http: Http) -> list[Posting]:
    headers = {}
    key = os.environ.get("HIMALAYAS_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    found: dict[str, Posting] = {}
    for q in cfg.get("queries") or []:
        for page in range(1, int(cfg.get("pages", 1)) + 1):
            params = {"q": q, "page": page}
            if cfg.get("country"):
                params["country"] = cfg["country"]
            data = http.get_json(SEARCH_URL, params=params, headers=headers or None)
            jobs = parse(data)
            for p in jobs:
                found.setdefault(p.id, p)
            if not jobs:
                break
    return list(found.values())
