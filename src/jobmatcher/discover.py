"""Grow the employer list automatically.

After a run, look at companies whose postings came from a search source (Himalayas, Hacker News,
Adzuna) and that the classifier rated relevant (role_match at or above a threshold). For each one
not already tracked or tried, look for a public Greenhouse, Lever or Ashby board. A board is
accepted only when its token is the full company name (``acmebio`` or ``acme-bio``), never just
the first word, which can belong to a different company. Found boards are added to
companies.yaml, so the next run fetches that employer's full job list. Every name tried is
recorded in user/discovered.yaml, so nothing is looked up twice.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yaml

from jobmatcher.config import Paths, load_companies
from jobmatcher.findboard import add_to_companies, candidate_tokens, find_board
from jobmatcher.sources.http import Http
from jobmatcher.store import Store

SEARCH_SOURCES = ("himalayas", "hn", "adzuna")


def _norm(name: str) -> str:
    toks = candidate_tokens(name)
    return toks[0] if toks else name.lower()


def load_tried(path: Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}


def candidates(store: Store, classifier: str, tracked: set[str], tried: dict, min_role: float) -> list[str]:
    best: dict[str, tuple[float, str]] = {}
    q = ("SELECT p.company, r.decision FROM results r JOIN postings p ON p.id = r.posting_id "
         "WHERE r.classifier = ? AND p.source IN (%s)" % ",".join("?" * len(SEARCH_SOURCES)))
    for row in store.db.execute(q, (classifier, *SEARCH_SOURCES)):
        role = float(json.loads(row["decision"])["sub_scores"].get("role_match", 0))
        key = _norm(row["company"])
        if role < min_role or not key or key in tracked or key in tried:
            continue
        if key not in best or role > best[key][0]:
            best[key] = (role, row["company"])
    return [name for _, name in sorted(best.values(), reverse=True)]


def discover(paths: Paths, store: Store, classifier: str, cfg: dict, http: Http | None = None) -> dict:
    http = http or Http(retries=1)
    tried_path = paths.user / "discovered.yaml"
    tried = load_tried(tried_path)
    tracked = {_norm(c.name) for c in load_companies(paths)} | {c.token for c in load_companies(paths)}
    names = candidates(store, classifier, tracked, tried, float(cfg.get("min_role_match", 0.5)))
    names = names[: int(cfg.get("max_per_run", 15))]
    added = []
    today = date.today().isoformat()
    for name in names:
        strict = candidate_tokens(name)[:2]  # full name, joined or hyphenated
        hits = [h for h in find_board(name, http, tokens=strict) if h.jobs > 0]
        top = max(hits, key=lambda h: h.jobs) if hits else None
        tried[_norm(name)] = {"name": name, "checked": today,
                              "board": f"{top.ats}/{top.token}" if top else None}
        if top:
            added.append({"name": name, "ats": top.ats, "token": top.token})
    if added:
        add_to_companies(paths.companies, added)
    if names:
        tried_path.write_text(yaml.safe_dump(tried, sort_keys=True, allow_unicode=True), encoding="utf-8")
    return {"checked": len(names), "added": len(added)}
