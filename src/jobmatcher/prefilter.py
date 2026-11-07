"""Code-only rules that drop clear mismatches before any paid classifier call.

Every rule keeps a posting when it cannot tell (no date, no salary, no location it can place):
the classifier, and you, are better at judging ambiguous cases than a regex.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable

from jobmatcher.clean import clearance_rank, clearance_required, parse_years_required
from jobmatcher.geo import haversine_miles, parse_location, zip_coords
from jobmatcher.models import Posting

KEPT = "kept"


def _has_any(text: str, words: list[str]) -> bool:
    for w in words or []:
        if re.search(rf"(?<![a-z0-9]){re.escape(str(w).lower())}(?![a-z0-9])", text.lower()):
            return True
    return False


class Prefilter:
    def __init__(self, cfg: dict, now: datetime | None = None) -> None:
        self.cfg = cfg
        self.now = now or datetime.now(timezone.utc)
        loc = cfg.get("location") or {}
        self.home = zip_coords(loc["home_zip"]) if loc.get("home_zip") else None
        if loc.get("home_zip") and self.home is None:
            raise ValueError("location.home_zip is not a known US zip code")
        self.rules: list[tuple[str, Callable[[Posting], bool]]] = [
            ("too_old", self._age),
            ("title_excluded", self._title_exclude),
            ("title_not_included", self._title_include),
            ("seniority", self._seniority),
            ("location", self._location),
            ("clearance", self._clearance),
            ("years_required", self._years),
            ("salary_below_floor", self._salary),
        ]

    # Each rule returns True to keep.
    def _age(self, p: Posting) -> bool:
        days = self.cfg.get("max_age_days")
        return not (days and p.posted_at and p.posted_at < self.now - timedelta(days=days))

    def _title_exclude(self, p: Posting) -> bool:
        return not _has_any(p.title, (self.cfg.get("title") or {}).get("exclude"))

    def _title_include(self, p: Posting) -> bool:
        include = (self.cfg.get("title") or {}).get("include")
        return not include or _has_any(p.title, include)

    def _seniority(self, p: Posting) -> bool:
        return not _has_any(p.title, self.cfg.get("seniority_exclude"))

    def _location(self, p: Posting) -> bool:
        loc = self.cfg.get("location") or {}
        countries = {c.upper() for c in loc.get("countries") or []}
        places = [parse_location(s) for s in p.locations]
        if not places:
            if p.workplace == "remote":
                return bool(loc.get("remote", True))
            return bool(loc.get("keep_unknown", True))

        def country_ok(place) -> bool:
            if place.country == "foreign":
                return False
            return not countries or place.country is None or place.country in countries

        for place in places:
            if not country_ok(place):
                continue
            if place.remote or p.workplace == "remote":
                if loc.get("remote", True):
                    return True
                continue
            if p.workplace == "hybrid" and not loc.get("hybrid", True):
                continue
            if self.home is None:
                return True  # no home set: anywhere in the allowed countries is fine
            if place.located:
                if haversine_miles(self.home, (place.lat, place.lon)) <= float(loc.get("radius_miles", 25)):
                    return True
                continue
            if loc.get("keep_unknown", True):
                return True
        return False

    def _clearance(self, p: Posting) -> bool:
        limit = self.cfg.get("max_clearance")
        if limit is None:
            return True
        return clearance_rank(clearance_required(f"{p.title}\n{p.text}")) <= clearance_rank(limit)

    def _years(self, p: Posting) -> bool:
        limit = self.cfg.get("max_years_required")
        if limit is None:
            return True
        required = parse_years_required(p.text)
        return required is None or required <= limit

    def _salary(self, p: Posting) -> bool:
        floor = self.cfg.get("min_salary")
        top = p.salary_max if p.salary_max is not None else p.salary_min
        return not (floor and top is not None and top < floor)

    def outcome(self, p: Posting) -> str:
        """``kept``, or the name of the first rule that dropped the posting."""
        for name, keep in self.rules:
            if not keep(p):
                return name
        return KEPT

    def run(self, postings: list[Posting]) -> tuple[dict[str, str], Counter]:
        outcomes = {p.id: self.outcome(p) for p in postings}
        return outcomes, Counter(outcomes.values())
