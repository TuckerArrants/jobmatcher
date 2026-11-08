"""The digest: which postings appear, re-deciding from stored sub-scores, resume choice, formats."""

from datetime import datetime, timedelta, timezone

import pytest

from jobmatcher import report
from jobmatcher.classifiers.jev import JevClassifier, load_question_set
from jobmatcher.classify import classify
from jobmatcher.cli import main
from jobmatcher.config import Paths, package_yaml
from jobmatcher.decide import make_decider
from jobmatcher.models import Posting
from jobmatcher.store import Store

from test_jev_classify import FakeJev, choice, noul, score

QSET = load_question_set("jobs_v1")
DECIDE = make_decider(package_yaml("decision.default.yaml"))
NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def strong_fake():
    """'Apply' for postings whose text says GOOD, 'skip' for BAD, blocked for PHD, else 'consider'."""
    class F(FakeJev):
        def system_one(self, body):
            text = body["state"]["posting"]
            if "GOOD" in text:
                self.forced = dict(verdict=choice("apply"), role_match=score(3), interest=score(3),
                                   level_fit=choice("fits"), hard_blocker=noul(0.05), unwanted=noul(0.05))
            elif "BAD" in text:
                self.forced = dict(verdict=choice("skip"), role_match=score(0), level_fit=choice("fits"),
                                   hard_blocker=noul(0.05), unwanted=noul(0.05))
            elif "PHD" in text:
                self.forced = dict(verdict=choice("apply"), role_match=score(3), hard_blocker=noul(0.95),
                                   unwanted=noul(0.05), level_fit=choice("fits"))
            else:
                self.forced = dict(verdict=choice("consider"), role_match=score(2), level_fit=choice("fits"),
                                   hard_blocker=noul(0.05), unwanted=noul(0.05))
            return super().system_one(body)
    return F()


def mk(i, text, source="greenhouse", title=None, **kw):
    return Posting(source=source, company=f"Co {i}", title=title or f"Data Scientist {i}",
                   url=f"https://x.com/{i}", text=text, locations=["Boston, MA"], workplace="hybrid",
                   posted_at=NOW - timedelta(days=i), **kw)


@pytest.fixture
def env(tmp_path):
    paths = Paths(tmp_path)
    (tmp_path / "profile.md").write_text("Target roles: data scientist.")
    (tmp_path / "resumes.yaml").write_text(
        "versions:\n  - {name: imaging, keywords: [imaging, mri]}\n  - {name: quant, keywords: [quant]}\ndefault: general\n"
    )
    store = Store(paths.db)
    return paths, store


def ingest_like(store, postings, seen_at):
    store.upsert_postings(postings, seen_at=seen_at)
    store.db.execute("UPDATE postings SET last_seen = ? WHERE id IN (%s)" % ",".join("?" * len(postings)),
                     (seen_at, *[p.id for p in postings]))
    store.db.commit()
    store.set_prefilter({p.id: "kept" for p in postings})


def test_digest_sections_rules_and_resume(env):
    paths, store = env
    ps = [mk(0, "GOOD fit, MRI imaging", salary_min=120000, salary_max=150000), mk(1, "BAD"),
          mk(2, "PHD required"), mk(3, "partly relevant", source="adzuna", title="Quant Analyst")]
    ingest_like(store, ps, "2026-11-07T10:00:00+00:00")
    classify(paths, store, classifier=JevClassifier(QSET, strong_fake(), DECIDE))
    d, md_path = report.write(paths, store, today=NOW)

    assert [r.posting.title for r in d.by_label("apply")] == ["Data Scientist 0"]
    assert {r.rule for r in d.by_label("skip")} == {"verdict_skip", "hard_blocker"}
    assert [r.posting.title for r in d.by_label("consider")] == ["Quant Analyst"]
    good = d.by_label("apply")[0]
    assert good.resume == "imaging" and d.by_label("consider")[0].resume == "quant"

    md = md_path.read_text()
    assert md.startswith("# Job digest 2026-11-07") and "$120k–$150k" in md
    assert "[Data Scientist 0](https://x.com/0)" in md and "<details>" in md
    assert "Jobs by Adzuna" in md  # attribution whenever an Adzuna listing is shown
    page = (paths.reports / "latest.html").read_text()
    assert page.startswith("<!doctype html>") and "https://x.com/0" in page
    assert (paths.reports / "2026-11-07.html").exists()


def test_second_report_shows_only_new_postings(env):
    paths, store = env
    clf = JevClassifier(QSET, strong_fake(), DECIDE)
    first = [mk(0, "GOOD"), mk(1, "BAD")]
    ingest_like(store, first, "2000-01-01T00:00:00+00:00")
    classify(paths, store, classifier=clf)
    d1, _ = report.write(paths, store, today=NOW)
    assert len(d1.rows) == 2

    later = [mk(5, "GOOD again")]
    ingest_like(store, first + later, "2999-01-01T00:00:00+00:00")  # first seen after the first report
    classify(paths, store, classifier=clf)
    d2, _ = report.write(paths, store, today=NOW)
    assert [r.posting.title for r in d2.rows] == ["Data Scientist 5"]
    d3, _ = report.write(paths, store, show_all=True, today=NOW)
    assert len(d3.rows) == 3


def test_closed_and_refiltered_postings_drop_out(env):
    paths, store = env
    ps = [mk(0, "GOOD"), mk(1, "GOOD too")]
    ingest_like(store, ps, "2026-11-07T10:00:00+00:00")
    classify(paths, store, classifier=JevClassifier(QSET, strong_fake(), DECIDE))
    ingest_like(store, ps[:1], "2026-11-08T10:00:00+00:00")  # posting 1 no longer listed
    store.set_prefilter({ps[0].id: "location"})  # posting 0 now fails a filter
    d, _ = report.write(paths, store, show_all=True, today=NOW)
    assert d.rows == []


def test_editing_thresholds_relabels_without_asking_again(env):
    paths, store = env
    ps = [mk(3, "partly relevant")]
    ingest_like(store, ps, "2026-11-07T10:00:00+00:00")
    fake = strong_fake()
    classify(paths, store, classifier=JevClassifier(QSET, fake, DECIDE))
    assert report.build(paths, store, show_all=True).by_label("consider")
    # A stricter "unwanted" threshold in user/decision.yaml relabels from the stored answers.
    (paths.user / "decision.yaml").write_text("unwanted_skip: 0.0\n")
    d = report.build(paths, store, show_all=True)
    assert [r.rule for r in d.rows] == ["unwanted"]
    assert len(fake.bodies) == 1


def test_cli_run_reports_counts_only_on_ci(env, monkeypatch, capsys):
    paths, store = env
    ingest_like(store, [mk(0, "GOOD")], "2026-11-07T10:00:00+00:00")
    classify(paths, store, classifier=JevClassifier(QSET, strong_fake(), DECIDE))
    store.close()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert main(["--user-dir", str(paths.user), "report"]) == 0
    printed = capsys.readouterr().out
    assert "report: apply 1, consider 0, skip 0" in printed
    assert "Data Scientist" not in printed and "Co 0" not in printed and "written to" not in printed


def test_formatting_helpers():
    p = mk(0, "x", salary_min=90000, salary_max=90000)
    assert report.salary_text(p) == "$90k"
    assert report.where_text(p) == "Boston, MA, hybrid"
    assert report.rule_text("orderings_disagree:verdict,level_fit") == \
        "answer changed with option order (verdict, level_fit)"
    assert report.scores_text({"role_match": 1.0, "interest": 0.34, "level_fit": "fits",
                               "verdict__p_apply": 0.72, "hard_blocker": 0.1}) == \
        "role exact · interest neutral · level fits · apply 72%"
    assert report.suggest_resume(p, {}) is None
