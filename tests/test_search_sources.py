"""Adzuna and USAJobs adapters, and cross-source duplicates, against fixtures (no network, no keys)."""

from datetime import datetime, timezone

import pytest

from jobmatcher.clean import clearance_required
from jobmatcher.config import Company, Paths
from jobmatcher.ingest import DUPLICATE, ingest, mark_duplicates
from jobmatcher.prefilter import KEPT
from jobmatcher.sources import adzuna, usajobs
from jobmatcher.store import Store

from conftest import NOW, FakeHttp, fixture


def test_adzuna_parse():
    jobs = {p.title: p for p in adzuna.parse(fixture("adzuna.json"))}
    da = jobs["Data Analyst"]
    assert da.company == "Example Health System" and da.locations == ["Chattanooga, Tennessee"]
    from jobmatcher.geo import parse_location
    assert parse_location(da.locations[0]).located  # placeable, so the radius rule can use it
    assert (da.salary_min, da.salary_max) == (70000, 85000)
    ml = jobs["Machine Learning Engineer - Remote"]
    assert ml.workplace == "remote" and ml.locations == ["United States"]
    assert ml.salary_min is None and ml.salary_max is None  # Adzuna's predicted salary is not used
    # The id comes from the ad id, not the redirect URL, whose query changes between calls.
    again = fixture("adzuna.json")
    again["results"][0]["redirect_url"] = "https://www.adzuna.com/land/ad/4400001?se=DIFFERENT"
    assert adzuna.parse(again)[0].id == da.id


def test_adzuna_search_queries_near_home_and_remote(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")
    http = FakeHttp({"https://api.adzuna.com/v1/api/jobs/us/search/": fixture("adzuna.json")})
    cfg = {"country": "us", "queries": ["data scientist"], "near_home": True, "remote": True,
           "results_per_page": 50, "pages": 2, "max_days_old": 30}
    found = adzuna.search(cfg, "37377", 25, http)
    params = [p for _, p, _ in http.requests]
    assert {p["what"] for p in params} == {"data scientist", "data scientist remote"}
    near = next(p for p in params if "where" in p)
    assert near["where"] == "37377" and near["distance"] == int(25 * 1.609344) + 5  # kilometres
    assert len(http.requests) == 2  # a short page ends paging for that query
    assert len(found) == 3  # the same ads from both queries are merged


def test_usajobs_parse_skips_closed_and_status_only():
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    jobs = {p.title: p for p in usajobs.parse(fixture("usajobs.json"), now=now)}
    assert set(jobs) == {"Data Scientist (Biomedical)"}  # one closed, one current-employees only
    ds = jobs["Data Scientist (Biomedical)"]
    assert ds.workplace == "remote" and (ds.salary_min, ds.salary_max) == (99200, 153354)
    assert "Develop models for MRI analysis" in ds.text and "Public Trust" in ds.text
    assert "National Institutes of Health" in ds.company
    every = {p.title: p for p in usajobs.parse(fixture("usajobs.json"), now=now, public_only=False)}
    assert clearance_required(every["Operations Research Analyst"].text) == "secret"
    early = usajobs.parse(fixture("usajobs.json"), now=datetime(2026, 11, 2, tzinfo=timezone.utc))
    stat = next(p for p in early if p.title == "Statistician")
    assert (stat.salary_min, stat.salary_max) == (40 * 2080, 50 * 2080)


def test_usajobs_sends_required_headers_from_env(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "k")
    monkeypatch.setenv("USAJOBS_EMAIL", "someone@example.com")
    http = FakeHttp({"https://data.usajobs.gov/api/Search": fixture("usajobs.json")})
    usajobs.search({"keywords": ["data scientist"], "results_per_page": 900, "days_posted": 90}, http, now=NOW)
    url, params, headers = http.requests[0]
    assert headers == {"Host": "data.usajobs.gov", "User-Agent": "someone@example.com", "Authorization-Key": "k"}
    assert params["ResultsPerPage"] == 500 and params["DatePosted"] == 60  # clamped to the API's limits


def test_duplicates_keep_the_full_text_copy():
    from jobmatcher.sources import greenhouse

    gh = greenhouse.parse(fixture("greenhouse.json"), "Example Bio")
    az = adzuna.parse(fixture("adzuna.json"))
    everything = gh + az
    outcomes = {p.id: KEPT for p in everything}
    assert mark_duplicates(everything, outcomes) == 1
    dup = next(p for p in az if p.title == "Data Scientist, Clinical Imaging")
    assert outcomes[dup.id] == DUPLICATE  # "Example Bio Inc" on Adzuna == "Example Bio" on Greenhouse
    assert outcomes[gh[0].id] == KEPT


def test_ingest_skips_sources_without_keys_and_counts(tmp_path, monkeypatch, fake_http):
    for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "USAJOBS_API_KEY", "USAJOBS_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    paths = Paths(tmp_path)
    (tmp_path / "searches.yaml").write_text("adzuna:\n  queries: [data scientist]\nusajobs:\n  keywords: [statistician]\n")
    store = Store(paths.db)
    res = ingest(paths, store, http=fake_http, companies=[Company("Example Bio", "greenhouse", "examplebio")], now=NOW)
    assert res["counts"]["adzuna_skipped_no_key"] == 1 and res["counts"]["usajobs_skipped_no_key"] == 1

    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")
    fake_http.routes["https://api.adzuna.com/v1/api/jobs/us/search/"] = fixture("adzuna.json")
    res = ingest(paths, store, http=fake_http, companies=[Company("Example Bio", "greenhouse", "examplebio")], now=NOW)
    c = res["counts"]
    assert c["adzuna_found"] == 3 and c["duplicates"] == 1
    assert store.count("prefilter = 'duplicate'") == 1
