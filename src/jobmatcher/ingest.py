"""Fetch every configured board once, store new postings, and run the prefilter."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from jobmatcher.config import Company, Paths, load_companies, load_filters
from jobmatcher.prefilter import KEPT, Prefilter
from jobmatcher.sources import ATS
from jobmatcher.sources.http import Http, NotFound, SourceError
from jobmatcher.store import Store, now_iso


def ingest(
    paths: Paths,
    store: Store,
    http: Http | None = None,
    companies: list[Company] | None = None,
    now: datetime | None = None,
) -> dict:
    http = http or Http()
    companies = load_companies(paths) if companies is None else companies
    prefilter = Prefilter(load_filters(paths), now=now)
    seen_at = now.isoformat() if now else now_iso()

    counts: Counter = Counter()
    failed: list[str] = []
    batch_ids: list[str] = []
    for company in companies:
        module = ATS[company.ats]
        try:
            postings = module.fetch(company.token, company.name, http)
        except (NotFound, SourceError, ValueError) as exc:
            counts["boards_failed"] += 1
            failed.append(f"{company.name} ({company.ats}/{company.token}): {type(exc).__name__}")
            continue
        counts["boards_ok"] += 1
        counts["fetched"] += len(postings)
        counts["new"] += store.upsert_postings(postings, seen_at=seen_at)
        batch_ids += [p.id for p in postings]

    # Re-filter everything seen this run, so edits to filters.yaml apply to open postings too.
    seen = [store.get(pid) for pid in dict.fromkeys(batch_ids)]
    outcomes, by_rule = prefilter.run([p for p in seen if p])
    store.set_prefilter(outcomes)
    counts["kept"] = by_rule.pop(KEPT, 0)
    result = {"counts": dict(counts), "dropped_by_rule": dict(by_rule), "failed": failed}
    store.record_run("ingest", {**counts, **{f"drop:{k}": v for k, v in by_rule.items()}})
    return result
