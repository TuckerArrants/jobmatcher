"""Fetch every configured board and search once, store new postings, run the prefilter, and mark
cross-source duplicates."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from jobmatcher.config import Company, Paths, load_companies, load_filters, load_layered
from jobmatcher.models import Posting
from jobmatcher.prefilter import KEPT, Prefilter
from jobmatcher.sources import ATS, adzuna, himalayas, hn, usajobs
from jobmatcher.sources.http import Http, NotFound, SourceError
from jobmatcher.store import Store, now_iso

# When the same job appears in several sources, keep the copy with the fullest text.
SOURCE_PRIORITY = {"greenhouse": 0, "lever": 0, "ashby": 0, "usajobs": 1, "himalayas": 2, "hn": 3, "adzuna": 4}
DUPLICATE = "duplicate"
_SUFFIXES = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|the|pbc|plc|gmbh)\b\.?")


def load_searches(paths: Paths) -> dict:
    return load_layered("searches.default.yaml", paths.user / "searches.yaml")


def dedupe_key(p: Posting) -> tuple[str, str]:
    company = " ".join(re.findall(r"[a-z0-9]+", _SUFFIXES.sub("", p.company.lower())))
    title = " ".join(re.findall(r"[a-z0-9]+", p.title.lower()))
    return company, title


def mark_duplicates(postings: list[Posting], outcomes: dict[str, str]) -> int:
    """Among kept postings, keep one per (company, title): the best source, then the newest."""
    groups: dict[tuple[str, str], list[Posting]] = {}
    for p in postings:
        if outcomes.get(p.id) == KEPT:
            groups.setdefault(dedupe_key(p), []).append(p)
    n = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda p: (SOURCE_PRIORITY.get(p.source, 9),
                                  -(p.posted_at.timestamp() if p.posted_at else 0)))
        for p in group[1:]:
            outcomes[p.id] = DUPLICATE
            n += 1
    return n


def ingest(
    paths: Paths,
    store: Store,
    http: Http | None = None,
    companies: list[Company] | None = None,
    now: datetime | None = None,
) -> dict:
    http = http or Http()
    companies = load_companies(paths) if companies is None else companies
    filters = load_filters(paths)
    searches = load_searches(paths)
    prefilter = Prefilter(filters, now=now)
    seen_at = now.isoformat() if now else now_iso()

    counts: Counter = Counter()
    failed: list[str] = []
    batch: list[Posting] = []

    def take(postings: list[Posting]) -> None:
        counts["fetched"] += len(postings)
        counts["new"] += store.upsert_postings(postings, seen_at=seen_at)
        batch.extend(postings)

    for company in companies:
        try:
            postings = ATS[company.ats].fetch(company.token, company.name, http)
        except (NotFound, SourceError, ValueError) as exc:
            counts["boards_failed"] += 1
            failed.append(f"{company.name} ({company.ats}/{company.token}): {type(exc).__name__}")
            continue
        counts["boards_ok"] += 1
        take(postings)

    loc = filters.get("location") or {}
    always = lambda: True  # noqa: E731  sources that need no key
    for name, keys_ok, run in (
        ("adzuna", adzuna.keys_present, lambda: adzuna.search(searches["adzuna"], loc.get("home_zip"),
                                                               float(loc.get("radius_miles", 25)), http)),
        ("usajobs", usajobs.keys_present, lambda: usajobs.search(searches["usajobs"], http, now=now)),
        ("himalayas", always, lambda: himalayas.search(searches["himalayas"], http)),
        ("hn", always, lambda: hn.search(searches["hn"], http)),
    ):
        cfg = searches[name]
        configured = cfg.get("queries") or cfg.get("keywords") or cfg.get("enabled")
        if not configured:
            continue
        if not keys_ok():
            counts[f"{name}_skipped_no_key"] += 1
            continue
        try:
            postings = run()
        except (NotFound, SourceError, ValueError) as exc:
            counts["searches_failed"] += 1
            failed.append(f"{name}: {type(exc).__name__}")
            continue
        counts[f"{name}_found"] += len(postings)
        take(postings)

    # Re-filter everything seen this run, so edits to filters.yaml apply to open postings too.
    unique = {p.id: p for p in batch}
    seen = [store.get(pid) for pid in unique]
    seen = [p for p in seen if p]
    outcomes, _ = prefilter.run(seen)
    counts["duplicates"] = mark_duplicates(seen, outcomes)
    store.set_prefilter(outcomes)
    by_rule = Counter(outcomes.values())
    counts["kept"] = by_rule.pop(KEPT, 0)
    by_rule.pop(DUPLICATE, None)
    result = {"counts": dict(counts), "dropped_by_rule": dict(by_rule), "failed": failed}
    store.record_run("ingest", {**counts, **{f"drop:{k}": v for k, v in by_rule.items()}})
    return result
