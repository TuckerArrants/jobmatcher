"""Hacker News "Ask HN: Who is hiring?" threads, via the free HN search API (hn.algolia.com).

Each top-level comment in the monthly thread is one employer's post. By convention its first
line reads "Company | Role(s) | Location | REMOTE/ONSITE | ...". The company and location are
taken from that line; the whole comment becomes the posting text. One request finds the
thread, one more fetches all its comments.
"""

from __future__ import annotations

import re
from typing import Any

from jobmatcher.clean import html_to_text
from jobmatcher.geo import parse_location
from jobmatcher.models import Posting, parse_time
from jobmatcher.sources.common import build_posting, workplace_from
from jobmatcher.sources.http import Http

NAME = "hn"
ATTRIBUTION = ("Posts from Hacker News “Who is hiring”", "https://news.ycombinator.com")
STORIES_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL = "https://hn.algolia.com/api/v1/items/{id}"

_NOT_ROLE = re.compile(
    r"^(remote|onsite|on-site|hybrid|full[- ]?time|part[- ]?time|contract|intern(ship)?s?|"
    r"visa|h1b|https?://|www\.|\$|£|€|\d+k)", re.IGNORECASE)
_ROLE_WORDS = re.compile(
    r"\b(engineer|scientist|analyst|researcher|developer|designer|manager|lead|architect|"
    r"specialist|director|head|founding|staff|intern|quant|statistician|biologist|chemist)\b",
    re.IGNORECASE)


def thread_ids(data: dict[str, Any], months: int) -> list[int]:
    hits = [h for h in data.get("hits", []) if str(h.get("title", "")).lower().startswith("ask hn: who is hiring")]
    hits.sort(key=lambda h: h.get("created_at_i", 0), reverse=True)
    return [int(h["objectID"]) for h in hits[: max(1, months)]]


def split_header(first_line: str) -> tuple[str, str, list[str]]:
    """(company, role, locations) from "Company | Role | Location | REMOTE"."""
    parts = [p.strip() for p in re.split(r"\s*[|•·]\s*", first_line) if p.strip()]
    if not parts:
        return "", "", []
    company = re.sub(r"\s*\(.*?\)\s*$", "", parts[0]).strip()
    roles, locs = [], []
    for part in parts[1:]:
        if _ROLE_WORDS.search(part):
            roles.append(part)
        elif not _NOT_ROLE.match(part):
            place = parse_location(part)
            if place.state or place.country or place.remote:
                locs.append(part)
        elif re.match(r"(remote|onsite|on-site|hybrid)\b", part, re.IGNORECASE):
            locs.append(part)
    return company, "; ".join(roles), locs


def parse(item: dict[str, Any]) -> list[Posting]:
    out = []
    for c in item.get("children") or []:
        html_text = c.get("text")
        if not html_text or c.get("author") in (None, "[deleted]"):
            continue
        text = html_to_text(html_text)
        first = text.splitlines()[0] if text else ""
        company, role, locs = split_header(first)
        if not company or len(company) > 60:
            continue
        title = role or first[:120]
        out.append(
            build_posting(
                source=NAME,
                company=company,
                title=title,
                url=f"https://news.ycombinator.com/item?id={c['id']}",
                description_html=html_text,
                locations=locs,
                workplace=workplace_from([first]),
                posted_at=parse_time(c.get("created_at_i") or c.get("created_at")),
                raw={"id": c.get("id"), "first_line": first},
            )
        )
    return out


def search(cfg: dict, http: Http) -> list[Posting]:
    stories = http.get_json(STORIES_URL, params={"tags": "story,author_whoishiring", "hitsPerPage": 10})
    found: dict[str, Posting] = {}
    for tid in thread_ids(stories, int(cfg.get("months", 1))):
        for p in parse(http.get_json(ITEM_URL.format(id=tid))):
            found.setdefault(p.id, p)
    return list(found.values())
