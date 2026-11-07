"""USAJobs search API (free key).

Required headers (developer.usajobs.gov): Host: data.usajobs.gov, User-Agent: <your email>,
Authorization-Key: <your key>. The email and key come from the environment only.

One request per keyword, nationwide. The prefilter then applies your radius using the
coordinates USAJobs gives for every location, which is more exact than its city-name search.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from jobmatcher.models import Posting, parse_time
from jobmatcher.sources.common import build_posting, split_locations, workplace_from
from jobmatcher.sources.http import Http

NAME = "usajobs"
SEARCH_URL = "https://data.usajobs.gov/api/Search"


def keys_present() -> bool:
    return bool(os.environ.get("USAJOBS_API_KEY") and os.environ.get("USAJOBS_EMAIL"))


def _annual(rem: list[dict]) -> tuple[float | None, float | None]:
    for r in rem or []:
        code = (r.get("RateIntervalCode") or "").upper()
        try:
            lo, hi = float(r.get("MinimumRange")), float(r.get("MaximumRange"))
        except (TypeError, ValueError):
            continue
        if code in ("PA", "PER YEAR"):
            return lo, hi
        if code in ("PH", "PER HOUR"):
            return lo * 2080, hi * 2080
    return None, None


def _text(d: dict[str, Any]) -> str:
    details = (d.get("UserArea") or {}).get("Details") or {}
    duties = details.get("MajorDuties") or []
    if isinstance(duties, str):
        duties = [duties]
    parts = [
        f"<h3>Summary</h3><p>{details.get('JobSummary', '')}</p>",
        "<h3>Duties</h3><ul>" + "".join(f"<li>{x}</li>" for x in duties) + "</ul>",
        f"<h3>Qualifications</h3><p>{d.get('QualificationSummary', '')}</p>",
        f"<h3>Education</h3><p>{details.get('Education', '')}</p>",
        f"<h3>Requirements</h3><p>{details.get('Requirements', '')}</p>",
    ]
    clearance = details.get("SecurityClearance")
    if clearance and str(clearance).lower() not in ("not required", "not applicable", "none"):
        parts.append(f"<p>Security clearance: {clearance}</p>")
    return "\n".join(parts)


def _is_public(d: dict[str, Any]) -> bool:
    details = (d.get("UserArea") or {}).get("Details") or {}
    paths = details.get("HiringPath")
    if not paths:
        return True  # unknown: keep
    return any(str(p).lower() == "public" for p in paths)


def parse(data: dict[str, Any], now: datetime | None = None, public_only: bool = True) -> list[Posting]:
    now = now or datetime.now(timezone.utc)
    items = ((data.get("SearchResult") or {}).get("SearchResultItems")) or []
    out = []
    for item in items:
        d = item.get("MatchedObjectDescriptor") or {}
        url = d.get("PositionURI")
        if not url:
            continue
        closes = parse_time(d.get("ApplicationCloseDate"))
        if closes and closes < now:
            continue
        if public_only and not _is_public(d):
            continue
        locs = split_locations(*[loc.get("LocationName") for loc in d.get("PositionLocation") or []])
        details = (d.get("UserArea") or {}).get("Details") or {}
        remote_flag = str(details.get("RemoteIndicator", "")).lower() == "true"
        out.append(
            build_posting(
                source=NAME,
                company=" / ".join(x for x in (d.get("OrganizationName"), d.get("DepartmentName")) if x)
                or "US federal government",
                title=d.get("PositionTitle", ""),
                url=url,
                description_html=_text(d),
                locations=locs,
                workplace="remote" if remote_flag else workplace_from(locs),
                posted_at=parse_time(d.get("PublicationStartDate")),
                salary=_annual(d.get("PositionRemuneration") or []),
                currency="USD",
                raw=d,
            )
        )
    return out


def search(cfg: dict, http: Http, now: datetime | None = None) -> list[Posting]:
    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": os.environ["USAJOBS_EMAIL"],
        "Authorization-Key": os.environ["USAJOBS_API_KEY"],
    }
    found: dict[str, Posting] = {}
    for kw in cfg.get("keywords") or []:
        params = {
            "Keyword": kw,
            "ResultsPerPage": min(int(cfg.get("results_per_page", 250)), 500),
            "DatePosted": max(0, min(int(cfg.get("days_posted", 30)), 60)),
        }
        data = http.get_json(SEARCH_URL, params=params, headers=headers)
        for p in parse(data, now=now, public_only=cfg.get("public_only", True)):
            found.setdefault(p.id, p)
    return list(found.values())
