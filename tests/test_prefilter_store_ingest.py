from datetime import timedelta

import pytest
import yaml

from jobmatcher.config import Company, ConfigError, Paths, deep_merge, load_companies, load_filters, package_yaml
from jobmatcher.ingest import ingest
from jobmatcher.models import Posting
from jobmatcher.prefilter import Prefilter
from jobmatcher.store import Store

from conftest import NOW


def make(title="Data Scientist", locations=("Remote - US",), workplace=None, text="", **kw):
    from jobmatcher.sources.common import workplace_from

    locations = list(locations)
    return Posting(
        source="greenhouse", company="Co", title=title, url=f"https://x.com/{title}/{locations}/{text}",
        locations=locations, workplace=workplace or workplace_from(locations), text=text,
        posted_at=kw.pop("posted_at", NOW - timedelta(days=1)), **kw,
    )


def cfg(**over):
    return deep_merge(package_yaml("filters.default.yaml"), over)


def test_defaults_keep_ordinary_posting():
    assert Prefilter(cfg(), now=NOW).outcome(make()) == "kept"


@pytest.mark.parametrize(
    "posting, over, rule",
    [
        (make(posted_at=NOW - timedelta(days=60)), {}, "too_old"),
        (make(title="Sales Engineer"), {"title": {"exclude": ["sales"]}}, "title_excluded"),
        (make(title="Office Manager"), {"title": {"include": ["data", "ml"]}}, "title_not_included"),
        (make(title="Director of Data Science"), {}, "seniority"),
        (make(locations=["London, UK"]), {}, "location"),
        (make(locations=["Remote - US"]), {"location": {"remote": False}}, "location"),
        (make(locations=["Denver, CO"]), {"location": {"home_zip": "37402", "radius_miles": 25}}, "location"),
        (make(text="Active TS/SCI clearance required"), {"max_clearance": "public_trust"}, "clearance"),
        (make(text="8+ years of experience"), {"max_years_required": 5}, "years_required"),
        (make(salary_min=60000, salary_max=80000), {"min_salary": 100000}, "salary_below_floor"),
    ],
)
def test_each_rule_drops(posting, over, rule):
    assert Prefilter(cfg(**over), now=NOW).outcome(posting) == rule


@pytest.mark.parametrize(
    "posting, over",
    [
        (make(locations=["Chattanooga, TN"]), {"location": {"home_zip": "37402", "radius_miles": 25}}),
        (make(locations=["Cleveland, TN"]), {"location": {"home_zip": "37402", "radius_miles": 35}}),
        (make(locations=["Denver, CO", "Remote - US"]), {"location": {"home_zip": "37402"}}),
        (make(locations=["Somewhere Unparseable"]), {"location": {"home_zip": "37402"}}),
        (make(locations=[]), {"location": {"home_zip": "37402"}}),
        (make(text="Ability to obtain Public Trust"), {"max_clearance": "public_trust"}),
        (make(text="no years mentioned"), {"max_years_required": 2}),
        (make(), {"min_salary": 100000}),  # unknown salary is kept
        (make(posted_at=None), {}),  # unknown date is kept
    ],
)
def test_rules_keep_when_ok_or_unknown(posting, over):
    assert Prefilter(cfg(**over), now=NOW).outcome(posting) == "kept"


def test_bad_home_zip_is_an_error():
    with pytest.raises(ValueError):
        Prefilter(cfg(location={"home_zip": "00000"}), now=NOW)


def test_layered_config_and_unknown_keys(tmp_path):
    paths = Paths(tmp_path)
    (tmp_path / "filters.yaml").write_text("location:\n  home_zip: '37402'\nmin_salary: 90000\n")
    f = load_filters(paths)
    assert f["location"]["home_zip"] == "37402" and f["location"]["radius_miles"] == 25
    assert f["min_salary"] == 90000 and f["max_age_days"] == 30
    (tmp_path / "filters.yaml").write_text("min_salry: 1\n")
    with pytest.raises(ConfigError):
        load_filters(paths)


def test_companies_validation(tmp_path):
    paths = Paths(tmp_path)
    (tmp_path / "companies.yaml").write_text(yaml.safe_dump({"companies": [{"name": "A", "ats": "workday", "token": "a"}]}))
    with pytest.raises(ConfigError):
        load_companies(paths)


def test_store_upsert_tracks_seen_and_new(tmp_path):
    store = Store(tmp_path / "data" / "jobs.sqlite")
    p = make()
    assert store.upsert_postings([p], seen_at="2026-11-01T00:00:00+00:00") == 1
    assert store.upsert_postings([p], seen_at="2026-11-02T00:00:00+00:00") == 0
    row = store.db.execute("SELECT first_seen, last_seen FROM postings").fetchone()
    assert tuple(row) == ("2026-11-01T00:00:00+00:00", "2026-11-02T00:00:00+00:00")
    back = store.get(p.id)
    assert back.title == p.title and back.locations == p.locations and back.posted_at == p.posted_at
    store.save_result(p.id, "fake:v1", "apply", {"rule": "x"}, {"a": 1}, 0.0001)
    assert store.has_result(p.id, "fake:v1") and not store.has_result(p.id, "fake:v2")
    store.set_label(p.id, "skip", "not for me")
    assert store.labels() == {p.id: "skip"}


def test_ingest_end_to_end_with_fakes(tmp_path, fake_http):
    paths = Paths(tmp_path)
    (tmp_path / "filters.yaml").write_text("location:\n  home_zip: '02139'\n  radius_miles: 25\n")
    companies = [
        Company("Example Bio", "greenhouse", "examplebio"),
        Company("Example Fund", "lever", "examplefund"),
        Company("Example Chem", "ashby", "examplechem"),
        Company("Missing", "greenhouse", "doesnotexist"),
    ]
    store = Store(paths.db)
    result = ingest(paths, store, http=fake_http, companies=companies, now=NOW)
    c = result["counts"]
    assert c["boards_ok"] == 3 and c["boards_failed"] == 1
    assert c["fetched"] == 5 and c["new"] == 5
    # Kept: Boston/remote data scientist, remote data analyst, SF hybrid + remote (US) ML engineer.
    # Dropped: London account executive (location), NYC/Chicago onsite quant (location).
    assert c["kept"] == 3
    assert result["dropped_by_rule"] == {"location": 2}
    # A second run finds nothing new and makes one request per board again.
    again = ingest(paths, store, http=fake_http, companies=companies, now=NOW)
    assert again["counts"]["new"] == 0
    assert len(fake_http.calls) == 8
