"""Adzuna job search API (free developer key).

Notes from Adzuna's docs and terms:
* results carry only a description snippet, not the full text;
* salary_min / salary_max may be Adzuna's prediction (salary_is_predicted); predicted values are
  kept in ``raw`` but never used as the posting's salary, so they cannot trigger a salary filter;
* listings shown to people need the attribution "Jobs by Adzuna", and raw data must not be
  redistributed, which is why reports credit Adzuna and data stays in the private user/ folder.
"""

from __future__ import annotations

import os
from typing import Any

from jobmatcher.models import Posting, parse_time, posting_id
from jobmatcher.sources.common import build_posting, split_locations, workplace_from
from jobmatcher.sources.http import Http

NAME = "adzuna"
ATTRIBUTION = "Jobs by Adzuna"
KM_PER_MILE = 1.609344


def search_url(country: str, page: int) -> str:
    return f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


def keys_present() -> bool:
    return bool(os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"))


def _place(loc: dict[str, Any]) -> str:
    """"City, State" from Adzuna's area list (country, state, county, city), which the location
    parser can place on the map; display_name often omits the state ("Chattanooga, Hamilton County")."""
    area = [a for a in loc.get("area") or [] if a]
    if len(area) >= 3:
        return f"{area[-1]}, {area[1]}"
    if len(area) == 2:
        return area[1]
    if area == ["US"]:
        return "United States"
    return loc.get("display_name") or ""


def parse(data: dict[str, Any]) -> list[Posting]:
    out = []
    for job in data.get("results", []):
        url = job.get("redirect_url")
        ad_id = job.get("id")
        if not url or not ad_id:
            continue
        loc = job.get("location") or {}
        locs = split_locations(_place(loc))
        predicted = str(job.get("salary_is_predicted", "0")).lower() in ("1", "true")
        lo, hi = (None, None) if predicted else (job.get("salary_min"), job.get("salary_max"))
        salary = (float(lo) if lo is not None else None, float(hi) if hi is not None else None)
        # Adzuna has no remote field; the title or snippet says so when a job is remote.
        blob = f"{job.get('title', '')} {job.get('description', '')}"
        workplace = workplace_from(locs + [blob]) if "remote" in blob.lower() else workplace_from(locs)
        p = build_posting(
            source=NAME,
            company=(job.get("company") or {}).get("display_name") or "Unknown",
            title=job.get("title", ""),
            url=url,
            description_html=job.get("description") or "",
            locations=locs,
            workplace=workplace,
            posted_at=parse_time(job.get("created")),
            salary=salary,  # (None, None) -> a range stated in the snippet text is still read
            currency="USD" if salary != (None, None) else None,
            raw=job,
        )
        # The redirect URL changes between calls; the ad id does not.
        p.id = posting_id(f"https://www.adzuna.com/details/{ad_id}")
        out.append(p)
    return out


def search(cfg: dict, home_zip: str | None, radius_miles: float, http: Http) -> list[Posting]:
    """One request per query, location mode and page."""
    country = cfg.get("country", "us")
    base = {
        "app_id": os.environ["ADZUNA_APP_ID"],
        "app_key": os.environ["ADZUNA_APP_KEY"],
        "results_per_page": int(cfg.get("results_per_page", 50)),
        "max_days_old": int(cfg.get("max_days_old", 30)),
        "content-type": "application/json",
    }
    variants: list[dict] = []
    for q in cfg.get("queries") or []:
        if cfg.get("near_home", True) and home_zip:
            # Adzuna's distance is in kilometres. The prefilter applies the exact radius afterwards.
            variants.append({"what": q, "where": home_zip, "distance": int(radius_miles * KM_PER_MILE) + 5})
        if cfg.get("remote", True):
            variants.append({"what": f"{q} remote"})
        if not home_zip and not cfg.get("remote", True):
            variants.append({"what": q})
    found: dict[str, Posting] = {}
    for v in variants:
        for page in range(1, int(cfg.get("pages", 1)) + 1):
            data = http.get_json(search_url(country, page), params={**base, **v})
            for p in parse(data):
                found.setdefault(p.id, p)
            if len(data.get("results", [])) < base["results_per_page"]:
                break
    return list(found.values())
