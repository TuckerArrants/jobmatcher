"""Himalayas and Hacker News sources, and automatic employer discovery (fixtures; no network)."""

import json

import yaml

from jobmatcher import discover as disc
from jobmatcher.config import Paths
from jobmatcher.models import Posting
from jobmatcher.report import credits, Digest, Row
from jobmatcher.sources import himalayas, hn
from jobmatcher.store import Store

from conftest import NOW, FakeHttp, fixture


def test_himalayas_parse():
    jobs = {p.company: p for p in himalayas.parse(fixture("himalayas.json"))}
    ds = jobs["Helix Example"]
    assert ds.workplace == "remote" and ds.locations == ["Remote - United States"]
    assert (ds.salary_min, ds.salary_max) == (120000, 150000)
    assert ds.url.startswith("https://himalayas.app/")  # terms: apply through applicationLink
    assert jobs["Euro Example"].salary_min is None  # non-USD salary not used
    assert jobs["Anywhere Co"].locations == ["Remote - Worldwide"]
    assert (jobs["Anywhere Co"].salary_min, jobs["Anywhere Co"].salary_max) == (40 * 2080, 50 * 2080)


def test_himalayas_search_one_request_per_query():
    http = FakeHttp({"https://himalayas.app/jobs/api/search": fixture("himalayas.json")})
    found = himalayas.search({"queries": ["data scientist", "ml"], "country": "US", "pages": 1}, http)
    assert len(http.requests) == 2 and len(found) == 3
    assert http.requests[0][1] == {"q": "data scientist", "page": 1, "country": "US"}


def test_hn_header_split():
    company, role, locs = hn.split_header(
        "Acme Bio | Machine Learning Engineer, Data Scientist | Boston, MA | ONSITE or REMOTE (US) | $150k-$200k")
    assert company == "Acme Bio" and role == "Machine Learning Engineer, Data Scientist"
    assert locs == ["Boston, MA", "ONSITE or REMOTE (US)"]
    assert hn.split_header("Quantly (YC W24) | Quant Researcher | New York, NY | ONSITE")[0] == "Quantly"


def test_hn_search_reads_latest_hiring_thread_only():
    http = FakeHttp({"https://hn.algolia.com/api/v1/search_by_date": fixture("hn_stories.json"),
                     "https://hn.algolia.com/api/v1/items/45000001": fixture("hn_item.json")})
    jobs = {p.company: p for p in hn.search({"months": 1}, http)}
    assert set(jobs) == {"Acme Bio", "Quantly"}  # deleted and header-less posts skipped; replies ignored
    acme = jobs["Acme Bio"]
    assert acme.workplace == "remote" and acme.url == "https://news.ycombinator.com/item?id=45000101"
    assert (acme.salary_min, acme.salary_max) == (150000, 200000) and "PyTorch" in acme.text
    assert jobs["Quantly"].workplace == "onsite" and jobs["Quantly"].title == "Quant Researcher"
    assert len(http.requests) == 2


def _store_with_results(paths, rows):
    store = Store(paths.db)
    for i, (source, company, role) in enumerate(rows):
        p = Posting(source=source, company=company, title=f"T{i}", url=f"https://x/{i}", posted_at=NOW)
        store.upsert_postings([p])
        store.save_result(p.id, "clf", "consider", {"sub_scores": {"role_match": role}}, {}, 0)
    return store


def test_discovery_adds_full_name_boards_once(tmp_path):
    paths = Paths(tmp_path)
    (tmp_path / "companies.yaml").write_text("companies:\n  - {name: Tracked Co, ats: lever, token: trackedco}\n")
    store = _store_with_results(paths, [
        ("hn", "Acme Bio", 0.9),          # relevant, has a full-name board -> added
        ("himalayas", "Acme Bio", 0.7),   # same company again -> looked up once
        ("hn", "Nomatch Labs", 0.8),      # relevant, only a first-word board exists -> not added
        ("hn", "Boring Corp", 0.1),       # not relevant -> not looked up
        ("greenhouse", "Board Co", 0.9),  # already from a board -> not a search source
        ("hn", "Tracked Co", 0.9),        # already tracked
    ])
    http = FakeHttp({"https://api.ashbyhq.com/posting-api/job-board/acmebio": {"jobs": [{"id": 1}]},
                     "https://boards-api.greenhouse.io/v1/boards/nomatch/": {"jobs": [{"id": 1}]}})
    res = disc.discover(paths, store, "clf", {"min_role_match": 0.5, "max_per_run": 10}, http=http)
    assert res == {"checked": 2, "added": 1}
    added = yaml.safe_load((tmp_path / "companies.yaml").read_text())["companies"]
    assert {"name": "Acme Bio", "ats": "ashby", "token": "acmebio"} in added and len(added) == 2
    tried = yaml.safe_load((tmp_path / "discovered.yaml").read_text())
    assert tried["acmebio"]["board"] == "ashby/acmebio" and tried["nomatchlabs"]["board"] is None
    assert not any("/nomatch/" in u or u.endswith("/nomatch") for u in http.calls)  # first word never tried
    again = disc.discover(paths, store, "clf", {"min_role_match": 0.5, "max_per_run": 10}, http=http)
    assert again == {"checked": 0, "added": 0}


def test_report_credits_sources_shown():
    def row(source):
        p = Posting(source=source, company="c", title="t", url=f"https://x/{source}")
        return Row(p, "apply", "r", 0.5, {}, None, "")
    d = Digest("2026-11-07", [row("himalayas"), row("hn"), row("greenhouse")])
    texts = [t for t, _ in credits(d)]
    assert any("Himalayas" in t for t in texts) and any("Hacker News" in t for t in texts)
    assert credits(Digest("2026-11-07", [row("lever")])) == []
